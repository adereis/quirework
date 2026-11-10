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

import copy
import re
import subprocess
import sys
import tomllib
from collections.abc import Mapping
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

# Each (file, revision) read as not yet made, so its note is printed once.
_GUESSED: set[tuple[str, str]] = set()


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


def _settings(top_, sha, prefix) -> tuple[dict | None, str]:
    """``sha``'s own configuration, renamed settings under their new names,
    or (None, why it cannot be read). Each call gets its own copy."""
    values, why = _cached_settings(str(top_), sha, str(prefix))
    return copy.deepcopy(values), why


@lru_cache(maxsize=64)
def _cached_settings(top_, sha, prefix) -> tuple[dict | None, str]:
    prefix = PurePosixPath(prefix)
    text = _show(top_, sha, _join(prefix, "quirework.toml"))
    if text is None:
        return None, "it holds no quirework.toml"
    try:
        values = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return None, f"its quirework.toml does not parse ({exc})"
    if values.get("schema") != SCHEMA:
        return None, f"its quirework.toml is not schema {SCHEMA}"
    try:
        return translate_renamed(values), ""
    except ValueError as exc:
        return None, str(exc)


def settings_at(rev: str, top_: Path | None = None) -> tuple[dict | None, str]:
    """The configuration committed at ``rev``, read as today's tool reads
    it, or (None, why it cannot be read)."""
    top_ = top_ or top()
    return _settings(top_, _resolve(top_, rev), _prefix(top_))


