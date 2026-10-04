"""What a backup driver is, and the order a copying driver's run follows.

A driver is built from one target's table in ``backup.toml``. It says what it
copies to, checks that the destination can be reached, and runs. A driver that
copies file by file subclasses `Transfer`: it supplies six small operations on
its destination, and `Transfer.run` puts them in the one order that keeps a
session from going missing there. The rules live here, once, rather than in
each driver, because a driver that got them subtly wrong would look like it
worked until the day the backup was needed.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from quirework.project import ConfigurationError
from quirework.provenance import store
from quirework.runs.storage import shown

PENDING, ACCEPTED = "pending", "accepted"
# The top level of the store: its identity, its backup.toml, and any script a
# `command` target runs. The empty name is how a driver is asked for it.
TOP = ""


class BackupError(RuntimeError):
    """A run that could not finish, said in a way a person can act on."""


def hidden(rel):
    """Whether a store-relative path is one a backup never copies.

    Every name starting with a dot: an ``accept``'s half-built ``.accepting-*``
    directory, a capture's ``.<name>.<random>`` file still being written, a
    git backup's ``.git``. None of them is a session.
    """
    return any(part.startswith(".") for part in Path(rel).parts)


@dataclass(frozen=True)
class Source:
    """The store a run copies: its directory and the identity it records."""

    root: Path
    identity: str

    def files(self, area, within=()):
        """``{rel: path}`` for every file a backup copies from ``area``.

        ``within`` narrows ``accepted`` to the named ``<family>/<version>``
        directories. ``TOP`` is the store's top level, files only.
        """
        if area == TOP:
            return {
                p.name: p
                for p in sorted(self.root.iterdir())
                if not hidden(p.name) and p.is_file() and not p.is_symlink()
            }
        base = self.root / area
        roots = [base / w for w in within] if within else [base]
        found = {}
        for start in roots:
            if not start.is_dir():
                continue
            for path in sorted(start.rglob("*")):
                rel = path.relative_to(base).as_posix()
                if not hidden(rel) and path.is_file() and not path.is_symlink():
                    found[rel] = path
        return found

    def versions(self):
        """Every accepted version, as ``<family>/<version>``."""
        return store.accepted_versions(self.root)


@dataclass
class Outcome:
    """What the destination holds once a run has finished."""

    versions: set = field(default_factory=set)  # accepted versions it holds
    pending: int = 0  # pending files it holds
    removed: int = 0  # pending files removed because the store dropped them
    detail: str = ""  # anything else the driver has to say
    # False where Quirework only handed the store to something it cannot see
    # into, such as a script, and so knows what was offered, not what arrived.
    seen: bool = True

    def summary(self):
        counts = (
            f"{len(self.versions)} accepted version(s) and {self.pending} pending "
            "file(s)"
        )
        text = f"it holds {counts}" if self.seen else f"the store held {counts}"
        if self.removed:
            text += f"; removed {self.removed} pending file(s) the store no longer has"
        return f"{text}; {self.detail}" if self.detail else text


class Driver:
    """One way of copying a store, built from one target's table.

    ``keys`` are the settings the table may hold besides ``driver``, and
    ``required`` the ones it must. A table holding anything else is refused, so
    a misspelled key fails where it was written instead of leaving a default
    in force.
    """

    name = ""
    keys = frozenset()
    required = frozenset()

    def __init__(self, target, settings, root):
        self.target = target
        self.root = Path(root)
        where = f"{shown(self.root / 'backup.toml')}: target {target}"
        unknown = sorted(set(settings) - {"driver"} - self.keys)
        if unknown:
            known = ", ".join(sorted(self.keys)) or "nothing else"
            raise ConfigurationError(
                f"{where}: unknown setting {unknown[0]}; the {self.name} driver "
                f"reads {known}"
            )
        missing = sorted(self.required - set(settings))
        if missing:
            raise ConfigurationError(
                f"{where}: the {self.name} driver needs {missing[0]}"
            )
        self.where = where
        self.configure(settings)

    def configure(self, settings):
        """Read and validate this driver's own settings; no I/O."""

    def describe(self):
        """What this target copies to, as a person reads it."""
        raise NotImplementedError

    def check(self, log):
        """Refuse a destination that cannot be used, copying nothing."""
        raise NotImplementedError

    def run(self, source, log):
        """Copy ``source``; return an `Outcome`, or raise `BackupError`."""
        raise NotImplementedError

    def setting(self, settings, key):
        """A required non-empty string setting."""
        value = settings.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"{self.where}: {key} must be a non-empty string")
        return value.strip()


