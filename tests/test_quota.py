"""A quota stop must cost the wall-clock wait, never the work already done.

Two shapes travel under one name and take opposite recoveries, so the tests
that matter are the ones pinning the boundary between them. A provider that
declined BEFORE running the model left nothing to keep, so the turn is
delivered again rather than continued: the shared runner rewinds to its
pre-submission mark and resubmits, and a driver that records no such mark
relaunches. A provider that stopped the model PARTWAY left reading, reasoning
and half-written files inside the session, and the only recovery that keeps
them resumes it.

Mistaking the second for the first is the expensive error: it restarts a turn
that was most of the way done and pays the whole bill again.
"""

import json

import pytest

from quirework.providers import quota


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path.parent


def claude_rows(*, produced, notice="You've hit your session limit"):
    rows = [dict(type="system", subtype="init", session_id="sid-1")]
    if produced:
        rows.append(
            dict(
                type="assistant",
                session_id="sid-1",
                message=dict(model="m", content=[dict(type="text", text="…")]),
            )
        )
    rows.append(
        dict(
            type="rate_limit_event",
            session_id="sid-1",
            rate_limit_info=dict(
                status="rejected", rateLimitType="five_hour", resetsAt=4_000_000_000
            ),
        )
    )
    rows.append(
        dict(
            type="result",
            subtype="success",
            is_error=True,
            session_id="sid-1",
            result=notice,
            modelUsage={"m": dict(outputTokens=9100)} if produced else {},
        )
    )
    return rows


# --------------------------------------------------------------------------- #
# which shape
# --------------------------------------------------------------------------- #
def test_a_refusal_before_the_model_ran_is_not_resumable(tmp_path):
    """Nothing was generated, so there is nothing to continue FROM.

    The turn is delivered again instead, which is exact either way: the
    shared runner rewinds and resubmits it, and a driver without that mark
    relaunches it. Asking a model to "continue" a turn it never began would
    invite it to invent a starting point.
    """
    record = write(tmp_path / "refused" / "events.jsonl", claude_rows(produced=False))
    assert quota.classify("claude", record)["phase"] == "refused"
    assert quota.resumable("claude", record) is None


def test_a_limit_that_lands_mid_turn_is_resumable(tmp_path):
    record = write(tmp_path / "cut" / "events.jsonl", claude_rows(produced=True))
    detail = quota.classify("claude", record)
    assert detail["phase"] == "interrupted"
    assert detail["scope"] == "five_hour"
    assert detail["resets_at"] == 4_000_000_000
    assert quota.resumable("claude", record) == "sid-1"


def test_an_ordinary_error_is_never_read_as_a_quota_stop(tmp_path):
    """A transport fault is ambiguous about what ran and must stay ambiguous."""
    record = write(
        tmp_path / "broken" / "events.jsonl",
        [
            dict(type="system", subtype="init", session_id="sid-1"),
            dict(
                type="result",
                subtype="error_during_execution",
                is_error=True,
                session_id="sid-1",
                result="fetch failed: connection reset",
                modelUsage={},
            ),
        ],
    )
    assert quota.classify("claude", record) is None


def test_a_completed_turn_is_not_a_quota_stop(tmp_path):
    record = write(
        tmp_path / "fine" / "events.jsonl",
        [
            dict(type="system", subtype="init", session_id="sid-1"),
            dict(
                type="result",
                subtype="success",
                is_error=False,
                session_id="sid-1",
                result="Done.",
                modelUsage={"m": dict(outputTokens=12)},
            ),
        ],
    )
    assert quota.classify("claude", record) is None


