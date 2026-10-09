"""The book's two inks, held against every figure source.

The book prints in black and one orange, the spreadsheet's "contradicts,
but I like it". Grays are black at a lighter weight, and white is the
paper. Nothing else reaches a figure.

The rule reads every SVG in `publish.figures`, embedded or not, and
every place a color can be written: the paint attributes, a `style`
attribute, and a `<style>` element. Black, white and any gray (red,
green and blue equal) pass, and so does the orange, exactly. Anything
else is refused, named colors included: "orange" is not the book's
orange.

A color can also arrive without being written. Opacity over the
orange, or a gradient from the orange to the paper, prints a tint of it,
and a filter can make a color from nothing, so all three are refused
where they could do that: any opacity below one, a gradient that is not
all gray or all one ink, and any filter. An embedded raster has colors
nobody can read from the source, so it is refused too.

Wired into `quirework dev check` by `prose.main`, and into
`quirework publish figures check` by `publish.figure_checks`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

RULE = "Two inks"
RULE_DESCRIPTION = "every figure is black, grays and the one orange"

ORANGE = (0xE8, 0x74, 0x0C)

PAINT = ("fill", "stroke", "stop-color", "flood-color", "lighting-color", "color")
OPACITY = ("opacity", "fill-opacity", "stroke-opacity", "stop-opacity")

# Values that paint nothing of their own: no paint, or paint resolved from
# another property this rule also reads.
_NEUTRAL = {"none", "transparent", "currentcolor", "inherit", "initial", "unset"}

_NAMED_GRAYS = {
    "black": 0x00,
    "dimgray": 0x69,
    "dimgrey": 0x69,
    "gray": 0x80,
    "grey": 0x80,
    "darkgray": 0xA9,
    "darkgrey": 0xA9,
    "silver": 0xC0,
    "lightgray": 0xD3,
    "lightgrey": 0xD3,
    "gainsboro": 0xDC,
    "whitesmoke": 0xF5,
    "white": 0xFF,
}

_DECLARATION = re.compile(r"(?<![\w-])([a-z-]+)\s*:\s*([^;{}]+)", re.IGNORECASE)
_HEX = re.compile(r"#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})", re.IGNORECASE)
_RGB = re.compile(r"rgba?\(([^)]*)\)", re.IGNORECASE)
_URL = re.compile(r"url\(\s*['\"]?#[^)]*\)", re.IGNORECASE)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _channel(text):
    text = text.strip()
    if text.endswith("%"):
        return round(float(text[:-1]) * 255 / 100)
    return round(float(text))


def _parse(value):
    """A color as (r, g, b, alpha), or None when it names no color."""
    v = value.strip().lower()
    if v in _NAMED_GRAYS:
        g = _NAMED_GRAYS[v]
        return (g, g, g, 1.0)
    m = _HEX.fullmatch(v)
    if m:
        digits = m.group(1)
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits)
        r, g, b = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
        alpha = int(digits[6:8], 16) / 255 if len(digits) == 8 else 1.0
        return (r, g, b, alpha)
    m = _RGB.fullmatch(v)
    if m:
        parts = re.split(r"[\s,/]+", m.group(1).strip())
        r, g, b = (_channel(p) for p in parts[:3])
        alpha = 1.0
        if len(parts) > 3:
            a = parts[3]
            alpha = float(a[:-1]) / 100 if a.endswith("%") else float(a)
        return (r, g, b, alpha)
    return None


def _ink(rgb):
    """Which ink a color is, or None when it is not one of the book's."""
    r, g, b = rgb
    if r == g == b:
        return "gray"
    if rgb == ORANGE:
        return "orange"
    return None


def _paint_problem(prop, value):
    v = value.strip()
    if v.lower() in _NEUTRAL or _URL.fullmatch(v):
        return None
    parsed = _parse(v)
    if parsed is None:
        return f"{prop} {v!r} is not one of the book's inks; write it in hex"
    r, g, b, alpha = parsed
    if _ink((r, g, b)) is None:
        return f"{prop} {v!r} is not black, a gray or the orange"
    if alpha < 1:
        return f"{prop} {v!r} is translucent, which prints a tint"
    return None


def _opacity_problem(prop, value):
    v = value.strip()
    try:
        amount = float(v[:-1]) / 100 if v.endswith("%") else float(v)
    except ValueError:
        return None
    if amount < 1:
        return f"{prop} {v} prints a tint"
    return None


def _declarations(element):
    """Every (property, value) an element sets, as attribute or style."""
    for prop, value in element.attrib.items():
        if prop != "style":
            yield prop, value
    for m in _DECLARATION.finditer(element.attrib.get("style", "")):
        yield m.group(1).lower(), m.group(2)
    if _local(element.tag) == "style":
        for m in _DECLARATION.finditer(element.text or ""):
            yield m.group(1).lower(), m.group(2)


def _gradient_problem(element):
    inks = set()
    for stop in element.iter():
        if _local(stop.tag) != "stop":
            continue
        for prop, value in _declarations(stop):
            if prop == "stop-color":
                parsed = _parse(value)
                if parsed:
                    inks.add(parsed[:3])
    if len(inks) > 1 and not all(_ink(rgb) == "gray" for rgb in inks):
        return "a gradient from the orange to another color prints a tint"
    return None


def figure_problems(path):
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as error:
        return [f"does not parse: {error}"]
    problems = []
    for element in root.iter():
        tag = _local(element.tag)
        if tag == "image":
            problems.append("an embedded image, whose colors cannot be checked")
        elif tag == "filter":
            problems.append("a filter, which can make a color the source never names")
        elif tag in ("linearGradient", "radialGradient"):
            problem = _gradient_problem(element)
            if problem:
                problems.append(problem)
        for prop, value in _declarations(element):
            if prop in PAINT:
                problem = _paint_problem(prop, value)
            elif prop in OPACITY:
                problem = _opacity_problem(prop, value)
            elif prop == "mix-blend-mode" and value.strip() != "normal":
                problem = f"mix-blend-mode {value.strip()} mixes the inks"
            else:
                problem = None
            if problem:
                problems.append(problem)
    return problems


def two_inks():
    from quirework.project import current_project

    project = current_project()
    if not project.get("publish.figures"):
        return []
    illustrations = project.path("publish.figures")
    problems = []
    for svg in sorted(illustrations.glob("**/*.svg")):
        rel = svg.relative_to(project.root)
        problems += [f"{rel}: {p}" for p in figure_problems(svg)]
    return problems
