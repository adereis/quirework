"""Proofs: a copy made before a book is published says so everywhere.

The book here is fictional and has a draft-copy switch: until it is the
boolean false, every render is a proof. The renders are stood in for, so the
tests need neither Quarto nor a real typesetter; the PDFs, EPUBs and DOCX
files the checks read are written by hand.
"""

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from quirework.project import Project, project_scope
from quirework.publish import bookstage, proof, render

_BOOK = """\
project:
  type: book
draft-copy: true
book:
  title: "A Kettle Book"
  cover-image: cover.jpg
  chapters:
    - index.qmd
"""

_POLICY = 'revision_metadata = true\nproof_switch = "draft-copy"\n'


def project_at(root):
    return Project.from_settings(
        root,
        {
            "manuscript": {
                "source": "docs",
                "chapters": "docs/canon",
                "quarto": "docs/_quarto.yml",
            },
            "publish": {"render_policy": "render.toml"},
        },
    )


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def book(tmp_path, monkeypatch):
    """A fictional book under git, at `book/` so that `logs/` is outside it."""
    root = tmp_path / "book"
    docs = root / "docs"
    docs.mkdir(parents=True)
    empty = tmp_path / "gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Ada Kettle")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "ada@example.org")
    (docs / "_quarto.yml").write_text(_BOOK, encoding="utf-8")
    (docs / "index.qmd").write_text("# Welcome\n\nA kettle boils.\n")
    (root / "render.toml").write_text(_POLICY)
    (root / ".gitignore").write_text("build/\n")
    project = project_at(root)
    monkeypatch.setattr(render._figures, "build", lambda stage=None, gray=False: 0)
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "A fictional first draft")
    with project_scope(project):
        yield root


def _short(root):
    return _git(root, "log", "-1", "--format=%h")


def _date(root):
    return _git(root, "log", "-1", "--format=%cs")


# --------------------------------------------------------------------------- #
# reading and setting a stage's configuration
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("false", False),
        ("FALSE", False),
        ("true  # a note", True),
        ('"false"', "false"),
        ("'it''s'", "it's"),
        ("plain words", "plain words"),
        ("", None),
        ("no", "no"),  # YAML 1.1's boolean, a string to Quarto's core schema
    ],
)
def test_a_scalar_reads_as_quarto_reads_it(text, value):
    assert bookstage.yaml_scalar(text) == value


def test_a_book_option_is_read_set_and_replaced(tmp_path):
    (tmp_path / "_quarto.yml").write_text(
        'book:\n  title: "T"\n  output-file: old\n  chapters:\n    - index.qmd\n'
        "format:\n  html: default\n"
    )
    assert bookstage.book_option(tmp_path, "title") == "T"
    assert bookstage.book_option(tmp_path, "output-file") == "old"
    assert bookstage.book_option(tmp_path, "html") is None
    bookstage.set_book_option(tmp_path, "output-file", "new")
    bookstage.set_book_option(tmp_path, "cover-image", "c.jpg")
    text = (tmp_path / "_quarto.yml").read_text()
    assert text == (
        'book:\n  cover-image: "c.jpg"\n  title: "T"\n  output-file: "new"\n'
        "  chapters:\n    - index.qmd\nformat:\n  html: default\n"
    )


def test_a_configuration_with_no_book_has_no_option_to_set(tmp_path):
    (tmp_path / "_quarto.yml").write_text("project:\n  type: website\n")
    with pytest.raises(ValueError, match="no book: block"):
        bookstage.set_book_option(tmp_path, "output-file", "x")


# --------------------------------------------------------------------------- #
# whether a render is a proof
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("line", "is_proof"),
    [
        ("draft-copy: true", True),
        ("draft-copy: false", False),
        ('draft-copy: "false"', True),
        ("draft-cpy: false", True),
    ],
    ids=["true", "false", "quoted", "misspelled"],
)
def test_only_the_boolean_false_publishes(book, line, is_proof):
    docs = book / "docs"
    (docs / "_quarto.yml").write_text(_BOOK.replace("draft-copy: true", line))
    assert proof.is_proof(docs) is is_proof


