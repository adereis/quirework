"""Where the spec stood at an earlier revision.

Two tools ask the past the same question. `spec diff` reads every section at
both ends of a range, and the trail's suggestions follow one section back
through the commits that changed it. Both used to read the spec files the
project configures now, which is right until a spec file moves: read at a
revision from before the move, today's paths name nothing, and every section
reads as new.

So a revision is read through the configuration it was committed with. Its
`quirework.toml` says where its spec files were, with any setting a release
has since renamed read under its new name (`project.RENAMED`). Where that
configuration cannot be read (absent, malformed, or of an older schema), the
project's map of moved paths places today's spec files at their old paths
instead. It is a table in the chapter policy file:

    [moved_paths]
    "notes/claims.md" = "spec/"         # one file split into a directory
    "drafts/" = "manuscript/"           # a directory moved whole

Each key is a path the project once used, relative to the project root, and
its value the path it moved to. A path ending in `/` is a directory, and
names everything below it. Where neither resolves a spec file, the read stops
with `Unplaced`, naming the path: it never falls back to today's files, which
would report every section of the older revision as new.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath

from quirework.manuscript import manifest, notes
from quirework.project import (
    SCHEMA,
    ConfigurationError,
    current_project,
    translate_renamed,
)


class Unplaced(LookupError):
    """A spec file that neither a revision's configuration nor the map of
    moved paths places at that revision."""


@dataclass(frozen=True)
class Spec:
    """The spec files of one revision, in reading order, and how they were
    found: ``configuration`` or ``moved paths``. Each file is a
    repository-relative path and the unit whose own spec it is, or None for a
    shared file, read by that revision's own `unit_spec` pattern."""

    files: tuple[tuple[str, str | None], ...]
    how: str
    why: str = ""  # why the configuration could not be read, when it was not


@dataclass(frozen=True)
class Location:
    rev: str
    path: str  # repository-relative
    first: int  # 1-based line of the section's heading
    last: int  # 1-based line the section ends on


def _git(top, *args):
    return subprocess.run(
        ["git", "-C", str(top), *args], capture_output=True, text=True, check=True
    ).stdout


def top() -> Path:
    root = current_project().root
    try:
        return Path(_git(root, "rev-parse", "--show-toplevel").strip())
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LookupError("the project is not in a git repository") from exc


def _prefix(top_: Path) -> PurePosixPath:
    """The project root, relative to the repository."""
    return PurePosixPath(current_project().root.resolve().relative_to(top_).as_posix())


def _show(top_, rev, path) -> str | None:
    try:
        return _git(top_, "show", f"{rev}:{path}")
    except subprocess.CalledProcessError:
        return None


def _tree(top_, rev) -> frozenset[str]:
    return _cached_tree(str(top_), _resolve(top_, rev))


@lru_cache(maxsize=256)
def _cached_tree(top_, sha) -> frozenset[str]:
    return frozenset(_git(top_, "ls-tree", "-r", "--name-only", sha).splitlines())


def _resolve(top_, rev) -> str:
    try:
        return _git(
            top_, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"
        ).strip()
    except subprocess.CalledProcessError:
        raise LookupError(f"'{rev}' names no commit in this repository") from None


def _join(prefix: PurePosixPath, path: str) -> str:
    return (prefix / path).as_posix() if str(prefix) != "." else path


def _configured(top_, rev, prefix) -> tuple[tuple | None, str]:
    """The spec files ``rev``'s own configuration names, or (None, why)."""
    text = _show(top_, rev, _join(prefix, "quirework.toml"))
    if text is None:
        return None, "it holds no quirework.toml"
    try:
        values = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return None, f"its quirework.toml does not parse ({exc})"
    if values.get("schema") != SCHEMA:
        return None, f"its quirework.toml is not schema {SCHEMA}"
    try:
        values = translate_renamed(values)
    except ValueError as exc:
        return None, str(exc)
    manuscript = values.get("manuscript") or {}
    shared = manuscript.get("spec")
    shared = [shared] if isinstance(shared, str) else list(shared or [])
    tree = _tree(top_, rev)
    files = []
    for path in shared:
        name = _join(prefix, path)
        if name not in tree:
            raise Unplaced(
                f"{rev}'s configuration names the spec file {path}, which {rev} "
                f"does not hold"
            )
        if not manifest.is_manifest(Path(name)):
            files.append((name, None))
            continue
        try:
            found = manifest.parse(Path(name), _show(top_, rev, name) or "")
        except ConfigurationError as exc:
            return None, f"its spec manifest cannot be read ({exc})"
        for listed in found.files():
            listed = listed.as_posix()
            if listed not in tree:
                raise Unplaced(
                    f"{rev}'s spec manifest {path} lists {listed}, which {rev} "
                    f"does not hold"
                )
            files.append((listed, None))
    pattern = manuscript.get("unit_spec")
    if pattern:
        regex = re.compile(
            "^"
            + re.escape(_join(prefix, str(pattern))).replace(
                re.escape("{slug}"), r"(?P<slug>[^/]+)"
            )
            + "$"
        )
        shared = {name for name, _ in files}
        for path in sorted(tree):
            if path not in shared and (m := regex.match(path)):
                files.append((path, m.group("slug")))
    return tuple(files), ""


