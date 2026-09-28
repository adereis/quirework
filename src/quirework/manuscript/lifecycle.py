"""Where each unit of the work stands: an idea, planned, a draft, published.

A book's chapters are written and revised together and carry no state of
their own. A site's posts each live a life: a brief in working material, a
spec before a word is drafted, a draft, a publication, and for an explainer
years of revisions after it. The layers already hold every stage but the
first, so this module only reads them:

- an **idea** is a file `manuscript.ideas` matches that no unit has taken up
  yet: none carries its name, and no mark or spec names where it lives (its
  directory, when it is named by one), which is how a post's @genesis
  points at the brief it grew from
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
    home: str  # its directory when the idea is named by one, else rel


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
        if by_directory:
            home = path.parent.relative_to(project.root).as_posix()
            out.append(Idea(path.parent.name, rel, home))
        else:
            out.append(Idea(path.stem, rel, rel))
    return out


def _names_path(said: str, path: str) -> bool:
    """Whether prose names `path` as a whole path: `ideas/lamp` is named by
    "in ideas/lamp," and by "ideas/lamp/brief.md", not by "ideas/lamp-two".
    A trailing period ends a sentence; one followed by a letter starts an
    extension, and so names another file."""
    return re.search(rf"(?<![\w./-]){re.escape(path)}(?![\w-]|\.\w)", said) is not None


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
    # @genesis points at the brief it grew from. An idea named by its
    # directory is its directory, so naming that is enough; an idea that is
    # one file among others in a directory is named only by its own path.
    try:
        specs = notes.spec_files()
    except ConfigurationError:
        specs = []
    said = "\n".join(
        [a.body for a in notes.scan() if a.body]
        + [f.read_text(encoding="utf-8") for f in specs]
    )
    return [
        idea
        for idea in found
        if idea.slug not in taken and not _names_path(said, idea.home)
    ]


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


def _claims(text: str) -> str:
    """A spec's text as its claims: without comments, anchors or the spacing
    a reflow changes. Renaming an anchor or rewrapping a paragraph revises
    nothing a reader was told."""
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"\{[^{}\n]*\}", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def unrevised() -> list[str] | None:
    """Advisory lines: a published unit whose own spec's claims changed after
    the page was last dated. None when the project has no git history.

    The spec is compared as it stood at the commit that last dated the page,
    or, for a spec written after that, as it was first committed: writing a
    spec for a post already published describes it, and only a change after
    that revises it. The comparison is of claims, not bytes (`_claims`), and
    of the working file, so an uncommitted revision is named before it is
    committed and a page being redated in the same change is not.
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
        last_dated = _git(
            root, "log", "-1", "--format=%H", "-G", dated, "--", u.rel
        ).stdout.strip()
        if not last_dated:
            continue  # the page has no dated history yet
        then = _git(root, "show", f"{last_dated}:./{spec_rel}")
        if then.returncode:
            first = _git(
                root, "log", "--diff-filter=A", "--format=%H", "--", spec_rel
            ).stdout.split()
            if not first:
                continue  # being written, not revised
            then = _git(root, "show", f"{first[-1]}:./{spec_rel}")
        if _claims(then.stdout) != _claims(spec.read_text(encoding="utf-8")):
            out.append(
                f"  {u.rel}: {spec_rel} changed after the {units.noun()} was last "
                f"dated; revise it, set {field}, and tell a reader what changed"
            )
    return out
