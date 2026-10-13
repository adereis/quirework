"""Staged-book structure utilities shared by render, redpen, and panel-kit.

Everything here operates on a staged copy of the book, never on configured
sources: walking and rewriting the staged `_quarto.yml`'s `book.chapters`
block (parts flattened for the formats that have no part concept), unnumbering a
promoted part's H1, copying a figure gallery beside a standalone chapter, capping
DOCX image widths for Google Docs, and repairing the two conformance defects the
EPUB writers emit. These are book-structure concepts shared by several commands.
"""

from __future__ import annotations

from quirework.project import current_project
import html
import json
import posixpath
import re
import shutil
import zipfile
from pathlib import Path
from typing import NamedTuple

from quirework.manuscript.quarto import BOOK_CONFIG, is_book, read_items
from quirework.publish import figures as _figures

# --------------------------------------------------------------------------- #
# book parts (build side): docx has none, so flatten them away. Reading the
# `book.chapters` block is the manuscript's job (manuscript.quarto); this side
# only rewrites a staged copy of it.
# --------------------------------------------------------------------------- #
_H1_ATTRS = re.compile(r"(?m)^(# .+?)(?:[ \t]*\{(.*?)\})?[ \t]*$")


def _options():
    """Read policy from the selected project for this operation."""
    from types import SimpleNamespace

    TYPST_PROLOGUES: dict[str, str] = (
        current_project().data("publish.layout_policy", {}).get("typst_prologues", {})
    )
    TYPST_EPILOGUES: dict[str, str] = (
        current_project().data("publish.layout_policy", {}).get("typst_epilogues", {})
    )
    return SimpleNamespace(
        TYPST_EPILOGUES=TYPST_EPILOGUES, TYPST_PROLOGUES=TYPST_PROLOGUES
    )


def _divider_slug(title: str) -> str:
    """A staged divider file's stem, by the book's own chapter-naming rule: the
    title lowercased and hyphenated, truncated at the first comma or colon. So
    "Part One: Departures" is `part-one.qmd`."""
    head = re.split(r"[,:]", title, maxsplit=1)[0]
    return re.sub(r"[^a-z0-9]+", "-", head.lower()).strip("-") or "part"


def _divider_heading(title: str) -> str:
    """A divider H1's inline content: the title, split into its label and its
    name so a stylesheet can set the two apart.

    A configured print template can apply the same split. A long part title may
    not fit the measure as one line. The label goes above a rule, small and tracked;
    the name below it carries the page. Doing the split HERE, in the markup, is
    what lets the EPUB have it: a stylesheet cannot separate two halves of one
    text node.

    The heading still carries the WHOLE title, punctuation and all, because the
    nav document, the Contents page and the DOCX all read it as text -- so the
    colon is a span of its own that the EPUB hides, rather than a character
    dropped here. A title with no colon (the back matter) is all name.
    """
    label, sep, name = title.partition(": ")
    if not sep:
        return f"[{title}]{{.part-name}}"
    return f"[{label}]{{.part-label}}[:]{{.part-colon}} [{name}]{{.part-name}}"


def _write_part_divider(stage: Path, title: str) -> str:
    """Materialize a divider chapter for a title-only part; return its relpath.

    `.unnumbered` so it consumes no chapter number (every format still reads
    1..N with the dividers between them), and `.part-divider` so the EPUB can
    style it as the page it is."""
    rel = f"{_divider_slug(title)}.qmd"
    dest = stage / rel
    if dest.exists():
        raise ValueError(f"{BOOK_CONFIG}: part divider {rel} collides with a book file")
    dest.write_text(
        f"# {_divider_heading(title)} {{.unnumbered .part-divider}}\n", encoding="utf-8"
    )
    return rel


def flatten_book_parts(stage: Path) -> tuple[list[str], list[str]]:
    """Rewrite the staged `_quarto.yml` so nothing is lost to a `part:`.

    Word and EPUB book output have no part-divider concept: Quarto drops every
    `part:` from those formats. For a *file*-based part (`part: sections/part-two.qmd`)
    that takes the whole part-opening essay down with it, prose and figures and
    all, without a warning; for a title-only part (`part: "Part One: …"`) it
    silently deletes the division of the book, which is how the EPUB shipped as
    twenty flat chapters with no Parts in it at all. HTML and Typst are
    unaffected and keep their real parts.

    So each part becomes a chapter in its own place in the reading order: a part
    file is promoted as-is, and a title-only part is **materialized** as a
    one-line divider `.qmd` written into the stage (`_write_part_divider`).
    Both are `.unnumbered`, so no chapter number shifts. Returns (promoted
    files, materialized dividers); the caller says so out loud.

    Raises ValueError if the staged config is not the shape this understands, so a
    future `_quarto.yml` cannot be silently mangled into a partial book.
    A plain Quarto project, such as an edition that is one memo, has no
    parts to lose and is left as it is."""
    if not is_book(stage):
        return [], []
    lines, ci, end, item_ind, items = read_items(stage)
    pad = " " * item_ind

    out: list[str] = []
    promoted: list[str] = []
    dividers: list[str] = []
    for it in items:
        if it.kind == "comment":
            out.append(it.value)  # a comment rides along verbatim
            continue
        if it.kind == "part-file":
            promoted.append(it.value)
            out.append(f"{pad}- {it.value}")
        elif it.kind == "part-title":
            rel = _write_part_divider(stage, it.value)
            dividers.append(rel)
            out.append(f"{pad}- {rel}")
        else:
            out.append(f"{pad}- {it.value}")
        out.extend(f"{pad}- {f}" for f in it.children)

    lines[ci + 1 : end] = out
    (stage / BOOK_CONFIG).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return promoted, dividers


