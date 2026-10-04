"""Copying a provenance store somewhere else, so it is never the only copy.

Claude Code deletes its own transcripts thirty days after they last changed,
and from then on the store holds the only copy of a session. So a store
describes its own backup, in ``backup.toml`` beside its ``store.toml``, and
``quirework provenance store backup`` carries it out. The file lives in the
store rather than in a project's configuration because what it names belongs
to the person and the machine, such as an rclone remote or a mounted disk,
while a project's configuration is committed and shared. It also lets a timer
back up every store under the provenance home without opening a project.

Each target in the file names a *driver*, which moves files, and where to.
``rclone``, ``directory``, ``git`` and ``command`` are built in, and a
``module:function`` names an installed extension, as ``publish.backend`` does.
Quirework, not the driver, decides what may be overwritten or deleted at the
destination; `base.Transfer.run` holds that order and says why it is safe.

Each run leaves a record under the XDG state directory, keyed by the store's
identity: when it ran, whether it finished, and what the destination held.
The records are per machine, because a backup is something this machine did,
and the identity keeps two stores from ever sharing one. ``store status``
reads them to say when a store was last backed up and what changed since.
"""

from __future__ import annotations

import fcntl
import importlib
import io
import json
import os
import re
import sys
import time
import tomllib
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from quirework.project import ConfigurationError, extension
from quirework.provenance import store
from quirework.provenance.backup.base import (
    PENDING,
    BackupError,
    Driver,
    Outcome,
    Source,
    Transfer,
)
from quirework.runs.storage import shown, write

__all__ = [
    "BackupError",
    "Driver",
    "Outcome",
    "Source",
    "Transfer",
    "back_up",
    "targets",
]

SETTINGS = "backup.toml"
BUILTIN = {
    "rclone": "quirework.provenance.backup.rclone:Rclone",
    "directory": "quirework.provenance.backup.directory:Directory",
    "git": "quirework.provenance.backup.git:Git",
    "command": "quirework.provenance.backup.command:Command",
}
# A target's name is part of its record's file name, so it stays plain.
_TARGET = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


# --------------------------------------------------------------------------- #
# what backup.toml declares
# --------------------------------------------------------------------------- #
def settings_path(root):
    return Path(root) / SETTINGS


def driver_for(target, settings, root):
    """The driver one target's table selects, built from that table."""
    where = f"{shown(settings_path(root))}: target {target}"
    name = settings.get("driver")
    if name in BUILTIN:
        module, attribute = BUILTIN[name].split(":")
        return getattr(importlib.import_module(module), attribute)(
            target, settings, root
        )
    if isinstance(name, str) and ":" in name:
        made = extension(name)(target, settings, root)
        if not isinstance(made, Driver):
            raise ConfigurationError(
                f"{where}: driver {name} must return a quirework backup Driver"
            )
        return made
    raise ConfigurationError(
        f"{where}: driver must be one of {', '.join(BUILTIN)} or an installed "
        f"module:function, not {name!r}"
    )


def targets(root):
    """The drivers ``backup.toml`` declares, in its order; ``None`` without one."""
    path = settings_path(root)
    if not path.exists():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot read {shown(path)}: {exc}") from exc
    unknown = sorted(set(data) - {"targets"})
    if unknown:
        raise ConfigurationError(
            f"{shown(path)}: unknown setting {unknown[0]}; the file holds "
            "[targets.<name>] tables"
        )
    declared = data.get("targets")
    if not isinstance(declared, dict) or not declared:
        raise ConfigurationError(
            f"{shown(path)} declares no targets; add a [targets.<name>] table, "
            "or delete the file"
        )
    drivers = []
    for name, settings in declared.items():
        if not _TARGET.fullmatch(name):
            raise ConfigurationError(
                f"{shown(path)}: a target's name must be a plain name such as "
                f"'gdrive', not {name!r}"
            )
        if not isinstance(settings, dict):
            raise ConfigurationError(f"{shown(path)}: targets.{name} must be a table")
        drivers.append(driver_for(name, settings, root))
    return drivers


def select(drivers, names, root):
    """The drivers named, in the file's order; all of them when none is named."""
    if not names:
        return drivers
    declared = {d.target: d for d in drivers}
    missing = [n for n in names if n not in declared]
    if missing:
        raise ConfigurationError(
            f"{shown(settings_path(root))} declares no target {missing[0]}; it "
            f"declares {', '.join(declared)}"
        )
    return [d for d in drivers if d.target in names]


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #
def state_dir(identity):
    """Where this machine keeps its records of one store's backups."""
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return base / "quirework" / "backup" / identity


