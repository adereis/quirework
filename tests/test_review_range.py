"""What a range review reads, stated before it runs; a synthetic repository."""

import subprocess
from pathlib import Path

import pytest

from quirework.project import Project, project_scope
from quirework.sops import review


def commit(repo, name):
    (repo / f"{name}.md").write_text(f"A fictional note called {name}.\n")
    for argv in (["add", "."], ["commit", "-qm", f"Add {name}"]):
        subprocess.run(
            ["git", "-C", str(repo), *argv],
            check=True,
            env={
                "GIT_AUTHOR_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@example.invalid",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "test@example.invalid",
                "HOME": str(repo),
                "PATH": "/usr/bin:/bin",
            },
        )


def test_a_range_names_its_commits_and_the_base_it_leaves_out(tmp_path):
    repo = tmp_path / "book"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for name in ("first", "second", "third"):
        commit(repo, name)
    with project_scope(Project.from_settings(repo, {})):
        base = review.git("rev-parse", "HEAD~2").strip()
        delta = review.resolve_range("HEAD~2..HEAD")
        lines = review.describe_range(delta)
        # `A..B` excludes `A`: two commits, and the one named first is the base.
        assert lines[0] == "range HEAD~2..HEAD: 2 commit(s)"
        assert [line.split(" ", 3)[-1] for line in lines[1:3]] == [
            "Add third",
            "Add second",
        ]
        assert lines[-1] == f"  base, not reviewed: {base[:9]}"
        assert "base" not in review.resolve_range("HEAD~2...HEAD")


def test_a_review_needs_a_delta():
    for spec in ("", None):
        with pytest.raises(ValueError, match="a review needs a delta"):
            review.resolve_range(spec)


def test_a_ref_that_is_not_a_range_is_refused_before_a_model_is_paid_for():
    with pytest.raises(ValueError, match="not a git range: 'HEAD~2'"):
        review.resolve_range("HEAD~2")


def test_a_range_that_covers_no_commits_is_refused(tmp_path):
    repo = tmp_path / "book"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    commit(repo, "first")
    with (
        project_scope(Project.from_settings(repo, {})),
        pytest.raises(ValueError, match="covers no commits"),
    ):
        review.resolve_range("HEAD..HEAD")


@pytest.mark.parametrize("kind", ["consistency", "procedure"])
def test_the_brief_the_delta_and_the_report_path_reach_the_reviewer(kind):
    delta = {"kind": "range", "spec": "a1..b2", "commits": ["abc1234 Add the kettle"]}
    report = Path("/report/report.md")
    prompt = review.compose("Check the kettle's whistle.", delta, report, kind=kind)
    assert "Check the kettle's whistle." in prompt
    assert "git diff a1..b2" in prompt and "abc1234 Add the kettle" in prompt
    assert str(report) in prompt
    worktree = {"kind": "worktree", "spec": "HEAD", "commits": []}
    prompt = review.compose("Check the tide.", worktree, report, kind=kind)
    assert "Check the tide." in prompt and "uncommitted working tree" in prompt
