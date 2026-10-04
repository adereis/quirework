"""Back a store up as a git repository: every run a commit, history kept.

The store becomes its own repository, so cloning it is a restore. Each run
commits the store as it stands and, when the target names a remote, pushes
the branch there. Where the copying drivers delete a rejected capture at the
destination, git keeps it in history, which is the one protection this driver
adds: a pending file deleted by hand is still in an earlier commit.

Two costs come with it. The repository's objects roughly double the store's
size on disk. And a pending capture that grows is committed whole again on
every run, so a long session in progress adds a copy of itself each time.

A store that already sits inside another repository, as one a project keeps
under its own tree does, is refused: that repository versions it already, and
a second one nested inside would hide the store from it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from quirework.project import ConfigurationError
from quirework.provenance import store
from quirework.provenance.backup.base import (
    PENDING,
    BackupError,
    Driver,
    Outcome,
    run_tool,
)
from quirework.runs.storage import shown

# A branch name git accepts, kept to the plain ones a person would choose.
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
# Dot names are never part of a backup; the same rule the copying drivers
# apply, kept in the repository's own exclude file rather than in a
# .gitignore that would itself be a file in the store.
EXCLUDE = "# Quirework: a backup never copies a name that starts with a dot.\n.*\n"


class Git(Driver):
    name = "git"
    keys = frozenset({"remote", "branch"})

    def configure(self, settings):
        self.remote = self.setting(settings, "remote") if "remote" in settings else None
        self.branch = (
            self.setting(settings, "branch") if "branch" in settings else "main"
        )
        if not _BRANCH.fullmatch(self.branch) or ".." in self.branch:
            raise ConfigurationError(
                f"{self.where}: branch must be a plain branch name, not {self.branch!r}"
            )

    def describe(self):
        pushed = f", pushed to {self.remote}" if self.remote else ""
        return f"git repository in the store, branch {self.branch}{pushed}"

    def _git(self, args, log, *, ok=(0,)):
        executable = shutil.which("git")
        if executable is None:
            raise BackupError("git is not installed")
        return run_tool(
            [executable, "-C", self.root, *args],
            log,
            what=f"git {args[0]}",
            env=_environment(executable),
            ok=ok,
        )

    def _alone(self):
        repository = store.versioned(self.root)
        if repository is not None and repository != self.root:
            raise BackupError(
                f"the store is inside the repository at {shown(repository)}, "
                "which versions it already; a git backup would nest a second "
                "repository inside it"
            )

    def check(self, log):
        self._alone()
        if self.remote:
            self._git(["ls-remote", "--heads", self.remote], log)

    def _prepare(self, log):
        if not (self.root / ".git").exists():
            self._git(["init", "--quiet", "--initial-branch", self.branch], log)
        exclude = self.root / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if EXCLUDE not in text:
            exclude.write_text(text + EXCLUDE, encoding="utf-8")

    def run(self, source, log):
        self._alone()
        self._prepare(log)
        # Staged under the store's lock, so the commit never holds a session
        # halfway between pending and accepted.
        with store.locked(source.root):
            versions = source.versions()
            pending = len(source.files(PENDING))
            self._git(["add", "--all"], log)
            staged = self._git(["diff", "--cached", "--quiet"], log, ok=(0, 1))
            changed = staged.returncode == 1
            if changed:
                self._git(
                    [
                        "commit",
                        "--quiet",
                        "--message",
                        "Back up the provenance store",
                        "--message",
                        (
                            f"{len(versions)} accepted version(s), {pending} "
                            "pending file(s)."
                        ),
                    ],
                    log,
                )
        if self.remote:
            self._git(
                ["push", "--quiet", self.remote, f"HEAD:refs/heads/{self.branch}"],
                log,
            )
        detail = "committed the changes" if changed else "nothing changed"
        if self.remote:
            detail += f" and pushed to {self.remote}"
        return Outcome(versions=versions, pending=pending, detail=detail)


def _environment(executable):
    """This process's environment without the variables that locate a repository.

    A backup started from inside a git hook inherits ``GIT_DIR``,
    ``GIT_INDEX_FILE`` and their company, which would aim every command at the
    hook's repository instead of the store's. Git names that set itself, so
    the rest of the person's git settings, such as ``GIT_SSH_COMMAND``, still
    apply.
    """
    local = subprocess.run(
        [executable, "rev-parse", "--local-env-vars"],
        capture_output=True,
        text=True,
        check=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    ).stdout.split()
    return {k: v for k, v in os.environ.items() if k not in local}
