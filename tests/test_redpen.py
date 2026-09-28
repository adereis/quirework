"""Hermetic tests for `quirework publish redpen` (synthetic .docx, no Word, no corpus).

`redpen read` parses an OOXML zip; the parser only touches word/document.xml and
word/comments.xml, so a two-entry zip is a sufficient fixture. The deletion
fixture nests the comment range inside the ``w:del`` exactly as Google Docs
exports a "comment on a suggestion", so the edit<->rationale pairing is tested."""

import zipfile
from pathlib import Path

import pytest

from conftest import patch_project_path
from quirework.project import Project, project_scope
from quirework.publish import redpen

# The review-notes file a project declares; every chapter's block carries
# the notes it names.
BOOK_NOTES_FILE = "_review-notes.md"


def project_at(root):
    """A Quarto book laid out as the configuration guide teaches, at `root`."""
    return Project.from_settings(
        root,
        {
            "manuscript": {
                "source": "docs",
                "chapters": "docs/canon",
                "quarto": "docs/_quarto.yml",
                "review_notes": f"docs/canon/{BOOK_NOTES_FILE}",
            },
            "publish": {"build": "build", "figures": "docs/canon/illustrations"},
        },
    )


@pytest.fixture(autouse=True)
def quarto_book(tmp_path):
    """A selected book with one titled chapter, for the author's block to
    name. Tests that build their own tree select it with `project_at`."""
    root = tmp_path / "selected"
    (root / "docs" / "canon").mkdir(parents=True)
    (root / "docs" / "_quarto.yml").write_text(
        "book:\n  chapters:\n    - index.qmd\n    - canon/the-harbor.qmd\n"
    )
    (root / "docs" / "index.qmd").write_text("# Welcome\n")
    (root / "docs" / "canon" / "the-harbor.qmd").write_text("# The harbor\n")
    with project_scope(project_at(root)):
        yield


NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _p(text, style=None):
    pr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{pr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def _p_ins(text):
    return (
        f'<w:p><w:ins w:id="1" w:author="A" w:date="D">'
        f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:ins></w:p>'
    )


def _p_del_commented(deltext, cid):
    """A tracked deletion with a comment range nested inside it (Google style)."""
    return (
        f'<w:p><w:del w:id="2" w:author="A" w:date="D">'
        f'<w:commentRangeStart w:id="{cid}"/>'
        f'<w:r><w:delText xml:space="preserve">{deltext}</w:delText></w:r></w:del>'
        f'<w:commentRangeEnd w:id="{cid}"/>'
        f'<w:r><w:commentReference w:id="{cid}"/></w:r></w:p>'
    )


def _sourced(root, source):
    """The fixture book with `manuscript.source` moved to another tree.

    The source↔stage split is configuration: the sidecars are read from
    `manuscript.source` while the block is injected into whatever tree is
    staged."""
    from quirework.project import Project

    project = project_at(root)
    settings = project.describe()["settings"]
    settings["manuscript"]["source"] = str(source)
    settings["manuscript"]["chapters"] = str(Path(source) / "canon")
    return Project.from_settings(root, settings)


def _make_docx(path, body_paras, comments_xml=""):
    doc = f"<w:document {NS}><w:body>{''.join(body_paras)}</w:body></w:document>"
    comments = f"<w:comments {NS}>{comments_xml}</w:comments>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", doc)
        z.writestr("word/comments.xml", comments)


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #
def test_source_index_skips_noise_and_locates(tmp_path):
    qmd = tmp_path / "ch.qmd"
    qmd.write_text(
        "# Heading\n\n"
        "<!-- @bind(x) -->\n"
        "A real prose sentence here.\n"
        "![cap](figures/x.svg){#fig-x}\n"
        "Another prose sentence.\n"
    )
    idx = redpen.source_index(qmd)
    texts = [t for _, _, t in idx]
    assert "A real prose sentence here." in texts
    assert not any("Heading" in t or "@bind" in t or "figures/" in t for t in texts)
    assert redpen.locate("A real prose sentence here.", idx) == "ch.qmd:4"


# --------------------------------------------------------------------------- #
# extract end-to-end on a synthetic review
# --------------------------------------------------------------------------- #
def _review(tmp_path):
    qmd = tmp_path / "ch.qmd"
    qmd.write_text(
        "# Chapter\n\nAn untouched sentence.\nThat doubling is the subject.\n"
    )
    docx = tmp_path / "reviewed.docx"
    _make_docx(
        docx,
        [
            _p("ch.qmd"),  # title paragraph (skipped)
            _p("Chapter", style="Heading1"),  # heading (skipped)
            _p_ins("Core concept"),  # concept block label
            _p_ins("Seed line one."),
            _p_ins("Seed line two."),
            _p_del_commented("That doubling is the subject.", cid=0),
        ],
        comments_xml=(
            '<w:comment w:id="0" w:author="A. Reviewer" w:date="2026-07-03T00:00:00Z">'
            "<w:p><w:r><w:t>unnecessary</w:t></w:r></w:p></w:comment>"
        ),
    )
    return redpen.extract(docx, redpen.source_index(qmd))


