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
from quirework.provenance.backup import exchange
from quirework.runs.storage import shown

PENDING, ACCEPTED, REMOVALS = "pending", "accepted", store.REMOVALS
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
    removed: int = 0  # pending files there that a decision covered
    detail: str = ""  # anything else the driver has to say
    # False where Quirework only handed the store to something it cannot see
    # into, such as a script, and so knows what was offered, not what arrived.
    seen: bool = True
    fetched: int = 0  # accepted versions brought in from there
    arrived: dict = field(default_factory=dict)  # pending files brought in: Sig
    dropped: int = 0  # pending files here that a decision covered

    def summary(self):
        counts = (
            f"{len(self.versions)} accepted version(s) and {self.pending} pending "
            "file(s)"
        )
        text = f"it holds {counts}" if self.seen else f"the store held {counts}"
        if self.fetched or self.arrived:
            text += (
                f"; brought in {self.fetched} accepted version(s) and "
                f"{len(self.arrived)} pending file(s)"
            )
        if self.removed:
            text += (
                f"; removed {self.removed} pending file(s) there that were "
                "accepted or rejected"
            )
        if self.dropped:
            text += (
                f"; removed {self.dropped} pending file(s) here that were "
                "accepted or rejected"
            )
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
    store's lock. `signatures` and `get` are what let several machines share
    one destination, and `run` needs them even when it only pushes, since a
    pending file there is compared with this store's copy before either moves.
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

    def put(self, source, area, *, overwrite, within=(), rels=None, log):
        """Copy ``source``'s files of ``area`` up, never deleting anything.

        Without ``overwrite`` a file already at the destination is kept, and
        one that differs from the store's raises: an accepted file never
        changes, so a difference is damage on one side. ``rels`` narrows the
        copy to those files, each copied over whatever the destination holds,
        which is how `run` sends only the pending copies that are later.
        """
        raise NotImplementedError

    def listing(self, area, log):
        """The relative paths of the files the destination holds in ``area``."""
        raise NotImplementedError

    def signatures(self, area, log):
        """``{rel: exchange.Sig}`` for the files the destination holds in ``area``."""
        raise NotImplementedError(
            f"the {self.name} driver cannot list sizes and times, which a run "
            "needs to compare a pending file with the store's copy; implement "
            "signatures() and get()"
        )

    def get(self, area, rels, into, log):
        """Copy these files of ``area`` down, under ``into``, keeping their times."""
        raise NotImplementedError(
            f"the {self.name} driver cannot copy files back from its destination; "
            "implement get()"
        )

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
        otherwise mix the two corpora. Several machines holding copies of one
        store share its identity on purpose, and share its destination.
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

    def _send_removals(self, source, log):
        ours = exchange.local_sigs(source.root, REMOVALS)
        theirs = self.signatures(REMOVALS, log)
        send = sorted(
            rel
            for rel, sig in ours.items()
            if rel not in theirs or exchange.newer(sig, theirs[rel])
        )
        if send:
            self.put(source, REMOVALS, overwrite=True, rels=send, log=log)

    def run(self, source, log, *, pull=False):
        """Copy what changes first, what never changes next, and delete last.

        Other machines may keep copies of this store and back up to the same
        destination, so a pending file there is never deleted for being absent
        here. It is deleted only when a decision covers it: an accepted copy of
        it, or a rejection of its session (`exchange.Decided.covers`). Both are
        put at the destination before anything they cover is removed there.

        Steps 2 to 5 run without the store's lock, so an ``accept`` can land
        among them: step 5 then missed the new version, and the pending capture
        is already gone from the store. Step 6 therefore takes the lock that
        ``accept`` and ``reject`` take, uploads whatever was decided since, and
        only then deletes what the decisions cover, at the destination and
        here. ``accept`` puts the new version in place before it clears the
        pending capture, so every session is in one area or the other at every
        moment, and so at the destination too. A run that fails before step 6
        has removed nothing.

        With ``pull`` the run also brings in what the destination holds that
        this store lacks: accepted versions, each checked against its own name;
        rejections; and pending files whose copy there is later.
        """
        self.ready(log)
        self.claim(source, log)
        root = source.root
        # 1. The identity and backup.toml, first, so even a run cut short
        #    leaves a destination that says whose it is.
        self.put(source, TOP, overwrite=True, log=log)
        fetched, arrived = 0, {}
        if pull:
            # 2. What other machines accepted and rejected, so the decisions
            #    below are made with everything the destination knows.
            listed = self.listing(ACCEPTED, log)
            missing = set(exchange.versions_there(listed)) - source.versions()
            fetched = exchange.fetch_versions(self, root, missing, listed, log)
            ours = exchange.local_sigs(root, REMOVALS)
            later = [
                rel
                for rel, sig in self.signatures(REMOVALS, log).items()
                if rel not in ours or exchange.newer(sig, ours[rel])
            ]
            exchange.fetch_newer(self, root, REMOVALS, later, log)
        # 3. Rejections, before any pending file they cover leaves there.
        self._send_removals(source, log)
        # 4. Pending captures: the later copy of each file wins, and a copy a
        #    decision covers goes nowhere.
        decided = exchange.Decided.read(root)
        ours = exchange.local_sigs(root, PENDING)
        theirs = self.signatures(PENDING, log)
        send = sorted(
            rel
            for rel, sig in ours.items()
            if not decided.covers(rel, sig)
            and (rel not in theirs or exchange.newer(sig, theirs[rel]))
        )
        if send:
            self.put(source, PENDING, overwrite=True, rels=send, log=log)
        if pull:
            later = [
                rel
                for rel, sig in theirs.items()
                if not decided.covers(rel, sig)
                and (rel not in ours or exchange.newer(sig, ours[rel]))
            ]
            arrived = exchange.fetch_newer(self, root, PENDING, later, log, settle=True)
        # 5. Accepted versions, which are never rewritten.
        before = source.versions()
        self.put(source, ACCEPTED, overwrite=False, log=log)
        # 6. Under the lock: catch up, then delete what the decisions cover.
        with store.locked(root):
            versions = source.versions()
            late = sorted(versions - before)
            if late:
                self.put(source, ACCEPTED, overwrite=False, within=late, log=log)
            self._send_removals(source, log)
            decided = exchange.Decided.read(root)
            stale = sorted(
                rel
                for rel, sig in self.signatures(PENDING, log).items()
                if decided.covers(rel, sig)
            )
            if stale:
                self.remove(PENDING, stale, log)
            dropped = exchange.drop_settled(root, decided)
            held = set(source.files(PENDING))
        return Outcome(
            versions=versions,
            pending=len(held),
            removed=len(stale),
            fetched=fetched,
            arrived=arrived,
            dropped=dropped,
        )
