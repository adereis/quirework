"""One spec section, printed, and with ``--context`` everything that touches it.

Working on one section of a long spec means reading it with what it leans on
and what leans on it: the sections it cites and the sections citing it, the
supplement entries that test or tell it, the related work that cites it, the
units that express it, and how much of its history the trail holds. Each of
those is a separate lookup otherwise. This prints them together:

    quirework spec section keeper
    quirework spec section keeper --context
    quirework spec section keeper --context --json

It reads every configured spec file, published or not, since it is a tool for
working in the project; the published map of the spec is `spec index`. A
neighbouring section is shown by its title and first sentence, enough to
decide whether to open it.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

from quirework.manuscript import notes, specindex, units
from quirework.project import ConfigurationError, current_project

_COMMENTS = re.compile(r"<!--.*?-->", re.DOTALL)
_SENTENCE = re.compile(r"(.+?[.!?])(?=\s|$)", re.DOTALL)


def raw_text(section: notes.Section) -> str:
    """The section as its file holds it, heading included, unmasked."""
    text = section.path.read_text(encoding="utf-8")
    masked = notes._mask_nonprose(notes.mask_fences(text))
    stops = notes.section_stops(masked)
    for m in notes._HEADING.finditer(masked):
        if len(m.group(1)) != 2:
            continue
        anchor = notes._ATTR_ID.search(m.group(3)) if m.group(3) else None
        if anchor and anchor.group(1) == section.anchor:
            stop = next((s for s in stops if s > m.start()), len(text))
            return text[m.start() : stop].rstrip() + "\n"
    raise LookupError(f"{section.slug} is no longer in {section.path}")


def first_sentence(body: str) -> str:
    """The section's first sentence, with comments and line breaks dropped."""
    prose = " ".join(_COMMENTS.sub(" ", body).split())
    m = _SENTENCE.match(prose)
    return (m.group(1) if m else prose).strip()


def _brief(section: notes.Section) -> dict:
    return {
        "slug": section.slug,
        "title": section.title,
        "first_sentence": first_sentence(section.body),
    }


def _find(slug: str, sections: list[notes.Section]) -> notes.Section:
    by_slug = {s.slug: s for s in sections}
    if slug not in by_slug:
        close = difflib.get_close_matches(slug, list(by_slug), n=5, cutoff=0.5)
        raise LookupError(
            f"no spec section is anchored {slug!r}"
            + (f"; did you mean {', '.join(close)}?" if close else "")
        )
    return by_slug[slug]


def section(slug: str, docs: Path | None = None) -> dict:
    """The section ``slug`` alone: its file and its text."""
    project = current_project()
    docs = Path(docs) if docs is not None else project.path("manuscript.source")
    found = _find(slug, [s for s in notes.spec_sections(docs) if s.slug])
    return {
        "slug": slug,
        "title": found.title,
        "file": specindex._rel(found.path, project.root),
        "text": raw_text(found),
    }


def context(slug: str, docs: Path | None = None) -> dict:
    """The section ``slug`` and what touches it, as ``--json`` prints it."""
    project = current_project()
    docs = Path(docs) if docs is not None else project.path("manuscript.source")
    sections = [s for s in notes.spec_sections(docs) if s.slug]
    section = _find(slug, sections)
    by_slug = {s.slug: s for s in sections}
    spec = set(by_slug)
    cites = specindex._unique(
        n for n, _ in notes.spec_text_refs(section.body) if n in spec and n != slug
    )
    cited_by = [
        s.slug
        for s in sections
        if s.slug != slug and any(n == slug for n, _ in notes.spec_text_refs(s.body))
    ]
    root = project.root
    entries = specindex._supplements(root, docs, spec)
    tested = [e for e in entries if slug in e["cites"] and e["role"] == "tests"]
    told = [e for e in entries if slug in e["cites"] and e["role"] == "expressions"]
    related = specindex._related_work(docs, root, spec).get(slug, [])
    titles = {u.slug: u.title for u in units.registry(docs)}
    order = list(titles)
    binding = sorted(
        (u for u, slugs in notes.chapter_bindings(docs).items() if slug in slugs),
        key=lambda u: (order.index(u) if u in order else len(order), u),
    )
    crumbs = None
    if project.get("manuscript.spec_trail") is not None:
        from quirework.manuscript import trail

        crumbs = trail.counts().get(slug, 0)
    return {
        "slug": slug,
        "title": section.title,
        "file": specindex._rel(section.path, root),
        "text": raw_text(section),
        "cites": [_brief(by_slug[n]) for n in cites],
        "cited_by": [_brief(by_slug[n]) for n in cited_by],
        "tested_by": [{"path": e["path"], "title": e["title"]} for e in tested],
        "told_by": [{"path": e["path"], "title": e["title"]} for e in told],
        "related_work": related,
        "units": [{"slug": u, "title": titles.get(u, u)} for u in binding],
        "trail": crumbs,
    }


def _neighbours(label: str, items: list[dict]) -> list[str]:
    if not items:
        return [f"{label}: none"]
    out = [f"{label} ({len(items)}):"]
    for item in items:
        out.append(f"  #{item['slug']}  {item['title']}")
        if item["first_sentence"]:
            out.append(f"      {item['first_sentence']}")
    return out


def _entries(label: str, items: list[dict]) -> list[str]:
    if not items:
        return [f"{label}: none"]
    return [f"{label} ({len(items)}):"] + [
        f"  {i['path']}  {i['title']}" for i in items
    ]


def render(found: dict) -> str:
    noun = units.noun()
    lines = [
        f"{found['file']}",
        "",
        found["text"].rstrip(),
        "",
        "-" * 72,
    ]
    lines += _neighbours("Cites", found["cites"])
    lines += _neighbours("Cited by", found["cited_by"])
    lines += _entries("Tested by", found["tested_by"])
    lines += _entries("Told by", found["told_by"])
    if found["related_work"]:
        lines.append(f"Related work ({len(found['related_work'])}):")
        for place in found["related_work"]:
            if "work" in place:
                lines.append(f"  {place['work']}  {place['title']}")
            else:
                lines.append(f"  {place['file']}: {place['heading'] or '(top)'}")
    else:
        lines.append("Related work: none")
    if found["units"]:
        lines.append(f"Bound by ({len(found['units'])}):")
        lines += [f"  {u['slug']}  {u['title']}" for u in found["units"]]
    else:
        lines.append(f"Bound by: no {noun}")
    if found["trail"] is not None:
        lines.append(f"Trail: {found['trail']} breadcrumb(s)")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="quirework spec section",
        description="Print one spec section. With --context, also the "
        "sections it cites and that cite it, the supplement entries that test "
        "or tell it, the related work citing it, the units that bind it and "
        "its trail.",
    )
    ap.add_argument("slug", help="the section's {#slug}")
    ap.add_argument("--context", action="store_true", help="add what touches it")
    ap.add_argument("--json", action="store_true", help="print it as JSON")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    slug = args.slug.lstrip("#")
    try:
        found = context(slug) if args.context else section(slug)
    except (LookupError, ConfigurationError) as exc:
        print(f"spec section: {exc}", file=sys.stderr)
        return 2
    if not args.context:
        if args.json:
            print(json.dumps(found, indent=2, ensure_ascii=False))
        else:
            print(f"{found['file']}\n\n{found['text']}", end="")
        return 0
    if args.json:
        print(json.dumps(found, indent=2, ensure_ascii=False))
    else:
        print(render(found), end="")
    return 0