def test_extract_pairs_comment_with_deletion(tmp_path):
    rev = _review(tmp_path)
    assert len(rev.comments) == 1
    c = rev.comments[0]
    assert c.on == "del"  # comment is on the deletion
    assert "That doubling is the subject." in c.anchor  # anchored to the deleted text
    assert c.note == "unnecessary"
    assert c.source == "ch.qmd:4"  # mapped to the source line


def test_extract_redlines(tmp_path):
    rev = _review(tmp_path)
    # the deletion shows as a redline mapped to its source line
    dels = [e for e in rev.edits if "⟦-" in e.redline]
    assert any(e.source == "ch.qmd:4" and "That doubling" in e.redline for e in dels)
    # inserted paragraphs at the top are unmapped (new) tracked edits; the
    # retired labeled-block channel used to route them into a @concept mark
    assert any(e.source == "new" and "Core concept" in e.redline for e in rev.edits)


def test_read_missing_file_errors(tmp_path):
    assert redpen.read(tmp_path / "nope.docx", tmp_path) == 2


# --------------------------------------------------------------------------- #
# review-notes block: build-side injection and read-side routing
# --------------------------------------------------------------------------- #
def test_render_notes_block_empty_has_placeholder():
    block = redpen.render_notes_block([], [], [])
    assert redpen.NOTES_START in block and redpen.NOTES_END in block
    assert "(none yet; add here)" in block
    # the author-facing sections
    assert (
        "Concepts:" not in block and "Guidance:" in block and "Variant notes:" in block
    )
    assert "Genesis:" in block
    assert block.index("Guidance:") < block.index(
        "Genesis:"
    )  # genesis follows guidance
    assert (
        "Claim:" not in block and "Decisions:" not in block
    )  # the dense/working layers stay out


def test_inject_review_notes(tmp_path):
    stage = tmp_path / "docs"
    canon = stage / "canon"
    (canon / "_marks").mkdir(parents=True)
    (stage / "_quarto.yml").write_text(
        "book:\n  chapters:\n    - canon/ch.qmd\n    - canon/nohead.qmd\n"
    )
    # the prose .qmd is mark-free; the marks live in the sidecar, mirroring the H1
    (canon / "ch.qmd").write_text("# The harbor {#sec-harbor}\n\nReal prose here.\n")
    (canon / "_marks" / "ch.md").write_text(
        "# The harbor {#sec-harbor}\n\n"
        "<!-- @guidance open cold on the crashed ship; keep the wetness image -->\n"
        "<!-- @genesis the ship comes from the author's dark science-fiction touchstones -->\n"
        "<!-- @bind(harbor) -->\n"
        "<!-- @todo sharpen the lookup-table definition -->\n"
        "<!-- @variant(swe) the k8s self-monitoring example lives here -->\n"
    )
    (canon / "nohead.qmd").write_text("Just prose, no heading.\n")

    # reading order comes from the staged config; the sidecars come from the
    # configured source, which here is the same tree
    with project_scope(project_at(tmp_path)):
        assert redpen.inject_review_notes(stage) == 1  # only the chapter with an H1

    out = (canon / "ch.qmd").read_text()
    assert redpen.NOTES_START in out and redpen.NOTES_END in out
    block = out[out.index(redpen.NOTES_START) : out.index(redpen.NOTES_END)]
    assert "open cold on the crashed ship" in block  # @guidance surfaced
    assert "dark science-fiction touchstones" in block  # @genesis surfaced
    assert (
        "(swe) the k8s self-monitoring example" in block
    )  # @variant surfaced with its kind
    # the binding and working-state marks are NOT in the author-facing block
    assert "@bind" not in block
    assert "sharpen the lookup-table definition" not in block
    # the injected block sits after the H1 and before the prose, and the prose .qmd
    # itself never gained a mark (they stayed in the sidecar)
    assert (
        out.index("# The harbor")
        < out.index(redpen.NOTES_START)
        < out.index("Real prose here.")
    )
    assert "<!-- @" not in out.replace(redpen.NOTES_START, "").replace(
        redpen.NOTES_END, ""
    )
    # a file without an H1 is left untouched
    assert (canon / "nohead.qmd").read_text() == "Just prose, no heading.\n"


