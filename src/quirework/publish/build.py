"""`quirework publish build`: render the manuscript through its backend."""

from __future__ import annotations

from quirework.publish import backends


def main(argv: list[str] | None = None) -> int:
    return backends.selected().build(argv)
