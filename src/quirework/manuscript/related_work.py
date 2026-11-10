"""Related work: the work this one is placed against, and the marks that cite it.

The spec says what the work holds, and a binding says where. Research has the
same two halves: a register of the other work the project reads, agrees with,
argues against or builds on, and the marks that cite it. Without a register a
`@cite` mark names its work in free prose, so nothing can say which units lean
on a paper, whether a slug still names anything, or whether a quotation is
still what the work says.

`manuscript.related_work` names the register, and
`manuscript.related_work_shape` says which of two shapes it has:

- `prose`: one file, written as an essay about the related work, the way a
  paper's related-work section is. Its paragraphs are not separate records, so
  it holds no works a mark can cite by slug.
- `entries`: one work per record, either each level-2 section
  `## Title {#slug}` of one file, like a spec section, or one file per work
  through a glob, each named by its file (by its directory when the glob names
  a fixed file, as `research/*/notes.md` does), its title from its frontmatter
  or first heading. A glob is always entries, so its shape may go unsaid.

In either shape the register cites the spec as a supplement entry does, with
the slug in a code span, `` `#slug` ``, and every such citation must name a spec
section. Its own `](#anchor)` links, and a code span naming one of its own
headings, point inside it. `manuscript.related_work_public` says whether the
register is published beside the spec; when it is, each citation must name a
published section, since its reader cannot open the others.

A mark cites a work as `<!-- @cite(slug) what it supports -->`. With an entries
register declared, a slug that names no work is a problem, as a binding to a
missing section is; with a prose register, any slug is, since there are no
works to name. A `@cite` with no slug stays free prose, since a passing
reference need not be registered.

`manuscript.related_work_copies` is a path pattern with `{slug}`, such as
`research/copies/{slug}.txt`: a plain-text capture of a work as it was read. A
quotation in a mark citing that work is then checked against it, the way
`spec quotes` checks quotations of the work itself. A work without a copy is
not checked, and the report says how many quotations that left.
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

KEY = "manuscript.related_work"
SHAPES = ("prose", "entries")

# A quotation in straight or curly double quotes. Every pair is matched,
# however short, and `quotations` then keeps those long enough to be one: five
# words or more. A shorter span is a term or a title, not a citation. A length
# floor in the pattern would lose the pairing instead, since a short span that
# fails to match leaves its closing mark to be read as the next opening one.
_QUOTED = re.compile(r"[\"“]([^\"“”]{1,600})[\"”]")


@dataclass(frozen=True)
class Work:
    slug: str
    title: str
    path: Path  # the register file, or the work's own file
    body: str


def configured() -> bool:
    return current_project().get(KEY) is not None


def _pattern() -> str:
    pattern = current_project().get(KEY)
    if not pattern:
        raise ConfigurationError(
            f"configure {KEY}: one file, or a glob with one file per work"
        )
    return str(pattern)


def is_glob(pattern: str) -> bool:
    return any(c in pattern for c in "*?[")


def shape() -> str:
    """`prose` or `entries`. The project's own validation refuses a file with
    no shape and a glob called prose, so only a glob reaches the default."""
    return str(current_project().get(f"{KEY}_shape") or "entries")


def _glob(pattern: str, docs: Path | None) -> list[Path]:
    """The files a glob names, read from ``docs`` when the glob lies under the
    manuscript source, so a check run on a staged copy reads the copy."""
    project = current_project()
    if docs is not None and project.get("manuscript.source") is not None:
        source = project.path("manuscript.source")
        if source.is_relative_to(project.root):
            prefix = source.relative_to(project.root).as_posix() + "/"
            if pattern.startswith(prefix):
                return sorted(Path(docs).glob(pattern[len(prefix) :]))
    return sorted(project.root.glob(pattern))


def _entry_files(pattern: str, docs: Path | None) -> list[Path]:
    return [
        p
        for p in _glob(pattern, docs)
        if p.is_file() and not p.name.startswith("_") and p.name != "README.md"
    ]


def files(docs: Path | None = None) -> list[Path]:
    """The register's files that exist: its one file, or one per work."""
    if not configured():
        return []
    pattern = _pattern()
    if is_glob(pattern):
        return _entry_files(pattern, docs)
    path = current_project().path(KEY)
    if docs is not None:
        path = notes._in_docs(docs, path)
    return [path] if path.is_file() else []