def test_inject_reads_marks_from_source_when_stage_has_no_sidecar(
    tmp_path, monkeypatch
):
    # the split's source↔stage coupling: sidecars live only in the SOURCE tree
    # (render mirrors exclude _marks/), so the build path must read them from
    # source while injecting into a staged .qmd that has no _marks/ beside it.
    src = tmp_path / "src" / "canon"
    (src / "_marks").mkdir(parents=True)
    (src / "ch.qmd").write_text("# Harbor {#sec-x}\n\nProse.\n")
    (src / "_marks" / "ch.md").write_text(
        "# Harbor {#sec-x}\n\n<!-- @guidance keep the harbor plain -->\n"
    )
    (src / BOOK_NOTES_FILE).write_text("")

    stage = tmp_path / "stage"
    (stage / "canon").mkdir(parents=True)
    (stage / "_quarto.yml").write_text("book:\n  chapters:\n    - canon/ch.qmd\n")
    (stage / "canon" / "ch.qmd").write_text(
        "# Harbor {#sec-x}\n\nProse.\n"
    )  # deliberately NO _marks/ here
    (stage / "canon" / BOOK_NOTES_FILE).write_text("")

    # book.source names the tree the sidecars live in; the stage is a copy
    monkeypatch.setattr(
        redpen, "current_project", lambda: _sourced(tmp_path, src.parent)
    )
    assert redpen.inject_review_notes(stage) == 1
    out = (stage / "canon" / "ch.qmd").read_text()
    assert redpen.NOTES_START in out
    assert (
        "keep the harbor plain" in out
    )  # pulled from the SOURCE sidecar, not the stage


def test_read_routes_review_note_edits(tmp_path):
    qmd = tmp_path / "ch.qmd"
    qmd.write_text("# The harbor\n\nSome prose.\n")
    docx = tmp_path / "reviewed.docx"
    _make_docx(
        docx,
        [
            _p("The harbor", style="Heading1"),
            _p(f"⟨ {redpen.NOTES_START} ⟩"),
            _p_ins("the tide is the harbor breathing"),  # author's in-place edit
            _p(f"⟨ {redpen.NOTES_END} ⟩"),
            _p("Some prose."),
        ],
    )
    rev = redpen.extract(docx, redpen.source_index(qmd))

    assert len(rev.notes_edits) == 1
    chapter, redline = rev.notes_edits[0]
    assert chapter == "The harbor"  # attributed to its chapter heading
    assert "the tide is the harbor" in redline
    # the block edit is NOT double-reported as a prose redline
    assert not any("the tide is the harbor" in e.redline for e in rev.edits)


# --------------------------------------------------------------------------- #
# book-wide review notes: manifest, @see pointers, top-of-document injection
# --------------------------------------------------------------------------- #
_BOOK_NOTE = (
    "<!-- @guidance(em-dashes)  no em-dashes anywhere in the book; readers "
    "treat them as an AI tell, so sweep every chapter before each "
    "red-pen round -->\n"
)


def test_render_book_notes_block_has_own_sentinel_and_placeholder():
    block = redpen.render_book_notes_block([], [], [])
    assert redpen.NOTES_START_BOOK in block and redpen.NOTES_END in block
    assert redpen.NOTES_START not in block.replace(
        redpen.NOTES_START_BOOK, ""
    )  # only the book sentinel
    assert "(none yet; add here)" in block
    assert "across the whole book" in block  # the book-wide intro


def test_see_pointers_resolve_echo_and_flag_dangling():
    by_slug = redpen._book_notes_by_slug(
        redpen._notes.scan_text(_BOOK_NOTE, BOOK_NOTES_FILE)
    )
    chap = redpen._notes.scan_text(
        "# Ch\n"
        "<!-- @see(em-dashes)  the long dialogue here is worst -->\n"
        "<!-- @see(nope)  points at nothing -->\n",
        "ch.qmd",
    )
    ptrs = redpen._see_pointers(chap, by_slug)
    resolved = next(p for p in ptrs if "em-dashes" in p)
    assert "no em-dashes anywhere" in resolved  # short echo of the note
    assert "the long dialogue here is worst" in resolved  # the @see rider rides along
    assert "before each red-pen round" not in resolved  # NOT the note in full
    assert "@guidance" not in resolved  # no internal kind token
    assert any(
        "nope" in p and "no book-wide note" in p for p in ptrs
    )  # dangling flagged


def test_see_pointer_inherits_its_targets_audience():
    """A @see at an AI-facing note (@bind/@todo) never reaches the review copy:
    the block's audience split has to hold for pointers as well as for marks, or
    the See also list names notes the document never printed."""
    book = _BOOK_NOTE + (
        "<!-- @bind(vector-gauge) -->\n"
        "<!-- @todo(v1-priorities)  the open work before v1.0 -->\n"
    )
    by_slug = redpen._book_notes_by_slug(redpen._notes.scan_text(book, BOOK_NOTES_FILE))
    chap = redpen._notes.scan_text(
        "# Ch\n"
        "<!-- @see(em-dashes)  human-facing target, shows -->\n"
        "<!-- @see(vector-gauge)  AI-facing target, dropped -->\n"
        "<!-- @see(v1-priorities)  working state, dropped -->\n",
        "ch.qmd",
    )
    ptrs = redpen._see_pointers(chap, by_slug)
    assert len(ptrs) == 1 and "em-dashes" in ptrs[0]
    assert not any("vector-gauge" in p or "v1-priorities" in p for p in ptrs)


