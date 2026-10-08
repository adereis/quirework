"""Living with a provider's account limit: what order to spend in, and how
to carry on after the wall.

A quota stop is not a failure of the work. The provider stopped selling
tokens for a while; the session, its reasoning and whatever the agent had
already written are all still there. So the recovery is a **resume of the
same session**, not a fresh run of the same task: the agent is handed one
short notice that it was interrupted and told to carry on.

Two shapes, and they need different recoveries:

``refused``
    The provider declined before the model ran, so nothing was generated.
    The harness has nonetheless already appended the prompt -- and often
    its own refusal notice -- to the session, which is why the shared
    runner rewinds the session file and resubmits the identical turn
    (``runs/engine.rewind_sessions``). Cheap and exact.

``interrupted``
    The model ran, used tools, wrote files, and the wall arrived partway
    through. Nothing can be replayed: the turn's reasoning is banked in
    the session and the only way to keep it is to continue that session.
    Restarting the task throws away the expensive half. One measured
    workshop seat spent 58 percent of its cost on output and reasoning
    and 4.5 percent on cached context reads, so a restart pays the whole
    bill again to re-derive what the session still holds.

This module owns the detection, the session-id recovery and the resume
argv for all three harnesses, so no driver has to recognize a quota stop
on its own. Deliberately stdlib-only and free of project imports: the
run engine's frozen executor, the workshop and the review driver all
reach it, and two of those must not pull the engine in behind them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

HARNESSES = ("codex", "claude", "agy")

# What a seat costs, declared rather than inferred. A table of model ids would
# be stale within a month -- providers rename models constantly -- and a run
# that departs from its roster has seats no table could know about. So a plan
# says which of ITS OWN seats is the cheap one and which is the premium one,
# and everything else is standard.
TIERS = ("cheap", "premium", "standard")


def spend_order(items, tier=lambda item: item.get("tier", "standard")):
    """Seats in the order a run should spend money on them.

    One cheap seat, then the premium seat, then the rest in declared order.
    The two ends of a roster fail differently and both failures are cheaper
    early, which is the whole argument:

    A **broken assignment** -- a missing file, an ambiguous instruction, an
    output path nobody can write -- is found by whichever seat runs first, so
    that seat should be the one it costs least to throw away. Only ONE cheap
    seat leads, because a second adds wall time and learns nothing the first
    did not.

    A **quota wall** arrives at the end of a budget, and the seat standing
    there when it lands is whichever ran last. Putting the premium seat second
    spends it into a nearly empty allowance and leaves the remainder available
    to continue it; the seats that then meet the wall are the cheap and
    replaceable ones, which is the loss a run can afford. Prefer a shakedown
    seat billed against a DIFFERENT account from the premium one, or the cheap
    run eats the budget it is supposed to be protecting.

    Nothing here runs anything, and nothing here is parallel. Seats sharing an
    account exhaust a five-hour window several times faster in parallel, and
    the wall then lands mid-turn on every one of them at once instead of on
    one; three measured workshop runs lost seats exactly that way.
    """
    ranked, led = [], False
    for item in items:
        if tier(item) == "cheap" and not led:
            ranked.append(item)
            led = True
    ranked += [i for i in items if tier(i) == "premium" and i not in ranked]
    ranked += [i for i in items if i not in ranked]
    return ranked


# What a provider says when it is a limit rather than a fault. Claude's
# wording has been seen as both "You've hit your session limit" and
# "Claude AI usage limit reached|<epoch>"; codex reports a code, except
# that 0.161 states a plan's usage limit in prose with none.
# The structured signals below are preferred wherever a harness emits one,
# and this is the fallback for a harness that only returns prose.
LIMIT_TEXT = re.compile(
    r"usage limit|session limit|rate.?limit|quota|too many requests|"
    r"resets? at|429\b",
    re.I,
)

CODEX_LIMIT_CODES = ("rate_limit_exceeded", "insufficient_quota")

# A provider declining because the model itself is busy, which is not a usage
# limit: there is no allowance to wait out and no reset time to wait for. The
# recovery is the same as a refusal's, because nothing ran either way, and the
# scope is reported separately so nobody is told to sit out a window that does
# not exist. A writers room lost a seat for a whole run to one of these, with
# the controller calling it an ordinary failure and refusing to take the turn
# again; the model answered normally two minutes later.
CAPACITY_TEXT = re.compile(
    r"at capacity|overloaded|try a different model|temporarily unavailable",
    re.I,
)


# The whole prompt a resumed agent gets. It says what happened, that the
# assignment is unchanged, and that the work already done is still its own
# -- and it deliberately says nothing about the task itself, because the
# session already holds the task and restating it invites a fresh start.
CONTINUATION = (
    "There was a session quota limit event and your previous turn was cut "
    "off partway through. Nothing about the assignment has changed and "
    "nothing you already did has been lost.\n\n"
    "Continue from where you were. Re-read what you have already written, "
    "do not redo work you have already completed, do not start over, and "
    "finish the task you were given."
)


def continuation(note=None):
    """The resume prompt, plus one caller line naming where its work is."""
    return CONTINUATION if not note else f"{CONTINUATION}\n\n{note.strip()}\n"


def rows(path):
    """Parse the complete JSON lines; an unterminated last line is dropped.

    A seat killed mid-flush leaves its stream ending in half a row. The
    receipt already fails on that, loudly, and the classifier runs right
    after it on the same stream. Raising here a second time was worse than
    useless: it escaped the room's launch before the receipt was written,
    so a killed turn banked no ``receipt.json`` and no round state, and the
    round reported a JSON parse error where the seat's exit code belonged.
    Only a line that lacks its newline is dropped; a malformed complete
    line is still the visible failure it always was.
    """
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with path.open("rb") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                if not line.endswith(b"\n"):
                    break
                raise
    return out


def session_id(harness, stream):
    """The harness session this record belongs to, even when the turn failed.

    The drivers read the session id out of a *successful* receipt, so a
    quota-stopped turn banks ``session_id: null`` and the one thing a
    resume needs survives only inside the event stream. Recovering it here
    is what makes a stopped turn resumable at all.
    """
    if harness == "codex":
        ids = [r["thread_id"] for r in stream if r.get("type") == "thread.started"]
    elif harness == "claude":
        ids = [r["session_id"] for r in stream if r.get("session_id")]
    elif harness == "agy":
        ids = [r["conversation_id"] for r in stream if r.get("conversation_id")]
    else:
        raise ValueError(f"unknown harness: {harness}")
    unique = list(dict.fromkeys(ids))
    # More than one session in one turn's stream means the record is not
    # what it claims to be; resuming "the" session would be a guess.
    return unique[0] if len(unique) == 1 else None


def _claude(stream):
    results = [r for r in stream if r.get("type") == "result"]
    if len(results) != 1 or not results[0].get("is_error"):
        return None
    result = results[0]
    said = str(result.get("result") or "")
    if CAPACITY_TEXT.search(said) and not LIMIT_TEXT.search(said):
        # Busy, not out of allowance. Nothing ran, so the turn is delivered
        # again rather than continued, and no reset time is reported because
        # there is none to report.
        return dict(
            phase="refused",
            scope="capacity",
            resets_at=None,
            detail=said[:200],
            output_tokens=0,
        )
    limit = [
        r
        for r in stream
        if r.get("type") == "rate_limit_event"
        and (r.get("rate_limit_info") or {}).get("status") == "rejected"
    ]
    if not limit and not LIMIT_TEXT.search(said):
        return None
    info = (limit[-1].get("rate_limit_info") or {}) if limit else {}
    # The receipt that says whether the model ran: a provider refusal
    # reports no output tokens anywhere and an empty `modelUsage`, because
    # nothing was generated. A real turn cannot satisfy that.
    produced = sum(
        v.get("outputTokens", 0) or 0 for v in (result.get("modelUsage") or {}).values()
    )
    return dict(
        phase="refused" if not produced else "interrupted",
        scope=info.get("rateLimitType") or "quota",
        resets_at=info.get("resetsAt"),
        detail=said[:200],
        output_tokens=produced,
    )


def _codex_limit(error):
    """Whether a failed Codex turn says it stopped for an account limit.

    The code is the structured signal and wins wherever it is present, so a
    coded failure that is not a limit code stays a fault. Codex 0.161 reports
    a ChatGPT plan's usage limit with no code at all, only the sentence, and a
    seat stopped that way was filed as an ambiguous receipt and could not be
    resumed. A codeless failure therefore falls back to the limit wording the
    other harnesses are already read by.
    """
    code = error.get("code")
    if code:
        return code in CODEX_LIMIT_CODES
    return bool(LIMIT_TEXT.search(str(error.get("message", ""))))


def _codex(stream):
    stopped = [r for r in stream if r.get("type") == "turn.failed"]
    failed = [r for r in stopped if _codex_limit(r.get("error") or {})]
    if not failed:
        busy = [
            r
            for r in stopped
            if CAPACITY_TEXT.search(str((r.get("error") or {}).get("message", "")))
        ]
        if not busy:
            return None
        error = busy[-1].get("error") or {}
        return dict(
            phase="refused",
            scope="capacity",
            resets_at=None,
            detail=str(error.get("message", ""))[:200],
            output_tokens=None,
        )
    error = failed[-1].get("error") or {}
    produced = any(r.get("type") == "item.completed" for r in stream)
    return dict(
        phase="interrupted" if produced else "refused",
        scope=error.get("code") or "quota",
        resets_at=error.get("resets_at"),
        detail=str(error.get("message", ""))[:200],
        output_tokens=None,
    )


def _agy(stream):
    results = [r["result"] for r in stream if r.get("event") == "result"]
    if len(results) != 1 or results[0].get("status") != "ERROR":
        return None
    result = results[0]
    said = str(result.get("error") or result.get("response") or "")
    if not LIMIT_TEXT.search(said):
        return None
    produced = (
        bool(result.get("response"))
        or bool((result.get("usage") or {}).get("output_tokens"))
        or any(r.get("step_update", {}).get("step_type") == "tool" for r in stream)
    )
    return dict(
        phase="interrupted" if produced else "refused",
        scope="quota",
        resets_at=None,
        detail=said[:200],
        output_tokens=(result.get("usage") or {}).get("output_tokens"),
    )


def classify(harness, record):
    """A quota stop in this record's event stream, or None.

    ``record`` is a directory holding ``events.jsonl``, or the file itself.
    Returns ``phase`` (``refused``/``interrupted``), the provider's scope and
    reset time where it gives one, and the session id to resume.

    Returning None is the safe answer and the common one: an ambiguous
    transport fault must never be treated as a quota stop, because the two
    recoveries differ and resubmitting an ambiguous turn can duplicate work
    that actually happened.
    """
    if harness not in HARNESSES:
        raise ValueError(f"unknown harness: {harness}")
    record = Path(record)
    stream = rows(record if record.is_file() else record / "events.jsonl")
    if not stream:
        return None
    detail = {"codex": _codex, "claude": _claude, "agy": _agy}[harness](stream)
    if detail is None:
        return None
    detail["harness"] = harness
    detail["session_id"] = session_id(harness, stream)
    return detail


def resumable(harness, record):
    """The session id to resume after an interrupted turn, or None.

    A ``refused`` stop is not resumable through this path on purpose: nothing
    ran, so there is no reasoning to continue from and the turn has to be
    delivered again rather than continued.

    *How* it is delivered again is the caller's, not this module's. The shared
    runner rewinds to the pre-submission mark and resubmits the identical turn,
    which it can do only because it marked the session files before sending
    (``runs/engine.session_marks``). A driver that takes no such mark -- the
    prose workshop, the review driver -- relaunches the turn in a fresh session
    instead, and loses nothing by it: its unit is one assignment in its own
    session, so there is no earlier turn a rewind would have preserved.
    """
    return resumable_session(classify(harness, record))


def resumable_session(detail):
    """The same rule as ``resumable``, over an already-classified stop.

    Split out so a caller that has paid for ``classify`` already -- the
    workshop's ``inspect`` walks every failed record -- applies the predicate
    without reading the event stream a second time. One owner for the rule,
    two ways in: deriving it from ``session_id`` alone is what made ``inspect``
    report a refused writer as resumable.
    """
    if not detail or detail["phase"] != "interrupted":
        return None
    return detail["session_id"]


def resume_argv(harness, session):
    """The tokens that turn a launch command into a resume of ``session``.

    Codex takes a subcommand, so its tokens follow ``exec``; Claude and agy
    take a flag anywhere in their argv. Every caller must then check the
    returned session id against ``session``: a harness that silently starts a
    new conversation instead of resuming looks identical until that check,
    and the evidence would be a second session claiming to be the first.
    """
    if not session:
        raise ValueError("a resume needs a session id")
    return {
        "codex": ["resume", session],
        "claude": ["--resume", session],
        "agy": ["--conversation", session],
    }[harness]
