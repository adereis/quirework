"""bookstage: staged-book structure utilities (extracted from redpen).

These tests moved with the code: flatten/walk the staged _quarto.yml's
book.chapters block, unnumber a promoted part's H1, copy a figure gallery
beside a standalone chapter, cap DOCX image widths for Google Docs.
"""

import re
import zipfile

import pytest

from quirework.manuscript import quarto
from quirework.project import Project, project_scope
from quirework.publish import bookstage


@pytest.fixture(autouse=True)
def quarto_book(tmp_path):
    """A Quarto book whose chapters sit in `canon/` under its source and whose
    render policy strips one EPUB style include, as a real book declares."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "render.toml").write_text('epub_style_headers = ["epub-styles.html"]\n')
    settings = {
        "manuscript": {"source": "docs", "chapters": "docs/canon"},
        "publish": {"render_policy": "render.toml"},
    }
    with project_scope(Project.from_settings(root, settings)):
        yield


NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _p(text, style=None):
    pr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{pr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def _make_docx(path, body_paras, comments_xml=""):
    doc = f"<w:document {NS}><w:body>{''.join(body_paras)}</w:body></w:document>"
    comments = f"<w:comments {NS}>{comments_xml}</w:comments>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", doc)
        z.writestr("word/comments.xml", comments)


# --------------------------------------------------------------------------- #
# build side: flattening the book's parts for a format that has none
# --------------------------------------------------------------------------- #
BOOK_YML = """\
project:
  type: book

book:
  title: "A Book"
  chapters:
    - index.qmd
    # a comment rides along
    - part: "Part One: What kettles are"
      chapters:
        - canon/01-first.qmd
        - canon/02-second.qmd
    - part: canon/part-two.qmd
      chapters:
        - canon/08-eighth.qmd
    - canon/12-last.qmd
    - part: Back matter
      chapters:
        - canon/sources.qmd

format:
  html:
    theme: cosmo
"""


def _stage_book(tmp_path, yml=BOOK_YML):
    (tmp_path / "_quarto.yml").write_text(yml)
    return tmp_path


def test_flatten_book_parts_promotes_files_and_materializes_titles(tmp_path):
    stage = _stage_book(tmp_path)
    promoted, dividers = bookstage.flatten_book_parts(stage)
    assert promoted == ["canon/part-two.qmd"]  # the essay survives
    assert dividers == ["part-one.qmd", "back-matter.qmd"]  # heading-only
    out = (stage / "_quarto.yml").read_text()
    assert "part:" not in out
    body = [l.strip() for l in out.splitlines()]
    # reading order preserved, each divider sitting where its `part:` stood
    assert body[body.index("- index.qmd") : body.index("- canon/sources.qmd") + 1] == [
        "- index.qmd",
        "# a comment rides along",
        "- part-one.qmd",
        "- canon/01-first.qmd",
        "- canon/02-second.qmd",
        "- canon/part-two.qmd",
        "- canon/08-eighth.qmd",
        "- canon/12-last.qmd",
        "- back-matter.qmd",
        "- canon/sources.qmd",
    ]
    # everything outside book.chapters is untouched, blank line and all
    assert out.endswith("\nformat:\n  html:\n    theme: cosmo\n")
    assert "\n\nformat:" in out


def test_materialized_divider_is_an_unnumbered_heading(tmp_path):
    """A divider must take no chapter number, or every chapter after it shifts."""
    stage = _stage_book(tmp_path)
    bookstage.flatten_book_parts(stage)
    assert (stage / "part-one.qmd").read_text() == (
        "# [Part One]{.part-label}[:]{.part-colon} [What kettles are]{.part-name}"
        " {.unnumbered .part-divider}\n"
    )
    assert (stage / "back-matter.qmd").read_text() == (
        "# [Back matter]{.part-name} {.unnumbered .part-divider}\n"
    )


def test_divider_heading_keeps_the_whole_title_as_text(tmp_path):
    """The spans are presentational; the heading's TEXT is still the full title.

    The nav document, the Contents page and the DOCX all read it as text, so a
    colon dropped here rather than hidden by the EPUB stylesheet would show up
    as "Part Two Further out" in three places at once."""
    for title in ("Part One: What kettles are", "Back matter"):
        head = bookstage._divider_heading(title)
        assert re.sub(r"[\[\]]|\{[^}]*\}", "", head) == title


STYLES = """\
<!-- A banner comment, which must not reach an XML-parsed document -->
<style type="text/css">
/* why the rhythm is what it is */
p {
  text-indent: 1.15em;   /* trailing note */
}