def test_inject_book_and_front_matter_blocks_whole_book(tmp_path):
    """Every configured prose file gets its own block, front matter included.

    The preface used to be named in the tool by filename, because it is the one
    chapter that sits outside `canon/`. Reading order answers that now: the
    injector walks `_quarto.yml`, so the preface is an ordinary entry and the
    welcome landing is simply the first one."""
    stage = tmp_path / "docs"
    canon = stage / "canon"
    (canon / "_marks").mkdir(parents=True)
    (canon / BOOK_NOTES_FILE).write_text(_BOOK_NOTE)
    (stage / "_quarto.yml").write_text(
        "book:\n  chapters:\n    - index.qmd\n    - preface.qmd\n    - canon/01-x.qmd\n"
    )
    # chapter prose is mark-free; its @see lives in the sidecar
    (canon / "01-x.qmd").write_text("# Chapter One {#sec-x}\n\nProse.\n")
    (canon / "_marks" / "01-x.md").write_text(
        "# Chapter One {#sec-x}\n\n<!-- @see(em-dashes)  here -->\n"
    )
    # index.qmd is the welcome landing (Quarto force-pins it first, so the
    # book-wide block lands here); the preface is its own file, with its sidecar
    # a level up (docs/_marks/preface.md).
    (stage / "index.qmd").write_text(
        "# Welcome {.unnumbered .unlisted}\n\nWelcome prose.\n"
    )
    (stage / "_marks").mkdir()
    (stage / "preface.qmd").write_text("# Preface {.unnumbered}\n\nPreface prose.\n")
    (stage / "_marks" / "preface.md").write_text(
        "# Preface {.unnumbered}\n\n<!-- @guidance keep the invitation minimal -->\n"
    )

    with project_scope(project_at(tmp_path)):
        assert (
            redpen.inject_review_notes(stage) == 3
        )  # the welcome, the preface, the chapter
        assert redpen.inject_book_block(stage, None) is True

    # the book-wide block lands at the top of the document = the welcome (index.qmd)
    welcome = (stage / "index.qmd").read_text()
    assert redpen.NOTES_START_BOOK in welcome
    assert (
        welcome.index("before each red-pen round")  # full note lives in the book block
        < welcome.index("Welcome prose.")
    )

    # the preface gets its OWN block (not the book-wide one), above its prose
    pref = (stage / "preface.qmd").read_text()
    assert redpen.NOTES_START_BOOK not in pref  # the book-wide block is not here
    assert (
        pref.index("keep the invitation minimal")  # preface's own guidance
        < pref.index("Preface prose.")
    )

    ch = (canon / "01-x.qmd").read_text()
    assert "See also" in ch and "no em-dashes anywhere" in ch  # a pointer, echoed
    assert "before each red-pen round" not in ch  # never the note in full


def test_inject_book_block_into_single_chapter(tmp_path):
    stage = tmp_path / "docs"
    canon = stage / "canon"
    canon.mkdir(parents=True)
    (stage / "_quarto.yml").write_text("book:\n  chapters:\n    - canon/07-x.qmd\n")
    (canon / BOOK_NOTES_FILE).write_text(
        "<!-- @guidance(throughline)  earn the lighthouse -->\n"
    )
    (canon / "07-x.qmd").write_text("# Ch Seven {#sec-x}\n\nProse.\n")

    with project_scope(project_at(tmp_path)):
        redpen.inject_review_notes(stage)  # the chapter's own block first
        assert redpen.inject_book_block(stage, "07-x") is True

    ch = (canon / "07-x.qmd").read_text()
    # book-wide block sits under the H1 and above the chapter's own block
    assert ch.index("# Ch Seven") < ch.index(redpen.NOTES_START_BOOK)
    assert ch.index("earn the lighthouse") < ch.index("Prose.")
    assert redpen.NOTES_START in ch  # the chapter's own block is still there


def test_inject_book_block_absent_manifest_is_noop(tmp_path):
    stage = tmp_path / "docs"
    (stage / "canon").mkdir(parents=True)
    (stage / "_quarto.yml").write_text("book:\n  chapters:\n    - index.qmd\n")
    (stage / "index.qmd").write_text("# Welcome {.unnumbered .unlisted}\n\nProse.\n")
    with project_scope(project_at(tmp_path)):
        assert (
            redpen.inject_book_block(stage, None) is False
        )  # no manifest, nothing injected


def _rendered_block_lines(qmd_text, fname="ch.qmd"):
    """The chapter block's item lines as the build would render them from source."""
    annos = redpen._notes.scan_text(qmd_text, fname)
    md = redpen.render_notes_block(
        redpen._anno_lines(annos, "guidance"),
        redpen._anno_lines(annos, "genesis"),
        redpen._anno_lines(annos, "variant"),
    )
    return redpen._block_lines(md)


