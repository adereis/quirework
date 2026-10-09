"""What the work stands on: a register of sources, and the marks that cite them.

The spec says what the work holds, and a binding says where. Research has the
same two halves and had only the second: a `@source` mark named its source in
free prose, so nothing could say which units lean on a paper, whether a slug
still names anything, or whether a quotation is still what the source says.

`manuscript.sources` names the register, in one of two shapes:

- a file, where each level-2 section `## Title {#slug}` is one source and its
  body says what the source is and what it holds, like a spec section
- a glob, one file per source, each named by its file (by its directory when
  the glob names a fixed file, as `research/*/notes.md` does), its title from
  its frontmatter or first heading

A mark cites a source as `<!-- @source(slug) what it supports -->`. With a
register declared, a slug that names no source is a problem, as a binding to a
missing section is. A `@source` with no slug stays free prose, since a
passing reference need not be registered.

`manuscript.source_copies` is a path pattern with `{slug}`, such as
`research/copies/{slug}.txt`: a plain-text capture of the source as it was
read. A quotation in a mark citing that source is then checked against it, the
way `spec quotes` checks quotations of the work itself. A source without a
copy is not checked, and the report says how many quotations that left.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from quirework.core import frontmatter
from quirework.manuscript import formats, notes, units
from quirework.project import ConfigurationError, current_project

# A quotation long enough to be one: five words or more, in straight or curly
# double quotes. A shorter span is a term or a title, not a citation.
_QUOTED = re.compile(r"[\"“]([^\"“”]{12,600})[\"”]")


@dataclass(frozen=True)
class Source:
    slug: str
    title: str
    path: Path  # the register file, or the source's own file
    body: str


def configured() -> bool:
    return current_project().get("manuscript.sources") is not None


def _pattern() -> str:
    pattern = current_project().get("manuscript.sources")
    if not pattern:
        raise ConfigurationError(
            "configure manuscript.sources: a register file, or a glob with one "
            "file per source"
        )
    return str(pattern)


def _is_glob(pattern: str) -> bool:
    return any(c in pattern for c in "*?[")


def register() -> list[Source]:
    """Every source, in the register's order."""
    project = current_project()
    pattern = _pattern()
    if not _is_glob(pattern):
        path = project.path("manuscript.sources")
        if not path.is_file():
            raise ConfigurationError(f"configured sources register is missing: {path}")
        return [
            Source(s.slug, s.title, path, s.body)
            for s in notes._sections_of(path)
            if s.slug
        ]
    by_directory = not _is_glob(pattern.rpartition("/")[2])
    out = []
    for path in sorted(project.root.glob(pattern)):
        if not path.is_file() or path.name.startswith("_") or path.name == "README.md":
            continue
        text = path.read_text(encoding="utf-8")
        slug = path.parent.name if by_directory else path.stem
        try:
            meta = frontmatter.fields(text)
        except ValueError as exc:
            raise ValueError(f"{formats.rel(path)}: {exc}") from None
        title = frontmatter.as_text(meta.get("title")) or formats.h1(text)[0] or slug
        found = frontmatter.block(text)
        out.append(Source(slug, title, path, text[found.end() :] if found else text))
    return out


def copy_path(slug: str) -> Path | None:
    """Where the captured copy of ``slug`` is, or would be; None if the
    project keeps none."""
    pattern = formats.slug_pattern("manuscript.source_copies")
    if pattern is None:
        return None
    return current_project().root / pattern.replace("{slug}", slug)


def citations() -> list[notes.Annotation]:
    """Every `@source` mark that names a source."""
    return [a for a in notes.scan() if a.kind == "source" and a.args]


def footprint_patterns(project) -> tuple[str, ...]:
    """The register, as footprint globs: its file, or its glob."""
    pattern = project.get("manuscript.sources")
    if not pattern:
        return ()
    if _is_glob(str(pattern)):
        return (str(pattern),)
    found = formats.relative(project, "manuscript.sources")
    return (found,) if found else ()


