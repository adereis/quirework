"""A derived edition's documents bind the spec as the canon's chapters do.

A fictional book about a harbour pilot, with two chapters, a spec of three
sections and two editions: `children`, a book of its own, and `memo`, one
document. Each edition's sidecars sit in its own `_marks/`, and its units
are named `KIND/STEM`, so the children's `tides` is never the canon's.
"""

import pytest

from quirework.manuscript import editions, notes, rename, sources, units
from quirework.project import Project, project_scope

SPEC = """\
# What the book holds

## The pilot reads the water {#reads-water}

Stated.

## Tides are kept in a ledger {#tide-ledger}

Stated.

## Nobody drowns {#nobody-drowns}

Stated.
"""


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def book(tmp_path):
    root = tmp_path / "harbour"
    docs = root / "docs"
    _write(
        docs / "_quarto.yml",
        "project:\n  type: book\nbook:\n  chapters:\n"
        "    - canon/tides.qmd\n    - canon/storm.qmd\n",
    )
    _write(docs / "canon" / "_spec.md", SPEC)
    _write(docs / "canon" / "tides.qmd", "# Tides {#sec-tides}\n\nThe ledger.\n")
    _write(
        docs / "canon" / "_marks" / "tides.md",
        "# Tides {#sec-tides}\n\n<!-- @bind(reads-water) -->\n"
        "<!-- @bind(tide-ledger) -->\n",
    )
    _write(docs / "canon" / "storm.qmd", "# Storm {#sec-storm}\n\nNobody drowns.\n")
    _write(
        docs / "canon" / "_marks" / "storm.md",
        "# Storm {#sec-storm}\n\n<!-- @bind(nobody-drowns) -->\n",
    )

    children = root / "variants" / "children"
    _write(
        children / "_quarto.yml",
        "project:\n  type: book\nbook:\n  chapters:\n    - tides.qmd\n",
    )
    _write(children / "tides.qmd", "# Tides {#sec-tides}\n\nA big book of tides.\n")
    _write(
        children / "_marks" / "tides.md",
        "# Tides {#sec-tides}\n\n<!-- @bind(tide-ledger) -->\n",
    )
    _write(children / "_raw" / "tides.md", "# Tides\n\n<!-- @bind(raw-draft) -->\n")

    memo = root / "variants" / "memo"
    _write(
        memo / "_quarto.yml",
        "project:\n  type: default\n  render:\n    - memo.qmd\n",
    )
    _write(memo / "memo.qmd", "# The harbour, briefly\n\n## Water\n\nRead.\n")
    _write(
        memo / "_marks" / "memo.md",
        "## Water\n\n<!-- @bind(reads-water) -->\n<!-- @bind(tide-ledger) -->\n"
        "<!-- @omit(nobody-drowns) The memo is about the ledger, not the storm. -->\n",
    )

    project = Project.from_settings(
        root,
        {
            "manuscript": {
                "source": "docs",
                "chapters": "docs/canon",
                "quarto": "docs/_quarto.yml",
                "spec": "docs/canon/_spec.md",
            },
            "publish": {"variants": "variants"},
        },
    )
    with project_scope(project):
        yield root


def _problems(capsys):
    rc = notes.check()
    return rc, capsys.readouterr().err


def test_one_commitment_is_listed_across_the_canon_and_every_edition(book, capsys):
    assert notes.main(["bind", "--slug", "tide-ledger"]) == 0
    out = capsys.readouterr().out
    rows = [line.split(":")[0].strip() for line in out.splitlines() if "_marks" in line]
    assert rows == [
        "docs/canon/_marks/tides.md",
        "variants/children/_marks/tides.md",
        "variants/memo/_marks/memo.md",
    ]


def test_an_editions_units_are_its_own_and_not_the_canons(book):
    # The canon's map from chapter to spec hears nothing of the editions,
    # though the children's edition has a document called `tides` too.
    assert notes.chapter_bindings() == {
        "tides": {"reads-water", "tide-ledger"},
        "storm": {"nobody-drowns"},
    }
    assert notes.edition_bindings() == {
        "children": {"bind": {"tide-ledger"}, "omit": set()},
        "memo": {"bind": {"reads-water", "tide-ledger"}, "omit": {"nobody-drowns"}},
    }
    # A blind draft is evidence, not a sidecar, so its text binds nothing.
    assert not any("raw-draft" in a.args for a in notes.scan())
    by_unit = {notes.unit_name(a) for a in notes.scan() if a.kind == "bind"}
    assert by_unit == {"tides", "storm", "children/tides", "memo/memo"}


