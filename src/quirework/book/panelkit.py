"""Panel kit: airgap-ready artifacts for the blind reader panels (`sops/persona-panel.md`).

From a single fresh render, build the two artifact shapes the panels read:

  - `book.pdf`              -- the whole book, figures embedded (Path B: whole-book panels)
  - `chapters/NN-slug.pdf`  -- one PDF per reading segment (Path C: sequential reveal)
  - `manifest.json`         -- per-segment title, filename, page count, and the source SHA

**The segments are SLICED out of `book.pdf`, never rendered a second time.** They
are therefore the book: its 5.5 x 8.5 trim, its face, its measure, its folios and
running heads, its figures where the typesetter put them, and its Part dividers.
Rendering each chapter standalone -- the earlier design -- produced a second,
unmaintained edition of the book (US Letter, Libertinus, 115 pages against the
book's 169) and silently dropped every Part divider, because a title-only
`part:` names no file. Both defects reached readers: one filed the missing Part
pages as a confusion in the book, another quietly assigned Part One's chapters
to Part Two. A kit that is not the book charges its own gaps to the author
(`sops/author-voice-read.md`, "What differs").

The slice is taken from the PDF's **own outline**, not from a page-range table
and not by fingerprinting page text: `bookstage.book_outline` builds the same
labels Quarto writes as bookmarks, so the two are compared entry by entry and a
mismatch is a hard error rather than a kit that is off by a chapter.

Segments are the units a reader actually consumes, which is not one per file:

  - the unlisted front matter (title page, welcome, copyright, dedication,
    contents) is ONE block, because nobody reads it a page at a time;
  - a Part divider RIDES on the chapter it opens, as it is bound in the book --
    the reader turns the divider and the chapter starts;
  - the back matter is ONE block, for the same reason as the front;
  - everything else -- the preface and each chapter -- is its own segment.

`--run-dir` assembles the neutral directory a blind seat is pointed at: the body
segments only (preface through the last chapter), no `book.pdf`, and a manifest
describing exactly what was copied. That last part is not tidiness. The run dir
used to be assembled by hand, and a shipped run's `manifest.json` was left
malformed by the hand-edit that dropped two entries from it.

This module only builds the artifacts. It never launches a persona: the agent
drives the blind runs under the SOP. The kit lives under `build/` (inside the
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
from quirework.book import bookstage as _bookstage
from quirework.book import render as _render

# the kit output tree (git-ignored)
# own render stage (see `build`)


def _settings():
    """Resolve paths from the current operation, without import-time state."""
    from types import SimpleNamespace

    BUILD_KIT = current_project().path("book.build", "build") / "panel-kit"
    STAGE_KIT = current_project().path("book.build", "build") / "panel-stage"
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
            "panel-kit: pypdf is required to slice the book "
            "(`pip install -e '.[docs]'`)."
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

    Quarto writes one per listed heading -- the preface, every Part divider, every
    chapter -- from the same reading order and the same H1s `bookstage.book_rows`
    walks, which is why the two can be checked against each other. Nested entries
    (a chapter's sections) are skipped: only the book's top level cuts a segment."""
    pypdf = _pypdf()
    reader = pypdf.PdfReader(str(pdf))
    rows: list[tuple[str, int]] = []
    for item in reader.outline:
        if isinstance(item, list):  # a nested level, not a book entry
            continue
        rows.append((str(item.title), reader.get_destination_page_number(item) + 1))
    return rows


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


def plan_segments(
    rows: list[_bookstage.Row], starts: list[int], total: int
) -> list[Segment]:
    """Group the book's reading order into the segments a reader consumes.

    `rows` is `bookstage.book_rows` (parts intact); `starts` is the first page of
    each row that the PDF outline lists, in the same order; `total` is the book's
    page count. Pure, so the grouping rules are testable without rendering a book.

    A Part divider is not a segment. It rides on the chapter it opens, because
    that is where it is bound and where the reader turns it. A Part with no
    numbered chapter in it is matter rather than body, so it collapses -- divider
    and all its pages -- into one block; that rule is what makes the back matter
    a single file without naming it in code."""
    listed = [i for i, r in enumerate(rows) if r.kind == "part" or r.listed]
    if len(listed) != len(starts):
        raise ValueError(
            f"panel-kit: the book lists {len(listed)} entries but its "
            f"PDF outline has {len(starts)}"
        )
    if not listed:
        raise ValueError("panel-kit: the book has no listed entries to slice on")
    page_of = dict(zip(listed, starts))
    head = listed[0]
    for r in rows[head + 1 :]:
        if r.kind == "chapter" and not r.listed:
            raise ValueError(
                "panel-kit: an .unlisted page outside the front "
                f"matter has no segment to sit in: {r.title}"
            )

    body_parts = {r.part for r in rows if r.kind == "chapter" and r.number is not None}
    segs: list[Segment] = []

    if head > 0:  # the unlisted pages before the first entry
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
    while i < len(rows):
        r = rows[i]
        if r.kind == "part":
            if r.title in body_parts:
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
            while i < len(rows) and rows[i].part == r.title:
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

    for this, nxt in zip(segs, segs[1:]):
        this.end = nxt.start - 1
    segs[-1].end = total
    if segs[0].start != 1 or any(s.end < s.start for s in segs):
        raise ValueError("panel-kit: the segment plan does not cover the book")
    return segs


