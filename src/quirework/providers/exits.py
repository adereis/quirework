"""Waiting for a seat's harness to exit, and stopping one that answered and stayed.

Every procedure that seats a model starts the harness with its stream sent
to the turn's ``events.jsonl``, hands it the prompt on stdin, and waits for
it to exit. A harness ends its turn by writing one closing row (Antigravity
and Claude Code a ``result``, Codex ``turn.completed`` or ``turn.failed``)
and is expected to exit right after. Antigravity sometimes does not: its
stream holds the whole answer and the closing row, and the process sits
idle, past its own ``--print-timeout`` and deaf to SIGTERM. A wait on the
process alone then lasts until someone kills it by hand, and the seats
behind it wait too.

``wait`` watches the stream while it waits for the process. Once the
closing row is in, the harness has ``EXIT_GRACE`` seconds to exit on its
own, which leaves it time to flush its native transcript. After that the
caller stops it (``stop``) and writes the stop down in the turn's record
(``record``), so the receipt says the harness was stopped and why, and a
person reading it never mistakes the forced exit for a crash.

Nothing here judges the answer. A stream with no closing row is never
taken for a finished one, so an answer cut off partway still waits for the
process or for the caller's timeout, and the receipt reads whatever the
stream holds exactly as it would have after a clean exit.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

# Seconds a harness has to exit on its own after writing its closing row. A
# clean exit takes a second or two; the rest is room for a slow flush of the
# native transcript a receipt reads.
EXIT_GRACE = 120
# How often the stream is read for the closing row while the process runs.
POLL_SECONDS = 2.0
# Seconds between SIGTERM and SIGKILL when a harness is stopped.
STOP_PATIENCE = 5

# Why ``wait`` returned without the harness exiting on its own.
ANSWERED = "answered"
TIMEOUT = "timeout"

# The file in a turn's record that says the harness was stopped after it
# answered.
STOP_FILE = "stopped.json"


def _closes(harness, row):
    """Whether ``row`` is ``harness``'s closing row for a turn."""
    if harness == "agy":
        return row.get("event") == "result"
    if harness == "claude":
        return row.get("type") == "result"
    if harness == "codex":
        return row.get("type") in ("turn.completed", "turn.failed")
    return False


class _Stream:
    """The complete rows a harness has appended to its stream so far.

    Read by offset, so each row is parsed once, and only up to the last
    newline, so a row the harness is halfway through writing waits for the
    next read.
    """

    def __init__(self, path):
        self.path, self.offset, self.partial = Path(path), 0, b""

    def closed(self, harness):
        try:
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read()
        except FileNotFoundError:
            return False
        self.offset += len(chunk)
        lines = (self.partial + chunk).split(b"\n")
        self.partial = lines.pop()
        for line in lines:
            # Cheap before parsing: a closing row names one of these.
            if b"result" not in line and b"turn." not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and _closes(harness, row):
                return True
        return False


def wait(
    proc,
    payload,
    *,
    harness,
    events,
    timeout=None,
    grace=None,
    poll=None,
):
    """Send ``payload`` and wait for ``proc`` to exit; return why it did not.

    ``None`` when the harness exited on its own. ``TIMEOUT`` when ``timeout``
    seconds passed first, and ``ANSWERED`` when ``harness`` wrote its closing
    row to ``events`` and was still running ``grace`` seconds later. Either
    way the process is left running for the caller to stop, as
    ``Popen.communicate`` leaves it after its own timeout. ``grace`` and
    ``poll`` default to ``EXIT_GRACE`` and ``POLL_SECONDS``.
    """
    grace = EXIT_GRACE if grace is None else grace
    poll = POLL_SECONDS if poll is None else poll
    stream = _Stream(events)
    started = time.monotonic()
    closed_at = None
    data = payload
    while True:
        step = poll
        now = time.monotonic()
        if timeout is not None:
            left = timeout - (now - started)
            if left <= 0:
                return TIMEOUT
            step = min(step, left)
        if closed_at is not None:
            step = min(step, max(grace - (now - closed_at), 0))
        try:
            # Input is given once: a later call carries on writing it.
            proc.communicate(data, timeout=step)
            return None
        except subprocess.TimeoutExpired:
            data = None
        now = time.monotonic()
        if closed_at is None:
            if stream.closed(harness):
                closed_at = now
        elif now - closed_at >= grace:
            return ANSWERED


def stop(proc, patience=None):
    """Stop ``proc`` and everything it started; return its exit status.

    The harness leads its own session (``start_new_session``), so its whole
    group is signalled, SIGTERM first and SIGKILL once ``patience`` seconds
    pass (``STOP_PATIENCE`` by default), since a harness that stayed after
    answering may ignore SIGTERM.
    """
    patience = STOP_PATIENCE if patience is None else patience
    for number in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, number)
        except (ProcessLookupError, PermissionError):
            break
        try:
            proc.wait(timeout=patience)
            break
        except subprocess.TimeoutExpired:
            continue
    return proc.wait()


def record(directory, *, harness, grace, exit_code):
    """Write down in a turn's record that its harness was stopped after it
    answered; return what was written."""
    stopped = dict(
        reason=f"{harness} wrote its closing row and did not exit within "
        f"{grace} seconds",
        harness=harness,
        grace_seconds=grace,
        exit_code=exit_code,
        stopped_at=time.time(),
    )
    (Path(directory) / STOP_FILE).write_text(json.dumps(stopped, indent=2) + "\n")
    return stopped


def recorded(directory):
    """What ``record`` wrote in a turn's record, or ``None``."""
    path = Path(directory) / STOP_FILE
    return json.loads(path.read_text()) if path.is_file() else None


def notice(stopped):
    """One sentence for a receipt about a harness stopped after it answered."""
    return (
        f"{stopped['reason']}, so it was stopped (exit status "
        f"{stopped['exit_code']}). Its stream was complete when it was "
        "stopped; the answer is judged from it as from any other."
    )
