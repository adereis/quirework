"""Rendering a book's derived editions through the book's own pipeline.

A fictional book about a lighthouse keeper's ledger, with three editions:
`verse`, a book of its own; `memo`, one plain document; and `children`,
which has blind drafts but nothing composed yet. Quarto itself is replaced
by a writer that records where it ran and emits one artifact per declared
format, so these cover staging, selection and consolidation, not Quarto.
"""

import json
import re

import pytest

from quirework.manuscript import quarto as _quarto
from quirework.project import Project, project_scope
from quirework.publish import editions, render

_BOOK = """\
project:
  type: book
book:
  title: "The Ledger"
  chapters:
    - index.qmd
    - canon/tides.qmd
format:
  html:
    theme: cosmo
  typst:
    papersize: a5
"""

_VERSE = """\
project:
  type: book
book:
  title: "The Ledger in Verse"
  chapters:
    - index.qmd
    - tides.qmd
format:
  html:
    theme: cosmo
"""

_MEMO = """\
project:
  type: default
  render:
    - memo.qmd
format:
  html:
    theme: cosmo
  typst:
    papersize: a4
"""

_PLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
    '<rect width="10" height="10" fill="#000"/></svg>\n'
)


def _format_keys(text):
    block = re.search(r"(?ms)^format:\n(.*?)(?=^\S|\Z)", text)
    return re.findall(r"(?m)^  ([A-Za-z][\w-]*):", block.group(1)) if block else []


@pytest.fixture
def book(tmp_path, monkeypatch):
    root = tmp_path / "ledger"
    docs = root / "docs"
    (docs / "canon" / "illustrations").mkdir(parents=True)
    (docs / "_quarto.yml").write_text(_BOOK)
    (docs / "index.qmd").write_text("# The Ledger\n")
    (docs / "canon" / "tides.qmd").write_text(
        "# Tides\n\nThe keeper wrote down every tide.\n\n"
        "![The ledger's first page](illustrations/plate.svg){#fig-plate}\n"
    )
    (docs / "canon" / "illustrations" / "plate.svg").write_text(_PLATE)

    verse = root / "variants" / "verse"
    (verse / "_raw").mkdir(parents=True)
    (verse / "_marks").mkdir()
    (verse / "_quarto.yml").write_text(_VERSE)
    (verse / "index.qmd").write_text("# The Ledger in Verse\n")
    (verse / "tides.qmd").write_text(
        "# Tides\n\nHe wrote the tide,\\\nand the tide wrote back.\n\n"
        "![The ledger's first page](illustrations/plate.svg){#fig-plate}\n"
    )
    (verse / "_raw" / "tides.md").write_text("# Tides\n\nA raw draft.\n")
    (verse / "_marks" / "tides.md").write_text("# Tides\n\n<!-- @todo scan -->\n")

    memo = root / "variants" / "memo"
    memo.mkdir()
    (memo / "_quarto.yml").write_text(_MEMO)
    (memo / "memo.qmd").write_text("# The Ledger, briefly\n\n## Why\n\nTides.\n")

    children = root / "variants" / "children" / "_raw"
    children.mkdir(parents=True)
    (children / "tides.md").write_text("# Tides\n\nA raw draft.\n")

    project = Project.from_settings(
        root,
        {
            "manuscript": {
                "source": "docs",
                "chapters": "docs/canon",
                "quarto": "docs/_quarto.yml",
            },
            "publish": {
                "build": "build",
                "variants": "variants",
                "figures": "docs/canon/illustrations",
            },
        },
    )
    from quirework.publish import fonts

    monkeypatch.setattr(render, "find_quarto", lambda: "quarto")
    monkeypatch.setattr(fonts, "missing", lambda formats=None: [])
    monkeypatch.setitem(
        render._fignumbers._READERS,
        "typst",
        (".pdf", lambda path: json.loads(path.read_text())),
    )
    calls = []

    def quarto(args, *, cwd):
        """Write one artifact per declared format, numbering each figure."""
        calls.append(cwd)
        site = cwd / "_site"
        site.mkdir(exist_ok=True)
        captions = [
            m.group("cap")
            for rel in _quarto.documents(cwd)
            for m in render._figures.EMBED.finditer((cwd / rel).read_text())
            if "#fig-" in (m.group("attrs") or "")
        ]
        pairs = [(str(n), cap) for n, cap in enumerate(captions, 1)]
        for fmt in _format_keys((cwd / "_quarto.yml").read_text()):
            if fmt == "html":
                (site / "index.html").write_text(
                    "".join(
                        f"<figcaption>Figure {n}: {cap}</figcaption>"
                        for n, cap in pairs
                    )
                )
            elif fmt == "typst":
                (site / "out.pdf").write_text(
                    json.dumps(
                        [(n, render._fignumbers.caption_key(c)) for n, c in pairs]
                    )
                )
        return 0

    monkeypatch.setattr(render.subprocess, "call", quarto)
    with project_scope(project):
        yield root, calls


