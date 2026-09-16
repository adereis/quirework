"""Neutral SVG grayscale conversion with explicit project transforms."""

import re
import xml.etree.ElementTree as ET
from quirework.project import current_project, extension

# Every hex form SVG accepts, not only the six-digit one. A three-digit
# `#fff` used to pass straight through the transform and straight through the
# check that exists to catch exactly that, so a color could reach the e-ink
# edition with nothing to report it. Lengths of five and seven are not colors:
# they are left alone rather than mangled.
_HEX = re.compile(r"#([0-9a-fA-F]{3,8})\b")


def _channels(value):
    """(red, green, blue, the alpha digits as written) for a hex color, or None."""
    if len(value) in (3, 4):
        return (*(int(digit * 2, 16) for digit in value[:3]), value[3:] * 2)
    if len(value) in (6, 8):
        return (*(int(value[i : i + 2], 16) for i in (0, 2, 4)), value[6:])
    return None


def _neutral(match):
    parsed = _channels(match.group(1))
    if parsed is None:
        return match.group(0)
    red, green, blue, alpha = parsed
    gray = round(0.299 * red + 0.587 * green + 0.114 * blue)
    return f"#{gray:02x}{gray:02x}{gray:02x}{alpha}"


def to_gray_svg(text, name=""):
    ET.fromstring(text)
    configured = current_project().get("book.grayscale_transform")
    return extension(configured)(text, name) if configured else _HEX.sub(_neutral, text)


def non_gray_hexes(text):
    """Every color in the text whose three channels are not all equal."""
    found = set()
    for match in _HEX.finditer(text):
        parsed = _channels(match.group(1))
        if parsed is not None and len(set(parsed[:3])) != 1:
            found.add("#" + match.group(1).lower())
    return found


def gray_text_for(name):
    from . import figures

    path = figures.resolve(name)
    return to_gray_svg(path.read_text(), name) if path.exists() else None
