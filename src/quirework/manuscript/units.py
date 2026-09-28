"""The manuscript's units: what they are, where they are, and in what order.

A unit is one piece of the written work: a chapter of a book, a post on a
site. Its slug is its durable identity, the name its marks sidecar carries,
the name an `@ch(slug)` reference resolves, and the key its bindings are
kept under. A title may drift from the slug and that is fine.

Two formats find units, selected by `manuscript.format`:

- `quarto` (the default): the chapter files a Quarto book project lists in
  the `book.chapters` block of `manuscript.quarto`, in that order. A slug is
  the file's stem, the marks sidecar sits beside the chapter as
  `_marks/<stem>.md`, and a printed chapter number is derived from position.
- `pages`: the Markdown pages `manuscript.pages` matches under
  `manuscript.source`, the shape a static site generator such as Hugo reads.
  A page named `index.md` takes its slug from its directory (a page bundle),
  any other page from its stem. Order is the frontmatter `date`, and a
  page's `draft` flag is carried along. Pages carry no number. A page's marks
  live at `manuscript.marks`, a path pattern with `{slug}` in it, which
  should sit outside the rendered tree: anything inside it is the site
  generator's to read, and marks are metadata a reader must never be served.

Everything that asks which files are prose, which are sidecars, or which
unit a sidecar belongs to asks this module, so the spec layer never assumes
a file extension or a directory convention of its own.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from quirework.manuscript import frontmatter, quarto
from quirework.project import ConfigurationError, current_project

# A unit reference in the metadata layer. `@x(y)` is the repo's one pointer
# shape (@bind, @see, @variant), and the sigil is what makes the guard
# decidable: a bare backticked token in an **Anchors:** run is legitimately also
# a figure name, a book title or an author, so "anything unit-shaped that
# does not resolve is an error" is only sayable with the tag attached.
#
# NOTE this is a reference FORM, never a mark kind. `<!-- @ch(x) ... -->` would
# be harvested as a new kind by notes._ANNO; `notes.check()` rejects it.
CH_REF = re.compile(r"@ch\(([^)]*)\)")

_SLUG_OK = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")

# A quarto chapter's marks live in a sidecar `<dir>/_marks/<stem>.md` beside it,
# never inline in the prose. The directory's name is Quirework's convention.
MARKS_DIR = "_marks"

# The formats and the noun each one's units go by in messages. A project can
# name its own (`manuscript.unit_name = "post"`), since a site's pages are
# usually called something more specific.
FORMATS = {"quarto": "chapter", "pages": "page"}

# Developer-guide files carry annotation *examples* in prose, not content.
SKIP_NAMES = {"AGENTS.md", "CLAUDE.md", "GEMINI.md"}


def manuscript_format() -> str:
    fmt = current_project().get("manuscript.format", "quarto")
    if fmt not in FORMATS:
        raise ConfigurationError(
            f"manuscript.format must be one of {', '.join(FORMATS)}, not {fmt!r}"
        )
    return fmt


def noun() -> str:
    """What this project calls one unit, for messages."""
    return current_project().get("manuscript.unit_name") or FORMATS[manuscript_format()]


def _source(docs: Path | None) -> Path:
    if docs is not None:
        return Path(docs)
    return current_project().path("manuscript.source")


def _base(docs: Path) -> Path:
    """What a unit's `rel` is measured from: the project root when the source
    is inside it, else the directory holding the source. The second case is a
    tree outside any project, which is what a test hands the registry."""
    root = current_project().root
    return root if docs.resolve().is_relative_to(root) else docs.parent


def _rel(path: Path) -> str:
    root = current_project().root
    path = Path(path)
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


@dataclass(frozen=True)
class Unit:
    slug: str  # the durable identity: sidecar, anchor, @ch(), binding key
    rel: str  # path to the prose file, relative to the project root
    title: str  # the H1 or frontmatter title, which may drift from the slug
    number: int | None = None  # a printed chapter number, from position
    date: str | None = None  # a page's frontmatter date
    draft: bool = False  # a page's frontmatter draft flag


_H1 = re.compile(r"^#\s+(.*?)\s*(?:\{(.*?)\})?\s*$")


def _h1(text: str) -> tuple[str, str]:
    """(title, attribute block) of the first H1 after any frontmatter."""
    found = frontmatter.block(text)
    body = text[found.end() :] if found else text
    for line in body.splitlines():
        m = _H1.match(line)
        if m:
            return m.group(1).strip(), (m.group(2) or "")
    return "", ""


# --------------------------------------------------------------------------- #
# quarto
# --------------------------------------------------------------------------- #
def _quarto_registry(docs: Path) -> list[Unit]:
    """Every book file in reading order, from `_quarto.yml` intersected with disk.

    The config is the sole source of order -- a filename only has to be unique
    now, never to sort -- so this is the one function that knows what "the next
    chapter" means. The printed number is derived here and nowhere else.
    """
    base = _base(docs)
    dividers = quarto.part_dividers(docs)
    out: list[Unit] = []
    n = 0
    for relpath in quarto.book_chapters(docs):
        p = docs / relpath
        if not p.exists():
            continue  # order_problems reports it; don't crash
        title, attrs = _h1(p.read_text(encoding="utf-8"))
        numbered = (
            relpath not in dividers
            and "unnumbered" not in attrs
            and p.name != "index.qmd"
        )
        if numbered:
            n += 1
        out.append(
            Unit(p.stem, str(p.relative_to(base)), title, n if numbered else None)
        )
    return out


def _quarto_order_problems(docs: Path) -> list[str]:
    """`_quarto.yml` <-> disk, both directions.

    The dual of the orphan-sidecar invariant, and under slug-only the only
    remaining way to orphan a chapter: rename the file, forget the config.
    `quarto.book_chapters` raises on a shape it cannot parse but never asks
    whether the files it names exist, and nothing asked the reverse."""
    cfg = docs / quarto.BOOK_CONFIG
    if not cfg.exists():
        return []
    listed = quarto.book_chapters(docs)
    problems = [
        f"{cfg.name}: lists {r}, which does not exist"
        for r in listed
        if not (docs / r).exists()
    ]
    on_disk = {
        str(p.relative_to(docs))
        for p in docs.rglob("*.qmd")
        if p.parent.name != MARKS_DIR
    }
    for extra in sorted(on_disk - set(listed)):
        problems.append(
            f"{extra}: on disk but absent from {cfg.name}, so it is "
            f"in no reading order and renders nowhere"
        )
    return problems


# --------------------------------------------------------------------------- #
# pages
# --------------------------------------------------------------------------- #
def _pages_glob() -> str:
    pattern = current_project().get("manuscript.pages")
    if not pattern:
        raise ConfigurationError(
            "manuscript.format = 'pages' needs manuscript.pages, a glob under "
            "manuscript.source such as 'posts/*/index.md'"
        )
    return pattern


def _page_slug(path: Path) -> str:
    """A page bundle's `index.md` is named by its directory; a page file by its stem."""
    return path.parent.name if path.stem == "index" else path.stem


