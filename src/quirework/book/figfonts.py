"""Carry the figures' typeface inside each staged SVG.

A page that shows a figure as an image (``<img src="NAME.svg">``, which is
how Quarto embeds one in HTML and EPUB) has the browser draw that SVG in
isolation. The SVG may use fonts installed on the reader's device and
nothing else: the page's own web fonts never reach it. A figure lettered
in the book's face is therefore drawn in that face only on a machine that
happens to have it installed, and everywhere else in a fallback of
different widths, which is where a hand-placed label runs out of its box.
Nothing fails when that happens, and it looks right on the author's
machine, because the author has the face.

A font the SVG carries itself does reach it. ``book.figure_fonts`` names
font files (any format fontTools reads, WOFF2 included). At build time
each staged figure gets one ``@font-face`` rule per face it could ask for,
holding a subset of only the characters that figure letters, as a data
URL. The tracked sources are never touched: this runs on the staged copy,
after any grayscale transform, so the transform never sees a data URL.

Leaving the setting unset embeds nothing, and a book that sets it needs
fontTools (with Brotli for WOFF2 output) at build and check time. A
character a configured face cannot draw is an error rather than a warning,
because the reader would get that one glyph from a fallback face.
"""

from __future__ import annotations

import base64
import html
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from quirework.project import ConfigurationError, current_project

SETTING = "book.figure_fonts"

# What a figure has to say before a face is worth embedding. A face that
# nothing in the file could ask for is left out, and the tests are
# deliberately loose: a word in a comment can only add a face, never lose
# one, and a face left out when it was wanted would be synthesized by the
# browser rather than drawn.
_ITALIC = re.compile(r"\b(italic|oblique)\b", re.I)
_BOLD = re.compile(r"\bbold(er)?\b|font-weight\s*[:=]\s*[\"']?[6-9]00\b", re.I)
# A family list as an attribute (``font-family="A,'B C',serif"``, either
# quote) or as a CSS declaration in a style block or style attribute.
_FAMILY = re.compile(
    r"font-family\s*=\s*\"([^\"]*)\"|font-family\s*=\s*'([^']*)'"
    r"|font-family\s*:\s*([^;\"}>]*)",
    re.I,
)
# Case changes and optional glyph sets change which glyphs the text needs,
# so a figure that uses them gets both cases and every layout feature.
_CASE = re.compile(r"text-transform", re.I)
_FEATURES = re.compile(r"font-variant|font-feature-settings", re.I)
_SVG_OPEN = re.compile(r"<!--.*?-->|<svg\b[^>]*>", re.S)


class FigureFontError(RuntimeError):
    """A configured face cannot letter a figure, or cannot be read at all."""


@dataclass(frozen=True)
class Face:
    path: Path
    family: str
    weight: int
    italic: bool


def configured() -> tuple[Face, ...]:
    """The faces the project asks figures to carry; empty when unset."""
    project = current_project()
    value = project.get(SETTING)
    if not value:
        return ()
    if isinstance(value, str):
        value = [value]
    base = (
        Path(project.origins[SETTING]).parent
        if SETTING in project.origins
        else project.root
    )
    faces = []
    for entry in value:
        path = Path(entry).expanduser()
        path = path if path.is_absolute() else (base / path).resolve()
        if not path.is_file():
            raise ConfigurationError(f"{SETTING}: no font file at {path}")
        faces.append(_describe(str(path)))
    return tuple(faces)


def _fonttools():
    try:
        from fontTools import subset
        from fontTools.ttLib import TTFont
    except ImportError as exc:  # pragma: no cover - depends on the machine
        raise FigureFontError(
            f"{SETTING} is set, but fontTools is not installed "
            "(pip install 'fonttools[woff]'; Fedora: python3-fonttools "
            "python3-brotli)"
        ) from exc
    return subset, TTFont


