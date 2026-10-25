"""A seat's harness that answers and then never exits is stopped, and said so.

The harness here is a fake: a short script that reads its prompt, writes a
stream in one harness's shape, and then exits, stays, or stays deaf to
SIGTERM, as told. Only the waiting is under test; what a receipt makes of
the stream is tested with each procedure.
"""

import json
import signal
import subprocess
import sys
import time

import pytest

from quirework.providers import exits

FAKE = r"""
import json, signal, sys, time
mode, harness = sys.argv[1], sys.argv[2]
prompt = sys.stdin.buffer.read()
closing = {
    "agy": {"event": "result", "result": {"status": "SUCCESS",
            "response": "The kettle whistles."}},
    "claude": {"type": "result", "subtype": "success",
               "result": "The kettle whistles."},
    "codex": {"type": "turn.completed"},
}[harness]
out = sys.stdout
out.write(json.dumps({"event": "init", "read": len(prompt)}) + "\n")
out.flush()
if mode == "work":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(3600)
if mode == "half":
    out.write(json.dumps(closing)[:-3])
    out.flush()
    time.sleep(3600)
out.write(json.dumps(closing) + "\n")
out.flush()
if mode == "exit":
    sys.exit(0)
if mode == "deaf":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(3600)
"""


@pytest.fixture
def launch(tmp_path):
    script = tmp_path / "fake_harness.py"
    script.write_text(FAKE)
    started = []

    def run(mode, harness="agy"):
        events = tmp_path / f"{mode}-{harness}.jsonl"
        with events.open("wb") as out:
            proc = subprocess.Popen(
                [sys.executable, str(script), mode, harness],
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        started.append(proc)
        return proc, events

    yield run
    for proc in started:
        if proc.poll() is None:
            exits.stop(proc, patience=0.2)


def test_a_harness_that_answers_and_stays_is_reported_after_its_grace(launch):
    proc, events = launch("stay")
    began = time.monotonic()
    why = exits.wait(
        proc, b"Boil it.", harness="agy", events=events, grace=0.5, poll=0.05
    )
    assert why == exits.ANSWERED
    assert 0.5 <= time.monotonic() - began < 10
    assert proc.poll() is None, "the caller stops it, not the wait"


def test_one_that_ignores_sigterm_is_killed(launch):
    proc, events = launch("deaf")
    assert (
        exits.wait(proc, b"", harness="agy", events=events, grace=0.2, poll=0.05)
        == exits.ANSWERED
    )
    assert exits.stop(proc, patience=0.3) == -signal.SIGKILL


@pytest.mark.parametrize("harness", ["agy", "claude", "codex"])
def test_each_harness_is_known_by_its_own_closing_row(launch, harness):
    proc, events = launch("stay", harness)
    assert (
        exits.wait(proc, b"", harness=harness, events=events, grace=0.1, poll=0.05)
        == exits.ANSWERED
    )


def test_another_harnesss_closing_row_closes_nothing(launch):
    proc, events = launch("stay", "claude")
    why = exits.wait(proc, b"", harness="agy", events=events, timeout=1, poll=0.05)
    assert why == exits.TIMEOUT


def test_a_harness_that_exits_is_left_alone(launch):
    proc, events = launch("exit")
    assert exits.wait(proc, b"Boil it.", harness="agy", events=events) is None
    assert proc.returncode == 0


def test_a_stream_still_working_waits_for_the_timeout(launch):
    proc, events = launch("work")
    began = time.monotonic()
    why = exits.wait(
        proc, b"", harness="agy", events=events, timeout=1, grace=0, poll=0.05
    )
    assert why == exits.TIMEOUT and time.monotonic() - began >= 1


def test_half_a_closing_row_is_not_a_closing_row(launch):
    proc, events = launch("half")
    why = exits.wait(
        proc, b"", harness="agy", events=events, timeout=1, grace=0, poll=0.05
    )
    assert why == exits.TIMEOUT, "a row still being written closes nothing"


def test_the_whole_prompt_reaches_the_harness_while_it_is_watched(launch):
    proc, events = launch("exit")
    prompt = b"A kettle boils. " * 200_000  # far past a pipe's buffer
    assert exits.wait(proc, prompt, harness="agy", events=events, poll=0.05) is None
    first = json.loads(events.read_text().splitlines()[0])
    assert first["read"] == len(prompt)


def test_a_stop_is_written_down_and_read_back(tmp_path):
    assert exits.recorded(tmp_path) is None
    written = exits.record(tmp_path, harness="agy", grace=120, exit_code=-9)
    assert exits.recorded(tmp_path) == written
    sentence = exits.notice(written)
    assert "did not exit within 120 seconds" in sentence
    assert "exit status -9" in sentence
