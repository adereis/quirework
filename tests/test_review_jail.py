"""A jailed reviewer can read the history it reviews; a synthetic repository."""

import json
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


def _hiding(repo, *hidden, reviews=None):
    """A project whose review policy hides these checkout paths."""
    (repo / "review.toml").write_text(
        "hidden = [" + ", ".join(f'"{h}"' for h in hidden) + "]\n"
    )
    settings = {"sops": {"review_policy": "review.toml"}}
    if reviews:
        settings["runs"] = {"reviews": reviews}
    return Project.from_settings(repo, settings)


def test_hidden_paths_are_masked_after_the_checkout_is_bound(tmp_path):
    main, _ = _worktree(tmp_path)
    (main / "scratch" / "rulings").mkdir(parents=True)
    (main / "scratch" / "rulings" / "verdicts.md").write_text("A settled call.\n")
    (main / "notes.md").write_text("The operator's priors.\n")
    (main / "records" / "reviews").mkdir(parents=True)
    with project_scope(
        _hiding(
            main, "scratch", "scratch/rulings", "notes.md", reviews="records/reviews"
        )
    ):
        # The nested path is covered by its parent, and the review records
        # join without being named.
        assert review.hidden_paths() == [
            (main / "scratch").resolve(),
            (main / "notes.md").resolve(),
            (main / "records" / "reviews").resolve(),
        ]
        binds = review.repository_binds()
        assert binds[:3] == ["--ro-bind", str(main), str(main)]
        assert binds[3:] == [
            "--tmpfs",
            str(main / "scratch"),
            "--ro-bind",
            "/dev/null",
            str(main / "notes.md"),
            "--tmpfs",
            str(main / "records" / "reviews"),
        ]
        # Only the committed one: the tracked file's history outlives a mask.
        assert review._tracked(review.hidden_paths()) == []
        _git(main, "add", "notes.md")
        assert review._tracked(review.hidden_paths()) == ["notes.md"]


@pytest.mark.parametrize("pattern", ["/etc", "../elsewhere", "link"])
def test_a_hidden_path_outside_the_checkout_or_behind_a_link_is_refused(
    tmp_path, pattern
):
    main, _ = _worktree(tmp_path)
    (main / "link").symlink_to(tmp_path)
    with project_scope(_hiding(main, pattern)):
        with pytest.raises(ValueError, match="review_policy.hidden"):
            review.hidden_paths()


@pytest.mark.skipif(not REAL_BWRAP, reason="needs the real bubblewrap")
def test_inside_a_real_jail_a_hidden_path_reads_as_empty(tmp_path, monkeypatch):
    main, _ = _worktree(tmp_path)
    (main / "scratch").mkdir()
    (main / "scratch" / "verdicts.md").write_text("A settled call.\n")
    (main / "notes.md").write_text("The operator's priors.\n")
    record = tmp_path / "record"
    with project_scope(_hiding(main, "scratch", "notes.md")):
        delta = review.resolve_range("HEAD")
        seen = review.preflight(delta, record, ENV)
        assert seen["hidden"] == ["scratch", "notes.md"]
        # Without the masks, the preflight is what notices.
        root = str(main)
        monkeypatch.setattr(
            review, "repository_binds", lambda: ["--ro-bind", root, root]
        )
        with pytest.raises(ValueError, match="hidden path is still readable"):
            review.preflight(delta, record, ENV)


CODEX = {
    "harness": "codex",
    "model": "demo-model",
    "effort": "high",
    "max_context_window": 400_000,
}


def test_a_jailed_codex_reviewer_leaves_confinement_to_the_jail(tmp_path):
    """Codex's own sandbox cannot start in the jail's read-only checkout.

    Codex 0.160.1 masks protected directories such as `.agents` and `.aws`
    in every writable root, a missing one needs a mount point created, and
    the checkout refuses the file. Every command and every write failed, the
    report included, and the turn still completed. Unjailed it starts, but
    must be told the report directory is writable, because the record store
    lies outside both the checkout and temp.
    """
    report = tmp_path / "report"
    extra = "sandbox_workspace_write.writable_roots=" + json.dumps([str(report)])
    for mode, session in (("run", None), ("resume", "s-1"), ("fork", "s-1")):
        jailed, _ = review.command(CODEX, mode, session, report)
        assert 'sandbox_mode="danger-full-access"' in jailed
        assert not any("writable_roots" in a for a in jailed)
        unjailed, _ = review.command(CODEX, mode, session, report, jailed=False)
        assert 'sandbox_mode="workspace-write"' in unjailed
        # As config, because a resume refuses `--add-dir` as it refuses `-s`.
        assert extra in unjailed and "--add-dir" not in unjailed
    run, _ = review.command(CODEX, "run", None, report)
    assert run[run.index("-s") + 1] == "danger-full-access"
    assert "-s" not in review.command(CODEX, "resume", "s-1", report)[0]


def _codex_turn(private, session, *contexts):
    """A turn's event stream, and the native session codex keeps beside it."""
    native = private / "sessions" / "2026" / "01" / "02"
    native.mkdir(parents=True)
    (native / f"rollout-2026-01-02T00-00-00-{session}.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "timestamp": at,
                    "type": "turn_context",
                    "payload": {
                        "model": "demo-model",
                        "effort": "high",
                        "sandbox_policy": {"type": kind},
                    },
                }
            )
            + "\n"
            for at, kind in contexts
        )
    )
    return [
        {"type": "thread.started", "thread_id": session},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}},
        {"type": "turn.completed", "usage": {}},
    ]


def test_a_jailed_codex_turn_proves_the_sandbox_it_was_served(tmp_path):
    # A run begun before the driver stopped asking for codex's own sandbox
    # carries that turn's context, and only the newest one is this turn's.
    private = tmp_path / "resumed"
    rows = _codex_turn(
        private,
        "s-1",
        ("2026-01-01T09:00:00.000Z", "workspace-write"),
        ("2026-01-02T09:00:00.000Z", "danger-full-access"),
    )
    receipt = review._answer("codex", CODEX, rows, private)
    assert receipt["sandbox"] == "danger-full-access"

    # A release that ignored the setting would complete the same silent way.
    ignored = tmp_path / "ignored"
    rows = _codex_turn(ignored, "s-2", ("2026-01-02T09:00:00.000Z", "workspace-write"))
    with pytest.raises(ValueError, match="served sandbox 'workspace-write'"):
        review._answer("codex", CODEX, rows, ignored)
