"""Airgap-ready artifacts for blind reader panels.

From a single fresh render, build the two artifact shapes the panels read:

  - `book.pdf`              -- the whole book, figures embedded (Path B: whole-book panels)
  - `chapters/NN-slug.pdf`  -- one PDF per reading segment (Path C: sequential reveal)
  - `manifest.json`         -- per-segment title, filename, page count, and the source SHA

Segments are sliced out of `book.pdf`, never rendered a second time. They retain
the published trim, typography, figures, running furniture, and part dividers.
A standalone chapter render would be a different edition and could make a
reader report a packaging defect as a defect in the canon.

The slice is taken from the PDF's **own outline**, not from a page-range table
and not by fingerprinting page text: `bookstage.book_outline` builds the same
labels Quarto writes as bookmarks, so the two are compared entry by entry and a
mismatch is a hard error rather than a kit that is off by a chapter. A template
that bookmarks no Part divider, as Quarto 1.9's Typst book template does, leaves
the dividers' pages to the links on the book's Contents page, which must agree
with every chapter's bookmark (`entry_pages`).

Segments are the units a reader actually consumes, which need not be one per file:

  - unlisted front matter is one block, with any pages the template prints
    before the book, such as a cover, a title page and its Contents;
  - a Part divider RIDES on the chapter it opens, as it is bound in the book --
    the reader turns the divider and the chapter starts;
  - back matter is one block;
  - each remaining chapter is its own segment.

`--run-dir` assembles the neutral directory a blind seat is pointed at: the body
segments only (preface through the last chapter), no `book.pdf`, and a manifest
    describing exactly what was copied.

This module only builds the artifacts. It never launches a persona: the agent
    drives the blind runs under the project procedure. The kit lives under `build/` (inside the
repo, git-ignored), so a blind seat must never read it in place -- assemble a run
dir out of it, or the repo's `AGENTS.md` leaks into the read.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from quirework.project import current_project
from quirework.publish import bookstage as _bookstage
from quirework.publish import render as _render
from quirework.delivery import text as _text

# the kit output tree (git-ignored)
# own render stage (see `build`)


def _settings():
    """Resolve paths from the current operation, without import-time state."""
    from types import SimpleNamespace

    BUILD_KIT = current_project().path("publish.build", "build") / "panel-kit"
    STAGE_KIT = current_project().path("publish.build", "build") / "panel-stage"
    return SimpleNamespace(BUILD_KIT=BUILD_KIT, STAGE_KIT=STAGE_KIT)


def _pypdf():
    """The PDF library, or a message saying how to get it. Reading the outline and
    cutting the segments are both structural operations on the rendered book, so
    this is a real dependency of the kit rather than the soft fallback it is in
    `page_count` -- it ships in the `docs` extra beside Quarto."""
    try:
        import pypdf
    except ModuleNotFoundError:
        raise SystemExit(
            "packet: pypdf is required to slice the book (`pip install -e '.[docs]'`)."
        ) from None
    return pypdf


def slug(title: str) -> str:
    """A segment's filename stem, by the book's own chapter-naming rule: lowercased
    and hyphenated, truncated at the first comma or colon."""
    head = re.split(r"[,:]", title, maxsplit=1)[0]
    return re.sub(r"[^a-z0-9]+", "-", head.lower()).strip("-") or "segment"


def page_count(pdf: Path) -> int:
    """Page count via poppler's `pdfinfo`, falling back to `pypdf`. Raises if neither
    is available rather than ship a kit with a silent 0 -- the whole-book count drives
    the panels' coverage gate ("read all N pages")."""
    info = shutil.which("pdfinfo")
    if info:
        out = subprocess.run([info, str(pdf)], capture_output=True, text=True)
        for line in out.stdout.splitlines():
            if line.startswith("Pages:"):
                return int(line.split(":", 1)[1].strip())
    try:
        import pypdf

        return len(pypdf.PdfReader(str(pdf)).pages)
    except ModuleNotFoundError:
        pass
    raise RuntimeError(
        f"cannot count pages of {pdf.name}: install poppler (pdfinfo) or pypdf"
    )


