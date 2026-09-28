"""Where the work lives: which files are the written work, in any format.

Several tools ask the same question in different words. Did this session edit
the work or the tooling around it? Is this commit one a reviewer must see?
Which paths does the history of the prose run through? Each used to answer it
for itself, from `manuscript.source` and the companion directories, which is
right for a book whose spec and marks sit inside its source and wrong for a
site that keeps them outside the tree its generator renders. This module is
the one answer, and it is built from what the project already declares:

- the units and their marks, as the format lays them out;
- the register: the shared spec, each unit's own spec, the sources, the
  references and the review notes;
- the companion collections;
- and, kept apart, the working roots (`manuscript.noncanonical_roots`), which
  are writing without authority. A caller measuring effort counts them; a
  caller deciding what a reviewer must see does not.

Every entry is a glob over project-relative POSIX paths in the `core.globs`
dialect. A project without a manuscript has an empty footprint.
"""

from __future__ import annotations

from dataclasses import dataclass

from quirework.core import globs
from quirework.manuscript import formats
from quirework.project import Project, current_project


@dataclass(frozen=True)
class Footprint:
    units: tuple[str, ...]  # the prose and its marks, as the format lays them out
    register: tuple[str, ...]  # spec files, references, review notes
    companions: tuple[str, ...]  # reader-facing collections beside the work
    working: tuple[str, ...]  # working material: writing, but no authority

    def patterns(self, working: bool = False) -> tuple[str, ...]:
        out = self.units + self.register + self.companions
        return out + self.working if working else out

    def covers(self, path: str, working: bool = False) -> bool:
        """Whether a project-relative path is part of the work."""
        path = path[2:] if path.startswith("./") else path
        return globs.any_match(path, self.patterns(working))


def _directories(project: Project, key: str) -> tuple[str, ...]:
    """A configured list of directories, each as a glob for everything under it."""
    out = []
    for value in project.get(key, ()) or ():
        if not value:
            continue
        text = str(value).strip("/")
        out.append("**" if text in ("", ".") else f"{text}/**")
    return tuple(out)


def footprint(project: Project | None = None) -> Footprint:
    project = project or current_project()
    if not project.get("manuscript"):
        return Footprint((), (), (), ())
    units = formats.get(project.get("manuscript.format", "quarto")).footprint(project)
    register = tuple(
        found
        for key in (
            "manuscript.spec",
            "manuscript.references",
            "manuscript.review_notes",
        )
        if (found := formats.relative(project, key)) is not None
    )
    unit_spec = project.get("manuscript.unit_spec")
    if unit_spec:
        register += (str(unit_spec).replace("{slug}", "*"),)
    from quirework.manuscript import sources

    register += sources.footprint_patterns(project)
    return Footprint(
        units,
        register,
        _directories(project, "manuscript.companion_dirs"),
        _directories(project, "manuscript.noncanonical_roots"),
    )