def _source_sha() -> tuple[str, bool]:
    """(short HEAD SHA, working-tree-dirty) for the docs sources, for the manifest.
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
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "docs"],
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
            "panel-kit: quarto not found. Install it (`pip install -e '.[docs]'`).",
            file=sys.stderr,
        )
        return 127
    pypdf = _pypdf()

    # Own stage: the whole-book typst render below writes a _site, and Quarto
    # cleans other formats' output from a shared _site — on build/book/ that
    # deleted the site render's HTML out from under it.
    rc = _render.stage_book(_settings().STAGE_KIT)
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
        print("panel-kit: whole-book render produced no PDF", file=sys.stderr)
        return 1
    book_pdf = max(book_pdfs, key=lambda p: p.stat().st_mtime)

    # The alignment guard: the book's own reading order against the bookmarks the
    # renderer wrote from it. Equal, or the slice is off by a chapter and nobody
    # finds out until a reader reports a book that skips one.
    rows = _bookstage.book_rows(stage)
    want = [label for _, label, _ in _bookstage.book_outline(stage)]
    got = outline_pages(book_pdf)
    if want != [t for t, _ in got]:
        for n, (a, b) in enumerate(zip(want, [t for t, _ in got])):
            if a != b:
                print(
                    f"panel-kit: the rendered outline diverges from the book's "
                    f"reading order at entry {n + 1}: {a!r} vs {b!r}",
                    file=sys.stderr,
                )
                return 1
        print(
            f"panel-kit: the book lists {len(want)} entries, its rendered outline "
            f"{len(got)}",
            file=sys.stderr,
        )
        return 1

    total = page_count(book_pdf)
    try:
        segs = plan_segments(rows, [p for _, p in got], total)
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
        f"panel-kit: {_shown(out_dir)}/ -- book.pdf ({total} pp), "
        f"{len(entries)} segment PDF(s) sliced from it, manifest.json"
        + (" [dirty tree]" if dirty else "")
    )
    print(
        f"panel-kit: {len(dividers)} Part divider(s) ride on the chapter they open "
        f"({', '.join(dividers) or '-'})"
    )
    print(
        "assemble a run dir out of it (`--run-dir`); never point a seat at build/ "
        "(see sops/persona-panel.md)."
    )
    return 0


def assemble(kit_dir: Path, run_dir: Path, matter: bool = False) -> int:
    """Copy a kit's segments into a neutral run dir, with a manifest of exactly those.

    The body only by default -- the preface through the last chapter. The front and
    back matter are each one block and most seats have no business reading either:
    a trade reader, a high-schooler or a persona seat spends its tokens on the book,
    not on the copyright page and the colophon. `--with-matter` ships them for the
    reads that do want them (the trust-signal tiering in
    `sops/reader-readthrough.md`).

    No `book.pdf`: a sequential read must not be able to read ahead. And the
    kept segments are **renumbered from 1**, because a run dir that opened at
    `02-preface.pdf` would tell the seat, in the file name, that something was
    withheld from it. Nothing is lost by it: a row still carries the `stem`, the
    printed chapter `number` and the `book_pages` it was cut from."""
    manifest_path = kit_dir / "manifest.json"
    if not manifest_path.is_file():
        print(
            f"panel-kit: no kit at {_shown(kit_dir)}/ (build it first)", file=sys.stderr
        )
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    kept = [e for e in manifest["chapters"] if matter or e["kind"] == "chapter"]
    if not kept:
        print("panel-kit: nothing to assemble", file=sys.stderr)
        return 1
    entries = []
    for seq, entry in enumerate(kept, start=1):
        row = dict(entry, seq=seq, file=f"chapters/{seq:02d}-{entry['stem']}.pdf")
        entries.append((entry["file"], row))

    if run_dir.exists() and any(run_dir.iterdir()):
        print(
            f"panel-kit: {run_dir} is not empty -- a run dir is assembled fresh, "
            "so name a new one rather than reusing a read's directory",
            file=sys.stderr,
        )
        return 1
    (run_dir / "chapters").mkdir(parents=True, exist_ok=True)
    for src_rel, row in entries:
        src = kit_dir / src_rel
        if not src.is_file():
            print(f"panel-kit: {src_rel} is missing from the kit", file=sys.stderr)
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
        f"panel-kit: {run_dir}/ -- {len(rows)} segment(s), {pages} pp, "
        "manifest.json, no book.pdf" + (" [dirty tree]" if manifest["dirty"] else "")
    )
    print(
        "airgap check: `ls -a` it -- chapters/, manifest.json, and whatever "
        "prompt the SOP writes in, and nothing else."
    )
    return 0


def _shown(path: Path) -> Path:
    try:
        return path.relative_to(current_project().root)
    except ValueError:
        return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="quirework book packet",
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
        "--reuse-kit",
        action="store_true",
        help="--run-dir: assemble from the existing kit, no rebuild "
        "(how two seats read the same state)",
    )
    args = ap.parse_args(argv)
    out_dir = Path(args.out).resolve() if args.out else _settings().BUILD_KIT

    if not (args.reuse_kit and args.run_dir):
        rc = build(out_dir)
        if rc:
            return rc
    if args.run_dir:
        return assemble(
            out_dir, Path(args.run_dir).expanduser().resolve(), matter=args.with_matter
        )
    return 0