def outline_pages(pdf: Path) -> list[tuple[str, int]]:
    """The rendered book's own top-level bookmarks: `(title, first page)`, in order.

    Quarto writes one per listed heading, from the same reading order and the
    same H1s `bookstage.book_rows` walks, which is why the two can be checked
    against each other. Whether a Part divider gets one depends on the template:
    the Typst book template of Quarto 1.9 bookmarks none (see `entry_pages`).
    Nested entries (a chapter's sections) are skipped: only the book's top level
    cuts a segment."""
    pypdf = _pypdf()
    reader = pypdf.PdfReader(str(pdf))
    rows: list[tuple[str, int]] = []
    for item in reader.outline:
        if isinstance(item, list):  # a nested level, not a book entry
            continue
        rows.append((str(item.title), reader.get_destination_page_number(item) + 1))
    return rows


def heading_pages(pdf: Path) -> list[tuple[str, int]] | None:
    """The top-level headings the PDF's tags record: `(title, page)`, in order.

    None for a PDF with no tags. The outline bookmarks only what the Contents
    lists, and Typst writes a named destination only where something links, so
    an unlisted page, such as a back cover, appears in neither. A tagged PDF,
    which Typst writes by default from 0.14, records every heading for screen
    readers in its structure tree, each with its page and its title. That is
    the compiler's own account of the book, not a guess from a page's text.
    A heading the tags give no title reads as an empty one."""
    pypdf = _pypdf()
    reader = pypdf.PdfReader(str(pdf))
    tree = reader.trailer["/Root"].get("/StructTreeRoot")
    if tree is None:
        return None
    number = {p.indirect_reference.idnum: n for n, p in enumerate(reader.pages, 1)}

    def kids(node) -> list:
        k = node.get("/K")
        k = k.get_object() if hasattr(k, "get_object") else k
        return list(k) if isinstance(k, list) else [] if k is None else [k]

    def page_of(node) -> int | None:
        pg = node.get("/Pg")
        if pg is not None:
            return number.get(pg.idnum)
        for kid in kids(node):
            kid = kid.get_object() if hasattr(kid, "get_object") else kid
            if isinstance(kid, dict) and (found := page_of(kid)) is not None:
                return found
        return None

    found: list[tuple[str, int]] = []
    stack = [tree.get_object()]
    while stack:
        node = stack.pop()
        if node.get("/S") == "/H1":
            page = page_of(node)
            if page is not None:
                found.append((str(node.get("/T", "")), page))
            continue
        for kid in reversed(kids(node)):
            kid = kid.get_object() if hasattr(kid, "get_object") else kid
            if isinstance(kid, dict):
                stack.append(kid)
    return found


def contents_pages(pdf: Path, before: int) -> list[int]:
    """The pages the book's Contents page links to, in the order it lists them.

    Read from the internal links on the pages before `before`, the first
    bookmarked entry's page, which is where a book's Contents page sits; the
    body's own cross-references are never read. A title and its page number
    are two links to one place and count once."""
    pypdf = _pypdf()
    reader = pypdf.PdfReader(str(pdf))
    numbers = {
        (p.indirect_reference.idnum, p.indirect_reference.generation): n + 1
        for n, p in enumerate(reader.pages)
    }
    named = reader.named_destinations
    found: list[tuple[object, int]] = []
    for page in reader.pages[: before - 1]:
        links = []
        for annot in page.get("/Annots") or []:
            annot = annot.get_object()
            if annot.get("/Subtype") != "/Link":
                continue
            action = annot.get("/A")
            dest = (
                annot.get("/Dest") if action is None else action.get_object().get("/D")
            )
            if dest is None:  # a link to the web, not into the book
                continue
            if isinstance(dest, str):
                if dest not in named:
                    continue
                key, target = dest, reader.get_destination_page_number(named[dest]) + 1
            else:
                ref = dest[0].indirect_reference or dest[0]
                target = numbers.get((ref.idnum, ref.generation))
                if target is None:
                    continue
                key = (target, *(str(v) for v in dest[1:]))
            left, _, _, top = (float(v) for v in annot["/Rect"])
            links.append((-top, left, key, target))
        found.extend((key, target) for _, _, key, target in sorted(links))
    pages: list[int] = []
    last = None
    for key, target in found:
        if key != last:
            pages.append(target)
        last = key
    return pages


