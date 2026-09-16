"""Hermetic tests for the two EPUB conformance fixes (no Quarto, no java).

Both defects these repair are invisible in a reader and were found only by the
validator. That cuts the other way too: **EPUBCheck cannot police the repair.**
A transform that deleted a figure outright would leave a perfectly valid book,
and a passing `quirework book epubcheck` would report it as clean. So the tests
that matter here are the ones asserting what must SURVIVE -- the image, its
own alt text, the face declarations -- and not merely that the invalid markup
went away.
"""

import re
import zipfile

from quirework.book import bookstage as b

FIGURE = (
    '<div id="fig-x" class="quarto-float" alt="A described figure.">\n'
    '<figure class="quarto-float">\n'
    '<div aria-describedby="fig-x-caption">\n'
    '<img src="../media/file0.svg" alt="A described figure." />\n'
    "</div>\n"
    '<figcaption id="fig-x-caption">Figure 1.1: A caption.</figcaption>\n'
    "</figure>\n</div>\n"
)

FACE = (
    '@font-face {\n  font-family: "Spectral";\n'
    '  src: url("../fonts/Spectral-Regular.woff2") format("woff2"),\n'
    '       url("fonts/Spectral-Regular.woff2") format("woff2");\n'
    "  font-weight: 400;\n}"
)


def test_the_image_and_its_own_alt_survive():
    """The property EPUBCheck cannot check: a valid book may be a gutted one."""
    out, removed = b.strip_invalid_alt(FIGURE)
    assert removed == 1
    assert out.count("<img") == 1
    assert '<img src="../media/file0.svg" alt="A described figure." />' in out


def test_the_wrapping_div_loses_its_alt_but_keeps_everything_else():
    out, _ = b.strip_invalid_alt(FIGURE)
    assert "alt=" not in out.split("<img")[0]
    assert 'id="fig-x"' in out and 'class="quarto-float"' in out


def test_alt_is_kept_on_every_element_permitted_to_carry_it():
    for tag in sorted(b.ALT_OK):
        markup = f'<{tag} alt="keep me" />'
        out, removed = b.strip_invalid_alt(markup)
        assert out == markup and removed == 0


def test_a_document_with_nothing_to_fix_is_returned_unchanged():
    clean = "<p>Ordinary prose with no alt anywhere.</p>"
    assert b.strip_invalid_alt(clean) == (clean, 0)


ENTRIES = {
    "EPUB/fonts/Spectral-Regular.woff2",
    "EPUB/text/ch001.xhtml",
    "EPUB/nav.xhtml",
}
SINGLE = FACE.replace(
    '  src: url("../fonts/Spectral-Regular.woff2") format("woff2"),\n'
    '       url("fonts/Spectral-Regular.woff2") format("woff2");',
    '  src: url("../fonts/Spectral-Regular.woff2") format("woff2");',
)


def test_a_face_is_kept_where_its_url_resolves():
    assert b.font_face_resolves(SINGLE, "EPUB/text/ch001.xhtml", ENTRIES)


def test_a_face_is_dropped_where_its_url_points_outside_the_container():
    """`../fonts/` from `EPUB/nav.xhtml` climbs above the container root."""
    assert not b.font_face_resolves(SINGLE, "EPUB/nav.xhtml", ENTRIES)


def test_a_face_is_dropped_when_only_some_of_its_urls_resolve():
    """Validity needs EVERY src url to resolve, not just the one css picks."""
    assert not b.font_face_resolves(FACE, "EPUB/text/ch001.xhtml", ENTRIES)


def test_pruning_keeps_the_document_otherwise_intact():
    doc = f"<html><style>{SINGLE}</style><body>{FIGURE}</body></html>"
    kept, dropped = b.prune_font_faces(doc, "EPUB/text/ch001.xhtml", ENTRIES)
    assert dropped == 0 and kept == doc
    pruned, dropped = b.prune_font_faces(doc, "EPUB/nav.xhtml", ENTRIES)
    assert dropped == 1
    assert "@font-face" not in pruned
    assert "<img" in pruned and "<style>" in pruned


def _epub(path, docs):
    """A minimal container: mimetype first and stored, as OCF requires."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        z.writestr("EPUB/styles/stylesheet1.css", "body { margin: 0; }\n")
        z.writestr("EPUB/fonts/Spectral-Regular.woff2", b"\x00")
        for name, text in docs.items():
            z.writestr(name, text)


def test_conform_keeps_the_faces_in_the_content_documents(tmp_path):
    """The engine registers a face only from the document's own style element,
    so a 'tidier' relocation into the stylesheet ships the wrong typeface."""
    epub = tmp_path / "b.epub"
    doc = f"<html><style>{SINGLE}</style><body>{FIGURE}</body></html>"
    _epub(epub, {"EPUB/text/ch001.xhtml": doc, "EPUB/nav.xhtml": doc})
    b.conform_epub(epub)

    with zipfile.ZipFile(epub) as z:
        chapter = z.read("EPUB/text/ch001.xhtml").decode()
        nav = z.read("EPUB/nav.xhtml").decode()
        css = z.read("EPUB/styles/stylesheet1.css").decode()

    # Kept where it resolves and where the engine will read it ...
    assert chapter.count("@font-face") == 1
    assert 'url("../fonts/Spectral-Regular.woff2")' in chapter
    # ... dropped from the one document it points nowhere from ...
    assert "@font-face" not in nav
    # ... and never moved into the stylesheet.
    assert "@font-face" not in css
    # The figure and its own alt survive in both.
    for text in (chapter, nav):
        assert text.count("<img") == 1
        assert 'alt="A described figure."' in text
        assert not re.search(r"<div[^>]*\balt=", text)


def test_conform_preserves_the_ocf_entry_order_and_stored_mimetype(tmp_path):
    """A repack that compresses `mimetype` or moves it trades two defects for
    a worse one: the container stops being a valid EPUB at all."""
    epub = tmp_path / "b.epub"
    _epub(
        epub,
        {
            "EPUB/text/ch001.xhtml": f"<html><style>{FACE}</style><body>{FIGURE}</body></html>"
        },
    )
    before = zipfile.ZipFile(epub).namelist()
    b.conform_epub(epub)
    with zipfile.ZipFile(epub) as z:
        assert z.namelist() == before
        first = z.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED


def test_conform_on_an_already_clean_book_changes_nothing(tmp_path):
    epub = tmp_path / "b.epub"
    _epub(epub, {"EPUB/text/ch001.xhtml": "<html><body><p>Plain.</p></body></html>"})
    before = epub.read_bytes()
    b.conform_epub(epub)
    assert epub.read_bytes() == before


def test_every_document_is_swept_not_only_the_first(tmp_path):
    """A per-document fix has to reach every document, or the later chapters
    stay invalid while the first one looks repaired."""
    epub = tmp_path / "b.epub"
    docs = {
        f"EPUB/text/ch{i:03d}.xhtml": f"<html><style>{FACE}</style><body>{FIGURE}</body></html>"
        for i in range(1, 6)
    }
    _epub(epub, docs)
    b.conform_epub(epub)
    with zipfile.ZipFile(epub) as z:
        for name in docs:
            text = z.read(name).decode()
            # FACE carries a url that resolves nowhere, so it goes everywhere.
            assert "@font-face" not in text
            assert not re.search(r"<div[^>]*\balt=", text)
            assert text.count("<img") == 1
