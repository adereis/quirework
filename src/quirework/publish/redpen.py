"""The red-pen review loop: render a reviewable `.docx`, read the marks back.

The book is refined by red-penning a rendered copy in Google Docs, then applying
the marks into the canonical `.qmd` (canon stays the source of truth). This module
is the mechanical layer for both ends of that loop:

    quirework publish redpen build [--chapter STEM] [--gray]   # book (or a chapter) -> .docx
    quirework publish redpen read  REVIEWED.docx      # extract marks, map them to source
    quirework publish redpen notes REVIEWED.docx      # dump each block verbatim, by chapter
    quirework publish redpen archive REVIEWED.docx --out ARTIFACT.md   # the durable record

``--gray`` doubles every figure in the built copy: beneath each color figure it
appends the **generated grayscale twin** the EPUB and print editions ship (see
``graybuild``), under the one caption, so the author can catch a gray gone muddy
(the one failure ``quirework publish figures --check`` cannot see: it checks the transform
is clean, not that the result still reads). The color embed is left byte-for-byte
intact -- same caption, number, and ``#fig-`` cross-reference -- and the twin is a
bare, unnumbered image there to look at, not to comment onto; a gray fix goes to
the transform, not prose. See ``double_figures``.

`build` also injects a visible **review-notes block** at each section top (bounded
by the ``NOTES_START`` / ``NOTES_END`` sentinels), so the author sees the
work-in-progress while reading and can edit it in place; this happens in the
staged tree only, never in the configured sources. The block is **author-facing**: it carries
the human-readable authorial layers — ``@guidance`` (how it should read),
``@genesis`` (where it came from: why this subject, the inspiration behind it),
and ``@variant`` (what a derived edition does differently here). The bodyless
``@bind`` relationship and the ``@todo`` working state are deliberately
kept *out* of the block (harvested with ``quirework spec notes``, for the agent, not
the review copy). There are three flavors:

* **per-chapter** — each chapter's own ``@guidance`` / ``@genesis`` /
  ``@variant`` marks, plus a *See also* list resolving that chapter's
  ``@see(slug)`` pointers to the book-wide notes (a short echo, not the note in
  full). A pointer inherits its target's audience: only ``@see`` marks resolving
  to the three block kinds appear, so the list can never name a note the document
  did not print (see ``_see_pointers``);
* **book-wide** — a single block at the very top of the document, built from
  ``manuscript.review_notes`` (a never-rendered HTML-comment manifest of
  notes that apply across the whole book). A whole-book note is written once
  there and *referred to* where it bites via ``@see(slug)`` marks, never repeated
  verbatim in every chapter.

A whole-book `build` also **flattens the book's parts** in the staged tree: Word
has no part-divider concept and Quarto drops every ``part:`` from that format,
which would silently take a file-based part's whole opening essay
(``part: sections/part-two.qmd``) with it. See ``flatten_book_parts``.

`read` parses a `.docx` exported from Google Docs and reports these channels,
each located back to a source line:

* **comments** — margin notes; a comment placed *on a tracked change* is paired
  with the exact edit it annotates ("delete X, and here's why"), because Google
  nests the comment range inside the `w:del`/`w:ins`;
* **tracked edits** — shown as paragraph redlines (``⟦-deleted-⟧⟦+inserted+⟧``)
  mapped to the source line range they touch. Edits must be made in Google Docs
  **Suggesting mode** to arrive as tracked changes; plain-mode edits bake into the
  body and are not read here (they can only be recovered by a noisy diff);
* **review-notes edits** — tracked changes made *inside* a review-notes block,
  grouped by the block they belong to (a chapter or ``«book-wide»`` routed to
  ``manuscript.review_notes``), to be folded back into that section's
  ``@guidance`` / ``@genesis`` / ``@variant`` metadata rather than into prose;
* **review-notes free edits** — the review-notes block is *generated* from source,
  so a block edited in **plain mode** (not Suggesting) is still recoverable: its
  current text is diffed against the block freshly rendered from source, and the
  delta (``- was`` / ``+ now`` lines) is reported per block. This is the channel
  for the plain-edit-the-block, comment-the-prose workflow, and it assumes the
  source ``@``-marks have not drifted since the build (a wholesale change is drift,
  not an edit);

`notes` is the companion read for a block the author *rewrote wholesale* (rather
than lightly edited): it dumps every review-notes block's content **verbatim** —
by chapter, grouped by section, list nesting preserved, insertions accepted and
deletions dropped, typography folded to ASCII. `read`'s free-edit channel shows
only the *delta* from source (right for folding small edits back); when a block
carries a full first-person guidance or genesis account, the delta loses the
words and structure, so `notes` reproduces it instead.

Nothing is applied automatically: `read` reports and the operator triages under
the project's procedure. Pure stdlib (zip + ElementTree); the `.docx` is an OOXML zip.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import re
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from quirework.project import current_project
from quirework.manuscript import quarto as _quarto
from quirework.publish import bookstage as _bookstage
from quirework.publish import figures as _figures
from quirework.manuscript import notes as _notes
from quirework.publish.bookstage import cap_docx_image_widths
from quirework.publish.bookstage import copy_gallery
from quirework.publish.bookstage import flatten_book_parts
from quirework.publish.bookstage import unnumber_h1


# where review copies are collected
# The review copy renders from its own stage, never from render's build/book/:
# this stage's sources are MUTATED (review blocks injected, parts flattened,
# figures doubled), and its whole-book render writes a _site of its own — on a
# shared stage that clobbered the publish outputs and cross-contaminated the
# incremental .quarto cache.


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _settings():
    """Resolve paths from the current operation, without import-time state."""
    from types import SimpleNamespace

    CANONICAL = current_project().path("manuscript.chapters")
    SOURCE = current_project().path("manuscript.source")
    REVIEW_NOTES = (
        current_project().path("manuscript.review_notes")
        if current_project().get("manuscript.review_notes") is not None
        else None
    )
    BUILD_REDPEN = current_project().path("publish.build", "build") / "redpen"
    STAGE_REDPEN = current_project().path("publish.build", "build") / "redpen-stage"
    return SimpleNamespace(
        BUILD_REDPEN=BUILD_REDPEN,
        CANONICAL=CANONICAL,
        REVIEW_NOTES=REVIEW_NOTES,
        SOURCE=SOURCE,
        STAGE_REDPEN=STAGE_REDPEN,
    )


def q(tag: str) -> str:
    return W + tag


_WS = re.compile(r"\s+")
# Google Docs rewrites straight quotes/hyphens into typographic forms on export;
# the source is plain ASCII. Fold them back so a docx snippet matches its source
# line (comment mapping) and an unchanged review-notes line does not read as an
# edit (the free-edit diff). Applied on both sides, so it only ever aligns them.
_TYPO = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",  # ‘ ’ ‚ ‛ -> '
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',  # “ ” „ ‟ -> "
        "–": "-",
        "—": "-",  # – — -> -
        "…": "...",
        " ": " ",  # … -> ..., nbsp -> space
    }
)


def _norm(s: str) -> str:
    s = s.translate(_TYPO)
    s = re.sub(r"-{2,}", "-", s)  # source '--' (ASCII em-dash) == a folded '—'
    return _WS.sub(" ", s).strip()


def _fold(s: str) -> str:
    """Like _norm but *without* the hyphen collapse — for reproducing a block's
    text verbatim: fold Google-export typography (smart quotes, em-dash, ellipsis,
    nbsp) to the ASCII the author actually typed and tidy whitespace, but leave
    the words, hyphens, and any real punctuation untouched (no '--'->'-' munging,
    no markdown stripping)."""
    return _WS.sub(" ", s.translate(_TYPO)).strip()


_HEADING_NUM = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+")


def _strip_heading_number(s: str) -> str:
    """Drop Quarto's rendered section number ('9. Where steam escapes' -> 'Where
    steam escapes'), so a whole-book docx heading resolves to its source H1."""
    return _HEADING_NUM.sub("", s).strip()