# --------------------------------------------------------------------------- #
# the Contents page (build side): only Typst can build its own
# --------------------------------------------------------------------------- #
CONTENTS_FILE = "contents.qmd"
# Two divs, not one with both classes: Quarto *consumes* the conditional div
# once it has evaluated the condition, keeping only its children, so a styling
# class riding on it never reaches the output. The inner one survives.
_CONTENTS_OPEN = '::: {.content-visible unless-format="typst"}\n::: {.book-contents}'
_CONTENTS_CLOSE = ":::\n:::"


def _h1(qmd: Path) -> tuple[str, list[str]] | None:
    """`(title, attrs)` from a staged chapter's H1; None when it has none."""
    m = _H1_ATTRS.search(qmd.read_text(encoding="utf-8"))
    if not m:
        return None
    return m.group(1)[2:].strip(), (m.group(2) or "").split()


def _ensure_h1_id(qmd: Path, stem: str) -> str | None:
    """The explicit id on a staged chapter's H1, written in as `sec-<stem>` when
    it has none. None when the file has no H1.

    Deriving Pandoc's auto-generated id instead would be a guess, and a wrong
    guess is a dead link in the Contents page that nothing checks. Writing the
    id makes the target the one we linked to, by construction. Stems are unique
    across the book (`chapters --check` invariant 10), so the id is too."""
    text = qmd.read_text(encoding="utf-8")
    m = _H1_ATTRS.search(text)
    if not m:
        return None
    attrs = (m.group(2) or "").split()
    for a in attrs:
        if a.startswith("#"):
            return a[1:]
    ident = f"sec-{stem}"
    qmd.write_text(
        text[: m.start()]
        + f"{m.group(1)} {{{' '.join([f'#{ident}'] + attrs)}}}"
        + text[m.end() :],
        encoding="utf-8",
    )
    return ident


class Row(NamedTuple):
    """One entry of the book's reading order, parts intact.

    `kind` is `part` (a divider) or `chapter`. `rel` is the staged `.qmd`, and
    is None for a title-only part, which names no file. `title` is the H1 (or
    the part title) with no number on it; `number` is the printed chapter
    number or None (parts, and every `.unnumbered` page); `listed` is False for
    an `.unlisted` page, which appears in no Contents and no PDF outline;
    `part` is the title of the part the row sits in, None outside one."""

    kind: str
    rel: str | None
    title: str
    number: int | None
    listed: bool
    part: str | None


def book_rows(stage: Path) -> list[Row]:
    """The book's reading order, one row per entry, parts intact.

    **The one owner of what the book's order and its chapter numbering are.**
    Numbers are assigned the way Quarto assigns them -- sequentially over the
    chapters that are not `.unnumbered`, with parts consuming none -- so
    nothing built from these rows can disagree with the printed running heads.
    `book_outline` (the Contents page) and `panelkit` (the reader kit) are both
    projections of this; a second walker computing its own numbers is how the
    Contents page and the pages it points at drift apart.

    Read from the **unflattened** structure, so the parts are still parts. Call
    before `flatten_book_parts`, never after."""
    _, _, _, _, items = read_items(stage)
    rows: list[Row] = []
    n = 0

    def chapter(rel: str, part: str | None) -> None:
        nonlocal n
        head = _h1(stage / rel)
        if head is None:
            return
        title, attrs = head
        number = None
        if ".unnumbered" not in attrs:
            n += 1
            number = n
        rows.append(Row("chapter", rel, title, number, ".unlisted" not in attrs, part))

    for it in items:
        if it.kind == "comment":
            continue
        if it.kind == "chapter":
            chapter(it.value, None)
            continue
        if it.kind == "part-file":
            head = _h1(stage / it.value)
            title = head[0] if head else it.value
            rows.append(Row("part", it.value, title, None, True, title))
        else:
            title = it.value
            rows.append(Row("part", None, title, None, True, title))
        for kid in it.children:
            chapter(kid, title)
    return rows


def book_outline(
    stage: Path, anchors: bool = False
) -> list[tuple[str, str, str | None]]:
    """The book's reading order as Contents rows: `(kind, label, link)`.

    A projection of `book_rows`: the `.unlisted` pages (the welcome, copyright
    and dedication pages, and the Contents page itself) are left out, exactly as
    they are left out of the print outline and the EPUB nav, and a chapter's
    label carries its printed number.

    `anchors` picks the link form, which is not cosmetic: **Quarto rewrites a
    `chapter.qmd` link to the output file for HTML only.** In EPUB it ships the
    `.qmd` through verbatim and every row is a dead link. But EPUB and DOCX are
    each built from one Pandoc document, so an in-document `#id` resolves there
    (Pandoc repoints it across the split chapter files), while in a Quarto HTML
    book, where each chapter is its own page, a bare `#id` would only ever find
    the Contents page itself. So: file links for the HTML/Typst stage, anchors
    for the EPUB/DOCX stage.

    The labels are also what the rendered PDF's own bookmarks read, which is
    what lets `panelkit` slice the book by them (`panelkit.outline_pages`)."""
    rows: list[tuple[str, str, str | None]] = []
    for r in book_rows(stage):
        if r.kind == "part":
            rows.append(("part", r.title, None))
            continue
        if not r.listed:
            continue
        label = f"{r.number} {r.title}" if r.number is not None else r.title
        link = r.rel
        if anchors:
            assert r.rel is not None
            ident = _ensure_h1_id(stage / r.rel, Path(r.rel).stem)
            if ident is None:
                continue
            link = f"#{ident}"
        rows.append(("chapter", label, link))
    return rows