# A chapter's bookmark carries its printed number however the template writes
# it: "4 Title" in one, "4. Title" in Quarto 1.9's Typst book template.
_NUMBERED = re.compile(r"^(\d+)\.?\s+")


def _label(text: str) -> str:
    return _NUMBERED.sub(r"\1 ", text.strip())


def entry_pages(
    outline: list[tuple[str, str]],
    bookmarks: list[tuple[str, int]],
    contents: list[int],
) -> list[int]:
    """The first page of every listed entry, checked against the book's order.

    `outline` is `bookstage.book_outline`'s `(kind, label)`; `bookmarks` the
    rendered book's `outline_pages`; `contents` its `contents_pages`. A template
    that bookmarks every Part answers alone. One that bookmarks only chapters,
    as Quarto 1.9's Typst book template does, leaves the Parts to the Contents
    page, which links every entry; its link for each chapter must then land on
    that chapter's bookmarked page, or a Part's page is not to be trusted.
    Raises ValueError, naming the first disagreement."""
    want = [_label(label) for _, label in outline]
    titles = [_label(title) for title, _ in bookmarks]
    parts = {label for kind, label in outline if kind == "part"}
    chapters = [label for (kind, _), label in zip(outline, want) if kind == "chapter"]
    expected = want if not parts or parts & set(titles) else chapters
    if titles != expected:
        for n, (a, b) in enumerate(zip(expected, titles)):
            if a != b:
                raise ValueError(
                    f"packet: the rendered outline diverges from the book's "
                    f"reading order at entry {n + 1}: {a!r} vs {b!r}"
                )
        raise ValueError(
            f"packet: the book lists {len(expected)} entries, its rendered "
            f"outline {len(titles)}"
        )
    if expected is want:
        return [page for _, page in bookmarks]
    if len(contents) != len(want):
        raise ValueError(
            f"packet: the rendered book bookmarks no Part, and its Contents page "
            f"links {len(contents)} entries where the book lists {len(want)}, so "
            "the kit cannot place its Parts"
        )
    marks = iter(page for _, page in bookmarks)
    for (kind, _), label, page in zip(outline, want, contents):
        if kind == "chapter" and page != (mark := next(marks)):
            raise ValueError(
                f"packet: the Contents page sends {label!r} to page {page}, its "
                f"bookmark to page {mark}"
            )
    return contents


@dataclass
class Segment:
    """One unit of the kit: a slice of `book.pdf` a reader consumes in one sitting."""

    kind: str  # "front-matter" | "chapter" | "back-matter"
    stem: str  # filename stem (a chapter's `.qmd` stem)
    title: str
    start: int  # first page in book.pdf, 1-based inclusive
    end: int = 0  # last page, filled in once the next segment is known
    number: int | None = None  # the printed chapter number, None for everything else
    part: str | None = None  # the Part this segment sits in
    opens_part: str | None = None  # the Part whose divider page rides on this segment
    contains: list[str] = field(default_factory=list)  # a block's constituent pages

    @property
    def pages(self) -> int:
        return self.end - self.start + 1


def body_span(rows: list[_bookstage.Row]) -> tuple[int, int]:
    """The book's body as `(head, tail)`, the slice `rows[head:tail]`.

    The body runs from the first listed entry through the last. The unlisted
    pages before it are front matter, such as a welcome or a copyright page.
    The unlisted pages after it are back matter, such as a back cover written
    the way the authoring guide shows, `.unnumbered .unlisted` after the last
    chapter. A reader turns past both, and the Contents lists neither.

    The packet slices on this and a self-driven directory leaves out what it
    excludes, so the two cannot disagree about where a book starts and ends.
    When each kept its own copy, one handed a reader the back cover as a
    chapter and the other refused the book."""
    listed = [i for i, r in enumerate(rows) if r.kind == "part" or r.listed]
    if not listed:
        return len(rows), len(rows)
    return listed[0], listed[-1] + 1


def body_parts(rows: list[_bookstage.Row]) -> set[str | None]:
    """The Parts that hold a numbered chapter, None standing for none at all.
    Any other Part is matter, such as a "Back matter" Part of sources and a
    colophon, and a packet collapses it into one block."""
    return {r.part for r in rows if r.kind == "chapter" and r.number is not None}