@font-face {
  font-family: "Example Serif";
  src: url("../fonts/S.woff2") format("woff2");
}
</style>
"""


def test_strip_style_comments_keeps_the_rules_and_drops_the_rest(tmp_path):
    """The include is copied into every content document, so the reader pays for
    each line of it ~38 times; the doctrine stays in the source."""
    (tmp_path / bookstage.EPUB_STYLES).write_text(STYLES, encoding="utf-8")
    before, after = bookstage.strip_style_comments(tmp_path)
    out = (tmp_path / bookstage.EPUB_STYLES).read_text(encoding="utf-8")
    assert after < before
    assert "<!--" not in out and "/*" not in out  # nothing to parse as a comment
    assert "why the rhythm" not in out and "trailing note" not in out
    assert "text-indent: 1.15em;" in out  # every rule survives
    assert 'url("../fonts/S.woff2") format("woff2")' in out
    assert out.startswith('<style type="text/css">') and out.rstrip().endswith(
        "</style>"
    )


def test_strip_style_comments_does_not_read_a_string_as_a_comment(tmp_path):
    """A `/*` inside a url or a content string is not a comment opener. Eating
    from there to the next `*/` would swallow real rules, and a regex does
    exactly that."""
    css = (
        '<style type="text/css">\n'
        'a::after { content: "/* not a comment */"; }\n'
        "b { color: red; }\n"
        "</style>\n"
    )
    (tmp_path / bookstage.EPUB_STYLES).write_text(css, encoding="utf-8")
    bookstage.strip_style_comments(tmp_path)
    out = (tmp_path / bookstage.EPUB_STYLES).read_text(encoding="utf-8")
    assert 'content: "/* not a comment */";' in out
    assert "b { color: red; }" in out


def test_strip_style_comments_refuses_a_missing_or_styleless_file(tmp_path):
    """A rename must break the render, not silently stop stripping."""
    with pytest.raises(ValueError, match="not in the staged book"):
        bookstage.strip_style_comments(tmp_path)
    (tmp_path / bookstage.EPUB_STYLES).write_text(
        "<p>no styles here</p>\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="no <style> element"):
        bookstage.strip_style_comments(tmp_path)


def test_part_divider_will_not_clobber_a_book_file(tmp_path):
    stage = _stage_book(tmp_path)
    (stage / "part-one.qmd").write_text("# A real chapter\n")
    with pytest.raises(ValueError, match="collides"):
        bookstage.flatten_book_parts(stage)


def test_book_chapters_reading_order(tmp_path):
    stage = _stage_book(tmp_path)
    assert quarto.book_chapters(stage) == [
        "index.qmd",
        "canon/01-first.qmd",
        "canon/02-second.qmd",
        "canon/part-two.qmd",  # a file-based part divider is itself a chapter
        "canon/08-eighth.qmd",
        "canon/12-last.qmd",
        "canon/sources.qmd",  # title-only "Back matter" dropped, its file kept
    ]
    # read-only: it must not rewrite the manifest the way flatten does
    assert "part:" in (stage / "_quarto.yml").read_text()


# --------------------------------------------------------------------------- #
# build side: the Contents page, which only Typst can build for itself
# --------------------------------------------------------------------------- #
def _stage_with_pages(tmp_path):
    stage = _stage_book(tmp_path)
    (stage / "canon").mkdir()
    (stage / "contents.qmd").write_text(
        "# Contents {.unnumbered .unlisted}\n\n"
        "```{=typst}\n#outline(title: none, depth: 1)\n```\n"
    )
    (stage / "index.qmd").write_text("# Welcome {.unnumbered .unlisted}\n")
    (stage / "canon" / "01-first.qmd").write_text("# First {#sec-first}\n")
    (stage / "canon" / "02-second.qmd").write_text("# Second {#sec-second}\n")
    (stage / "canon" / "part-two.qmd").write_text("# Part Two {#sec-part-two}\n")
    (stage / "canon" / "08-eighth.qmd").write_text("# Eighth {#sec-eighth}\n")
    (stage / "canon" / "12-last.qmd").write_text("# Last {#sec-last}\n")
    (stage / "canon" / "sources.qmd").write_text("# Sources {.unnumbered}\n")
    return stage


def test_book_outline_numbers_like_quarto(tmp_path):
    """Parts and .unnumbered pages take no number, .unlisted pages no row --
    so the page cannot disagree with the numbers the book itself prints."""
    rows = bookstage.book_outline(_stage_with_pages(tmp_path))
    assert rows == [
        ("part", "Part One: What kettles are", None),
        ("chapter", "1 First", "canon/01-first.qmd"),
        ("chapter", "2 Second", "canon/02-second.qmd"),
        ("part", "Part Two", None),  # a file-based divider
        ("chapter", "3 Eighth", "canon/08-eighth.qmd"),
        ("chapter", "4 Last", "canon/12-last.qmd"),
        ("part", "Back matter", None),
        ("chapter", "Sources", "canon/sources.qmd"),  # .unnumbered
    ]  # index.qmd .unlisted: no row


def test_book_outline_anchors_write_the_id_they_link_to(tmp_path):
    """Deriving Pandoc's auto-id would be a guess, and a wrong guess is a dead
    link nothing checks. The id is written in when a chapter has none."""
    stage = _stage_with_pages(tmp_path)
    rows = bookstage.book_outline(stage, anchors=True)
    assert ("chapter", "1 First", "#sec-first") in rows  # kept its own
    assert ("chapter", "Sources", "#sec-sources") in rows  # written in
    assert (stage / "canon" / "sources.qmd").read_text() == (
        "# Sources {#sec-sources .unnumbered}\n"
    )


def test_render_contents_keeps_typst_out_of_it(tmp_path):
    stage = _stage_with_pages(tmp_path)
    assert bookstage.render_contents(stage) == 8
    page = (stage / "contents.qmd").read_text()
    assert "#outline(title: none, depth: 1)" in page  # print untouched
    assert 'content-visible unless-format="typst"' in page
    assert "**Part One: What kettles are**" in page
    # a run of chapters is one paragraph of hard-broken lines, wrapped so a
    # stylesheet can indent it under its part
    assert (
        "::: {.contents-run}\n"
        "[1 First](canon/01-first.qmd)\\\n"
        "[2 Second](canon/02-second.qmd)\n"
        ":::"
    ) in page


def test_render_contents_no_ops_without_a_contents_page(tmp_path):
    assert bookstage.render_contents(_stage_book(tmp_path)) == 0


def test_flatten_book_parts_is_idempotent(tmp_path):
    stage = _stage_book(tmp_path)
    bookstage.flatten_book_parts(stage)
    once = (stage / "_quarto.yml").read_text()
    assert bookstage.flatten_book_parts(stage) == ([], [])
    assert (stage / "_quarto.yml").read_text() == once


def test_flatten_book_parts_rejects_shapes_it_cannot_read(tmp_path):
    for yml in (
        "project:\n  type: book\n",  # no book:
        "book:\n  title: x\n",  # no chapters:
        "book:\n  chapters:\n\nformat:\n  html:\n",  # empty list
        "book:\n  chapters:\n    - part:\n      chapters:\n        - a.qmd\n",  # bare part
        "book:\n  chapters:\n    - a.qmd\n      title: nope\n",  # nested key
    ):
        stage = _stage_book(tmp_path, yml)
        with pytest.raises(ValueError):
            bookstage.flatten_book_parts(stage)


def test_unnumber_h1_adds_class_beside_existing_attrs(tmp_path):
    qmd = tmp_path / "part-two.qmd"
    qmd.write_text("# Part Two: What we pay them {#sec-part-two}\n\nProse.\n")
    assert bookstage.unnumber_h1(qmd) is True
    assert qmd.read_text().startswith(
        "# Part Two: What we pay them {#sec-part-two .unnumbered}\n"
    )
    assert bookstage.unnumber_h1(qmd) is True  # idempotent
    assert qmd.read_text().count(".unnumbered") == 1

    bare = tmp_path / "bare.qmd"
    bare.write_text("# Plain\n\nProse.\n")
    bookstage.unnumber_h1(bare)
    assert bare.read_text().startswith("# Plain {.unnumbered}\n")


def test_unnumber_h1_without_h1_is_false(tmp_path):
    qmd = tmp_path / "no-h1.qmd"
    qmd.write_text("---\ntitle: Preface\n---\n\nProse.\n")
    assert bookstage.unnumber_h1(qmd) is False


def test_copy_gallery_uses_the_name_the_embeds_point_at(tmp_path):
    from quirework.publish import figures

    stage = tmp_path / "stage"
    (stage / "canon" / figures.GALLERY).mkdir(parents=True)
    (stage / "canon" / figures.GALLERY / "the-kettle.svg").write_text("<svg/>")
    work = tmp_path / "work"
    assert bookstage.copy_gallery(stage, work) == 1
    # a chapter embeds `illustrations/the-kettle.svg`; the copy must resolve it
    assert (work / figures.GALLERY / "the-kettle.svg").exists()
    assert figures.GALLERY in figures.EMBED.pattern


def test_copy_gallery_without_a_staged_gallery_is_empty(tmp_path):
    stage = tmp_path / "stage"
    (stage / "canon").mkdir(parents=True)
    assert bookstage.copy_gallery(stage, tmp_path / "work") == 0


def test_copy_gallery_copies_the_named_gray_set(tmp_path):
    from quirework.publish import figures

    stage = tmp_path / "stage"
    (stage / "canon" / figures.GRAY_GALLERY).mkdir(parents=True)
    (stage / "canon" / figures.GRAY_GALLERY / "the-kettle.svg").write_text("<svg/>")
    work = tmp_path / "work"
    assert bookstage.copy_gallery(stage, work, figures.GRAY_GALLERY) == 1
    # the --gray twins embed `illustrations-gray/the-kettle.svg`; the copy resolves it
    assert (work / figures.GRAY_GALLERY / "the-kettle.svg").exists()
    # the default (color) gallery is untouched by a gray-only copy
    assert not (work / figures.GALLERY).exists()


# --------------------------------------------------------------------------- #
# cap_docx_image_widths: shrink over-wide figures so Google Docs stops cropping
# --------------------------------------------------------------------------- #
def _drawing(cx, cy):
    """A picture drawing mirroring Pandoc's docx output: a wp:extent, a blip whose
    extension-list <a:ext uri=...> blocks carry NO cx, and the self-closing
    transform <a:ext cx.. cy..>. Only wp:extent and the transform a:ext must move."""
    return (
        "<w:drawing><wp:inline>"
        f'<wp:extent cx="{cx}" cy="{cy}"/>'
        '<wp:effectExtent b="0" l="0" r="0" t="0"/>'
        '<a:graphic><a:graphicData uri="pic"><pic:pic><pic:blipFill>'
        '<a:blip r:embed="rId1"><a:extLst>'
        '<a:ext uri="{28A0092B-C50C-407E-A947-70E740481C1C}"><a14:useLocalDpi val="0"/></a:ext>'
        '<a:ext uri="{96DAC541-7B7A-43D3-8B79-37D633B846F1}"><asvg:svgBlip r:embed="rId2"/></a:ext>'
        "</a:extLst></a:blip><a:stretch><a:fillRect/></a:stretch></pic:blipFill>"
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm></pic:spPr>'
        "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing>"
    )


def test_cap_docx_image_widths_scales_oversized_preserving_aspect(tmp_path):
    max_w = bookstage.MAX_IMG_WIDTH_EMU
    big_cx, big_cy = max_w * 2, max_w  # 2:1, well over the cap
    small_cx, small_cy = max_w // 2, max_w // 4  # under the cap -> untouched
    over = tmp_path / "over.docx"
    _make_docx(over, [_drawing(big_cx, big_cy), _drawing(small_cx, small_cy)])

    bookstage.cap_docx_image_widths(over)

    with zipfile.ZipFile(over) as z:
        doc = z.read("word/document.xml").decode()
    # capped to the max width, aspect preserved (2:1 -> cy halves to max_w // 2),
    # in BOTH the layout extent and the picture transform
    assert f'<wp:extent cx="{max_w}" cy="{max_w // 2}"/>' in doc
    assert f'<a:ext cx="{max_w}" cy="{max_w // 2}"/>' in doc
    assert str(big_cx) not in doc  # the oversized value is gone
    # the under-cap image is left exactly as-is
    assert f'<wp:extent cx="{small_cx}" cy="{small_cy}"/>' in doc
    # extension-list <a:ext uri=...> blocks (no cx) are never rewritten
    assert doc.count("useLocalDpi") == 2
    assert doc.count("asvg:svgBlip") == 2


def test_cap_docx_image_widths_is_noop_without_oversized_images(tmp_path):
    plain = tmp_path / "plain.docx"
    _make_docx(plain, [_p("just text"), _drawing(bookstage.MAX_IMG_WIDTH_EMU, 1000)])
    before = plain.read_bytes()
    bookstage.cap_docx_image_widths(plain)
    assert plain.read_bytes() == before  # unchanged file, byte-for-byte


def test_typst_prologue_and_epilogue_land_on_the_right_side(tmp_path):
    # WHICH END is the whole point: `set page` applies from the next page, so
    # the numbering switch has to sit at the END of the front matter, not at
    # the head of the first chapter.
    (tmp_path / "canon").mkdir()
    pre = tmp_path / "preface.qmd"
    pre.write_text("# Preface\n\nBody.\n")
    src = tmp_path / "canon" / "sources.qmd"
    src.write_text("# Sources\n\nBody.\n")
    touched = bookstage.inject_typst_prologues(
        tmp_path,
        prologues={"canon/sources.qmd": "#set text(size: 0.93em)\n"},
        epilogues={"preface.qmd": '#set page(numbering: "1")\n'},
    )
    assert set(touched) == {"canon/sources.qmd", "preface.qmd"}
    assert src.read_text().startswith("```{=typst}\n#set text(size: 0.93em)\n```")
    assert pre.read_text().rstrip().endswith("```")
    assert pre.read_text().index("# Preface") < pre.read_text().index("=typst")


def test_typst_injection_is_idempotent_and_skips_absent_files(tmp_path):
    # A stage may hold a subset of the book (a one-chapter redpen build, a
    # panel kit), so a block with nowhere to land is not an error there.
    f = tmp_path / "preface.qmd"
    f.write_text("# Preface\n")
    eps = {"preface.qmd": "#x\n", "canon/missing.qmd": "#y\n"}
    first = bookstage.inject_typst_prologues(tmp_path, prologues={}, epilogues=eps)
    again = bookstage.inject_typst_prologues(tmp_path, prologues={}, epilogues=eps)
    assert first == ["preface.qmd"] and again == []
    assert f.read_text().count("=typst") == 1


_QUARTO_DROP = """\
project:
  type: book