def render_contents(
    stage: Path, anchors: bool = False, contents: str = CONTENTS_FILE
) -> int:
    """Write a real Contents page into the staged book. Returns the row count.

    `contents.qmd` is a raw ```{=typst}` `#outline` block, which is exactly one
    format's table of contents: in EPUB, DOCX and HTML the page rendered as a
    bare "Contents" heading with nothing under it. So the list is generated here
    instead, from the same `_quarto.yml` reading order and the same H1s the book
    is built from, and appended inside a `content-visible unless-format="typst"`
    div -- print keeps the Typst outline, every other format gets this. It is
    rebuilt on every render from the staged sources, so it cannot rot.

    `anchors` picks the link form for this stage; `book_outline` owns why.
    Entries are hard-line-broken lines rather than a list, so the page reads the
    same in all three formats with no per-format styling: a bulleted list would
    need CSS the DOCX cannot take. No-ops (returning 0) when the stage has no
    contents page."""
    page = stage / contents
    if not page.is_file():
        return 0
    rows = book_outline(stage, anchors=anchors)
    if not rows:
        return 0

    out: list[str] = [_CONTENTS_OPEN, ""]
    run: list[str] = []

    def flush() -> None:
        if run:
            # The run is wrapped so a stylesheet can indent a part's chapters
            # under its heading: both are paragraphs otherwise, and no selector
            # can tell "a paragraph that is only bold" from the rest.
            out.extend(["::: {.contents-run}", "\\\n".join(run), ":::", ""])
            run.clear()

    for kind, label, link in rows:
        text = label.replace("[", r"\[").replace("]", r"\]")
        if kind == "part":
            flush()
            out.extend([f"**{text}**", ""])
            continue
        run.append(f"[{text}]({link})")
    flush()
    out.append(_CONTENTS_CLOSE)

    page.write_text(
        page.read_text(encoding="utf-8").rstrip("\n") + "\n\n" + "\n".join(out) + "\n",
        encoding="utf-8",
    )
    return len(rows)


# --------------------------------------------------------------------------- #
# the EPUB style include (build side): the reader gets the rules, not the why
# --------------------------------------------------------------------------- #
EPUB_STYLES = "epub-styles.html"  # `include-in-header` for epub in BOOK_CONFIG
_STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style>", re.S | re.I)


