"""`quirework publish build`: render the manuscript through its backend."""

from __future__ import annotations

from quirework.publish import backends


def main(argv: list[str] | None = None) -> int:
    if backends.selected() == "hugo":
        from quirework.publish import hugo

        return hugo.main(argv)
    from quirework.publish import render

    return render.main(argv)