@lru_cache(maxsize=None)
def _describe(path: str) -> Face:
    """Family, weight and slope, read from the font itself."""
    _, TTFont = _fonttools()
    try:
        font = TTFont(path)
    except Exception as exc:
        raise FigureFontError(f"{SETTING}: cannot read {path} ({exc})") from exc
    name = font["name"]
    family = name.getDebugName(16) or name.getDebugName(1)
    if not family:
        raise FigureFontError(f"{SETTING}: {path} names no family")
    os2 = font["OS/2"]
    italic = bool(os2.fsSelection & 1) or bool(font["head"].macStyle & 2)
    return Face(Path(path), family, int(os2.usWeightClass), italic)


@lru_cache(maxsize=None)
def _raw(path: str) -> bytes:
    return Path(path).read_bytes()


@lru_cache(maxsize=None)
def _cmap(path: str) -> frozenset[int]:
    _, TTFont = _fonttools()
    return frozenset(TTFont(io.BytesIO(_raw(path))).getBestCmap())


def _families(svg: str) -> set[str]:
    named: set[str] = set()
    for groups in _FAMILY.findall(svg):
        value = html.unescape("".join(groups))
        for part in value.split(","):
            part = part.strip().strip("'\"").strip()
            if part:
                named.add(part.casefold())
    return named


def lettered(svg: str) -> frozenset[str]:
    """Every character a figure's text elements draw, plus the space."""
    root = ET.fromstring(svg)
    chars = {" "}
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "text":
            chars.update(c for c in "".join(element.itertext()) if ord(c) >= 32)
    if _CASE.search(svg):
        chars |= {c.upper() for c in chars} | {c.lower() for c in chars}
    return frozenset(chars)


def wanted(svg: str, faces: tuple[Face, ...]) -> tuple[Face, ...]:
    """The configured faces this figure could ask a renderer for."""
    families = _families(svg)
    italic, bold = bool(_ITALIC.search(svg)), bool(_BOLD.search(svg))
    return tuple(
        face
        for face in faces
        if face.family.casefold() in families
        and (italic or not face.italic)
        and (bold or face.weight < 600)
    )


@lru_cache(maxsize=None)
def _subset(path: str, chars: frozenset[str], all_features: bool) -> str:
    subset, TTFont = _fonttools()
    font = TTFont(io.BytesIO(_raw(path)))
    options = subset.Options()
    options.flavor = "woff2"
    options.hinting = False
    options.desubroutinize = True
    if all_features:
        options.layout_features = ["*"]
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=[ord(c) for c in chars])
    subsetter.subset(font)
    out = io.BytesIO()
    try:
        font.save(out)
    except ImportError as exc:  # pragma: no cover - depends on the machine
        raise FigureFontError(
            f"{SETTING}: writing WOFF2 needs Brotli "
            "(pip install brotli; Fedora: python3-brotli)"
        ) from exc
    return base64.b64encode(out.getvalue()).decode("ascii")


def uncovered(svg: str, faces: tuple[Face, ...]) -> list[str]:
    """``face: chars`` for each wanted face missing a character the figure letters."""
    chars = lettered(svg)
    out = []
    for face in wanted(svg, faces):
        cmap = _cmap(str(face.path))
        missing = sorted(c for c in chars if ord(c) not in cmap)
        if missing:
            out.append(f"{face.path.name} lacks {''.join(missing)!r}")
    return out


def embed(svg: str, name: str, faces: tuple[Face, ...]) -> str:
    """The figure with its faces carried inside it; unchanged when none apply."""
    chosen = wanted(svg, faces)
    if not chosen:
        return svg
    gaps = uncovered(svg, faces)
    if gaps:
        raise FigureFontError(f"{name}: {'; '.join(gaps)}")
    chars = lettered(svg)
    all_features = bool(_FEATURES.search(svg))
    rules = "".join(
        "@font-face{"
        f'font-family:"{face.family}";'
        f"font-style:{'italic' if face.italic else 'normal'};"
        f"font-weight:{face.weight};"
        f"src:url(data:font/woff2;base64,{_subset(str(face.path), chars, all_features)})"
        ' format("woff2")}'
        for face in chosen
    )
    opening = next(
        (m for m in _SVG_OPEN.finditer(svg) if m.group(0).startswith("<svg")), None
    )
    if opening is None:
        raise FigureFontError(f"{name}: no <svg> element to carry the fonts")
    return f"{svg[: opening.end()]}<style>{rules}</style>{svg[opening.end() :]}"
