import json

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