def read_record(identity, target):
    """The record of one target's last runs, or ``None`` before the first."""
    path = state_dir(identity) / f"{target}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"cannot read {shown(path)}: {exc}") from exc


def _stamp(when):
    return datetime.fromtimestamp(when, UTC).isoformat(timespec="seconds")


def _epoch(stamp):
    return datetime.fromisoformat(stamp).timestamp()


def signatures(source):
    """``{rel: [size, mtime_ns]}`` of the store's pending files, as they stand.

    Taken when a run starts, so a capture that grows while the run uploads
    reads as changed afterwards rather than as safely copied.
    """
    found = {}
    for rel, path in source.files(PENDING).items():
        try:
            st = path.stat()
        except FileNotFoundError:
            continue
        found[rel] = [st.st_size, st.st_mtime_ns]
    return found


@contextmanager
def _running(directory, target, root):
    """One run per store and target at a time, across every process."""
    with (directory / f"{target}.lock").open("a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError(
                f"a backup of {shown(root)} to {target} is already running"
            ) from None
        yield


def back_up(source, driver):
    """Run one target and record the outcome; return the `Outcome`.

    A run that fails is recorded as the last attempt, beside the last success,
    and the error is raised again: the record is for ``store status``, not a
    substitute for saying so.
    """
    directory = state_dir(source.identity)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _running(directory, driver.target, source.root):
        record = read_record(source.identity, driver.target) or {}
        record.update(
            store=str(source.root),
            target=driver.target,
            driver=driver.name,
            destination=driver.describe(),
        )
        started = time.time()
        captured = signatures(source)
        # Marked as begun before anything is copied, so a run the machine cut
        # off, by losing power or by a timer's SIGTERM, reads as unfinished
        # rather than leaving the last success to speak for it.
        record["attempt"] = {
            "started": _stamp(started),
            "finished": None,
            "ok": None,
            "error": None,
        }
        write(directory / f"{driver.target}.json", record)
        log_path = directory / f"{driver.target}.log"
        with log_path.open("w", encoding="utf-8") as log:
            log.write(
                f"# {driver.describe()}\n# store {source.root}, started "
                f"{_stamp(started)}\n"
            )
            try:
                outcome = driver.run(source, log)
            except Exception as exc:
                record["attempt"] = {
                    "started": _stamp(started),
                    "finished": _stamp(time.time()),
                    "ok": False,
                    "error": str(exc),
                }
                write(directory / f"{driver.target}.json", record)
                raise
            log.write(f"# finished {_stamp(time.time())}: {outcome.summary()}\n")
        finished = time.time()
        record["attempt"] = {
            "started": _stamp(started),
            "finished": _stamp(finished),
            "ok": True,
            "error": None,
        }
        record["success"] = {
            "started": _stamp(started),
            "finished": _stamp(finished),
            "versions": sorted(outcome.versions),
            "pending": captured,
            "removed": outcome.removed,
            "seen": outcome.seen,
        }
        write(directory / f"{driver.target}.json", record)
        return outcome


def log_of(identity, target):
    return state_dir(identity) / f"{target}.log"


# --------------------------------------------------------------------------- #
# what status and config check say
# --------------------------------------------------------------------------- #
def _sessions(rels):
    """The pending sessions a set of store-relative pending paths belongs to."""
    found = set()
    for rel in rels:
        family, _, rest = rel.partition("/")
        if family in store.FAMILIES and rest:
            found.add((family, store.unit_of(family, rest)))
    return found