# Review-notes block: human-facing marks, surfaced as a visible, editable block
# in the review .docx so the author sees what is in progress while reading
# — and can edit it in place. Injected only into the staged tree;
# the sentinels below let `read` recognize the block and route its edits back to
# metadata rather than into prose. The book-wide block has its own start sentinel
# (it contains NOTES_START as a substring, so `read` must test it first) and its
# edits route to _review-notes.md, not to any chapter.
NOTES_START = "review notes · not part of the book · edit these freely"
NOTES_START_BOOK = "book-wide review notes · not part of the book · edit these freely"
NOTES_END = "end review notes"

# The captions rendered at the head of each section inside a block (render_block
# emits them as `*<caption>*`; Google renders the italics away). Matched on their
# opening word(s) so `redpen notes` can group a block's items under their section.
_NOTE_CAPTIONS = (
    "guidance",
    "genesis",
    "variant notes",
    "variant",
    "see also",
)


def _caption_of(low: str) -> str | None:
    """The section a paragraph opens. Longest match wins."""
    for c in sorted(_NOTE_CAPTIONS, key=len, reverse=True):
        if low.startswith(c + ":"):
            return c
    return None


BOOK_NOTES_LABEL = "«book-wide»"

_H1 = re.compile(r"(?m)^# .+$")
_FRONTMATTER = re.compile(r"(?s)\A---\n.*?\n---[ \t]*\n")


def _insert_point(text: str) -> int | None:
    """Char offset just after a section's visible title — a chapter's ``# H1`` or,
    for a file titled only via YAML frontmatter, the closing line of that
    frontmatter. None when the file has neither (nothing to sit under)."""
    fm = _FRONTMATTER.match(text)
    start = fm.end() if fm else 0
    h1 = _H1.search(text, start)
    if h1:
        return h1.end()
    return start if fm else None


# --------------------------------------------------------------------------- #
# review-notes block (build side)
# --------------------------------------------------------------------------- #
def _notes_items(bodies: list[str]) -> str:
    return "\n".join(f"- {b}" for b in bodies) if bodies else "- *(none yet; add here)*"


def _render_block(start: str, intro: str, sections: list[tuple[str, list[str]]]) -> str:
    """Assemble a sentinel-bounded review-notes block from captioned item lists."""
    parts = [f"**⟨ {start} ⟩**"]
    if intro:
        parts.append(intro)
    for caption, items in sections:
        parts.append(f"*{caption}*")
        parts.append(_notes_items(items))
    parts.append(f"**⟨ {NOTES_END} ⟩**")
    return "\n\n".join(parts)


def render_notes_block(
    guidance: list[str],
    genesis: list[str],
    variant: list[str],
    see: list[str] | None = None,
) -> str:
    """The visible review-notes block (Markdown) for one chapter or the preface.
    `see` holds resolved pointers to book-wide notes (echoes, not the full text)."""
    sections = [
        (
            "Guidance: your own words -- how this chapter should read (voice, length, "
            "depth, theme, goals).",
            guidance,
        ),
        (
            "Genesis: where this chapter came from -- why this subject, and the "
            "inspiration behind it.",
            genesis,
        ),
        (
            "Variant notes: what a derived edition should do differently here. "
            "Not part of the canonical book.",
            variant,
        ),
    ]
    if see:
        sections.append(
            (
                "See also: book-wide notes that bite here. They live at the top of "
                "the book; edit them there, not in this list.",
                see,
            )
        )
    return _render_block(NOTES_START, "", sections)


def render_book_notes_block(
    guidance: list[str],
    genesis: list[str],
    variant: list[str],
) -> str:
    """The single book-wide review-notes block."""
    intro = (
        "*These notes apply across the whole book, not to one chapter. A "
        "chapter that leans on one shows a short pointer down in its own "
        "block; the note itself lives here. Add, strike, or revise freely.*"
    )
    sections = [
        ("Guidance: book-wide voice, depth, theme, goals.", guidance),
        ("Genesis: where the book came from -- the inspirations behind it.", genesis),
        (
            "Variant notes: what a derived edition does differently throughout. "
            "Not part of the canonical book.",
            variant,
        ),
    ]
    return _render_block(NOTES_START_BOOK, intro, sections)


@lru_cache(maxsize=8)
def _chapter_titles_at(root: Path) -> dict[str, str]:
    from quirework.manuscript.units import registry

    return {c.slug: c.title for c in registry()}


def _chapter_titles() -> dict[str, str]:
    """The reading order's titles, cached per project.

    Keyed by the selected project's root, never globally: one process can
    select more than one project -- a test suite does, and so does any caller
    that resolves a second book -- and a cache that ignores the selection
    answers the second project with the first one's chapters."""
    return _chapter_titles_at(current_project().root)


def resolve_ch_refs(text: str) -> str:
    """`@ch(the-sidecar)` -> "The sidecar", for the author-facing block only.

    The metadata layer names a chapter by `@ch(slug)` because that is the one
    form a guard can resolve. The three block kinds are a *register*, though,
    not just a filter: they are the only marks the author reads, and they are
    written in plain human prose. A metadata sigil in one is normally miscast;
    the source keeps the checkable `@ch` form and the review
    copy gets the chapter's name, which is what AGENTS.md tells reader-facing
    prose to use anyway. An unknown slug is left verbatim rather than blanked,
    so a bad pointer is visible instead of silently vanishing."""
    titles = _chapter_titles()
    return _notes._CH_REF.sub(lambda m: titles.get(m.group(1), m.group(0)), text)


def _anno_lines(annos, kind: str) -> list[str]:
    """Bodies of `@kind` marks, each prefixed with its `(args)` when present."""
    out = []
    for a in annos:
        if a.kind == kind and a.body:
            out.append(
                resolve_ch_refs((f"({', '.join(a.args)}) " if a.args else "") + a.body)
            )
    return out


def _source_root_for(chapter_dir: Path) -> Path:
    """The source root that contains a configured chapter directory."""
    root = Path(chapter_dir)
    for _ in current_project().chapter_subdir.parts:
        root = root.parent
    return root


def _review_notes_path(chapter_dir: Path) -> Path | None:
    """The configured review manifest mapped into the supplied source tree."""
    configured = _settings().REVIEW_NOTES
    if configured is None:
        return None
    try:
        relative = configured.relative_to(_settings().SOURCE)
    except ValueError:
        return configured
    return _source_root_for(chapter_dir) / relative


def book_notes(chapter_dir: Path):
    """The configured book-wide annotations, or an empty list."""
    f = _review_notes_path(chapter_dir)
    return (
        _notes.scan_text(f.read_text(encoding="utf-8"), f.name)
        if f is not None and f.exists()
        else []
    )


