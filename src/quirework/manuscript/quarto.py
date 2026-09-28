"""The `quarto` manuscript format: a Quarto book's chapters.

The format takes its units, and their order, from the `book.chapters` block of
the configured Quarto file. That block is read here, by one walker, and the
publisher's staging code (`publish.bookstage`) reads it through the same
functions when it rewrites a staged copy. A shape this parser does not
recognize raises rather than yielding a partial book.

A chapter's slug is its file stem, its marks sit beside it as
`_marks/<stem>.md`, and a printed number is derived from its position.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from quirework.manuscript import formats
from quirework.manuscript.formats import Format, Unit
from quirework.project import ConfigurationError, Project

BOOK_CONFIG = "_quarto.yml"
_CHAPTER_SUFFIXES = (".qmd", ".md", ".ipynb")

_YAML_ITEM = re.compile(r"^(\s*)-\s+(.*?)\s*$")


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
    `chapter` (a file), `part-file` (`part: sections/part-two.qmd`, a divider that
    is itself prose) or `part-title` (`part: "Part One: …"`, a bare heading).
    `value` is the comment line, the file path, or the part title; `children`
    are the chapter files nested under a part."""

    kind: str
    value: str
    children: list[str]


def _chapters_items(lines: list[str], ci: int, end: int) -> tuple[int, list[_Item]]:
    """Parse a located `book.chapters` block into `(item indent, items)`.

    **The single walker.** Every consumer of the book's reading order goes
    through this -- `book_chapters` (read-only), and bookstage's `flatten_book_parts` (rewrites
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


def read_items(stage: Path) -> tuple[list[str], int, int, int, list[_Item]]:
    """`_chapters_items` over a stage's `_quarto.yml`, plus what a rewrite needs."""
    lines = (stage / BOOK_CONFIG).read_text(encoding="utf-8").splitlines()
    ci, end, _ = _find_chapters_block(lines)
    item_ind, items = _chapters_items(lines, ci, end)
    return lines, ci, end, item_ind, items


def book_chapters(stage: Path) -> list[str]:
    """Ordered chapter files from a staged `_quarto.yml`, parts flattened.

    Reading order, one entry per real file: bare chapters, file-based part dividers
    (`part: sections/part-two.qmd`), and each part's nested chapters. Title-only parts
    (`part: "Part One: …"`) contribute nothing, since they name no file."""
    _, _, _, _, items = read_items(stage)
    out: list[str] = []
    for it in items:
        if it.kind in ("chapter", "part-file"):
            out.append(it.value)
        out.extend(it.children)
    return out


def part_dividers(docs: Path) -> set[str]:
    """The file-based `part:` entries. Quarto renders these as part dividers
    rather than chapters, so they carry no printed number even though their H1
    is not marked `.unnumbered`."""
    text = (docs / BOOK_CONFIG).read_text(encoding="utf-8")
    return {
        m.group(1)
        for m in re.finditer(
            r"(?m)^\s*-\s+part:\s*[\"']?([\w./-]+\.qmd)[\"']?\s*$", text
        )
    }


# A chapter's marks live in a sidecar `<dir>/_marks/<stem>.md` beside it, never
# inline in the prose. The directory's name is Quirework's convention.
MARKS_DIR = "_marks"


class Quarto(Format):
    name = "quarto"
    noun = "chapter"

    def validate(self, project: Project) -> None:
        chapters = project.path("manuscript.chapters")
        if not chapters.is_dir():
            raise ConfigurationError(
                f"configured canon directory is missing: {chapters}"
            )
        quarto = project.path("manuscript.quarto")
        if not quarto.is_file():
            raise ConfigurationError(f"configured Quarto file is missing: {quarto}")

    def registry(self, docs: Path) -> list[Unit]:
        """Every book file in reading order, from `_quarto.yml` intersected with
        disk.

        The config is the sole source of order -- a filename only has to be
        unique now, never to sort -- so this is the one function that knows
        what "the next chapter" means. The printed number is derived here and
        nowhere else.
        """
        base = formats.base(docs)
        dividers = part_dividers(docs)
        out: list[Unit] = []
        n = 0
        for relpath in book_chapters(docs):
            p = docs / relpath
            if not p.exists():
                continue  # order_problems reports it; don't crash
            title, attrs = formats.h1(p.read_text(encoding="utf-8"))
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

    def order_problems(self, docs: Path) -> list[str]:
        """`_quarto.yml` <-> disk, both directions.

        The dual of the orphan-sidecar invariant, and under slug-only the only
        remaining way to orphan a chapter: rename the file, forget the config.
        `book_chapters` raises on a shape it cannot parse but never asks
        whether the files it names exist, and nothing asked the reverse."""
        cfg = docs / BOOK_CONFIG
        if not cfg.exists():
            return []
        listed = book_chapters(docs)
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

    def prose_files(self, docs: Path) -> list[Path]:
        # A chapter on disk but missing from `_quarto.yml` is still prose,
        # which is why this reads the disk and not the registry.
        return sorted(p for p in docs.rglob("*.qmd") if p.parent.name != MARKS_DIR)

    def slug_of(self, path: Path) -> str:
        return Path(path).stem

    def prose_token(self, token: str) -> str | None:
        return Path(token).stem if token.endswith(".qmd") else None

    def marks_path(self, slug: str, docs: Path) -> Path | None:
        # A sidecar's place is beside its chapter, so a slug no chapter
        # carries has none.
        for prose in self.prose_files(docs):
            if prose.stem == slug:
                return prose.parent / MARKS_DIR / f"{slug}.md"
        return None

    def sidecars(self, docs: Path) -> list[Path]:
        return sorted(docs.rglob(f"{MARKS_DIR}/*.md"))

    def sidecar_slug(self, path: Path) -> str | None:
        path = Path(path)
        return path.stem if path.parent.name == MARKS_DIR else None

    def prose_for_sidecar(self, path: Path, docs: Path) -> Path | None:
        # The sibling path whether or not it exists: the sidecar's position
        # names it, and an orphan is reported by naming that path.
        slug = self.sidecar_slug(path)
        if slug is None:
            return None
        return Path(path).parent.parent / f"{slug}.qmd"

    def harvest(self, docs: Path) -> list[Path]:
        # The whole source directory, because the sidecars sit beside the
        # chapters.
        return [docs]

    def default_layers(self, project: Project) -> tuple[str, ...]:
        # A source outside the project root cannot be expressed relative to
        # it, so that case searches the project tree for the directory name
        # rather than silently matching nothing.
        source = project.path("manuscript.source")
        try:
            relative = source.relative_to(project.root)
        except ValueError:
            return (f"**/{MARKS_DIR}/*.md",)
        return (f"{relative}/**/{MARKS_DIR}/*.md",)

    def footprint(self, project: Project) -> tuple[str, ...]:
        # Everything under the source: the chapters, the sidecars beside
        # them, and the figures and front matter the book is made of.
        source = formats.relative(project, "manuscript.source")
        if source is None:
            return ()
        return ("**",) if source == "." else (f"{source}/**",)

    def listing(self, units: list[Unit]) -> list[str]:
        return [
            f"  {u.number:>2}  {u.slug:26} {u.title}"
            if u.number
            else f"   -  {u.slug:26} {u.title}"
            for u in units
        ]


FORMAT = Quarto()
