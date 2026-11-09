"""Which spec sections a range of commits changed.

The book is the invented kettle notebook the trail tests use, committed in a
repository of the test's own, so each assertion reads a real git history.
"""

import pytest

from quirework.manuscript import specdiff
from quirework.project import project_scope

from test_trail import SPEC, _book, _git


@pytest.fixture
def history(tmp_path, monkeypatch):
    """The notebook's spec through four commits: drafted, reworded, a section
    renamed with a pointer to it, and a rewrap that changes nothing."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    project = _book(tmp_path, provenance=False)
    root, spec = project.root, project.path("manuscript.spec")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "manuscript")
    _git(root, "commit", "-q", "-m", "Draft the notebook")
    spec.write_text(SPEC.replace("cools by another.", "cools by another route."))
    _git(root, "commit", "-q", "-am", "Say cooling is a route")
    text = spec.read_text().replace("{#kettle-signal}", "{#whistle-signal}")
    text = text.replace("by another route.", "by another route (#whistle-signal).")
    spec.write_text(text)
    _git(root, "commit", "-q", "-am", "Rename the signal section")
    renamed = spec.read_text()
    pointed = renamed.replace(" (#whistle-signal)", " (#kettle-signal)")
    spec.write_text(pointed.replace("Reporting a state", "Reporting\na state"))
    _git(root, "commit", "-q", "-am", "Point at the old name and rewrap")
    with project_scope(project):
        yield project


def _states(changes):
    return {(c.state, c.slug) for c in changes}


def test_a_range_lists_the_sections_it_changed(history):
    assert _states(specdiff.diff("HEAD~3..HEAD~2")) == {
        ("changed", "cooling-asymmetry")
    }


def test_a_rename_is_a_new_section_a_gone_one_and_pointer_changes(history):
    assert _states(specdiff.diff("HEAD~2..HEAD~1")) == {
        ("new", "whistle-signal"),
        ("gone", "kettle-signal"),
        ("changed", "cooling-asymmetry"),
    }


def test_a_change_to_pointers_alone_is_reported_apart(history):
    # The last commit points at another slug and rewraps a line: the rewrap is
    # no change, and the pointer is bookkeeping, not a change to the claim.
    assert _states(specdiff.diff("HEAD~1")) == {("pointers", "cooling-asymmetry")}


def test_by_commit_names_each_commit_and_what_it_changed(history):
    commits = specdiff.by_commit("HEAD~3..HEAD")
    assert [c.subject for c in commits] == [
        "Say cooling is a route",
        "Rename the signal section",
        "Point at the old name and rewrap",
    ]
    assert _states(commits[-1].changes) == {("pointers", "cooling-asymmetry")}


def test_the_command_prints_one_row_a_section(history, capsys):
    assert specdiff.main(["HEAD~3..HEAD~2"]) == 0
    assert capsys.readouterr().out == (
        "  changed   #cooling-asymmetry — Cooling is not the reverse of boiling\n"
    )


def test_a_range_without_a_start_is_refused(history, capsys):
    assert specdiff.main(["..HEAD"]) == 2
    assert "names no starting commit" in capsys.readouterr().err


def test_an_unknown_commit_is_refused_not_read_as_empty(history, capsys):
    assert specdiff.main(["nosuchref..HEAD"]) == 2
    assert "'nosuchref' names no commit" in capsys.readouterr().err