def test_a_section_an_edition_neither_carries_nor_omits_is_named(book, capsys):
    rc, err = _problems(capsys)
    assert rc == 0
    assert "variants/children: nobody-drowns" in err
    assert "variants/children: reads-water" in err
    assert "variants/memo:" not in err


def test_an_omission_belongs_to_an_edition_and_says_why(book, capsys):
    memo = book / "variants" / "memo" / "_marks" / "memo.md"
    memo.write_text(
        "## Water\n\n<!-- @bind(reads-water) -->\n<!-- @bind(tide-ledger) -->\n"
        "<!-- @omit(nobody-drowns) -->\n<!-- @omit(no-such-section) Why. -->\n"
        "<!-- @omit(reads-water) Both at once. -->\n"
    )
    canon = book / "docs" / "canon" / "_marks" / "storm.md"
    canon.write_text(
        "# Storm {#sec-storm}\n\n<!-- @bind(nobody-drowns) -->\n"
        "<!-- @omit(tide-ledger) The storm chapter has no ledger. -->\n"
    )
    rc, err = _problems(capsys)
    assert rc == 1
    assert "@omit('nobody-drowns') gives no reason" in err
    assert "@omit('no-such-section') names no section" in err
    assert "variants/memo: binds and omits 'reads-water'" in err
    assert "docs/canon/_marks/storm.md:4: @omit belongs to a derived edition" in err


def test_an_editions_sidecars_are_held_to_the_canons_rules(book, capsys):
    children = book / "variants" / "children"
    (children / "_marks" / "tides.md").write_text(
        "# Tides {#sec-tides}\n\n<!-- @bind(tide-ledgr) -->\n\n## Neap\n"
    )
    (children / "_marks" / "gone.md").write_text("# Gone\n")
    (children / "tides.qmd").write_text(
        "# Tides {#sec-tides}\n\n<!-- @todo inline -->\nA big book.\n"
    )
    rc, err = _problems(capsys)
    assert rc == 1
    assert "variants/children/_marks/tides.md:3: @bind('tide-ledgr') binds no" in err
    assert "variants/children/_marks/tides.md: heading 'Neap'" in err
    assert "variants/children/_marks/gone.md: orphan sidecar" in err
    assert "variants/children/tides.qmd:3: @todo mark left in prose" in err
    assert "variants/children/_marks/tides.md" in err


def test_a_rename_reaches_an_editions_bindings_and_omissions(book, capsys):
    rename.apply(rename.plan("nobody-drowns", "all-hands-saved")[1])
    memo = (book / "variants" / "memo" / "_marks" / "memo.md").read_text()
    assert "@omit(all-hands-saved) The memo is about the ledger" in memo
    rename.apply(rename.plan("tide-ledger", "tide-book")[1])
    children = (book / "variants" / "children" / "_marks" / "tides.md").read_text()
    assert "@bind(tide-book)" in children
    assert notes.check() == 0
    capsys.readouterr()


def test_counts_and_citations_keep_an_edition_to_itself(book):
    memo = book / "variants" / "memo" / "_marks" / "memo.md"
    memo.write_text(
        memo.read_text() + "<!-- @count(two chapters) the canon's -->\n"
        "<!-- @source(ledger) the harbour ledger -->\n"
    )
    # An edition's @count exempts nothing in the canon it is not part of.
    assert units.count_marks() == {}
    assert sources.cited_by().get("ledger") == ["memo/memo"]
    assert editions.unit_of(memo) == "memo/memo"
    assert editions.unit_of(book / "variants" / "memo" / "memo.qmd") == "memo/memo"
    assert editions.unit_of(book / "variants" / "memo" / "_raw" / "memo.md") is None
    assert editions.unit_of(book / "docs" / "canon" / "tides.qmd") is None