def run_tool(argv, log, *, what, cwd=None, env=None, ok=(0,)):
    """Run one external program, its output going to the run's log.

    The program never reads from the terminal: a backup runs from a timer as
    often as from a shell, and a prompt there would wait forever. A status
    outside ``ok`` raises with the last thing the program said, since that is
    usually the one line that explains it; ``what`` names the step in that
    message, such as ``rclone copy``.
    """
    log.write(f"$ {shlex.join(str(a) for a in argv)}\n")
    log.flush()
    try:
        done = subprocess.run(
            [str(a) for a in argv],
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BackupError(f"cannot run {what}: {exc}") from exc
    log.write(done.stdout)
    log.write(done.stderr)
    log.flush()
    if done.returncode not in ok:
        said = [ln for ln in (done.stderr + done.stdout).splitlines() if ln.strip()]
        last = said[-1].strip() if said else "it printed nothing"
        raise BackupError(f"{what} exited with status {done.returncode}: {last}")
    return done


class Transfer(Driver):
    """A driver that copies file by file, in the order Quirework decides.

    A subclass supplies the operations below and inherits `run`. None of them
    deletes anything except `remove`, and `run` calls that last, holding the
    store's lock.
    """

    def ready(self, log):
        """Refuse a destination that cannot be written; create nothing."""
        raise NotImplementedError

    def top(self, log):
        """Every name at the destination's top level; empty when it is absent."""
        raise NotImplementedError

    def read(self, name, log):
        """The text of one file at the destination's top level."""
        raise NotImplementedError

    def put(self, source, area, *, overwrite, within=(), log):
        """Copy ``source``'s files of ``area`` up, never deleting anything.

        Without ``overwrite`` a file already at the destination is kept, and
        one that differs from the store's raises: an accepted file never
        changes, so a difference is damage on one side.
        """
        raise NotImplementedError

    def listing(self, area, log):
        """The relative paths of the files the destination holds in ``area``."""
        raise NotImplementedError

    def remove(self, area, rels, log):
        """Delete these files of ``area`` at the destination."""
        raise NotImplementedError

    def check(self, log):
        self.ready(log)
        self.top(log)

    def claim(self, source, log):
        """Refuse a destination that holds anything but this store's backup.

        The identity is what keeps two stores from sharing a destination: a
        path pasted from the book's backup.toml into the site's would
        otherwise mix the two corpora, and the next run's pruning would
        delete one store's pending files because the other does not have them.
        """
        names = {n for n in self.top(log) if not hidden(n)}
        if store.IDENTITY in names:
            found = store.identity_in(
                self.read(store.IDENTITY, log),
                f"{store.IDENTITY} at {self.describe()}",
            )
            if found != source.identity:
                raise BackupError(
                    f"{self.describe()} holds store {found}, not this store "
                    f"({source.identity}); give each store a destination of its own"
                )
        elif names:
            raise BackupError(
                f"{self.describe()} holds files but no {store.IDENTITY}, so it is "
                "not a backup of this store; name an empty or new destination"
            )

    def run(self, source, log):
        """Copy what changes first, what never changes next, and delete last.

        A pending file is removed at the destination only once the version
        that replaced it is there. Steps 2 and 3 run without the store's lock,
        so an ``accept`` can land between them: step 3 then missed the new
        version, and the pending capture is already gone from the store. Step
        4 therefore takes the lock that ``accept`` takes, uploads whatever was
        accepted since step 3, and only then removes the pending files the
        store no longer has. ``accept`` puts the new version in place before it
        clears the pending capture, so every session is in one area or the
        other at every moment, and so at the destination too. A run that fails
        before step 4 has removed nothing.
        """
        self.ready(log)
        self.claim(source, log)
        # 1. The identity and backup.toml, first, so even a run cut short
        #    leaves a destination that says whose it is.
        self.put(source, TOP, overwrite=True, log=log)
        # 2. Captures, including the ones that grew since the last run.
        self.put(source, PENDING, overwrite=True, log=log)
        # 3. Accepted versions, which are never rewritten.
        before = source.versions()
        self.put(source, ACCEPTED, overwrite=False, log=log)
        # 4. Under the lock: catch up, then prune.
        with store.locked(source.root):
            versions = source.versions()
            late = sorted(versions - before)
            if late:
                self.put(source, ACCEPTED, overwrite=False, within=late, log=log)
            held = set(source.files(PENDING))
            stale = self.listing(PENDING, log) - held
            if stale:
                self.remove(PENDING, sorted(stale), log)
        return Outcome(versions=versions, pending=len(held), removed=len(stale))
