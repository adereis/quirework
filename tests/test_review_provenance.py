"""A reviewer can be given the provenance store, read-only; a synthetic book.

The jail blanks `$HOME`, which takes the store and the per-user file naming it
out of view. A review that asks for the store gets exactly that store bound
back, read-only, and the preflight proves it reads before a model is paid for.
Every repository and store here is fiction written for the purpose.
"""

import shutil
import subprocess
import sys

import pytest

from quirework.project import Project, project_scope
from quirework.provenance import store
from quirework.sops import review

ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "PATH": "/usr/bin:/bin",
}

REAL_BWRAP = sys.platform.startswith("linux") and shutil.which("bwrap")

CODEX = {
    "harness": "codex",
    "model": "demo-model",
    "effort": "high",
    "max_context_window": 400_000,
}


def _repo(tmp_path):
    """A checkout with one commit about a fictional kettle."""
    repo = tmp_path / "book"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "kettle.md").write_text("A fictional kettle that whistles.\n")
    for args in (("add", "."), ("commit", "-qm", "Add the kettle")):
        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            env={**ENV, "HOME": str(repo)},
        )
    return repo


def _named(repo, tmp_path, monkeypatch, **extra):
    """A project whose store is named under a home outside the checkout."""
    monkeypatch.setenv("QUIREWORK_PROVENANCE_HOME", str(tmp_path / "stores"))
    settings = {"provenance": {"enabled": True, "store": "kettle-book"}, **extra}
    return Project.from_settings(repo, settings)


def test_a_review_binds_no_store_unless_it_asked():
    assert review.provenance_binds(None) == []
    assert review.provenance_binds(CODEX) == []


def test_a_named_store_is_bound_with_the_home_that_finds_it(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    with project_scope(_named(repo, tmp_path, monkeypatch)):
        store.init()
        bound = review.provenance_store()
        root = str((tmp_path / "stores" / "kettle-book").resolve())
        assert bound == {"store": root, "home": str((tmp_path / "stores").resolve())}
        # Only this store is bound; the home is named so it can be found, and
        # any other store kept under it stays out of view.
        assert review.provenance_binds({**CODEX, "provenance": bound}) == [
            "--ro-bind",
            root,
            root,
            "--setenv",
            "QUIREWORK_PROVENANCE_HOME",
            bound["home"],
        ]


def test_a_store_named_by_directory_needs_no_home(tmp_path):
    repo = _repo(tmp_path)
    settings = {"provenance": {"enabled": True, "store_dir": str(tmp_path / "s")}}
    with project_scope(Project.from_settings(repo, settings)):
        store.init()
        bound = review.provenance_store()
        assert bound == {"store": str((tmp_path / "s").resolve())}
        argv = review.provenance_binds({**CODEX, "provenance": bound})
        assert "--setenv" not in argv


def test_a_missing_store_is_refused_before_a_run_opens(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    with project_scope(_named(repo, tmp_path, monkeypatch)):
        with pytest.raises(ValueError, match="--provenance: there is no provenance"):
            review.provenance_store()


def test_a_store_gone_since_the_run_opened_is_refused(tmp_path):
    gone = {"store": str(tmp_path / "nowhere"), "home": str(tmp_path)}
    with pytest.raises(ValueError, match="store this run binds is gone"):
        review.provenance_binds({**CODEX, "provenance": gone})


def test_a_store_the_review_policy_hides_is_refused(tmp_path):
    repo = _repo(tmp_path)
    (repo / "review.toml").write_text('hidden = ["private"]\n')
    settings = {
        "provenance": {"enabled": True, "store_dir": "private/store"},
        "sops": {"review_policy": "review.toml"},
    }
    with project_scope(Project.from_settings(repo, settings)):
        store.init()
        with pytest.raises(ValueError, match="review_policy.hidden keeps"):
            review.provenance_store()


def test_the_launch_line_says_what_the_reviewer_reaches():
    bound = {**CODEX, "provenance": {"store": "/stores/kettle-book"}}
    assert "provenance=read-only" in review.launch_summary(bound, True)
    # A record from before the field existed bound no store.
    assert "provenance=none" in review.launch_summary(CODEX, True)
    # Unjailed, the reviewer stands in the operator's home, store included.
    assert "provenance=unjailed" in review.launch_summary(CODEX, False)


@pytest.mark.skipif(not REAL_BWRAP, reason="needs the real bubblewrap")
def test_inside_a_real_jail_the_store_reads_as_the_hosts(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    record = tmp_path / "record"
    with project_scope(_named(repo, tmp_path, monkeypatch)):
        store.init()
        config = {**CODEX, "provenance": review.provenance_store()}
        delta = review.resolve_range("HEAD")
        seen = review.preflight(delta, record, ENV, config)
        assert seen["provenance"] == config["provenance"]["store"]
        # Without the bind the jail cannot see the store, and the preflight is
        # what notices, before anything is launched.
        monkeypatch.setattr(review, "provenance_binds", lambda config: [])
        with pytest.raises(ValueError, match="provenance store does not read"):
            review.preflight(delta, record, ENV, config)