def _page_files(docs: Path) -> list[Path]:
    """The pages under ``docs``. A Hugo branch bundle's `_index.md` is a list
    page, and other `_`-prefixed files are partials, so neither is a unit."""
    return sorted(
        p
        for p in docs.glob(_pages_glob())
        if p.is_file() and not p.name.startswith("_") and p.name not in SKIP_NAMES
    )


def _page(path: Path, base: Path) -> Unit:
    text = path.read_text(encoding="utf-8")
    try:
        meta = frontmatter.fields(text)
    except ValueError as exc:
        raise ValueError(f"{_rel(path)}: {exc}") from None
    title = frontmatter.as_text(meta.get("title")) or _h1(text)[0]
    slug = _page_slug(path)
    return Unit(
        slug,
        str(path.relative_to(base)),
        title or slug,
        None,
        frontmatter.as_text(meta.get("date")),
        bool(meta.get("draft", False)),
    )


def _pages_registry(docs: Path) -> list[Unit]:
    """Every page, oldest first; an undated page after the dated ones."""
    base = _base(docs)
    pages = [_page(p, base) for p in _page_files(docs)]
    return sorted(pages, key=lambda u: (u.date is None, u.date or "", u.slug))


def _marks_pattern() -> str | None:
    """`manuscript.marks` for pages, or None when the project keeps no marks."""
    pattern = current_project().get("manuscript.marks")
    if pattern is not None and "{slug}" not in pattern:
        raise ConfigurationError(
            "manuscript.marks must contain {slug}, e.g. 'specs/{slug}/marks.md'"
        )
    return pattern


