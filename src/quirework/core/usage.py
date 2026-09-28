"""What ran, when, and in which project -- recorded locally, sent nowhere.

Quirework has grown a lot of commands and nobody knows which of them earn
their keep. This answers that from the only evidence that is not a guess:
the commands the CLI actually dispatched. One JSON object per invocation,
appended under the user's state directory, read back by `quirework usage`.

It is deliberately not a transcript. A record carries the command, the
procedure's name when the command names one, the project root, the exit
status and how long it took. COMMAND ARGUMENTS ARE NEVER RECORDED:
`provenance search` takes a verbatim phrase out of the author's notes and
`publish redpen` takes review prose, so a file that quietly accumulated argv
would be a private corpus nobody agreed to keep. The one argument that is
recorded is a procedure's name, and only once the project has resolved it
to a file under its own `sops.root`, because "which procedure" is the
question this module exists to answer.

The store is per-user, not per-project, which is the opposite of how
`Project.state_root` works and is the point: the interesting comparison is
across a person's books, so the project root is a FIELD here rather than a
directory. A project switches the whole thing off with `enabled = false`
under `[usage]`; `QUIREWORK_USAGE=0` switches it off for one process or
one shell, which is what a shared machine or a test harness wants.

Recording never costs a command its result. An unwritable store is
reported once on stderr and then left alone for the rest of the process --
degraded out loud rather than in silence, and never by failing the work the
author actually asked for.

Three kinds of invocation are outside the count, and a report read
without knowing that will understate them. `--help` is not use of a
command. The `config` and `usage` groups inspect the tool rather than do
its work. And a procedure read straight off disk by an agent following a
generated skill never reaches this code, so procedure counts are a floor
rather than a total.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from quirework import __version__
from quirework.project import ConfigurationError, current_project
from quirework.runs.storage import lock, shown

SCHEMA = 1
PATTERN = "usage-%Y-%m.jsonl"

# Commands whose first argument names a procedure rather than carrying the
# author's material. Extend this only for an argument drawn from a set the
# tool itself defines; a bare word on any other command line is as likely to
# be a phrase out of the book as it is to be a keyword.
NAMED = {("sop", "show"), ("sop", "explain")}

# What a procedure's name can look like: a file stem, nothing else. A search
# phrase has spaces, a path has separators, an option has a leading dash, and
# none of the three gets past this.
STEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")

_silenced = False  # one warning per process, not one per command


def enabled(project) -> bool:
    """Whether this project records anything, environment first.

    The environment wins over the file for the same reason `QUIREWORK_PROJECT`
    does: someone running on a borrowed machine, or a test harness driving a
    project it did not write, needs an off switch that does not require
    editing the book's configuration.
    """
    override = os.environ.get("QUIREWORK_USAGE")
    if override is not None:
        return override.strip().lower() not in ("", "0", "off", "false", "no")
    return bool(project.get("usage.enabled", True))


def store(project) -> Path:
    """The directory holding the records, shared by every project of this user."""
    default = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return project.path("usage.store", str(default / "quirework" / "usage"))


def _target(command, argv):
    first = next((a for a in argv if not a.startswith("-")), None)
    if tuple(command) not in NAMED or not first or not STEM.match(first):
        return None
    return first


class Outcome:
    """The status a dispatched command reached, filled in by the caller."""

    __slots__ = ("status",)

    def __init__(self):
        self.status = None


@contextmanager
def record(command, argv=(), *, project):
    """Time one dispatched command and append its record when it finishes.

    The caller sets `.status` on the yielded outcome. A command that raises
    leaves it unset and is recorded with a null status, which is how the
    report tells a failure apart from a clean non-zero exit.
    """
    key = tuple(command)
    outcome = Outcome()
    if key[0] == "usage" or not enabled(project):
        yield outcome
        return
    started = time.monotonic()
    try:
        yield outcome
    finally:
        entry = {
            "schema": SCHEMA,
            "at": _stamp(),
            "command": " ".join(key),
            "project": str(project.root),
            "status": outcome.status,
            "ms": round((time.monotonic() - started) * 1000),
            "version": __version__,
        }
        target = _target(key, argv)
        if target and outcome.status == 0:
            # A name only gets kept once the project resolved it to a
            # procedure. A failed lookup is the one case where the argument
            # was not drawn from `sops.root`, and dropping it is what lets
            # this file promise that a recorded name is a real procedure.
            entry["target"] = target
        append(_named(entry, project), project=project)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _named(entry, project):
    name = project.get("project.name")
    if isinstance(name, str) and name.strip():
        entry["name"] = name.strip()
    return entry


def seat(workflow, config, *, project=None):
    """Record that a model seat was launched: through which harness, on
    which model, at what effort, for which kind of workflow.

    This answers a question the command count cannot: whether a harness or
    a model is used at all, across every book. It records nothing about the
    seat's assignment. No prompt, no material name, no persona and no seat
    id reach the store, for the same reason command arguments never do; a
    file that accumulated them would be a private corpus nobody agreed to
    keep. A run stopped by a provider is still a launch, so the record is
    made before the process starts and carries no outcome.
    """
    project = current_project() if project is None else project
    if not enabled(project):
        return
    entry = {
        "schema": SCHEMA,
        "kind": "seat",
        "at": _stamp(),
        "project": str(project.root),
        "workflow": workflow,
        "harness": config["harness"],
        "model": config["model"],
        "effort": config.get("effort"),
        "version": __version__,
    }
    append(_named(entry, project), project=project)


def append(entry, *, project):
    """Add one record, or say once why this run cannot.

    A command's result must not depend on whether a metrics file was
    writable, so the failure is reported and then dropped rather than
    raised. It is reported once: a full disk would otherwise print the same
    line for every command in a build.
    """
    global _silenced
    if _silenced:
        return
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    try:
        directory = store(project)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / datetime.now(timezone.utc).strftime(PATTERN)
        with (
            lock(directory / ".lock", wait=True),
            path.open("a", encoding="utf-8") as f,
        ):
            f.write(line)
    except (OSError, ConfigurationError) as exc:
        _silenced = True
        print(f"quirework: not recording usage this run: {exc}", file=sys.stderr)


def read(project, *, since=None, here=False):
    """Every record in the store, oldest first, with a count of torn lines.

    A line that will not parse is a partial write from an interrupted
    command, not a bug in the reader. It is counted and reported rather than
    skipped in silence, because a store that is quietly losing records
    should say so.

    `since` accepts either form `when` does, and is normalized here rather
    than at the command line, so no caller can hand an age to a comparison
    that only understands a date.

    `here` means this project, not this directory. A book gets checked out
    more than once -- a canon pass in one worktree while tooling moves in
    another -- and a command run in either one is the same book's work, so
    every checkout's copy of the project counts.
    """
    directory = store(project)
    since = when(since) if since else None
    roots = None
    if here:
        from quirework.provenance import worktrees

        roots = {str(p) for p in worktrees.project_trees(project.root)}
    rows, damaged = [], 0
    if not directory.is_dir():
        return rows, damaged
    for path in sorted(directory.glob("usage-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                damaged += 1
                continue
            if not isinstance(entry, dict) or "at" not in entry:
                damaged += 1
                continue
            if since and entry["at"][:10] < since:
                continue
            if roots is not None and entry.get("project") not in roots:
                continue
            rows.append(entry)
    rows.sort(key=lambda e: e["at"])
    return rows, damaged


def when(text):
    """A `--since` or `--before` value as an ISO date.

    Both forms a person reaches for are accepted, so nobody has to guess
    which one this tool wanted: an age like `30d`, `12w` or `6m`, or a
    literal date like `2026-01-01`.
    """
    text = text.strip()
    match = re.fullmatch(r"(\d+)\s*([dwm])", text, re.I)
    if match:
        scale = {"d": 1, "w": 7, "m": 30}[match[2].lower()]
        # UTC, because that is the clock the `at` stamps it filters were
        # written by; a local date is a day out on either side of midnight.
        today = datetime.now(timezone.utc).date()
        return (today - timedelta(days=int(match[1]) * scale)).isoformat()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"not a date or an age like 30d: {text!r}") from exc


def _is_seat(entry) -> bool:
    return entry.get("kind") == "seat"


def summarize(rows):
    """Counts by command, by procedure, by seat and by project, plus the span.

    A record without a `kind` is a command; the field arrived with seat
    records and older files never wrote it.
    """
    commands = [e for e in rows if not _is_seat(e)]
    seats = [e for e in rows if _is_seat(e)]
    procedures = {}
    for entry in commands:
        target = entry.get("target")
        if not target:
            continue
        seen = procedures.setdefault(
            target, {"count": 0, "first": None, "last": None, "projects": {}}
        )
        seen["count"] += 1
        # One name can be a procedure in several books, and the store is
        # shared across all of them, so a count without its projects cannot
        # be read back to the book it came from.
        seen["projects"][entry["project"]] = (
            seen["projects"].get(entry["project"], 0) + 1
        )
        at = entry["at"]
        seen["first"] = at if seen["first"] is None else min(seen["first"], at)
        seen["last"] = at if seen["last"] is None else max(seen["last"], at)
    pairs = {}
    for entry in seats:
        key = f"{entry['harness']} {entry['model']}"
        seen = pairs.setdefault(
            key,
            {
                "harness": entry["harness"],
                "model": entry["model"],
                "count": 0,
                "first": None,
                "last": None,
                "workflows": {},
                "efforts": {},
                "projects": {},
            },
        )
        seen["count"] += 1
        at = entry["at"]
        seen["first"] = at if seen["first"] is None else min(seen["first"], at)
        seen["last"] = at if seen["last"] is None else max(seen["last"], at)
        for field, value in (
            ("workflows", entry.get("workflow")),
            ("efforts", entry.get("effort")),
            ("projects", entry["project"]),
        ):
            seen[field][value] = seen[field].get(value, 0) + 1
    names = {}
    for entry in rows:
        if entry.get("name"):
            names[entry["project"]] = entry["name"]
    return {
        "events": len(commands),
        "first": rows[0]["at"] if rows else None,
        "last": rows[-1]["at"] if rows else None,
        "commands": dict(Counter(e["command"] for e in commands).most_common()),
        "procedures": dict(
            sorted(procedures.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
        ),
        "seats": {
            "count": len(seats),
            "harnesses": dict(Counter(e["harness"] for e in seats).most_common()),
            "workflows": dict(Counter(e["workflow"] for e in seats).most_common()),
            "pairs": dict(
                sorted(pairs.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
            ),
        },
        "projects": dict(Counter(e["project"] for e in rows).most_common()),
        "project_names": names,
        "months": dict(sorted(Counter(e["at"][:7] for e in rows).items())),
        "failures": sum(1 for e in commands if e.get("status") not in (0, None)),
        "errors": sum(1 for e in commands if e.get("status") is None),
    }


def prune(project, cutoff, *, dry_run=False):
    """Drop records older than a date, and any file left with nothing in it."""
    directory = store(project)
    dropped, files = 0, 0
    if not directory.is_dir():
        return dropped, files
    with lock(directory / ".lock", wait=True):
        for path in sorted(directory.glob("usage-*.jsonl")):
            lines = [
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            keep = []
            for line in lines:
                try:
                    entry = json.loads(line)
                    fresh = isinstance(entry, dict) and entry.get("at", "")[:10] >= (
                        cutoff
                    )
                except json.JSONDecodeError:
                    fresh = False  # a torn line has no date to defend it
                if fresh:
                    keep.append(line)
            if len(keep) == len(lines):
                continue
            dropped += len(lines) - len(keep)
            if dry_run:
                files += not keep
                continue
            if not keep:
                path.unlink()
                files += 1
                continue
            with tempfile.NamedTemporaryFile(
                "w", dir=directory, delete=False, encoding="utf-8"
            ) as f:
                f.write("\n".join(keep) + "\n")
                temporary = f.name
            os.replace(temporary, path)
    return dropped, files


def _show(project, options):
    rows, damaged = read(project, since=options.since, here=options.here)
    report = summarize(rows)
    if options.json:
        print(json.dumps({**report, "store": str(store(project))}, indent=2))
        return 0
    where = shown(store(project))
    if not rows:
        print(f"No usage recorded in {where}.")
        if not enabled(project):
            print("Recording is off for this project.")
        return 0
    span = f"{report['first'][:10]} to {report['last'][:10]}"
    print(
        f"{report['events']} command(s) and {report['seats']['count']} seat "
        f"launch(es) recorded, {span}, in {where}."
    )
    print("\nCommands")
    for name, count in list(report["commands"].items())[: options.limit]:
        print(f"  {count:>6}  {name}")
    if report["procedures"]:
        print("\nProcedures")
        for name, seen in list(report["procedures"].items())[: options.limit]:
            window = f"{seen['first'][:10]} to {seen['last'][:10]}"
            print(f"  {seen['count']:>6}  {name:<28}  {window}")
    if report["seats"]["count"]:
        print("\nSeats")
        for key, seen in list(report["seats"]["pairs"].items())[: options.limit]:
            kinds = ", ".join(f"{k} {v}" for k, v in seen["workflows"].items())
            print(f"  {seen['count']:>6}  {key:<40}  {kinds}")
    print("\nProjects")
    for path, count in list(report["projects"].items())[: options.limit]:
        label = report["project_names"].get(path, "")
        print(f"  {count:>6}  {shown(path):<40}  {label}")
    print("\nBy month")
    for month, count in report["months"].items():
        print(f"  {count:>6}  {month}")
    if report["failures"] or report["errors"]:
        print(
            f"\n{report['failures']} non-zero exit(s), "
            f"{report['errors']} command(s) that raised."
        )
    if damaged:
        print(f"{damaged} unreadable record(s) skipped.", file=sys.stderr)
    return 0


def _sops(project, options):
    rows, damaged = read(project, since=options.since, here=options.here)
    report = summarize(rows)
    procedures = list(report["procedures"].items())[: options.limit]
    if options.json:
        print(json.dumps(dict(procedures), indent=2))
        return 0
    if not procedures:
        print("No procedure reads recorded.")
        print(
            "Only `sop show` and `sop explain` are counted; a procedure an "
            "agent reads\nstraight off disk never reaches Quirework."
        )
        return 0
    for name, seen in procedures:
        window = f"{seen['first'][:10]} to {seen['last'][:10]}"
        print(f"  {seen['count']:>6}  {name:<32}  {window}")
        # Where the reads came from, since the store spans every book this
        # user writes and the name alone does not say which one this was.
        for root, count in sorted(seen["projects"].items(), key=lambda kv: -kv[1]):
            label = report["project_names"].get(root, "")
            share = f"{count:>6}  " if len(seen["projects"]) > 1 else " " * 8
            print(f"        {share}{shown(root)}  {label}".rstrip())
    if damaged:
        print(f"{damaged} unreadable record(s) skipped.", file=sys.stderr)
    return 0


def _seats(project, options):
    rows, damaged = read(project, since=options.since, here=options.here)
    report = summarize(rows)
    pairs = list(report["seats"]["pairs"].items())[: options.limit]
    if options.json:
        print(json.dumps(dict(pairs), indent=2))
        return 0
    if not pairs:
        print("No seat launches recorded.")
        print(
            "Rooms, workshops, reviews, runs and experiments record one line per\n"
            "seat they launch, with its harness and model and nothing about its "
            "assignment."
        )
        return 0
    for key, seen in pairs:
        window = f"{seen['first'][:10]} to {seen['last'][:10]}"
        kinds = ", ".join(f"{k} {v}" for k, v in seen["workflows"].items())
        print(f"  {seen['count']:>6}  {key:<40}  {window}  {kinds}")
        for root, count in sorted(seen["projects"].items(), key=lambda kv: -kv[1]):
            label = report["project_names"].get(root, "")
            share = f"{count:>6}  " if len(seen["projects"]) > 1 else " " * 8
            print(f"        {share}{shown(root)}  {label}".rstrip())
    if damaged:
        print(f"{damaged} unreadable record(s) skipped.", file=sys.stderr)
    return 0


def _log(project, options):
    rows, damaged = read(project, since=options.since, here=options.here)
    recent = rows[-options.limit :]
    if options.json:
        print(json.dumps(recent, indent=2))
        return 0
    for entry in recent:
        if _is_seat(entry):
            what = f"seat {entry['workflow']} {entry['harness']} {entry['model']}"
            print(
                f"  {entry['at']}  {what:<34}  {'':>8}  {'':<8}  {shown(entry['project'])}"
            )
            continue
        status = entry.get("status")
        mark = (
            "ok" if status == 0 else ("raised" if status is None else f"exit {status}")
        )
        what = entry["command"] + (f" {entry['target']}" if entry.get("target") else "")
        print(
            f"  {entry['at']}  {what:<34}  {entry.get('ms', 0):>6}ms  "
            f"{mark:<8}  {shown(entry['project'])}"
        )
    if damaged:
        print(f"{damaged} unreadable record(s) skipped.", file=sys.stderr)
    return 0


def _prune(project, options):
    cutoff = when(options.before)
    dropped, files = prune(project, cutoff, dry_run=options.dry_run)
    verb = "would remove" if options.dry_run else "removed"
    print(f"{verb} {dropped} record(s) before {cutoff}, {files} empty file(s).")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="quirework usage",
        description="Which commands and procedures this installation actually "
        "runs. Recorded on this machine, read only by this command, and sent "
        "nowhere.",
        epilog="Disable recording with `enabled = false` under [usage] in "
        "quirework.toml, or QUIREWORK_USAGE=0 for one shell.",
    )
    verbs = parser.add_subparsers(dest="verb")

    def shared(name, help_text, limit):
        p = verbs.add_parser(name, prog=f"quirework usage {name}", help=help_text)
        p.add_argument(
            "--since",
            metavar="WHEN",
            help="only records since a date (2026-01-01) or an age (30d, 12w)",
        )
        p.add_argument(
            "--here",
            action="store_true",
            help="only records made in this project, in any of its checkouts",
        )
        p.add_argument("--json", action="store_true", help="machine-readable output")
        p.add_argument(
            "--limit", type=int, default=limit, metavar="N", help="rows to print"
        )
        return p

    shared("show", "summarize recorded usage", 20)
    shared("sops", "rank procedures by recorded reads", 40)
    shared("seats", "rank harnesses and models by seats launched", 40)
    shared("log", "read the most recent records", 20)
    remove = verbs.add_parser(
        "prune", prog="quirework usage prune", help="remove old records"
    )
    remove.add_argument(
        "--before",
        metavar="WHEN",
        default="365d",
        help="remove records older than this date or age (default: 365d)",
    )
    remove.add_argument(
        "--dry-run", action="store_true", help="report what would go, remove nothing"
    )
    options = parser.parse_args(argv or ["show"])
    project = current_project()
    handlers = {
        "show": _show,
        "sops": _sops,
        "seats": _seats,
        "log": _log,
        "prune": _prune,
    }
    return handlers[options.verb or "show"](project, options)


if __name__ == "__main__":
    raise SystemExit(main())
