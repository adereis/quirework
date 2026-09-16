"""Staged-book structure utilities shared by render, redpen, and panel-kit.

Everything here operates on a *staged* copy of the book (`build/...`), never on
`docs/` itself: walking and rewriting the staged `_quarto.yml`'s `book.chapters`
block (parts flattened for the formats that have no part concept), unnumbering a
promoted part's H1, copying a figure gallery beside a standalone chapter, capping
DOCX image widths for Google Docs, and repairing the two conformance defects the
EPUB writers emit. These grew up inside redpen (the
first consumer) and were extracted once render and panel-kit needed them too:
they are book-structure concepts, not review concepts, and their old home forced
lazy-import cycles in both directions.
"""

from __future__ import annotations

from quirework.project import current_project
import posixpath
import re
import shutil
import zipfile
from pathlib import Path
from typing import NamedTuple

from quirework.book import figures as _figures

# --------------------------------------------------------------------------- #
# book parts (build side): docx has none, so flatten them away
# --------------------------------------------------------------------------- #
BOOK_CONFIG = "_quarto.yml"
_CHAPTER_SUFFIXES = (".qmd", ".md", ".ipynb")

_YAML_ITEM = re.compile(r"^(\s*)-\s+(.*?)\s*$")
_H1_ATTRS = re.compile(r"(?m)^(# .+?)(?:[ \t]*\{(.*?)\})?[ \t]*$")


def _options():
    """Read policy from the selected project for this operation."""
    from types import SimpleNamespace

    TYPST_PROLOGUES: dict[str, str] = (
        current_project().data("book.layout_policy", {}).get("typst_prologues", {})
    )
    TYPST_EPILOGUES: dict[str, str] = (
        current_project().data("book.layout_policy", {}).get("typst_epilogues", {})
    )
    return SimpleNamespace(
        TYPST_EPILOGUES=TYPST_EPILOGUES, TYPST_PROLOGUES=TYPST_PROLOGUES
    )


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _unquote(s: str) -> str:
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'" else s


def _find_chapters_block(lines: list[str]) -> tuple[int, int, int]:
    """Locate `book:` → `chapters:` in a staged `_quarto.yml`: (key line, first line
    after the list, the key's indent). Raises ValueError when it is not there."""
    try:
        bi = next(i for i, l in enumerate(lines) if l.rstrip() == "book:")
    except StopIteration:
        raise ValueError(f"{BOOK_CONFIG}: no top-level `book:` key") from None
    ci = None
    for i in range(bi + 1, len(lines)):
        s = lines[i].strip()
        if not s or s.startswith("#"):
            continue
        if _indent(lines[i]) == 0:
            break  # left the book: block without finding it
        if s == "chapters:":
            ci = i
            break
    if ci is None:
        raise ValueError(f"{BOOK_CONFIG}: no `chapters:` under `book:`")
    cind = _indent(lines[ci])
    j = ci + 1
    while j < len(lines) and (not lines[j].strip() or _indent(lines[j]) > cind):
        j += 1
    while j - 1 > ci and not lines[j - 1].strip():
        j -= 1  # leave the blank line before the next key
    return ci, j, cind


def _part_chapters(cont: list[str]) -> list[str]:
    """The chapter files nested under one `- part:` item."""
    files: list[str] = []
    seen = False
    for line in cont:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s == "chapters:":
            seen = True
            continue
        m = _YAML_ITEM.match(line)
        if not (seen and m):
            raise ValueError(f"{BOOK_CONFIG}: unexpected line under a part: {line!r}")
        files.append(_unquote(m.group(2)))
    return files


class _Item(NamedTuple):
    """One entry in a staged `book.chapters` list, as `_chapters_items` reads it.

    `kind` is `comment` (the line verbatim, so a rewrite can put it back),
    `chapter` (a file), `part-file` (`part: canon/part-two.qmd`, a divider that
    is itself prose) or `part-title` (`part: "Part One: …"`, a bare heading).
    `value` is the comment line, the file path, or the part title; `children`
    are the chapter files nested under a part."""

    kind: str
    value: str
    children: list[str]


