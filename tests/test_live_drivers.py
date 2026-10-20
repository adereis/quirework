"""Each driver live, on its cheapest model: does its record still prove it?

A harness updates itself several times a week, and a release can change what
its records say while every flag Quirework passes still works. agy 1.4.3
renamed its file tools and stopped naming the file in a read's result, and
every reading check on its seats then reported the book unread. The unit
tests went on passing, because their fixtures were written from the release
before. Only a live seat shows what the installed release records.

The live test runs one seat per harness through the run engine, on a book of
two short chapters and a figure, and checks what Quirework makes of the
record. The receipt must prove each chapter read in full and in order, each
reaction written before the next chapter opened and left alone after it, and
the figure opened. The run's feed must name every file the seat touched. A
cheap model can also fail a check by not doing as it was told, and the
message names the check either way.

It reaches a real provider, so it runs only when asked, as
`QUIREWORK_LIVE=1 .venv/bin/python -m pytest -m live`. Each result is kept
per machine by `live_drivers`, whose ledger the pre-commit hook reads to warn
of a driver whose installed version has not passed. The tests of the ledger
itself run with the rest of the suite.
"""

import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import zlib

import pytest

import live_drivers
from quirework.providers import blind, harness as drivers
from quirework.runs import feed
from quirework.runs.storage import tree_digest
from quirework.testing import real_environment

LIVE = os.environ.get("QUIREWORK_LIVE") == "1"
# A turn's limit, in seconds, and the command's, past which it is stopped.
# A quota refusal is never waited out: the run stops, and so does the test.
TIMEOUT, BACKSTOP = 600, 900

CHAPTERS = {
    "01-the-lamp.md": (
        "# The lamp\n\n"
        "Oren climbed the ninety steps before dusk.\n"
        "The lamp had gone out twice that week.\n"
        "Figure: figures/the-lamp.png shows the lamp room.\n"
    ),
    "02-the-ledger.md": (
        "# The ledger\n\n"
        "He wrote the hour in the ledger.\n"
        "The ink froze at the tip of the pen.\n"
        "By morning the ships had passed.\n"
        "Nobody came to read what he wrote.\n"
    ),
}
FIGURE = "the-lamp.png"
PROMPT = """\
Read the short book in {book}/chapters with your own tools, one chapter at a \
time, in order.

For each chapter:
1. Read the whole chapter. Read 02-the-ledger.md in two parts: lines 1 to 3 \
first, then the rest.
2. Open every figure the chapter names, from {book}/figures.
3. Write your reaction to drafts/<the chapter's file name> in your working \
directory: two sentences on what happens in the chapter.

Write each reaction before you open the next chapter, and leave it unchanged \
once you have opened the next chapter.

When both reactions are written, edit drafts/02-the-ledger.md to add one more \
sentence at its end. Then try to read {book}/chapters/03-the-end.md, which does \
not exist; that is expected. Finally, reply with one short sentence saying you \
are done.
"""
UNBANKED = "the turn banked no answer"
# What the receipt must say of a seat that did as it was told.
PROVEN = {
    "referenced_files_read": "2/2",
    "referenced_files_in_order": True,
    "reactions_before_next": True,
    "reactions_unrevised": True,
    "referenced_images_opened": "1/1",
    "read_root_digests_held": True,
}


def png(size=64, colour=(200, 160, 40)):
    """A plain square image, made here so the book holds nothing borrowed."""

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + bytes(colour) * size for _ in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", header),
            chunk(b"IDAT", zlib.compress(rows)),
            chunk(b"IEND", b""),
        ]
    )


def write_book(book):
    (book / "chapters").mkdir(parents=True)
    (book / "figures").mkdir()
    for name, text in CHAPTERS.items():
        (book / "chapters" / name).write_text(text)
    (book / "figures" / FIGURE).write_bytes(png())
    return book


