"""A review followed as it runs: its turns, its reading and its report."""

import json

from quirework.project import current_project
from quirework.runs import feed
from quirework.runs.storage import write
from quirework.sops import review

NAME = "2026-01-02-review-kettle"


def told(following):
    return "\n".join(following.poll())


def launch(record, turn, mode="run", parent=None):
    folder = record / "turns" / turn
    write(folder / "launch.json", {"mode": mode, "parent_turn": parent})
    return folder


def stream(folder, rows):
    with (folder / "events.jsonl").open("a") as out:
        out.writelines(json.dumps(row) + "\n" for row in rows)


def tool(name, **given):
    content = [{"type": "tool_use", "name": name, "input": given}]
    return {"type": "assistant", "message": {"content": content}}


def test_a_review_is_told_with_its_report_as_the_reviewer_writes_it(tmp_path):
    store = tmp_path / "store"
    record = store / NAME
    following = review.Feed(NAME, store, width=100)
    assert told(following) == ""
    write(record / "run.json", {"config": {"harness": "claude", "model": "opus"}})
    folder = launch(record, "01")
    checkout = current_project().root
    report = folder / "report" / "report.md"
    report.parent.mkdir(parents=True)
    stream(
        folder,
        [
            tool("Read", file_path=str(checkout / "chapters" / "kettle.md")),
            tool("Write", file_path=f"{review.JAILED_REPORT}/report.md", content="x"),
        ],
    )
    report.write_text("## F1 The kettle boils twice\n\nSTATUS: partial\n")
    text = told(following)
    assert "reviewer [claude/opus] starts turn 01: a first reading" in text
    assert "read     chapters/kettle.md" in text
    assert "wrote    report/report.md" in text
    assert "## F1 The kettle boils twice" in text
    report.write_text(
        "## F1 The kettle boils twice\n\n## F2 Noon comes early\n\nSTATUS: complete\n"
    )
    text = told(following)
    assert "## F2 Noon comes early" in text and "F1" not in text
    entry = {
        "turn": "01",
        "status": "completed",
        "seconds": 75,
        "findings": 2,
        "report_status": "complete",
    }
    write(
        record / "run.json",
        {"config": {"harness": "claude", "model": "opus"}, "turns": [entry]},
    )
    assert (
        "reviewer finished turn 01: completed in 1m15s, 2 findings, report says complete"
        in told(following)
    )


def test_a_continued_turn_shows_only_what_it_adds_to_the_report(tmp_path):
    store = tmp_path / "store"
    record = store / NAME
    write(record / "run.json", {"config": {"harness": "codex", "model": "terra"}})
    first = launch(record, "01")
    (first / "report").mkdir()
    (first / "report" / "report.md").write_text("## F1 The kettle boils twice\n")
    following = review.Feed(NAME, store, history="none", width=100)
    launch(record, "02", mode="continue", parent="01")
    assert "starts turn 02: continuing turn 01 after a provider limit" in told(
        following
    )
    (first / "report" / "report.md").write_text(
        "## F1 The kettle boils twice\n\n## F2 Noon comes early\n"
    )
    text = told(following)
    assert "## F2 Noon comes early" in text and "F1" not in text


def test_a_continuation_followed_with_its_stopped_turn_repeats_nothing(tmp_path):
    store = tmp_path / "store"
    record = store / NAME
    write(record / "run.json", {"config": {"harness": "codex", "model": "terra"}})
    first = launch(record, "01")
    (first / "report").mkdir()
    (first / "report" / "report.md").write_text("## F1 The kettle boils twice\n")
    following = review.Feed(NAME, store, width=100)
    assert "## F1" in told(following)
    (first / "report" / "report.md").write_text(
        "## F1 The kettle boils twice\n\n## F2 Noon comes early\n"
    )
    launch(record, "02", mode="continue", parent="01")
    text = told(following)
    assert text.count("## F2 Noon comes early") == 1 and "F1" not in text


def test_a_review_run_tells_its_first_turn_though_its_record_came_later(tmp_path):
    """`run` names the review before `start` writes anything."""
    store = tmp_path / "store"
    following = review.Feed(NAME, store, history="none", show=feed.BRIEF, width=100)
    assert told(following) == ""
    record = store / NAME
    write(record / "run.json", {"config": {"harness": "claude", "model": "opus"}})
    folder = launch(record, "01")
    stream(folder, [tool("Write", file_path=f"{review.JAILED_REPORT}/report.md")])
    text = told(following)
    assert "starts turn 01" in text and "wrote    report/report.md" in text
