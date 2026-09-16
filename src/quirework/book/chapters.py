"""The chapter registry: the one place a chapter's identity is resolved.

A chapter has ONE durable name, its **slug**. The slug is the filename, the
sidecar filename, the `{#sec-}` anchor and the reference token, and it never
changes -- not for a reorder, not for an insert, not for a retitle. Reading
order lives only in `docs/_quarto.yml`; the printed chapter number is derived
from it and is written down nowhere.

Reader-facing prose names a chapter by its scene or its title. The metadata
layer names it by `@ch(slug)`. Nothing anywhere names it by a number.

This replaces an ordinal-prefix scheme (`07b`, `07b2`, `07c`) that had produced
five disagreeing identifiers for one chapter -- file stem, title, anchor,
`_map.md` board number, and the printed number Quarto derives from position --
plus a sixth, `ch7a`, that named a chapter with no file at all. The prefix also
stopped buying what it was for: under the author's collation `ls docs/canon`
listed `06b` before `06` and `07b2` before `07b`. The trap is that a rename
looks safe (the anchors survive, the book still builds, every gate stays green)
while the marks' prose cross-references silently repoint; that happened twice,
in c4396ab and again in 1ed2e71, and only the second was caught.

Under slug-only there is nothing positional to rename, so the mistake cannot be
made. What the guards here catch is the residue: a reference written in the
retired numeric vocabulary, and an `@ch()` that resolves to nothing.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from quirework.book import bookstage as _bookstage
from quirework.book import notes as _notes
from quirework.project import current_project

# A chapter reference in the metadata layer. `@x(y)` is the repo's one pointer
# shape (@bind, @see, @variant), and the sigil is what makes the guard
# decidable: a bare backticked token in an **Anchors:** run is legitimately also
# a figure name, a book title or an author, so "anything chapter-shaped that
# does not resolve is an error" is only sayable with the tag attached.
#
# NOTE this is a reference FORM, never a mark kind. `<!-- @ch(x) ... -->` would
# be harvested as a new kind by notes._ANNO; `notes.check()` rejects it.
CH_REF = re.compile(r"@ch\(([^)]*)\)")

_SLUG_OK = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")
_WS_RUN = re.compile(r"\s+")

# Where a chapter may be named by reference rather than by path. Canon prose is
# deliberately absent: prose names a chapter by its scene or title and never by
# a pointer, which is checks.numeric_chapter_refs' rule and stays there.


# Where "chapter 8" can only mean a chapter's IDENTITY, so the numeric ban
# reaches. Deliberately narrower than METADATA_LAYERS, because the same words
# mean something else elsewhere and a guard that cannot tell them apart is a
# guard people learn to bypass:
#   - the SOPs describe a panel's *reading position* ("the seat holds chapter
#     12 while reacting to chapter 1"), which is positional by intent and
#     correct; reader-readthrough.md even contrasts kit numbering with book
#     numbering in those words.
#   - the guide quotes the banned form as an EXAMPLE of a bad opener
#     ("Chapter 4 left a debt"), which it must be able to print. That example
#     now lives in the `docs/` card, so neither AGENTS.md is an identity layer.
#   - books-and-papers/ discusses other authors' books, which have chapters too.


# The retired numeric vocabulary, mapped to the slug that replaces it.
#
# CLOSED. This table never grows, because the scheme it belongs to is
# abolished: there is no way to mint a new `07d`. That is what lets the guard
# be an explicit list instead of an open regex, and an explicit list is what
# keeps it free of false positives -- an open pattern over this repo's own
# content hits clock times (`19:00:58`), hyphenated numerics (`10-year`) and
# ISO dates (every `2026-07-28` contains an `07`).
#
# Deliberately ABSENT, each for a reason that has bitten:
#   - `ch01`..`ch09` (zero-padded) is a PANEL-KIT FILE sequence, not a book
#     chapter: the kit numbers its own segments, so its sixth file is not
#     chapter 6 (sops/reader-readthrough.md says so in as many words). The kit
#     names them `NN-slug` now, but the older `chNN` form is all over the review
#     dirs, and every zero-padded hit in the metadata layers today is a kit
#     file.
#   - bare `06` / `07` / `08`.. would match inside every ISO date in the corpus.
#     The one genuine bare-number reference is fixed by hand, not by table.
#   - `ch13`..`ch19` appear only in historical analyses, which are never
#     rewritten, and are panel-kit sequence numbers there too.


# Longest-first, so `ch7b2` is consumed before `ch7b` and `ch7b` before `ch7`.
# A naive shortest-first pass corrupts every lettered token it touches.


# A BACKTICKED bare number (`` `08` ``, `` `10` ``) is a seventh dialect, and
# it is the reason the exercises could not simply be swept with the table: a
# bare `08` is unusable as a pattern because it sits inside every ISO date in
# the corpus, but a BACKTICKED one never does. The entries used it as their
# house form -- 147 of them -- so it is mapped rather than merely reported.
# Derived from the full stems, so it cannot drift from them.


# A RANGE (`ch1-2`, `ch1-6`) is the same vocabulary and slipped straight past
# the table: the hyphen is inside the token, so the `(?![\w-])` boundary that
# keeps `ch7` out of `ch7b` also keeps `ch1` out of `ch1-2`. Six of them
# survived the first sweep, three in this repo's own guide. Named separately
# because there is no single slug to map a range onto -- the fix is to name
# the endpoints -- so this arm reports and never rewrites.
_RETIRED_RANGE = re.compile(r"(?<![\w-])ch\d+[a-z]?\d?-\d+[a-z]?(?![\w-])")

# The `stem:line` pointer (`07c:231`, `09:46-49`, `12:103-111`): a chapter
# reference AND a line number, so it rots twice over -- once on a rename and
# again on any prose edit that shifts a line. Only the chapter half is a token
# the table can fix; the line half has to be resolved by hand into the phrase
# or the anchor it meant, which is why these are reported separately.
#
# The shape is indistinguishable from a clock time, and the corpus is full of
# them: the dated-note creation stamp (`<!-- Added: 2026-06-30 08:07 ... -->`) and
# the session timestamps quoted in @ref bodies (`2026-06-20 12:09-12:18`). What
# tells them apart is a date immediately before, and only that -- so the
# exclusion is applied over a window that may span a wrapped line, not
# per-line. `(?!\d*:)` additionally drops the middle of `19:00:58`.
_STEM_LINE = re.compile(r"(?<![\w:-])(0[0-9][a-z]?[0-9]?|1[0-2]):(\d+)(?!\d*:)")
_DATE_BEFORE = re.compile(r"\d{4}-\d{2}-\d{2}\s*\Z")


def _default_layers() -> tuple[str, ...]:
    """Where a project's metadata sidecars are, derived from its book source.

    The sidecar directory's *name* is Quirework's convention
    (``notes.MARKS_DIR``); its *position* belongs to the book. A literal
    ``docs/**`` default therefore held only for a project that happened to name
    its source directory ``docs``, and for every other project the glob matched
    nothing at all. That is the worst available failure: a scan over zero files
    reports no problems, so ``chapters --check`` passed by looking at nothing
    and the author was told the metadata was consistent.

    A source outside the project root cannot be expressed as a pattern relative
    to it, so that case searches the project tree for the directory name rather
    than silently matching nothing. Either way ``chapter_policy`` overrides it.
    """
    project = current_project()
    source = project.path("book.source", "docs")
    try:
        relative = source.relative_to(project.root)
    except ValueError:
        return (f"**/{_notes.MARKS_DIR}/*.md",)
    return (f"{relative}/**/{_notes.MARKS_DIR}/*.md",)


def _options():
    """Read policy from the selected project for this operation."""
    from types import SimpleNamespace

    default_layers = _default_layers()
    METADATA_LAYERS = tuple(
        current_project()
        .data("book.chapter_policy", {})
        .get("metadata_layers", default_layers)
    )
    IDENTITY_LAYERS = tuple(
        current_project()
        .data("book.chapter_policy", {})
        .get("identity_layers", default_layers)
    )
    RETIRED_TOKENS: dict[str, str] = (
        current_project().data("book.chapter_policy", {}).get("retired_tokens", {})
    )
    _RETIRED_RE = re.compile(
        r"(?<![\w-])("
        + "|".join(re.escape(t) for t in sorted(RETIRED_TOKENS, key=len, reverse=True))
        + r")(?![\w-])"
    )
    BACKTICKED_NUMBERS: dict[str, str] = {
        t.split("-", 1)[0]: s
        for t, s in RETIRED_TOKENS.items()
        if "-" in t and len(t) > 4
    }
    _BACKTICKED_NUM = re.compile(
        r"`(" + "|".join(sorted(BACKTICKED_NUMBERS, key=len, reverse=True)) + r")`"
    )
    if not RETIRED_TOKENS:
        _RETIRED_RE = re.compile(r"(?!x)x")
    if not BACKTICKED_NUMBERS:
        _BACKTICKED_NUM = re.compile(r"(?!x)x")
    return SimpleNamespace(
        BACKTICKED_NUMBERS=BACKTICKED_NUMBERS,
        IDENTITY_LAYERS=IDENTITY_LAYERS,
        METADATA_LAYERS=METADATA_LAYERS,
        RETIRED_TOKENS=RETIRED_TOKENS,
        _BACKTICKED_NUM=_BACKTICKED_NUM,
        _RETIRED_RE=_RETIRED_RE,
    )


@dataclass(frozen=True)
class Chapter:
    slug: str  # the durable identity: filename, sidecar, anchor, @ch()
    rel: str  # repo-relative path to the prose file
    title: str  # the H1, which may drift from the slug and that is fine
    number: int | None  # the printed chapter number, derived from position


def _h1(path: Path) -> tuple[str, str]:
    """(title, attribute block) of a file's first H1, or ("", "")."""
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^#\s+(.*?)\s*(?:\{(.*?)\})?\s*$", line)
        if m:
            return m.group(1).strip(), (m.group(2) or "")
    return "", ""


def _part_dividers(docs: Path) -> set[str]:
    """The file-based `part:` entries. Quarto renders these as part dividers
    rather than chapters, so they carry no printed number even though their H1
    is not marked `.unnumbered`."""
    text = (docs / _bookstage.BOOK_CONFIG).read_text(encoding="utf-8")
    return {
        m.group(1)
        for m in re.finditer(
            r"(?m)^\s*-\s+part:\s*[\"']?([\w./-]+\.qmd)[\"']?\s*$", text
        )
    }


def registry(docs: Path | None = None) -> list[Chapter]:
    """Every book file in reading order, from `_quarto.yml` intersected with disk.

    The config is the sole source of order -- a filename only has to be unique
    now, never to sort -- so this is the one function that knows what "the next
    chapter" means. The printed number is derived here and nowhere else.

    ``rel`` is relative to the tree *containing* ``docs``, which for the live
    book is the repo root and so reads exactly as before. Anchoring it to
    ``ROOT`` unconditionally made the ``docs`` argument unusable for any tree
    outside the repo, which left the registry's behavior testable only against
    the live book's current content -- and that is precisely how two of its
    tests came to assert a part divider that had since been dissolved.
    """
    docs = (
        Path(docs)
        if docs is not None
        else current_project().path("book.source", "docs")
    )
    root = docs.parent
    dividers = _part_dividers(docs)
    out: list[Chapter] = []
    n = 0
    for relpath in _bookstage.book_chapters(docs):
        p = docs / relpath
        if not p.exists():
            continue  # invariant 10 reports it; don't crash
        title, attrs = _h1(p)
        numbered = (
            relpath not in dividers
            and "unnumbered" not in attrs
            and p.name != "index.qmd"
        )
        if numbered:
            n += 1
        out.append(
            Chapter(p.stem, str(p.relative_to(root)), title, n if numbered else None)
        )
    return out


def slugs(docs: Path | None = None) -> set[str]:
    return {c.slug for c in registry(docs)}


def bijection_problems(docs: Path | None = None) -> list[str]:
    """`_quarto.yml` <-> disk, both directions (notes invariant 10).

    The exact dual of the orphan-sidecar invariant, and under slug-only the
    only remaining way to orphan a chapter: rename the file, forget the config.
    `bookstage.book_chapters` raises on a shape it cannot parse but never asks
    whether the files it names exist, and nothing asked the reverse."""
    docs = (
        Path(docs)
        if docs is not None
        else current_project().path("book.source", "docs")
    )
    cfg = docs / _bookstage.BOOK_CONFIG
    if not cfg.exists():
        return []
    listed = _bookstage.book_chapters(docs)
    problems = [
        f"{cfg.name}: lists {r}, which does not exist"
        for r in listed
        if not (docs / r).exists()
    ]
    on_disk = {
        str(p.relative_to(docs))
        for p in docs.rglob("*.qmd")
        if p.parent.name != "_marks"
    }
    for extra in sorted(on_disk - set(listed)):
        problems.append(
            f"{extra}: on disk but absent from {cfg.name}, so it is "
            f"in no reading order and renders nowhere"
        )
    return problems


def _layer_files(root: Path | None = None) -> list[Path]:
    base = Path(root) if root is not None else current_project().root
    out: list[Path] = []
    for pattern in _options().METADATA_LAYERS:
        out += sorted(p for p in base.glob(pattern) if p.is_file())
    return sorted(set(out))


@dataclass(frozen=True)
class Hit:
    rel: str
    line: int
    token: str
    note: str


def scan_layers(
    root: Path | None = None, known: set[str] | None = None
) -> tuple[list[Hit], list[Hit], list[Hit]]:
    """(unresolved @ch refs, retired tokens, stem:line pointers) in the layers."""
    base = Path(root) if root is not None else current_project().root
    known = known if known is not None else slugs()
    unresolved: list[Hit] = []
    retired: list[Hit] = []
    pointers: list[Hit] = []
    for f in _layer_files(base):
        rel = str(f.relative_to(base))
        # Fenced code is masked, length-preserving, exactly as notes.scan does:
        # a documented *example* of the pointer form is not a live reference,
        # and the guide has to be able to show one. Prose that needs to name
        # the tag does it without the delimiters, which is the same convention
        # the sidecar banners already use for @-marks.
        text = _notes._mask_fences(f.read_text(encoding="utf-8"))
        for n, line in enumerate(text.splitlines(), 1):
            for m in CH_REF.finditer(line):
                if m.group(1) not in known:
                    near = ", ".join(
                        sorted(s for s in known if m.group(1) and m.group(1) in s)[:3]
                    )
                    unresolved.append(
                        Hit(
                            rel,
                            n,
                            m.group(0),
                            f"no such chapter{f'; did you mean {near}?' if near else ''}",
                        )
                    )
            for m in _options()._RETIRED_RE.finditer(line):
                retired.append(
                    Hit(
                        rel,
                        n,
                        m.group(1),
                        f"-> @ch({_options().RETIRED_TOKENS[m.group(1)]})",
                    )
                )
            for m in _RETIRED_RANGE.finditer(line):
                retired.append(Hit(rel, n, m.group(0), "a range: name the endpoints"))
            for m in _options()._BACKTICKED_NUM.finditer(line):
                retired.append(
                    Hit(
                        rel,
                        n,
                        m.group(0),
                        f"-> `@ch({_options().BACKTICKED_NUMBERS[m.group(1)]})`",
                    )
                )
        # Whole-file, because the date that marks a clock time may sit on the
        # previous line: the sidecars wrap at 72 columns and a quoted session
        # timestamp lands wherever the reflow puts it.
        for m in _STEM_LINE.finditer(text):
            window = _WS_RUN.sub(" ", text[max(0, m.start() - 40) : m.start()])
            if _DATE_BEFORE.search(window):
                continue  # a clock time, not a pointer
            pointers.append(
                Hit(
                    rel,
                    text.count("\n", 0, m.start()) + 1,
                    m.group(0),
                    "stem:line pointer; resolve by hand",
                )
            )
    return unresolved, retired, pointers


def count_advisory(docs: Path | None = None) -> list[str]:
    """The spelled-out chapter count in the prose, against the live one.

    `_marks/the-boiler.md` asks for this in as many words: the numeric-reference
    grep does not catch counts, and an insert or a cut re-counts every site.
    Advisory, because de-numbering the sentence is an equally good fix and that
    is an author call."""
    docs = (
        Path(docs)
        if docs is not None
        else current_project().path("book.source", "docs")
    )
    words = {
        1: "one",
        2: "two",
        3: "three",
        4: "four",
        5: "five",
        6: "six",
        7: "seven",
        8: "eight",
        9: "nine",
        10: "ten",
        11: "eleven",
        12: "twelve",
        13: "thirteen",
        14: "fourteen",
        15: "fifteen",
        16: "sixteen",
        17: "seventeen",
        18: "eighteen",
        19: "nineteen",
        20: "twenty",
    }
    reg = registry(docs)
    total = sum(1 for c in reg if c.number)
    out: list[str] = []
    # Book-scale numbers only. "breaks it two chapters on" is a distance, not a
    # census, and the prose says that sort of thing often; a count of the whole
    # book is necessarily in the same range as the book.
    big = {w for n, w in words.items() if n >= 10}
    pattern = re.compile(
        r"\b(" + "|".join(sorted(big)) + r")\s+chapters\b", re.IGNORECASE
    )
    for c in reg:
        p = docs.parent / c.rel  # same base `rel` is measured against
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in pattern.finditer(line):
                said = m.group(1).lower()
                # A chapter counting the ones *before* it says total - 1.
                if said not in (words.get(total), words.get(total - 1)):
                    out.append(
                        f"  {c.rel}:{n}: says '{m.group(0)}'; the book "
                        f"has {total} numbered chapters"
                    )
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="quirework book chapters",
        description="The chapter registry: reading order, and the guards that "
        "keep every reference to a chapter resolvable.",
        epilog="examples:\n"
        "  quirework book chapters            reading order (printed no., slug, title)\n"
        "  quirework book chapters --check    every @ch() resolves; no retired tokens\n"
        "  quirework book chapters --legacy   the retired token -> slug alias table",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="report unresolved @ch() refs and retired numeric tokens",
    )
    ap.add_argument(
        "--legacy",
        action="store_true",
        help="print the retired-token alias table (for reading the "
        "provenance corpus and old panel reports, which are "
        "never rewritten)",
    )
    ap.add_argument(
        "--gate",
        action="store_true",
        help="with --check, exit non-zero on a problem (the ratchet; "
        "off while the sweep is in flight)",
    )
    args = ap.parse_args(argv)

    if args.legacy:
        print(
            "retired token -> slug  (the corpus and historical analyses are\n"
            "never rewritten; use this table to read an old log or report)\n"
        )
        for tok in sorted(
            _options().RETIRED_TOKENS,
            key=lambda t: (_options().RETIRED_TOKENS[t], len(t)),
        ):
            print(f"  {tok:26} {_options().RETIRED_TOKENS[tok]}")
        return 0

    if not args.check:
        for c in registry():
            num = f"{c.number:>2}" if c.number else " -"
            print(f"  {num}  {c.slug:26} {c.title}")
        return 0

    problems = 0
    for p in bijection_problems():
        print(f"✗ {p}", file=sys.stderr)
        problems += 1

    unresolved, retired, pointers = scan_layers()
    if unresolved:
        print(f"\n✗ {len(unresolved)} unresolved @ch() reference(s):", file=sys.stderr)
        for h in unresolved:
            print(f"  {h.rel}:{h.line}: {h.token} — {h.note}", file=sys.stderr)
        problems += len(unresolved)

    if retired:
        print(
            f"\n{'✗' if args.gate else 'advisory:'} {len(retired)} retired "
            f"numeric chapter token(s) in the metadata layers "
            f"({len({h.rel for h in retired})} file(s)):",
            file=sys.stderr,
        )
        by_file: dict[str, int] = {}
        for h in retired:
            by_file[h.rel] = by_file.get(h.rel, 0) + 1
        for rel, n in sorted(by_file.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>4}  {rel}", file=sys.stderr)
        if args.gate:
            problems += len(retired)

    if pointers:
        print(
            f"\nadvisory: {len(pointers)} stem:line pointer(s) — a chapter "
            f"reference and a line number, so it rots on a rename AND on any "
            f"prose edit; resolve each by hand:",
            file=sys.stderr,
        )
        for h in pointers:
            print(f"  {h.rel}:{h.line}: {h.token}", file=sys.stderr)

    counts = count_advisory()
    if counts:
        print(
            f"\nadvisory: {len(counts)} spelled-out chapter count(s) out of "
            f"step with the book:",
            file=sys.stderr,
        )
        for line in counts:
            print(line, file=sys.stderr)

    if problems:
        print(f"\n✗ chapters --check: {problems} problem(s).", file=sys.stderr)
        return 1
    print(
        f"✓ chapters --check: {len(registry())} files in reading order, "
        f"every @ch() resolves."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
