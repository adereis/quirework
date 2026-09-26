"""Read part of a procedure: its outline, named sections, or a declared mode.

A long procedure is usually read for one of the things it covers, and the
procedure itself says which sections that needs. Reading it whole on every
run costs the reader all of it; slicing it by hand (a heading grep, then a
line range) works but leaves no record that the read happened. These
helpers do the slicing, so `sop show` can print exactly that part, through
the command that counts the read.

A section is a Markdown heading through the line before the next heading at
its own level or above, so it carries its subsections. A procedure declares
what a kind of request needs in a marker, one per mode:

    <!-- @mode per-edit: Choosing a mode; Reviewing one edit -->

The mode's reading is the procedure's opening (everything before its second
heading, where it states its goal and scope) followed by those sections.

Stdlib only, like the rest of the SOP layer, so repository hooks can run it
without an installed environment.
"""

from __future__ import annotations

import re

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_ATTRIBUTES = re.compile(r"\s*\{[^{}]*\}\s*$")
_MODE = re.compile(r"<!--\s*@mode\b(.*?)-->", re.S)
_MODE_NAME = re.compile(r"[a-z][a-z0-9-]*")
PATH_SEPARATOR = " > "


def _key(title: str) -> str:
    """How a title is matched: case, spacing, code ticks and emphasis ignored."""
    return " ".join(re.sub(r"[`*]", "", title).split()).casefold()


def _comment_open(line: str, open_: bool) -> bool:
    """Whether an HTML comment is still open after this line."""
    for token in re.findall(r"<!--|-->", line):
        open_ = token == "<!--"
    return open_


def headings(text: str) -> list[dict]:
    """Every heading, in order, with its level, title, path and line range.

    A `#` line inside a fenced code block or a multi-line HTML comment is not
    a heading; both are where a procedure quotes Markdown it is describing.
    """
    lines = text.splitlines(keepends=True)
    found, fence, comment = [], None, False
    for number, line in enumerate(lines):
        if fence:
            if line.lstrip().startswith(fence):
                fence = None
            continue
        if comment:
            comment = _comment_open(line, comment)
            continue
        opening = _FENCE.match(line)
        if opening:
            fence = opening.group(1)
            continue
        match = _HEADING.match(line)
        if match:
            title = _ATTRIBUTES.sub("", match.group(2)).strip()
            found.append(
                {"level": len(match.group(1)), "title": title, "start": number}
            )
        comment = _comment_open(line, comment)
    stack = []
    for index, item in enumerate(found):
        while stack and stack[-1]["level"] >= item["level"]:
            stack.pop()
        item["path"] = [s["title"] for s in stack] + [item["title"]]
        stack.append(item)
        item["end"] = next(
            (h["start"] for h in found[index + 1 :] if h["level"] <= item["level"]),
            len(lines),
        )
    return found


def outline(text: str) -> str:
    """The heading tree, indented by level, then any declared modes."""
    tree = headings(text)
    top = min((h["level"] for h in tree), default=1)
    rows = [f"{'  ' * (h['level'] - top)}{h['title']}" for h in tree]
    declared = modes(text)
    if declared:
        rows += ["", "Modes (sop show NAME --mode MODE):"]
        rows += [f"  {name}: {'; '.join(titles)}" for name, titles in declared.items()]
    return "\n".join(rows) + "\n"


def find(text: str, title: str) -> dict:
    """The one heading a title names; `Parent > Child` settles a repeated one."""
    wanted = [_key(part) for part in title.split(PATH_SEPARATOR.strip())]
    tree = headings(text)
    matches = [
        h for h in tree if [_key(t) for t in h["path"][-len(wanted) :]] == wanted
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        paths = "; ".join(PATH_SEPARATOR.join(h["path"]) for h in matches)
        raise ValueError(
            f"section {title!r} is ambiguous: {paths}. Name it by its path, "
            f"parents first, joined with {PATH_SEPARATOR.strip()!r}"
        )
    available = ", ".join(repr(h["title"]) for h in tree) or "none"
    raise ValueError(f"no section {title!r}; the headings are {available}")


def _join(text: str, spans) -> str:
    """The lines the spans cover, once each, in document order."""
    lines = text.splitlines(keepends=True)
    covered = sorted({n for start, end in spans for n in range(start, end)})
    return "".join(lines[n] for n in covered)


def select(text: str, titles) -> str:
    """The named sections, verbatim, in document order and never twice."""
    return _join(
        text, [(h["start"], h["end"]) for h in (find(text, t) for t in titles)]
    )


def modes(text: str) -> dict[str, list[str]]:
    """The modes a procedure declares, each with the section titles it needs."""
    declared = {}
    for body in _MODE.findall(text):
        name, colon, titles = body.strip().partition(":")
        name = name.strip()
        titles = [t.strip() for t in " ".join(titles.split()).split(";") if t.strip()]
        if not colon or not _MODE_NAME.fullmatch(name) or not titles:
            raise ValueError(
                f"@mode marker needs `NAME: Section; Section`, got {body.strip()!r}"
            )
        if name in declared:
            raise ValueError(f"@mode {name} is declared twice")
        declared[name] = titles
    return declared


def check_modes(text: str) -> None:
    """Every declared section resolves, so a renamed heading fails a gate."""
    for name, titles in modes(text).items():
        for title in titles:
            try:
                find(text, title)
            except ValueError as exc:
                raise ValueError(f"@mode {name}: {exc}") from None


def mode(text: str, name: str) -> str:
    """The opening and the declared sections of one mode."""
    declared = modes(text)
    if name not in declared:
        offered = ", ".join(declared) or "none"
        raise ValueError(f"no mode {name!r}; this procedure declares {offered}")
    tree = headings(text)
    opening = tree[1]["start"] if len(tree) > 1 else len(text.splitlines())
    spans = [(0, opening)]
    spans += [(h["start"], h["end"]) for h in (find(text, t) for t in declared[name])]
    return _join(text, spans)
