"""Reader-facing prose rules for Chapter and Verse Control.

Quirework has no opinion about what a book's prose says; these two
rules are this book's own, both stated in its spec
(manuscript/sections/_spec.md): the punctuation treaty
(#punctuation-treaty) and the choice of "quirk" over "commitment"
(#quirks-not-commitments). Marks and guides are a different register
and are out of scope here; this checks only the chapters a reader
actually reads.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

EM_DASH = "—"
_COMMITMENT = re.compile(r"\bcommitments?\b", re.IGNORECASE)

# The two chapters allowed to say "commitment": the scene where Mark
# rejects the word for one, and the callback where he calls the same
# file "the file of commitments he refuses to call commitments." Both
# uses are about the rejection itself. Anywhere else, uncommented on,
# the book's own word is "quirk".
_EXEMPT = {"the-book-that-disagreed-with-itself.qmd", "the-sidecar.qmd"}


def _chapter_files() -> list[tuple[str, Path]]:
    from quirework.book.chapters import registry
    from quirework.project import current_project

    root = current_project().root
    return [(chapter.rel, root / chapter.rel) for chapter in registry()]


def check_prose() -> list[str]:
    problems: list[str] = []
    for rel, path in _chapter_files():
        text = path.read_text(encoding="utf-8")
        exempt = Path(rel).name in _EXEMPT
        for n, line in enumerate(text.splitlines(), 1):
            if EM_DASH in line:
                problems.append(f"{rel}:{n}: em-dash in reader-facing prose")
            if not exempt and _COMMITMENT.search(line):
                problems.append(
                    f"{rel}:{n}: says 'commitment'; this book's word is 'quirk'"
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="quirework dev check")
    ap.add_argument(
        "--staged",
        action="store_true",
        help="ignored: the whole reader-facing prose set is always in scope",
    )
    ap.parse_args(argv)
    problems = check_prose()
    for p in problems:
        print(p)
    print(
        f"prose checks: {'failed' if problems else 'passed'} ({len(problems)} problem(s))"
    )
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
