"""What a project publishes: one target, or several that build on each other.

A project that publishes one thing names its backend in `[publish]`, and that
is its one target, named for what the backend makes (`book` for Quarto, `site`
for Hugo). A project that publishes more names each target in a table of its
own, with the backend that builds it:

    [publish.targets.book]
    backend = "quarto"

    [publish.targets.site]
    backend = "hugo"
    site = "website"
    needs = ["book"]
    copy = [
      ["book:.", "book/"],
      ["book:*.pdf", "downloads/the-book.pdf"],
    ]

A target sees its own table laid over `[publish]`, so the shared keys
(`build`, and any a backend reads) are written once and a target overrides
what it must. `needs` names the targets built first, and `copy` takes their
output into this one's after it builds: `TARGET:GLOB` relative to that
target's output, into a directory (a destination ending in `/`, or `.` for
the whole tree) or onto one file, which the glob must then match exactly.
Nothing is copied into a site's own source; a site that deploys the build
tree deploys what the copy put there.

A project publishes through at most one Quarto target, because Quarto staging
has one set of directories under the build tree and every Quarto-only command
works on it.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from quirework.project import (
    ConfigurationError,
    Project,
    current_project,
    project_scope,
)
from quirework.publish import backends

_KEYS = {"backend", "needs", "copy"}


@dataclass(frozen=True)
class Target:
    name: str
    project: Project  # the project as this target sees it
    backend: backends.Backend
    needs: tuple[str, ...]
    copy: tuple[tuple[str, str, str], ...]  # (target, glob, destination)


def _copy_rules(name: str, rows) -> tuple[tuple[str, str, str], ...]:
    where = f"publish.targets.{name}.copy"
    if not isinstance(rows, list):
        raise ConfigurationError(f"{where} must be a list of [source, destination]")
    out = []
    for row in rows:
        if not (
            isinstance(row, list)
            and len(row) == 2
            and all(isinstance(x, str) and x for x in row)
            and ":" in row[0]
        ):
            raise ConfigurationError(
                f'{where}: each row is ["TARGET:GLOB", "DESTINATION"], not {row!r}'
            )
        source, destination = row
        target, _, pattern = source.partition(":")
        if Path(destination).is_absolute() or ".." in Path(destination).parts:
            raise ConfigurationError(
                f"{where}: {destination} leaves the target's output"
            )
        out.append((target, pattern, destination))
    return tuple(out)


def targets(project: Project | None = None) -> dict[str, Target]:
    """Every target, by name, in the order declared."""
    project = project or current_project()
    declared = project.get("publish.targets")
    if not declared:
        backend = backends.selected(project)
        name = backend.target or backend.name
        return {name: Target(name, project, backend, (), ())}
    if project.get("publish.backend") is not None:
        raise ConfigurationError(
            "publish.backend and publish.targets both say what builds the work; "
            "name the backend in each target"
        )
    out = {}
    for name, table in declared.items():
        where = f"publish.targets.{name}"
        if not isinstance(table, dict):
            raise ConfigurationError(f"{where} must be a table")
        if "backend" not in table:
            raise ConfigurationError(f"{where} needs a backend")
        needs = table.get("needs", [])
        if not isinstance(needs, list) or not all(isinstance(n, str) for n in needs):
            raise ConfigurationError(f"{where}.needs must be a list of target names")
        out[name] = Target(
            name,
            project.overlay("publish", where, drop=("targets",)),
            backends.get(table["backend"]),
            tuple(needs),
            _copy_rules(name, table.get("copy", [])),
        )
    return out


def order(wanted: list[str], known: dict[str, Target]) -> list[str]:
    """``wanted`` and everything it needs, each after what it needs."""
    out: list[str] = []

    def visit(name, path):
        if name in path:
            cycle = " -> ".join([*path[path.index(name) :], name])
            raise ConfigurationError(f"publish targets need each other: {cycle}")
        if name in out:
            return
        for need in known[name].needs:
            visit(need, [*path, name])
        out.append(name)

    for name in wanted:
        visit(name, [])
    return out


def validate(project: Project) -> None:
    """Refuse targets that cannot all be built as declared."""
    known = targets(project)
    quarto = [t.name for t in known.values() if t.backend.name == "quarto"]
    if len(quarto) > 1:
        raise ConfigurationError(
            f"publish targets {', '.join(quarto)} are all Quarto; a project has "
            f"one Quarto target, since its staging and commands are one"
        )
    for target in known.values():
        for need in target.needs:
            if need not in known:
                raise ConfigurationError(
                    f"publish.targets.{target.name} needs {need!r}, which is no "
                    f"target; targets: {', '.join(known)}"
                )
        for source, _, _ in target.copy:
            if source not in target.needs:
                raise ConfigurationError(
                    f"publish.targets.{target.name} copies from {source!r}, which "
                    f"it does not list in needs"
                )
        with project_scope(target.project):
            target.backend.validate(target.project)
    order(list(known), known)


def for_backend(backend: str, command: str, project: Project) -> Project:
    """The project as the one target built by ``backend`` sees it, for a
    command that works on that backend's build alone."""
    known = targets(project)
    found = [t for t in known.values() if t.backend.name == backend]
    if not found:
        have = ", ".join(
            t.name if t.name == t.backend.name else f"{t.name} ({t.backend.name})"
            for t in known.values()
        )
        raise ConfigurationError(
            f"`quirework {command}` works on a {backend} build, and no target of "
            f"this project is one: {have}"
        )
    return found[0].project


def copy_into(target: Target, known: dict[str, Target]) -> list[str]:
    """Apply ``target``'s copy rules into its finished output; return what
    was copied, as `source -> destination` lines."""
    into = target.backend.output(target.project, target.name)
    done = []
    for source, pattern, destination in target.copy:
        needed = known[source]
        origin = needed.backend.output(needed.project, source)
        if not origin.is_dir():
            raise ConfigurationError(
                f"{target.name} copies from {source}, which has no output at "
                f"{origin}; build it first"
            )
        dest = into / destination
        if pattern in (".", ""):
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copytree(origin, dest, dirs_exist_ok=True)
            done.append(f"{source}:{pattern} -> {destination}")
            continue
        matched = sorted(p for p in origin.glob(pattern) if p.is_file())
        if destination.endswith("/"):
            if not matched:
                raise ConfigurationError(f"{source}:{pattern} matched nothing")
            dest.mkdir(parents=True, exist_ok=True)
            for path in matched:
                shutil.copy2(path, dest / path.name)
        else:
            if len(matched) != 1:
                raise ConfigurationError(
                    f"{source}:{pattern} names one file, {destination}, and "
                    f"matched {len(matched)}"
                )
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(matched[0], dest)
        done.append(f"{source}:{pattern} -> {destination}")
    return done