def _sidecar_annos(source: Path):
    """A prose file's annotations from its sibling metadata sidecar."""
    side = source.parent / _notes.MARKS_DIR / f"{source.stem}.md"
    return (
        _notes.scan_text(side.read_text(encoding="utf-8"), source.name)
        if side.exists()
        else []
    )


def _book_notes_by_slug(annos) -> dict:
    """Index book-wide notes by their slug (the first token of the parenthetical),
    so a chapter's @see(slug) can resolve to the note it points at."""
    out: dict = {}
    for a in annos:
        if a.args and a.args[0]:
            out.setdefault(a.args[0], a)
    return out


def _see_pointers(annos, by_slug: dict) -> list[str]:
    """Compact pointers for a section's @see(slug) marks: the book-wide note is
    echoed (a short prefix), never repeated in full. A dangling slug is flagged so
    the author notices it; an optional rider on the @see rides along.

    **A pointer inherits its target's audience.** A @see at a non-block note,
    normally @todo, is agent cross-referencing and is dropped here: it stays
    harvested by `quirework spec notes` but never reaches the review copy. The same
    filter prevents a malformed binding note from leaking through a pointer."""
    out = []
    for a in annos:
        if a.kind != "see":
            continue
        slug = a.args[0] if a.args else ""
        ref = by_slug.get(slug)
        if ref and ref.kind not in _notes.BLOCK_KINDS:
            continue
        if ref:
            body = resolve_ch_refs(ref.body)
            echo = body[:70].rstrip() + ("…" if len(body) > 70 else "")
            line = f"**{slug}** · {echo}"
        elif slug:
            line = f"**{slug}** · ⚠ no book-wide note with this slug (see the top of the book)"
        else:
            line = "⚠ @see with no slug"
        if a.body:
            line += f"  ·  {a.body}"
        out.append(line)
    return out


def _inject_own_block(qmd: Path, by_slug: dict, source: Path) -> bool:
    """Insert a section's own review-notes block at its title anchor (H1, or the
    preface's frontmatter). False when the file has no anchor to sit under. The
    block is built from the chapter's source sidecar (`_sidecar_annos`); the
    injection target is the staged `qmd`."""
    text = qmd.read_text(encoding="utf-8")
    pt = _insert_point(text)
    if pt is None:
        return False
    annos = _sidecar_annos(source)
    block = render_notes_block(
        _anno_lines(annos, "guidance"),
        _anno_lines(annos, "genesis"),
        _anno_lines(annos, "variant"),
        see=_see_pointers(annos, by_slug),
    )
    qmd.write_text(
        text[:pt] + "\n\n" + block + "\n\n" + text[pt:].lstrip("\n"), encoding="utf-8"
    )
    return True


def inject_review_notes(stage: Path) -> int:
    """Insert review blocks into every configured prose file in reading order."""
    chapter_dir = stage / current_project().chapter_subdir
    by_slug = _book_notes_by_slug(book_notes(chapter_dir))
    return sum(
        _inject_own_block(stage / rel, by_slug, _settings().SOURCE / rel)
        for rel in _quarto.book_chapters(stage)
        if (stage / rel).is_file()
    )


_GUIDE_BODY = """\
This is a **review copy** of the book, not the source of truth.
Your marks here are read back by a tool and applied to the canonical
source files after discussion. The document itself is never edited
directly.

**What gets detected**

There are three editing surfaces, each read back differently.

*Main text*

Use **Comments** (Insert > Comment) for observations, anchored to
specific text.

Use **Suggesting mode** (Editing > Suggesting) for edits, so they
arrive as tracked changes. A comment placed on a suggestion pairs the
exact edit with its rationale; this is the richest mark.

**Plain-mode edits in the main text are NOT detected.** If you type
directly without Suggesting mode on, the changes bake into the body
and cannot be read back.

*Review-notes blocks*

Each chapter has a bordered block near its top, marked *review notes
-- not part of the book -- edit these freely.* These contain:

- **Guidance:** your own words for how this chapter should read
  (voice, length, depth, theme, goals). Extend or correct freely.
- **Genesis:** where the chapter came from -- why this subject, the
  inspiration behind it. Your origin story; never an agent's to invent.
- **Variant notes:** what a derived edition should do differently here.
  Not part of the book.

These blocks **can be edited freely in plain mode** (no Suggesting
needed). The tool recovers your changes by comparing against the
source. You can also use Suggesting mode inside them.

*Book-wide block*

At the very top of the document (above the preface or chapter), a
single block holds notes that span the whole book. Edit whole-book
observations there once, not in every chapter.

**When you are done**

1. Download the document as .docx (File > Download > Microsoft Word).
2. Share the file back.

The tool will extract your comments, tracked edits, and any changes
to the review-notes blocks, and map each one back to its source line.
"""


def _first_book_file(stage: Path) -> Path | None:
    """The first configured prose file that exists in a staged book."""
    return next(
        (
            stage / rel
            for rel in _quarto.book_chapters(stage)
            if (stage / rel).is_file()
        ),
        None,
    )


def inject_guide(stage: Path, chapter: str | None) -> bool:
    """Inject the review-workflow guide into the staged tree.

    Whole-book builds use the first configured prose file. Single-chapter
    builds use that chapter. The guide sits after any YAML frontmatter."""
    if chapter:
        stem = chapter[:-4] if chapter.endswith(".qmd") else chapter
        target = stage / current_project().chapter_subdir / f"{stem}.qmd"
    else:
        target = _first_book_file(stage)
    if target is None or not target.exists():
        return False
    text = target.read_text(encoding="utf-8")
    fm = _FRONTMATTER.match(text)
    pt = fm.end() if fm else 0
    body = "\n**How to review this document**\n\n" + _GUIDE_BODY + "\n***\n\n"
    target.write_text(text[:pt] + body + text[pt:], encoding="utf-8")
    return True


def inject_book_block(stage: Path, chapter: str | None) -> bool:
    """Insert the book-wide block at the very top of the document: into the
    first configured prose file for a whole-book build, or
    into the single chapter for a --chapter build (so the book-wide notes ride
    along either way). Runs *after* the own-block injectors, so it lands above
    the target's own block. False when there is no manifest or no target to
    inject into."""
    canonical = stage / current_project().chapter_subdir
    if _review_notes_path(canonical) is None:
        return False
    annos = book_notes(canonical)
    if not annos:
        return False
    block = render_book_notes_block(
        _anno_lines(annos, "guidance"),
        _anno_lines(annos, "genesis"),
        _anno_lines(annos, "variant"),
    )
    if chapter:
        stem = chapter[:-4] if chapter.endswith(".qmd") else chapter
        target = canonical / f"{stem}.qmd"
    else:
        target = _first_book_file(stage)
    if target is None or not target.exists():
        return False
    text = target.read_text(encoding="utf-8")
    pt = _insert_point(text)
    if pt is None:
        return False
    target.write_text(
        text[:pt] + "\n\n" + block + "\n\n" + text[pt:].lstrip("\n"), encoding="utf-8"
    )
    return True


# --------------------------------------------------------------------------- #
# docx parsing
# --------------------------------------------------------------------------- #
def load(docx: Path) -> tuple[ET.Element, dict[str, dict]]:
    """Return the `<w:body>` element and {comment id -> {author,date,text}}."""
    z = zipfile.ZipFile(docx)
    body = ET.fromstring(z.read("word/document.xml")).find(q("body"))
    comments: dict[str, dict] = {}
    if "word/comments.xml" in z.namelist():
        for c in ET.fromstring(z.read("word/comments.xml")).iter(q("comment")):
            comments[c.get(q("id"))] = {
                "author": c.get(q("author")) or "?",
                "date": (c.get(q("date")) or "")[:10],
                "text": _norm("".join(t.text or "" for t in c.iter(q("t")))),
            }
    return body, comments


