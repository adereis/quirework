"""A spec section's summary: the labeled paragraph that opens it.

A reader scanning a long spec, a person or an agent, wants each section's
gist before its text. A project that opens each section with one names the
label the summary carries in bold, and the feature is off until it does:

    [manuscript]
    spec_summary_label = "Summary."
    spec_summary_words = 70           # the advisory limit, 70 by default
    spec_summary_min_words = 40       # a lower bound, unset by default
    spec_summary_opening_words = 200  # the opening's limit, unset by default
    spec_digest = "spec/digest.md"    # every summary, by part

A section's summary is then its first paragraph, directly under its heading,
when that paragraph opens with the label in bold:

    ## The flash {#flash}

    **Summary.** The flash marks the rock on a clear night.

The summary is that paragraph without its label, comments dropped and
whitespace collapsed. `summaries` is the one reader of them: the spec's map
carries what it returns, the digest lists every section's summary under its
part, in reading order (`specindex`), and the primer, `spec section` and this
module's command print it, so a tool of a project's own reads it there too:

    quirework spec summary flash fog     each section's title and summary
    quirework spec summary --all         every published section's, by part
    quirework spec summary --missing     the published sections with none yet
    quirework spec summary --opening     the spec's own, from its opening
    quirework spec summary flash --json  the same, as data

The command's exit status is 1 when a section or an opening it was asked for
has no summary.

A summary is read where its section is not, in the index, the digest or a
page built from them, so it says what the section holds in words of its own.
`spec notes --check` refuses a summary that cites a section (`#slug`, in a
code span or not), since its reader has the gist and not the section to
follow the citation from, and a label with no summary after it. A summary
longer than the word limit is an advisory, and so is one shorter than the
lower bound, where the project sets one, and the count of published sections
that have none yet, since a project may add them gradually.

A spec kept as section files may name an `opening` in its manifest, the text
before the first part. The opening can carry the spec's own summary, the gist
of the whole spec, by the same label:

    # The lighthouse

    Version: 3

    **Summary.** A lighthouse warns ships off the rock, by its light...

The opening's text starts below its title, the level-1 heading the file opens
with, frontmatter and comments above it skipped, or at its top where it has no
title. Paragraphs made only of metadata lines, each a name of up to four words,
a colon and a value (`Version: 3`), are skipped too, and the next paragraph is
the summary when it opens with the label in bold; anything else there, a
comment included, means the opening carries none. `openings` reads it. The
digest prints it first, the primer's skeleton at its top and
`spec summary --opening` alone, and `spec notes --check` holds it to the
summaries' rules. Its length is advised on only where
`manuscript.spec_summary_opening_words` sets a limit, since a whole spec's gist
runs longer than a section's.

A summary is easiest to leave behind in the edit that moved its section.
`staged_unrevised` is for a project's own pre-commit hook to print, as
`trail.staged_without_breadcrumb` is: the sections a staged change rewrote
outside their summaries while leaving each summary as it was, so whoever made
the change confirms the summary still states what the section now holds.
`staged_unrevised_openings` is the same reminder for the openings.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path
from typing import NamedTuple

from quirework.core import frontmatter
from quirework.manuscript import history, notes
from quirework.project import (
    ConfigurationError,
    Project,
    current_project,
    project_scope,
)

LABEL = "manuscript.spec_summary_label"
WORDS = "manuscript.spec_summary_words"
MIN_WORDS = "manuscript.spec_summary_min_words"
OPENING_WORDS = "manuscript.spec_summary_opening_words"
DIGEST = "manuscript.spec_digest"
DEFAULT_WORDS = 70

_PARAGRAPH_END = re.compile(r"\n[ \t]*\n")
# A metadata line under an opening's title: a name of one to four words, a
# colon, and a value on the same line, such as `Version: 3`.
_FIELD = re.compile(r"[^\W_][\w-]*(?:[ \t]+[\w-]+){0,3}:[ \t]+\S")


def label(project=None) -> str | None:
    """The bold label a summary opens with, or None while summaries are off."""
    return (project or current_project()).get(LABEL)


def word_limit(project=None) -> int:
    """How many words a summary may run to before the advisory names it."""
    return (project or current_project()).get(WORDS, DEFAULT_WORDS)


def word_floor(project=None) -> int | None:
    """How many words a summary needs before the advisory stops naming it, or
    None where the project sets no lower bound."""
    return (project or current_project()).get(MIN_WORDS)


def opening_limit(project=None) -> int | None:
    """How many words an opening's summary may run to before the advisory
    names it, or None where the project sets no limit for it."""
    return (project or current_project()).get(OPENING_WORDS)


def summary(paragraph: str, label: str) -> str | None:
    """The summary ``paragraph`` carries: None unless it opens with ``label``
    in bold, and an empty string for a label with nothing after it."""
    bold = f"**{label}**"
    if not paragraph.startswith(bold):
        return None
    rest = paragraph[len(bold) :]
    if rest and not rest[0].isspace():
        return None  # `**Summary.**s` is not the label standing alone
    return " ".join(notes._COMMENT.sub(" ", rest).split())


def _opening(path: Path, text: str, label: str):
    """``(name, summary, rest)`` for each anchored section of the spec file
    ``path`` holding ``text``: its full name, its summary or None, and the
    text after the summary's paragraph, or after the heading where it has no
    summary. The paragraph is read as the file holds it, so a comment or a
    code block between the heading and a labeled paragraph means the section
    does not open with a summary."""
    masked = notes._mask_nonprose(notes.mask_fences(text))
    stops = notes.section_stops(masked)
    owner = notes.spec_owner(path)
    for m in notes._HEADING.finditer(masked):
        if len(m.group(1)) != 2:
            continue
        anchor = notes._ATTR_ID.search(m.group(3)) if m.group(3) else None
        if not anchor:
            continue
        stop = next((s for s in stops if s > m.start()), len(text))
        # From the end of the heading's own line: the heading pattern's
        # trailing space runs on over a masked comment below it.
        line_end = masked.find("\n", m.start())
        start = len(text) if line_end == -1 else line_end
        body = text[start:stop].strip()
        first, *rest = _PARAGRAPH_END.split(body, 1)
        held = summary(first, label)
        name = anchor.group(1)
        yield (
            f"{owner}/{name}" if owner else name,
            held,
            body if held is None else "".join(rest),
        )


def of_file(path: Path, label: str) -> dict[str, str | None]:
    """Each anchored section of the spec file ``path``, by its full name, and
    its summary or None, read as `_opening` reads it."""
    text = path.read_text(encoding="utf-8")
    return {name: held for name, held, _ in _opening(path, text, label)}


def summaries(docs: Path | None = None, files=None) -> dict[str, str | None]:
    """Every anchored spec section's summary, by its full name, in the order
    the register reads them, or None for a section that opens with none.

    This is the reader the spec's map, the digest, the primer,
    `spec section` and `spec summary` share, and the one a project's own tool
    calls; only `spec notes --check` reads the paragraphs itself, since it
    judges them. A label with nothing after it is no summary. While the
    project labels no summaries no section has one, so every section maps to
    None. ``files`` narrows the read to those spec files, as the map and the
    digest read the published ones; by default every spec file is read,
    published or not.
    """
    project = current_project()
    docs = Path(docs) if docs is not None else project.path("manuscript.source")
    files = notes.spec_files(docs) if files is None else files
    found = label(project)
    if found is None:
        return {s.slug: None for f in files for s in notes._sections_of(f) if s.slug}
    return {
        name: held or None
        for f in files
        for name, held, _ in _opening(f, f.read_text(encoding="utf-8"), found)
    }


# --------------------------------------------------------------------------- #
# the spec's own summary, in the opening its manifest names
# --------------------------------------------------------------------------- #
class Opening(NamedTuple):
    """A spec manifest's opening, and the summary it carries."""

    path: Path  # the opening file
    title: str  # its level-1 title, else its frontmatter title, else its name
    summary: str | None  # None where it carries none


