"""`spec quotes --all`: the coverage the default scan trades for precision."""

import pytest

from quirework.manuscript import quotes
from quirework.project import Project, project_scope

CHAPTER = """\
# The fuse {#sec-fuse}

The fuse held until the second kettle went on.
Then the lights went out, and the fuses were checked one by one.
"""

MARKS = """\
# The fuse {#sec-fuse}

<!-- @guidance the chapter reads "The fuse held until the second kettle
     went on" and must keep saying so -->
<!-- @guidance the closing sentence is now "Then the breaker tripped",
     after the rewrite -->
<!-- @guidance keep the "breaker" out of the opening -->
<!-- @guidance the curly form “the lights went out” is quoted too -->
<!-- @source the electrician said "a fuse is a promise" -->
"""


@pytest.fixture
def book(tmp_path):
    source = tmp_path / "manuscript"
    (source / "_marks").mkdir(parents=True)
    (source / "fuse.qmd").write_text(CHAPTER)
    (source / "_marks" / "fuse.md").write_text(MARKS)
    with project_scope(
        Project.from_settings(tmp_path, {"manuscript": {"source": "manuscript"}})
    ):
        yield source


def test_the_default_scan_stays_narrow(book):
    """Uncued and short quotations are not its business."""
    assert quotes.scan() == []


def test_all_finds_the_uncued_and_the_short(book):
    found = {h.quote for h in quotes.scan_all()}
    # "now" is no cue, and the rewrite left the note behind.
    assert "Then the breaker tripped" in found
    # Short, and uncued; here it is the retired word being named.
    assert "breaker" in found
    # Everything outside the book is listed too; the author rules.
    assert "a fuse is a promise" in found
    # Present, straight or curly, cued or not: never listed.
    assert "the lights went out" not in found
    assert "The fuse held until the second kettle went on" not in found


def test_grep_keeps_the_spans_a_retired_term_appears_in(book):
    found = [h.quote for h in quotes.scan_all(grep=r"\bbreaker\b")]
    assert found == ["Then the breaker tripped", "breaker"]


def test_a_word_inside_a_longer_word_is_not_present():
    """A retired "fuse" does not survive in "fuses"."""
    assert not quotes._present("fuse", "the fuses were checked")
    assert quotes._present("fuse", "the fuse held")


def test_a_span_the_default_scan_reported_is_not_reported_twice(book):
    (book / "fuse.qmd").write_text(CHAPTER.replace("second kettle", "third kettle"))
    cued = quotes.scan()
    assert [h.quote for h in cued] == ["The fuse held until the second kettle went on"]
    assert cued[0].quote not in {h.quote for h in quotes.scan_all(skip=cued)}


def test_the_command_reports_both_groups_and_refuses_a_bad_pattern(book, capsys):
    assert quotes.main(["--grep", "breaker"]) == 0
    err = capsys.readouterr().err
    assert "every cited phrase resolves" in err
    assert "2 other quoted phrase(s) matching 'breaker' not found" in err
    with pytest.raises(SystemExit):
        quotes.main(["--grep", "("])
