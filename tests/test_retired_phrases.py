"""A retired phrase is a ruling already made, so `text check` gates on it."""

import pytest

from quirework.project import Project, project_scope
from quirework.text import jargon

POLICY = """\
scope_glob = [["manuscript", "*.qmd"]]
allow = [["history.qmd", "once called it", "the chapter about the rename"]]

[terms]
"utili[sz]e" = "use"

[retired]
"cold signal" = "the kettle's whistle"
"""


@pytest.fixture
def book(tmp_path):
    (tmp_path / "manuscript").mkdir()
    (tmp_path / "terms.toml").write_text(POLICY)
    settings = {"text": {"terminology": "terms.toml"}}
    with project_scope(Project.from_settings(tmp_path, settings)):
        yield tmp_path / "manuscript"


def check(book, name, text, *argv):
    (book / name).write_text(text)
    return jargon.main([str(book / name), *argv])


def test_every_occurrence_is_reported_across_a_line_break(book, capsys):
    text = "The Cold\nSignal came first.\n\nLater the cold signal again.\n"
    assert check(book, "one.qmd", text) == 1
    hits = jargon.retired(paths=[str(book / "one.qmd")])
    assert [(h.line, h.term) for h in hits] == [(1, "Cold Signal"), (4, "cold signal")]
    assert "gated" in capsys.readouterr().err


def test_what_excuses_a_term_does_not_excuse_a_retired_phrase(book):
    """A gloss, a quotation, a heading: the reader still meets the phrase."""
    text = (
        "# The cold signal\n\n"
        "The cold signal, the view that kettles think, is gone.\n\n"
        'She wrote "cold signal" on the lid.\n\n'
        "| a cold signal | row |\n"
    )
    (book / "two.qmd").write_text(text)
    assert [h.line for h in jargon.retired(paths=[str(book / "two.qmd")])] == [
        1,
        3,
        5,
        7,
    ]


def test_a_code_span_or_a_settled_keep_excuses_it(book):
    text = (
        "The slug `cold signal` names the file.\n\nWe once called it the cold signal.\n"
    )
    (book / "history.qmd").write_text(text)
    assert jargon.retired(paths=[str(book / "history.qmd")]) == []


def test_a_phrase_inside_longer_words_is_not_the_phrase(book):
    (book / "three.qmd").write_text("A scold signals nothing.\n")
    assert jargon.retired(paths=[str(book / "three.qmd")]) == []


def test_bare_jargon_alone_still_only_warns(book, capsys):
    assert check(book, "four.qmd", "They utilise the kettle.\n") == 0
    assert "✓ retired" in capsys.readouterr().out
    assert check(book, "four.qmd", "They utilise the kettle.\n", "--check") == 1


def test_the_list_names_the_retired_phrases(book, capsys):
    assert jargon.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "retired (gated):" in out
    assert "cold signal" in out and "the kettle's whistle" in out