def _is_metadata(paragraph: str) -> bool:
    """Whether every line of ``paragraph`` is a metadata line, `Name: value`."""
    lines = paragraph.splitlines()
    return bool(lines) and all(_FIELD.match(line) for line in lines)


def _read_opening(path: Path, text: str, label: str | None):
    """``(title, summary, rest)`` for the spec opening ``path`` holding ``text``.

    The opening's text starts at its first line that is neither frontmatter
    nor a comment. Where that line is a level-1 heading, it is the title and
    the text runs on below it; the text ends at the first level-2 heading, if
    the file holds one. Paragraphs made only of metadata lines (`Version: 3`)
    are skipped, and the paragraph after them is the summary when it opens
    with ``label`` in bold. It is read as the file holds it, so a comment or a
    code block before the labeled paragraph means the opening carries no
    summary, as it does in a section. ``rest`` is the text after the summary's
    paragraph, so the title and the metadata are never part of it; where the
    opening carries no summary it is the text from the first paragraph that
    is not metadata. The summary is None where there is none or ``label`` is
    None, and an empty string for a label with nothing after it."""
    masked = notes._mask_nonprose(notes.mask_fences(text))
    lead = notes._mask_nonprose(text)
    begin = len(lead) - len(lead.lstrip())
    heading = None
    m = notes._HEADING.match(masked, begin)
    if m and len(m.group(1)) == 1:
        heading = m.group(2).strip()
        # From the end of the title's own line, as a section reads from the
        # end of its heading's.
        line_end = masked.find("\n", m.start())
        begin = len(text) if line_end == -1 else line_end
    stop = next(
        (
            h.start()
            for h in notes._HEADING.finditer(masked, begin)
            if len(h.group(1)) == 2
        ),
        len(text),
    )
    rest = text[begin:stop].strip()
    held = None
    while rest:
        first, *after = _PARAGRAPH_END.split(rest, 1)
        following = after[0].strip() if after else ""
        if _is_metadata(first):
            rest = following
            continue
        held = summary(first, label) if label is not None else None
        if held is not None:
            rest = following
        break
    return _opening_title(path, text, heading), held, rest