def back_matter_start(
    headings: list[tuple[str, int]] | None,
    rows: list[_bookstage.Row],
    starts: list[int],
) -> int | None:
    """The page the unlisted pages after the body start on, from the PDF's
    tagged `headings`, or None when the tags do not show it.

    `rows` and `starts` are what `plan_segments` takes. The tags are trusted
    only where they agree with the outline: every listed chapter's heading
    must be tagged, in order, on the page its bookmark names. Otherwise the
    two describe different books, and this raises rather than cut the back
    matter off at a guess."""
    _, tail = body_span(rows)
    if tail == len(rows) or headings is None:
        return None
    key = _bookstage.heading_key
    listed = [i for i, r in enumerate(rows) if r.kind == "part" or r.listed]
    seen = -1
    for i, page in zip(listed, starts):
        if rows[i].kind != "chapter":
            continue
        want = key(rows[i].title)
        at = next(
            (j for j in range(seen + 1, len(headings)) if key(headings[j][0]) == want),
            None,
        )
        if at is None or headings[at][1] != page:
            tagged = "untagged" if at is None else f"tagged on page {headings[at][1]}"
            raise ValueError(
                f"packet: the PDF bookmarks {rows[i].title} on page {page} but has "
                f"it {tagged}, so its tags cannot place the back matter"
            )
        seen = at
    first = key(rows[tail].title)
    return next((p for title, p in headings[seen + 1 :] if key(title) == first), None)


def plan_segments(
    rows: list[_bookstage.Row],
    starts: list[int],
    total: int,
    back_start: int | None = None,
) -> list[Segment]:
    """Group the book's reading order into the segments a reader consumes.

    `rows` is `bookstage.book_rows` (parts intact); `starts` is the first page of
    each row that the PDF outline lists, in the same order; `total` is the book's
    page count. `back_start` is the page the unlisted pages after the body begin
    on, which no outline records; a book with such pages needs it. Pure, so the
    grouping rules are testable without rendering a book.

    A Part divider is not a segment. It rides on the chapter it opens, because
    that is where it is bound and where the reader turns it. A Part with no
    numbered chapter in it is matter rather than body, so it collapses -- divider
    and all its pages -- into one block; that rule is what makes the back matter
    a single file without naming it in code. The unlisted pages after the body
    (`body_span`) are one back-matter block too, or join the matter Part they
    follow."""
    listed = [i for i, r in enumerate(rows) if r.kind == "part" or r.listed]
    if len(listed) != len(starts):
        raise ValueError(
            f"packet: the book lists {len(listed)} entries but its "
            f"PDF outline has {len(starts)}"
        )
    if not listed:
        raise ValueError("packet: the book has no listed entries to slice on")
    page_of = dict(zip(listed, starts))
    head, tail = body_span(rows)
    kept = body_parts(rows)
    matter_parts = {r.title for r in rows if r.kind == "part"} - kept
    for r in rows[head + 1 : tail]:
        if r.kind == "chapter" and not r.listed and r.part not in matter_parts:
            raise ValueError(
                "packet: an .unlisted page inside the body has no segment "
                f"to sit in: {r.title}"
            )

    segs: list[Segment] = []

    # The pages before the first entry: the book's unlisted ones, and any the
    # template prints itself, such as a cover, a title page and its Contents.
    if head > 0 or page_of[head] > 1:
        segs.append(
            Segment(
                "front-matter",
                "front-matter",
                "Front matter",
                start=1,
                contains=[r.title for r in rows[:head]],
            )
        )

    pending: tuple[str, int] | None = None  # a Part divider waiting for its chapter
    i = head
    while i < tail:
        r = rows[i]
        if r.kind == "part":
            if r.title in kept:
                pending = (r.title, page_of[i])
                i += 1
                continue
            segs.append(
                Segment(  # matter, not body: one block
                    "back-matter",
                    slug(r.title),
                    r.title,
                    start=page_of[i],
                    contains=[c.title for c in rows[i + 1 :] if c.part == r.title],
                )
            )
            i += 1
            while i < tail and rows[i].part == r.title:
                i += 1
            pending = None
            continue
        assert r.rel is not None
        opens, start = pending or (None, page_of[i])
        segs.append(
            Segment(
                "chapter",
                Path(r.rel).stem,
                r.title,
                start=start,
                number=r.number,
                part=r.part,
                opens_part=opens,
            )
        )
        pending = None
        i += 1

    # The unlisted pages after the body. After a matter Part they are its own
    # last pages, or follow it, and its block already runs to the end of the
    # book. After a chapter they need a block, and a page to start it on.
    trailing = rows[tail:]
    if trailing and segs[-1].kind == "back-matter":
        last = segs[-1]
        last.contains += [r.title for r in trailing if r.part != last.title]
    elif trailing:
        if back_start is None:
            raise ValueError(
                "packet: the book ends with unlisted pages that neither the PDF's "
                f"outline nor its tags place: {', '.join(r.title for r in trailing)}"
            )
        segs.append(
            Segment(
                "back-matter",
                "back-matter",
                "Back matter",
                start=back_start,
                contains=[r.title for r in trailing],
            )
        )

    for this, nxt in zip(segs, segs[1:]):
        this.end = nxt.start - 1
    segs[-1].end = total
    if segs[0].start != 1 or any(s.end < s.start for s in segs):
        raise ValueError("packet: the segment plan does not cover the book")
    return segs


