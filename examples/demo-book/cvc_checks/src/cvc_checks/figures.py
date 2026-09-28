"""STYLE.md's numeric constraints, checked against the figure sources.

STYLE.md (manuscript/sections/illustrations/STYLE.md) states the
palette, the stroke widths and the type size as exact numbers on
purpose: "it exists to be checked, not admired." This is that check.
Wired in as `publish.figure_checks`, so `quirework publish figures check`
runs it alongside the built-in embed/grayscale gates.
"""

from __future__ import annotations

import re

PALETTE = {"#f7f4ee", "#1a1a1a", "#2a6f6f", "#b5482f"}
STROKE_WIDTHS = {"10", "6"}
FONT_SIZE = "22"
FONT_FAMILY = "Liberation Serif"

_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
_STROKE_WIDTH = re.compile(r'stroke-width="([^"]*)"')
_FONT_SIZE = re.compile(r'font-size="([^"]*)"')
_FONT_FAMILY = re.compile(r'font-family="([^"]*)"')


def check_style() -> list[str]:
    from quirework.project import current_project

    illustrations = current_project().path("publish.figures")
    problems: list[str] = []
    for svg in sorted(illustrations.glob("*.svg")):
        text = svg.read_text(encoding="utf-8")
        rel = svg.name
        for m in _HEX.finditer(text):
            found = m.group(0).lower()
            if found not in PALETTE:
                problems.append(f"{rel}: color {found} is not one of STYLE.md's four")
        for m in _STROKE_WIDTH.finditer(text):
            if m.group(1) not in STROKE_WIDTHS:
                problems.append(
                    f"{rel}: stroke-width {m.group(1)!r} is neither STYLE.md's 10 nor 6"
                )
        for m in _FONT_SIZE.finditer(text):
            if m.group(1) != FONT_SIZE:
                problems.append(
                    f"{rel}: font-size {m.group(1)!r} is not STYLE.md's one size, {FONT_SIZE}"
                )
        for m in _FONT_FAMILY.finditer(text):
            if m.group(1) != FONT_FAMILY:
                problems.append(
                    f"{rel}: font-family {m.group(1)!r} is not STYLE.md's {FONT_FAMILY!r}"
                )
    return problems