def _opening_title(path: Path, text: str, heading: str | None) -> str:
    if heading:
        return heading
    try:
        found = frontmatter.as_text(frontmatter.fields(text).get("title"))
    except ValueError:
        found = None
    return found or path.name


def openings(docs: Path | None = None, *, published: bool = False) -> list[Opening]:
    """Each spec manifest's opening, in the order `manuscript.spec` lists the
    manifests, with the summary it carries: None where it carries none, and
    for every opening while the project labels no summaries. A label with
    nothing after it is no summary. With ``published``, only the openings of
    the published spec, the ones the digest reads. An opening its manifest
    names and its directory lacks is left out."""
    project = current_project()
    docs = Path(docs) if docs is not None else project.path("manuscript.source")
    found = label(project)
    kept = None
    if published:
        from quirework.manuscript import specindex

        kept = {p.resolve() for p in specindex._published(docs)}
    out = []
    for listed in notes.spec_manifests(docs):
        path = listed.opening
        if path is None or not path.is_file():
            continue
        if kept is not None and path.resolve() not in kept:
            continue
        title, text, _ = _read_opening(path, path.read_text(encoding="utf-8"), found)
        out.append(Opening(path, title, text or None))
    return out


def opening_rows(
    *, published: bool = False, docs: Path | None = None, root: Path | None = None
) -> list[dict]:
    """Each opening as `spec summary --opening --json` holds it: its file from
    the project root, its title and its summary, None where it has none."""
    from quirework.manuscript import specindex

    root = Path(root) if root is not None else current_project().root
    return [
        {
            "file": specindex._rel(found.path, root),
            "title": found.title,
            "summary": found.summary,
        }
        for found in openings(docs, published=published)
    ]


def wrapped(text: str, indent: int) -> str:
    """``text`` filled to 72 columns, every line indented ``indent`` spaces,
    as a summary prints under its section's title."""
    pad = " " * indent
    return textwrap.fill(
        text,
        72,
        initial_indent=pad,
        subsequent_indent=pad,
        break_long_words=False,
        break_on_hyphens=False,
    )