def test_codex_and_agy_report_the_same_two_shapes(tmp_path):
    codex_cut = write(
        tmp_path / "cx" / "events.jsonl",
        [
            dict(type="thread.started", thread_id="th-9"),
            dict(
                type="item.completed", item=dict(type="agent_message", text="partial")
            ),
            dict(
                type="turn.failed",
                error=dict(code="rate_limit_exceeded", message="limit", resets_at=123),
            ),
        ],
    )
    assert quota.classify("codex", codex_cut)["phase"] == "interrupted"
    assert quota.resumable("codex", codex_cut) == "th-9"

    codex_refused = write(
        tmp_path / "cx2" / "events.jsonl",
        [
            dict(type="thread.started", thread_id="th-9"),
            dict(
                type="turn.failed", error=dict(code="insufficient_quota", message="no")
            ),
        ],
    )
    assert quota.classify("codex", codex_refused)["phase"] == "refused"

    agy_cut = write(
        tmp_path / "agy" / "events.jsonl",
        [
            dict(event="init", conversation_id="cv-2"),
            dict(
                event="step_update",
                conversation_id="cv-2",
                step_update=dict(step_type="tool"),
            ),
            dict(
                event="result",
                conversation_id="cv-2",
                result=dict(
                    status="ERROR",
                    error="429 rate limit reached",
                    usage=dict(output_tokens=40),
                ),
            ),
        ],
    )
    assert quota.classify("agy", agy_cut)["phase"] == "interrupted"
    assert quota.resumable("agy", agy_cut) == "cv-2"


def test_an_ambiguous_session_is_not_resumed(tmp_path):
    """Two sessions in one turn's stream means the record is not what it claims.

    Resuming "the" session would be a guess, and the guess would look like
    evidence afterwards.
    """
    rows = claude_rows(produced=True)
    rows[1]["session_id"] = "sid-2"
    record = write(tmp_path / "two" / "events.jsonl", rows)
    assert quota.classify("claude", record)["session_id"] is None
    assert quota.resumable("claude", record) is None


# --------------------------------------------------------------------------- #
# resuming
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "harness,expected",
    [
        ("codex", ["resume", "s"]),
        ("claude", ["--resume", "s"]),
        ("agy", ["--conversation", "s"]),
    ],
)
def test_each_harness_names_its_session_differently(harness, expected):
    assert quota.resume_argv(harness, "s") == expected


def test_the_continuation_prompt_says_only_what_happened():
    """It must not restate the task: the session already holds it, and a
    restated assignment is an invitation to start over."""
    text = quota.continuation()
    assert "quota limit" in text and "Continue from where you were" in text
    assert "do not start over" in text
    assert quota.continuation("Your report is at /x.").endswith(
        "Your report is at /x.\n"
    )


# --------------------------------------------------------------------------- #
# spend order
# --------------------------------------------------------------------------- #
def seats(*pairs):
    return [dict(id=name, tier=tier) for name, tier in pairs]


def test_one_cheap_seat_leads_then_the_premium_one():
    order = quota.spend_order(
        seats(
            ("sol", "standard"),
            ("opus", "premium"),
            ("mini", "cheap"),
            ("flash", "cheap"),
            ("terra", "standard"),
        )
    )
    assert [s["id"] for s in order] == ["mini", "opus", "sol", "flash", "terra"]


def test_a_second_cheap_seat_does_not_lead_as_well():
    """One shakedown finds the broken assignment; a second only costs time."""
    order = quota.spend_order(seats(("a", "cheap"), ("b", "cheap")))
    assert [s["id"] for s in order] == ["a", "b"]


def test_a_roster_without_tiers_keeps_its_declared_order():
    order = quota.spend_order([dict(id=x) for x in ("one", "two", "three")])
    assert [s["id"] for s in order] == ["one", "two", "three"]


def test_every_seat_appears_exactly_once():
    declared = seats(
        ("a", "premium"),
        ("b", "cheap"),
        ("c", "standard"),
        ("d", "premium"),
        ("e", "cheap"),
    )
    order = quota.spend_order(declared)
    assert sorted(s["id"] for s in order) == ["a", "b", "c", "d", "e"]
    assert len(order) == len(declared)
