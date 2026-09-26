"""What `review status` can say about a turn that is still running."""

import json

import pytest

from quirework.project import Project, project_scope
from quirework.providers import models
from quirework.sops import review

RUN = "2026-01-01-review-a-fictional-edit"


@pytest.fixture
def record(tmp_path):
    settings = {"runs": {"reviews": "records/reviews"}}
    (tmp_path / "records" / "reviews" / RUN).mkdir(parents=True)
    with project_scope(Project.from_settings(tmp_path, settings)):
        yield tmp_path / "state" / RUN


def lines(*rows, tail=""):
    """A stream as a running harness leaves it: complete rows, then a tail."""
    return "".join(json.dumps(r) + "\n" for r in rows) + tail


def open_run(record, config, turns=()):
    record.mkdir(parents=True, exist_ok=True)
    (record / "run.json").write_text(
        json.dumps({"run": RUN, "config": config, "delta": {}, "turns": list(turns)})
    )


def pending_turn(record, turn, events, launch=None, report=None):
    folder = record / "turns" / turn
    (folder / "report").mkdir(parents=True)
    (folder / "events.jsonl").write_text(events)
    if launch is not None:
        (folder / "launch.json").write_text(json.dumps(launch))
    if report is not None:
        (folder / "report" / "report.md").write_text(report)
    return folder


def uncollected(record):
    return review.status(RUN, store=record.parent)["uncollected_turns"]


def test_compaction_is_read_from_event_types_not_from_what_was_read(record):
    """Grepping the stream for the word found five hits and no compaction."""
    open_run(record, {"harness": "claude", "model": "opus", "effort": "high"})
    said = {"type": "text", "text": "The procedure says compaction is a red flag."}
    usage = {"input_tokens": 10, "cache_read_input_tokens": 240_000}
    pending_turn(
        record,
        "01",
        lines(
            {"type": "system", "subtype": "init"},
            {"type": "assistant", "message": {"content": [said], "usage": usage}},
            {"type": "user", "message": {"content": [said]}},
            tail='{"type": "assistant", "message": {"usage": {"input_to',
        ),
    )
    (turn,) = uncollected(record)
    assert turn["compaction_events"] == []
    assert turn["events"] == 3
    assert turn["unreadable_lines"] == 0
    assert turn["context"] == {
        "last_request": 240_010,
        "window": models.CLAUDE_MAX_WINDOW,
        "share": "24%",
    }
    stream = record / "turns" / "01" / "events.jsonl"
    stream.write_text(
        lines({"type": "system", "subtype": "compact_boundary"}, {"type": "bad"})
        + "not json\n"
    )
    (turn,) = uncollected(record)
    assert turn["compaction_events"] == ["compact_boundary"]
    assert turn["unreadable_lines"] == 1


def test_a_jailed_codex_turn_is_measured_from_its_native_transcript(record):
    config = {"harness": "codex", "model": "demo-model", "effort": "high"}
    open_run(record, config | {"max_context_window": 400_000})
    private = record / ".blind" / "codex"
    sessions = private / "sessions" / "2026" / "01" / "01"
    sessions.mkdir(parents=True)
    count = {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": 90_000,
                    "cached_input_tokens": 10_000,
                }
            },
        },
    }
    (sessions / "rollout-2026-01-01-s-7.jsonl").write_text(
        lines(count, {"type": "compacted", "payload": {"message": ""}})
    )
    # Another session in the same private home is not this turn's.
    (sessions / "rollout-2026-01-01-s-8.jsonl").write_text(
        lines({"type": "compacted", "payload": {}})
    )
    pending_turn(
        record,
        "01",
        lines({"type": "thread.started", "thread_id": "s-7"}),
        launch={"private_runtime": str(private)},
    )
    (turn,) = uncollected(record)
    assert turn["compaction_events"] == ["compacted"]
    assert turn["context"]["last_request"] == 100_000
    assert turn["context"]["share"] == "25%"


def test_what_cannot_be_measured_says_why_rather_than_reading_as_zero(record):
    open_run(record, {"harness": "codex", "model": "m", "max_context_window": 1})
    pending_turn(record, "01", "", launch={"private_runtime": None})
    (turn,) = uncollected(record)
    assert turn["context"]["last_request"] is None
    assert "unjailed" in turn["context"]["evidence"]
    open_run(record, {"harness": "agy", "model": "gemini-pro-high"})
    (turn,) = uncollected(record)
    assert "agy reports no per-request usage" == turn["context"]["evidence"]


def test_a_continued_turn_shows_the_report_it_is_still_writing(record):
    """A continuation writes on in the interrupted turn's report."""
    open_run(
        record,
        {"harness": "claude", "model": "opus", "effort": "high"},
        turns=[{"turn": "01", "status": "quota-stopped", "session_id": "s-1"}],
    )
    folder = record / "turns" / "01" / "report"
    folder.mkdir(parents=True)
    (folder / "report.md").write_text("STATUS: partial\nPROGRESS: commits 4/9\n")
    pending_turn(record, "02", "", launch={"mode": "continue", "parent_turn": "01"})
    (turn,) = uncollected(record)
    assert turn["turn"] == "02"
    assert turn["progress"] == "PROGRESS: commits 4/9"
    assert turn["report_path"].endswith("turns/01/report/report.md")