def findings(docs: Path, spec: set[str]) -> tuple[list[str], list]:
    """What `spec notes --check` reports about the summaries: the problems,
    and the advisory groups as `notes._findings` returns them. Nothing while
    the label is unset.

    Every spec file's summaries are held to the rules, published or not,
    since a rule about what a summary is applies wherever one is written. The
    count of sections without one is taken over the published spec, the
    sections the index and the digest read.

    An opening's summary is held to the same rules, and to its own word limit
    where the project sets one; the sections' limit and lower bound do not
    apply to it, since a whole spec's gist runs longer than a section's."""
    found = label()
    if found is None:
        return [], []
    from quirework.manuscript import specindex

    limit, least, most = word_limit(), word_floor(), opening_limit()
    problems: list[str] = []
    long: list[str] = []
    short: list[str] = []
    opening_long: list[str] = []
    for listed in notes.spec_manifests(docs):
        path = listed.opening
        if path is None or not path.is_file():
            continue  # the manifest's own check names a missing opening
        at = notes._rel(path)
        _, text, _ = _read_opening(path, path.read_text(encoding="utf-8"), found)
        if text == "":
            problems.append(
                f"{at}: the summary label **{found}** has no summary after it; "
                f"write the summary or drop the label"
            )
            continue
        if text is None:
            continue
        for m in notes._SPEC_REF.finditer(text):
            if notes.resolve(m.group(1), None, spec) in spec:
                problems.append(
                    f"{at}: the opening's summary cites '#{m.group(1)}'. A "
                    f"summary is read where its text is not, so it says what "
                    f"the spec holds without a citation; the opening's text "
                    f"can carry it."
                )
        words = specindex._words(text)
        if most is not None and words > most:
            opening_long.append(f"  {at} — {words} words")
    read = {path: of_file(path, found) for path in notes.spec_files(docs)}
    for path, held in read.items():
        owner = notes.spec_owner(path)
        for slug, text in held.items():
            at = f"{notes._rel(path)}#{slug}"
            if text == "":
                problems.append(
                    f"{at}: the summary label **{found}** has no summary after "
                    f"it; write the summary or drop the label"
                )
                continue
            if text is None:
                continue
            for m in notes._SPEC_REF.finditer(text):
                if notes.resolve(m.group(1), owner, spec) in spec:
                    problems.append(
                        f"{at}: the summary cites '#{m.group(1)}'. A summary is "
                        f"read where its section is not, so it says what the "
                        f"section holds without a citation; the section's text "
                        f"can carry it."
                    )
            words = specindex._words(text)
            if words > limit:
                long.append(f"  {at} — {words} words")
            elif least is not None and words < least:
                short.append(f"  {at} — {words} words")
    published = {
        slug: text
        for path in specindex._published(docs)
        for slug, text in (read[path] if path in read else of_file(path, found)).items()
    }
    missing = sum(1 for text in published.values() if not text)
    groups = [
        (
            f"{{n}} spec summary/summaries run past {limit} words; a summary "
            f"is the section's gist, read where its text is not",
            long,
        )
    ]
    if least is not None:
        groups.append(
            (
                f"{{n}} spec summary/summaries run under {least} words; a "
                f"summary is the section's gist, read where its text is not",
                short,
            )
        )
    if most is not None:
        groups.append(
            (
                f"{{n}} spec opening summary/summaries run past {most} words; "
                f"an opening's summary is the whole spec's gist, read where "
                f"its text is not",
                opening_long,
            )
        )
    if missing:
        groups.append(
            (
                "spec sections with no summary yet; a project may add them gradually",
                [
                    f"  {missing} of {len(published)} published section(s) open "
                    f"with no **{found}** paragraph"
                ],
            )
        )
    return problems, groups


# --------------------------------------------------------------------------- #
# a reminder for spec commits
# --------------------------------------------------------------------------- #
def _flat(text: str) -> str:
    """``text`` with its comments dropped and its whitespace collapsed."""
    return " ".join(notes._COMMENT.sub(" ", text).split())


def _holdings(specs, label):
    """``{slug: (summary, rest)}`` for every section of ``specs``, as compared.

    ``specs`` maps each spec file to its text. The rest of a section, its text
    outside the summary's paragraph, is compared with its comments dropped
    and its whitespace collapsed, as the summary already is, so a rewrap or a
    mark changes nothing either holds, and with no regard to its file or part,
    so a section moved unchanged is the same section.
    """
    out = {}
    for path, text in specs.items():
        for name, held, rest in _opening(Path(path), text, label):
            out[name] = (held, _flat(rest))
    return out