def problems() -> list[str]:
    """What a register and its citations cannot be: a source without a slug
    or with one taken, and a citation naming no source."""
    out: list[str] = []
    seen: dict[str, Source] = {}
    pattern = _pattern()
    if not _is_glob(pattern):
        path = current_project().path("manuscript.sources")
        for s in notes._sections_of(path) if path.is_file() else []:
            if not s.slug:
                out.append(
                    f"{formats.rel(path)}: source '{s.title}' has no {{#slug}} anchor"
                )
    for s in register():
        if s.slug in seen:
            out.append(
                f"{formats.rel(s.path)}: source slug '{s.slug}' is also "
                f"'{seen[s.slug].title}' in {formats.rel(seen[s.slug].path)}"
            )
        seen.setdefault(s.slug, s)
    for a in citations():
        at = f"{a.source or a.file}:{a.line}"
        if len(a.args) != 1:
            out.append(f"{at}: @source takes one slug (got {len(a.args)})")
        elif a.args[0] not in seen:
            near = [s for s in seen if a.args[0] in s or s in a.args[0]][:3]
            hint = f"; did you mean {', '.join(near)}?" if near else ""
            out.append(
                f"{at}: @source({a.args[0]}) names no source in the register{hint}"
            )
    return out


def _searchable(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().rstrip(".,;:").lower()


def quotations() -> tuple[list[str], int]:
    """(advisory lines, quotations not checked for want of a copy).

    A quoted span in a mark citing a source is read as a quotation of it, and
    checked against the source's captured copy when there is one."""
    copies: dict[str, str | None] = {}
    lines: list[str] = []
    unchecked = 0
    for a in citations():
        spans = [
            m.group(1) for m in _QUOTED.finditer(a.body) if len(m.group(1).split()) >= 5
        ]
        if not spans:
            continue
        slug = a.args[0]
        if slug not in copies:
            path = copy_path(slug)
            copies[slug] = (
                _searchable(path.read_text(encoding="utf-8"))
                if path is not None and path.is_file()
                else None
            )
        text = copies[slug]
        for span in spans:
            if text is None:
                unchecked += 1
            elif _searchable(span) not in text:
                shown = span if len(span) <= 80 else span[:79] + "…"
                lines.append(
                    f'  {a.source or a.file}:{a.line}: @source({slug}) "{shown}"'
                )
    return lines, unchecked


def cited_by() -> dict[str, list[str]]:
    """source slug -> the units that cite it, in reading order."""
    order = {u.slug: i for i, u in enumerate(units.registry())}
    out: dict[str, set[str]] = {}
    for a in citations():
        out.setdefault(a.args[0], set()).add(notes.unit_name(a))
    return {
        k: sorted(v, key=lambda u: (order.get(u, len(order)), u))
        for k, v in out.items()
    }


def check() -> int:
    found = problems()
    for line in found:
        print(f"✗ {line}", file=sys.stderr)
    by = cited_by()
    sources = register()
    uncited = [s for s in sources if s.slug not in by]
    if uncited:
        print(
            f"\nadvisory ({len(uncited)} source(s) no {units.noun()} cites; read "
            f"and not used, or used and not cited):",
            file=sys.stderr,
        )
        for s in uncited:
            print(f"  {formats.rel(s.path)}: {s.slug}", file=sys.stderr)
    misquoted, unchecked = quotations()
    if misquoted:
        print(
            f"\nadvisory ({len(misquoted)} quotation(s) not in the captured copy "
            f"of the source they cite):",
            file=sys.stderr,
        )
        for line in misquoted:
            print(line, file=sys.stderr)
    if unchecked:
        print(
            f"\nnote: {unchecked} quotation(s) cite a source with no captured "
            f"copy, so they were not checked.",
            file=sys.stderr,
        )
    if found:
        print(f"\n✗ sources --check: {len(found)} problem(s).", file=sys.stderr)
        return 1
    print(f"✓ sources --check: {len(sources)} source(s), every citation resolves.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="quirework spec sources",
        description="The register of sources the work stands on, and which "
        "units cite each one.",
        epilog="examples:\n"
        "  quirework spec sources                 every source, with its citing units\n"
        "  quirework spec sources --slug seth     who cites the sources matching seth\n"
        "  quirework spec sources -c the-kettle   what one unit cites\n"
        "  quirework spec sources --check         citations resolve; quotations hold",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--check", action="store_true", help="the register's gate")
    ap.add_argument("--slug", metavar="SUBSTR", help="only sources matching this")
    ap.add_argument("-c", "--unit", metavar="SLUG", help="only what this unit cites")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    by = cited_by()
    shown = 0
    for s in register():
        if args.slug and args.slug.lower() not in s.slug.lower():
            continue
        citing = by.get(s.slug, [])
        if args.unit and args.unit not in citing:
            continue
        shown += 1
        who = ", ".join(citing) or f"no {units.noun()}"
        print(f"  {s.slug:34} {s.title}\n      cited by: {who}")
    if not shown:
        print("no source matches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
