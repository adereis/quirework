"""The manuscript's units: what they are, where they are, and in what order.

A unit is one piece of the written work: a chapter of a book, a post on a
site. Its slug is its durable identity, the name its marks sidecar carries,
the name an `@unit(slug)` reference resolves, and the key its bindings are
kept under. A title may drift from the slug and that is fine.

The selected format (`manuscript.format`, see `formats`) answers every
question about the layout. This module is the interface the spec layer asks
through, so nothing above it assumes a file extension or a directory
convention of its own.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from quirework.manuscript import formats
from quirework.manuscript.formats import SKIP_NAMES, Unit  # noqa: F401
from quirework.manuscript.quarto import MARKS_DIR  # noqa: F401
from quirework.project import current_project

# A unit reference in the metadata layer. `@x(y)` is the repo's one pointer
# shape (@bind, @see, @variant), and the sigil is what makes the guard
# decidable: a bare backticked token in an **Anchors:** run is legitimately also
# a figure name, a book title or an author, so "anything unit-shaped that
# does not resolve is an error" is only sayable with the tag attached.
#
# NOTE this is a reference FORM, never a mark kind. `<!-- @unit(x) ... -->` would
# be harvested as a new kind by notes._ANNO; `notes.check()` rejects it.
UNIT_REF = re.compile(r"@unit\(([^)]*)\)")


def manuscript_format() -> str:
    """The selected format's name."""
    return formats.current().name


def noun() -> str:
    """What this project calls one unit, for messages."""
    return current_project().get("manuscript.unit_name") or formats.current().noun


def _source(docs: Path | None) -> Path:
    if docs is not None:
        return Path(docs)
    return current_project().path("manuscript.source")


# --------------------------------------------------------------------------- #
# the interface the spec layer uses
# --------------------------------------------------------------------------- #
def registry(docs: Path | None = None) -> list[Unit]:
    """Every unit in reading order."""
    return formats.current().registry(_source(docs))


def slugs(docs: Path | None = None) -> set[str]:
    return {u.slug for u in registry(docs)}


def order_problems(docs: Path | None = None) -> list[str]:
    """What stops the units on disk from being the manuscript's units."""
    return formats.current().order_problems(_source(docs))


def prose_files(docs: Path | None = None) -> list[Path]:
    """Every prose file, whether or not a reading order lists it.

    A mark left in one of these is in the rendered text, which is the first
    thing the notes gate refuses.
    """
    return formats.current().prose_files(_source(docs))


def slug_of(path: Path | str) -> str:
    """The unit slug a prose file carries."""
    return formats.current().slug_of(Path(path))


def names(docs: Path | None = None) -> set[str]:
    """The slug of every prose file on disk."""
    return {slug_of(p) for p in prose_files(docs)}


def marks_path(slug: str, docs: Path | None = None) -> Path | None:
    """Where the marks sidecar of the unit ``slug`` lives, or would; None when
    it has no place for one."""
    return formats.current().marks_path(slug, _source(docs))


def sidecars(docs: Path | None = None) -> list[Path]:
    """Every marks sidecar on disk."""
    return formats.current().sidecars(_source(docs))


def sidecar_slug(path: Path) -> str | None:
    """The unit slug a marks sidecar belongs to, or None if it is no sidecar."""
    return formats.current().sidecar_slug(Path(path))


def prose_for_sidecar(path: Path, docs: Path | None = None) -> Path | None:
    """The prose file a sidecar's marks describe; None when it has none."""
    return formats.current().prose_for_sidecar(Path(path), _source(docs))


def attributed(path: Path, docs: Path | None = None) -> Path:
    """What a file's marks are attributed to: a sidecar's unit, else the file."""
    if sidecar_slug(path) is None:
        return Path(path)
    return prose_for_sidecar(path, docs) or Path(path)


def harvest(docs: Path | None = None) -> list[Path]:
    """The files and directories `notes.scan` reads by default."""
    return formats.current().harvest(_source(docs))


