"""What a range review reads, stated before it runs; a synthetic repository."""

import subprocess

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
