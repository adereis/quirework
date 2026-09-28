"""The Quarto format's unit registry: reading order, numbering, and the
correspondence between `_quarto.yml` and the files on disk.

Reading order lives only in `_quarto.yml`, and a unit's printed number is
derived from its position there. Each case builds a minimal source tree
rather than reading a real book, so a change to one book's content cannot
break what these pin: the mechanism.
"""

from quirework.manuscript import units


def _source(tmp_path, config, *files):
    """A minimal source tree: a `_quarto.yml` and the unit files it names."""
    docs = tmp_path / "docs"
    (docs / "sections").mkdir(parents=True)
    (docs / "_quarto.yml").write_text(config)
    for name, body in files:
        (docs / name).write_text(body)
    return docs


def test_order_comes_from_the_config_not_the_filesystem(tmp_path):
    """The configured order is the exact reverse of the alphabetical one: a
    registry that fell back to a directory listing would come out backwards,
    not merely differently, so this cannot pass by coincidence."""
    docs = _source(
        tmp_path,
        "book:\n  chapters:\n"
        "    - sections/c.qmd\n    - sections/b.qmd\n    - sections/a.qmd\n",
        ("sections/a.qmd", "# A\n"),
        ("sections/b.qmd", "# B\n"),
        ("sections/c.qmd", "# C\n"),
    )
    assert [u.slug for u in units.registry(docs)] == ["c", "b", "a"]


def test_a_string_part_divider_contributes_no_unit(tmp_path):
    """A bare title string names no file, so it never enters the registry and
    cannot absorb a number."""
    docs = _source(
        tmp_path,
        "book:\n  chapters:\n    - sections/a.qmd\n"
        '    - part: "Part Two: Further out"\n'
        "      chapters:\n        - sections/b.qmd\n",
        ("sections/a.qmd", "# A\n"),
        ("sections/b.qmd", "# B\n"),
    )
    registry = units.registry(docs)
    assert [u.slug for u in registry] == ["a", "b"]
    assert [u.number for u in registry] == [1, 2]


def test_a_file_based_part_divider_takes_no_number(tmp_path):
    """A file-based part opener is a divider, not a numbered unit, so the
    units on either side of it are numbered as if it were not there."""
    docs = _source(
        tmp_path,
        "book:\n  chapters:\n    - sections/a.qmd\n"
        "    - part: sections/opener.qmd\n"
        "      chapters:\n        - sections/b.qmd\n",
        ("sections/a.qmd", "# A\n"),
        ("sections/opener.qmd", "# Opener\n"),
        ("sections/b.qmd", "# B\n"),
    )
    by = {u.slug: u for u in units.registry(docs)}
    assert by["opener"].number is None, "a divider is not a numbered unit"
    assert (by["a"].number, by["b"].number) == (1, 2)


def test_an_unnumbered_h1_takes_no_number(tmp_path):
    docs = _source(
        tmp_path,
        "book:\n  chapters:\n    - sections/a.qmd\n    - sections/b.qmd\n",
        ("sections/a.qmd", "# A {.unnumbered}\n"),
        ("sections/b.qmd", "# B\n"),
    )
    by = {u.slug: u for u in units.registry(docs)}
    assert by["a"].number is None and by["b"].number == 1


def test_the_landing_page_is_never_numbered(tmp_path):
    """`index.qmd` is the website landing and carries no `.unnumbered`; it is
    excluded by name, which is the one place a filename is load-bearing."""
    docs = _source(
        tmp_path,
        "book:\n  chapters:\n    - index.qmd\n    - sections/a.qmd\n",
        ("index.qmd", "# Welcome\n"),
        ("sections/a.qmd", "# A\n"),
    )
    by = {u.slug: u for u in units.registry(docs)}
    assert by["index"].number is None and by["a"].number == 1


def test_a_file_missing_from_the_config_is_reported(tmp_path):
    docs = _source(
        tmp_path,
        "book:\n  chapters:\n    - sections/a.qmd\n",
        ("sections/a.qmd", "# A\n"),
        ("sections/b.qmd", "# B\n"),
    )
    problems = units.order_problems(docs)
    assert len(problems) == 1 and "sections/b.qmd" in problems[0]


def test_a_config_entry_with_no_file_is_reported(tmp_path):
    docs = _source(tmp_path, "book:\n  chapters:\n    - sections/gone.qmd\n")
    problems = units.order_problems(docs)
    assert len(problems) == 1 and "gone.qmd" in problems[0]


def test_sidecars_are_not_expected_in_the_config(tmp_path):
    """`_marks/` is metadata, never a unit; it renders nowhere by design."""
    docs = _source(
        tmp_path,
        "book:\n  chapters:\n    - sections/a.qmd\n",
        ("sections/a.qmd", "# A\n"),
    )
    (docs / "sections" / "_marks").mkdir()
    (docs / "sections" / "_marks" / "a.md").write_text("# A\n")
    assert units.order_problems(docs) == []