def report(source, now=None):
    """Lines for ``store status`` about each target, and whether a run is due.

    ``None`` for the lines when the store declares no backup. A
    ``backup.toml`` that cannot be read is reported as a line rather than
    raised, so status still shows everything else it knows.
    """
    try:
        drivers = targets(source.root)
    except ConfigurationError as exc:
        return [f"backup    {exc}"], True
    if drivers is None:
        return None, False
    now = time.time() if now is None else now
    versions = source.versions()
    current = signatures(source)
    lines, due = [], False
    for driver in drivers:
        lines.append(f"backup    {driver.target}: {driver.describe()}")
        try:
            record = read_record(source.identity, driver.target) or {}
        except ConfigurationError as exc:
            lines.append(f"          {exc}")
            due = True
            continue
        attempt, success = record.get("attempt"), record.get("success")
        if attempt and attempt.get("ok") is None:
            age = store._age(now - _epoch(attempt["started"]))
            lines.append(
                f"          last run started {age} ago and has not finished: it "
                "is still running, or it was cut off"
            )
            due = True
        elif attempt and not attempt.get("ok"):
            age = store._age(now - _epoch(attempt["finished"]))
            lines.append(f"          last run failed {age} ago: {attempt['error']}")
            due = True
        if not success:
            lines.append("          never backed up")
            due = True
            continue
        text = f"last succeeded {store._age(now - _epoch(success['finished']))} ago"
        accepted = len(versions - set(success.get("versions", ())))
        held = success.get("pending", {})
        changed = len(
            _sessions(rel for rel, sig in current.items() if held.get(rel) != sig)
        )
        if accepted or changed:
            parts = []
            if accepted:
                parts.append(f"{accepted} accepted version(s)")
            if changed:
                parts.append(f"{changed} pending session(s)")
            text += f"; not yet backed up: {' and '.join(parts)}"
            due = True
        lines.append(f"          {text}")
    return lines, due


def unconfigured(source):
    """What to say about a store that declares no backup."""
    repository = store.versioned(source.root)
    if repository is not None and repository != source.root:
        return f"none configured; the repository at {shown(repository)} versions it"
    return (
        "none configured, so once Claude Code prunes a transcript this store "
        f"holds the only copy; describe one in {shown(settings_path(source.root))} "
        "(`quirework provenance store backup --help` shows how)"
    )


# --------------------------------------------------------------------------- #
# the command
# --------------------------------------------------------------------------- #
def _duration(seconds):
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds} s"
    return f"{seconds // 60} min {seconds % 60} s"


def stores_in_home():
    """Every store under the provenance home, and the home itself."""
    home = store.find_home()
    if not home.path.is_dir():
        return home, []
    found = []
    for root in sorted(home.path.iterdir()):
        identity = store._identity(root) if root.is_dir() else None
        if identity is not None:
            found.append(Source(root, identity))
    return home, found


def _one_store(source, names, check, out):
    """Back up or check one store; return ``(configured, failures)``."""
    print(f"store {shown(source.root)}", file=out)
    try:
        drivers = targets(source.root)
    except ConfigurationError as exc:
        print(f"  {exc}", file=out)
        return True, 1
    if drivers is None:
        print(f"  {unconfigured(source)}", file=out)
        return False, 0
    if names:
        chosen = [d for d in drivers if d.target in names]
        if not chosen:
            print(f"  declares none of the targets {', '.join(names)}", file=out)
            return False, 0
    else:
        chosen = drivers
    failures = 0
    for driver in chosen:
        print(f"  {driver.target:9} {driver.describe()}", file=out)
        out.flush()
        started = time.time()
        try:
            if check:
                driver.check(io.StringIO())
                print("            ready", file=out)
            else:
                outcome = back_up(source, driver)
                print(
                    f"            done in {_duration(time.time() - started)}; "
                    f"{outcome.summary()}",
                    file=out,
                )
        except (ConfigurationError, OSError, RuntimeError, ValueError) as exc:
            # One target's failure never stops the next one, or the next store:
            # each is reported here and counted in the exit status.
            failures += 1
            if check:
                print(f"            not ready: {exc}", file=out)
            else:
                print(
                    f"            failed after {_duration(time.time() - started)}: "
                    f"{exc}",
                    file=out,
                )
                print(
                    f"            the log is {shown(log_of(source.identity, driver.target))}",
                    file=out,
                )
    return True, failures


def cmd_backup(args):
    out = sys.stdout
    names = args.target or []
    if args.every_store:
        home, sources = stores_in_home()
        print(f"stores under {home.describe()}", file=out)
        if not sources:
            print(
                "no provenance store is there; if the stores live elsewhere, "
                "point provenance.home at them",
                file=sys.stderr,
            )
            return 1
    else:
        opened = store.open_store()
        sources = [Source(opened.root, opened.identity)]
        drivers = targets(opened.root)
        if drivers is None:
            raise ConfigurationError(
                f"no backup is configured for this store: describe one in "
                f"{shown(settings_path(opened.root))}; `quirework provenance store "
                "backup --help` shows how"
            )
        select(drivers, names, opened.root)
    configured, failures = 0, 0
    for source in sources:
        did, failed = _one_store(source, names, args.check, out)
        configured += did
        failures += failed
    if not configured:
        print("nothing was backed up: no store declares a target", file=sys.stderr)
        return 1
    return 1 if failures else 0