def _source_sha() -> tuple[str, bool]:
    """(short HEAD SHA, working-tree-dirty) for configured book sources.
    The kit renders the working tree, so a dirty flag says the read includes
    uncommitted edits. Returns ("unknown", False) outside a git checkout."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=current_project().root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown", False
    # The packet records whether the book had uncommitted edits, so the status
    # query must name the configured source tree.
    source = current_project().path("manuscript.source")
    try:
        named = source.relative_to(current_project().root)
    except ValueError:
        named = source
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", str(named)],
        cwd=current_project().root,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return sha, bool(dirty)


def _entry(seq: int, seg: Segment) -> dict:
    """One `manifest.json` row. `number` is the number PRINTED on the page (null for
    the preface and the matter blocks), so a finding can be matched back to the book
    without counting files; `opens_part` names the divider a segment carries."""
    row = {
        "seq": seq,
        "file": f"chapters/{seq:02d}-{seg.stem}.pdf",
        "kind": seg.kind,
        "stem": seg.stem,
        "title": seg.title,
        "number": seg.number,
        "part": seg.part,
        "opens_part": seg.opens_part,
        "pages": seg.pages,
        "book_pages": [seg.start, seg.end],
    }
    if seg.contains:
        row["contains"] = seg.contains
    return row


def _cut(reader, seg: Segment, dest: Path) -> None:
    """Write one segment's pages out as their own PDF."""
    pypdf = _pypdf()
    writer = pypdf.PdfWriter()
    for n in range(seg.start - 1, seg.end):
        writer.add_page(reader.pages[n])
    with dest.open("wb") as fh:
        writer.write(fh)