def _canon(tmp_path):
    """A source tree shaped like the configured one: `docs/canon/` under a root.

    The layout is load-bearing now. `note_content_diffs` resolves a document
    heading by walking up from the chapter directory to the source root and
    globbing it, so a fixture written straight into `tmp_path` puts the whole
    pytest scratch tree in scope and the neighbouring test's "Test Chapter"
    answers first."""
    canon = tmp_path / "docs" / "canon"
    (canon / "_marks").mkdir(parents=True)
    return canon


def _docx_with_block(path, heading, block_lines, trailing="Prose."):
    paras = [_p(heading, style="Heading1"), _p(f"⟨ {redpen.NOTES_START} ⟩")]
    paras += [_p(x) for x in block_lines]
    paras += [_p(f"⟨ {redpen.NOTES_END} ⟩"), _p(trailing)]
    _make_docx(path, paras)


def test_review_note_content_recovers_free_edits(tmp_path):
    canon = _canon(tmp_path)
    (canon / "ch.qmd").write_text("# Test Chapter {#sec-x}\n\nProse.\n")
    (canon / "_marks" / "ch.md").write_text(
        "# Test Chapter {#sec-x}\n\n"
        "<!-- @guidance keep this guidance unchanged -->\n"
        "<!-- @guidance original wording -->\n"
        "<!-- @genesis an origin note -->\n"
    )
    src_lines = _rendered_block_lines((canon / "_marks" / "ch.md").read_text())

    # the author's *plain-mode* edits to the block: reword guidance, strike the
    # genesis note, add new guidance — no tracked changes at all.
    doc_lines = []
    for l in src_lines:
        if l == "original wording":
            doc_lines.append("reworded guidance")  # reworded in place
        elif l == "an origin note":
            continue  # struck
        else:
            doc_lines.append(l)
    doc_lines.append("brand new guidance")  # freshly typed

    docx = tmp_path / "reviewed.docx"
    _docx_with_block(docx, "Test Chapter", doc_lines)
    rev = redpen.extract(docx, redpen.source_index(canon))

    # the block's current text is captured even though nothing is a tracked change
    assert rev.notes_edits == []  # no ins/del -> the old channel is empty
    assert rev.notes_content and rev.notes_content[0][0] == "Test Chapter"

    diffs = redpen.note_content_diffs(rev.notes_content, canon)
    assert len(diffs) == 1
    owner, removed, added, resolved = diffs[0]
    assert resolved is True and owner == "Test Chapter"
    assert "original wording" in removed and "an origin note" in removed
    assert "reworded guidance" in added and "brand new guidance" in added
    # the untouched note appears in neither delta
    assert all("keep this guidance unchanged" not in side for side in (removed, added))
    # and it surfaces in the read output under its own channel
    assert "REVIEW-NOTES FREE EDITS" in redpen.format_note_content_diffs(
        rev.notes_content, canon
    )


def test_note_content_diffs_unchanged_block_is_silent(tmp_path):
    canon = _canon(tmp_path)
    (canon / "ch.qmd").write_text("# Test Chapter {#sec-x}\n\nProse.\n")
    (canon / "_marks" / "ch.md").write_text(
        "# Test Chapter {#sec-x}\n\n<!-- @guidance a note -->\n"
    )
    doc_lines = _rendered_block_lines(
        (canon / "_marks" / "ch.md").read_text()
    )  # left exactly as built
    docx = tmp_path / "reviewed.docx"
    _docx_with_block(docx, "Test Chapter", doc_lines)
    rev = redpen.extract(docx, redpen.source_index(canon))
    assert (
        redpen.note_content_diffs(rev.notes_content, canon) == []
    )  # no edits -> no noise
    assert redpen.format_note_content_diffs(rev.notes_content, canon) == ""


def test_owner_resolves_through_quarto_numbering_and_smart_quotes(tmp_path):
    # a whole-book render numbers the heading ("3. …") and Google Docs rewrites
    # ' -> ’ on export; an untouched block must still resolve and stay silent.
    canon = _canon(tmp_path)
    (canon / "ch.qmd").write_text("# The keeper's walk {#sec-x}\n\nProse.\n")
    (canon / "_marks" / "ch.md").write_text(
        "# The keeper's walk {#sec-x}\n\n"
        "<!-- @guidance don't let the flow-is-peak reading creep back -->\n"
    )
    src_lines = _rendered_block_lines((canon / "_marks" / "ch.md").read_text())
    doc_lines = [l.replace("'", "’") for l in src_lines]  # smart apostrophes
    docx = tmp_path / "r.docx"
    _docx_with_block(docx, "3. The keeper’s walk", doc_lines)  # numbered, smart heading
    rev = redpen.extract(docx, redpen.source_index(canon))
    # resolves the numbered heading AND folds the smart quotes -> no phantom diff
    assert redpen.note_content_diffs(rev.notes_content, canon) == []