def segs(el: ET.Element, state: str = ""):
    """Yield (kind, text) in document order; kind is '', 'ins', or 'del'."""
    for c in el:
        tag = c.tag
        if tag == q("ins"):
            yield from segs(c, "ins")
        elif tag == q("del"):
            yield from segs(c, "del")
        elif tag in (q("t"), q("delText")):
            yield (state, c.text or "")
        elif tag == q("drawing"):
            yield (state, "〔figure〕")
        else:
            yield from segs(c, state)


def anchored(body: ET.Element) -> tuple[dict[str, str], dict[str, set], dict[str, str]]:
    """Per comment id: (anchored text incl. deleted text, change-context set,
    nearest preceding text). Recursive so `w:del`/`w:ins` containment is tracked
    while the (flat) comment range markers toggle in document order."""
    active: set[str] = set()
    buf: dict[str, list[str]] = {}
    ctx: dict[str, set] = {}
    near: dict[str, str] = {"_last": ""}

    def walk(el: ET.Element, in_del: bool, in_ins: bool) -> None:
        for c in el:
            tag = c.tag
            if tag == q("commentRangeStart"):
                cid = c.get(q("id"))
                active.add(cid)
                buf.setdefault(cid, [])
                ctx.setdefault(cid, set())
                near[cid] = near["_last"]
            elif tag == q("commentRangeEnd"):
                active.discard(c.get(q("id")))
            elif tag == q("del"):
                walk(c, True, in_ins)
            elif tag == q("ins"):
                walk(c, in_del, True)
            elif tag in (q("t"), q("delText")):
                txt = c.text or ""
                for cid in active:
                    buf[cid].append(txt)
                    ctx[cid].add("del" if in_del else "ins" if in_ins else "text")
                if txt.strip():
                    near["_last"] = txt.strip()
            elif tag == q("drawing"):
                for cid in active:
                    buf[cid].append("〔figure〕")
                    ctx[cid].add("figure")
            else:
                walk(c, in_del, in_ins)
            if tag == q("p"):  # keep paragraph breaks legible inside a comment span
                for cid in active:
                    buf[cid].append(" / ")

    walk(body, False, False)
    return (
        {c: "".join(v) for c, v in buf.items()},
        ctx,
        {k: v for k, v in near.items() if k != "_last"},
    )


# --------------------------------------------------------------------------- #
# source mapping (one sentence per line makes this exact)
# --------------------------------------------------------------------------- #
def _in_reading_order(source: Path) -> list[Path]:
    """The chapter files under `source`, in the book's reading order.

    `locate` takes the FIRST match, so iteration order decides which chapter a
    snippet is blamed on when two of them carry the same sentence -- and that
    misfires in practice: a review procedure recorded a shared phrase being
    reported against one chapter when it belonged to a later one. A
    lexical sort approximated reading order only while the filenames carried
    ordinals, and it did not even do that reliably (`06b` sorts before `06`).
    The slugs carry no order at all, so read it from `_quarto.yml`, which owns
    it -- first-match-in-reading-order is the meaningful tiebreak for a linear
    book, and was the one job the numeric prefix was really doing.

    Falls back to the lexical sort if the config cannot be read: an ambiguous
    blame is a worse outcome than a wrong one only if it stops the build.
    """
    if source.is_file():
        return [source]
    files = sorted(source.glob("*.qmd"))
    try:
        order = _quarto.book_chapters(source.parent)
    except (OSError, ValueError):
        return files
    rank = {Path(r).name: i for i, r in enumerate(order)}
    return sorted(files, key=lambda p: (rank.get(p.name, len(rank)), p.name))


def source_index(source: Path) -> list[tuple[str, int, str]]:
    """(relpath, line, normalized) for prose lines across the source .qmd(s);
    headings, HTML comments, and figure-embed lines are skipped.

    In reading order: see `_in_reading_order`."""
    files = _in_reading_order(source)
    idx: list[tuple[str, int, str]] = []
    for f in files:
        try:
            rel = str(f.relative_to(current_project().root))
        except ValueError:
            rel = f.name
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if not s or s.startswith(("<!--", "#", "![")):
                continue
            idx.append((rel, n, _norm(s)))
    return idx


def locate(text: str, idx) -> str | None:
    """Map a snippet to `relpath:line` (first match) or None."""
    t = _norm(text)
    if len(t) < 4:
        return None
    for rel, n, s in idx:
        if t in s or (len(t) > 12 and t[:40] in s):
            return f"{rel}:{n}"
    return None


def para_range(before: str, idx) -> str | None:
    """Source span for a changed paragraph, from the sentences it retains/deletes.
    A paragraph lies within one chapter, so lo/hi share a file."""
    b = _norm(before)
    hits = [(rel, n) for rel, n, s in idx if len(s) > 6 and s in b]
    if not hits:
        return None
    rel = hits[0][0]
    lines = [n for r, n in hits if r == rel]
    lo, hi = min(lines), max(lines)
    return f"{rel}:{lo}" if lo == hi else f"{rel}:{lo}-{hi}"


def figure_index(source: Path) -> list[tuple[str, int, str]]:
    """(relpath, line, normalized) for figure-embed lines (`![…](illustrations/…)`),
    which `source_index` skips. Lets a comment on a figure map to its embed.

    Same first-match semantics, so the same reading order."""
    files = _in_reading_order(source)
    idx: list[tuple[str, int, str]] = []
    for f in files:
        try:
            rel = str(f.relative_to(current_project().root))
        except ValueError:
            rel = f.name
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if s.startswith("!["):
                idx.append((rel, n, _norm(s)))
    return idx


_FIG_LABEL = re.compile(r"Figure\s+[\d.]+\s*:")


def locate_figure(anchor: str, fig_idx) -> str | None:
    """Map a figure comment's anchor to a `relpath:line` embed. The docx anchor
    reads like `〔figure〕 / Figure 11.1: The same table …`; its caption text is
    the alt text of the source embed line, so match on that."""
    t = _norm(_FIG_LABEL.sub(" ", anchor.replace("〔figure〕", " ").replace("/", " ")))
    if len(t) < 6:
        return None
    key = t[:40]
    for rel, n, s in fig_idx:
        if key in s or t in s:
            return f"{rel}:{n}"
    return None


def _para_style(p: ET.Element) -> str:
    ppr = p.find(q("pPr"))
    st = ppr.find(q("pStyle")) if ppr is not None else None
    return (st.get(q("val")) or "") if st is not None else ""


def _para_level(p: ET.Element) -> int:
    """List nesting depth (`w:pPr/w:numPr/w:ilvl`) of a paragraph, 0 when it is
    not a list item — so a block dump can reproduce the author's bullet nesting."""
    ppr = p.find(q("pPr"))
    numpr = ppr.find(q("numPr")) if ppr is not None else None
    ilvl = numpr.find(q("ilvl")) if numpr is not None else None
    return int(ilvl.get(q("val")) or 0) if ilvl is not None else 0


