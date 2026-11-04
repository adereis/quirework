"""Which turn a review's next one continues, and what a stopped turn banks.

Every record here is fiction written for the test, and no model is called:
each turn's event stream is written by hand, as a harness would leave it.
"""

import json
import subprocess

import pytest

from quirework.project import Project, project_scope
from quirework.sops import review

RUN = "2026-01-01-review-a-fictional-edit"
CODEX = {
    "harness": "codex",
    "model": "demo-model",
    "effort": "high",
    "max_context_window": 400_000,
}
TREE = {"head": "0" * 40, "status": "", "reviewed_hashes": {}}

TURNS = {
    "turns": [
        {"turn": "01", "mode": "run", "session_id": "s-1"},
        {"turn": "02", "mode": "resume", "session_id": "s-1"},
        {"turn": "03", "mode": "fork", "session_id": "s-2"},
    ]
}


def test_a_fork_never_becomes_the_trunk():
    """A fork exists so a disagreement can be pressed while the first answer
    stays intact and quotable. Defaulting to the last turn would move the
    trunk onto the branch the moment anyone argued."""
    assert review._parent_turn(TURNS) == ("02", "s-1")


def test_a_branch_is_continued_by_naming_it():
    assert review._parent_turn(TURNS, "03") == ("03", "s-2")


def test_a_turn_that_never_got_a_session_is_not_a_parent():
    state = {
        "turns": [
            {"turn": "01", "mode": "run", "session_id": "s-1"},
            {"turn": "02", "mode": "resume", "session_id": None},
        ]
    }
    assert review._parent_turn(state) == ("01", "s-1")


def test_an_unknown_turn_is_refused_rather_than_defaulted():
    with pytest.raises(ValueError, match="no completed turn '09'"):
        review._parent_turn(TURNS, "09")


@pytest.mark.parametrize("jailed", [True, False])
def test_a_dry_run_allocates_no_turn(tmp_path, monkeypatch, jailed):
    """`review prompt` launches nothing, so it must create nothing either.

    The next turn's number is counted from the turn directories, so a dry
    run that made one would push the first real turn to 02 and leave an
    empty 01, which reads as a turn that produced no report.
    """
    monkeypatch.setattr(review.blind, "backend_error", lambda: None)
    monkeypatch.setattr(review.blind, "runner_binds", lambda names: ([], []))
    # The jail's own preflight reads the range; a..b names no real commits.
    monkeypatch.setattr(review, "preflight", lambda *a: None)
    repo = tmp_path / "book"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    delta = {"kind": "range", "spec": "a..b", "commits": ["abc1234 Add the kettle"]}
    with project_scope(Project.from_settings(repo, {})):
        out = review._turn(
            RUN,
            "run",
            "Check the kettle.",
            store=tmp_path / "state",
            jailed=jailed,
            dry_run=True,
            config=CODEX,
            delta=delta,
        )
    assert out["prompt"] and out["argv"]
    record = tmp_path / "state" / RUN
    assert not (record / "turns").exists()
    assert review._next_turn(record) == "01"


# --------------------------------------------------------------------------- #
# a turn a provider limit cut short
# --------------------------------------------------------------------------- #
def _events(*rows):
    return "".join(json.dumps(row) + "\n" for row in rows)


@pytest.fixture
def stopped(tmp_path):
    """A run whose first turn met a usage limit with its report half written.

    Call it with ``produced=False`` for a turn the limit refused before the
    reviewer did anything.
    """
    settings = {"runs": {"reviews": "records"}}
    (tmp_path / "book" / "records" / RUN).mkdir(parents=True)
    record = tmp_path / "state" / RUN

    def build(produced=True):
        turn = record / "turns" / "01"
        (turn / "report").mkdir(parents=True)
        (turn / "report" / "report.md").write_text(
            "STATUS: partial\nPROGRESS: chapters 2/5\n\n### F1 The kettle boils dry\n"
        )
        rows = [{"type": "thread.started", "thread_id": "th-7"}]
        if produced:
            item = {"type": "agent_message", "text": "Reading the kettle chapter."}
            rows.append({"type": "item.completed", "item": item})
        error = {
            "code": "rate_limit_exceeded",
            "message": "usage limit reached",
            "resets_at": 4_000_000_000,
        }
        rows.append({"type": "turn.failed", "error": error})
        (turn / "events.jsonl").write_text(_events(*rows))
        (turn / "prompt.md").write_text("The original frame, brief and delta.")
        review.write_json(
            record / "run.json",
            {
                "run": RUN,
                "config": CODEX,
                "jailed": False,
                "delta": {
                    "kind": "range",
                    "spec": "a..b",
                    "commits": ["abc1234 Add the kettle"],
                    "paths": ["chapters/kettle.md"],
                },
                "turns": [],
            },
        )
        return record, turn

    with project_scope(Project.from_settings(tmp_path / "book", settings)):
        yield build


