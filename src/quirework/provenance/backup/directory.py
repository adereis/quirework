"""Back a store up into a directory on a mounted disk, in plain Python.

For a NAS share, an external drive, or any path the machine can write. No
external program is involved, so the copy does exactly what `Transfer` asks of
it: every file lands under a temporary name, is flushed to the disk, and only
then takes its real name, so a run cut off midway never leaves a truncated
file that a later run would take for a finished one.

Its one rule of its own is that the destination's top directory must already
exist. A network share that failed to mount usually leaves its mount point
behind as an empty local directory, and a backup that created what it did not
find would fill the laptop's own disk and report success.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from quirework.project import ConfigurationError
from quirework.provenance.backup.base import BackupError, Transfer, hidden
from quirework.runs.storage import shown

# Two copies of a pending capture are the same when their sizes match and
# their modification times agree to within this many seconds. Capture keeps
# the session's own time, so a capture that grew differs in both. The margin
# absorbs a destination file system that stores coarser times than the store.
MTIME_MARGIN = 1.0


class Directory(Transfer):
    name = "directory"
    keys = required = frozenset({"destination"})

    def configure(self, settings):
        value = Path(self.setting(settings, "destination")).expanduser()
        if not value.is_absolute():
            raise ConfigurationError(
                f"{self.where}: destination must be an absolute path, not "
                f"{settings['destination']!r}"
            )
        self.destination = value

    def describe(self):
        return f"directory {shown(self.destination)}"

    def ready(self, log):
        if not self.destination.is_dir():
            raise BackupError(
                f"{shown(self.destination)} does not exist or is not a directory. "
                "A backup never creates its destination's top directory, so an "
                "unmounted disk is never mistaken for an empty one: mount it, or "
                "create the directory once by hand"
            )
        if not os.access(self.destination, os.W_OK | os.X_OK):
            raise BackupError(f"{shown(self.destination)} is not writable")

    def top(self, log):
        if not self.destination.is_dir():
            return set()
        return {p.name for p in self.destination.iterdir()}

    def read(self, name, log):
        return (self.destination / name).read_text(encoding="utf-8")

    def _base(self, area):
        return self.destination / area if area else self.destination

    def put(self, source, area, *, overwrite, within=(), log):
        base = self._base(area)
        copied = 0
        for rel, path in source.files(area, within).items():
            target = base / rel
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise BackupError(f"{shown(target)} is not a regular file")
            if target.exists():
                held, there = path.stat(), target.stat()
                if overwrite:
                    if held.st_size == there.st_size and (
                        abs(held.st_mtime - there.st_mtime) <= MTIME_MARGIN
                    ):
                        continue
                elif held.st_size == there.st_size:
                    continue
                else:
                    raise BackupError(
                        f"{shown(target)} differs from the accepted file "
                        f"{shown(path)}, and an accepted file never changes: one "
                        "of the two is damaged. Run `quirework provenance store "
                        "verify` to check the store's copy"
                    )
            _copy(path, target)
            copied += 1
        log.write(f"directory: copied {copied} file(s) into {base}\n")

    def listing(self, area, log):
        base = self._base(area)
        if not base.is_dir():
            return set()
        return {
            p.relative_to(base).as_posix()
            for p in base.rglob("*")
            if not hidden(p.relative_to(base).as_posix()) and p.is_file()
        }

    def remove(self, area, rels, log):
        base = self._base(area)
        for rel in rels:
            path = base / rel
            path.unlink(missing_ok=True)
            parent = path.parent
            while parent != base and parent.is_relative_to(base):
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        log.write(f"directory: removed {len(rels)} file(s) from {base}\n")


def _copy(path, target):
    """Copy one file under a temporary name, flush it, then rename it in place.

    Only the modification time is carried over. Permission bits mean little on
    a share that maps every file to one mode, and setting them there can fail
    outright, while the time is what the next run compares.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "wb") as out, path.open("rb") as src:
            shutil.copyfileobj(src, out, 1 << 20)
            out.flush()
            os.fsync(out.fileno())
        st = path.stat()
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