def build(out_dir: Path) -> int:
    quarto = _render.find_quarto()
    if not quarto:
        print(
            "packet: quarto not found. Install it (`pip install -e '.[docs]'`).",
            file=sys.stderr,
        )
        return 127
    pypdf = _pypdf()

    # Own stage: the whole-book typst render below writes a _site, and Quarto
    # cleans other formats' output from a shared _site — on build/book/ that
    # deleted the site render's HTML out from under it.
    rc = _render.stage_book_reader(_settings().STAGE_KIT)
    if rc:
        return rc
    stage = _settings().STAGE_KIT

    rc = subprocess.call(
        [quarto, "render", "--to", "typst", "--output-dir", "_site"], cwd=stage
    )
    if rc:
        return rc
    book_pdfs = list((stage / "_site").glob("*.pdf"))
    if not book_pdfs:
        print("packet: whole-book render produced no PDF", file=sys.stderr)
        return 1
    book_pdf = max(book_pdfs, key=lambda p: p.stat().st_mtime)

    # The alignment guard: the book's own reading order against the bookmarks the
    # renderer wrote from it. Equal, or the slice is off by a chapter and nobody
    # finds out until a reader reports a book that skips one.
    rows = _bookstage.book_rows(stage)
    outline = [(kind, label) for kind, label, _ in _bookstage.book_outline(stage)]
    got = outline_pages(book_pdf)
    total = page_count(book_pdf)
    try:
        contents = contents_pages(book_pdf, got[0][1] if got else total + 1)
        starts = entry_pages(outline, got, contents)
        # Unlisted pages after the body have no bookmark; the tags place them.
        back = None
        if body_span(rows)[1] < len(rows):
            back = back_matter_start(heading_pages(book_pdf), rows, starts)
        segs = plan_segments(rows, starts, total, back)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    if out_dir.exists():
        shutil.rmtree(out_dir)  # never let a dropped segment linger
    chapters_dir = out_dir / "chapters"
    chapters_dir.mkdir(parents=True)
    shutil.copyfile(book_pdf, out_dir / "book.pdf")

    reader = pypdf.PdfReader(str(out_dir / "book.pdf"))
    entries = [_entry(n, s) for n, s in enumerate(segs, start=1)]
    for seg, entry in zip(segs, entries):
        _cut(reader, seg, out_dir / entry["file"])

    sha, dirty = _source_sha()
    manifest = {
        "source_sha": sha,
        "dirty": dirty,
        "book": {"file": "book.pdf", "pages": total},
        "chapters": entries,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    dividers = [e["opens_part"] for e in entries if e["opens_part"]]
    print(
        f"packet: {_shown(out_dir)}/ -- book.pdf ({total} pp), "
        f"{len(entries)} segment PDF(s) sliced from it, manifest.json"
        + (" [dirty tree]" if dirty else "")
    )
    print(
        f"packet: {len(dividers)} Part divider(s) ride on the chapter they open "
        f"({', '.join(dividers) or '-'})"
    )
    print("assemble a run dir out of it (`--run-dir`); never point a seat at build/.")
    return 0


def assemble(kit_dir: Path, run_dir: Path, matter: bool = False) -> int:
    """Copy a kit's segments into a neutral run dir, with a manifest of exactly those.

    The body only by default -- the preface through the last chapter. The front and
    back matter are each one block and most seats have no business reading either:
    a trade reader, a high-schooler or a persona seat spends its tokens on the book,
    not on the copyright page and the colophon. `--with-matter` ships them for the
    reads that do want them.

    No `book.pdf`: a sequential read must not be able to read ahead. And the
    kept segments are **renumbered from 1**, because a run dir that opened at
    `02-preface.pdf` would tell the seat, in the file name, that something was
    withheld from it. Nothing is lost by it: a row still carries the `stem`, the
    printed chapter `number` and the `book_pages` it was cut from."""
    manifest_path = kit_dir / "manifest.json"
    if not manifest_path.is_file():
        print(f"packet: no kit at {_shown(kit_dir)}/ (build it first)", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    kept = [e for e in manifest["chapters"] if matter or e["kind"] == "chapter"]
    if not kept:
        print("packet: nothing to assemble", file=sys.stderr)
        return 1
    entries = []
    for seq, entry in enumerate(kept, start=1):
        row = dict(entry, seq=seq, file=f"chapters/{seq:02d}-{entry['stem']}.pdf")
        entries.append((entry["file"], row))

    if run_dir.exists() and any(run_dir.iterdir()):
        print(
            f"packet: {run_dir} is not empty -- a run dir is assembled fresh, "
            "so name a new one rather than reusing a read's directory",
            file=sys.stderr,
        )
        return 1
    (run_dir / "chapters").mkdir(parents=True, exist_ok=True)
    for src_rel, row in entries:
        src = kit_dir / src_rel
        if not src.is_file():
            print(f"packet: {src_rel} is missing from the kit", file=sys.stderr)
            return 1
        shutil.copyfile(src, run_dir / row["file"])

    rows = [row for _, row in entries]
    out = {
        "source_sha": manifest["source_sha"],
        "dirty": manifest["dirty"],
        "chapters": rows,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    pages = sum(r["pages"] for r in rows)
    print(
        f"packet: {run_dir}/ -- {len(rows)} segment(s), {pages} pp, "
        "manifest.json, no book.pdf" + (" [dirty tree]" if manifest["dirty"] else "")
    )
    print(
        "airgap check: `ls -a` it -- chapters/, manifest.json, and whatever "
        "prompt the SOP writes in, and nothing else."
    )
    return 0


def _fail(message: str, code: int = 1) -> int:
    """Report a fatal packet error after everything already printed.

    Progress goes to stdout and errors to stderr, and stdout is buffered
    when it is not a terminal, so without the flush an error would print
    ahead of the progress lines that came before it and read as if the
    build had gone on past it.
    """
    sys.stdout.flush()
    print(f"packet: {message}", file=sys.stderr)
    return code


def manifest_path(out: Path) -> Path:
    """Where a prose directory's provenance lives: beside it, never in it.

    The directory itself is what a seat mounts, and it must hold its
    chapters and figures and nothing else, so its record of which book it
    came from sits one level up, where the seat is not given access.
    """
    return out.parent / f"{out.name}.manifest.json"


def part_heads(rows) -> list[str]:
    """The Part line each body chapter of a prose directory opens with.

    A print reader turns a divider page before a Part's first chapter, and from
    then on knows which Part they are in. A prose directory has no pages, so a
    seat reading it could not say where one Part ended, and its remarks about
    the book's structure described the delivery instead of the book. Each
    chapter file therefore carries what the pages would have shown: the first
    chapter of a Part opens with the divider, its Part's title on its own above
    a rule, and every later chapter of that Part opens with a running head
    naming it. A chapter outside any Part, such as a preface, carries neither.

    The titles are the printed ones from `book_rows`, so the directory cannot
    name a Part differently from the divider in the book.
    """
    heads, current = [], None
    for row in rows:
        if row.part is None:
            heads.append("")
        elif row.part != current:
            heads.append(f"# {row.part}\n\n---\n\n")
        else:
            heads.append(f"*{row.part}*\n\n")
        current = row.part
    return heads


def prose_dir(out: Path) -> int:
    """Build the directory a SELF-DRIVEN seat reads: clean chapter text, real names.

    The packet's PDFs are for a seat that is handed its pages. A self-driven seat
    instead walks a directory with its own tools, one chapter at a time, and that
    needs two things the packet cannot give it. The chapter text has to be the
    reader-facing projection rather than the source, or the marks and anchors
    leak. And the figure files have to keep the names the chapter text
    references, which frozen `file` delivery deliberately destroys: it renames
    every material to `material-NNN.ext` so a filename cannot leak information,
    and a chapter asking for `doubling.png` in a directory of `material-024.png`
    stops the read.

    Reading order and chapter numbering come from `bookstage.book_rows`, the one
    owner, so a self-driven read cannot disagree with the printed book about
    which chapter is which. Matter is excluded, as it is for an assembled run
    directory.
    """
    stage = _settings().STAGE_KIT
    rc = _render.stage_book_reader(stage)
    if rc:
        return rc
    # The body, by the rules the packet slices on (`body_span`, `body_parts`),
    # because `kind == "chapter"` alone also matches a copyright page, a back
    # cover or a colophon. What is left is what a reader reads.
    all_rows = _bookstage.book_rows(stage)
    head, tail = body_span(all_rows)
    kept = body_parts(all_rows)
    rows = [
        r
        for r in all_rows[head:tail]
        if r.kind == "chapter" and r.rel and (r.part is None or r.part in kept)
    ]
    # The back matter the directory leaves out, by title. A reader knows it is
    # there, as a print reader sees the pages after the last chapter, so the
    # plan names it to the seat, without the pages.
    back = [
        r.title
        for n, r in enumerate(all_rows)
        if r.kind == "chapter"
        and n >= head
        and (n >= tail or (r.part is not None and r.part not in kept))
    ]
    if out.exists() and any(out.iterdir()):
        return _fail(f"{_shown(out)} is not empty -- name a fresh directory")
    (out / "chapters").mkdir(parents=True, exist_ok=True)
    (out / "figures").mkdir(exist_ok=True)
    figures = 0
    parts = {}
    for i, (row, head) in enumerate(zip(rows, part_heads(rows)), start=1):
        source = stage / row.rel
        # No alt text: the seat opens the figure files themselves, and a
        # description beside each embed let one rate plates it never opened.
        text, refs = _text.reader_markdown(
            source.read_text(encoding="utf-8"), alt=False
        )
        stem = Path(row.rel).stem
        name = f"{i:02d}-{stem}.md"
        (out / "chapters" / name).write_text(head + text, encoding="utf-8")
        parts[name] = row.part
        for ref in refs:
            # The embed is relative to the chapter that carries it, which is the
            # only placement the projection itself assumes. Resolving it against
            # a named gallery directory instead would make this command work for
            # one layout and fail silently for the next.
            original = (source.parent / ref["source"]).resolve()
            target = out / "figures" / Path(ref["file"]).name
            if target.exists():
                continue
            if not original.is_file():
                return _fail(
                    f"{_shown(source)} references {ref['source']}, "
                    "which is not in the stage"
                )
            if original.suffix.lower() == ".svg":
                if not shutil.which("rsvg-convert"):
                    return _fail(
                        "rsvg-convert not found (Fedora: sudo dnf "
                        "install librsvg2-tools)",
                        127,
                    )
                subprocess.run(
                    ["rsvg-convert", "-w", "1600", "-o", str(target), str(original)],
                    check=True,
                    capture_output=True,
                )
            elif original.suffix.lower() == target.suffix.lower():
                shutil.copyfile(original, target)
            else:
                # The projection names every figure `.png`, so anything else
                # would land under a name that misdescribes its bytes.
                return _fail(
                    f"{ref['source']} is neither SVG nor PNG; convert "
                    "it in the figure set rather than here"
                )
            figures += 1
    # The provenance a mounted read cannot carry inside its own directory:
    # which book state this is, whether it had uncommitted edits, and a digest
    # of exactly these bytes, so a plan can pin what the seat will read and a
    # later reader can check it.
    from quirework.runs.storage import hashes, tree_digest

    sha, dirty = _source_sha()
    record = manifest_path(out)
    record.write_text(
        json.dumps(
            {
                "source_sha": sha,
                "dirty": dirty,
                "chapters": [p.name for p in sorted((out / "chapters").glob("*.md"))],
                # Which Part each chapter file sits in, so a read's answers
                # about the book's structure can be checked against it.
                "parts": parts,
                "back_matter": back,
                "files": hashes(out),
                "digest": tree_digest(out),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"packet: {_shown(out)}/ -- {len(rows)} chapter file(s), "
        f"{figures} figure(s), no book.pdf" + (" [dirty tree]" if dirty else "")
    )
    print(f"packet: provenance in {_shown(record)}")
    print(
        "airgap check: `ls -a` it -- chapters/, figures/, and nothing else. Mount "
        "it read-only at a path that does not name this project; a seat with a "
        "shell probes its surroundings before it reads."
    )
    return 0


def _shown(path: Path) -> Path:
    try:
        return path.relative_to(current_project().root)
    except ValueError:
        return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="quirework publish packet",
        description="Build airgap-ready panel artifacts (whole book + one PDF per "
        "reading segment, sliced from it, + manifest).",
    )
    ap.add_argument("--out", default=None, help="kit dir (default: build/panel-kit/)")
    ap.add_argument(
        "--run-dir",
        default=None,
        help="also assemble a neutral run dir here (body segments only)",
    )
    ap.add_argument(
        "--with-matter",
        action="store_true",
        help="--run-dir: include the front- and back-matter blocks",
    )
    ap.add_argument(
        "--prose-dir",
        default=None,
        help="build the self-driven seat's directory here (clean chapter text "
        "+ named figure images) and build no PDFs at all",
    )
    ap.add_argument(
        "--reuse-kit",
        action="store_true",
        help="--run-dir: assemble from the existing kit, no rebuild "
        "(how two seats read the same state)",
    )
    args = ap.parse_args(argv)
    out_dir = Path(args.out).resolve() if args.out else _settings().BUILD_KIT
    if args.prose_dir:
        return prose_dir(Path(args.prose_dir).expanduser().resolve())

    if not (args.reuse_kit and args.run_dir):
        rc = build(out_dir)
        if rc:
            return rc
    if args.run_dir:
        return assemble(
            out_dir, Path(args.run_dir).expanduser().resolve(), matter=args.with_matter
        )
    return 0