def _chapters_items(lines: list[str], ci: int, end: int) -> tuple[int, list[_Item]]:
    """Parse a located `book.chapters` block into `(item indent, items)`.

    **The single walker.** Every consumer of the book's reading order goes
    through this -- `book_chapters` (read-only), `flatten_book_parts` (rewrites
    the block) and `book_rows` (the numbered reading order, which the Contents
    page and the reader kit are both projections of) -- so a `_quarto.yml` shape
    one of them understands cannot silently mislead another. There used to be two hand-rolled walkers here that agreed only by
    inspection. Raises ValueError on a shape this does not recognize, so a
    future `_quarto.yml` cannot be quietly read as a partial book."""
    block = lines[ci + 1 : end]
    real = [l for l in block if l.strip() and not l.strip().startswith("#")]
    if not real:
        raise ValueError(f"{BOOK_CONFIG}: `book.chapters` is empty")
    item_ind = _indent(real[0])

    items: list[_Item] = []
    k = 0
    while k < len(block):
        line = block[k]
        s = line.strip()
        k += 1
        if not s:
            continue
        if s.startswith("#"):
            items.append(_Item("comment", line, []))
            continue
        m = _YAML_ITEM.match(line)
        if not m or _indent(line) != item_ind:
            raise ValueError(
                f"{BOOK_CONFIG}: unexpected line in book.chapters: {line!r}"
            )
        cont: list[str] = []
        while k < len(block) and (not block[k].strip() or _indent(block[k]) > item_ind):
            cont.append(block[k])
            k += 1
        body = m.group(2)
        if not body.startswith("part:"):
            if [l for l in cont if l.strip()]:
                raise ValueError(
                    f"{BOOK_CONFIG}: nested keys under a chapter: {line!r}"
                )
            items.append(_Item("chapter", _unquote(body), []))
            continue
        val = _unquote(body[len("part:") :].strip())
        kids = _part_chapters(cont)
        if val.endswith(_CHAPTER_SUFFIXES):
            items.append(_Item("part-file", val, kids))
        elif val:
            items.append(_Item("part-title", val, kids))
        else:
            raise ValueError(f"{BOOK_CONFIG}: a `part:` with neither title nor file")
    return item_ind, items


def _read_items(stage: Path) -> tuple[list[str], int, int, int, list[_Item]]:
    """`_chapters_items` over a stage's `_quarto.yml`, plus what a rewrite needs."""
    lines = (stage / BOOK_CONFIG).read_text(encoding="utf-8").splitlines()
    ci, end, _ = _find_chapters_block(lines)
    item_ind, items = _chapters_items(lines, ci, end)
    return lines, ci, end, item_ind, items


def book_chapters(stage: Path) -> list[str]:
    """Ordered chapter files from a staged `_quarto.yml`, parts flattened.

    Reading order, one entry per real file: bare chapters, file-based part dividers
    (`part: canon/part-two.qmd`), and each part's nested chapters. Title-only parts
    (`part: "Part One: …"`) contribute nothing, since they name no file."""
    _, _, _, _, items = _read_items(stage)
    out: list[str] = []
    for it in items:
        if it.kind in ("chapter", "part-file"):
            out.append(it.value)
        out.extend(it.children)
    return out


def _divider_slug(title: str) -> str:
    """A staged divider file's stem, by the book's own chapter-naming rule: the
    title lowercased and hyphenated, truncated at the first comma or colon. So
    "Part One: What spouts do" is `part-one.qmd`."""
    head = re.split(r"[,:]", title, maxsplit=1)[0]
    return re.sub(r"[^a-z0-9]+", "-", head.lower()).strip("-") or "part"


def _divider_heading(title: str) -> str:
    """A divider H1's inline content: the title, split into its label and its
    name so a stylesheet can set the two apart.

    Print does the same split in `docs/typst-show.typ` and for the same reason:
    every part title reads "Part Two: What we pay them", and set as one line it
    is too long for the measure. The label goes above a rule, small and tracked;
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
    style it as the page it is (`docs/epub-styles.html`)."""
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
    `part:` from those formats. For a *file*-based part (`part: canon/part-two.qmd`)
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
    future `_quarto.yml` cannot be silently mangled into a partial book."""
    lines, ci, end, item_ind, items = _read_items(stage)
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
    _, _, _, _, items = _read_items(stage)
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
            .data("book.render_policy", {})
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


# --------------------------------------------------------------------------- #
# EPUB conformance (build side): what the writers leave invalid
# --------------------------------------------------------------------------- #
#
# Both defects below are INVISIBLE in a reader -- every desktop and device
# reader opens the book correctly with them in place -- and both are rejected
# by EPUBCheck, which is what a retailer's ingestion runs. So neither could be
# found by looking at the book, and `quirework book epubcheck` is what found them.
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
# rule applies to the rest of the body. `contents.qmd` is the precedent: it has
# carried a raw `#outline` since the book had a contents page. These blocks are
# written into the STAGED copies only, so `docs/` keeps no build scaffolding
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
# `docs/typst-show.typ` matches this exact string and renders nothing; the two
# must stay in step, and this is the only place it is spelled.
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
    `docs/typst-show.typ` matches to render nothing at all, so no page is
    emitted. The marker only ever exists in a staged copy.

    Returns the chapters actually emptied.
    """
    lines, ci, end, _item_ind, items = _read_items(stage)
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