def register() -> list[Work]:
    """Every work, in the register's order; none for a prose register."""
    project = current_project()
    pattern = _pattern()
    if shape() == "prose":
        return []
    if not is_glob(pattern):
        path = project.path(KEY)
        if not path.is_file():
            raise ConfigurationError(
                f"configured related-work register is missing: {path}"
            )
        return [
            Work(s.slug, s.title, path, s.body)
            for s in notes._sections_of(path)
            if s.slug
        ]
    by_directory = not is_glob(pattern.rpartition("/")[2])
    out = []
    for path in _entry_files(pattern, None):
        text = path.read_text(encoding="utf-8")
        slug = path.parent.name if by_directory else path.stem
        try:
            meta = frontmatter.fields(text)
        except ValueError as exc:
            raise ValueError(f"{formats.rel(path)}: {exc}") from None
        title = frontmatter.as_text(meta.get("title")) or formats.h1(text)[0] or slug
        found = frontmatter.block(text)
        out.append(Work(slug, title, path, text[found.end() :] if found else text))
    return out


def copy_path(slug: str) -> Path | None:
    """Where the captured copy of ``slug`` is, or would be; None if the
    project keeps none."""
    pattern = formats.slug_pattern(f"{KEY}_copies")
    if pattern is None:
        return None
    return current_project().root / pattern.replace("{slug}", slug)


def citations() -> list[notes.Annotation]:
    """Every `@cite` mark that names a work."""
    return [a for a in notes.scan() if a.kind == "cite" and a.args]


def footprint_patterns(project) -> tuple[str, ...]:
    """The register, as footprint globs: its file, or its glob."""
    pattern = project.get(KEY)
    if not pattern:
        return ()
    if is_glob(str(pattern)):
        return (str(pattern),)
    found = formats.relative(project, KEY)
    return (found,) if found else ()


def spec_citation_problems(docs: Path, spec: set[str]) -> list[str]:
    """Resolve the register's spec citations, the `#slug`s it writes in code
    spans as a supplement entry does.

    Each one names a spec section. Where `manuscript.related_work_public`
    publishes the register beside the spec, each also names a published
    section, since its reader cannot open the others. The register's own
    `](#anchor)` links point inside it and are not read, and a code span
    naming one of its own headings, rather than a spec section, is a pointer
    inside it too."""
    if not configured():
        return []
    project = current_project()
    public = (
        notes.public_spec_files(docs) if project.get(f"{KEY}_public", False) else None
    )
    home = {s.slug: s.path for s in notes.spec_sections(docs) if s.slug}
    problems: list[str] = []
    # Read from the tree being checked, as the spec files are, so a check of
    # a staged copy reads the copy's register and never the live one's
    # against the copy's spec.
    for path in files(docs):
        text = notes.mask_fences(path.read_text(encoding="utf-8"))
        rel = notes._rel(path)
        own = {label for _, label in notes._headings(text)}
        for slug, pos in notes._code_spec_refs(text):
            ln = text.count("\n", 0, pos) + 1
            if slug not in spec and slug in own:
                continue
            if slug not in spec:
                problems.append(
                    f"{rel}:{ln}: cites '#{slug}', which names no spec section. "
                    f"The register points at a commitment that has moved or "
                    f"gone: name the section that holds it now."
                )
            elif (held := notes._unpublished(slug, home, public)) is not None:
                problems.append(
                    f"{rel}:{ln}: cites '#{slug}', a section of "
                    f"{notes._rel(held)}, which is not published "
                    f"(manuscript.spec_public). The register is published "
                    f"beside it ({KEY}_public), so it cites only what its "
                    f"reader can open."
                )
    return problems


def problems() -> list[str]:
    """What a register and its citations cannot be: a work without a slug or
    with one taken, and a citation naming no work."""
    out: list[str] = []
    seen: dict[str, Work] = {}
    pattern = _pattern()
    prose = shape() == "prose"
    if not prose and not is_glob(pattern):
        path = current_project().path(KEY)
        for s in notes._sections_of(path) if path.is_file() else []:
            if not s.slug:
                out.append(
                    f"{formats.rel(path)}: work '{s.title}' has no {{#slug}} anchor"
                )
    for w in register():
        if w.slug in seen:
            out.append(
                f"{formats.rel(w.path)}: work slug '{w.slug}' is also "
                f"'{seen[w.slug].title}' in {formats.rel(seen[w.slug].path)}"
            )
        seen.setdefault(w.slug, w)
    for a in citations():
        at = f"{a.source or a.file}:{a.line}"
        if len(a.args) != 1:
            out.append(f"{at}: @cite takes one slug (got {len(a.args)})")
        elif prose:
            out.append(
                f"{at}: @cite({a.args[0]}) names a work, and the related-work "
                f"register is prose ({KEY}_shape), so it holds none to name. "
                f"Cite in free prose, with no slug, or keep the register as "
                f"entries."
            )
        elif a.args[0] not in seen:
            near = [s for s in seen if a.args[0] in s or s in a.args[0]][:3]
            hint = f"; did you mean {', '.join(near)}?" if near else ""
            out.append(
                f"{at}: @cite({a.args[0]}) names no work in the related-work "
                f"register{hint}"
            )
    return out