def _css_without_comments(css: str) -> str:
    """`css` with every `/* ... */` removed, and strings left alone.

    A regex would be shorter and wrong: a comment opener inside a `url("...")`
    or a `content` string is not a comment, and eating from there to the next
    `*/` would silently swallow real rules. So this scans, tracking whether it
    is inside a quoted string."""
    out: list[str] = []
    i, n, quote = 0, len(css), ""
    while i < n:
        c = css[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:  # an escaped quote is not the end
                out.append(css[i + 1])
                i += 2
                continue
            if c == quote:
                quote = ""
            i += 1
        elif c in "\"'":
            quote = c
            out.append(c)
            i += 1
        elif css.startswith("/*", i):
            end = css.find("*/", i + 2)
            i = n if end < 0 else end + 2  # unterminated: drop to end of file
        else:
            out.append(c)
            i += 1
    return "".join(out)


def strip_style_comments(stage: Path, rel: str | None = None) -> tuple[int, int]:
    """Reduce a staged style include to its rules. Returns (before, after) bytes.

    **This file is not loaded once; it is copied into the `<head>` of every
    content document in the EPUB.** So every line of it is paid for ~38 times
    over, and the comments are the bulk of it -- the source runs about 15 KB, of
    which under 3 KB is rules, which is 580 KB of commentary shipped inside a
    book nobody can read it in. The doctrine belongs in the source, where it is
    read and maintained; the reader's copy carries the rules alone.

    Everything outside the `<style>` element goes with them, which retires a
    hazard rather than only saving bytes: the file's HTML banner comment shipped
    into 38 documents that are parsed as XML, where a double hyphen inside a
    comment is illegal and invalidates every one of them.

    Raises if the file is absent (a rename must break the render rather than
    silently stop stripping) or if the result's braces do not balance."""
    if rel is None:
        names = (
            current_project()
            .data("publish.render_policy", {})
            .get("epub_style_headers", [])
        )
        counts = [strip_style_comments(stage, name) for name in names]
        return tuple(sum(pair[i] for pair in counts) for i in range(2))
    path = stage / rel
    if not path.is_file():
        raise ValueError(
            f"{rel}: not in the staged book; "
            f"{BOOK_CONFIG} names it as an epub include-in-header"
        )
    src = path.read_text(encoding="utf-8")
    blocks = _STYLE_BLOCK.findall(src)
    if not blocks:
        raise ValueError(f"{rel}: no <style> element to strip")
    rules = "\n".join(_css_without_comments(b) for b in blocks)
    rules = re.sub(r"\n{3,}", "\n\n", rules).strip()
    if rules.count("{") != rules.count("}"):
        raise ValueError(
            f"{rel}: stripping unbalanced the braces; "
            "a comment marker inside a string is the likely cause"
        )
    out = f'<style type="text/css">\n{rules}\n</style>\n'
    path.write_text(out, encoding="utf-8")
    return len(src.encode("utf-8")), len(out.encode("utf-8"))


def unnumber_h1(qmd: Path) -> bool:
    """Mark a file's H1 `.unnumbered`, so a promoted part reads as the divider it
    is rather than as an extra numbered chapter — and the chapter numbers the
    author saw last review round do not all shift by one. Idempotent; False when
    the file has no H1."""
    text = qmd.read_text(encoding="utf-8")
    m = _H1_ATTRS.search(text)
    if not m:
        return False
    attrs = (m.group(2) or "").split()
    if ".unnumbered" not in attrs:
        attrs.append(".unnumbered")
    qmd.write_text(
        text[: m.start()] + f"{m.group(1)} {{{' '.join(attrs)}}}" + text[m.end() :],
        encoding="utf-8",
    )
    return True


def copy_gallery(stage: Path, work: Path, gallery: str = _figures.GALLERY) -> int:
    """Copy a staged figure gallery beside a standalone chapter, under the name its
    embeds use (`illustrations/NAME.svg` by default; pass `figures.GRAY_GALLERY`
    for the `--gray` twin set). Returns the number of SVGs copied; 0 when the
    staged gallery is absent."""
    dest = work / gallery
    dest.mkdir(parents=True, exist_ok=True)
    src = stage / current_project().chapter_subdir / gallery
    if not src.is_dir():
        return 0
    svgs = sorted(src.glob("*.svg"))
    for svg in svgs:
        shutil.copyfile(svg, dest / svg.name)
    return len(svgs)


# Google Docs clips (does not scale) an inline image whose display width sits
# near the text-column width: the right edge overflows the frame and is cut off,
# in both Office .docx mode and a native-converted doc, for PNG and SVG alike.
# 4.5in leaves comfortable slack under a 6.5in column; verified to render whole
# where the full ~5.8in figure is clipped. (Raising it re-introduces the crop.)
MAX_IMG_WIDTH_EMU = int(4.5 * 914400)  # 914400 EMU = 1 inch


def cap_docx_image_widths(
    docx_path: Path, max_width_emu: int = MAX_IMG_WIDTH_EMU
) -> None:
    """Shrink over-wide embedded figures so Google Docs stops cropping them.

    The docx itself is well-formed -- the crop is Google's layout step, not the
    file (an oversized inline image is clipped, not scaled down). So we scale any
    drawing whose ``wp:extent`` width exceeds ``max_width_emu`` down to it,
    preserving aspect ratio, by rewriting the layout extent and the picture's
    transform extent together. Everything else in the docx is copied verbatim.

    This replaces an earlier SVG width-injection fix that never worked: the crop
    is format-independent (a PNG-only doc clips identically), so the SVG was never
    the cause.
    """
    doc = "word/document.xml"
    changed = 0

    def _cap_drawing(m: "re.Match[str]") -> str:
        nonlocal changed
        block = m.group(0)
        ext = re.search(r'<wp:extent\b[^>]*\bcx="(\d+)"[^>]*\bcy="(\d+)"', block)
        if not ext:
            return block
        cx, cy = int(ext.group(1)), int(ext.group(2))
        if cx <= max_width_emu:
            return block
        ncx = max_width_emu
        ncy = round(cy * max_width_emu / cx)

        # wp:extent (layout) and the self-closing a:ext (picture transform) carry
        # the same cx/cy; the extension-list <a:ext uri=...> blocks have no cx and
        # are left untouched.
        def _rescale(tag_re: str, s: str) -> str:
            return re.sub(
                tag_re,
                lambda t: (
                    t.group(0)
                    .replace(f'cx="{cx}"', f'cx="{ncx}"')
                    .replace(f'cy="{cy}"', f'cy="{ncy}"')
                ),
                s,
            )

        block = _rescale(r"<wp:extent\b[^>]*/>", block)
        block = _rescale(r'<a:ext\b[^>]*\bcx="\d+"[^>]*/>', block)
        changed += 1
        return block

    with zipfile.ZipFile(docx_path) as zin:
        names = zin.namelist()
        if doc not in names:
            return
        infos = {i.filename: i for i in zin.infolist()}
        blobs = {n: zin.read(n) for n in names}

    text = re.sub(
        r"<w:drawing>.*?</w:drawing>",
        _cap_drawing,
        blobs[doc].decode("utf-8"),
        flags=re.S,
    )
    if not changed:
        return
    blobs[doc] = text.encode("utf-8")

    tmp = docx_path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in names:
            zout.writestr(infos[n], blobs[n])
    tmp.replace(docx_path)


# The style an unlisted chapter's title takes in a DOCX: Heading 1's look, and
# the outline level of body text, so the contents field passes over it.
UNLISTED_DOCX_STYLE = "UnlistedHeading1"
_UNLISTED_DOCX_STYLE_XML = (
    f'<w:style w:type="paragraph" w:customStyle="1" '
    f'w:styleId="{UNLISTED_DOCX_STYLE}"><w:name w:val="Unlisted Heading 1" />'
    '<w:basedOn w:val="Heading1" /><w:next w:val="BodyText" />'
    '<w:pPr><w:outlineLvl w:val="9" /></w:pPr></w:style>'
)
_DOCX_HEADING1 = re.compile(r'<w:pStyle w:val="Heading1"\s*/>')


def heading_key(title: str) -> str:
    """What two forms of one title share: its letters and digits.

    The book's form is Markdown, with a span's brackets and attributes or a
    link's target; Word's or a PDF's is the rendered text, with typographic
    quotes. A key this coarse still tells any two chapters apart, which is
    all the match in `unlist_docx_headings`, or in a reader packet's reading
    of the PDF's tags, asks of it."""
    title = re.sub(r"\{[^}]*\}|\]\([^)]*\)", "", title)
    return re.sub(r"[\W_]+", "", title).lower()


def unlist_docx_headings(docx_path: Path, rows: list[Row]) -> list[str]:
    """Keep a DOCX's `.unlisted` chapters out of its contents.

    Pandoc writes a DOCX's contents as a field, `TOC \\o "1-3" \\h \\z \\u`,
    which Word fills from heading styles and outline levels when the reader
    updates it, so `.unlisted` reaches no DOCX: a chapter titled in Heading 1
    joins the contents the first time the field is updated. Each unlisted
    chapter's title moves to `UNLISTED_DOCX_STYLE`, based on Heading 1 so it
    looks the same, whose outline level is body text, and the field passes
    over it. Word's navigation pane passes over it too, as the EPUB's does.

    `rows` is `book_rows` of the book before its parts were flattened, since
    flattening makes each Part one Heading 1 in its place. The rows are
    matched to the Heading 1 paragraphs by position, and every
    pair's titles must agree, or ValueError: a heading that cannot be placed
    is not quietly left in the contents or taken out. Returns the titles
    moved."""
    if all(r.listed for r in rows):
        return []
    doc, styles = "word/document.xml", "word/styles.xml"
    with zipfile.ZipFile(docx_path) as zin:
        names = zin.namelist()
        infos = {i.filename: i for i in zin.infolist()}
        blobs = {n: zin.read(n) for n in names}
    text = blobs[doc].decode("utf-8")
    headings = [
        m
        for m in re.finditer(r"<w:p\b.*?</w:p>", text, flags=re.S)
        if _DOCX_HEADING1.search(m.group(0))
    ]
    if len(headings) != len(rows):
        raise ValueError(
            f"docx: {docx_path.name} has {len(headings)} chapter headings where "
            f"the book has {len(rows)} chapters, so its unlisted ones cannot be "
            "told apart"
        )
    moved: list[str] = []
    out, last = [], 0
    for n, (m, row) in enumerate(zip(headings, rows), start=1):
        runs = re.findall(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>", m.group(0))
        found = html.unescape("".join(runs)).strip()
        if row.number is not None:  # printed before the title it numbers
            found = re.sub(rf"^{row.number}\.?\s+", "", found)
        if heading_key(found) != heading_key(row.title):
            raise ValueError(
                f"docx: {docx_path.name}'s heading {n} reads {found!r} where the "
                f"book has {row.title!r}"
            )
        if row.listed:
            continue
        out += [
            text[last : m.start()],
            _DOCX_HEADING1.sub(
                f'<w:pStyle w:val="{UNLISTED_DOCX_STYLE}" />', m.group(0), count=1
            ),
        ]
        last = m.end()
        moved.append(row.title)
    blobs[doc] = ("".join(out) + text[last:]).encode("utf-8")
    sheet = blobs[styles].decode("utf-8")
    if f'w:styleId="{UNLISTED_DOCX_STYLE}"' not in sheet:
        sheet = sheet.replace("</w:styles>", _UNLISTED_DOCX_STYLE_XML + "</w:styles>")
        blobs[styles] = sheet.encode("utf-8")
    tmp = docx_path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in names:
            zout.writestr(infos[name], blobs[name])
    tmp.replace(docx_path)
    return moved


# --------------------------------------------------------------------------- #
# EPUB conformance (build side): what the writers leave invalid
# --------------------------------------------------------------------------- #
#
# Both defects below are INVISIBLE in a reader -- every desktop and device
# reader opens the book correctly with them in place -- and both are rejected
# by EPUBCheck, which is what a retailer's ingestion runs. So neither could be
# found by looking at the book, and `quirework publish epubcheck` is what found them.
# The fixes are applied to the built artifact for the same reason the DOCX
# image cap is: the defect is in what a writer emits, not in the source.

# `alt` is permitted on <img>, <area> and <input>, and on nothing else. Quarto
# copies a figure's alt text onto the WRAPPING float div as well as onto the
# image inside it, so the book shipped 39 invalid attributes. The image keeps
# its own copy, so removing the div's costs no accessibility text at all.
ALT_OK = frozenset({"img", "area", "input"})

_TAG = re.compile(r"<([a-z][a-z0-9-]*)\b([^>]*)>", re.I)
_ALT_ATTR = re.compile(r'\salt="[^"]*"')

# The faces stay INLINE in the content documents, and the reason is a measured
# one rather than a preference. Relocating them into the container's own
# `styles/stylesheet1.css` is the tidier fix on paper -- one copy instead of
# 38, and a url that resolves from a single place -- but crengine, the engine
# KOReader and the Kobos run, registers `@font-face` only from a document's own
# style element. Moved to the stylesheet, it silently stopped registering them
# and the book fell back to a default serif: valid, and set in the wrong face.
# That is the failure `fonts.py` refuses to render into, arrived at from the
# other direction, so the rule here is the same one. Verified by the engine's
# own "(cause: embedded fonts)" re-init line, which is what disappeared.
#
# So the fix is narrower: a face is kept wherever its url actually resolves,
# and dropped only from the document where it points nowhere. In practice that
# is `nav.xhtml`, which sits one level above `text/` and is not in the spine,
# so no reader renders it as a page anyway.
_FONT_FACE = re.compile(r"@font-face\s*\{[^}]*\}", re.S)
_SRC_URL = re.compile(r'url\("([^"]+)"\)')


def strip_invalid_alt(xhtml: str) -> tuple[str, int]:
    """Drop `alt` from every element not permitted to carry it."""
    removed = 0

    def _fix(m: "re.Match[str]") -> str:
        nonlocal removed
        tag, attrs = m.group(1), m.group(2)
        if 'alt="' not in attrs or tag.lower() in ALT_OK:
            return m.group(0)
        pruned = _ALT_ATTR.sub("", attrs)
        if pruned == attrs:
            return m.group(0)
        removed += 1
        return f"<{tag}{pruned}>"

    return _TAG.sub(_fix, xhtml), removed


def font_face_resolves(block: str, doc_name: str, entries: set[str]) -> bool:
    """Does every `src` url in this face reach a real file, from this document?

    A css url in a document's own style element resolves against THAT document,
    so the same rule is right in `text/ch001.xhtml` and wrong in `nav.xhtml` one
    level up. The book used to ship two urls per face for exactly that reason --
    `../fonts/` for the content documents and `fonts/` for nav -- and since a
    `src` list is tried in order, it always rendered correctly. But the url that
    did not match a given document's depth resolved nowhere, and a `src` url
    that resolves nowhere is invalid: 4 faces x 38 documents, 152 errors, in a
    book with no visible fault.
    """
    urls = _SRC_URL.findall(block)
    if not urls:
        return True  # nothing to resolve; not ours to judge
    base = posixpath.dirname(doc_name)
    return all(posixpath.normpath(posixpath.join(base, u)) in entries for u in urls)


def prune_font_faces(text: str, doc_name: str, entries: set[str]) -> tuple[str, int]:
    """Drop the faces that point nowhere from this document; keep the rest."""
    dropped = 0

    def _keep(m: "re.Match[str]") -> str:
        nonlocal dropped
        if font_face_resolves(m.group(0), doc_name, entries):
            return m.group(0)
        dropped += 1
        return ""

    return _FONT_FACE.sub(_keep, text), dropped


def conform_epub(epub_path: Path) -> None:
    """Make the built EPUB valid: drop the stray `alt`s and the dead faces.

    One pass over the container, then a repack that preserves entry ORDER and
    per-entry compression -- `mimetype` must stay first and stored or the OCF
    layer is invalid, which would trade two defects for a worse one.
    """
    with zipfile.ZipFile(epub_path) as zin:
        names = zin.namelist()
        infos = {i.filename: i for i in zin.infolist()}
        blobs = {n: zin.read(n) for n in names}

    entries = set(names)
    changed = False
    for name in names:
        if not name.endswith((".xhtml", ".html")):
            continue
        text = blobs[name].decode("utf-8")
        before = text
        text, _ = strip_invalid_alt(text)
        text, _ = prune_font_faces(text, name, entries)
        if text != before:
            blobs[name] = text.encode("utf-8")
            changed = True

    if not changed:
        return

    tmp = epub_path.with_suffix(".tmp.epub")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in names:
            zout.writestr(infos[n], blobs[n])
    tmp.replace(epub_path)


# --------------------------------------------------------------------------- #
# Typst prologues (build side): the settings that must land mid-document
# --------------------------------------------------------------------------- #

# Some print settings cannot live in the template, because they have to take
# effect *partway through the book* rather than at the top of it: front matter
# numbered in roman and the body restarting at arabic 1, and the back matter
# set smaller than the argument. A template runs once, before any of it.
#
# Typst's own scoping is what forces this. A `set` rule inside a function body
# applies to that function's own content and reverts afterwards, so `part()`
# cannot switch the numbering of everything that follows it; and hand-rolling
# the folio in the page footer is not a way out either, because `#outline`
# formats each contents entry with the `page.numbering` in effect at its target
# and would print the wrong figures.
#
# What DOES work is a raw block sitting in the document flow, where a `set`
# rule applies to the rest of the body. These blocks are written into staged
# copies only, so the configured sources keep no build scaffolding
# and no prose file has to carry a print setting.
#
# `{=typst}` means every other format ignores the block, so HTML, EPUB and DOCX
# are untouched and no guard is needed for them.


# Appended, not prepended, and WHICH END MATTERS. Both statements below are
# scoped to a page boundary, and putting them at the head of the first chapter
# put them on the wrong side of it: `set page` applies from the NEXT page, so
# the chapter that carried the rule kept roman while everything after it went
# arabic, and the contents printed "Steeping yourself ... xi" between two
# arabic rows. Issued at the END of the last front-matter page, the rule takes
# effect on the page after it, which is the first page of the body.
#
# `counter(page).update(0)` rather than 1 for the same reason: it applies to
# the page it lands on, the preface's last, so the part divider becomes 0 and
# the first chapter becomes 1. Neither of those two prints a folio, so no
# reader sees a zero.


def inject_typst_prologues(
    stage: Path,
    prologues: dict[str, str] | None = None,
    epilogues: dict[str, str] | None = None,
) -> list[str]:
    """Wrap named staged chapters in their Typst-only raw blocks.

    Returns the chapters actually touched. Missing files are skipped rather
    than raised on: a stage may legitimately hold a subset of the book (a
    single-chapter redpen build, a panel kit), and a block that has nowhere to
    land is not an error there.
    """
    touched: list[str] = []
    for rel, body in (
        _options().TYPST_PROLOGUES if prologues is None else prologues
    ).items():
        f = stage / rel
        if not f.is_file():
            continue
        block = "```{=typst}\n" + body + "```\n\n"
        text = f.read_text(encoding="utf-8")
        if block not in text:
            f.write_text(block + text, encoding="utf-8")
            touched.append(rel)
    for rel, body in (
        _options().TYPST_EPILOGUES if epilogues is None else epilogues
    ).items():
        f = stage / rel
        if not f.is_file():
            continue
        block = "\n```{=typst}\n" + body + "```\n"
        text = f.read_text(encoding="utf-8")
        if block not in text:
            f.write_text(text.rstrip("\n") + "\n" + block, encoding="utf-8")
            touched.append(rel)
    return touched


# The title a staged chapter is reduced to when a format should not print it.
# The configured Typst template matches this exact string and renders nothing.
PRINT_OMIT_MARKER = "quirework-omit-from-this-format"


def blank_chapters(stage: Path, rels: set[str]) -> list[str]:
    """Empty named chapters in a staged book, leaving their config entry.

    For a front-matter page that belongs to one format and not another. The
    reader-facing case is the welcome landing: Quarto pins `index.qmd` to
    reading-position 1 in EVERY format (the filename is special-cased, so a
    list position elsewhere is ignored), and it is the web edition's home page,
    but in print it is a two-sentence leaf sitting where a printed book has
    nothing.

    Two routes were tried and neither works, so the shape of this is forced:

    - **Quarto's conditional content leaves the heading.** Wrapping the file in
      `content-visible unless-format="typst"` drops the body and keeps the H1,
      because a chapter's opening heading is hoisted out of the div before the
      condition is evaluated. Print then shows a page with just a title on it,
      which is worse than the page it replaced.
    - **The entry cannot be removed.** Deleting it from `book.chapters` fails
      the render outright: "Book contents must include a home page".

    - **Emptying the file is not enough either.** Quarto synthesizes a chapter
      heading for a file with no H1 of its own, and synthesizes it NUMBERED:
      the blank leaf came back as chapter 1 with a stray numeral on it, and
      every real chapter shifted up by one.

    So the entry stays, and the file is replaced by an unnumbered, unlisted
    heading carrying `PRINT_OMIT_MARKER`. Unnumbered keeps it out of the
    chapter count and unlisted keeps it out of the contents; the marker is what
    the configured Typst template matches to render nothing at all, so no page is
    emitted. The marker only ever exists in a staged copy.

    Returns the chapters actually emptied. A plain Quarto project renders
    each document on its own, with no front matter to drop, so nothing in
    one is emptied.
    """
    if not is_book(stage):
        return []
    lines, ci, end, _item_ind, items = read_items(stage)
    known = {it.value for it in items if it.kind == "chapter"}
    for it in items:
        known.update(it.children)
    blanked = []
    for rel in sorted(rels & known):
        f = stage / rel
        if not f.is_file():
            continue
        f.write_text(
            f"# {PRINT_OMIT_MARKER} {{.unnumbered .unlisted}}\n", encoding="utf-8"
        )
        blanked.append(rel)
    return blanked


# --------------------------------------------------------------------------- #
# metadata a stage sets: a reader instrument's render of the book as published
# --------------------------------------------------------------------------- #
_METADATA_KEY = re.compile(r"[A-Za-z][\w-]*")


def set_top_metadata(stage: Path, values: dict) -> list[str]:
    """Set each of `values` as a top-level key of the staged `_quarto.yml`.

    The top level is where a book declares metadata that every chapter and
    every filter reads. A key the file already declares has its value, with
    any block under it, replaced in place, and a new key is added at the end.
    Values are TOML's scalars, strings, booleans and numbers, written as JSON,
    which YAML reads as the same value: `false` stays the boolean a filter
    tests for, never the string. Operates on staged copies only.

    Raises ValueError for a key YAML would read differently or a value that is
    not a scalar, rather than write a file Quarto reads another way. Returns
    the keys set."""
    for key, value in values.items():
        if not _METADATA_KEY.fullmatch(key):
            raise ValueError(f"metadata key {key!r} is not a plain name")
        if not isinstance(value, (str, bool, int, float)):
            raise ValueError(
                f"metadata {key} must be a string, a number or a boolean, "
                f"not {type(value).__name__}"
            )
    cfg = stage / BOOK_CONFIG
    lines = cfg.read_text(encoding="utf-8").splitlines()
    for key, value in values.items():
        line = f"{key}: {json.dumps(value, ensure_ascii=False)}"
        at = next(
            (i for i, s in enumerate(lines) if re.match(rf"{re.escape(key)}\s*:", s)),
            None,
        )
        if at is None:
            lines.append(line)
            continue
        end = at + 1  # the value's block: indented or blank lines under it
        while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t"):
            end += 1
        while end > at + 1 and not lines[end - 1].strip():
            end -= 1
        lines[at:end] = [line]
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list(values)


# --------------------------------------------------------------------------- #
# reading a stage's configuration back, and setting a book option in it
# --------------------------------------------------------------------------- #
_YAML_TRUE = frozenset({"true", "True", "TRUE"})
_YAML_FALSE = frozenset({"false", "False", "FALSE"})
_SINGLE_QUOTED = re.compile(r"'((?:[^']|'')*)'")


def yaml_scalar(text: str) -> object:
    """A YAML scalar as Quarto's core schema reads it, for the shapes a book's
    configuration writes at its top level: a quoted or plain string, or a
    boolean. Numbers and dates stay the text they were written as, and an empty
    value (a block follows) is None. Only `true` and `false` in the three
    spellings the core schema accepts are booleans, so a quoted "false" is the
    string it looks like, as it is to Quarto."""
    text = text.strip()
    if not text:
        return None
    if text[0] == '"':
        try:
            return json.JSONDecoder().raw_decode(text)[0]
        except ValueError:
            return text
    if text[0] == "'":
        m = _SINGLE_QUOTED.match(text)
        return m.group(1).replace("''", "'") if m else text
    plain = re.split(r"\s+#", text, maxsplit=1)[0].strip()
    if plain in _YAML_TRUE:
        return True
    if plain in _YAML_FALSE:
        return False
    return plain


def top_metadata(stage: Path, key: str) -> object:
    """The value of top-level `key` in the stage's `_quarto.yml`, read as
    `yaml_scalar` reads it, or None when the file does not set it or sets a
    block under it."""
    for line in (stage / BOOK_CONFIG).read_text(encoding="utf-8").splitlines():
        m = re.match(rf"{re.escape(key)}\s*:(.*)$", line)
        if m:
            return yaml_scalar(m.group(1))
    return None


def _book_block(lines: list[str]) -> tuple[int, int, int] | None:
    """(the `book:` line, the end of its block, its children's indent)."""
    at = next((i for i, s in enumerate(lines) if re.match(r"book\s*:\s*$", s)), None)
    if at is None:
        return None
    end, indent = at + 1, None
    while end < len(lines):
        line = lines[end]
        if line.strip() and not line.lstrip().startswith("#"):
            width = len(line) - len(line.lstrip())
            if width == 0:
                break
            indent = width if indent is None else indent
        elif line.strip() and line[0] not in " \t":
            break  # a comment at column 0 belongs to whatever follows
        end += 1
    return at, end, indent or 2


def book_option(stage: Path, key: str) -> object:
    """The value of `book.KEY` in the stage's `_quarto.yml`, read as
    `yaml_scalar` reads it, or None when the book does not set it."""
    lines = (stage / BOOK_CONFIG).read_text(encoding="utf-8").splitlines()
    block = _book_block(lines)
    if block is None:
        return None
    at, end, indent = block
    pattern = re.compile(rf" {{{indent}}}{re.escape(key)}\s*:(.*)$")
    for line in lines[at + 1 : end]:
        m = pattern.match(line)
        if m:
            return yaml_scalar(m.group(1))
    return None


def set_book_option(stage: Path, key: str, value: str | bool | int | float) -> None:
    """Set `book.KEY` in the staged `_quarto.yml`, replacing the value and any
    block under it in place, or adding the key as the block's first child.

    The value is written as JSON, which YAML reads as the same scalar. Raises
    ValueError for a key YAML would read differently, a value that is not a
    scalar, or a configuration with no `book:` block to set it in. Operates on
    staged copies only."""
    if not _METADATA_KEY.fullmatch(key):
        raise ValueError(f"book option {key!r} is not a plain name")
    if not isinstance(value, (str, bool, int, float)):
        raise ValueError(f"book.{key} must be a scalar, not {type(value).__name__}")
    cfg = stage / BOOK_CONFIG
    lines = cfg.read_text(encoding="utf-8").splitlines()
    block = _book_block(lines)
    if block is None:
        raise ValueError(f"{cfg} has no book: block to set {key} in")
    at, end, indent = block
    line = f"{' ' * indent}{key}: {json.dumps(value, ensure_ascii=False)}"
    pattern = re.compile(rf" {{{indent}}}{re.escape(key)}\s*:")
    found = next((i for i in range(at + 1, end) if pattern.match(lines[i])), None)
    if found is None:
        lines.insert(at + 1, line)
    else:
        stop = found + 1  # the value's own block, indented deeper than its key
        while stop < end and (
            not lines[stop].strip()
            or len(lines[stop]) - len(lines[stop].lstrip()) > indent
        ):
            stop += 1
        while stop > found + 1 and not lines[stop - 1].strip():
            stop -= 1
        lines[found:stop] = [line]
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")


def repack_zip(path: Path, blobs: dict[str, bytes]) -> None:
    """Rewrite the zip at `path` with some entries' contents replaced.

    Entry order and each entry's compression are kept, which an EPUB needs:
    `mimetype` has to stay first and stored, or the container is invalid."""
    with zipfile.ZipFile(path) as zin:
        infos = zin.infolist()
        contents = {i.filename: zin.read(i.filename) for i in infos}
    contents.update(blobs)
    tmp = path.with_name(path.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in infos:
            zout.writestr(info, contents[info.filename])
    tmp.replace(path)
