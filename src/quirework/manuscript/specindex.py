"""The spec's index: one generated map of the published spec.

A reader, a person or an agent, arriving at a spec of many sections needs a
map before a section: what each section is called, which part holds it, how
long it is, which sections it leans on and which lean on it, and what outside
the spec applies or tells it. Every view built later, a site's pages or an
endpoint an agent asks, reads the same facts, so they are computed once into
one index and written to the files `manuscript.spec_index` names:

    [manuscript.spec_index]
    markdown = "spec/index.md"   # the map a reader opens
    json = "spec/index.json"     # the same facts, for tools

Either may be left out. `quirework spec index --write` writes them, and
`spec notes --check` fails while either is stale, so an edit to a section, a
supplement entry or the related-work register lands with its index.

The index reads only what is published. Where `manuscript.spec_public` names
the published spec files it reads those and no others, so a working register
beside the spec never appears in it, nor a citation of one. For each section
it holds the slug, title, part, file and length in words; the sections its
prose cites (`#slug`) and the sections citing it; the supplement entries that
cite it, by role, tests apart from expressions; and where the related-work
register cites it, as a count per file for a prose register, whose headings
are its own organization and may follow the work's units, or as the works of
a register of entries: each with its stance where the work links the section,
and the notes that relate works to it. It never lists units: which units express a section is the
work's business, not the spec's, and `quirework spec section --context` shows
it locally.

Nothing in it is dated or tied to a commit, so it changes only when what it
describes does.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from quirework.core import frontmatter
from quirework.manuscript import formats, notes, units
from quirework.project import ConfigurationError, current_project
from quirework.text import mdwrap

KEY = "manuscript.spec_index"
FORMATS = ("markdown", "json")
COMMAND = "quirework spec index --write"

_COMMENTS = re.compile(r"<!--.*?-->", re.DOTALL)
_WORD = re.compile(r"[^\W_][\w'’-]*")


@dataclass
class Indexed:
    slug: str
    title: str
    part: str | None
    file: str
    words: int
    cites: list[str] = field(default_factory=list)
    cited_by: list[str] = field(default_factory=list)
    tested_by: list[str] = field(default_factory=list)
    told_by: list[str] = field(default_factory=list)
    related_work: list[dict] = field(default_factory=list)


def configured(project=None) -> bool:
    return (project or current_project()).get(KEY) is not None


def targets(project=None) -> dict[str, Path]:
    """The files the index is written to, by format, from the project root."""
    project = project or current_project()
    found = project.get(KEY) or {}
    return {fmt: project.root / found[fmt] for fmt in FORMATS if found.get(fmt)}


def _published(docs: Path) -> list[Path]:
    public = notes.public_spec_files(docs)
    files = public if public is not None else notes.spec_files(docs)
    return [f for f in files if f.is_file()]


def _parts_of(files: list[Path], docs: Path) -> list[tuple[str | None, list[str]]]:
    """The parts in reading order, each with its sections' slugs. A manifest
    names its parts; a single file groups its sections under its level-1
    headings; a file with neither puts its sections in one untitled part."""
    by_file: dict[Path, str | None] = {}
    for found in notes.spec_manifests(docs):
        by_file.update(found.part_of())
    parts: list[tuple[str | None, list[str]]] = []

    def add(title, slug):
        if not parts or parts[-1][0] != title:
            parts.append((title, []))
        parts[-1][1].append(slug)

    for path in files:
        if path in by_file:
            for s in notes._sections_of(path):
                if s.slug:
                    add(by_file[path], s.slug)
            continue
        text = notes._mask_nonprose(notes.mask_fences(path.read_text()))
        title = None
        for m in notes._HEADING.finditer(text):
            level = len(m.group(1))
            if level == 1:
                title = m.group(2).strip()
            elif level == 2:
                anchor = notes._ATTR_ID.search(m.group(3)) if m.group(3) else None
                if anchor:
                    owner = notes.spec_owner(path)
                    name = anchor.group(1)
                    add(title, f"{owner}/{name}" if owner else name)
    return parts


def _words(body: str) -> int:
    return len(_WORD.findall(_COMMENTS.sub(" ", body)))


def _unique(items):
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _entry_title(path: Path, text: str) -> str:
    try:
        meta = frontmatter.fields(text)
    except ValueError:
        meta = {}
    return frontmatter.as_text(meta.get("title")) or formats.h1(text)[0] or path.stem


def _entry_cites(text: str, stems: set[str], spec: set[str]) -> list[str]:
    """The sections an entry cites: its anchors run, any @bind token, and the
    `#slug`s it writes in code spans, in the order they first appear."""
    found = [(m.start(), m.group(1)) for m in notes._BIND_REF.finditer(text)]
    found += [(pos, slug) for slug, pos in notes._code_spec_refs(text)]
    run = notes._anchors_run(text)
    if run is not None:
        pairs, _ = notes.anchor_pairs(text, stems, spec)
        found += [(run.start(), slug) for _, slug in pairs]
    return _unique(slug for _, slug in sorted(found) if slug in spec)


def _supplements(root: Path, docs: Path, spec: set[str]) -> list[dict]:
    stems = units.names(docs)
    out = []
    for sup in notes.supplements():
        directory = root / sup.path
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if not notes._is_entry(path):
                continue
            text = notes.mask_fences(path.read_text(encoding="utf-8"))
            out.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "role": sup.role,
                    "title": _entry_title(path, text),
                    "cites": _entry_cites(text, stems, spec),
                }
            )
    return out


def _heading_before(headings: list[tuple[int, str]], pos: int) -> str | None:
    label = None
    for off, title in headings:
        if off > pos:
            break
        label = title
    return label


def _related_work(
    docs: Path, root: Path, spec: set[str], *, locate: bool = True
) -> dict[str, list[dict]]:
    """slug -> where the related-work register cites it.

    A register of entries is located by work. A prose register is located by
    the heading each citation sits under, or, with ``locate`` false, counted
    per file: its headings are the register's own organization, which may
    follow the work's units, so the published index keeps to the count and
    `spec section --context` shows the headings locally."""
    from quirework.manuscript import related_work

    out: dict[str, list[dict]] = {}
    if not related_work.configured():
        return out
    if related_work.shape() == "prose":
        for path in related_work.files(docs):
            text = notes.mask_fences(path.read_text(encoding="utf-8"))
            masked = notes._mask_nonprose(text)
            headings = [
                (m.start(), m.group(2).strip()) for m in notes._HEADING.finditer(masked)
            ]
            rel = _rel(path, root)
            counted: dict[str, int] = {}
            for slug, pos in notes._code_spec_refs(text):
                if slug not in spec:
                    continue
                if not locate:
                    counted[slug] = counted.get(slug, 0) + 1
                    continue
                item = {"file": rel, "heading": _heading_before(headings, pos)}
                if item not in out.setdefault(slug, []):
                    out[slug].append(item)
            for slug, n in counted.items():
                out.setdefault(slug, []).append({"file": rel, "citations": n})
        return out
    # A work that declares links is placed by them, each with its stance; one
    # that declares none, by the sections its prose cites, with no stance.
    for work in related_work.register():
        if work.links:
            bearing = [
                (k.section, k.stance, list(k.flags))
                for k in work.links
                if k.section in spec and k.problem is None
            ]
        else:
            bearing = [
                (slug, None, [])
                for slug in _unique(
                    s for s, _ in notes._code_spec_refs(notes.mask_fences(work.body))
                )
                if slug in spec
            ]
        for slug, stance, flags in bearing:
            item = {
                "work": work.slug,
                "title": work.title,
                "stance": stance,
                "flags": flags,
            }
            if item not in out.setdefault(slug, []):
                out[slug].append(item)
    for note in related_work.register_notes():
        for link in note.links:
            item = {"note": note.slug, "title": note.title}
            if link.section in spec and item not in out.setdefault(link.section, []):
                out[link.section].append(item)
    return out


def _rel(path: Path, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def build(docs: Path | None = None, root: Path | None = None) -> dict:
    """The index, as the JSON file holds it."""
    project = current_project()
    docs = Path(docs) if docs is not None else project.path("manuscript.source")
    root = Path(root) if root is not None else project.root
    files = _published(docs)
    sections = [s for f in files for s in notes._sections_of(f) if s.slug]
    spec = {s.slug for s in sections}
    parts = _parts_of(files, docs)
    part_of = {slug: title for title, slugs in parts for slug in slugs}
    indexed = {
        s.slug: Indexed(
            s.slug, s.title, part_of.get(s.slug), _rel(s.path, root), _words(s.body)
        )
        for s in sections
    }
    for s in sections:
        cites = _unique(
            name
            for name, _ in notes.spec_text_refs(s.body)
            if name in spec and name != s.slug
        )
        indexed[s.slug].cites = cites
        for name in cites:
            indexed[name].cited_by.append(s.slug)
    entries = _supplements(root, docs, spec)
    for entry in entries:
        for slug in entry["cites"]:
            if entry["role"] == "tests":
                indexed[slug].tested_by.append(entry["path"])
            elif entry["role"] == "expressions":
                indexed[slug].told_by.append(entry["path"])
    for slug, places in _related_work(docs, root, spec, locate=False).items():
        indexed[slug].related_work = places
    order = [slug for _, slugs in parts for slug in slugs]
    return {
        "generated_by": COMMAND,
        "parts": [{"title": title, "sections": slugs} for title, slugs in parts],
        "sections": [vars(indexed[slug]) for slug in order],
        "supplements": entries,
    }


def render_json(index: dict) -> str:
    return json.dumps(index, indent=2, ensure_ascii=False) + "\n"


def _cited(places: list[dict]) -> int:
    """How often the related work cites a section: its citations in a prose
    register, its works in a register of entries (a note is not a work)."""
    return sum(p.get("citations", 1) for p in places if "note" not in p)


def _bearing(places: list[dict]) -> str | None:
    """The works bearing on a section, grouped by stance in the project's
    order, and the notes on it; None for a prose register's counts."""
    works = [p for p in places if "work" in p]
    kept = [p["note"] for p in places if "note" in p]
    if not works and not kept:
        return None
    from quirework.manuscript import related_work

    declared = related_work.vocabulary("stances") or ()
    stances = sorted(
        {p["stance"] for p in works},
        key=lambda s: (
            s is None,
            declared.index(s) if s in declared else len(declared),
            s or "",
        ),
    )
    said = []
    for stance in stances:
        names = ", ".join(
            f"`{p['work']}`" + (f" ({', '.join(p['flags'])})" if p["flags"] else "")
            for p in works
            if p["stance"] == stance
        )
        said.append(f"{stance or 'cited by'} {names}")
    if kept:
        said.append(f"noted in {_code(kept)}")
    return "related work: " + "; ".join(said)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