def _pattern_regex(pattern: str) -> re.Pattern:
    head, _, tail = pattern.partition("{slug}")
    return re.compile(re.escape(head) + r"(?P<slug>[^/]+)" + re.escape(tail) + r"\Z")


def _pages_order_problems(docs: Path) -> list[str]:
    """Two pages sharing a slug, or a slug no reference could name."""
    problems: list[str] = []
    seen: dict[str, str] = {}
    for u in _pages_registry(docs):
        if u.slug in seen:
            problems.append(
                f"{u.rel}: slug '{u.slug}' is also {seen[u.slug]}'s; a slug "
                f"is an identity and names one {noun()}"
            )
        seen.setdefault(u.slug, u.rel)
        if not _SLUG_OK.match(u.slug):
            problems.append(
                f"{u.rel}: slug '{u.slug}' is not lowercase words joined by "
                f"hyphens, so no @ch() reference can name it"
            )
    return problems


# --------------------------------------------------------------------------- #
# the interface the spec layer uses
# --------------------------------------------------------------------------- #
def registry(docs: Path | None = None) -> list[Unit]:
    """Every unit in reading order."""
    docs = _source(docs)
    if manuscript_format() == "pages":
        return _pages_registry(docs)
    return _quarto_registry(docs)


def slugs(docs: Path | None = None) -> set[str]:
    return {u.slug for u in registry(docs)}


def order_problems(docs: Path | None = None) -> list[str]:
    """What stops the units on disk from being the manuscript's units."""
    docs = _source(docs)
    if manuscript_format() == "pages":
        return _pages_order_problems(docs)
    return _quarto_order_problems(docs)


def prose_files(docs: Path | None = None) -> list[Path]:
    """Every prose file, whether or not a reading order lists it.

    A mark left in one of these is in the rendered text, which is the first
    thing the notes gate refuses. A quarto chapter on disk but missing from
    `_quarto.yml` is still prose, which is why this reads the disk and not
    the registry.
    """
    docs = _source(docs)
    if manuscript_format() == "pages":
        return _page_files(docs)
    return sorted(p for p in docs.rglob("*.qmd") if p.parent.name != MARKS_DIR)


def slug_of(path: Path | str) -> str:
    """The unit slug a prose file carries."""
    path = Path(path)
    if manuscript_format() == "pages":
        return _page_slug(path)
    return path.stem


def names(docs: Path | None = None) -> set[str]:
    """The slug of every prose file on disk."""
    return {slug_of(p) for p in prose_files(docs)}


def marks_path(slug: str, docs: Path | None = None) -> Path | None:
    """Where the marks sidecar of the unit ``slug`` lives, or would.

    None when the project keeps no marks, or for a book when no chapter
    carries the slug: a quarto sidecar's place is beside its chapter.
    """
    if manuscript_format() == "pages":
        pattern = _marks_pattern()
        if pattern is None:
            return None
        return current_project().root / pattern.replace("{slug}", slug)
    for prose in prose_files(docs):
        if prose.stem == slug:
            return prose.parent / MARKS_DIR / f"{slug}.md"
    return None


def sidecars(docs: Path | None = None) -> list[Path]:
    """Every marks sidecar on disk."""
    if manuscript_format() == "pages":
        pattern = _marks_pattern()
        if pattern is None:
            return []
        root = current_project().root
        return sorted(
            p for p in root.glob(pattern.replace("{slug}", "*")) if p.is_file()
        )
    return sorted(_source(docs).rglob(f"{MARKS_DIR}/*.md"))


def sidecar_slug(path: Path) -> str | None:
    """The unit slug a marks sidecar belongs to, or None if it is no sidecar."""
    path = Path(path)
    if manuscript_format() == "pages":
        pattern = _marks_pattern()
        if pattern is None:
            return None
        root = current_project().root
        try:
            rel = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return None
        m = _pattern_regex(pattern).match(rel)
        return m.group("slug") if m else None
    return path.stem if path.parent.name == MARKS_DIR else None


