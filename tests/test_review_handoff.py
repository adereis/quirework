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