def _tree(folder):
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


def test_a_build_renders_the_book_then_every_composed_edition(book, capsys):
    root, calls = book
    sources = _tree(root / "variants")

    assert render.main([]) == 0

    build = root / "build"
    assert calls == [
        build / "book",
        build / "book-print",
        build / "variants" / "memo" / "book",
        build / "variants" / "memo" / "book-print",
        build / "variants" / "verse" / "book",
    ]
    # Quarto never ran in an edition's directory, so nothing appeared there.
    assert _tree(root / "variants") == sources
    out = capsys.readouterr().out
    assert "the verse edition is in" in out and "children" not in out
    # The print stage's PDF is consolidated beside the screen output.
    assert (build / "variants" / "memo" / "book" / "_site" / "out.pdf").is_file()


def test_an_edition_stages_its_documents_and_the_books_figures(book):
    root, _ = book
    assert render.main(["--edition", "verse"]) == 0

    stage = root / "build" / "variants" / "verse" / "book"
    # Its blind drafts are evidence and its marks are metadata: neither is
    # a source, so neither reaches the tree Quarto reads.
    assert not (stage / editions.RAW).exists()
    assert not (stage / "_marks").exists()
    assert (stage / "tides.qmd").is_file()
    # The figure comes from the book's gallery, not from a copy in the edition.
    assert (stage / "illustrations" / "plate.svg").is_file()


def test_editions_are_chosen_by_name_or_left_out(book):
    root, calls = book
    build = root / "build"

    assert render.main(["--edition", "memo,verse"]) == 0
    assert build / "book" not in calls
    assert {c.parent.name for c in calls} == {"memo", "verse"}

    calls.clear()
    assert render.main(["--no-editions"]) == 0
    assert calls == [build / "book", build / "book-print"]


def test_an_edition_with_nothing_composed_cannot_be_rendered(book, capsys):
    assert render.main(["--edition", "children"]) == 2
    err = capsys.readouterr().err
    assert "no composed edition children" in err
    assert "_quarto.yml" in err and "memo, verse" in err


def test_selection_refuses_a_contradiction_and_a_missing_kind(book):
    with pytest.raises(ValueError, match="contradict"):
        render.select(["--edition", "memo", "--no-editions"])
    with pytest.raises(ValueError, match="needs a kind"):
        render.select(["--edition"])
    assert render.select(["--cache-refresh"]) == (
        True,
        ["memo", "verse"],
        ["--cache-refresh"],
    )


def test_an_edition_renders_only_the_formats_it_declares(book, capsys):
    root, calls = book
    build = root / "build"

    assert render.main(["--to", "typst"]) == 0

    assert calls == [build / "book-print", build / "variants" / "memo" / "book-print"]
    assert "the verse edition declares none of the formats" in capsys.readouterr().out


def test_a_project_without_editions_renders_the_book_alone(book):
    root, calls = book
    from quirework.project import current_project

    bare = current_project().replaced({"publish.variants": str(root / "none")})
    with project_scope(bare):
        assert editions.composed() == []
        assert render.main([]) == 0
    assert calls == [root / "build" / "book", root / "build" / "book-print"]


def test_the_documents_of_a_plain_project_and_of_a_book(tmp_path):
    (tmp_path / "_quarto.yml").write_text(_MEMO)
    assert not _quarto.is_book(tmp_path)
    assert _quarto.documents(tmp_path) == ["memo.qmd"]

    # With no render list, Quarto renders every input outside `_` and `.`.
    (tmp_path / "_quarto.yml").write_text("project:\n  type: default\n")
    for rel in ("memo.qmd", "notes/aside.md", "_raw/draft.md", "README.md"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("# A page\n")
    assert _quarto.documents(tmp_path) == ["memo.qmd", "notes/aside.md"]

    (tmp_path / "_quarto.yml").write_text(_VERSE)
    assert _quarto.is_book(tmp_path)
    # Declaring a book is enough to be one, so a missing `book:` block fails
    # as a broken book instead of passing as a plain project.
    (tmp_path / "_quarto.yml").write_text("project:\n  type: book\n")
    assert _quarto.is_book(tmp_path)
    (tmp_path / "_quarto.yml").write_text(_VERSE)
    assert _quarto.documents(tmp_path) == ["index.qmd", "tides.qmd"]


def test_a_plain_project_has_no_parts_to_flatten_and_no_pages_to_blank(tmp_path):
    from quirework.publish import bookstage

    (tmp_path / "_quarto.yml").write_text(_MEMO)
    (tmp_path / "memo.qmd").write_text("# Memo\n")
    assert bookstage.flatten_book_parts(tmp_path) == ([], [])
    assert bookstage.blank_chapters(tmp_path, {"memo.qmd"}) == []
    assert (tmp_path / "_quarto.yml").read_text() == _MEMO
    assert (tmp_path / "memo.qmd").read_text() == "# Memo\n"
