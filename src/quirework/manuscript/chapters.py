"""Resolve chapter identity and reading order.

A chapter's slug is its filename stem, sidecar stem, section anchor, and
`@ch(slug)` reference. Reading order comes from the configured Quarto project.
Printed chapter numbers are derived from that order and are not identifiers.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from quirework.publish import bookstage as _bookstage
from quirework.manuscript import notes as _notes
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


def _default_layers() -> tuple[str, ...]:
    """Where a project's metadata sidecars are, derived from its book source.

    The sidecar directory's name is Quirework's convention. Its position belongs
    to the configured book source. Deriving the glob keeps an empty match from
    masquerading as a successful consistency check.

    A source outside the project root cannot be expressed as a pattern relative
    to it, so that case searches the project tree for the directory name rather
    than silently matching nothing. Either way ``chapter_policy`` overrides it.
    """
    project = current_project()
    source = project.path("manuscript.source")
    try:
        relative = source.relative_to(project.root)
    except ValueError:
        return (f"**/{_notes.MARKS_DIR}/*.md",)
    return (f"{relative}/**/{_notes.MARKS_DIR}/*.md",)


def _options():
    """Read policy from the selected project for this operation."""
    from types import SimpleNamespace

    configured = (
        current_project().data("manuscript.chapter_policy", {}).get("metadata_layers")
    )
    # The default is derived only when nothing is declared. Computing it either
    # way would make a project that names its own layers fail for want of a
    # `manuscript.source` the answer never used.
    layers = tuple(configured) if configured is not None else _default_layers()
    return SimpleNamespace(METADATA_LAYERS=layers)


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
        Path(docs) if docs is not None else current_project().path("manuscript.source")
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
        Path(docs) if docs is not None else current_project().path("manuscript.source")
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


def scan_layers(root: Path | None = None, known: set[str] | None = None) -> list[Hit]:
    """Unresolved `@ch` references in the configured metadata layers."""
    base = Path(root) if root is not None else current_project().root
    known = known if known is not None else slugs()
    unresolved: list[Hit] = []
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
    return unresolved


def count_advisory(docs: Path | None = None) -> list[str]:
    """The spelled-out chapter count in the prose, against the live one.

    A sidecar can ask for this in as many words: the numeric-reference
    grep does not catch counts, and an insert or a cut re-counts every site.
    Advisory, because de-numbering the sentence is an equally good fix and that
    is an author call."""
    docs = (
        Path(docs) if docs is not None else current_project().path("manuscript.source")
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
        prog="quirework spec units",
        description="The chapter registry: reading order, and the guards that "
        "keep every reference to a chapter resolvable.",
        epilog="examples:\n"
        "  quirework spec units            reading order (printed no., slug, title)\n"
        "  quirework spec units --check    every @ch() resolves",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="report unresolved @ch() references",
    )
    args = ap.parse_args(argv)

    if not args.check:
        for c in registry():
            num = f"{c.number:>2}" if c.number else " -"
            print(f"  {num}  {c.slug:26} {c.title}")
        return 0

    problems = 0
    for p in bijection_problems():
        print(f"✗ {p}", file=sys.stderr)
        problems += 1

    unresolved = scan_layers()
    if unresolved:
        print(f"\n✗ {len(unresolved)} unresolved @ch() reference(s):", file=sys.stderr)
        for h in unresolved:
            print(f"  {h.rel}:{h.line}: {h.token} — {h.note}", file=sys.stderr)
        problems += len(unresolved)

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
