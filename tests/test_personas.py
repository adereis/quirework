"""Revision checks for a project that is not the whole git checkout.

`sop personas check --base REV` compares each profile against the same file at
an earlier commit and refuses a changed profile that kept its revision. It
finds the earlier file through git, which names paths from the repository
root, while the project names them from the project root. When a book is the
whole checkout those are the same string and nothing notices. When it sits in
a subdirectory they never match, and a comparison that matches nothing reports
nothing: the check passes by skipping every profile it was asked to check.
"""

import json
import subprocess

import pytest

from quirework.project import Project, project_scope
from quirework.sops import personas


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _profile(**overrides):
    data = {
        "schema_version": 1,
        "id": "reader",
        "revision": 1,
        "label": "An ordinary reader",
        "description": "A fictional test perspective",
        "tags": ["reader"],
        "prompt": "You are reading for pleasure. Say what is clear.",
    }
    data.update(overrides)
    return json.dumps(data, indent=2) + "\n"


def _repository(tmp_path, book_at):
    repo = tmp_path / "repo"
    book = repo / book_at if book_at else repo
    (book / "profiles").mkdir(parents=True)
    (book / "profiles" / "reader.json").write_text(_profile())
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "nobody@example.invalid")
    _git(repo, "config", "user.name", "A Fixture")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the first profile")
    return book


def _project(book):
    return Project.from_settings(book, {"sops": {"personas": "profiles"}})


@pytest.mark.parametrize("book_at", ["", "examples/demo-book"])
def test_a_changed_profile_without_a_new_revision_is_refused(tmp_path, book_at):
    book = _repository(tmp_path, book_at)
    (book / "profiles" / "reader.json").write_text(
        _profile(prompt="You are reading for spite. Say what is unclear.")
    )
    with project_scope(_project(book)):
        with pytest.raises(ValueError, match="needs a new revision"):
            personas.check(root=book, base="HEAD")


@pytest.mark.parametrize("book_at", ["", "examples/demo-book"])
def test_a_changed_profile_with_a_new_revision_passes(tmp_path, book_at):
    book = _repository(tmp_path, book_at)
    (book / "profiles" / "reader.json").write_text(
        _profile(revision=2, prompt="You are reading for spite.")
    )
    with project_scope(_project(book)):
        assert [p["profile"]["revision"] for p in personas.check(root=book, base="HEAD")] == [2]


@pytest.mark.parametrize("book_at", ["", "examples/demo-book"])
def test_an_unchanged_profile_passes(tmp_path, book_at):
    book = _repository(tmp_path, book_at)
    with project_scope(_project(book)):
        assert [p["profile"]["id"] for p in personas.check(root=book, base="HEAD")] == [
            "reader"
        ]