book:
  chapters:
    - index.qmd
    - copyright.qmd
    - part: "Part One"
      chapters:
        - canon/one.qmd
"""


def test_blank_chapters_leaves_the_entry_and_marks_the_file(tmp_path):
    # The config entry MUST survive: Quarto refuses a book whose chapter list
    # has no home page. And the file must not merely be emptied, because
    # Quarto then synthesizes a NUMBERED chapter for it and every real chapter
    # shifts up by one -- the marker heading is unnumbered and unlisted.
    (tmp_path / "canon").mkdir()
    (tmp_path / "_quarto.yml").write_text(_QUARTO_DROP)
    for rel in ("index.qmd", "copyright.qmd", "canon/one.qmd"):
        (tmp_path / rel).write_text("# H\n\nBody.\n")
    assert bookstage.blank_chapters(tmp_path, {"index.qmd"}) == ["index.qmd"]
    assert (tmp_path / "_quarto.yml").read_text() == _QUARTO_DROP
    body = (tmp_path / "index.qmd").read_text()
    assert bookstage.PRINT_OMIT_MARKER in body
    assert ".unnumbered" in body and ".unlisted" in body
    assert "Body." not in body
    assert (tmp_path / "copyright.qmd").read_text() == "# H\n\nBody.\n"


def test_blank_chapters_ignores_a_name_the_book_does_not_have(tmp_path):
    (tmp_path / "canon").mkdir()
    (tmp_path / "_quarto.yml").write_text(_QUARTO_DROP)
    for rel in ("index.qmd", "copyright.qmd", "canon/one.qmd"):
        (tmp_path / rel).write_text("# H\n")
    assert bookstage.blank_chapters(tmp_path, {"nope.qmd"}) == []
    assert (tmp_path / "_quarto.yml").read_text() == _QUARTO_DROP