def test_real_edit_survives_numbering_and_quote_folding(tmp_path):
    canon = _canon(tmp_path)
    (canon / "ch.qmd").write_text("# The keeper's walk {#sec-x}\n\nProse.\n")
    (canon / "_marks" / "ch.md").write_text(
        "# The keeper's walk {#sec-x}\n\n<!-- @guidance keep this one -->\n"
    )
    src_lines = _rendered_block_lines((canon / "_marks" / "ch.md").read_text())
    doc_lines = [l.replace("'", "’") for l in src_lines] + ["a freshly typed note"]
    docx = tmp_path / "r.docx"
    _docx_with_block(docx, "3. The keeper’s walk", doc_lines)
    rev = redpen.extract(docx, redpen.source_index(canon))
    diffs = redpen.note_content_diffs(rev.notes_content, canon)
    assert len(diffs) == 1
    owner, removed, added, resolved = diffs[0]
    assert resolved is True and removed == [] and "a freshly typed note" in added


def test_read_routes_book_block_edits_to_book_label(tmp_path):
    qmd = tmp_path / "index.qmd"
    qmd.write_text("Welcome prose.\n")
    docx = tmp_path / "reviewed.docx"
    _make_docx(
        docx,
        [
            _p("Welcome", style="Heading1"),  # book-wide block sits under the welcome
            _p(f"⟨ {redpen.NOTES_START_BOOK} ⟩"),
            _p_ins("sweep for em-dashes before v1"),
            _p(f"⟨ {redpen.NOTES_END} ⟩"),
            _p("Welcome prose."),
        ],
    )
    rev = redpen.extract(docx, redpen.source_index(qmd))
    assert len(rev.notes_edits) == 1
    owner, redline = rev.notes_edits[0]
    assert owner == redpen.BOOK_NOTES_LABEL  # routes to _review-notes.md, not "Preface"
    assert "sweep for em-dashes" in redline


def test_locate_figure_maps_to_embed(tmp_path):
    qmd = tmp_path / "ch.qmd"
    qmd.write_text(
        "# Chapter\n\n"
        "Prose.\n"
        "![The same table, run on the boats we build.](figures/tide-table.svg){#fig-x}\n"
    )
    fig_idx = redpen.figure_index(qmd)
    where = redpen.locate_figure(
        "〔figure〕 / Figure 11.1: The same table, run on the boats we build. The harbor",
        fig_idx,
    )
    assert where == "ch.qmd:4"


# --------------------------------------------------------------------------- #
# redpen notes: verbatim block dump, grouped under the block's own captions
# --------------------------------------------------------------------------- #
def _p_list(text, level=0):
    """A bullet paragraph at nesting `level` (Google encodes depth in w:ilvl)."""
    return (
        f'<w:p><w:pPr><w:numPr><w:ilvl w:val="{level}"/>'
        f'<w:numId w:val="1"/></w:numPr></w:pPr>'
        f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
    )


def _p_list_insdel(kept, ins, deltext, level=0):
    """A bullet carrying a tracked insertion and a tracked deletion."""
    return (
        f'<w:p><w:pPr><w:numPr><w:ilvl w:val="{level}"/></w:numPr></w:pPr>'
        f'<w:r><w:t xml:space="preserve">{kept}</w:t></w:r>'
        f'<w:ins w:id="1" w:author="A" w:date="D">'
        f'<w:r><w:t xml:space="preserve">{ins}</w:t></w:r></w:ins>'
        f'<w:del w:id="2" w:author="A" w:date="D">'
        f'<w:r><w:delText xml:space="preserve">{deltext}</w:delText></w:r></w:del></w:p>'
    )


def test_review_note_sections_verbatim_nesting_and_tracked_changes(tmp_path):
    # a numbered whole-book heading, a smart apostrophe, a nested bullet, and a
    # bullet with a tracked ins + del — the shape `redpen notes` must reproduce.
    docx = tmp_path / "reviewed.docx"
    _make_docx(
        docx,
        [
            _p("2. Reading the tide", style="Heading1"),
            _p(f"⟨ {redpen.NOTES_START} ⟩"),
            _p("Variant notes: what a young-reader edition does here."),
            _p_list("It’s a gauge, not a switch.", level=0),  # smart '’' -> "'"
            _p_list("A sub-point.", level=1),  # nested one deeper
            _p("Guidance: how it should read."),
            _p_list_insdel("keep this", " and add this", " drop this", level=0),
            _p("Genesis: where it came from."),
            _p_list("Grew up watching the tides with a parent.", level=0),
            _p(f"⟨ {redpen.NOTES_END} ⟩"),
            _p("Prose."),
        ],
    )
    body, _ = redpen.load(docx)
    secs = redpen.review_note_sections(body)

    assert len(secs) == 1
    owner, caps = secs[0]
    assert owner == "2. Reading the tide"  # heading kept verbatim (numbered)
    by = dict(caps)
    # variant notes: typography folded, nesting preserved, verbatim words
    assert by["variant notes"] == [
        (0, "It's a gauge, not a switch."),
        (1, "A sub-point."),
    ]
    # guidance: insertion accepted, deletion dropped
    assert by["guidance"] == [(0, "keep this and add this")]
    # genesis is its own section, not folded into guidance
    assert by["genesis"] == [(0, "Grew up watching the tides with a parent.")]

    out = redpen.format_notes(secs)
    assert "2. Reading the tide" in out
    assert "    - It's a gauge, not a switch." in out  # level 0
    assert "      - A sub-point." in out  # level 1: two extra spaces
    assert redpen.notes(docx) == 0  # command runs clean
    assert redpen.main(["notes", str(docx)]) == 0  # and dispatches from the CLI