def write_plan(path, book, harness):
    policy = dict(
        tools="write",
        filesystem=drivers.CAPABILITIES[harness]["filesystem"],
        read_roots=[str(book)],
        read_root_digests={str(book): tree_digest(book)},
    )
    turn = dict(
        id="wholebook",
        prompt=PROMPT.format(book=book),
        outputs=[f"drafts/{name}" for name in CHAPTERS],
        referenced_files=[str(book / "chapters" / name) for name in CHAPTERS],
        referenced_images=[str(book / "figures" / FIGURE)],
    )
    seat = dict(
        id="reader",
        harness=harness,
        model=live_drivers.DRIVERS[harness],
        effort=live_drivers.EFFORT,
        policy=policy,
        turns=[turn],
    )
    path.write_text(json.dumps(dict(title="Live driver test", seats=[seat])))
    return path


def live_project():
    """The project the live runs belong to, one per clone.

    At one place, so the canary's records for it are kept from one live run
    to the next. It names the same cheap models for the canary as the seats
    run on, which a seat on Claude or Codex needs before it can be prepared.
    """
    folder = live_drivers.ledger().parent / "live-project"
    folder.mkdir(parents=True, exist_ok=True)
    models = ", ".join(f'{h} = "{m}"' for h, m in sorted(live_drivers.DRIVERS.items()))
    (folder / "quirework.toml").write_text(
        "# Written by tests/test_live_drivers.py, for its live runs.\n"
        f"schema = 2\n\n[providers]\ncanary_models = {{{models}}}\n"
    )
    return folder


def quirework(*args, project, environment, log):
    """Run the command as the person would, with their own logins, and
    return its exit status.

    The run's own `--timeout` stops the seat; this stops the command, and
    whatever it started, should the command outlive that.
    """
    with log.open("a") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "quirework", *map(str, args)],
            cwd=project,
            env=environment,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return proc.wait(timeout=BACKSTOP)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            command = " ".join(map(str, args[:3]))
            raise RuntimeError(f"`quirework {command}` ran past {BACKSTOP}s") from None


def failed(command, code, log):
    tail = log.read_text().splitlines()[-15:]
    return f"`quirework {command}` exited {code}:\n" + "\n".join(tail)


def attempt(record):
    found = sorted((record / "records" / "reader" / "wholebook").glob("attempt-*"))
    return found[-1] if found else None


def launched_version(record):
    """The version the turn ran on, as its own launch recorded it."""
    folder = attempt(record)
    launch = folder / "launch.json" if folder else None
    if launch is None or not launch.is_file():
        return None
    return (json.loads(launch.read_text()).get("canary") or {}).get("version")


def judged(record, book):
    """Every way the record fails to prove what the seat was told to do."""
    path = record / "state" / "reader.json"
    done = json.loads(path.read_text()).get("done", {}) if path.is_file() else {}
    if "wholebook" not in done:
        return [UNBANKED]
    done = done["wholebook"]
    receipt = done["receipt"]
    problems = [
        f"receipt {key} is {receipt.get(key)!r}, not {wanted!r}"
        for key, wanted in PROVEN.items()
        if receipt.get(key) != wanted
    ]
    # A proxied session bus is how agy reaches its login keyring; the flag
    # describes the jail by design, not the record.
    expected = set(drivers.bus_flags(attempt(record)))
    problems += [
        f"red flag: {flag}"
        for flag in receipt.get("red_flags", [])
        if flag not in expected
    ]
    # The seat's own lines, under its name and a verb. The run's lines list
    # the declared outputs whatever the seat's lines said, so they prove
    # nothing of what the feed can follow.
    acts = [
        line
        for line in feed.RunFeed(record, width=240).poll()
        if re.match(r".{8}  reader  \S", line)
    ]
    for kind, name in [("chapters", n) for n in CHAPTERS] + [
        ("drafts", n) for n in CHAPTERS
    ]:
        if not any(f"{kind}/{name}" in line for line in acts):
            problems.append(f"no line of the seat's in the feed names {kind}/{name}")
    problems += [
        f"the feed notes: {line.split('note', 1)[1].strip()}"
        for line in acts
        if re.match(r".{8}  reader  note ", line)
    ]
    return problems


