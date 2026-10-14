import json
import os
import time

import pytest

from quirework.runs import observe
from quirework.runs.storage import digest


def native(tmp_path):
    path = tmp_path / "session.jsonl"
    rows = [
        {"type": "turn_context", "payload": {"model": "fixture", "effort": "xhigh"}},
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "model_context_window": 500,
                    "last_token_usage": {"input_tokens": 400},
                    "total_token_usage": {
                        "input_tokens": 900,
                        "cached_input_tokens": 700,
                    },
                },
            },
        },
        {"type": "compacted", "timestamp": "fictitious-time", "payload": {}},
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "model_context_window": 500,
                    "last_token_usage": {"input_tokens": 100},
                    "total_token_usage": {
                        "input_tokens": 1000,
                        "cached_input_tokens": 750,
                    },
                },
            },
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def test_observation_preserves_peak_across_compaction_and_latest_total(tmp_path):
    path = native(tmp_path)
    original = path.read_bytes()
    result = observe.codex(path)
    assert result["models"] == ["fixture"]
    assert result["efforts"] == ["xhigh"]
    assert result["compaction_markers"] == [
        {"timestamp": "fictitious-time", "tags": ["compacted"]}
    ]
    assert result["peak_input_tokens"] == 400
    assert result["last_input_tokens"] == 100
    assert result["usage"]["input_tokens"] == 1000
    assert path.read_bytes() == original


def test_incomplete_tail_is_pending_but_malformed_complete_row_fails(tmp_path):
    path = native(tmp_path)
    with path.open("a") as stream:
        stream.write('{"type":')
    assert observe.codex(path)["native_rows"] == 4
    with path.open("a") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="malformed native row"):
        observe.codex(path)


def test_record_observation_detects_policy_breach_without_mutation(tmp_path):
    root = tmp_path / "record"
    sessions = root / "work/reader/seat.blind-state/codex/sessions"
    sessions.mkdir(parents=True)
    native(sessions)
    plan = {
        "schema": 1,
        "seats": [
            {
                "id": "reader",
                "config": {"harness": "codex", "model": "fixture", "effort": "xhigh"},
                "policy": {"allow_compaction": False},
            }
        ],
    }
    (root / "plan.json").write_text(json.dumps(plan))
    (root / "plan-digest.json").write_text(
        json.dumps({"sha256": digest(root / "plan.json")})
    )
    result = observe.observe(root)
    assert result["diagnostic_only"] is True
    assert result["seats"]["reader"]["policy_violations"] == [
        "compaction despite allow_compaction=false"
    ]


@pytest.mark.parametrize(
    "harness,relative_path,marker",
    [
        (
            "claude",
            "projects/example/session.jsonl",
            {"type": "system", "subtype": "compact_boundary"},
        ),
        (
            "agy",
            "antigravity-cli/brain/example/.system_generated/logs/transcript_full.jsonl",
            {"event": "context_compacted"},
        ),
    ],
)
def test_other_harness_compaction_is_red_flag_even_when_allowed(
    tmp_path, harness, relative_path, marker
):
    root = tmp_path / "record"
    transcript = root / "work/reader/seat.blind-state" / harness / relative_path
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps(marker) + "\n")
    plan = {
        "schema": 1,
        "seats": [
            {
                "id": "reader",
                "config": {"harness": harness, "model": "fixture", "effort": "high"},
                "policy": {"allow_compaction": True},
            }
        ],
    }
    (root / "plan.json").write_text(json.dumps(plan))
    (root / "plan-digest.json").write_text(
        json.dumps({"sha256": digest(root / "plan.json")})
    )
    seat = observe.observe(root)["seats"]["reader"]
    assert seat["available"] and seat["red_flags"]
    assert not seat["policy_violations"]


def test_partial_utf8_tail_is_pending(tmp_path):
    path = native(tmp_path)
    with path.open("ab") as stream:
        stream.write(b'{"type":"\xe2')
    assert observe.codex(path)["native_rows"] == 4
    with path.open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ValueError, match="malformed native row"):
        observe.codex(path)


def test_compaction_words_in_content_are_not_events():
    values = [
        {"type": "user", "payload": "Please discuss compaction."},
        {
            "type": "assistant",
            "payload": {"item": ["compacted"]},
            "event": {"content": "context_compacted"},
        },
    ]
    assert observe.compaction_markers(values) == []


def claude_record(tmp_path, *, model="claude-fixture-1-0", turns=1):
    """A one-seat Claude record with a turn in flight in this process."""
    root = tmp_path / "record"
    plan = {
        "schema": 1,
        "seats": [
            {
                "id": "reader",
                "config": {"harness": "claude", "model": model, "effort": "high"},
                "policy": {"allow_compaction": False},
                "turns": [{"id": f"t{i}"} for i in range(turns)],
            }
        ],
    }
    root.mkdir()
    (root / "plan.json").write_text(json.dumps(plan))
    (root / "plan-digest.json").write_text(
        json.dumps({"sha256": digest(root / "plan.json")})
    )
    return root