def _code(names) -> str:
    return ", ".join(f"`{n}`" for n in names)


def _entry_name(path: str) -> str:
    """An entry named by its directory and stem, `drills/cold-start`."""
    p = Path(path)
    return f"{p.parent.name}/{p.stem}"


def _cell(text: str) -> str:
    return text.replace("|", r"\|")


def render_markdown(index: dict, at: Path, root: Path) -> str:
    """The map a reader opens, written beside ``at``: a table per part, and
    under it the names behind each row's counts."""
    sections = {s["slug"]: s for s in index["sections"]}
    base = at.parent

    def link(file: str) -> str:
        return Path(os.path.relpath(root / file, base)).as_posix()

    n_parts = sum(1 for p in index["parts"] if p["title"] is not None)
    lines = [
        f"<!-- Generated by `{COMMAND}` from the published spec, its",
        "     supplements and its related work. Do not edit it by hand: the",
        "     command rewrites it, and `quirework spec notes --check` fails",
        "     while it is stale. -->",
        "",
        "# Index",
        "",
        (
            _count(len(sections), "section")
            + (f" in {_count(n_parts, 'part')}" if n_parts else "")
            + ". Each table lists a part's sections in reading order, with "
            "how long each is, how many sections it cites and is cited by, "
            "how many entries test or tell it, and how often the related "
            "work cites it. The names behind the counts follow each table."
        ),
    ]
    for part in index["parts"]:
        lines += ["", f"## {part['title'] or 'Sections'}", ""]
        lines += [
            (
                "| Section | Slug | Words | Cites | Cited by | Tested by "
                "| Told by | Related work |"
            ),
            "|---|---|--:|--:|--:|--:|--:|--:|",
        ]
        for slug in part["sections"]:
            s = sections[slug]
            lines.append(
                f"| [{_cell(s['title'])}]({link(s['file'])}) | `{slug}` | "
                f"{s['words']} | {len(s['cites'])} | {len(s['cited_by'])} | "
                f"{len(s['tested_by'])} | {len(s['told_by'])} | "
                f"{_cited(s['related_work'])} |"
            )
        notes_ = []
        for slug in part["sections"]:
            s = sections[slug]
            said = []
            if s["cites"]:
                said.append(f"cites {_code(s['cites'])}")
            if s["cited_by"]:
                said.append(f"cited by {_code(s['cited_by'])}")
            if s["tested_by"]:
                said.append(
                    f"tested by {_code(_entry_name(p) for p in s['tested_by'])}"
                )
            if s["told_by"]:
                said.append(f"told by {_code(_entry_name(p) for p in s['told_by'])}")
            if (bearing := _bearing(s["related_work"])) is not None:
                said.append(bearing)
            if said:
                notes_.append(f"- `{slug}` " + "; ".join(said) + ".")
        if notes_:
            lines += [""] + notes_
    return mdwrap.reflow_columns("\n".join(lines) + "\n")