def moved_paths() -> dict[str, str]:
    """The project's map of moved paths, old to new, project-relative."""
    table = current_project().data("manuscript.chapter_policy", {}).get("moved_paths")
    return {str(k): str(v) for k, v in (table or {}).items()}


def _back(path: str, moves: dict[str, str]) -> str | None:
    """The path ``path`` had before the most specific move that produced it."""
    best = None
    for old, new in moves.items():
        if new.endswith("/"):
            if path.startswith(new):
                # A file split into a directory maps back to the file; a
                # directory moved whole keeps the rest of the path.
                rest = path[len(new) :]
                found = old + rest if old.endswith("/") else old
            else:
                continue
        elif path == new:
            found = old
        else:
            continue
        if best is None or len(new) > best[0]:
            best = (len(new), found)
    return best[1] if best else None


def _mapped(top_, rev, prefix) -> tuple[str, ...]:
    """Today's spec files placed at ``rev`` through the map of moved paths."""
    moves = moved_paths()
    tree = _tree(top_, rev)
    root = current_project().root.resolve()
    out = []
    for path in notes.spec_files():
        today = path.resolve().relative_to(root).as_posix()
        here, seen = today, {today}
        while _join(prefix, here) not in tree:
            back = _back(here, moves)
            if back is None or back in seen:
                raise Unplaced(
                    f"the spec file {today} is not at {rev}, and no moved path "
                    f"places it there (manuscript.chapter_policy, moved_paths)"
                )
            here = back
            seen.add(here)
        name = (_join(prefix, here), notes.spec_owner(path))
        if name not in out:
            out.append(name)
    return tuple(out)


def spec_at(rev: str, top_: Path | None = None) -> Spec:
    """Where ``rev`` kept its spec: through its own configuration, else the
    map of moved paths, else `Unplaced`."""
    top_ = top_ or top()
    sha = _resolve(top_, rev)
    prefix = _prefix(top_)
    files, why = _configured(top_, sha, prefix)
    if files is not None:
        return Spec(files, "configuration")
    return Spec(_mapped(top_, sha, prefix), "moved paths", why)


def _sections(top_, rev, name, owner):
    """``(slug, section, text)`` for each anchored section of one file at
    ``rev``, named as that revision named it."""
    text = _show(top_, rev, name) or ""
    for section in notes._sections_of(top_ / name, text):
        if section.anchor:
            slug = f"{owner}/{section.anchor}" if owner else section.anchor
            yield slug, section, text


def sections_at(rev: str, top_: Path | None = None, spec: Spec | None = None):
    """``{slug: (title, body)}`` for every spec section at ``rev``."""
    top_ = top_ or top()
    spec = spec or spec_at(rev, top_)
    return {
        slug: (section.title, section.body)
        for name, owner in spec.files
        for slug, section, _ in _sections(top_, rev, name, owner)
    }


def section_range(text: str, anchor: str) -> tuple[int, int] | None:
    """``(first, last)`` 1-based lines of the section anchored ``anchor``."""
    masked = notes._mask_nonprose(notes.mask_fences(text))
    heads = [m for m in notes._HEADING.finditer(masked) if len(m.group(1)) == 2]
    stops = notes.section_stops(masked)
    for m in heads:
        found = notes._ATTR_ID.search(m.group(3) or "")
        if found and found.group(1) == anchor:
            first = text.count("\n", 0, m.start()) + 1
            stop = next((s for s in stops if s > m.start()), None)
            last = (
                text.count("\n", 0, stop)
                if stop is not None
                else text.count("\n") + (not text.endswith("\n"))
            )
            return first, last
    return None


def locate(slug: str, rev: str, top_: Path | None = None) -> Location | None:
    """Where the section ``slug`` stood at ``rev``, or None when ``rev``'s spec
    holds no such section. A unit's own section, ``unit/anchor``, is looked
    for by its anchor in that unit's spec file."""
    top_ = top_ or top()
    for name, owner in spec_at(rev, top_).files:
        for found, section, text in _sections(top_, rev, name, owner):
            if found == slug:
                lines = section_range(text, section.anchor)
                if lines is not None:
                    return Location(rev, name, *lines)
    return None


def body(location: Location, top_: Path | None = None) -> str:
    """The section's text at its location, comments dropped and whitespace
    collapsed, as `spec diff` compares sections."""
    top_ = top_ or top()
    lines = (_show(top_, location.rev, location.path) or "").splitlines()
    text = "\n".join(lines[location.first : location.last])
    return " ".join(notes._COMMENT.sub(" ", text).split())