def in_flight(root, rows, *, alive=True):
    from quirework.runs.storage import process_identity

    state = {
        "done": {},
        "attempts": {},
        "pending": {
            "turn": "t0",
            "phase": "process-started",
            "created_at": time.time() - 600,
            "pid": os.getpid(),
            "process_identity": process_identity(os.getpid()) if alive else "gone",
        },
    }
    (root / "state").mkdir(exist_ok=True)
    (root / "state/reader.json").write_text(json.dumps(state))
    events = root / "records/reader/t0/attempt-000/events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text("".join(json.dumps(r) + "\n" for r in rows))


def thinking(*steps):
    """Claude Code's thinking estimates for one request: running total and step."""
    total, rows = 0, []
    for step in steps:
        total += step
        rows.append(
            {
                "type": "system",
                "subtype": "thinking_tokens",
                "estimated_tokens": total,
                "estimated_tokens_delta": step,
            }
        )
    return rows


def test_a_claude_seat_thinking_mid_turn_shows_its_progress(tmp_path):
    root = claude_record(tmp_path)
    in_flight(root, [{"type": "system", "subtype": "init"}, *thinking(50, 100, 150)])
    native = root / "work/reader/seat.blind-state/claude/projects/w/sid.jsonl"
    native.parent.mkdir(parents=True)
    native.write_text(json.dumps({"type": "user", "message": {"content": "Read."}}))
    seat = observe.observe(root)["seats"]["reader"]
    progress = seat["progress"]
    assert progress["state"] == "running"
    assert progress["now"] == "thinking"
    assert progress["thinking_tokens_estimated"] == 300
    assert progress["seconds_since_last_event"] < 60
    assert 590 <= progress["elapsed_seconds"] < 660
    # Nothing has completed, and the session says so instead of printing
    # an empty model list and a null usage.
    (session,) = seat["sessions"]
    assert "models" not in session and "usage" not in session
    assert "only once the response completes" in session["unavailable"]


def test_thinking_estimates_add_up_across_requests(tmp_path):
    root = claude_record(tmp_path)
    reply = {
        "type": "assistant",
        "message": {"id": "m1", "content": [{"type": "thinking", "thinking": ""}]},
    }
    in_flight(root, [*thinking(50, 100), reply])
    progress = observe.observe(root)["seats"]["reader"]["progress"]
    assert progress["now"] == "writing, after thinking"
    resumed = [
        *thinking(50, 100),
        reply,
        {"type": "user", "isSynthetic": True, "message": {"content": "Resume."}},
        *thinking(50, 25),
    ]
    in_flight(root, resumed)
    progress = observe.observe(root)["seats"]["reader"]["progress"]
    assert progress["thinking_tokens_estimated"] == 225
    assert progress["harness_messages"] == 1


def test_a_turn_whose_process_is_gone_is_not_reported_running(tmp_path):
    root = claude_record(tmp_path)
    in_flight(root, thinking(50), alive=False)
    progress = observe.observe(root)["seats"]["reader"]["progress"]
    assert progress["state"] == "exited, not yet banked"


def test_a_seat_with_no_progress_says_why(tmp_path):
    root = claude_record(tmp_path, turns=2)
    progress = observe.observe(root)["seats"]["reader"]["progress"]
    assert progress == {"state": "not started", "turns": "0 of 2 turns banked"}


def test_a_claude_session_reports_what_its_completed_responses_used(tmp_path):
    root = claude_record(tmp_path, model="fixture")
    native = root / "work/reader/seat.blind-state/claude/projects/w/sid.jsonl"
    native.parent.mkdir(parents=True)
    rows = []
    for ident, stop, out in (("m1", "max_tokens", 300), ("m2", "end_turn", 20)):
        for block in ("thinking", "text"):
            rows.append(
                {
                    "type": "assistant",
                    "effort": "high",
                    "message": {
                        "id": ident,
                        "model": "claude-fixture-1-0",
                        "stop_reason": stop,
                        "content": [{"type": block}],
                        "usage": {"input_tokens": 5, "output_tokens": out},
                    },
                }
            )
    native.write_text("".join(json.dumps(r) + "\n" for r in rows))
    seat = observe.observe(root)["seats"]["reader"]
    (session,) = seat["sessions"]
    assert session["models"] == ["claude-fixture-1-0"]
    assert session["responses"] == 2
    assert session["responses_at_output_limit"] == 1
    # One response's rows repeat its usage; it is counted once.
    assert session["usage"]["output_tokens"] == 320
    assert session["usage"]["input_tokens"] == 10
    # A family alias in the plan is satisfied by any version of the family.
    assert seat["policy_violations"] == []
