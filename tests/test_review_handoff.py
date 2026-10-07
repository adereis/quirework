"""What a review turn tells the operator next, and which harness a name implies."""

import pytest

from quirework.project import Project, project_scope
from quirework.providers import models
from quirework.sops import review

RUN = "2026-01-01-review-a-fictional-edit"


def entry(**overrides):
    base = {
        "turn": "01",
        "status": "completed",
        "report": "01-codex-demo-model.md",
        "model": "demo-model",
        "selected_model": "demo-model",
        "effort": "high",
        "session_id": "s-1",
        "find_only": True,
        "workspace": "unchanged",
        "quota_resets_at": None,
    }
    return base | overrides


@pytest.fixture
def project(tmp_path):
    settings = {"runs": {"reviews": "records/reviews"}}
    with project_scope(Project.from_settings(tmp_path, settings)):
        yield tmp_path


@pytest.mark.parametrize(
    "name, harness",
    [
        ("opus", "claude"),
        ("fable", "claude"),
        ("opus-5.5", "claude"),
        ("claude-sonnet-5", "claude"),
        ("terra", "codex"),
        ("gpt-6-astra", "codex"),
        ("gemini-pro", "codex"),
    ],
)
def test_only_a_claude_name_leaves_codex(name, harness):
    # Antigravity is never inferred: its effort is part of the model id.
    assert models.harness_for(name) == harness


def test_a_completed_turn_states_its_report_its_checks_and_the_trailer(project):
    out = review.handoff(entry(), RUN, "codex", project / "state")
    assert out["report_path"] == f"records/reviews/{RUN}/01-codex-demo-model.md"
    assert out["checks"][0].startswith("model demo-model at effort high")
    assert "no compaction" in out["checks"][1]
    assert out["checks"][2] == "find-only (workspace: unchanged)"
    assert out["report_path"] in out["next"]
    assert f"`Reviewed-by: demo-model ({RUN})`" in out["next"]
    jailed = review.handoff(
        entry(sandbox="danger-full-access"), RUN, "codex", project / "state"
    )
    assert jailed["checks"][0] == (
        "model demo-model at effort high, sandbox danger-full-access, "
        "as the harness turn context recorded them"
    )


def test_an_unconfirmed_effort_is_not_claimed(project):
    out = review.handoff(entry(), RUN, "claude", project / "state")
    assert "requested, not confirmed" in out["checks"][0]
    bare = review.handoff(entry(model=None), RUN, "codex", project / "state")
    assert "no served receipt" in bare["checks"][0]


def test_a_quota_stop_names_the_continuation_not_a_relaunch(project):
    out = review.handoff(
        entry(status="quota-stopped", quota_resets_at="at noon"),
        RUN,
        "codex",
        project / "state",
    )
    assert out["next"].startswith(f"quirework sop review continue {RUN}")
    # A turn that did not complete proved nothing about model or compaction.
    assert out["checks"] == ["find-only (workspace: unchanged)"]


def test_a_failed_turn_points_at_its_evidence_and_refuses_a_duplicate(project):
    out = review.handoff(
        entry(status="failed", find_only=False, workspace="unjailed-and-moved"),
        RUN,
        "codex",
        project / "state",
    )
    assert "events.jsonl" in out["next"] and "Do not launch a duplicate" in out["next"]
    assert out["checks"] == [
        "FIND-ONLY NOT ESTABLISHED (workspace: unjailed-and-moved)"
    ]


def test_a_report_that_says_it_stopped_short_is_not_passed_off_as_done(project):
    # The harness finished cleanly; the reviewer's own report did not.
    out = review.handoff(
        entry(report_status="partial", progress="PROGRESS: commits 0/3"),
        RUN,
        "codex",
        project / "state",
    )
    assert out["checks"][2] == (
        "REVIEW NOT FINISHED: the report ends at STATUS: partial "
        "(PROGRESS: commits 0/3)"
    )
    assert out["next"].startswith("The reviewer's own report says it did not")
    assert f"quirework sop review resume {RUN}" in out["next"]
    # The findings it did bank are still to be verified, with the trailer.
    assert f"`Reviewed-by: demo-model ({RUN})`" in out["next"]