def unrevised(before_specs, after_specs) -> list[str]:
    """The spec sections a change rewrote while it left their summaries as
    they were.

    ``before_specs`` and ``after_specs`` map each spec file to its text before
    and after the change. A section counts when it had a summary before, its
    summary after is the same, and its text outside the summary's paragraph is
    not, once comments are dropped and whitespace collapsed. A section that
    gains its first summary, changes or loses its summary, is new or is
    deleted never counts. Slugs come in the order the spec after the change
    holds them, and there are none while the project labels no summaries.

    It reads no file and runs nothing. It is a reminder, never a gate: many
    edits do not move what a section holds, and only whoever made the change
    knows whether its summary still says it.
    """
    found = label()
    if found is None:
        return []
    before, after = _holdings(before_specs, found), _holdings(after_specs, found)
    out = []
    for slug, (held, rest) in after.items():
        then_held, then_rest = before.get(slug, (None, None))
        if held and then_held == held and then_rest != rest:
            out.append(slug)
    return out


def staged_unrevised(root=None) -> list[str]:
    """``unrevised`` for what is staged, compared with ``HEAD``.

    Reads every spec file as the project configures it, at ``HEAD`` and as
    staged, and runs nothing while the project labels no summaries. ``root`` is
    a project directory to read, for a caller outside one; by default, the
    current project.
    """
    if root is not None:
        with project_scope(Project.load(root)):
            return staged_unrevised()
    if label() is None:
        return []
    before, after = history.staged_specs()
    return unrevised(before, after)


def unrevised_openings(before_specs, after_specs) -> list[str]:
    """The spec openings a change rewrote while it left their summaries as
    they were, each as its file from the project root.

    ``before_specs`` and ``after_specs`` map each spec file to its text before
    and after the change, as `unrevised` takes them; the openings among them
    are the files the project's spec manifests name as `opening`, so the
    manifests are read. An opening counts when it carried a summary before,
    its summary after is the same, and its text after the summary's paragraph
    is not, once comments are dropped and whitespace collapsed. Its title and
    the metadata lines under it are not that text, so a new version number is
    no rewrite. An opening that gains its first summary, changes or loses its
    summary, or is new or deleted never counts. There are none while the
    project labels no summaries.
    """
    from quirework.manuscript import specindex

    found = label()
    if found is None:
        return []
    project = current_project()
    named = {
        m.opening.resolve() for m in notes.spec_manifests() if m.opening is not None
    }
    before = {Path(p).resolve(): text for p, text in before_specs.items()}
    out = []
    for path, text in after_specs.items():
        path = Path(path)
        if path.resolve() not in named or not before.get(path.resolve()):
            continue
        _, held, rest = _read_opening(path, text, found)
        _, then_held, then_rest = _read_opening(path, before[path.resolve()], found)
        if held and then_held == held and _flat(then_rest) != _flat(rest):
            out.append(specindex._rel(path, project.root))
    return out


def staged_unrevised_openings(root=None) -> list[str]:
    """``unrevised_openings`` for what is staged, compared with ``HEAD``, as
    `staged_unrevised` is for the sections; ``root`` likewise names a project
    directory to read."""
    if root is not None:
        with project_scope(Project.load(root)):
            return staged_unrevised_openings()
    if label() is None:
        return []
    before, after = history.staged_specs()
    return unrevised_openings(before, after)


# --------------------------------------------------------------------------- #
# quirework spec summary
# --------------------------------------------------------------------------- #
def rows(slugs=(), *, published=False, docs: Path | None = None) -> list[dict]:
    """The sections `spec summary` prints, each as ``--json`` holds it: its
    slug, title, part, file and summary, None where it has none.

    Named by ``slugs``, a section is found in any spec file, since naming one
    is work in the project; an unknown slug is refused with close matches.
    With ``published``, the rows are every published section in reading order,
    the sections the map, the digest and the advisory count read. Refused
    while the project labels no summaries.
    """
    from quirework.manuscript import specindex, specsection

    project = current_project()
    if label(project) is None:
        raise ConfigurationError(
            f"summaries are off; set {LABEL} to the label a summary opens with"
        )
    docs = Path(docs) if docs is not None else project.path("manuscript.source")
    files = specindex._published(docs) if published else notes.spec_files(docs)
    sections = [s for f in files for s in notes._sections_of(f) if s.slug]
    held = summaries(docs, files=files)
    parts = specindex._parts_of(files, docs)
    part_of = {slug: title for title, names in parts for slug in names}
    if published:
        by_slug = {s.slug: s for s in sections}
        chosen = [by_slug[n] for _, names in parts for n in names if n in by_slug]
    else:
        chosen = [specsection._find(slug.lstrip("#"), sections) for slug in slugs]
    return [
        {
            "slug": s.slug,
            "title": s.title,
            "part": part_of.get(s.slug),
            "file": specindex._rel(s.path, project.root),
            "summary": held.get(s.slug),
        }
        for s in chosen
    ]


