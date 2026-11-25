"""One store kept on several machines, meeting at a shared backup target.

Each machine has its own copy of a project's store, under the identity the
ledger names, and captures only its own sessions into it. A target that
several machines back up to is where those copies meet. The accepted half
merges by itself: a version never changes and is named by a hash of what it
holds, so any copy can be checked before it is used. The pending half is the
hard one. A capture grows while its session runs, it leaves the pending area
once it is accepted or rejected, and nothing outside the machine that made it
says it exists.

So nothing here deletes a pending file because some copy lacks it, which is
what made two machines on one target delete each other's captures. A pending
file is deleted only when a decision covers it:

- an accepted copy of the same file, of the same size and time, or newer;
- a rejection of its session, recorded in the store's ``removed/`` area with
  the moment it was made, which covers every capture of that session up to
  then.

Between two copies of one pending file the newer wins, by the modification
time capture keeps, which is the session's own and so always comes from one
machine's clock. Times are compared within `MARGIN`, since a remote such as
SFTP keeps them to the second.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from quirework.project import ConfigurationError
from quirework.provenance import store
from quirework.runs.storage import shown

# Two times closer than this are the same time: SFTP keeps a modification
# time to the second, and a capture copied there and back loses the rest.
MARGIN = 1.0

_FRACTION = re.compile(r"(\.\d{6})\d+")


def parse_time(stamp):
    """Seconds since the epoch from an RFC 3339 time, such as rclone prints.

    Fractions past the microsecond are dropped first, because Python before
    3.12 refuses them, and `MARGIN` makes them irrelevant anyway.
    """
    return datetime.fromisoformat(_FRACTION.sub(r"\1", stamp)).timestamp()


@dataclass(frozen=True)
class Sig:
    """What two copies of a file are compared by: its size and its time."""

    size: int
    mtime: float

    @classmethod
    def of(cls, path):
        st = Path(path).stat()
        return cls(st.st_size, st.st_mtime)


def newer(a, b):
    """Whether copy ``a`` is later than copy ``b``.

    Within the margin the larger one is later, since a capture only grows.
    Two copies of one size and time are the same capture.
    """
    if a.mtime > b.mtime + MARGIN:
        return True
    if b.mtime > a.mtime + MARGIN:
        return False
    return a.size > b.size


# --------------------------------------------------------------------------- #
# rejections
# --------------------------------------------------------------------------- #
def removal_path(root, family, unit):
    return Path(root) / store.REMOVALS / family / f"{unit}.toml"


def record_removal(root, family, unit, when_ns):
    """Mark every pending capture of a session up to ``when_ns`` as rejected.

    A later rejection of the same session moves the mark forward, never back.
    The file's modification time is set to the mark too, so a target compares
    two copies of it as it compares pending files.
    """
    path = removal_path(root, family, unit)
    held = read_removal(path)
    if held is not None and held >= when_ns:
        return path
    store._write_atomic(
        path,
        "# Rejected from the pending area. Every machine sharing this store\n"
        "# deletes its pending captures of the session made up to `through`,\n"
        "# in nanoseconds since the epoch. The ledger's removed list says why.\n"
        f'session = "{unit}"\n'
        f"through = {when_ns}\n",
    )
    os.utime(path, ns=(when_ns, when_ns))
    return path


def read_removal(path):
    """The moment a removal file covers, in nanoseconds, or ``None``."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    match = re.search(r"^through\s*=\s*(\d+)\s*$", text, re.MULTILINE)
    if match is None:
        raise ConfigurationError(
            f"{shown(path)} records no `through` time; it was not written "
            "by `quirework provenance reject`"
        )
    return int(match.group(1))


def removals(root):
    """``{(family, unit): seconds}`` for every rejection the store records."""
    found = {}
    for family in store.FAMILIES:
        base = Path(root) / store.REMOVALS / family
        if not base.is_dir():
            continue
        for path in base.iterdir():
            if path.name.startswith(".") or path.suffix != ".toml":
                continue
            through = read_removal(path)
            if through is not None:
                found[(family, path.stem)] = through / 1e9
    return found


