"""Where each unit of the work stands: an idea, planned, a draft, published.

A book's chapters are written and revised together and carry no state of
their own. A site's posts each live a life: a brief in working material, a
spec before a word is drafted, a draft, a publication, and for an explainer
years of revisions after it. The layers already hold every stage but the
first, so this module only reads them:

- an **idea** is a file `manuscript.ideas` matches that no unit has taken up
  yet: none carries its name, and no mark or spec names its path, which is
  how a post's @genesis points at the brief it grew from
- a unit is **planned** while it has a spec and no page
- a page is a **draft** or **published** by its own frontmatter

Two advisories follow from the published stage, because what an author
promised a reader is at stake there and nowhere earlier. A published unit
with an open @todo is waiting on something its readers cannot see. And a
published unit whose own spec changed after the page was last dated says
something it no longer holds, or holds something it no longer says, until the
page is revised and its revision field (`lastmod` unless
`manuscript.revision_field` names another) records it. The second reads the
history, so it needs the project in git; each page is compared with the
commit that last touched its spec, not with a clock.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from quirework.manuscript import notes, units
from quirework.project import ConfigurationError, current_project


@dataclass(frozen=True)
class Idea:
    slug: str
    rel: str  # the file the ideas glob matched, relative to the root


def revision_field() -> str:
    return str(current_project().get("manuscript.revision_field", "lastmod"))


def ideas() -> list[Idea]:
    """Every file `manuscript.ideas` matches, named as a unit would be: by its
    directory when the glob names a fixed file (`ideas/*/brief.md`), else by
    its stem."""
    project = current_project()
    pattern = project.get("manuscript.ideas")
    if not pattern:
        return []
    by_directory = not any(c in pattern.rpartition("/")[2] for c in "*?[")
    out = []
    for path in sorted(project.root.glob(pattern)):
        if not path.is_file() or path.name.startswith("_") or path.name == "README.md":
            continue
        rel = path.relative_to(project.root).as_posix()
        slug = path.parent.name if by_directory else path.stem
        out.append(Idea(slug, rel))
    return out


def planned() -> list[tuple[str, Path]]:
    """(slug, spec file) for each unit spec whose unit has no page yet."""
    if notes._unit_spec_pattern() is None:
        return []
    present = units.names()
    return [
        (owner, f)
        for f in notes.spec_files()
        if (owner := notes.spec_owner(f)) is not None and owner not in present
    ]


def open_ideas() -> list[Idea]:
    """The ideas no unit has taken up yet."""
    found = ideas()
    if not found:
        return []
    taken = units.names() | {slug for slug, _ in planned()}
    # A mark or a spec that names the idea's path has taken it up: a post's
    # @genesis points at the brief it grew from.
    try:
        specs = notes.spec_files()
    except ConfigurationError:
        specs = []
    said = " ".join(a.body for a in notes.scan() if a.body)
    said += " ".join(f.read_text(encoding="utf-8") for f in specs)
    out = []
    for idea in found:
        where = idea.rel.rpartition("/")[0] if "/" in idea.rel else idea.rel
        if idea.slug in taken or idea.rel in said or f"{where}/" in said:
            continue
        out.append(idea)
    return out


def published() -> list[units.Unit]:
    return [u for u in units.registry() if u.draft is False]


def open_todos() -> list[str]:
    """Advisory lines: an open @todo on a published unit."""
    live = {u.rel for u in published()}
    if not live:
        return []
    return [
        f"  {a.source or a.file}:{a.line}: @todo {a.body}".rstrip()
        for a in notes.scan()
        if a.kind == "todo" and a.file in live
    ]


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    )


def unrevised() -> list[str] | None:
    """Advisory lines: a published unit whose own spec was revised after the
    page was last dated. None when the project has no git history to read.

    Creating a spec is not revising one: a spec written for a post already
    published describes it, and the first change that counts is the one
    after that. So a spec not yet tracked is never named, a tracked spec with
    uncommitted edits is named unless the page is being redated too, and a
    committed spec is named when a commit after the page's last dating
    changed it, other than the commit that created it.
    """
    project = current_project()
    root = project.root
    if _git(root, "rev-parse", "--verify", "--quiet", "HEAD").returncode:
        return None
    field = revision_field()
    dated = rf"^(date|{re.escape(field)})[[:space:]]*[:=]"
    redating = re.compile(rf"(?m)^[+-](date|{re.escape(field)})\s*[:=]")
    out = []
    for u in published():
        spec = notes.unit_spec_path(u.slug)
        if spec is None or not spec.is_file():
            continue
        spec_rel = spec.relative_to(root).as_posix()
        if redating.search(_git(root, "diff", "HEAD", "-U0", "--", u.rel).stdout):
            continue  # being redated now
        status = _git(root, "status", "--porcelain", "--", spec_rel).stdout
        if status.startswith("??"):
            continue  # being written, not revised
        if status.strip():
            stale = True
        else:
            last_dated = _git(
                root, "log", "-1", "--format=%H", "-G", dated, "--", u.rel
            ).stdout.strip()
            if not last_dated:
                continue  # the page has no dated history yet
            created = _git(
                root, "log", "--diff-filter=A", "--format=%H", "--", spec_rel
            ).stdout.split()
            since = _git(
                root, "log", "--format=%H", f"{last_dated}..HEAD", "--", spec_rel
            ).stdout.split()
            stale = any(c not in created[-1:] for c in since)
        if stale:
            out.append(
                f"  {u.rel}: {spec_rel} changed after the {units.noun()} was last "
                f"dated; revise it, set {field}, and tell a reader what changed"
            )
    return out