def rendered(docs: Path | None = None, root: Path | None = None) -> dict[Path, str]:
    """Each configured index file and the text it should hold."""
    project = current_project()
    root = Path(root) if root is not None else project.root
    found = targets(project)
    if not found:
        return {}
    index = build(docs, root)
    out = {}
    for fmt, path in found.items():
        path = root / path.relative_to(project.root)
        out[path] = (
            render_json(index) if fmt == "json" else render_markdown(index, path, root)
        )
    return out


def held_in(root: Path) -> bool:
    """Whether the tree at ``root`` is held to the index: the project itself,
    or a full copy of it, such as a staged tree a gate checks, which carries
    the project's configuration file. A tree built to exercise some other
    check holds no index and is not asked for one."""
    project = current_project()
    root = Path(root).resolve()
    if root == project.root.resolve():
        return True
    name = project.config_file.name if project.config_file else "quirework.toml"
    return (root / name).is_file()


def stale(docs: Path | None = None, root: Path | None = None) -> list[Path]:
    """The configured index files missing or not as the spec makes them."""
    return [
        path
        for path, text in rendered(docs, root).items()
        if not path.is_file() or path.read_text(encoding="utf-8") != text
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="quirework spec index",
        description="Generate the index of the published spec: every section "
        "with its part, length, the sections it cites and is cited by, the "
        "supplement entries that test or tell it, and the related work citing "
        "it. Without a flag, print a summary.",
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--write",
        action="store_true",
        help="write the files manuscript.spec_index names",
    )
    mode.add_argument(
        "--check", action="store_true", help="fail when an index file is stale"
    )
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    project = current_project()
    if not configured(project) and (args.write or args.check):
        print(
            f"spec index: configure {KEY} with a markdown file, a json file "
            "or both, to write the index",
            file=sys.stderr,
        )
        return 2
    try:
        if args.check:
            found = stale()
            for path in found:
                print(
                    f"✗ {_rel(path, project.root)} is stale; run `{COMMAND}`",
                    file=sys.stderr,
                )
            if not found:
                print("spec index: up to date")
            return 1 if found else 0
        if args.write:
            for path, text in rendered().items():
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.is_file() or path.read_text(encoding="utf-8") != text:
                    path.write_text(text, encoding="utf-8")
                    print(f"wrote {_rel(path, project.root)}")
                else:
                    print(f"{_rel(path, project.root)} is up to date")
            return 0
        index = build()
    except ConfigurationError as exc:
        print(f"spec index: {exc}", file=sys.stderr)
        return 2
    sections = index["sections"]
    entries = index["supplements"]
    print(
        f"{len(sections)} section(s) in {len(index['parts'])} part(s); "
        f"{sum(len(s['cites']) for s in sections)} citation(s) between sections; "
        f"{len(entries)} supplement entr{'y' if len(entries) == 1 else 'ies'}; "
        f"{sum(1 for s in sections if s['related_work'])} section(s) cited by "
        "the related work"
    )
    for fmt, path in targets(project).items():
        print(f"  {fmt}: {_rel(path, project.root)}")
    if not configured(project):
        print(f"  (not written: configure {KEY} to keep it as files)")
    return 0
