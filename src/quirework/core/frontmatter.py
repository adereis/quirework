"""A page's frontmatter, in either Markdown convention.

A page opens with TOML between `+++` lines, which is Hugo's default, or YAML
between `---` lines, which is what Quarto, Jekyll and most other generators
write. The manuscript reads a few top-level scalars from it (`title`, `date`,
`draft`) and never writes it, and a reader copy made for delivery drops the
block whole, since a reader of the rendered page never sees it.

TOML is parsed with the standard library. YAML is not in it, and Quirework's
core takes no third-party dependency, so YAML is read for top-level scalar
keys only: a quoted or bare string, or a boolean. A nested map, a list or a
block scalar under a key is skipped rather than guessed at, which is enough
for the three fields the manuscript uses and wrong for nothing it reads.
"""

from __future__ import annotations

import re
import tomllib
from datetime import date, datetime

# Either block, anchored at the very start of the file. The closing fence may
# end the file. Offsets into the text are what callers mask with, so both
# patterns keep the fences inside the match.
TOML = re.compile(r"(?s)\A\+\+\+[ \t]*\n(.*?)\n\+\+\+[ \t]*(?:\n|\Z)")
YAML = re.compile(r"(?s)\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)")

_YAML_SCALAR = re.compile(r"^([A-Za-z_][\w-]*):[ \t]*(.*?)[ \t]*$")


def block(text: str) -> re.Match | None:
    """The frontmatter block opening ``text``, fences included, if any."""
    return TOML.match(text) or YAML.match(text)


def _yaml_value(raw: str):
    if not raw or raw[0] in "[{|>&*!":
        return None
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    raw = re.sub(r"\s+#.*$", "", raw)
    lowered = raw.lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    return raw


def fields(text: str) -> dict:
    """Top-level frontmatter fields; empty when the page has none.

    A TOML block that does not parse raises ``ValueError`` naming the problem:
    a page whose metadata cannot be read is not one to silently call untitled.
    """
    m = TOML.match(text)
    if m:
        try:
            return tomllib.loads(m.group(1))
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"frontmatter is not valid TOML: {exc}") from exc
    m = YAML.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if not line or line[0] in " \t#-":
            continue  # nested content, a comment, or a list item
        found = _YAML_SCALAR.match(line)
        if found:
            value = _yaml_value(found.group(2))
            if value is not None:
                out[found.group(1)] = value
    return out


def as_text(value) -> str | None:
    """A frontmatter date or string as the text a listing prints."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)