def review_note_edits(body: ET.Element) -> tuple[set, list[tuple[str, str]]]:
    """Find review-notes blocks (bounded by the sentinels) in the reviewed doc.

    Returns (ids of the block `w:p` paragraphs, to skip in the prose-edit scan;
    list of (owner, redline) for tracked edits made *inside* a block, so the
    author's in-place metadata edits route to sidecars, not prose). The
    owner is the chapter/preface heading, or `«book-wide»` for the book-wide block
    (whose start sentinel is tested first, as it contains NOTES_START)."""
    block_ids: set = set()
    edits: list[tuple[str, str]] = []
    chapter = None
    owner = None  # where the current block's edits route to
    in_block = False
    for p in body.findall(q("p")):
        parts = list(segs(p))
        text = _norm("".join(t for _, t in parts))
        low = text.lower()
        if _para_style(p).lower().startswith("heading") and text:
            chapter = text
        if not in_block:
            if NOTES_START_BOOK in low:
                in_block, owner = True, BOOK_NOTES_LABEL
                block_ids.add(id(p))
            elif NOTES_START in low:
                in_block, owner = True, chapter
                block_ids.add(id(p))
            continue
        block_ids.add(id(p))
        if NOTES_END in low:
            in_block, owner = False, None
            continue
        if any(k in ("ins", "del") for k, _ in parts):
            red = ""
            for k, t in parts:
                red += (
                    ("⟦+" + t + "+⟧")
                    if k == "ins"
                    else ("⟦-" + t + "-⟧")
                    if k == "del"
                    else t
                )
            edits.append((owner or chapter or "?", _norm(red)))
    return block_ids, edits


def review_note_content(body: ET.Element) -> list[tuple[str, list[str]]]:
    """Current *text* of each review-notes block: (owner, [item lines]), tracked
    changes accepted (ins kept, del dropped) and free plain-mode edits included.

    This is how a block edited *without* Suggesting mode is recovered: the block
    is generated from source, so diffing this final text against the block freshly
    rendered from source reveals exactly what the author changed (see
    `note_content_diffs`). The sentinels and blank paragraphs are dropped; item
    text is stripped of its bullet/italic decoration for a clean line-diff."""
    out: list[tuple[str, list[str]]] = []
    chapter = None
    owner = None
    cur: list[str] | None = None
    for p in body.findall(q("p")):
        parts = list(segs(p))
        low = _norm("".join(t for _, t in parts)).lower()
        if _para_style(p).lower().startswith("heading") and low:
            chapter = _norm("".join(t for _, t in parts))
        if cur is None:
            if NOTES_START_BOOK in low:
                owner, cur = BOOK_NOTES_LABEL, []
            elif NOTES_START in low:
                owner, cur = chapter, []
            continue
        if NOTES_END in low:
            out.append((owner or "?", cur))
            cur = None
            continue
        item = _norm_item("".join(t for k, t in parts if k != "del"))
        if item:
            cur.append(item)
    return out


def review_note_sections(body: ET.Element):
    """Each review-notes block's **verbatim** content, structured for display:
    ``[(owner, [(caption, [(level, text), …]), …]), …]``.

    Tracked insertions accepted and deletions dropped; Google-export typography
    folded to ASCII; list nesting preserved (`level`); items grouped under their
    caption (Guidance / Genesis / Variant / See also), with a leading ``""``
    caption for the book-wide block's intro line. Unlike ``review_note_content`` — which
    strips bullet/markdown decoration for a clean line-diff — this keeps the
    author's words intact, so a block rewritten in place can be reproduced
    rather than merely diffed."""
    out: list = []
    chapter = None
    owner = None
    cur: list | None = None  # [(caption, items)] for the open block
    items: list | None = None  # the current caption's [(level, text)]
    for p in body.findall(q("p")):
        parts = list(segs(p))
        low = _norm("".join(t for _, t in parts)).lower()  # sentinel/caption test
        if _para_style(p).lower().startswith("heading") and low:
            chapter = _norm("".join(t for _, t in parts))
        if cur is None:
            if NOTES_START_BOOK in low:
                owner, cur, items = BOOK_NOTES_LABEL, [], None
            elif NOTES_START in low:
                owner, cur, items = chapter, [], None
            continue
        if NOTES_END in low:
            out.append((owner or "?", cur))
            cur = items = None
            continue
        cap = _caption_of(low)
        if cap is not None:
            items = []
            cur.append((cap, items))
            continue
        text = _fold("".join(t for k, t in parts if k != "del"))
        if not text:
            continue
        if items is None:  # content before the first caption
            items = []
            cur.append(("", items))
        items.append((_para_level(p), text))
    return out


def format_notes(sections) -> str:
    """Render `review_note_sections` as a readable per-chapter dump."""
    out: list[str] = []
    for owner, caps in sections:
        out.append("=" * 72)
        out.append(owner or "?")
        for cap, items in caps:
            out.append(f"\n  {cap or '(intro)'}:")
            for level, text in items:
                out.append("    " + "  " * level + "- " + text)
        out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# free-edit recovery: diff a block's current text against a fresh source render
# --------------------------------------------------------------------------- #
def _norm_item(s: str) -> str:
    """A block line stripped of bullet + inline-markdown decoration and
    ws-collapsed, so a rendered ``- *…*`` / ``- `code` `` line matches the doc
    paragraph Google exported with those markers rendered away."""
    s = s.strip().lstrip("-").strip()
    s = re.sub(r"[`*_]", "", s)  # code spans + emphasis Google drops on export
    return _norm(s)


def _block_lines(block_md: str) -> list[str]:
    """The comparable item/caption lines of a rendered block (sentinels and blanks
    dropped, each decoration-stripped)."""
    out = []
    for raw in block_md.splitlines():
        s = raw.strip()
        low = s.lower()
        if not s or NOTES_START_BOOK in low or NOTES_START in low or NOTES_END in low:
            continue
        item = _norm_item(s)
        if item:
            out.append(item)
    return out


def _source_h1_map(src_dir: Path) -> dict[str, Path]:
    """{lower-cased H1 title -> prose file}, to resolve a document heading."""
    m: dict[str, Path] = {}
    source_root = _source_root_for(src_dir)
    for f in sorted(source_root.rglob("*.qmd")):
        h1 = _H1.search(f.read_text(encoding="utf-8"))
        if h1:
            m[_norm(re.sub(r"\{#.*?\}", "", h1.group(0).lstrip("#"))).lower()] = f
    return m


def _render_source_block(
    owner: str, src_dir: Path, h1map: dict[str, Path]
) -> list[str] | None:
    """The block freshly rendered from *current* source for `owner`, as comparable
    lines — or None if the owner can't be resolved to a source file."""
    if owner == BOOK_NOTES_LABEL:
        if _review_notes_path(src_dir) is None:
            return None
        annos = book_notes(src_dir)
        return _block_lines(
            render_book_notes_block(
                _anno_lines(annos, "guidance"),
                _anno_lines(annos, "genesis"),
                _anno_lines(annos, "variant"),
            )
        )
    key = _norm(_strip_heading_number(owner)).lower()
    f = h1map.get(key)
    if f is None:
        return None
    annos = _sidecar_annos(f)
    by_slug = _book_notes_by_slug(book_notes(src_dir))
    return _block_lines(
        render_notes_block(
            _anno_lines(annos, "guidance"),
            _anno_lines(annos, "genesis"),
            _anno_lines(annos, "variant"),
            see=_see_pointers(annos, by_slug),
        )
    )