def test_review_note_sections_book_label_and_intro(tmp_path):
    docx = tmp_path / "book.docx"
    _make_docx(
        docx,
        [
            _p(f"⟨ {redpen.NOTES_START_BOOK} ⟩"),  # contains NOTES_START; must win
            _p("These notes apply across the whole book."),  # intro, before any caption
            _p("Guidance: the book's throughline."),
            _p_list("The book rhymes with itself.", level=0),
            _p(f"⟨ {redpen.NOTES_END} ⟩"),
        ],
    )
    body, _ = redpen.load(docx)
    owner, caps = redpen.review_note_sections(body)[0]
    assert owner == redpen.BOOK_NOTES_LABEL
    assert caps[0] == (
        "",
        [(0, "These notes apply across the whole book.")],
    )  # intro under ""
    assert dict(caps)["guidance"] == [(0, "The book rhymes with itself.")]


# --------------------------------------------------------------------------- #
# double_figures: --gray appends each figure's grayscale twin under the color one
# --------------------------------------------------------------------------- #
def test_double_figures_appends_gray_twin_and_preserves_embed(tmp_path):
    from quirework.publish import figures

    canon = tmp_path / "canon"
    canon.mkdir()
    embed = '![A caption.](illustrations/the-gauge.svg){#fig-gauge fig-alt="a gauge"}'
    (canon / "ch.qmd").write_text(
        f"# Title\n\nProse before.\n\n{embed}\n\nProse after.\n"
    )

    assert redpen.double_figures(canon) == 1
    out = (canon / "ch.qmd").read_text()
    # the color embed line survives byte-for-byte (caption, #fig- anchor, alt)
    assert embed in out
    # the gray twin is a bare, uncaptioned image so it takes no figure number
    assert f"![]({figures.GRAY_GALLERY}/the-gauge.svg)" in out
    # and it sits below the color embed, with the label under it
    assert out.index(embed) < out.index(f"{figures.GRAY_GALLERY}/the-gauge.svg")
    assert "grayscale twin" in out
    # surrounding prose is undisturbed
    assert "Prose before." in out and "Prose after." in out


def test_double_figures_leaves_non_color_lines_alone(tmp_path):
    canon = tmp_path / "canon"
    canon.mkdir()
    # a gray line (as the pass itself would add) and a prose line that merely
    # mentions the gallery must not be matched -> the pass is not self-amplifying
    body = (
        "# Title\n\n"
        "![](illustrations-gray/x.svg)\n\n"
        "See illustrations/x.svg in the text, not an embed.\n"
    )
    (canon / "ch.qmd").write_text(body)
    assert redpen.double_figures(canon) == 0
    assert (canon / "ch.qmd").read_text() == body


def test_source_index_follows_reading_order_not_the_filesystem(tmp_path):
    """`locate` takes the first match, so iteration order decides which
    chapter a shared sentence is blamed on. Alphabetically `a-quiet-cove`
    precedes `the-harbor`; in the book it does not."""
    from quirework.publish import redpen as _r

    stage = tmp_path
    canon = stage / "canon"
    canon.mkdir()
    (stage / "_quarto.yml").write_text(
        "book:\n  chapters:\n    - canon/the-harbor.qmd\n    - canon/a-quiet-cove.qmd\n"
    )
    (canon / "the-harbor.qmd").write_text("# F\n\nA shared sentence here.\n")
    (canon / "a-quiet-cove.qmd").write_text("# A\n\nA shared sentence here.\n")
    idx = _r.source_index(canon)
    assert _r.locate("A shared sentence here", idx).endswith("the-harbor.qmd:3")


def test_source_index_falls_back_when_the_config_is_unreadable(tmp_path):
    from quirework.publish import redpen as _r

    canon = tmp_path / "canon"
    canon.mkdir()
    (canon / "b.qmd").write_text("# B\n\nbee.\n")
    (canon / "a.qmd").write_text("# A\n\nay.\n")
    idx = _r.source_index(canon)  # no _quarto.yml beside it
    assert [r for r, _, _ in idx] == sorted(r for r, _, _ in idx)


