"""A jailed reviewer can read the history it reviews; a synthetic repository."""

import shutil
import subprocess
import sys

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

REAL_BWRAP = sys.platform.startswith("linux") and shutil.which("bwrap")


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env={**ENV, "HOME": str(repo)},
    )


def _commit(repo, name):
    (repo / f"{name}.md").write_text(f"A fictional note called {name}.\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", f"Add {name}")


def _worktree(tmp_path):
    """A main checkout with one commit, and a linked worktree with two more."""
    main = tmp_path / "book"
    main.mkdir()
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    _commit(main, "first")
    linked = tmp_path / "book-review"
    _git(main, "worktree", "add", "-q", "-b", "edits", str(linked))
    for name in ("second", "third"):
        _commit(linked, name)
    return main, linked


def test_a_worktree_binds_the_shared_git_directory_once(tmp_path):
    main, linked = _worktree(tmp_path)
    with project_scope(Project.from_settings(linked, {})):
        # The worktree's own directory lives inside the shared one, so a
        # single bind carries both.
        assert review.git_dirs() == [(main / ".git").resolve()]
        binds = review.repository_binds()
        assert binds[:3] == ["--ro-bind", str(linked), str(linked)]
        shared = str((main / ".git").resolve())
        assert binds[3:] == ["--ro-bind", shared, shared]


def test_an_ordinary_checkout_needs_no_bind_beyond_its_root(tmp_path):
    main, _ = _worktree(tmp_path)
    with project_scope(Project.from_settings(main, {})):
        assert review.git_dirs() == []
        assert review.repository_binds() == ["--ro-bind", str(main), str(main)]


@pytest.mark.skipif(not REAL_BWRAP, reason="needs the real bubblewrap")
def test_inside_a_real_jail_a_worktree_reviewer_reads_the_range(tmp_path, monkeypatch):
    _, linked = _worktree(tmp_path)
    record = tmp_path / "record"
    with project_scope(Project.from_settings(linked, {})):
        delta = review.resolve_range("HEAD~2..HEAD")
        seen = review.preflight(delta, record, ENV)
        assert seen["commits_readable"] == 2
        # Nothing is created by the probe: it carries no runner.
        assert not record.exists()
        # The binding this replaced, the root alone, is the failure the
        # preflight exists to catch before a model is paid for.
        root = str(linked)
        monkeypatch.setattr(
            review, "repository_binds", lambda: ["--ro-bind", root, root]
        )
        with pytest.raises(ValueError, match="git cannot read the delta"):
            review.preflight(delta, record, ENV)