def note_content_diffs(
    notes_content, source
) -> list[tuple[str, list[str], list[str], bool]]:
    """Per edited block: (owner, removed, added, resolved). `removed` are lines in
    the source render but not the doc (a struck / reworded-away note); `added` are
    lines in the doc but not the source (a new or reworded note). `resolved` is
    False when the source block could not be located (the whole doc block is shown
    as added). Unchanged blocks are omitted."""
    src = Path(source)
    h1map = _source_h1_map(src) if src.is_dir() else {}
    out = []
    for owner, doc_lines in notes_content:
        b = [x for x in doc_lines if x]
        a_full = _render_source_block(owner, src, h1map)
        if a_full is None:
            if b:
                out.append((owner, [], b, False))
            continue
        a = [x for x in a_full if x]
        removed = [x for x in a if x not in b]
        added = [x for x in b if x not in a]
        if removed or added:
            out.append((owner, removed, added, True))
    return out


def format_note_content_diffs(notes_content, source) -> str:
    diffs = note_content_diffs(notes_content, source)
    if not diffs:
        return ""
    out = [
        f"\n### REVIEW-NOTES FREE EDITS ({len(diffs)}) "
        "-> blocks edited in plain mode; fold each delta into that section's "
        "human-facing marks (assumes the source annotations "
        "have not drifted since the build)"
    ]
    for owner, removed, added, resolved in diffs:
        tag = "" if resolved else "  (source block unresolved; showing the whole block)"
        out.append(f"\n[{owner}]{tag}")
        out.extend(f"  - was: {x}" for x in removed)
        out.extend(f"  + now: {x}" for x in added)
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# extract -> structured result
# --------------------------------------------------------------------------- #
@dataclass
class Comment:
    author: str
    date: str
    on: str  # 'del' | 'ins' | 'figure' | 'text'
    anchor: str
    note: str
    source: str | None


@dataclass
class Edit:
    source: str  # 'relpath:line', 'relpath:lo-hi', or 'new'
    redline: str


@dataclass
class Review:
    comments: list[Comment] = field(default_factory=list)
    edits: list[Edit] = field(default_factory=list)
    notes_edits: list[tuple[str, str]] = field(default_factory=list)  # (owner, redline)
    notes_content: list[tuple[str, list[str]]] = field(
        default_factory=list
    )  # (owner, lines)


def extract(docx: Path, idx, fig_idx=None) -> Review:
    body, comments = load(docx)
    anch, ctx, near = anchored(body)
    rev = Review()
    block_ids, rev.notes_edits = review_note_edits(body)
    rev.notes_content = review_note_content(body)

    for cid, c in comments.items():
        a = anch.get(cid, "").strip(" /")
        kinds = ctx.get(cid, set())
        on = (
            "del"
            if "del" in kinds
            else "ins"
            if "ins" in kinds
            else "figure"
            if "figure" in kinds
            else "text"
        )
        if on == "figure":
            where = locate_figure(a, fig_idx) if fig_idx else None
        else:
            where = locate(a, idx) if a else None
        if where is None and on == "ins":
            where = "inserted block (new; no source line)"
        elif where is None:
            near_txt = near.get(cid, "")
            where = f"near “…{near_txt[-40:]}”" if near_txt else "unlocated"
        rev.comments.append(Comment(c["author"], c["date"], on, a, c["text"], where))

    for p in body.findall(q("p")):
        if id(p) in block_ids:
            continue  # review-notes block: its edits are routed, not prose
        parts = list(segs(p))
        if not any(k in ("ins", "del") for k, _ in parts):
            continue
        red = before = ""
        for k, t in parts:
            if k == "ins":
                red += "⟦+" + t + "+⟧"
            elif k == "del":
                red += "⟦-" + t + "-⟧"
                before += t
            else:
                red += t
                before += t
        rev.edits.append(Edit(para_range(before, idx) or "new", _norm(red)))

    return rev