# --------------------------------------------------------------------------- #
# which pending copies a decision covers
# --------------------------------------------------------------------------- #
@dataclass
class Decided:
    """What the store has settled: accepted copies by file, rejections by session."""

    accepted: dict = field(default_factory=dict)  # (family, rel) -> [Sig]
    rejected: dict = field(default_factory=dict)  # (family, unit) -> seconds

    @classmethod
    def read(cls, root):
        accepted = {}
        for family in store.FAMILIES:
            base = Path(root) / "accepted" / family
            if not base.is_dir():
                continue
            for version in base.iterdir():
                if not store._VERSION.fullmatch(version.name):
                    continue
                for path in version.rglob("*"):
                    if path.is_file():
                        rel = path.relative_to(version).as_posix()
                        accepted.setdefault((family, rel), []).append(Sig.of(path))
        return cls(accepted, removals(root))

    def covers(self, reference, sig):
        """Whether a pending copy, named ``<family>/<rel>``, is settled.

        It is when its session was rejected after it was captured, when an
        accepted copy is the same capture, and when an accepted copy is newer,
        since then it is an older capture of what was accepted.
        """
        family, _, rel = reference.partition("/")
        through = self.rejected.get((family, store.unit_of(family, rel)))
        if through is not None and sig.mtime <= through + MARGIN:
            return True
        for kept in self.accepted.get((family, rel), ()):
            same = kept.size == sig.size and abs(kept.mtime - sig.mtime) <= MARGIN
            if same or kept.mtime > sig.mtime + MARGIN:
                return True
        return False


# --------------------------------------------------------------------------- #
# bringing copies in
# --------------------------------------------------------------------------- #
def local_sigs(root, area):
    """``{rel: Sig}`` for the files a backup carries from one area."""
    from quirework.provenance.backup.base import Source

    return {rel: Sig.of(path) for rel, path in Source(root, "").files(area).items()}


def _stage(root, area):
    base = Path(root) / area
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=base, prefix=".arriving-"))


def versions_there(listed):
    """``{'<family>/<version>': [rel within the family]}`` from an accepted listing."""
    found = {}
    for rel in listed:
        parts = rel.split("/")
        if (
            len(parts) >= 3
            and parts[0] in store.FAMILIES
            and store._VERSION.fullmatch(parts[1])
        ):
            found.setdefault(f"{parts[0]}/{parts[1]}", []).append(rel)
    return found


def fetch_versions(driver, root, wanted, listed, log):
    """Copy the named accepted versions in from a target; return how many came.

    Each one is checked before it takes its place: the hashes of the files
    that arrived must give the version's own name, which is how the store
    named it when it was accepted. A version that does not is refused and
    named, and nothing of it stays behind.
    """
    from quirework.provenance.backup.base import ACCEPTED, BackupError

    there = versions_there(listed)
    wanted = sorted(v for v in wanted if v in there)
    if not wanted:
        return 0
    stage = _stage(root, ACCEPTED)
    try:
        driver.get(ACCEPTED, sorted(r for v in wanted for r in there[v]), stage, log)
        came = 0
        for version in wanted:
            family, name = version.split("/")
            arrived = stage / family / name
            hashes = {
                p.relative_to(arrived).as_posix(): store.sha256_file(p)
                for p in sorted(arrived.rglob("*"))
                if p.is_file()
            }
            if store.version_of(hashes) != name:
                raise BackupError(
                    f"{driver.describe()} holds accepted version {version}, but "
                    "its files do not hash to that name, so one of them is "
                    "damaged there; nothing of it was taken"
                )
            final = Path(root) / ACCEPTED / family / name
            final.parent.mkdir(parents=True, exist_ok=True)
            store._freeze(arrived)
            try:
                os.rename(arrived, final)
            except OSError:
                if not final.is_dir():
                    raise
                # Another process put the same version in place meanwhile;
                # its name promises the same bytes.
                continue
            came += 1
        return came
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def fetch_newer(driver, root, area, rels, log, *, settle=False):
    """Copy pending or removal files in where the target's copy is later.

    The files land in a hidden directory first and take their places under
    the store's lock, each one only if it is still later than what the store
    holds by then. With ``settle``, a pending file that a decision made
    meanwhile covers stays out too. Return ``{rel: Sig}`` of what took its
    place.
    """
    if not rels:
        return {}
    stage = _stage(root, area)
    try:
        driver.get(area, sorted(rels), stage, log)
        taken = {}
        with store.locked(root):
            decided = Decided.read(root) if settle else None
            for rel in sorted(rels):
                arrived = stage / rel
                if not arrived.is_file():
                    continue
                sig = Sig.of(arrived)
                if decided is not None and decided.covers(rel, sig):
                    continue
                final = Path(root) / area / rel
                if final.is_file() and not newer(sig, Sig.of(final)):
                    continue
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(arrived, final)
                taken[rel] = sig
        return taken
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def drop_settled(root, decided):
    """Delete this store's pending files a decision covers; return how many.

    Called under the store's lock, so no ``accept`` is halfway through one.
    """
    base = Path(root) / "pending"
    dropped = 0
    for rel, sig in local_sigs(root, "pending").items():
        if decided.covers(rel, sig):
            path = base / rel
            path.unlink(missing_ok=True)
            dropped += 1
            parent = path.parent
            while parent != base and parent.is_relative_to(base):
                if parent.parent == base:
                    break  # a family's own directory stays
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
    return dropped
