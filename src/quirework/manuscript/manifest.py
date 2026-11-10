"""A spec kept as a directory of section files, read through a manifest.

One file per section makes a section's history the log of one path, and lets
someone outside the project propose a change to the file itself. The order,
and the parts that group the sections for reading, then live in one place: a
manifest beside the files, named in `manuscript.spec` like any spec file.

    # spec/manifest.toml
    opening = "opening.md"

    [[parts]]
    title = "What the light is for"
    sections = ["flash", "horn"]

    [[parts]]
    title = "Who keeps it"
    sections = ["keeper"]

Each section is the file `<slug>.md` in the manifest's directory, holding one
level-2 section anchored `{#slug}`. `opening`, when given, is the text before
the first part. A spec without parts lists `sections` at the top level
instead. A part carries a title and no identifier, so it is not a file: the
manifest names it, and the primer groups sections under it.

The files joined in the manifest's order (`join`) are the spec as one file
would hold it: each file ends with one newline, a part opens with its title as
a level-1 heading, and one blank line separates every piece. So a single-file
spec split along its headings joins back to the same bytes.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from quirework.project import ConfigurationError

SUFFIX = ".toml"


@dataclass(frozen=True)
class Part:
    title: str | None  # None for a spec without parts
    sections: tuple[str, ...]


@dataclass(frozen=True)
class Manifest:
    path: Path
    opening: Path | None
    parts: tuple[Part, ...]

    @property
    def directory(self) -> Path:
        return self.path.parent

    def section_file(self, slug: str) -> Path:
        return self.directory / f"{slug}.md"

    def files(self) -> list[Path]:
        """Every file the spec is read from, in reading order."""
        out = [self.opening] if self.opening is not None else []
        return out + [self.section_file(s) for p in self.parts for s in p.sections]

    def part_of(self) -> dict[Path, str | None]:
        """Each section file, and the title of the part that holds it."""
        return {self.section_file(s): p.title for p in self.parts for s in p.sections}


def is_manifest(path: Path) -> bool:
    return Path(path).suffix == SUFFIX


def parse(path: Path, text: str) -> Manifest:
    """The manifest ``text`` says, as the file ``path`` would hold it."""
    where = str(path)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"{where}: the spec manifest does not parse: {exc}")
    unknown = set(data) - {"opening", "parts", "sections"}
    if unknown:
        raise ConfigurationError(
            f"{where}: unknown manifest keys: {', '.join(sorted(unknown))}"
        )
    if ("parts" in data) == ("sections" in data):
        raise ConfigurationError(
            f"{where}: a spec manifest lists either [[parts]], each with its "
            "sections, or top-level sections, and not both"
        )
    raw = (
        data["parts"]
        if "parts" in data
        else [{"title": None, "sections": data["sections"]}]
    )
    if not isinstance(raw, list):
        raise ConfigurationError(f"{where}: parts must be a list of tables")
    parts, seen = [], set()
    for i, part in enumerate(raw, 1):
        if not isinstance(part, dict) or set(part) - {"title", "sections"}:
            raise ConfigurationError(
                f"{where}: part {i} takes a title and its sections, and nothing else"
            )
        title = part.get("title")
        if "parts" in data and (not isinstance(title, str) or not title.strip()):
            raise ConfigurationError(f"{where}: part {i} has no title")
        sections = part.get("sections")
        if not isinstance(sections, list) or not all(
            isinstance(s, str) and s and "/" not in s for s in sections
        ):
            raise ConfigurationError(
                f"{where}: part {i}'s sections must be a list of slugs"
            )
        for slug in sections:
            if slug in seen:
                raise ConfigurationError(f"{where}: section '{slug}' is listed twice")
            seen.add(slug)
        parts.append(Part(title, tuple(sections)))
    opening = data.get("opening")
    if opening is not None and (not isinstance(opening, str) or "/" in opening):
        raise ConfigurationError(f"{where}: opening names a file beside the manifest")
    directory = Path(path).parent
    return Manifest(
        Path(path),
        directory / opening if opening else None,
        tuple(parts),
    )


def read(path: Path) -> Manifest:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"cannot read the spec manifest {path}: {exc}")
    return parse(Path(path), text)


def join(manifest: Manifest, read_text=None) -> str:
    """The spec as one file would hold it. ``read_text`` reads a file, by
    default from disk; a caller reading another revision passes its own."""
    read_text = read_text or (lambda p: Path(p).read_text(encoding="utf-8"))
    pieces = [read_text(manifest.opening)] if manifest.opening else []
    for part in manifest.parts:
        if part.title is not None:
            pieces.append(f"# {part.title}\n")
        pieces += [read_text(manifest.section_file(s)) for s in part.sections]
    return "\n".join(pieces)


def problems(manifest: Manifest, sections_of) -> list[tuple[Path, str]]:
    """What a manifest and its directory cannot be, each as the file it is
    about and what is wrong: a listed file missing, a file that is not the one
    section its name says, or a section file in the directory that the
    manifest does not list. ``sections_of`` reads a file's level-2 sections
    (`notes._sections_of`)."""
    out = []
    if manifest.opening is not None and not manifest.opening.is_file():
        out.append(
            (manifest.path, f"its opening, {manifest.opening.name}, does not exist")
        )
    listed = set()
    for part in manifest.parts:
        for slug in part.sections:
            path = manifest.section_file(slug)
            listed.add(path)
            if not path.is_file():
                out.append(
                    (manifest.path, f"lists '{slug}', and {path.name} does not exist")
                )
                continue
            anchors = [s.anchor for s in sections_of(path)]
            if anchors != [slug]:
                held = ", ".join(f"#{a}" if a else "(no anchor)" for a in anchors)
                why = (
                    f"holds {held or 'no section'}; a section file holds one "
                    f"section, anchored by its file name (#{slug})"
                )
                out.append((path, why))
    for path in sorted(manifest.directory.glob("*.md")):
        if path in listed or path == manifest.opening:
            continue
        if path.name not in ("README.md", "AGENTS.md"):
            out.append((path, "a file beside the spec manifest that it does not list"))
    return out
