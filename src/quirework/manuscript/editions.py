"""Derived editions as the spec layer sees them: more units that answer to the spec.

An edition is a directory under `publish.variants`, one per kind. Its
documents sit at its top level, and each may keep a marks sidecar,
`_marks/STEM.md` beside it, as a chapter does. A sidecar's `@bind` marks
name the spec sections its document carries, and its `@omit` marks name
the ones the edition leaves out and why. So one commitment can be followed
from the chapter that states it to every edition that retells it.

An edition's unit is named `KIND/STEM`. The poems' `otto` is then never
taken for the canon's chapter `otto`, and nothing that maps the canon's
chapters to the spec, such as a trail or a backfill menu, hears of it.

This is layout only. The publisher renders an edition from the same
directories, and imports this module for where they are; the spec layer
never imports the publisher.
"""

from __future__ import annotations

from pathlib import Path

from quirework.manuscript import quarto
from quirework.manuscript.quarto import BOOK_CONFIG, MARKS_DIR
from quirework.project import current_project

# Every way an edition document can be written, in the order a sidecar's
# document is looked for.
_SUFFIXES = (".qmd", ".md")


def root() -> Path | None:
    """The configured editions root, or None for a project with no editions."""
    if current_project().get("publish.variants") is None:
        return None
    return current_project().path("publish.variants")


def kinds() -> list[str]:
    """Every edition directory, composed or not, by kind, sorted."""
    base = root()
    if base is None or not base.is_dir():
        return []
    return sorted(
        folder.name
        for folder in base.iterdir()
        if folder.is_dir() and not folder.name.startswith(("_", "."))
    )


def composed() -> list[str]:
    """Every edition that has a Quarto file of its own, by kind, sorted."""
    return [kind for kind in kinds() if (root() / kind / BOOK_CONFIG).is_file()]


def documents(kind: str) -> list[Path]:
    """The documents a composed edition renders, as they are on disk.

    Raises ValueError when its Quarto file is not a shape the parser reads.
    """
    folder = root() / kind
    return [
        folder / rel for rel in quarto.documents(folder) if (folder / rel).is_file()
    ]


def sidecars() -> list[Path]:
    """Every edition's marks sidecars, by kind, then by name."""
    base = root()
    return [
        path
        for kind in kinds()
        for path in sorted((base / kind / MARKS_DIR).glob("*.md"))
        if path.is_file()
    ]


def _kind_and_stem(path: Path) -> tuple[str, str] | None:
    """(kind, stem) for an edition's document or sidecar, else None."""
    base = root()
    if base is None:
        return None
    path = Path(path)
    if not path.is_absolute():
        path = current_project().root / path
    try:
        parts = path.resolve().relative_to(base).parts
    except ValueError:
        return None
    if len(parts) == 2 and Path(parts[1]).suffix in _SUFFIXES:
        return parts[0], Path(parts[1]).stem
    if len(parts) == 3 and parts[1] == MARKS_DIR and parts[2].endswith(".md"):
        return parts[0], Path(parts[2]).stem
    return None


def unit_of(path: Path | str) -> str | None:
    """`KIND/STEM` for an edition's document or sidecar, else None."""
    found = _kind_and_stem(Path(path))
    return f"{found[0]}/{found[1]}" if found else None


def is_sidecar(path: Path) -> bool:
    return unit_of(path) is not None and Path(path).parent.name == MARKS_DIR


def prose_for(sidecar: Path) -> Path:
    """The document an edition sidecar describes, or where it would be."""
    sidecar = Path(sidecar)
    candidates = [sidecar.parent.parent / f"{sidecar.stem}{s}" for s in _SUFFIXES]
    return next((c for c in candidates if c.is_file()), candidates[0])