def _banked(project, report=None):
    """Collect one completed codex turn, with or without a report file."""
    record = project / "state" / RUN
    (project / "records" / "reviews" / RUN).mkdir(parents=True)
    turn = record / "turns" / "01"
    (turn / "report").mkdir(parents=True)
    if report is not None:
        (turn / "report" / "report.md").write_text(report)
    said = "No command ran, so nothing in the delta was reviewed."
    (turn / "events.jsonl").write_text(
        "".join(
            review.json.dumps(row) + "\n"
            for row in (
                {"type": "thread.started", "thread_id": "s-1"},
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": said},
                },
                {"type": "turn.completed", "usage": {}},
            )
        )
    )
    (record / "run.json").write_text(
        review.json.dumps(
            {
                "run": RUN,
                "config": {"harness": "codex", "model": "demo-model", "effort": "high"},
                "delta": {"kind": "worktree", "paths": ["chapter.md"]},
                "turns": [],
            }
        )
    )
    tree = {"head": "0" * 40, "status": ""}
    return review._collect(
        RUN,
        "01",
        tree,
        tree,
        0.0,
        store=record.parent,
        jailed=False,
        report_dir=turn / "report",
    )


def test_a_reviewer_that_never_wrote_its_report_is_not_passed_off(project, capsys):
    """A reviewer whose tools all failed completes like any other.

    It wrote no report, no status line and no findings, and its final message
    said in plain words that nothing was reviewed. Every receipt check passed
    and the turn read as an empty review.
    """
    out = _banked(project)
    assert out["status"] == "completed" and out["findings"] == 0
    assert "REPORT FILE NOT WRITTEN" in out["checks"][2]
    assert out["next"].startswith("The reviewer never wrote the report file")
    assert "never wrote its report file" in capsys.readouterr().err
    (turn,) = review.status(RUN, store=project / "state")["turns"]
    assert turn["answer_source"] == "final message"


def test_a_written_report_carries_no_such_flag(project, capsys):
    out = _banked(project, report="STATUS: complete\n\n## F1 A fictional defect\n")
    assert out["findings"] == 1
    assert not any("NOT WRITTEN" in check for check in out["checks"])
    assert out["next"].startswith("Read records/reviews/")
    assert "never wrote" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "body, stated, flagged",
    [
        ("STATUS: partial\n...\nSTATUS: complete\n", "complete", False),
        ("STATUS: **Complete.**\n", "**Complete.**", False),
        ("STATUS: partial\nPROGRESS: 2/9\n", "partial", True),
        ("A report with no status line.\n", None, False),
    ],
)
def test_only_the_last_status_line_counts(body, stated, flagged):
    assert review._report_status(body) == stated
    assert review._unfinished(stated) is flagged


@pytest.mark.parametrize(
    "config, line",
    [
        (
            {"harness": "codex", "model": "demo-model", "effort": "high"}
            | {"max_context_window": 400000},
            "harness=codex model=demo-model effort=high context=400000 "
            "compaction=prevented jailed=true",
        ),
        (
            {"harness": "claude", "model": "opus", "effort": "xhigh"}
            | {"model_pinned": "claude-opus-5-5"},
            f"harness=claude model=claude-opus-5-5 effort=xhigh "
            f"context={models.CLAUDE_MAX_WINDOW} compaction=prevented jailed=true",
        ),
        (
            {"harness": "agy", "model": "gemini-pro-high", "effort": "high"},
            "harness=agy model=gemini-pro-high effort=high context=default "
            "compaction=detected jailed=true",
        ),
    ],
)
def test_one_line_names_the_reviewer_before_the_argv(config, line):
    """The jail is a hundred lines of mounts; what gets checked is this."""
    assert review.launch_summary(config, True) == line
    assert review.launch_summary(config, False).endswith("jailed=false")


def test_a_short_status_keeps_what_a_poll_asks_and_drops_the_rest(project):
    (project / "records" / "reviews" / RUN).mkdir(parents=True)
    record = project / "state" / RUN
    for turn, body in (("01", "STATUS: complete\n"), ("02", "STATUS: partial\n")):
        (record / "turns" / turn / "report").mkdir(parents=True)
        (record / "turns" / turn / "report" / "report.md").write_text(
            body + "PROGRESS: commits 2/7\n"
        )
    (record / "run.json").write_text(
        review.json.dumps(
            {
                "run": RUN,
                "config": {"harness": "codex", "model": "demo-model"},
                "delta": {"kind": "range", "range": "a..b"},
                "turns": [entry(report_status="complete", findings=3, mode="run")],
            }
        )
    )
    short = review.short_status(review.status(RUN, store=record.parent))
    assert set(short) == {"run", "turns", "uncollected_turns", "continuable"}
    assert short["turns"] == [
        {
            "turn": "01",
            "mode": "run",
            "status": "completed",
            "report_status": "complete",
            "findings": 3,
        }
    ]
    (pending,) = short["uncollected_turns"]
    assert pending["turn"] == "02"
    assert pending["report_status"] == "partial"
    assert pending["progress"] == "PROGRESS: commits 2/7"
    assert not any(key.endswith("_path") for key in pending)
    assert short["continuable"] == []