def test_without_a_switch_nothing_is_a_proof(book):
    (book / "render.toml").write_text("revision_metadata = true\n")
    assert proof.is_proof(book / "docs") is None
    stage = book / "build" / "book"
    assert render.stage_book(stage) == 0
    assert bookstage.book_option(stage, "output-file") is None


def test_a_switch_that_names_no_key_stops_the_stage(book, capsys):
    (book / "render.toml").write_text(_POLICY.replace('"draft-copy"', '"a key"'))
    assert render.stage_book(book / "build" / "book") == 1
    assert "proof_switch must name a metadata key" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# preparing a stage
# --------------------------------------------------------------------------- #
def test_a_proof_is_named_and_titled_for_its_commit(book):
    stage = book / "build" / "book"
    assert render.stage_book(stage) == 0
    short, date = _short(book), _date(book)
    made = proof.proof_of(stage)
    assert made.name == f"a-kettle-book_proof_{date}_{short}"
    assert made.title == f"A Kettle Book (uncorrected proof {short})"
    assert not made.modified and made.copy is None
    assert bookstage.book_option(stage, "output-file") == made.name


def test_a_proof_with_changes_not_committed_says_so_in_its_name(book):
    (book / "docs" / "index.qmd").write_text("# Welcome\n\nIt whistles.\n")
    stage = book / "build" / "book"
    assert render.stage_book(stage) == 0
    assert proof.proof_of(stage).name.endswith(f"_{_short(book)}-modified")


def test_the_policy_shapes_the_name_and_the_title(book):
    (book / "render.toml").write_text(
        _POLICY + 'proof_name = "{slug}-PROOF-{short}"\n'
        'proof_title = "{title} [{date}]"\n'
    )
    stage = book / "build" / "book"
    assert render.stage_book(stage) == 0
    made = proof.proof_of(stage)
    assert made.name == f"a-kettle-book-PROOF-{_short(book)}"
    assert made.title == f"A Kettle Book [{_date(book)}]"


def test_a_pattern_with_a_field_it_cannot_fill_stops_the_stage(book, capsys):
    (book / "render.toml").write_text(_POLICY + 'proof_name = "{isbn}"\n')
    assert render.stage_book(book / "build" / "book") == 1
    assert "names a field it cannot fill" in capsys.readouterr().err


def test_a_proof_needs_its_revision(book, capsys):
    (book / "render.toml").write_text('proof_switch = "draft-copy"\n')
    assert render.stage_book(book / "build" / "book") == 1
    assert "set revision_metadata = true" in capsys.readouterr().err


def test_the_published_book_keeps_its_own_name(book):
    docs = book / "docs"
    (docs / "_quarto.yml").write_text(
        _BOOK.replace("draft-copy: true", "draft-copy: false")
    )
    _git(book, "commit", "-qam", "A fictional publication")
    stage = book / "build" / "book"
    assert render.stage_book(stage) == 0
    assert proof.proof_of(stage) is None
    assert bookstage.book_option(stage, "output-file") is None


def test_a_copy_for_one_person_is_numbered_and_named(book):
    token = proof._COPY.set({"number": 7, "recipient": "Ada Kettle"})
    try:
        stage = book / "build" / "book"
        assert render.stage_book(stage) == 0
    finally:
        proof._COPY.reset(token)
    made = proof.proof_of(stage)
    assert made.name.endswith("_copy-07") and "Ada" not in made.name
    assert made.title.endswith(", copy 7)")
    assert (made.copy, made.recipient) == (7, "Ada Kettle")