@pytest.mark.live
@pytest.mark.skipif(not LIVE, reason="reaches a real provider: set QUIREWORK_LIVE=1")
@pytest.mark.parametrize("harness", sorted(live_drivers.DRIVERS))
def test_a_driver_s_record_still_proves_what_its_seat_did(harness, tmp_path):
    if shutil.which(harness) is None:
        pytest.skip(f"{harness} is not installed")
    book = write_book(tmp_path / "book")
    record = tmp_path / "record"
    plan = write_plan(tmp_path / "plan.json", book, harness)
    log = tmp_path / "quirework.log"
    run = dict(project=live_project(), environment=real_environment(), log=log)
    model, problems = live_drivers.DRIVERS[harness], []
    try:
        code = quirework("sop", "runs", "prepare", plan, record, **run)
        if code:
            problems.append(failed("sop runs prepare", code, log))
        else:
            seat = json.loads((record / "plan.json").read_text())["seats"][0]
            model = seat["config"]["model"]
            # A seat that banked its answer with any red flag exits 1, so
            # the record is judged rather than the status, which is told
            # only when no answer was banked to judge.
            start = ("sop", "runs", "start", record, "--timeout", TIMEOUT)
            code = quirework(*start, "--wait-quota", 0, **run)
            found = judged(record, book)
            problems += found
            if code and found == [UNBANKED]:
                problems.append(failed("sop runs start", code, log))
    except Exception as exc:  # noqa: BLE001 -- recorded as a failure, then reported
        problems.append(f"{type(exc).__name__}: {exc}")
    version = launched_version(record) or blind.runner_version(harness)
    live_drivers.record(harness, version, model, problems)
    assert not problems, f"{harness} {version} on {model}:\n" + "\n".join(problems)


# -- The ledger the pre-commit hook reads ----------------------------------------


def test_a_result_counts_for_its_exact_version(tmp_path):
    ledger = tmp_path / "live-drivers.json"
    live_drivers.record("agy", "1.4.0", "flash", [], path=ledger)
    problem = "receipt is wrong\n  and the log says more"
    live_drivers.record("codex", "0.163.0", "luna", [problem], path=ledger)
    results = live_drivers.results(ledger)
    installed = {"agy": "1.4.3", "claude": "2.1.305", "codex": "0.163.0"}
    assert live_drivers.untested(installed, results) == [
        "agy 1.4.3 (last passed 1.4.0, " + results["agy"]["passed"]["at"][:10] + ")",
        "claude 2.1.305 (never passed on this machine)",
        "codex 0.163.0 (failed "
        + results["codex"]["last"]["at"][:10]
        + ": receipt is wrong)",
    ]
    live_drivers.record("agy", "1.4.3", "flash", [], path=ledger)
    assert live_drivers.untested({"agy": "1.4.3"}, live_drivers.results(ledger)) == []


def test_a_failure_keeps_the_last_pass(tmp_path):
    ledger = tmp_path / "live-drivers.json"
    live_drivers.record("claude", "2.1.304", "haiku", [], path=ledger)
    live_drivers.record("claude", "2.1.305", "haiku", ["no answer"], path=ledger)
    standing = live_drivers.results(ledger)["claude"]
    assert standing["passed"]["version"] == "2.1.304"
    assert standing["last"]["version"] == "2.1.305"
    assert not standing["last"]["passed"]


def test_a_failure_after_a_pass_at_the_same_version_is_said(tmp_path):
    """The harness did not change, so something here did."""
    ledger = tmp_path / "live-drivers.json"
    live_drivers.record("claude", "2.1.305", "haiku", [], path=ledger)
    live_drivers.record("claude", "2.1.305", "haiku", ["no answer"], path=ledger)
    results = live_drivers.results(ledger)
    assert live_drivers.untested({"claude": "2.1.305"}, results) == [
        f"claude 2.1.305 (failed {results['claude']['last']['at'][:10]}: no answer)"
    ]


def test_a_driver_that_is_not_installed_is_not_warned_about(tmp_path):
    assert live_drivers.untested({"codex": None}, {}) == []


def test_the_warning_never_fails_the_commit(tmp_path, monkeypatch, capsys):
    """Advice: an unreadable ledger is said, and the commit goes on."""
    ledger = tmp_path / "live-drivers.json"
    ledger.write_text("{not json")
    monkeypatch.setattr(live_drivers, "ledger", lambda: ledger)
    assert live_drivers.main(["--warn"]) == 0
    assert "could not check the live drivers" in capsys.readouterr().err
