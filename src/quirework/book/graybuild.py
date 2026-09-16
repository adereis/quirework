"""Neutral SVG grayscale conversion with explicit project transforms."""

import re
import xml.etree.ElementTree as ET
from quirework.project import current_project, extension

_HEX = re.compile(r"#([0-9a-fA-F]{6})")


def _neutral(match):
    value = match.group(1)
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    gray = round(0.299 * r + 0.587 * g + 0.114 * b)
    return f"#{gray:02x}{gray:02x}{gray:02x}"


def to_gray_svg(text, name=""):
    ET.fromstring(text)
    configured = current_project().get("book.grayscale_transform")
    return extension(configured)(text, name) if configured else _HEX.sub(_neutral, text)


def non_gray_hexes(text):
    return {
        "#" + m.group(1).lower()
        for m in _HEX.finditer(text)
        if len({m.group(1)[i : i + 2].lower() for i in (0, 2, 4)}) != 1
    }


def gray_text_for(name):
    from . import figures

    path = figures.resolve(name)
    return to_gray_svg(path.read_text(), name) if path.exists() else None