def test_the_reader_stage_is_published_and_named_as_no_copy(book):
    (book / "render.toml").write_text(
        _POLICY + "[reader_metadata]\ndraft-copy = false\n"
    )
    stage = book / "build" / "panel-stage"
    assert render.stage_book_reader(stage) == 0
    assert proof.proof_of(stage) is None
    assert bookstage.book_option(stage, "output-file") == render.READER_STAGE_FILE


def test_a_copy_cannot_be_made_from_the_reader_stage(book, capsys):
    (book / "render.toml").write_text(
        _POLICY + "[reader_metadata]\ndraft-copy = false\n"
    )
    token = proof._COPY.set({"number": 1, "recipient": "Ada Kettle"})
    try:
        assert render.stage_book_reader(book / "build" / "panel-stage") == 1
    finally:
        proof._COPY.reset(token)
    assert "renders the book as published" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# the cover
# --------------------------------------------------------------------------- #
@pytest.fixture
def typst(monkeypatch):
    """Stand in for `quarto typst compile`: draw a small image, keep the call."""
    from PIL import Image

    calls = []

    def run(command):
        calls.append(command)
        Image.new("RGB", (8, 12), "black").save(command[4])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(proof, "_typeset", run)
    monkeypatch.setattr(render, "find_quarto", lambda: "quarto")
    return calls


def _inputs(command):
    return dict(
        command[i + 1].split("=", 1) for i, a in enumerate(command) if a == "--input"
    )


def test_a_proof_s_cover_is_drawn_with_its_identity(book, typst):
    (book / "render.toml").write_text(_POLICY + 'cover_source = "cover.typ"\n')
    (book / "docs" / "cover.typ").write_text("#set page(width: 1in)\n")
    _git(book, "add", ".")
    _git(book, "commit", "-qm", "A fictional cover")
    token = proof._COPY.set({"number": 3, "recipient": "Ada Kettle"})
    try:
        stage = book / "build" / "book"
        assert render.stage_book(stage) == 0
    finally:
        proof._COPY.reset(token)
    assert _inputs(typst[0]) == {
        "proof": "true",
        "commit": _git(book, "rev-parse", "HEAD"),
        "short": _short(book),
        "date": _date(book),
        "modified": "false",
        "copy": "3",
        "recipient": "Ada Kettle",
    }
    cover = (stage / "cover.jpg").read_bytes()
    assert proof._cover_marked(cover, proof.proof_of(stage))
    assert not (stage / ".quirework-cover.png").exists()


def test_the_published_cover_is_drawn_without_a_mark(book, typst):
    (book / "render.toml").write_text(_POLICY + 'cover_source = "cover.typ"\n')
    (book / "docs" / "cover.typ").write_text("#set page(width: 1in)\n")
    docs = book / "docs"
    (docs / "_quarto.yml").write_text(
        _BOOK.replace("draft-copy: true", "draft-copy: false")
    )
    stage = book / "build" / "book"
    assert render.stage_book(stage) == 0
    assert _inputs(typst[0]) == {"proof": "false"}
    assert proof.COVER_MARK.encode() not in (stage / "cover.jpg").read_bytes()


def test_a_cover_source_needs_an_image_to_compile_into(book, typst, capsys):
    (book / "render.toml").write_text(_POLICY + 'cover_source = "cover.typ"\n')
    (book / "docs" / "cover.typ").write_text("#set page(width: 1in)\n")
    docs = book / "docs"
    (docs / "_quarto.yml").write_text(_BOOK.replace("  cover-image: cover.jpg\n", ""))
    assert render.stage_book(book / "build" / "book") == 1
    assert "needs book.cover-image" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# the artifacts, written by hand, and the check that reads them back