def _bank(record, **kwargs):
    return review._collect(
        RUN, "01", TREE, TREE, 0.0, store=record.parent, jailed=False, **kwargs
    )


def test_a_stopped_turn_banks_the_session_it_never_returned(stopped):
    """A continuation needs the session id, and a failed turn has no receipt.

    The id survives only in the event stream, so a driver that read it from
    a successful receipt alone banked none, and left the review unresumable.
    """
    record, _ = stopped()
    out = _bank(record)
    assert out["session_id"] == "th-7"
    assert out["status"] == "quota-stopped"
    (banked,) = review.read_json(record / "run.json")["turns"]
    assert banked["quota_stop"] == "interrupted"
    assert banked["quota_scope"] == "rate_limit_exceeded"
    # The half-written report is still the turn's product.
    assert out["findings"] == 1
    assert review.status(RUN, store=record.parent)["continuable"] == ["01"]


def test_a_refused_turn_is_reported_as_refused_not_interrupted(stopped):
    """A different recovery: nothing ran, so there is nothing to continue."""
    record, _ = stopped(produced=False)
    assert _bank(record)["status"] == "quota-refused"
    assert review.status(RUN, store=record.parent)["continuable"] == []


def test_a_continuation_sends_only_the_notice_and_keeps_the_same_report(stopped):
    """The brief, the delta and the persona are already in the session.

    Sending them again is how a continuation turns back into the restart it
    exists to avoid, and a fresh report would renumber banked findings.
    """
    record, turn = stopped()
    _bank(record)
    out = review._turn(
        RUN, "continue", "", store=record.parent, jailed=False, dry_run=True
    )
    prompt = out["prompt"]
    assert "quota limit" in prompt
    assert "Add the kettle" not in prompt, "the delta must not be sent again"
    assert "original frame" not in prompt
    assert str(turn / "report" / "report.md") in prompt
    assert "do not disturb or renumber existing findings" in prompt
    assert out["argv"][:4] == ["codex", "exec", "resume", "th-7"]
    assert not (record / "turns" / "02").exists()


def test_a_continuation_without_a_session_says_so(stopped):
    record, _ = stopped()
    review.write_json(
        record / "run.json",
        {
            **review.read_json(record / "run.json"),
            "turns": [{"turn": "01", "status": "failed", "session_id": None}],
        },
    )
    with pytest.raises(ValueError, match="no recorded session id to continue"):
        review._turn(
            RUN, "continue", "", store=record.parent, jailed=False, dry_run=True
        )
    assert not (record / "turns" / "02").exists()


@pytest.mark.parametrize(
    "mode, status",
    [("resume", "wrong-session"), ("continue", "wrong-session"), ("fork", "completed")],
)
def test_a_turn_that_lands_in_another_session_is_banked_and_said(
    stopped, capsys, mode, status
):
    """Banked, not raised: by then a foreign session may have written into
    the report, and the operator needs the record. A fork is exempt, since a
    new session is its whole purpose."""
    record, turn = stopped()
    (turn / "events.jsonl").write_text(
        _events(
            {"type": "thread.started", "thread_id": "th-9"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Hi."},
            },
            {"type": "turn.completed", "usage": {}},
        )
    )
    out = _bank(record, mode=mode, expect_session="th-7")
    assert out["status"] == status
    (banked,) = review.read_json(record / "run.json")["turns"]
    assert banked["status"] == status and banked["session_id"] == "th-9"
    said = capsys.readouterr()
    if status == "wrong-session":
        assert "not a continuation" in out["error"]
        assert f"{mode} landed in session th-9, not th-7" in said.err
    else:
        assert out["error"] is None
    assert said.out == ""