def _searchable(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().rstrip(".,;:").lower()


def quotations() -> tuple[list[str], int]:
    """(advisory lines, quotations not checked for want of a copy).

    A quoted span in a mark citing a work is read as a quotation of it, and
    checked against the work's captured copy when there is one."""
    copies: dict[str, str | None] = {}
    lines: list[str] = []
    unchecked = 0
    for a in citations():
        spans = [
            m.group(1)
            for m in _QUOTED.finditer(a.body)
            if len(m.group(1)) >= 12 and len(m.group(1).split()) >= 5
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
                    f'  {a.source or a.file}:{a.line}: @cite({slug}) "{shown}"'
                )
    return lines, unchecked


def cited_by() -> dict[str, list[str]]:
    """work slug -> the units that cite it, in reading order."""
    order = {u.slug: i for i, u in enumerate(units.registry())}
    out: dict[str, set[str]] = {}
    for a in citations():
        out.setdefault(a.args[0], set()).add(notes.unit_name(a))
    return {
        k: sorted(v, key=lambda u: (order.get(u, len(order)), u))
        for k, v in out.items()
    }


def _spec_slugs(docs: Path) -> set[str]:
    return {s.slug for s in notes.spec_sections(docs) if s.slug}


def check() -> int:
    docs = current_project().path("manuscript.source")
    found = problems() + spec_citation_problems(docs, _spec_slugs(docs))
    for line in found:
        print(f"✗ {line}", file=sys.stderr)
    works = register()
    if shape() == "entries":
        by = cited_by()
        uncited = [w for w in works if w.slug not in by]
        if uncited:
            print(
                f"\nadvisory ({len(uncited)} work(s) no {units.noun()} cites; "
                f"read and not used, or used and not cited):",
                file=sys.stderr,
            )
            for w in uncited:
                print(f"  {formats.rel(w.path)}: {w.slug}", file=sys.stderr)
    misquoted, unchecked = quotations()
    if misquoted:
        print(
            f"\nadvisory ({len(misquoted)} quotation(s) not in the captured copy "
            f"of the work they cite):",
            file=sys.stderr,
        )
        for line in misquoted:
            print(line, file=sys.stderr)
    if unchecked:
        print(
            f"\nnote: {unchecked} quotation(s) cite a work with no captured "
            f"copy, so they were not checked.",
            file=sys.stderr,
        )
    if found:
        print(f"\n✗ related-work --check: {len(found)} problem(s).", file=sys.stderr)
        return 1
    if shape() == "prose":
        print("✓ related-work --check: a prose register, every spec citation resolves.")
    else:
        print(f"✓ related-work --check: {len(works)} work(s), every citation resolves.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="quirework spec related-work",
        description="The related work the work is placed against, and which "
        "units cite each one.",
        epilog="examples:\n"
        "  quirework spec related-work                every work, with its citing units\n"
        "  quirework spec related-work --slug seth    who cites the works matching seth\n"
        "  quirework spec related-work -c the-kettle  what one unit cites\n"
        "  quirework spec related-work --check        citations resolve; quotations hold",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--check", action="store_true", help="the register's gate")
    ap.add_argument("--slug", metavar="SUBSTR", help="only works matching this")
    ap.add_argument("-c", "--unit", metavar="SLUG", help="only what this unit cites")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    if shape() == "prose":
        shown = ", ".join(formats.rel(p) for p in files()) or _pattern()
        print(
            f"the related-work register ({shown}) is prose: it holds no works "
            f"to list, and `spec related-work --check` checks its citations of "
            f"the spec."
        )
        return 0
    by = cited_by()
    shown = 0
    for w in register():
        if args.slug and args.slug.lower() not in w.slug.lower():
            continue
        citing = by.get(w.slug, [])
        if args.unit and args.unit not in citing:
            continue
        shown += 1
        who = ", ".join(citing) or f"no {units.noun()}"
        print(f"  {w.slug:34} {w.title}\n      cited by: {who}")
    if not shown:
        print("no work matches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