# --------------------------------------------------------------------------- #
def _pdf(path, pages):
    """A PDF with one line of Helvetica text per page."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
        content = len(objects) + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
            f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
            f"/BaseFont /Helvetica >> >> >> /Contents {content + 1} 0 R >>"
        )
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream"
        )
        kids.append(f"{content} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    path.write_bytes(out)


def _epub(path, title, pages, cover=b"\xff\xd8 plain cover"):
    opf = (
        '<package><metadata><dc:title id="t1">' + title + "</dc:title>"
        '<dc:title id="t2">ignored</dc:title></metadata><manifest>'
        '<item id="cover_xhtml" href="text/cover.xhtml"/>'
        '<item properties="cover-image" id="img" href="media/cover.jpg"/>'
        + "".join(
            f'<item id="p{i}" href="text/p{i}.xhtml"/>' for i in range(len(pages))
        )
        + '</manifest><spine><itemref idref="cover_xhtml"/>'
        + "".join(f'<itemref idref="p{i}"/>' for i in range(len(pages)))
        + "</spine></package>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="EPUB/content.opf"/>'
            "</rootfiles></container>",
        )
        z.writestr("EPUB/content.opf", opf)
        z.writestr(
            "EPUB/text/cover.xhtml",
            "<html><head><style>p{margin:0}</style></head>"
            '<body><img src="../media/cover.jpg"/></body></html>',
        )
        z.writestr("EPUB/media/cover.jpg", cover)
        for i, text in enumerate(pages):
            z.writestr(
                f"EPUB/text/p{i}.xhtml", f"<html><body><p>{text}</p></body></html>"
            )


def _docx(path, title, text):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("docProps/core.xml", f"<cp><dc:title>{title}</dc:title></cp>")
        z.writestr("word/document.xml", f"<w:document><w:t>{text}</w:t></w:document>")


@pytest.fixture
def prepared(book):
    """A screen stage prepared as a proof, with its site directory."""
    stage = book / "build" / "book"
    assert render.stage_book(stage) == 0
    site = stage / "_site"
    site.mkdir()
    return stage, site, proof.proof_of(stage)


def test_a_proof_stamped_on_every_page_passes(prepared, capsys):
    stage, site, made = prepared
    stamp = f"Uncorrected proof {made.short}"
    _pdf(site / f"{made.name}.pdf", [stamp, f"Page two {stamp}"])
    mark = f"{proof.COVER_MARK} {made.commit}".encode()
    _epub(site / f"{made.name}.epub", made.title, [stamp, stamp], cover=b"\xff" + mark)
    _docx(site / f"{made.name}.docx", made.title, stamp)
    (site / "index.html").write_text(f"<html><body>{stamp}</body></html>")
    formats = ["typst", "epub", "docx", "html"]
    assert proof.verify(site, [(stage, formats)]) == 0
    assert capsys.readouterr().out.count(f"names {made.short} throughout") == 4


def test_a_pdf_page_without_the_stamp_stops_the_build(prepared, capsys):
    stage, site, made = prepared
    _pdf(site / f"{made.name}.pdf", [made.short, "A kettle boils.", made.short])
    assert proof.verify(site, [(stage, ["typst"])]) == 1
    assert f"1 of 3 pages do not name {made.short} (pages 2)" in capsys.readouterr().err


def test_an_epub_that_does_not_say_what_it_is_stops_the_build(prepared, capsys):
    stage, site, made = prepared
    _epub(site / f"{made.name}.epub", "A Kettle Book", [made.short, "No stamp here."])
    assert proof.verify(site, [(stage, ["epub"])]) == 1
    err = capsys.readouterr().err
    assert f"the library title does not name {made.short}" in err
    assert "1 reading-order documents do not name" in err and "p1.xhtml" in err
    assert "the cover is not the proof's own cover" in err


def test_a_proof_left_under_another_name_stops_the_build(prepared, capsys):
    stage, site, made = prepared
    _pdf(site / "A-Kettle-Book.pdf", [made.short])
    assert proof.verify(site, [(stage, ["typst"])]) == 1
    assert "was not written under its proof name" in capsys.readouterr().err


def test_a_copy_names_its_reader_on_the_first_page(book, capsys):
    token = proof._COPY.set({"number": 2, "recipient": "Ada Kettle"})
    try:
        stage = book / "build" / "book"
        assert render.stage_book(stage) == 0
    finally:
        proof._COPY.reset(token)
    made = proof.proof_of(stage)
    site = stage / "_site"
    site.mkdir()
    _pdf(site / f"{made.name}.pdf", [made.short, f"Copy 2 for Ada Kettle {made.short}"])
    assert proof.verify(site, [(stage, ["typst"])]) == 1
    assert "the first page does not name Ada Kettle" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("extracted", "given"),
    [
        ("Copy 2 for So\ufb01a Kettle", "Sofia Kettle"),  # an fi ligature
        ("A D A  K E T T L E", "ADA KETTLE"),  # tracked capitals
        ("Ada O\u2019Kettle", "Ada O'Kettle"),  # a typeset apostrophe
        ("ab\xadc1234", "abc1234"),  # a soft hyphen
    ],
    ids=["ligature", "tracking", "apostrophe", "soft-hyphen"],
)
def test_set_type_reads_back_as_the_words_it_sets(extracted, given):
    assert proof._bare(given) in proof._bare(extracted)


def test_the_published_book_is_not_read_back(book):
    docs = book / "docs"
    (docs / "_quarto.yml").write_text(
        _BOOK.replace("draft-copy: true", "draft-copy: false")
    )
    stage = book / "build" / "book"
    assert render.stage_book(stage) == 0
    assert proof.verify(stage / "_site", [(stage, ["typst", "epub"])]) == 0


def test_a_format_it_cannot_read_is_named_not_failed(prepared, capsys):
    stage, site, _ = prepared
    assert proof.verify(site, [(stage, ["odt"])]) == 0
    assert "odt is not read back" in capsys.readouterr().out


def test_the_library_title_changes_and_the_pages_do_not(prepared):
    stage, site, made = prepared
    _epub(site / f"{made.name}.epub", "A Kettle Book", ["A Kettle Book"])
    _docx(site / f"{made.name}.docx", "A Kettle Book", "A Kettle Book")
    assert len(proof.retitle(site, stage)) == 2
    with zipfile.ZipFile(site / f"{made.name}.epub") as z:
        assert z.infolist()[0].filename == "mimetype"
        assert z.infolist()[0].compress_type == zipfile.ZIP_STORED
        opf = z.read("EPUB/content.opf").decode()
        assert f'<dc:title id="t1">{made.title}</dc:title>' in opf
        assert '<dc:title id="t2">ignored</dc:title>' in opf
        assert z.read("EPUB/text/p0.xhtml").decode().count("A Kettle Book") == 1
    with zipfile.ZipFile(site / f"{made.name}.docx") as z:
        assert made.title in z.read("docProps/core.xml").decode()


# --------------------------------------------------------------------------- #
# `publish copy`
# --------------------------------------------------------------------------- #
@pytest.fixture
def log(book, tmp_path, monkeypatch):
    """A per-user configuration naming a log of copies outside the book."""
    home = tmp_path / "xdg"
    (home / "quirework").mkdir(parents=True)
    path = tmp_path / "logs" / "copies.jsonl"
    (home / "quirework" / "config.toml").write_text(f'[copies]\nlog = "{path}"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    monkeypatch.setattr(render, "book_formats", lambda: ["html", "typst", "epub"])
    monkeypatch.setattr(render, "find_quarto", lambda: "quarto")
    return path


@pytest.fixture
def renders(monkeypatch):
    """Stand in for the render: stage for real, then write each stage's files
    under the names its proof gives them, stamped throughout."""
    calls = []

    def fake(quarto, argv, any_font, edition=None):
        calls.append(argv)
        site = render._options().BUILD_BOOK / render.SITE
        site.mkdir(parents=True, exist_ok=True)
        for stage, ext in (
            (render._settings().BUILD_BOOK_PRINT, ".pdf"),
            (render._settings().BUILD_BOOK_NOPARTS, ".epub"),
        ):
            assert render.stage_book(stage) == 0
            made = proof.proof_of(stage)
            (site / f"{made.name}{ext}").write_text(f"{made.short} {made.recipient}")
        return 0

    monkeypatch.setattr(render, "render", fake)
    return calls


def _log(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_a_copy_is_numbered_logged_and_kept_apart(book, log, renders, capsys):
    assert proof.main(["--for", "Ada  Kettle"]) == 0
    assert renders == [["--to", "typst,epub"]]
    copies = book / "build" / "copies"
    names = sorted(p.name for p in copies.iterdir())
    assert len(names) == 2
    assert all(Path(n).stem.endswith("_copy-01") for n in names)
    assert not any("Ada" in n for n in names)
    started, issued = _log(log)
    assert started["copy"] == issued["copy"] == 1
    assert started["recipient"] == "Ada Kettle"
    assert started["commit"] == _git(book, "rev-parse", "HEAD")
    assert sorted(f["name"] for f in issued["files"]) == names
    assert all(len(f["sha256"]) == 64 for f in issued["files"])
    assert proof.main(["--for", "Bea Pot"]) == 0
    assert _log(log)[-1]["copy"] == 2
    assert "copy 2, prepared for Bea Pot" in capsys.readouterr().out


def test_a_copy_needs_a_log_outside_the_project(book, log, renders, capsys):
    config = log.parent.parent / "xdg" / "quirework" / "config.toml"
    config.unlink()
    assert proof.main(["--for", "Ada Kettle"]) == 2
    assert "set copies.log" in capsys.readouterr().err
    config.write_text(f'[copies]\nlog = "{book / "copies.jsonl"}"\n')
    assert proof.main(["--for", "Ada Kettle"]) == 2
    assert "inside the project" in capsys.readouterr().err
    assert renders == []


def test_a_copy_is_refused_on_changes_not_committed(book, log, renders, capsys):
    (book / "docs" / "index.qmd").write_text("# Welcome\n\nIt whistles.\n")
    assert proof.main(["--for", "Ada Kettle"]) == 2
    assert "changes not yet committed" in capsys.readouterr().err
    assert renders == [] and not log.exists()


def test_a_copy_of_the_published_book_is_refused(book, log, renders, capsys):
    docs = book / "docs"
    (docs / "_quarto.yml").write_text(
        _BOOK.replace("draft-copy: true", "draft-copy: false")
    )
    _git(book, "commit", "-qam", "A fictional publication")
    assert proof.main(["--for", "Ada Kettle"]) == 2
    assert "the book is published" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "says"),
    [
        (["--to", "html", "--for", "Ada"], "HTML is a site"),
        (["--to", "odt", "--for", "Ada"], "no declared format odt"),
        ([], "name the person"),
        (["--for", "Ada", "--fast"], "unknown argument"),
    ],
)
def test_a_copy_asked_for_wrongly_is_refused(book, log, renders, capsys, argv, says):
    assert proof.main(argv) == 2
    assert says in capsys.readouterr().err
    assert renders == []


def test_a_render_that_fails_logs_the_number_as_failed(book, log, monkeypatch):
    monkeypatch.setattr(render, "render", lambda *a, **k: 3)
    assert proof.main(["--for", "Ada Kettle"]) == 3
    assert [r["event"] for r in _log(log)] == ["started", "failed"]
    assert not (book / "build" / "copies").exists()


def test_a_log_it_cannot_read_is_never_guessed_past(book, log, renders, capsys):
    log.parent.mkdir(parents=True)
    log.write_text('{"copy": 4}\nnot a record\n')
    assert proof.main(["--for", "Ada Kettle"]) == 1
    assert "line 2 of the log of copies is not a record" in capsys.readouterr().err
    assert renders == []


def test_help_names_the_log_and_touches_nothing(capsys):
    assert proof.main(["--help"]) == 0
    assert "copies.log" in capsys.readouterr().out
