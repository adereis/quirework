"""Which reviewable commits carry no review; a synthetic repository.

The history is fiction: a book of kettles and tides whose review policy
declares its chapters reviewable, and whose tools directory is not.
"""

import subprocess

import pytest

from quirework.project import Project, project_scope
from quirework.sops import review

ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "PATH": "/usr/bin:/bin",
}
TRAILER = "Reviewed-by: demo-model (2026-01-01-review-kettle)"


def _git(repo, *args):
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**ENV, "HOME": str(repo)},
    )
    return out.stdout.strip()


def _commit(repo, message, *paths):
    for path in paths:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        with (repo / path).open("a") as out:
            out.write(f"A line for {message!r}.\n")
    _git(repo, "add", *paths)
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def history(tmp_path):
    """Four commits: one unreviewed, one reviewed, then tooling, then prose."""
    repo = tmp_path / "book"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "review.toml").write_text('reviewable = ["chapters/"]\n')
    commits = {
        "first": _commit(repo, "Add the kettle", "chapters/kettle.md"),
        "reviewed": _commit(
            repo, f"Revise the kettle\n\n{TRAILER}", "chapters/kettle.md"
        ),
        "tooling": _commit(repo, "Add a build script", "tools/build.py"),
        "prose": _commit(
            repo,
            "Add the tide",
            "chapters/tide.md",
            "chapters/kettle.md",
            "tools/build.py",
        ),
    }
    settings = {"sops": {"review_policy": "review.toml"}}
    with project_scope(Project.from_settings(repo, settings)):
        yield commits


def test_the_audit_starts_from_the_last_reviewed_commit(history):
    """The natural question is what has landed since the last review, so
    that is the default rather than a ref the operator must remember."""
    out = review.audit()
    assert out["range"] == f"{history['reviewed']}..HEAD"
    assert out["commits"] == 2
    assert [row["commit"] for row in out["unreviewed"]] == [history["prose"][:8]]


def test_the_audit_counts_only_reviewable_paths(history):
    """A tooling commit is not unreviewed prose, and a commit touching both
    counts only the files a reviewer of the work would check."""
    (row,) = review.audit()["unreviewed"]
    assert row == {
        "commit": history["prose"][:8],
        "subject": "Add the tide",
        "files": 2,
    }


def test_a_commit_that_carries_the_trailer_is_not_reported(history):
    out = review.audit(f"{history['first']}..HEAD")
    assert out["commits"] == 3
    reported = [row["commit"] for row in out["unreviewed"]]
    assert history["reviewed"][:8] not in reported
    assert reported == [history["prose"][:8]]


def test_a_bare_ref_is_read_as_a_range_to_head(history):
    assert review.audit("HEAD~3")["range"] == "HEAD~3..HEAD"
    spec = f"{history['first']}..{history['tooling']}"
    assert review.audit(spec)["range"] == spec