def _entry(row: dict, indent: int) -> list[str]:
    pad = " " * indent
    lines = [f"{pad}#{row['slug']}  {row['title']}"]
    if row["summary"]:
        lines.append(wrapped(row["summary"], indent + 4))
    else:
        lines.append(
            f"{pad}    no summary: the section opens with no **{label()}** paragraph"
        )
    return lines


def _opening_entry(row: dict) -> list[str]:
    lines = [f"{row['file']}  {row['title']}"]
    if row["summary"]:
        lines.append(wrapped(row["summary"], 4))
    else:
        lines.append(f"    no summary: the opening carries no **{label()}** paragraph")
    return lines


def _openings_asked() -> list[dict]:
    """The rows `spec summary --opening` prints: every opening the spec's
    manifests name, published or not, as naming a section reaches the working
    register. Refused while summaries are off, and where no manifest names an
    opening."""
    if label() is None:
        raise ConfigurationError(
            f"summaries are off; set {LABEL} to the label a summary opens with"
        )
    found = opening_rows()
    if not found:
        raise LookupError(
            "the spec has no opening: no spec manifest names an opening file"
        )
    return found


def _by_part(found: list[dict], entry) -> list[str]:
    """``found`` under a line per part, each part after a blank line."""
    lines: list[str] = []
    part = object()
    for row in found:
        if row["part"] != part:
            part = row["part"]
            if lines:
                lines.append("")
            if part is not None:
                lines.append(part)
        lines += entry(row)
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="quirework spec summary",
        description="Print spec sections' summaries: each named section's "
        "title and summary, every published section's by part (--all), the "
        "published sections with none yet (--missing), or the spec's own "
        "summary, from the opening its manifest names (--opening). The exit "
        "status is 1 when a section or an opening asked for has no summary.",
        epilog="examples:\n"
        "  quirework spec summary flash fog     each section's title and summary\n"
        "  quirework spec summary --all         every published section's, by part\n"
        "  quirework spec summary --missing     the published sections with none\n"
        "  quirework spec summary --opening     the spec's own, from its opening\n"
        "  quirework spec summary flash --json  the same, as data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("slugs", nargs="*", metavar="SLUG", help="a section's {#slug}")
    which = ap.add_mutually_exclusive_group()
    which.add_argument(
        "--all",
        action="store_true",
        help="every published section, by part in reading order",
    )
    which.add_argument(
        "--missing",
        action="store_true",
        help="only the published sections that have no summary yet",
    )
    which.add_argument(
        "--opening",
        action="store_true",
        help="the spec's own summary, from the opening each spec manifest names",
    )
    ap.add_argument("--json", action="store_true", help="print them as JSON")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    listing = args.all or args.missing
    if bool(args.slugs) == (listing or args.opening):
        print(
            "spec summary: name the sections, or list them with --all or "
            "--missing, or ask for the spec's own with --opening, not both",
            file=sys.stderr,
        )
        return 2
    try:
        if args.opening:
            found = _openings_asked()
        else:
            found = rows(args.slugs, published=listing)
    except (LookupError, ConfigurationError) as exc:
        print(f"spec summary: {exc}", file=sys.stderr)
        return 2
    total = len(found)
    if args.missing:
        found = [row for row in found if not row["summary"]]
    status = int(any(not row["summary"] for row in found))
    if args.json:
        print(json.dumps(found, indent=2, ensure_ascii=False))
        return status
    if args.missing:
        if not found:
            print(f"every published section opens with a **{label()}** paragraph")
            return 0
        lines = _by_part(found, lambda row: [f"  #{row['slug']}  {row['title']}"])
        lines += [
            "",
            f"{len(found)} of {total} published section(s) open with no "
            f"**{label()}** paragraph",
        ]
    elif args.opening:
        lines = [line for row in found for line in _opening_entry(row)]
    elif args.all:
        lines = _by_part(found, lambda row: _entry(row, 2))
        have = sum(1 for row in found if row["summary"])
        lines += [""] if lines else []
        lines.append(f"{have} of {total} published section(s) open with a summary")
    else:
        lines = [line for row in found for line in _entry(row, 0)]
    print("\n".join(lines))
    return status
