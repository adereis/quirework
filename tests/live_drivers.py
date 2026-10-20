"""Which drivers have passed the live driver test at the version installed.

`test_live_drivers.py` runs one short seat per harness on its cheapest model
and keeps each result here: per machine, in the clone's git directory, so
every worktree shares it and no commit carries it. A harness updates itself
several times a week, and a release can change what its records say, so a
result counts for the exact version it ran on and no other.

The pre-commit hook runs this file with `--warn`. It then names each
installed driver whose version has not passed, and never fails the commit:
a harness that updated itself is worth knowing about, but nothing in the
commit depends on it. Run on its own, it prints every driver's standing.
"""

from __future__ import annotations

import datetime
import fcntl
import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

# The cheapest model family each harness offers, run at the lowest effort all
# three accept. A family resolves to its newest member, so the next release
# of a cheap model is tested as itself without an edit here.
DRIVERS = {"agy": "flash", "claude": "haiku", "codex": "luna"}
EFFORT = "low"
COMMAND = "QUIREWORK_LIVE=1 .venv/bin/python -m pytest -m live"
SCHEMA = 1


def _git(*args):
    found = subprocess.run(
        ["git", "-C", str(Path(__file__).parent), *args],
        capture_output=True,
        text=True,
    )
    return found.returncode, found.stdout.strip()


def ledger() -> Path:
    """The results file, in the git directory every worktree of the clone shares."""
    code, common = _git("rev-parse", "--path-format=absolute", "--git-common-dir")
    if code or not common:
        raise RuntimeError("this checkout has no git directory to keep results in")
    return Path(common) / "quirework" / "live-drivers.json"


def results(path=None) -> dict:
    """Each harness's latest result and its last pass, as far as this clone knows."""
    path = path or ledger()
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{path} is schema {data.get('schema')}, not {SCHEMA}")
    return data["drivers"]


@contextmanager
def _locked(path):
    """One writer at a time: the drivers' tests may run in parallel."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def record(harness, version, model, problems, path=None):
    """Keep one live result: the harness's latest, and its last pass if it passed."""
    path = path or ledger()
    code, head = _git("rev-parse", "--short", "HEAD")
    changed, _ = _git("diff", "--quiet", "HEAD", "--")
    entry = dict(
        version=version,
        model=model,
        effort=EFFORT,
        passed=not problems,
        problems=list(problems),
        at=datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        commit=(head + ("+changes" if changed else "")) if not code else None,
    )
    with _locked(path):
        drivers = results(path)
        standing = drivers.setdefault(harness, {})
        standing["last"] = entry
        if entry["passed"]:
            standing["passed"] = entry
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".live-drivers-")
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump({"schema": SCHEMA, "drivers": drivers}, out, indent=2)
            out.write("\n")
        os.replace(temporary, path)
    return entry


def _day(entry):
    return entry["at"][:10]


def untested(installed, drivers):
    """A line for each installed driver whose version has not passed, saying why.

    The latest result at a version is its standing, so a failure after a
    pass at the same version is said: the harness did not change, so
    something here did.
    """
    lines = []
    for harness, version in installed.items():
        if version is None:
            continue
        standing = drivers.get(harness, {})
        passed, last = standing.get("passed"), standing.get("last")
        if last and last["version"] == version and not last["passed"]:
            # One line of the first problem: a problem can carry a log's tail.
            first = (last["problems"] or ["no reason kept"])[0].splitlines()[0]
            first = first if len(first) <= 90 else first[:89] + "…"
            why = f"failed {_day(last)}: {first}"
        elif passed and passed["version"] == version:
            continue
        elif passed:
            why = f"last passed {passed['version']}, {_day(passed)}"
        else:
            why = "never passed on this machine"
        lines.append(f"{harness} {version} ({why})")
    return lines


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv not in ([], ["--warn"]):
        print("usage: live_drivers.py [--warn]", file=sys.stderr)
        return 2
    warn = bool(argv)
    try:
        from quirework.providers.blind import runner_version

        installed = {harness: runner_version(harness) for harness in DRIVERS}
        drivers = results()
    except Exception as exc:  # noqa: BLE001 -- advice never costs the commit its gate
        if not warn:
            raise
        print(f"pre-commit: could not check the live drivers: {exc}", file=sys.stderr)
        return 0
    if warn:
        lines = untested(installed, drivers)
        if lines:
            say = ["live drivers not tested at the installed version:"]
            say += [f"  {line}" for line in lines] + [f"run: {COMMAND}"]
            print("\n".join(f"pre-commit: {line}" for line in say), file=sys.stderr)
        return 0
    for harness, version in installed.items():
        standing = drivers.get(harness, {})
        passed, last = standing.get("passed"), standing.get("last")
        lines = untested({harness: version}, drivers)
        if version is None:
            print(f"{harness}: not installed")
        elif not lines:
            print(f"{harness} {version}: passed {_day(passed)} at {passed['commit']}")
        else:
            print(lines[0])
            if last and last["version"] == version:
                for problem in last["problems"]:
                    print("  " + "\n  ".join(problem.splitlines()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
