"""Path globs over project-relative POSIX paths.

One dialect for every setting that names files by pattern (`text.modes`, a
work's footprint), so a pattern means the same thing wherever it is written.
`**/` matches any number of directories, including none, and a trailing `**`
matches everything below; `*` and `?` stay inside one path segment. `fnmatch`
lets `*` cross a slash, which would make `content/*.md` claim every file
under `content/`.
"""

from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=256)
def regex(pattern: str) -> re.Pattern:
    """A path glob as a compiled regex that must match the whole path."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches(path: str, pattern: str) -> bool:
    return regex(pattern).match(path) is not None


def any_match(path: str, patterns) -> bool:
    return any(matches(path, p) for p in patterns)