# --------------------------------------------------------------------------- #
# references to units in the metadata layers
# --------------------------------------------------------------------------- #
def _default_layers() -> tuple[str, ...]:
    """Where a project's metadata sidecars are, as globs relative to its root.

    The sidecar layout belongs to the format. Deriving the glob keeps an empty
    match from masquerading as a successful consistency check. Either way
    ``chapter_policy`` overrides it.
    """
    return formats.current().default_layers(current_project())


def layers() -> tuple[str, ...]:
    """The metadata layers, as globs relative to the project root.

    What `manuscript.chapter_policy` declares under `metadata_layers`, else
    the format's sidecar layout. An extension scanning these layers for a
    guard of its own asks here, so it reads exactly what `spec units --check`
    reads.
    """
    configured = (
        current_project().data("manuscript.chapter_policy", {}).get("metadata_layers")
    )
    # The default is derived only when nothing is declared. Computing it either
    # way would make a project that names its own layers fail for want of a
    # `manuscript.source` the answer never used.
    return tuple(configured) if configured is not None else _default_layers()


def layer_files(root: Path | None = None) -> list[Path]:
    """Every file the metadata layers match, sorted, under `root` (the
    project root by default)."""
    base = Path(root) if root is not None else current_project().root
    out: list[Path] = []
    for pattern in layers():
        out += sorted(p for p in base.glob(pattern) if p.is_file())
    return sorted(set(out))


@dataclass(frozen=True)
class Hit:
    rel: str
    line: int
    token: str
    note: str


def scan_layers(root: Path | None = None, known: set[str] | None = None) -> list[Hit]:
    """Unresolved `@unit` references in the configured metadata layers."""
    from quirework.manuscript import notes

    base = Path(root) if root is not None else current_project().root
    known = known if known is not None else slugs()
    unresolved: list[Hit] = []
    for f in layer_files(base):
        rel = str(f.relative_to(base))
        # Fenced code is masked, length-preserving, exactly as notes.scan does:
        # a documented *example* of the pointer form is not a live reference,
        # and the guide has to be able to show one. Prose that needs to name
        # the tag does it without the delimiters, which is the same convention
        # the sidecar banners already use for @-marks.
        text = notes.mask_fences(f.read_text(encoding="utf-8"))
        for n, line in enumerate(text.splitlines(), 1):
            for m in UNIT_REF.finditer(line):
                if m.group(1) not in known:
                    near = ", ".join(
                        sorted(s for s in known if m.group(1) and m.group(1) in s)[:3]
                    )
                    hint = f"; did you mean {near}?" if near else ""
                    unresolved.append(
                        Hit(rel, n, m.group(0), f"no such {noun()}{hint}")
                    )
    return unresolved


def count_advisory(docs: Path | None = None) -> list[str]:
    """The spelled-out chapter count in the prose, against the live one.

    A sidecar can ask for this in as many words: the numeric-reference
    grep does not catch counts, and an insert or a cut re-counts every site.
    Advisory, because de-numbering the sentence is an equally good fix and that
    is an author call. Only numbered units are counted, so a format without
    printed numbers has nothing to compare.

    A count of something else, such as the chapters of another book the work
    is about, is named in its unit's sidecar as `@count(eleven chapters)` and
    is not compared. Without that, a memoir of an unfinished book is told on
    every check that its own subject is out of step, and an advisory read as
    noise every time is not read the time it is right."""
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
    total = sum(1 for u in reg if u.number)
    if not total:
        return []
    out: list[str] = []
    # Book-scale numbers only. "breaks it two chapters on" is a distance, not a
    # census, and the prose says that sort of thing often; a count of the whole
    # book is necessarily in the same range as the book.
    big = {w for n, w in words.items() if n >= 10}
    pattern = re.compile(
        r"\b(" + "|".join(sorted(big)) + r")\s+chapters\b", re.IGNORECASE
    )
    base = formats.base(_source(docs))
    elsewhere = count_marks(docs)
    for u in reg:
        p = base / u.rel
        excused = elsewhere.get(u.slug, {})
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in pattern.finditer(line):
                if _phrase(m.group(0)) in excused:
                    continue
                said = m.group(1).lower()
                # A chapter counting the ones *before* it says total - 1.
                if said not in (words.get(total), words.get(total - 1)):
                    out.append(
                        f"  {u.rel}:{n}: says '{m.group(0)}'; the book "
                        f"has {total} numbered chapters"
                    )
    return out