def test_ch_refs_become_chapter_titles_in_the_authors_block():
    """The block is the one thing the author reads, and it is plain prose.
    The source keeps the checkable @unit() form; the review copy gets a name."""
    from quirework.publish import redpen as _r

    out = _r.resolve_ch_refs("the tide carries forward to @unit(the-harbor).")
    assert out == "the tide carries forward to The harbor."


def test_an_unknown_ch_slug_is_left_visible():
    from quirework.publish import redpen as _r

    assert "@unit(no-such-chapter)" in _r.resolve_ch_refs("see @unit(no-such-chapter)")


# --------------------------------------------------------------------------- #
# --out and archive: the durable record that replaces the .docx
# --------------------------------------------------------------------------- #
def _round_docx(tmp_path):
    """A minimal reviewed round: one chapter, one comment on a deletion."""
    qmd = tmp_path / "ch.qmd"
    qmd.write_text(
        "# Chapter\n\nAn untouched sentence.\nThat doubling is the subject.\n"
    )
    docx = tmp_path / "V9 - The-Book.docx"
    _make_docx(
        docx,
        [
            _p("ch.qmd"),
            _p("Chapter", style="Heading1"),
            _p_del_commented("That doubling is the subject.", cid=0),
        ],
        comments_xml=(
            '<w:comment w:id="0" w:author="A. Reviewer" w:date="2026-07-30T00:00:00Z">'
            "<w:p><w:r><w:t>unnecessary</w:t></w:r></w:p></w:comment>"
        ),
    )
    return docx, qmd


def test_read_out_writes_the_marks_instead_of_printing(tmp_path, capsys):
    """An agent redirecting a dump for context hygiene must not destroy it."""
    docx, qmd = _round_docx(tmp_path)
    out = tmp_path / "marks.md"
    assert redpen.read(docx, qmd, out) == 0
    assert "unnecessary" in out.read_text()
    printed = capsys.readouterr().out
    assert "unnecessary" not in printed  # kept out of the terminal
    assert "marks.md" in printed  # but the receipt names the file


def test_notes_out_writes_to_a_file(tmp_path):
    docx, _ = _round_docx(tmp_path)
    out = tmp_path / "notes.md"
    assert redpen.notes(docx, out) == 0
    assert out.exists()


def test_archive_carries_header_both_channels_and_raw_xml(tmp_path):
    docx, qmd = _round_docx(tmp_path)
    out = tmp_path / "v9-2026-07-30.md"
    assert redpen.archive(docx, qmd, out, canon_sha="abc1234") == 0
    text = out.read_text()

    # the header is the join key ccfilter matches on, plus provenance
    assert f"{redpen.SOURCE_DOCX_KEY}:    V9 - The-Book.docx" in text
    assert "canon-sha:      abc1234" in text
    assert "comments:       1" in text
    assert "marks-dated:    2026-07-30" in text
    assert "anchors:        exact 1 / fuzzy 0 / unlocated 0" in text

    # BOTH channels: `read` renders a rewritten block as a delta, which loses
    # the author's words, so `notes` (verbatim) is archived alongside it
    assert "## MARKS" in text and "## REVIEW-NOTES, VERBATIM" in text
    assert "unnecessary" in text

    # the raw XML is what keeps re-extraction possible once the .docx is gone
    raw = out.with_suffix(".raw")
    assert (raw / "document.xml").exists() and (raw / "comments.xml").exists()
    assert "That doubling" in (raw / "document.xml").read_text()


def test_archive_no_raw_skips_the_xml(tmp_path):
    docx, qmd = _round_docx(tmp_path)
    out = tmp_path / "v9.md"
    assert redpen.archive(docx, qmd, out, raw=False) == 0
    assert not out.with_suffix(".raw").exists()


def test_archive_missing_inputs_error(tmp_path):
    _, qmd = _round_docx(tmp_path)
    assert redpen.archive(tmp_path / "nope.docx", qmd, tmp_path / "o.md") == 2
    docx, _ = _round_docx(tmp_path)
    assert redpen.archive(docx, tmp_path / "nodir", tmp_path / "o.md") == 2


def test_reading_a_missing_review_names_the_file(tmp_path, capsys):
    from quirework.cli import main

    root = tmp_path / "book"
    (root / "docs" / "canon").mkdir(parents=True)
    (root / "docs" / "_quarto.yml").write_text(
        "book:\n  chapters:\n    - canon/a.qmd\n"
    )
    (root / "docs" / "canon" / "a.qmd").write_text("# A\n")
    (root / "quirework.toml").write_text(
        'schema = 2\n\n[manuscript]\nsource = "docs"\nchapters = "docs/canon"\n'
        'quarto = "docs/_quarto.yml"\n'
    )
    missing = tmp_path / "no-such-review.docx"
    assert (
        main(["--project", str(root), "publish", "redpen", "read", str(missing)]) == 2
    )
    captured = capsys.readouterr()
    assert "no such file" in captured.out + captured.err