def format_review(rev: Review) -> str:
    out: list[str] = []
    label = {
        "del": "a DELETION",
        "ins": "an INSERTION",
        "figure": "a figure",
        "text": "text",
    }
    out.append(f"### COMMENTS ({len(rev.comments)})")
    for c in rev.comments:
        out.append(f"\n[{c.author}] {c.date}  (on {label[c.on]})")
        out.append(f"  edit/anchor: “{c.anchor[:110]}”")
        out.append(f"  -> {c.source}")
        out.append(f"  note: {c.note}")
    out.append(f"\n### TRACKED EDITS ({len(rev.edits)} paragraph redlines)")
    for e in rev.edits:
        out.append(f"\n[{e.source}]\n  {e.redline}")
    if rev.notes_edits:
        out.append(
            f"\n### REVIEW-NOTES EDITS ({len(rev.notes_edits)}) "
            f"-> fold into @guidance / @genesis / @variant metadata, not prose "
            f"({BOOK_NOTES_LABEL} -> configured manuscript.review_notes)"
        )
        for owner, red in rev.notes_edits:
            out.append(f"\n[{owner}]\n  {red}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def _newest_docx(root: Path) -> Path | None:
    docxs = [p for p in root.rglob("*.docx") if not p.name.startswith("~$")]
    return max(docxs, key=lambda p: p.stat().st_mtime) if docxs else None


# --------------------------------------------------------------------------- #
# figure doubling (build side): show each figure's grayscale twin under it
# --------------------------------------------------------------------------- #
# `redpen build --gray` appends, under every color figure, the *generated* gray
# rendering the EPUB and print editions actually ship (see graybuild). The author
# reads the two under the one caption and catches a gray that has gone muddy or
# lost a distinction the color carried -- the one failure `quirework publish figures
# --check` cannot see (it verifies the transform is clean, not that the result
# still reads). The color embed line is left byte-for-byte intact (same caption,
# number, `#fig-` anchor), so every `@fig-NAME` cross-reference still resolves;
# only a bare, unnumbered gray image and a small label are added below it. The
# twin is eyes-only: a mark on it has no routing home in `redpen read` (a gray fix
# goes to graybuild, not to prose or a mark), so it is there to look at.
_GRAY_TWIN_LABEL = (
    "*grayscale twin: how the EPUB and print editions ship this "
    "figure (desaturated for e-ink). Check it still reads.*"
)
_COLOR_EMBED = re.compile(
    rf"^[ \t]*!\[.*\]\({_figures.GALLERY}/([A-Za-z0-9][\w-]*)\.svg\)(?:\{{.*\}})?[ \t]*$"
)


def double_figures(stage_canon: Path) -> int:
    """Append each color figure's grayscale twin beneath it, in the staged canon.

    Rewrites staged chapter prose only: after every embed
    line pointing at the color gallery (`illustrations/NAME.svg`), insert the
    generated gray image (`illustrations-gray/NAME.svg`, bare and uncaptioned so
    it takes no figure number of its own) and a label. The original embed line is
    untouched, so its caption, number, and `#fig-` anchor survive and every
    `@fig-NAME` cross-reference still resolves. Returns the number of figures
    doubled. The gray gallery must already sit beside the color one in the stage
    (see `figures.generate_gray`). The `illustrations/` match is slash-anchored,
    so it never re-matches an `illustrations-gray/` line the pass itself adds."""
    n = 0
    for qmd in sorted(stage_canon.glob("*.qmd")):
        out: list[str] = []
        for line in qmd.read_text(encoding="utf-8").splitlines():
            out.append(line)
            m = _COLOR_EMBED.match(line)
            if m:
                out += [
                    "",
                    f"![]({_figures.GRAY_GALLERY}/{m.group(1)}.svg)",
                    "",
                    _GRAY_TWIN_LABEL,
                ]
                n += 1
        qmd.write_text("\n".join(out) + "\n", encoding="utf-8")
    return n


def _build_chapter(
    quarto: str, stage: Path, stem: str, out_dir: Path, gray: bool = False
) -> tuple[int, Path | None]:
    """Render one chapter to its own .docx. A Quarto *book* always collapses to a
    single whole-book document, so a chapter is rendered standalone: copy its
    staged .qmd and the materialized figure gallery into a work dir with no book
    `_quarto.yml`, render there, then collect. The source filename becomes the doc
    title, so every comment maps straight back to its chapter. With `gray`, the
    staged .qmd already carries the grayscale twins (`double_figures` ran on the
    stage), so the gray gallery is copied in beside the color one."""
    src_qmd = stage / current_project().chapter_subdir / f"{stem}.qmd"
    if not src_qmd.exists():
        print(
            f"redpen: no chapter '{stem}' "
            f"(looked under {current_project().chapter_subdir})",
            file=sys.stderr,
        )
        return 2, None
    work = out_dir / "_work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    shutil.copyfile(src_qmd, work / f"{stem}.qmd")
    copy_gallery(stage, work)
    if gray:
        copy_gallery(stage, work, _figures.GRAY_GALLERY)
    rc = subprocess.call(
        [
            quarto,
            "render",
            f"{stem}.qmd",
            "--to",
            "docx",
            "--metadata",
            f"title={stem}.qmd",
        ],
        cwd=work,
    )
    produced = work / f"{stem}.docx"
    if rc or not produced.exists():
        return (rc or 1), None
    cap_docx_image_widths(produced)
    dest = out_dir / f"{stem}.docx"
    shutil.copyfile(produced, dest)
    shutil.rmtree(work)
    return 0, dest


def build(chapter: str | None, out_dir: Path, gray: bool = False) -> int:
    from quirework.publish import render as _render

    quarto = _render.find_quarto()
    if not quarto:
        print(
            "redpen: quarto not found. Install it (`pip install -e '.[docs]'`).",
            file=sys.stderr,
        )
        return 127
    rc = _render.stage_book(_settings().STAGE_REDPEN)
    if rc:
        return rc
    stage = _settings().STAGE_REDPEN
    if gray:  # double each figure with its EPUB/print gray twin
        ng = _figures.generate_gray(
            _figures.embedded_names(),
            stage / current_project().chapter_subdir / _figures.GRAY_GALLERY,
        )
        nd = double_figures(stage / current_project().chapter_subdir)
        print(
            f"redpen: --gray: generated {ng} grayscale twin(s), doubled {nd} figure(s)"
        )
    n = inject_review_notes(stage)
    book = inject_book_block(stage, chapter)  # book-wide block at the document top
    guide = inject_guide(stage, chapter)  # workflow guide at the very front
    print(
        f"redpen: review-notes block injected into {n} section(s)"
        + ("; book-wide block at the top" if book else "")
        + ("; guide chapter prepended" if guide else "")
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    if chapter:
        stem = chapter[:-4] if chapter.endswith(".qmd") else chapter
        rc, dest = _build_chapter(quarto, stage, stem, out_dir, gray=gray)
        if rc:
            return rc
    else:
        promoted, dividers = flatten_book_parts(stage)
        for rel in promoted:
            unnumber_h1(stage / rel)
        if promoted or dividers:
            print(
                "redpen: docx has no book parts; "
                f"promoted {len(promoted)} part file(s) to chapters"
                + (f" ({', '.join(promoted)})" if promoted else "")
                + f", materialized {len(dividers)} divider(s)"
                + (f" ({', '.join(dividers)})" if dividers else "")
            )
        rc = subprocess.call(
            [quarto, "render", "--to", "docx", "--output-dir", "_site"], cwd=stage
        )
        if rc:
            return rc
        produced = _newest_docx(stage)  # book -> one .docx in _site
        if not produced:
            print("redpen: render produced no .docx", file=sys.stderr)
            return 1
        cap_docx_image_widths(produced)
        dest = out_dir / produced.name
        shutil.copyfile(produced, dest)

    try:
        shown = dest.relative_to(current_project().root)
    except ValueError:
        shown = dest
    print(f"redpen: review copy -> {shown}")
    print("upload it to Google Docs; use Suggesting mode for edits, then:")
    print("  quirework publish redpen read <downloaded>.docx")
    return 0


def _emit(text: str, out: Path | None) -> None:
    """Print to stdout, or write to `out` and print a one-line receipt.

    The receipt matters: an agent that redirects a dump away from the terminal
    is following the context-hygiene rule, and the marks then exist *only* in
    the file. See `archive` for why that channel has to be durable.
    """
    if out is None:
        print(text)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    try:
        shown = out.relative_to(current_project().root)
    except ValueError:
        shown = out
    print(f"redpen: wrote {len(text):,} chars -> {shown}")


def read(docx: Path, source: Path, out: Path | None = None) -> int:
    if not docx.exists():
        print(f"redpen: no such file: {docx}", file=sys.stderr)
        return 2
    idx = source_index(source)
    fig_idx = figure_index(source)
    rev = extract(docx, idx, fig_idx)
    text = format_review(rev)
    diffs = format_note_content_diffs(rev.notes_content, source)
    if diffs:
        text += "\n" + diffs
    _emit(text, out)
    return 0


def notes(docx: Path, out: Path | None = None) -> int:
    """Dump every review-notes block's verbatim human-facing metadata, grouped
    by chapter and section. Complements `read`: `read`
    diffs a block against source to fold *small* edits back, while `notes`
    reproduces a block the author *rewrote wholesale*, nesting intact — content
    that a delta cannot carry. Source-free (nothing to map)."""
    if not docx.exists():
        print(f"redpen: no such file: {docx}", file=sys.stderr)
        return 2
    body, _ = load(docx)
    _emit(format_notes(review_note_sections(body)), out)
    return 0


# --------------------------------------------------------------------------- #
# archive -- the durable record of a round
# --------------------------------------------------------------------------- #

# The reviewed `.docx` are not kept: they are binary, image-heavy, and live
# outside the repo. The archive is what survives them, and it is the *only*
# surviving record of the author's marks for a round -- so it carries both
# extraction channels, not one. `read` renders a rewritten review-notes block
# as a `- was` / `+ now` delta, which loses the author's words and the bullet
# nesting; `notes` dumps every block verbatim. A delta is not an archive.
#
# It is also the channel the blind cold-read reads from. `redpen read` prints
# to stdout, so the marks used to reach a session only as a Bash `tool_result`,
# which `ccfilter` drops as tool output -- 58,321 characters of author marks
# across 8 rounds, 0% surviving into the render. Worse, the rounds whose agents
# correctly redirected the dump to a scratch file destroyed the record most
# thoroughly. `ccfilter` now joins a session to this archive on the `.docx`
# filename in the command line (see `SOURCE_DOCX_KEY`), so the marks survive
# both the redirect and the deletion of the `.docx` itself.

SOURCE_DOCX_KEY = "source-docx"  # the join key ccfilter matches on
ARCHIVE_RAW_PARTS = (
    "word/comments.xml",
    "word/document.xml",
    "word/commentsExtended.xml",
    "word/people.xml",
)


def _git_short(rev: str = "HEAD", cwd: Path | None = None) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", rev],
            cwd=str(cwd or current_project().root),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _anchor_tally(rev: Review) -> tuple[int, int, int]:
    """(exact, fuzzy, unlocated) over the round's comments."""
    exact = fuzzy = lost = 0
    for c in rev.comments:
        if c.source.startswith("near "):
            fuzzy += 1
        elif c.source == "unlocated":
            lost += 1
        else:
            exact += 1
    return exact, fuzzy, lost


def _dump_raw(docx: Path, raw_dir: Path) -> list[str]:
    """Extract the text-bearing XML parts so a later extractor can be re-run.

    The `.docx` itself is not archived (binary, and mostly embedded images);
    these parts are plain text, diffable, and carry every comment, tracked
    change and anchor context. They allow a later extractor to re-read the
    durable record.
    """
    written = []
    raw_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(docx) as z:
        have = set(z.namelist())
        for part in ARCHIVE_RAW_PARTS:
            if part not in have:
                continue
            dest = raw_dir / part.split("/")[-1]
            dest.write_bytes(z.read(part))
            written.append(f"{dest.name} ({dest.stat().st_size:,} bytes)")
    return written


def archive(
    docx: Path,
    source: Path,
    out: Path,
    canon_sha: str | None = None,
    raw: bool = True,
    label: str | None = None,
) -> int:
    """Write one round's durable provenance artifact: header + both channels."""
    if not docx.exists():
        print(f"redpen: no such file: {docx}", file=sys.stderr)
        return 2
    if not source.exists():
        print(f"redpen: no such source: {source}", file=sys.stderr)
        return 2

    idx = source_index(source)
    fig_idx = figure_index(source)
    rev = extract(docx, idx, fig_idx)
    body, _ = load(docx)
    sections = review_note_sections(body)

    exact, fuzzy, lost = _anchor_tally(rev)
    mtime = dt.datetime.fromtimestamp(docx.stat().st_mtime)
    dates = sorted({c.date for c in rev.comments if c.date})
    sha256 = hashlib.sha256(docx.read_bytes()).hexdigest()
    round_label = label or out.stem

    head = [
        f"# Red-pen round {round_label}",
        "",
        "Durable archive of one red-pen review round: the author's marks,",
        "extracted from the reviewed `.docx` and mapped against the canon of",
        "that moment. The `.docx` is not kept; this file and the sibling",
        "`.raw/` XML are the record. Never public (`private/AGENTS.md`).",
        "",
        "```",
        f"{SOURCE_DOCX_KEY}:    {docx.name}",
        f"docx-mtime:     {mtime:%Y-%m-%d %H:%M:%S}",
        f"docx-sha256:    {sha256}",
        f"marks-dated:    {dates[0] if dates else '?'}"
        + (f" .. {dates[-1]}" if len(dates) > 1 else ""),
        f"canon-sha:      {canon_sha or _git_short(cwd=source)}",
        f"canon-dir:      {source}",
        f"extracted:      {dt.date.today():%Y-%m-%d}",
        f"book revision: {_git_short()}",
        f"comments:       {len(rev.comments)}",
        f"tracked-edits:  {len(rev.edits)}",
        f"review-notes:   {len(rev.notes_edits)} edits, {len(sections)} blocks",
        f"anchors:        exact {exact} / fuzzy {fuzzy} / unlocated {lost}",
        "```",
        "",
    ]

    parts = ["\n".join(head)]
    parts.append("## MARKS  (`quirework publish redpen read`)\n")
    parts.append(format_review(rev))
    diffs = format_note_content_diffs(rev.notes_content, source)
    if diffs:
        parts.append(diffs)
    parts.append("\n## REVIEW-NOTES, VERBATIM  (`quirework publish redpen notes`)\n")
    parts.append(format_notes(sections) or "(no review-notes blocks in this round)")

    out.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(parts).rstrip() + "\n"
    out.write_text(text, encoding="utf-8")

    try:
        shown = out.relative_to(current_project().root)
    except ValueError:
        shown = out
    print(
        f"redpen: archived {round_label} -> {shown}  "
        f"({len(rev.comments)} comments, {len(rev.edits)} edits, "
        f"anchors {exact}/{fuzzy}/{lost}, {len(text):,} chars)"
    )
    if raw:
        for line in _dump_raw(docx, out.with_suffix(".raw")):
            print(f"  raw: {line}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="quirework publish redpen",
        description="Red-pen review loop: build a reviewable .docx, read the marks back.",
    )
    sub = ap.add_subparsers(dest="cmd")

    b = sub.add_parser(
        "build", help="render the book (or one --chapter) to a reviewable .docx"
    )
    b.add_argument(
        "--chapter", help="one chapter slug, e.g. the-sidecar (default: whole book)"
    )
    b.add_argument(
        "--gray",
        action="store_true",
        help="double every figure: append its grayscale (EPUB/print) twin under the color one",
    )
    b.add_argument("--out", default=None, help="output dir (default: build/redpen/)")

    r = sub.add_parser(
        "read", help="extract marks from a reviewed .docx and map them to source"
    )
    r.add_argument("docx", help="the reviewed .docx downloaded from Google Docs")
    r.add_argument(
        "--source",
        default=None,
        help="source .qmd or dir (default: manuscript.chapters)",
    )
    r.add_argument(
        "--out",
        default=None,
        help="write to PATH instead of stdout (keeps the marks out of the "
        "terminal without destroying them; see `archive`)",
    )

    n = sub.add_parser(
        "notes",
        help="dump each review-notes block verbatim (the author's own words), by chapter",
    )
    n.add_argument("docx", help="the reviewed .docx downloaded from Google Docs")
    n.add_argument("--out", default=None, help="write to PATH instead of stdout")

    a = sub.add_parser(
        "archive",
        help="write a round's durable provenance artifact (both channels + header)",
    )
    a.add_argument("docx", help="the reviewed .docx downloaded from Google Docs")
    a.add_argument(
        "--out",
        required=True,
        help="artifact path, e.g. .quirework/provenance/redpen/round-7.md",
    )
    a.add_argument(
        "--source",
        default=None,
        help="canon of that round (a worktree at its SHA); default: manuscript.chapters",
    )
    a.add_argument("--canon-sha", default=None, help="SHA to record in the header")
    a.add_argument(
        "--label",
        default=None,
        help="round label for the heading (default: --out stem)",
    )
    a.add_argument(
        "--no-raw",
        action="store_true",
        help="skip the sibling .raw/ XML dump (which is what keeps re-extraction possible)",
    )

    args = ap.parse_args(argv)
    if args.cmd == "build":
        return build(
            args.chapter,
            Path(args.out) if args.out else _settings().BUILD_REDPEN,
            gray=args.gray,
        )
    if args.cmd == "read":
        return read(
            Path(args.docx),
            Path(args.source) if args.source else _settings().CANONICAL,
            Path(args.out) if args.out else None,
        )
    if args.cmd == "notes":
        return notes(Path(args.docx), Path(args.out) if args.out else None)
    if args.cmd == "archive":
        return archive(
            Path(args.docx),
            Path(args.source) if args.source else _settings().CANONICAL,
            Path(args.out),
            canon_sha=args.canon_sha,
            raw=not args.no_raw,
            label=args.label,
        )
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
