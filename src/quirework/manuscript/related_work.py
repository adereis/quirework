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

A work of an entries register may say how it bears on a section. A link is a
heading inside the work whose text is the section's slug in a code span, and
whose first line is the link's stance:

    ## `#lamp-period`

    *Stance: support.*

    The almanac gives the period the post relies on.

The rest of the subsection, up to the next heading of its level or above, is
the link's note. A stance line may add flags after commas, `*Stance: counter,
contested.*`. `manuscript.related_work_stances` names the stances a project
uses and `manuscript.related_work_flags` the flags; with either declared, a
word outside it is refused, and without it any word is read. A work kept in a
file of its own may state its facts in TOML frontmatter: `title`, `authors` (a
list), `year`, `venue` (the citation as text) and `verification`, a table whose
`status` is unchecked, checked or verified, the last two with the `date` of
the check and what it was made `against`. A work with no `verification` is
unchecked.

`manuscript.related_work_notes` is a glob of notes, a second kind of record
beside the works: prose that relates several works to each other and to the
sections they bear on, which belongs to no one work. A note names its works in
TOML frontmatter (`title`, and `works`, a list of their slugs) and links to
sections with the same headings a work uses, with no stance: a stance says how
one work bears on a section, so it stays on that work's link. A note states
no citation, since the works it names hold their own.

`spec related-work` lists the register four ways besides its default: by
name, by stance, per section, and the works not yet verified.
`manuscript.related_work_index` names a Markdown file holding all four, which
`--write` writes and `spec notes --check` holds to the records, so the
listings a reader opens are generated and never kept by hand.

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
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
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


# A work's facts, which its own file's frontmatter may state, and the values a
# verification's status may take.
FACTS = ("title", "authors", "year", "venue", "verification")
STATUSES = ("unchecked", "checked", "verified")

# A link: a heading whose text is a section's slug in a code span and nothing
# else, below the level a work's own heading takes in a one-file register.
_LINK = re.compile(r"(?m)^(#{2,6})[ \t]+`#([A-Za-z][\w-]*)`[ \t]*$")
_ANY_HEADING = re.compile(r"(?m)^(#{1,6})[ \t]")
# Its first line: `*Stance: NAME.*`, or with flags, `*Stance: NAME, FLAG.*`.
_STANCE = re.compile(r"^\*Stance:[ \t]*(.*?)\.\*[ \t]*$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Link:
    """How a work bears on one spec section: the section, a stance and its
    flags, and the note under the link's heading."""

    section: str
    stance: str | None
    flags: tuple[str, ...]
    note: str
    line: int
    problem: str | None = None  # a stance line that does not read


@dataclass(frozen=True)
class Note:
    """Prose relating several works to each other and to the sections they
    bear on: a record of the register's that is no one work's."""

    slug: str
    title: str
    path: Path
    works: tuple[str, ...]
    links: tuple[Link, ...]
    facts: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class Work:
    slug: str
    title: str
    path: Path  # the register file, or the work's own file
    body: str
    facts: dict = field(default_factory=dict, compare=False)
    links: tuple[Link, ...] = ()


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
    """The register's files that exist: its one file, or one per work, and
    one per note."""
    if not configured():
        return []
    pattern = _pattern()
    if is_glob(pattern):
        found = _entry_files(pattern, docs)
    else:
        path = current_project().path(KEY)
        if docs is not None:
            path = notes._in_docs(docs, path)
        found = [path] if path.is_file() else []
    return found + [p for p in note_files(docs) if p not in found]


def note_files(docs: Path | None = None) -> list[Path]:
    """The notes' files, one per note; none where the project keeps none."""
    pattern = current_project().get(f"{KEY}_notes")
    return _entry_files(str(pattern), docs) if pattern else []


def _slug_of(pattern: str, path: Path) -> str:
    by_directory = not is_glob(pattern.rpartition("/")[2])
    return path.parent.name if by_directory else path.stem


def register_notes() -> list[Note]:
    """Every note, in file order."""
    pattern = current_project().get(f"{KEY}_notes")
    out = []
    for path in note_files():
        text = path.read_text(encoding="utf-8")
        try:
            meta = frontmatter.fields(text)
        except ValueError as exc:
            raise ValueError(f"{formats.rel(path)}: {exc}") from None
        slug = _slug_of(str(pattern), path)
        title = frontmatter.as_text(meta.get("title")) or formats.h1(text)[0] or slug
        found = frontmatter.block(text)
        works = meta.get("works")
        out.append(
            Note(
                slug,
                title,
                path,
                tuple(works) if isinstance(works, list) else (),
                _links(text, found.end() if found else 0, len(text), stanced=False),
                dict(meta),
            )
        )
    return out


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
        text = path.read_text(encoding="utf-8")
        spans = _work_spans(text)
        return [
            Work(s.slug, s.title, path, s.body, {}, _links(text, *spans[s.slug]))
            for s in notes._sections_of(path)
            if s.slug
        ]
    out = []
    for path in _entry_files(pattern, None):
        text = path.read_text(encoding="utf-8")
        slug = _slug_of(pattern, path)
        try:
            meta = frontmatter.fields(text)
        except ValueError as exc:
            raise ValueError(f"{formats.rel(path)}: {exc}") from None
        title = frontmatter.as_text(meta.get("title")) or formats.h1(text)[0] or slug
        found = frontmatter.block(text)
        start = found.end() if found else 0
        facts = {k: meta[k] for k in FACTS if k in meta}
        out.append(
            Work(slug, title, path, text[start:], facts, _links(text, start, len(text)))
        )
    return out


def _work_spans(text: str) -> dict[str, tuple[int, int]]:
    """slug -> (start, end) of each work's section in a one-file register: from
    its level-2 heading to the next heading of level 1 or 2."""
    masked = notes._mask_nonprose(notes.mask_fences(text))
    heads = [
        (m.start(), len(m.group(1)), (m.group(3) or "").strip())
        for m in notes._HEADING.finditer(masked)
        if len(m.group(1)) <= 2
    ]
    out: dict[str, tuple[int, int]] = {}
    for i, (pos, level, anchor) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        slug = anchor.lstrip("#").split()[0] if anchor.startswith("#") else ""
        if level == 2 and slug:
            out.setdefault(slug, (pos, end))
    return out


def _links(text: str, start: int, end: int, *, stanced: bool = True) -> tuple:
    """The links between ``start`` and ``end`` of ``text``. A work's link
    needs a stance line; a note's (``stanced`` false) takes none."""
    masked = notes._mask_nonprose(notes.mask_fences(text))
    found = []
    for m in _LINK.finditer(masked, start, end):
        level = len(m.group(1))
        stop = end
        for h in _ANY_HEADING.finditer(masked, m.end(), end):
            if len(h.group(1)) <= level:
                stop = h.start()
                break
        body = text[m.end() : stop].strip("\n")
        first, _, rest = body.lstrip("\n").partition("\n")
        line = text.count("\n", 0, m.start()) + 1
        stance, flags, problem, note = None, (), None, body.strip()
        if first.startswith("*Stance"):
            note = rest.strip()
            read = _STANCE.match(first.strip())
            parts = [p.strip() for p in read.group(1).split(",")] if read else []
            if not stanced:
                problem = (
                    "a note's link takes no stance: a stance says how one work "
                    "bears on a section, so it belongs on that work's link"
                )
            elif not parts or not all(parts):
                problem = (
                    f"the stance line reads {first.strip()!r}; write it as "
                    "`*Stance: NAME.*`, or `*Stance: NAME, FLAG.*` with flags"
                )
            else:
                stance, flags = parts[0], tuple(parts[1:])
        elif stanced:
            problem = (
                "the link has no stance line: its first line says how the work "
                "bears on the section, as `*Stance: NAME.*`"
            )
        found.append(Link(m.group(2), stance, flags, note, line, problem))
    return tuple(found)


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
    """The register, as footprint globs: its file or its glob, and its
    notes' glob."""
    pattern = project.get(KEY)
    if not pattern:
        return ()
    kept = (str(project.get(f"{KEY}_notes")),) if project.get(f"{KEY}_notes") else ()
    if is_glob(str(pattern)):
        return (str(pattern), *kept)
    found = formats.relative(project, KEY)
    return ((found,) if found else ()) + kept


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
        out += _record_problems(w)
    out += _note_problems(seen)
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


def vocabulary(key: str) -> tuple[str, ...] | None:
    """The stances (``stances``) or flags (``flags``) the project declares, or
    None where it declares none and any word is read."""
    found = current_project().get(f"{KEY}_{key}")
    return None if found is None else tuple(found)


def verification(work: Work) -> dict:
    """A work's verification as data: its status, and the date and source of
    the check where one was made. A work that states none is unchecked."""
    found = work.facts.get("verification")
    if not isinstance(found, Mapping):
        return {"status": "unchecked", "date": None, "against": None}
    when = found.get("date")
    return {
        "status": found.get("status", "unchecked"),
        "date": when.isoformat() if isinstance(when, date) else when,
        "against": found.get("against"),
    }


def _yaml_facts(path: Path) -> list[str]:
    """Facts a YAML block holds that its reader skips: YAML is read for
    top-level scalars only, so a list of authors or a verification table there
    would be dropped without a word."""
    text = path.read_text(encoding="utf-8")
    found = frontmatter.YAML.match(text)
    if not found:
        return []
    return [
        m.group(1)
        for m in re.finditer(
            r"(?m)^(authors|verification):[ \t]*(?:$|[\[{|>])", found.group(1)
        )
    ]


def _fact_problems(where: str, facts: dict) -> list[str]:
    out = []
    if "title" in facts and not isinstance(facts["title"], str):
        out.append(f"{where}: title must be text")
    authors = facts.get("authors")
    if authors is not None and (
        not isinstance(authors, list)
        or not authors
        or not all(isinstance(a, str) and a.strip() for a in authors)
    ):
        out.append(f"{where}: authors must be a list of names")
    year = facts.get("year")
    if year is not None and (
        isinstance(year, bool)
        or not (isinstance(year, int) or (isinstance(year, str) and year.isdigit()))
    ):
        out.append(f"{where}: year must be a number, such as 1911")
    if "venue" in facts and not isinstance(facts["venue"], str):
        out.append(f"{where}: venue must be text, the citation as written")
    checked = facts.get("verification")
    if checked is None:
        return out
    if not isinstance(checked, Mapping):
        return out + [
            (
                f"{where}: verification is a table with a status, and for a check "
                "its date and what it was made against"
            )
        ]
    unknown = set(checked) - {"status", "date", "against"}
    if unknown:
        out.append(
            f"{where}: verification sets {', '.join(sorted(unknown))}; it holds "
            "a status, a date and what the check was made against"
        )
    status = checked.get("status")
    if status not in STATUSES:
        out.append(
            f"{where}: verification status is {status!r}; it is one of "
            + ", ".join(STATUSES)
        )
        return out
    when, against = checked.get("date"), checked.get("against")
    if when is not None and not (
        isinstance(when, date) or (isinstance(when, str) and _ISO_DATE.match(when))
    ):
        out.append(f"{where}: verification date must be a date, such as 2031-04-02")
    if against is not None and not (isinstance(against, str) and against.strip()):
        out.append(f"{where}: verification against must name what was read")
    if status == "unchecked" and (when is not None or against is not None):
        out.append(
            f"{where}: an unchecked work records no check; give it a status of "
            "checked or verified, or drop the date and the source"
        )
    if status != "unchecked" and (when is None or against is None):
        out.append(
            f"{where}: a {status} work says when it was checked (date) and "
            "against what (against)"
        )
    return out


def _record_problems(work: Work) -> list[str]:
    """What a work's facts and links cannot be."""
    rel = formats.rel(work.path)
    out = _fact_problems(f"{rel}: work '{work.slug}'", work.facts)
    if is_glob(_pattern()):
        out += [
            f"{rel}: work '{work.slug}' states {key} in YAML frontmatter, which "
            "is read for top-level scalars only, so it would be dropped; write "
            "the work's facts in TOML frontmatter between +++ lines"
            for key in _yaml_facts(work.path)
        ]
    stances, flags = vocabulary("stances"), vocabulary("flags")
    linked: set[str] = set()
    for link in work.links:
        at = f"{rel}:{link.line}: work '{work.slug}', link to '#{link.section}'"
        if link.section in linked:
            out.append(f"{at}: the work links this section twice; keep one link")
        linked.add(link.section)
        if link.problem:
            out.append(f"{at}: {link.problem}")
            continue
        if stances is not None and link.stance not in stances:
            out.append(
                f"{at}: stance {link.stance!r} is not one the project declares "
                f"({KEY}_stances: {', '.join(stances)})"
            )
        for flag in link.flags:
            if flags is not None and flag not in flags:
                out.append(
                    f"{at}: flag {flag!r} is not one the project declares "
                    f"({KEY}_flags: {', '.join(flags) or 'none'})"
                )
    return out


def _note_problems(works: dict[str, Work]) -> list[str]:
    """What a note cannot be: a citation of its own, a work it names that the
    register lacks, a stance, a slug a work already has, a file that is also
    a work's."""
    out: list[str] = []
    work_files = {w.path.resolve() for w in works.values()}
    for note in register_notes():
        rel = formats.rel(note.path)
        at = f"{rel}: note '{note.slug}'"
        if note.path.resolve() in work_files:
            out.append(
                f"{at} is also read as a work: the notes' glob and the works' "
                "glob must not name the same file"
            )
            continue
        if note.slug in works:
            out.append(
                f"{at} has the slug of the work in {formats.rel(works[note.slug].path)}"
            )
        cited = [k for k in FACTS if k != "title" and k in note.facts]
        if cited:
            out.append(
                f"{at} states {', '.join(cited)}: a note states no citation, "
                "since the works it names hold their own"
            )
        if "title" in note.facts and not isinstance(note.facts["title"], str):
            out.append(f"{at}: title must be text")
        named = note.facts.get("works")
        if not isinstance(named, list) or not named:
            out.append(
                f"{at} names no works: give it `works`, a list of the slugs of "
                "the works it relates, in TOML frontmatter"
            )
        else:
            for slug in named:
                if not isinstance(slug, str) or slug not in works:
                    out.append(f"{at} names {slug!r}, which is no work in the register")
        sections: set[str] = set()
        for link in note.links:
            where = f"{rel}:{link.line}: note '{note.slug}', link to '#{link.section}'"
            if link.section in sections:
                out.append(f"{where}: the note links this section twice")
            sections.add(link.section)
            if link.problem:
                out.append(f"{where}: {link.problem}")
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
    if shape() == "entries" and stale_listing(docs, docs.parent):
        found.append(f"{formats.rel(index_path())} is stale; run `{INDEX_COMMAND}`")
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


INDEX_KEY = f"{KEY}_index"
INDEX_COMMAND = "quirework spec related-work --write"


def _stance_order(stances) -> list:
    """Stances in the order the project declares them, then any it does not,
    alphabetically, and a prose citation (None) last."""
    declared = vocabulary("stances") or ()
    return sorted(
        set(stances),
        key=lambda s: (
            s is None,
            declared.index(s) if s in declared else len(declared),
            s or "",
        ),
    )


def listing(docs: Path | None = None) -> dict:
    """The register's listings: every work by name, the links by stance and by
    section, the notes on each section, and the works not yet verified."""
    docs = docs if docs is not None else current_project().path("manuscript.source")
    sections = [s for s in notes.spec_sections(docs) if s.slug]
    titles = {s.slug: s.title for s in sections}
    works = sorted(register(), key=lambda w: (w.title.casefold(), w.slug))
    by_name = [
        {
            "work": w.slug,
            "title": w.title,
            "authors": list(w.facts.get("authors") or []),
            "year": w.facts.get("year"),
            "venue": w.facts.get("venue"),
            "verification": verification(w),
            "links": [
                {"section": k.section, "stance": k.stance, "flags": list(k.flags)}
                for k in w.links
                if k.problem is None
            ],
        }
        for w in works
    ]
    by_stance: dict = {}
    by_section: dict = {}
    for w in by_name:
        for k in w["links"]:
            by_stance.setdefault(k["stance"], []).append(
                {"work": w["work"], "section": k["section"]}
            )
            by_section.setdefault(k["section"], {"works": [], "notes": []})[
                "works"
            ].append({"work": w["work"], "stance": k["stance"], "flags": k["flags"]})
    for note in register_notes():
        for k in note.links:
            if k.problem is None:
                by_section.setdefault(k.section, {"works": [], "notes": []})[
                    "notes"
                ].append(note.slug)
    order = [s.slug for s in sections]
    return {
        "by_name": by_name,
        "by_stance": [
            {"stance": s, "links": by_stance[s]} for s in _stance_order(by_stance)
        ],
        "by_section": [
            {"section": s, "title": titles.get(s, s), **by_section[s]}
            for s in [*order, *sorted(set(by_section) - set(order))]
            if s in by_section
        ],
        "unverified": [
            w["work"] for w in by_name if w["verification"]["status"] == "unchecked"
        ],
    }


def _who(w: dict) -> str:
    said = ", ".join(w["authors"])
    if w["year"] is not None:
        said = f"{said}, {w['year']}" if said else str(w["year"])
    return said


def render_listing(found: dict) -> str:
    """The listing file: by name, by stance, per section and unverified."""
    lines = [
        f"<!-- Generated by `{INDEX_COMMAND}` from the related-work records.",
        "     Do not edit it by hand: the command rewrites it, and",
        "     `quirework spec notes --check` fails while it is stale. -->",
        "",
        "# Related work: the listings",
        "",
        "## By name",
        "",
    ]
    for w in found["by_name"]:
        who = _who(w)
        linked = "; ".join(
            f"`#{k['section']}` ({', '.join([k['stance'], *k['flags']])})"
            for k in w["links"]
        )
        lines.append(
            f"- **{w['title']}** (`{w['work']}`)"
            + (f", {who}" if who else "")
            + (f". {linked}." if linked else ".")
        )
    lines += ["", "## By stance"]
    for group in found["by_stance"]:
        lines += ["", f"### {group['stance']}", ""]
        by_work: dict = {}
        for k in group["links"]:
            by_work.setdefault(k["work"], []).append(k["section"])
        lines += [
            f"- `{w}`: " + ", ".join(f"`#{s}`" for s in secs)
            for w, secs in by_work.items()
        ]
    lines += ["", "## By section"]
    for s in found["by_section"]:
        lines += ["", f"### `#{s['section']}` {s['title']}", ""]
        for stance in _stance_order(k["stance"] for k in s["works"]):
            lines.append(
                f"- {stance}: "
                + ", ".join(
                    f"`{k['work']}`"
                    + (f" ({', '.join(k['flags'])})" if k["flags"] else "")
                    for k in s["works"]
                    if k["stance"] == stance
                )
            )
        if s["notes"]:
            lines.append("- notes: " + ", ".join(f"`{n}`" for n in s["notes"]))
    lines += ["", "## Not yet verified", ""]
    lines += [f"- `{w}`" for w in found["unverified"]] or ["None."]
    from quirework.text import mdwrap

    return mdwrap.reflow_columns("\n".join(lines) + "\n")


def index_path(root: Path | None = None) -> Path | None:
    """Where the listing file is kept under ``root``, or None."""
    project = current_project()
    found = project.get(INDEX_KEY)
    if not found:
        return None
    return (Path(root) if root is not None else project.root) / str(found)


def stale_listing(docs: Path | None = None, root: Path | None = None) -> bool:
    """Whether the listing file is missing or not what the records make."""
    path = index_path(root)
    if path is None:
        return False
    return not path.is_file() or path.read_text(encoding="utf-8") != render_listing(
        listing(docs)
    )


def _print_listing(found: dict, view: str) -> None:
    if view == "by_name":
        for w in found["by_name"]:
            who = _who(w)
            print(f"  {w['work']:34} {w['title']}" + (f" ({who})" if who else ""))
    elif view == "by_stance":
        for group in found["by_stance"]:
            print(f"{group['stance']}:")
            for k in group["links"]:
                print(f"  {k['work']:34} #{k['section']}")
    elif view == "by_section":
        for s in found["by_section"]:
            print(f"#{s['section']}  {s['title']}")
            for k in s["works"]:
                said = ", ".join([k["stance"], *k["flags"]])
                print(f"  {k['work']:34} {said}")
            for n in s["notes"]:
                print(f"  note {n}")
    else:
        for slug in found["unverified"]:
            print(f"  {slug}")
        if not found["unverified"]:
            print("every work records a check.")


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
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="the register's gate")
    mode.add_argument(
        "--write", action="store_true", help=f"write the listing file {INDEX_KEY} names"
    )
    for flag, said in (
        ("--by-name", "every work by name, with its authors and year"),
        ("--by-stance", "the links, grouped by stance"),
        ("--by-section", "each section's works by stance, and its notes"),
        ("--unverified", "the works no check is recorded for"),
    ):
        mode.add_argument(flag, action="store_true", help=said)
    ap.add_argument("--slug", metavar="SUBSTR", help="only works matching this")
    ap.add_argument("-c", "--unit", metavar="SLUG", help="only what this unit cites")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    views = ("by_name", "by_stance", "by_section", "unverified")
    if (args.write or any(getattr(args, v) for v in views)) and shape() == "prose":
        print(
            "spec related-work: a prose register holds no works to list",
            file=sys.stderr,
        )
        return 2
    if args.write:
        path = index_path()
        if path is None:
            print(
                f"spec related-work: configure {INDEX_KEY} to keep the listings "
                "as a file",
                file=sys.stderr,
            )
            return 2
        text = render_listing(listing())
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            print(f"{formats.rel(path)} is up to date")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"wrote {formats.rel(path)}")
        return 0
    for view in views:
        if getattr(args, view):
            _print_listing(listing(), view)
            return 0
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
