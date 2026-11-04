"""The find-only checksum, and who a moved workspace is blamed on.

The repositories here are synthetic, and every path in them is fiction.
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


def _repo(tmp_path, *tracked):
    """A checkout with one commit holding ``tracked``, each a fictional note."""
    repo = tmp_path / "book"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for name in tracked:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(f"A fictional note called {name}.\n")
    for args in (("add", "."), ("commit", "-qm", "Add the notes")):
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            env={**ENV, "HOME": str(repo)},
        )
    return repo


def _untracked(repo, *names):
    for name in names:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("Written during the run.\n")


@pytest.mark.parametrize(
    "policy, runs",
    [
        # The driver's own default.
        (None, ".quirework/reviews"),
        # A project that keeps its review records beside other records.
        ('own_output = "records/*-review-*"\n', "records"),
    ],
)
def test_the_checksum_ignores_every_review_run_and_watches_the_rest(
    tmp_path, policy, runs
):
    """Every run's directory, not just this one's.

    Two reviews launched together accused each other: the second saw the
    first's directory appear and reported a violation neither had made.
    The exclusion is one glob, not a blanket, so the rest of the tree, the
    records directory's other contents among it, is still watched.
    """
    repo = _repo(tmp_path, "kettle.md", "records/README.md")
    settings = {}
    if policy:
        (repo / "review.toml").write_text(policy)
        settings = {"sops": {"review_policy": "review.toml"}}
    _untracked(
        repo,
        f"{runs}/2026-01-01-review-kettle/report.md",
        f"{runs}/2026-01-02-review-tide/report.md",
        "records/notes/today.md",
        "stray.md",
    )
    with project_scope(Project.from_settings(repo, settings)):
        state = review.worktree_state()
        head = review.git("rev-parse", "HEAD").strip()
    assert state["head"] == head
    assert "-review-" not in state["status"]
    assert "?? records/notes/" in state["status"].splitlines()
    assert "?? stray.md" in state["status"].splitlines()


def test_an_unstaged_edit_keeps_the_first_column_of_its_status(tmp_path):
    """Porcelain's first column is the index, blank for an unstaged edit.

    Stripping the whole status would take that blank from the first line
    only, and cost that one path its first letter when it is read back.
    """
    repo = _repo(tmp_path, "kettle.md")
    (repo / "kettle.md").write_text("Edited while the review ran.\n")
    with project_scope(Project.from_settings(repo, {})):
        after = review.worktree_state()
    assert after["status"] == " M kettle.md"
    before = {"head": after["head"], "status": ""}
    assert review._moved_paths(before, after) == ["kettle.md"]


def test_moved_paths_reads_status_and_head_together():
    """Status alone misses a commit, and HEAD alone misses an edit."""
    before = {"head": "h1", "status": " M chapters/kettle.md\n?? notes.md"}
    after = {
        "head": "h1",
        "status": "M  chapters/kettle.md\n?? notes.md\n?? chapters/tide.md",
    }
    assert review._moved_paths(before, after) == [
        "chapters/kettle.md",
        "chapters/tide.md",
    ]
    assert review._moved_paths(before, {**before, "head": "h2"}) == ["HEAD"]


def test_a_staged_file_that_becomes_unstaged_has_moved():
    """The set of paths is the same; only the columns say it changed."""
    before = {"head": "h1", "status": "M  chapters/kettle.md"}
    after = {"head": "h1", "status": " M chapters/kettle.md"}
    assert review._moved_paths(before, after) == ["chapters/kettle.md"]


def test_nothing_moved_is_a_clean_find_only():
    verdict = review._find_only_verdict([], [], jailed=True)
    assert verdict == {"find_only": True, "workspace": "unchanged", "note": ""}


def test_a_jailed_reviewer_is_never_blamed_for_a_workspace_that_moved():
    """A jailed reviewer cannot write to the checkout, so movement outside
    the reviewed files is somebody else's: a concurrent agent's commit, or
    another run's directory."""
    verdict = review._find_only_verdict(["tools/build.py"], [], jailed=True)
    assert verdict["find_only"] is True
    assert verdict["workspace"] == "concurrent-activity"
    assert "somebody else" in verdict["note"]


def test_an_unjailed_reviewer_is_a_candidate_author():
    """Without the jail the guarantee is gone, and the loud line comes back."""
    verdict = review._find_only_verdict(["tools/build.py"], [], jailed=False)
    assert verdict["find_only"] is False
    assert verdict["workspace"] == "unjailed-and-moved"
    assert "candidate author" in verdict["note"]


@pytest.mark.parametrize("jailed", [True, False])
def test_movement_in_the_reviewed_files_is_always_loud(jailed):
    """A worry apart from authorship, and the jail does not settle it: if the
    files under review move mid-run, the report may cite a state that is
    gone, whoever moved them."""
    moved = ["chapters/kettle.md"]
    verdict = review._find_only_verdict(moved, moved, jailed)
    assert verdict["workspace"] == "reviewed-files-moved"
    assert "no longer exists" in verdict["note"]
    # The jail still settles who wrote it.
    assert verdict["find_only"] is jailed