def settings_for(rev: str, top_: Path | None = None) -> tuple[dict, str]:
    """Settings that read ``rev`` where it kept its files, and how they were
    found: ``configuration`` or ``moved paths``.

    ``rev``'s own configuration where it can be read. Otherwise today's
    settings, with every path they name that exists today placed at ``rev``
    through the map of moved paths: a path that never moved is placed as
    itself, and one the map cannot place stops the read (`Unplaced`)."""
    top_ = top_ or top()
    sha, prefix = _resolve(top_, rev), _prefix(top_)
    values, _ = _settings(top_, sha, prefix)
    if values is not None:
        return values, "configuration"
    project = current_project()
    root = project.root

    def place(value):
        if isinstance(value, Mapping):
            return {k: place(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [place(v) for v in value]
        if isinstance(value, str) and value and (root / value).exists():
            placed = _back_to(top_, sha, prefix, value.rstrip("/"), "the path")
            placed = placed[len(str(prefix)) + 1 :] if str(prefix) != "." else placed
            return placed + ("/" if value.endswith("/") else "")
        return value

    return place(project.settings), "moved paths"


def _value(values: dict, key: str):
    node = values
    for part in key.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def _back_to(top_, sha, prefix, today: str, what: str) -> str:
    """``today``, a project-relative path, as the map of moved paths places
    it at ``sha``, or `Unplaced`."""
    moves, tree = moved_paths(), _tree(top_, sha)
    here, seen = today, {today}
    while not _held(tree, _join(prefix, here)):
        back = _back(here, moves)
        if back is None or back in seen:
            raise Unplaced(
                f"{what} {today} is not at {sha[:8]}, and no moved path places it "
                f"there (manuscript.chapter_policy, moved_paths)"
            )
        here = back
        seen.add(here)
    return _join(prefix, here)


def _held(tree: frozenset[str], name: str) -> bool:
    """Whether ``tree`` holds the file or directory ``name``."""
    name = name.rstrip("/")
    return name in tree or any(p.startswith(name + "/") for p in tree)


def path_at(key: str, rev: str, top_: Path | None = None, slug=None) -> str:
    """The path setting ``key`` named at ``rev``, repository-relative.

    Read from ``rev``'s own configuration; where that cannot be read, today's
    value placed through the map of moved paths; where neither can, or the
    configuration of ``rev`` sets no such key, `Unplaced`. ``slug`` fills a
    `{slug}` pattern, such as `manuscript.unit_spec`."""
    top_ = top_ or top()
    sha, prefix = _resolve(top_, rev), _prefix(top_)
    values, _ = _settings(top_, sha, prefix)
    if values is not None:
        found = _value(values, key)
        if not isinstance(found, str):
            raise Unplaced(f"{rev}'s configuration sets no {key}")
        return _join(prefix, found.replace("{slug}", slug or "{slug}"))
    today = current_project().get(key)
    if not isinstance(today, str):
        raise Unplaced(f"{rev}'s configuration cannot be read, and {key} is unset")
    return _back_to(top_, sha, prefix, today.replace("{slug}", slug or "{slug}"), key)


def _pairs(today: dict, then: dict, stem=""):
    """(today's path, then's path) for every path setting both name."""
    for key, value in today.items():
        other = then.get(key) if isinstance(then, Mapping) else None
        if isinstance(value, Mapping):
            yield from _pairs(value, other or {}, f"{stem}{key}.")
        elif isinstance(value, str) and isinstance(other, str):
            if "{" not in value:
                yield value, other
            elif value.partition("{")[2] == other.partition("{")[2]:
                # A pattern such as `notes/{slug}/marks.md` moved by its
                # fixed part: pair the parts before the placeholder.
                yield value.partition("{")[0], other.partition("{")[0]
        elif (
            isinstance(value, (list, tuple))
            and isinstance(other, (list, tuple))
            and len(value) == len(other)
            and all(isinstance(v, str) for v in (*value, *other))
        ):
            yield from zip(value, other)


def file_at(rel: str, rev: str, top_: Path | None = None) -> str:
    """Where today's file ``rel`` (project-relative) stood at ``rev``,
    repository-relative.

    Through ``rev``'s own configuration: the longest path setting that holds
    ``rel`` today is swapped for what that setting named at ``rev``, and a
    file under no setting keeps its path. Where that path is not at ``rev``,
    the map of moved paths is asked, and the configured answer stands if it
    cannot say, since a file created after ``rev`` was simply not there.
    Where ``rev``'s configuration cannot be read, the map alone answers, and
    a file it cannot place is read as one ``rev`` did not have yet, with a
    note on stderr naming it, since a move the map does not record would
    read the same way. That guess is made only for one file: a whole
    register read that way is what `spec_at` and `settings_for` refuse."""
    top_ = top_ or top()
    sha, prefix = _resolve(top_, rev), _prefix(top_)
    values, why = _settings(top_, sha, prefix)
    if values is None:
        # One file, not a whole register: one the map cannot place is read
        # as not yet made, as a configured answer the revision lacks is, and
        # the guess is said once, since a move the map does not record would
        # read the same way.
        try:
            return _back_to(top_, sha, prefix, rel, "the file")
        except Unplaced:
            if (rel, sha) not in _GUESSED:
                _GUESSED.add((rel, sha))
                print(
                    f"note: {rel} is read as a file {sha[:8]} did not have yet: "
                    f"{sha[:8]}'s configuration cannot be read ({why}), and no "
                    f"moved path places it (manuscript.chapter_policy, moved_paths)",
                    file=sys.stderr,
                )
            return _join(prefix, rel)
    today = {
        k: v for k, v in current_project().settings.items() if k != "extensions"
    }  # an extension's settings are its own, and may name anything
    best = None
    for now, then in _pairs(today, values):
        now, then = now.rstrip("/"), then.rstrip("/")
        if (rel == now or rel.startswith(now + "/")) and (
            best is None or len(now) > len(best[0])
        ):
            best = (now, then)
    placed = best[1] + rel[len(best[0]) :] if best else rel
    if _held(_tree(top_, sha), _join(prefix, placed)):
        return _join(prefix, placed)
    try:
        return _back_to(top_, sha, prefix, rel, "the file")
    except Unplaced:
        return _join(prefix, placed)


def path_today(rel: str, rev: str, top_: Path | None = None) -> str:
    """Today's name for ``rel``, a project-relative path as ``rev`` named it:
    the inverse of `file_at`, for a tool comparing what it found at ``rev``
    with what it finds now. A path under no setting that moved keeps its
    name."""
    top_ = top_ or top()
    sha, prefix = _resolve(top_, rev), _prefix(top_)
    values, _ = _settings(top_, sha, prefix)
    pairs = []
    if values is not None:
        today = {
            k: v for k, v in current_project().settings.items() if k != "extensions"
        }
        pairs = [
            (now.rstrip("/"), then.rstrip("/")) for now, then in _pairs(today, values)
        ]
    else:
        pairs = [
            (new.rstrip("/"), old.rstrip("/")) for old, new in moved_paths().items()
        ]
    best = None
    for now, then in pairs:
        if (rel == then or rel.startswith(then + "/")) and (
            best is None or len(then) > len(best[1])
        ):
            best = (now, then)
    return best[0] + rel[len(best[1]) :] if best else rel


def _configured(top_, rev, prefix) -> tuple[tuple | None, str]:
    """The spec files ``rev``'s own configuration names, or (None, why)."""
    values, why = _settings(top_, rev, prefix)
    if values is None:
        return None, why
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
        if new.endswith("/") and path == new.rstrip("/"):
            found = old.rstrip("/")  # the directory itself
        elif new.endswith("/"):
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


def _mapped(top_, rev, prefix, only=None) -> tuple:
    """Today's spec files placed at ``rev`` through the map of moved paths:
    every one, or the ones in ``only``."""
    root = current_project().root.resolve()
    out = []
    for path in notes.spec_files() if only is None else only:
        today = path.resolve().relative_to(root).as_posix()
        name = (
            _back_to(top_, rev, prefix, today, "the spec file"),
            notes.spec_owner(path),
        )
        if name not in out:
            out.append(name)
    return tuple(out)


def spec_at(rev: str, top_: Path | None = None, only=None) -> Spec:
    """Where ``rev`` kept its spec: through its own configuration, else the
    map of moved paths, else `Unplaced`.

    ``only``, a list of today's spec files, narrows what the map must place
    to the files a caller needs; a revision's own configuration always
    answers for all of them. A spec file created after ``rev`` cannot be
    told from one that moved where the map does not say, so the whole spec
    of such a revision is unplaced, while one section in an older file is
    still found."""
    top_ = top_ or top()
    sha = _resolve(top_, rev)
    prefix = _prefix(top_)
    files, why = _configured(top_, sha, prefix)
    if files is not None:
        return Spec(files, "configuration")
    return Spec(_mapped(top_, sha, prefix, only), "moved paths", why)


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
    # Only the file that holds the section today needs placing: a spec file
    # created later has nothing to say about where this section was.
    today = notes.spec_file_of(slug)
    only = [today] if today is not None else None
    for name, owner in spec_at(rev, top_, only).files:
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
