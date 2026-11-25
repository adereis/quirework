"""Back a store up to any rclone remote: Google Drive, SFTP, S3, a crypt layer.

The remote is configured in rclone, by ``rclone config``, and the target names
it with a path: ``gdrive:quirework-provenance/my-book``. Quirework never
handles the remote's credentials.

Each `Transfer` operation is one rclone command. ``rclone copy`` adds and
updates files without ever deleting one, which is what the pending and
accepted areas both need; the accepted area adds ``--immutable --checksum``,
so a file already there is never rewritten and one whose checksum differs
stops the run. Names that start with a dot are filtered out at every depth.
rclone runs with ``--ask-password=false``, so an encrypted rclone
configuration fails a timer's run with a message instead of waiting at a
prompt nobody sees.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import contextmanager

from quirework.project import ConfigurationError, private_tmp_parent
from quirework.provenance.backup.base import TOP, BackupError, Transfer, run_tool
from quirework.provenance.backup.exchange import Sig, parse_time
from quirework.runs.storage import shown

# rclone's filter rules apply in order, and the first that matches wins. These
# two leave out every dot name: a file, or a directory with all it holds.
HIDDEN = ("--filter", "- .*", "--filter", "- .*/**")
# Lists a whole tree in a few batched requests where the remote can, rather
# than one request per directory. Every accepted version is a directory, so
# on Google Drive a run that copies nothing took two minutes for five hundred
# versions without it and seconds with it. A remote that cannot batch ignores
# the flag.
FAST = ("--fast-list",)
# rclone's exit status when the directory it was asked to list does not exist,
# which for a destination nothing has been copied to yet is the normal case.
DIRECTORY_NOT_FOUND = 3


class Rclone(Transfer):
    name = "rclone"
    keys = required = frozenset({"destination"})

    def configure(self, settings):
        self.destination = self.setting(settings, "destination").rstrip("/")
        # A bare relative path would be read from wherever the run started,
        # which for a timer is not where anyone looked.
        if not (self.destination.startswith("/") or ":" in self.destination):
            raise ConfigurationError(
                f"{self.where}: destination must be remote:path or an absolute "
                f"path, not {settings['destination']!r}"
            )

    def describe(self):
        local = self.destination.startswith("/")
        return f"rclone to {shown(self.destination) if local else self.destination}"

    def _path(self, area):
        if not area:
            return self.destination
        joiner = "" if self.destination.endswith(":") else "/"
        return f"{self.destination}{joiner}{area}"

    def _remote(self):
        """The configured remote the destination names, or ``None``.

        A local path names none, and neither does a connection string such as
        ``:local:/path``, which carries its whole configuration with it.
        """
        if self.destination.startswith((":", "/")) or ":" not in self.destination:
            return None
        return self.destination.split(":", 1)[0]

    def _rclone(self, args, log, *, ok=(0,)):
        executable = shutil.which("rclone")
        if executable is None:
            raise BackupError(
                "rclone is not installed; install it from your distribution "
                "(on Fedora, `sudo dnf install rclone`) and configure the remote "
                "with `rclone config`"
            )
        return run_tool(
            [executable, *args, "--ask-password=false"],
            log,
            what=f"rclone {args[0]}",
            ok=ok,
        )

    def ready(self, log):
        remote = self._remote()
        if remote is None:
            return
        listed = self._rclone(["listremotes"], log).stdout.split()
        if f"{remote}:" not in listed:
            raise BackupError(
                f"rclone has no remote named {remote}; create it with "
                f"`rclone config`, or correct the destination {self.destination}"
            )

    def top(self, log):
        done = self._rclone(
            ["lsf", "--max-depth", "1", self.destination],
            log,
            ok=(0, DIRECTORY_NOT_FOUND),
        )
        if done.returncode == DIRECTORY_NOT_FOUND:
            return set()
        return {line.rstrip("/") for line in done.stdout.splitlines() if line}

    def read(self, name, log):
        return self._rclone(["cat", self._path(name)], log).stdout

    def put(self, source, area, *, overwrite, within=(), rels=None, log):
        local = source.root / area if area else source.root
        if not local.is_dir() or rels == []:
            return
        if rels is not None:
            # `run` chose these because its copy is the later one, so rclone
            # copies each over what is there, whatever the times say.
            with _listed(rels) as listed:
                self._rclone(
                    [
                        "copy",
                        str(local),
                        self._path(area),
                        "-v",
                        "--files-from-raw",
                        listed,
                        "--ignore-times",
                        "--no-traverse",
                    ],
                    log,
                )
            return
        args = ["copy", str(local), self._path(area), "-v", *HIDDEN]
        args += ["--max-depth", "1"] if area == TOP else FAST
        if not overwrite:
            args += ["--immutable", "--checksum"]
        if within:
            for version in within:
                args += ["--filter", f"+ /{version}/**"]
            args += ["--filter", "- **"]
        self._rclone(args, log)

    def signatures(self, area, log):
        done = self._rclone(
            ["lsjson", "-R", "--files-only", *HIDDEN, *FAST, self._path(area)],
            log,
            ok=(0, DIRECTORY_NOT_FOUND),
        )
        if done.returncode == DIRECTORY_NOT_FOUND or not done.stdout.strip():
            return {}
        try:
            listed = json.loads(done.stdout)
            return {
                item["Path"]: Sig(int(item["Size"]), parse_time(item["ModTime"]))
                for item in listed
            }
        except (ValueError, KeyError, TypeError) as exc:
            raise BackupError(
                f"rclone lsjson printed something other than a file listing: {exc}"
            ) from exc

    def get(self, area, rels, into, log):
        if not rels:
            return
        with _listed(rels) as listed:
            self._rclone(
                [
                    "copy",
                    self._path(area),
                    str(into),
                    "-v",
                    "--files-from-raw",
                    listed,
                    "--no-traverse",
                ],
                log,
            )

    def listing(self, area, log):
        done = self._rclone(
            ["lsf", "-R", "--files-only", *HIDDEN, *FAST, self._path(area)],
            log,
            ok=(0, DIRECTORY_NOT_FOUND),
        )
        if done.returncode == DIRECTORY_NOT_FOUND:
            return set()
        return {line for line in done.stdout.splitlines() if line}

    def remove(self, area, rels, log):
        with _listed(rels) as listed:
            self._rclone(
                ["delete", self._path(area), "--files-from-raw", listed, "-v"], log
            )
        # A remote such as Google Drive keeps a folder after its last file goes.
        self._rclone(["rmdirs", self._path(area), "--leave-root"], log)


@contextmanager
def _listed(rels):
    """A private file naming ``rels``, one per line, for ``--files-from-raw``.

    The list goes through a file, so no session id is ever read by rclone as
    a flag or a pattern; ``--files-from-raw`` takes each line as is.
    """
    fd, listed = tempfile.mkstemp(
        prefix="quirework-backup-", suffix=".txt", dir=private_tmp_parent()
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("".join(f"{rel}\n" for rel in rels))
        yield listed
    finally:
        os.unlink(listed)