def prose_for_sidecar(path: Path, docs: Path | None = None) -> Path | None:
    """The prose file a sidecar's marks describe; None when it has none.

    For a quarto chapter this is the sibling path whether or not it exists,
    since the sidecar's position names it. For a page it is the page whose
    slug the sidecar carries.
    """
    slug = sidecar_slug(path)
    if slug is None:
        return None
    if manuscript_format() == "pages":
        for p in prose_files(docs):
            if _page_slug(p) == slug:
                return p
        return None
    return Path(path).parent.parent / f"{slug}.qmd"


def attributed(path: Path, docs: Path | None = None) -> Path:
    """What a file's marks are attributed to: a sidecar's unit, else the file."""
    if sidecar_slug(path) is None:
        return Path(path)
    return prose_for_sidecar(path, docs) or Path(path)


def harvest(docs: Path | None = None) -> list[Path]:
    """The files and directories `notes.scan` reads by default.

    A quarto book is harvested as its whole source directory, because its
    sidecars sit beside the chapters. A site's pages and their sidecars are
    listed file by file: the sidecars live outside the source, and a site's
    source holds much besides the manuscript.
    """
    docs = _source(docs)
    if manuscript_format() == "pages":
        return prose_files(docs) + sidecars(docs)
    return [docs]


# --------------------------------------------------------------------------- #
# references to units in the metadata layers
# --------------------------------------------------------------------------- #
def _default_layers() -> tuple[str, ...]:
    """Where a project's metadata sidecars are, as globs relative to its root.

    The sidecar layout belongs to the format. Deriving the glob keeps an empty
    match from masquerading as a successful consistency check. A source outside
    the project root cannot be expressed relative to it, so that case searches
    the project tree for the directory name rather than silently matching
    nothing. Either way ``chapter_policy`` overrides it.
    """
    project = current_project()
    if manuscript_format() == "pages":
        pattern = _marks_pattern()
        return (pattern.replace("{slug}", "*"),) if pattern else ()
    source = project.path("manuscript.source")
    try:
        relative = source.relative_to(project.root)
    except ValueError:
        return (f"**/{MARKS_DIR}/*.md",)
    return (f"{relative}/**/{MARKS_DIR}/*.md",)


def _layers() -> tuple[str, ...]:
    configured = (
        current_project().data("manuscript.chapter_policy", {}).get("metadata_layers")
    )
    # The default is derived only when nothing is declared. Computing it either
    # way would make a project that names its own layers fail for want of a
    # `manuscript.source` the answer never used.
    return tuple(configured) if configured is not None else _default_layers()


def _layer_files(root: Path | None = None) -> list[Path]:
    base = Path(root) if root is not None else current_project().root
    out: list[Path] = []
    for pattern in _layers():
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
    from quirework.manuscript import notes

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
        text = notes._mask_fences(f.read_text(encoding="utf-8"))
        for n, line in enumerate(text.splitlines(), 1):
            for m in CH_REF.finditer(line):
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
    printed numbers has nothing to compare."""
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
    base = _base(_source(docs))
    for u in reg:
        p = base / u.rel
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in pattern.finditer(line):
                said = m.group(1).lower()
                # A chapter counting the ones *before* it says total - 1.
                if said not in (words.get(total), words.get(total - 1)):
                    out.append(
                        f"  {u.rel}:{n}: says '{m.group(0)}'; the book "
                        f"has {total} numbered chapters"
                    )
    return out


def _print_registry() -> None:
    units = registry()
    if manuscript_format() == "pages":
        for u in units:
            state = "draft" if u.draft else "     "
            print(f"  {u.date or '-':<25} {state}  {u.slug:34} {u.title}")
        return
    for u in units:
        num = f"{u.number:>2}" if u.number else " -"
        print(f"  {num}  {u.slug:26} {u.title}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="quirework spec units",
        description="The manuscript's units (chapters of a book, pages of a "
        "site) in reading order, and the guards that keep every reference to "
        "one resolvable.",
        epilog="examples:\n"
        "  quirework spec units            reading order: number or date, slug, title\n"
        "  quirework spec units --check    units on disk agree; every @ch() resolves",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="report units missing from the reading order and unresolved @ch()",
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
        print(f"\n✗ units --check: {problems} problem(s).", file=sys.stderr)
        return 1
    print(
        f"✓ units --check: {len(registry())} {noun()}(s) in reading order, "
        f"every @ch() resolves."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
