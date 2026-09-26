"""`book notes` as a command: `--check --since REF` and the `relations` alias."""

import subprocess

import pytest

from quirework.book import notes
from quirework.project import Project, project_scope

SPEC = """# What the notebook holds

## A kettle signals without thinking {#kettle-signal}

A whistle reports a state.
"""

MARKS = """# The opening {#sec-opening}

<!-- @bind(kettle-signal) -->
<!-- @guidance open on the whistle, as settled 2031-04-02 -->
"""


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def book(tmp_path, monkeypatch):
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "repo" / "book"
    sections = root / "manuscript" / "sections"
    (sections / "_marks").mkdir(parents=True)
    (root / "manuscript" / "_quarto.yml").write_text(
        "project:\n  type: book\nbook:\n  chapters:\n    - sections/opening.qmd\n"
    )
    (sections / "_spec.md").write_text(SPEC)
    (sections / "opening.qmd").write_text("# The opening {#sec-opening}\n\nA kettle.\n")
    (sections / "_marks" / "opening.md").write_text(MARKS)
    # A file the revision must be read without: `git archive` would drop it.
    (root.parent / ".gitattributes").write_text("*.md export-ignore\n")
    git(root.parent, "init", "-q")
    git(root.parent, "add", ".")
    git(
        root.parent,
        "-c",
        "user.name=A. Writer",
        "-c",
        "user.email=writer@example.invalid",
        "commit",
        "-qm",
        "A kettle book",
    )
    settings = {
        "book": {
            "source": "manuscript",
            "chapters": "manuscript/sections",
            "quarto": "manuscript/_quarto.yml",
            "spec": "manuscript/sections/_spec.md",
        }
    }
    with project_scope(Project.from_settings(root, settings)):
        yield sections


def test_only_what_the_change_introduced_is_listed(book, capsys):
    marks = book / "_marks" / "opening.md"
    # Moved down by an edit above it: the standing advisory is not new.
    marks.write_text(
        MARKS.replace(
            "<!-- @bind",
            "<!-- @todo say what a whistle costs -->\n<!-- @bind",
        )
        + "<!-- @guidance keep the lid, per the author's call -->\n"
    )
    assert notes.check(since="HEAD") == 0
    err = capsys.readouterr().err
    new = err[err.index("new since HEAD:") : err.index("standing")]
    assert "author's call" in new
    assert "2031-04-02" not in new
    assert "1 standing advisory line(s) the book already had at HEAD" in err


def test_nothing_new_says_so(book, capsys):
    assert notes.check(since="HEAD") == 0
    err = capsys.readouterr().err
    assert "no advisory new since HEAD." in err
    assert "1 standing advisory line(s)" in err


def test_a_second_identical_advisory_is_new():
    before = [("{n} journaling", ["  a.md:4: @guidance — '2031-04-02'"])]
    after = [
        (
            "{n} journaling",
            [
                "  a.md:5: @guidance — '2031-04-02'",
                "  a.md:9: @guidance — '2031-04-02'",
            ],
        )
    ]
    new, standing = notes.introduced(after, before)
    assert new == [("{n} journaling", ["  a.md:9: @guidance — '2031-04-02'"])]
    assert standing == 1


def test_a_revision_that_is_not_a_commit_is_refused(book, capsys):
    assert notes.main(["--check", "--since", "no-such-ref"]) == 2
    assert "not a commit in this repository" in capsys.readouterr().err


def test_since_without_check_is_refused(book, capsys):
    assert notes.main(["--since", "HEAD"]) == 2
    assert "needs --check" in capsys.readouterr().err


def test_relations_is_the_bind_filter_without_the_word(book, capsys):
    """A harness guard that refuses "bind" anywhere in argv cannot run it."""
    assert notes.main(["relations"]) == 0
    by_alias = capsys.readouterr().out
    assert notes.main(["bind"]) == 0
    assert by_alias == capsys.readouterr().out
    assert "kettle-signal" in by_alias


def test_a_book_s_own_kind_of_that_name_is_not_guessed_at(book, capsys):
    marks = book / "_marks" / "opening.md"
    marks.write_text(MARKS + "<!-- @relations the whistle and the lid -->\n")
    assert notes.main(["relations"]) == 2
    assert "name the one you mean with --kind" in capsys.readouterr().err
    assert notes.main(["--kind", "relations"]) == 0
    assert "the whistle and the lid" in capsys.readouterr().out
