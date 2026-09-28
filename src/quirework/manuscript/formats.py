"""What a manuscript format is, and how one is selected.

A format answers every question the spec layer asks about the written work's
layout: which files are its units and in what order, which files are prose,
where a unit's marks sidecar lives, and which unit a sidecar belongs to. The
spec, marks, quotations and trail are written once against this interface, so
a new layout is one new `Format` and no change to them.

`manuscript.format` names one. The two built in are `quarto` (a Quarto book's
chapters, in `_quarto.yml` order) and `pages` (a site's Markdown pages). A
project can select a format of its own as an installed extension,
`module:function`, where the function returns a `Format`; like every
extension, it is loaded only when the project names it.
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from pathlib import Path

from quirework.manuscript import frontmatter
from quirework.project import ConfigurationError, Project, current_project, extension

# The formats Quirework ships, by module. Imported on first use, so a project
# that never names a format pays for neither.
BUILTIN = {
    "quarto": "quirework.manuscript.quarto",
    "pages": "quirework.manuscript.pages",
}

# A slug is lowercase words joined by hyphens: the one shape a reference, a
# path pattern and a spec binding can all carry without quoting.
SLUG_OK = re.compile(r"\A[a-z0-9]+(-[a-z0-9]+)*\Z")

# Developer-guide files carry annotation *examples* in prose, not content.
SKIP_NAMES = {"AGENTS.md", "CLAUDE.md", "GEMINI.md"}


@dataclass(frozen=True)
class Unit:
    slug: str  # the durable identity: sidecar, anchor, @unit(), binding key
    rel: str  # path to the prose file, relative to the project root
    title: str  # the H1 or frontmatter title, which may drift from the slug
    number: int | None = None  # a printed chapter number, from position
    date: str | None = None  # a page's frontmatter date
    draft: bool = False  # a page's frontmatter draft flag


class Format:
    """One way of laying out a manuscript. Subclasses answer for their layout.

    Every method that reads the tree takes ``docs``, the source directory,
    which is `manuscript.source` in normal use and a temporary tree when a
    check reads the work as it stood at a revision.
    """

    name: str = ""
    # What one unit is called in messages, unless `manuscript.unit_name` says.
    noun: str = "unit"

    def validate(self, project: Project) -> None:
        """Refuse a configuration this format cannot read. Called by
        `Project.require("manuscript")` inside the project's scope."""

    def registry(self, docs: Path) -> list[Unit]:
        """Every unit in reading order."""
        raise NotImplementedError

    def order_problems(self, docs: Path) -> list[str]:
        """What stops the units on disk from being the manuscript's units."""
        raise NotImplementedError

    def prose_files(self, docs: Path) -> list[Path]:
        """Every prose file on disk, whether or not a reading order lists it."""
        raise NotImplementedError

    def slug_of(self, path: Path) -> str:
        """The unit slug a prose file carries."""
        raise NotImplementedError

    def marks_path(self, slug: str, docs: Path) -> Path | None:
        """Where the unit's marks sidecar lives, or would; None if it has none."""
        raise NotImplementedError

    def sidecars(self, docs: Path) -> list[Path]:
        """Every marks sidecar on disk."""
        raise NotImplementedError

    def sidecar_slug(self, path: Path) -> str | None:
        """The unit a sidecar belongs to, or None if the file is no sidecar."""
        raise NotImplementedError

    def prose_for_sidecar(self, path: Path, docs: Path) -> Path | None:
        """The prose file a sidecar's marks describe; None when it has none."""
        raise NotImplementedError

    def harvest(self, docs: Path) -> list[Path]:
        """The files and directories `notes.scan` reads by default."""
        raise NotImplementedError

    def default_layers(self, project: Project) -> tuple[str, ...]:
        """Where the metadata sidecars are, as globs relative to the root."""
        raise NotImplementedError

    def listing(self, units: list[Unit]) -> list[str]:
        """The rows `spec units` prints, one per unit."""
        return [f"  {u.slug:34} {u.title}" for u in units]

    def footprint(self, project: Project) -> tuple[str, ...]:
        """Where the units and their marks live, as globs over project-relative
        paths (`core.globs`). Everything else in a work's footprint, its spec
        and companions, is the project's to declare and `footprint` adds it."""
        return ()


def get(name: str) -> Format:
    """The format ``name`` names: a built-in, or an extension's."""
    if name in BUILTIN:
        return importlib.import_module(BUILTIN[name]).FORMAT
    if ":" in name:
        made = extension(name)()
        if not isinstance(made, Format):
            raise ConfigurationError(
                f"manuscript.format {name} must return a quirework Format"
            )
        return made
    raise ConfigurationError(
        f"manuscript.format must be one of {', '.join(BUILTIN)} or an installed "
        f"module:function, not {name!r}"
    )


def current() -> Format:
    """The selected project's format; `quarto` when it declares none."""
    return get(current_project().get("manuscript.format", "quarto"))


# --------------------------------------------------------------------------- #
# helpers the built-in formats share
# --------------------------------------------------------------------------- #
def base(docs: Path) -> Path:
    """What a unit's `rel` is measured from: the project root when the source
    is inside it, else the directory holding the source. The second case is a
    tree outside any project, which is what a test hands the registry."""
    root = current_project().root
    return root if docs.resolve().is_relative_to(root) else docs.parent


def rel(path: Path) -> str:
    root = current_project().root
    path = Path(path)
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


_H1 = re.compile(r"^#\s+(.*?)\s*(?:\{(.*?)\})?\s*$")


def h1(text: str) -> tuple[str, str]:
    """(title, attribute block) of the first H1 after any frontmatter."""
    found = frontmatter.block(text)
    body = text[found.end() :] if found else text
    for line in body.splitlines():
        m = _H1.match(line)
        if m:
            return m.group(1).strip(), (m.group(2) or "")
    return "", ""


def relative(project: Project, key: str) -> str | None:
    """A configured path as a project-relative POSIX path; None when it is
    unset or lies outside the project, where no project-relative glob can
    name it."""
    if project.get(key) is None:
        return None
    try:
        return project.path(key).relative_to(project.root).as_posix()
    except ValueError:
        return None


def slug_pattern(key: str) -> str | None:
    """A configured path pattern that must carry `{slug}`, or None if unset."""
    pattern = current_project().get(key)
    if pattern is not None and "{slug}" not in str(pattern):
        raise ConfigurationError(
            f"{key} must contain {{slug}}, e.g. 'specs/{{slug}}/"
            f"{'spec' if key.endswith('spec') else 'marks'}.md'"
        )
    return pattern


def pattern_regex(pattern: str) -> re.Pattern:
    """A `{slug}` path pattern as a regex capturing the slug."""
    head, _, tail = pattern.partition("{slug}")
    return re.compile(re.escape(head) + r"(?P<slug>[^/]+)" + re.escape(tail) + r"\Z")