def _phrase(text: str) -> str:
    return " ".join(text.split()).lower()


def count_marks(docs: Path | None = None) -> dict[str, dict[str, str]]:
    """unit slug -> {counted phrase: where its `@count` mark is}."""
    from quirework.manuscript import notes

    out: dict[str, dict[str, str]] = {}
    roots = harvest(docs) if docs is not None else None
    for a in notes.scan(roots):
        if a.kind == "count" and a.args:
            where = f"{a.source}:{a.line}"
            out.setdefault(slug_of(a.file), {})[_phrase(a.args[0])] = where
    return out


def stale_count_marks(docs: Path | None = None) -> list[str]:
    """`@count` marks naming a phrase their unit no longer says.

    An exemption that outlives its sentence would excuse the next count
    written with the same words, which may well be about this book.
    """
    out: list[str] = []
    base = formats.base(_source(docs))
    texts = {u.slug: base / u.rel for u in registry(docs)}
    for slug, phrases in sorted(count_marks(docs).items()):
        path = texts.get(slug)
        said = _phrase(path.read_text(encoding="utf-8")) if path else ""
        for phrase, where in sorted(phrases.items()):
            if phrase not in said:
                out.append(f"  {where}: @count({phrase}) is not in {slug}")
    return out


def _print_registry() -> None:
    from quirework.manuscript import lifecycle

    for row in formats.current().listing(registry()):
        print(row)
    # The stages before a page exists: a spec with no page yet, and an idea no
    # unit has taken up.
    planned = lifecycle.planned()
    if planned:
        print(f"planned (a spec, no {noun()} yet):")
        for slug, spec in planned:
            print(f"  {slug:34} {formats.rel(spec)}")
    ideas = lifecycle.open_ideas()
    if ideas:
        print(f"ideas (no {noun()} has taken them up):")
        for idea in ideas:
            print(f"  {idea.slug:34} {idea.rel}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="quirework spec units",
        description="The manuscript's units (chapters of a book, pages of a "
        "site) in reading order, and the guards that keep every reference to "
        "one resolvable.",
        epilog="examples:\n"
        "  quirework spec units            reading order, then planned units and ideas\n"
        "  quirework spec units --check    units on disk agree; every @unit() resolves;\n"
        "                                  what published units still owe a reader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="report units missing from the reading order and unresolved @unit()",
    )
    args = ap.parse_args(argv)

    if not args.check:
        _print_registry()
        return 0

    problems = 0
    for p in order_problems():
        print(f"✗ {p}", file=sys.stderr)
        problems += 1

    unresolved = scan_layers()
    if unresolved:
        print(
            f"\n✗ {len(unresolved)} unresolved @unit() reference(s):", file=sys.stderr
        )
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
    stale = stale_count_marks()
    if stale:
        print(
            f"\nadvisory: {len(stale)} @count mark(s) name a count their "
            f"{noun()} no longer says:",
            file=sys.stderr,
        )
        for line in stale:
            print(line, file=sys.stderr)

    from quirework.manuscript import lifecycle

    todos = lifecycle.open_todos()
    if todos:
        print(
            f"\nadvisory ({len(todos)} open @todo on published {noun()}s; a "
            f"reader cannot see what it is waiting on):",
            file=sys.stderr,
        )
        for line in todos:
            print(line, file=sys.stderr)
    stale = lifecycle.unrevised()
    if stale is None and lifecycle.published():
        print(
            f"\nnote: not a git repository, so no published {noun()} is checked "
            f"against changes to its spec.",
            file=sys.stderr,
        )
    elif stale:
        print(
            f"\nadvisory ({len(stale)} published {noun()}(s) whose spec changed "
            f"after they were last dated):",
            file=sys.stderr,
        )
        for line in stale:
            print(line, file=sys.stderr)

    if problems:
        print(f"\n✗ units --check: {problems} problem(s).", file=sys.stderr)
        return 1
    print(
        f"✓ units --check: {len(registry())} {noun()}(s) in reading order, "
        f"every @unit() resolves."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
