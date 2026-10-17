"""Archive a review sheet's rulings as the author's own turns.

A review sheet puts a list of items in front of the author: proposed edits,
decisions, findings. The author chooses an option for each and may type a
comment beside it. Those choices and comments are the author's turns, as
much as anything said in a conversation, but the page that collected them
keeps them in its own storage, and nothing the provenance tools read holds
them. A trail could not point at a ruling, and a backfill reader could not
flag one.

``archive`` writes one round's ruling as a durable Markdown file in the
directory ``provenance.rulings`` declares, the way ``publish redpen archive``
keeps a red-pen round. ``provenance show`` then reads each file as a session
named ``rulings-`` plus its stem: one author turn per item, holding the item's
label, the option chosen and the comment, verbatim, with what the sheet
presented as the text the turn answered. The trail and its backfill reach the
rulings through ``show``, as they reach red-pen marks.

    quirework provenance rulings archive RECORD.json --name round-2
    quirework provenance show rulings-round-2
    quirework spec trail add SLUG --kind accept --session rulings-round-2 --turn 7

The record is tool-neutral JSON, written by whatever built the sheet:

    {"title": "Round two", "round": "r2", "sheet": "https://…",
     "ruled_at": "2031-05-07T14:08:57Z", "notes": "…",
     "items": [{"label": "E1", "presented": "…", "ruled": "…", "comment": "…"}]}

``label`` and ``ruled`` are required for every item, and labels are unique.
``presented``, ``comment``, ``notes``, ``sheet`` and ``round`` may be empty.
A non-empty ``notes`` becomes a last turn.

The sheet records one instant for the whole ruling, the moment it was marked
final, and none for any one item. So a round logs no time per turn, and a
breadcrumb names an item by its turn number, as it names a red-pen mark. The
round still takes its place in date order by the day of ``ruled_at``.

Every text field is kept as written, with two exceptions: line endings become
``\n``, and whitespace at the end of a line is dropped, because a project
that formats its Markdown refuses both, and a carriage return the formatter
folded would join two of the author's lines. Each field sits in a fenced block
whose fence is longer than any run of backticks in the text, so Markdown,
fences and headings inside a comment survive, and a column formatter leaves
them alone.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from quirework.project import ConfigurationError, current_project

PREFIX = "rulings-"

_RULINGS = re.compile(r"^## RULINGS\s*$", re.M)
_FENCE = re.compile(r"^(`{3,})([A-Za-z][\w-]*)?\s*$")
_ITEM = re.compile(r"^### (\S.*?)\s*$")
_SECTION = re.compile(r"^## (\S.*?)\s*$")
_META = re.compile(r"^([a-z][\w-]*):\s*(.*?)\s*$")
_FIELDS = ("presented", "ruled", "comment")
_NAME = re.compile(r"[A-Za-z0-9][\w.-]*")


@dataclass(frozen=True)
class Item:
    """One item the sheet put to the author, and the author's ruling on it."""

    label: str
    ruled: str
    presented: str = ""
    comment: str = ""

    def turn(self):
        """The author's turn: the label, the option chosen, the comment."""
        head = f"{self.label} · {self.ruled}"
        return f"{head}\n\n{self.comment}" if self.comment else head


@dataclass(frozen=True)
class Round:
    """One archived ruling: its header and its items, in the sheet's order."""

    title: str
    items: list[Item] = field(default_factory=list)
    round: str = ""
    sheet: str = ""
    ruled_at: str = ""
    notes: str = ""
    # The day the file was written: what dates a round with no ruled_at.
    archived: str = field(default="", compare=False)

    def instant(self):
        """When the ruling was marked final, aware, or ``None``."""
        if not self.ruled_at:
            return None
        try:
            moment = datetime.fromisoformat(self.ruled_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)

    def day(self):
        """The UTC day the ruling was marked final, else the day it was
        archived, or ``None``."""
        moment = self.instant()
        if moment is not None:
            return moment.astimezone(timezone.utc).date()
        try:
            return date.fromisoformat(self.archived) if self.archived else None
        except ValueError:
            return None

    def turns(self):
        """``(text, presented)`` for each author turn: the items, then notes."""
        out = [(item.turn(), item.presented) for item in self.items]
        if self.notes:
            out.append((self.notes, ""))
        return out


# --------------------------------------------------------------------------- #
# the archive directory
# --------------------------------------------------------------------------- #
def directory():
    """The rulings archive, only where the project declares one.

    The rulings are private and their author's, so they join the sessions
    only by the project's explicit ``provenance.rulings``, never by default.
    """
    project = current_project()
    if project.get("provenance.rulings") is None:
        return None
    return project.path("provenance.rulings")


def rounds():
    """Every archived round in the declared directory, by file name."""
    found = directory()
    if found is None or not found.is_dir():
        return []
    return [p for p in sorted(found.glob("*.md")) if is_round(p)]


def session_id(path):
    """A round's session id: ``rulings-`` and the archive file's stem."""
    return PREFIX + Path(path).stem


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def _text(path):
    # newline="" keeps a carriage return the author typed as typed.
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def is_round(path):
    """Whether ``path`` is an archived round rather than, say, a README."""
    try:
        return bool(_RULINGS.search(_text(path)))
    except OSError:
        return False


def _blocks(lines):
    """``(kind, value)`` for each heading and fenced block, in order.

    ``kind`` is ``section`` or ``item`` for a heading, and the fence's info
    word for a block (``meta``, ``presented``, ``ruled``, ``comment`` or
    ``notes``), whose value is its text exactly as written between the
    fences. A heading inside a block is the block's text, not a heading.
    """
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            width, info = len(fence.group(1)), fence.group(2) or ""
            closing = re.compile(rf"^`{{{width},}}\s*$")
            j = i + 1
            while j < len(lines) and not closing.match(lines[j]):
                j += 1
            if j == len(lines):
                raise ValueError(f"a fenced block opened on line {i + 1} never closes")
            out.append((info, "\n".join(lines[i + 1 : j])))
            i = j + 1
            continue
        if _SECTION.match(line):
            out.append(("section", _SECTION.match(line).group(1)))
        elif _ITEM.match(line):
            out.append(("item", _ITEM.match(line).group(1)))
        i += 1
    return out


def read(path):
    """The ``Round`` an archive file holds; ``ValueError`` if it is malformed."""
    return parse(_text(path), Path(path).name)


def parse(text, name="the archive"):
    """The ``Round`` archive text holds; ``ValueError`` if it is malformed."""
    lines = text.split("\n")
    title = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), "")
    meta, items, notes = {}, [], ""
    section, current = None, None
    for kind, value in _blocks(lines):
        if kind == "section":
            section, current = value, None
            continue
        if kind == "item":
            if section != "RULINGS":
                continue
            current = {"label": value}
            items.append(current)
            continue
        if kind == "meta" and section is None:
            for row in value.split("\n"):
                found = _META.match(row)
                if found:
                    meta[found.group(1)] = found.group(2)
        elif kind in _FIELDS and section == "RULINGS" and current is not None:
            current[kind] = value
        elif kind == "notes" and section == "NOTES":
            notes = value
    parsed = []
    for raw in items:
        if "ruled" not in raw:
            raise ValueError(f"{name}: item {raw['label']} has no ruling")
        parsed.append(
            Item(
                raw["label"],
                raw["ruled"],
                raw.get("presented", ""),
                raw.get("comment", ""),
            )
        )
    return Round(
        title,
        parsed,
        meta.get("round", ""),
        meta.get("sheet", ""),
        meta.get("ruled-at", ""),
        notes,
        meta.get("archived", ""),
    )


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #
def _fence(text):
    """A fence longer than any run of backticks in ``text``, three at least."""
    runs = [len(run) for run in re.findall(r"`+", text)]
    return "`" * max(3, max(runs, default=0) + 1)


def _block(info, text):
    fence = _fence(text)
    return f"{fence}{info}\n{text}\n{fence}"


def _plain(text):
    """``text`` with ``\n`` line endings and no whitespace ending a line."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines)


def validate(record):
    """The record as a ``Round``; ``ValueError`` naming the first problem.

    Text fields come back as ``_plain`` leaves them, which is how the archive
    keeps them.
    """
    if not isinstance(record, dict):
        raise ValueError("a rulings record is a JSON object")
    items = record.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("a rulings record needs a non-empty 'items' list")
    seen, parsed = set(), []
    for n, raw in enumerate(items, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"item {n} is not an object")
        label = raw.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"item {n} has no label")
        if label != label.strip() or "\n" in label or label.startswith("#"):
            raise ValueError(
                f"item {n}'s label {label!r} must be one line, with no "
                f"surrounding space, and must not start with '#'"
            )
        if label in seen:
            raise ValueError(f"label {label!r} appears twice")
        seen.add(label)
        ruled = raw.get("ruled")
        if not isinstance(ruled, str) or not ruled.strip():
            raise ValueError(f"item {label} records no ruling")
        for key in ("presented", "comment"):
            if not isinstance(raw.get(key, ""), str):
                raise ValueError(f"item {label}'s {key} is not text")
        parsed.append(
            Item(
                label,
                _plain(ruled),
                _plain(raw.get("presented", "")),
                _plain(raw.get("comment", "")),
            )
        )
    for key in ("title", "round", "sheet", "ruled_at", "notes"):
        if not isinstance(record.get(key, ""), str):
            raise ValueError(f"'{key}' is not text")
    for key in ("round", "sheet", "ruled_at"):
        if "\n" in record.get(key, ""):
            raise ValueError(f"'{key}' must be one line")
    title = (record.get("title") or "").strip() or "Rulings"
    if "\n" in title:
        raise ValueError("'title' must be one line")
    found = Round(
        title,
        parsed,
        record.get("round", ""),
        record.get("sheet", ""),
        record.get("ruled_at", ""),
        _plain(record.get("notes", "")),
    )
    if found.ruled_at and found.instant() is None:
        raise ValueError(f"'ruled_at' is not an ISO-8601 time: {found.ruled_at!r}")
    return found


_INTRO = (
    "Durable record of one review sheet's ruling: each item the sheet put "
    "to the author, the option the author chose, and the author's comment, "
    "verbatim. The sheet's own storage is not kept, so this file is the record."
)


def render(found, *, archived=None):
    """The archive file's text for a ``Round``."""
    archived = archived or date.today()
    meta = [
        f"round:     {found.round}",
        f"sheet:     {found.sheet}",
        f"ruled-at:  {found.ruled_at}",
        f"archived:  {archived.isoformat()}",
        f"items:     {len(found.items)}",
    ]
    parts = [
        f"# {found.title}",
        _INTRO,
        _block("meta", "\n".join(meta)),
        "## RULINGS",
    ]
    for item in found.items:
        parts.append(f"### {item.label}")
        if item.presented:
            parts.append(_block("presented", item.presented))
        parts.append(_block("ruled", item.ruled))
        if item.comment:
            parts.append(_block("comment", item.comment))
    if found.notes:
        parts += ["## NOTES", _block("notes", found.notes)]
    # The column formatter's fixed point, so a project that wraps its Markdown
    # leaves the file alone; it never touches a fenced block.
    from quirework.text import mdwrap

    return mdwrap.reflow_columns("\n\n".join(parts) + "\n")


def archive(record, name, *, out_dir=None, archived=None):
    """Write ``record`` as ``NAME.md`` in the archive; return the path.

    Refuses to overwrite: an archived ruling is the record, and a second
    ruling on the same sheet is a new round with a name of its own.
    """
    if not _NAME.fullmatch(name):
        raise ValueError(
            f"'{name}' is not a usable round name: letters, digits, '.', '_' "
            f"and '-', starting with a letter or digit"
        )
    found = validate(record)
    target_dir = out_dir if out_dir is not None else directory()
    if target_dir is None:
        raise ConfigurationError(
            "no rulings archive: declare provenance.rulings in quirework.toml "
            "(the configuration guide's 'Provenance' section)"
        )
    target = Path(target_dir) / f"{name}.md"
    if target.exists():
        raise FileExistsError(
            f"{target} already exists; an archived ruling is not rewritten. "
            f"Archive a later ruling under a new name"
        )
    text = render(found, archived=archived)
    if parse(text, target.name) != found:
        raise ValueError("the archive would not read back as written")
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "x", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return target


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="quirework provenance rulings",
        description=(
            "Keep a review sheet's rulings as the author's turns, so the "
            "trail can point at them and its backfill can read them."
        ),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    arc = sub.add_parser(
        "archive",
        help="write one round's ruling into the provenance.rulings archive",
        description=(
            "Write RECORD (JSON: title, round, sheet, ruled_at, notes, and "
            "items of label, presented, ruled and comment) as NAME.md in the "
            "directory provenance.rulings declares. It then reads as the "
            "session rulings-NAME. An existing file is never overwritten."
        ),
    )
    arc.add_argument("record", type=Path, help="the ruling as JSON; '-' reads stdin")
    arc.add_argument("--name", required=True, help="the round's file name, no suffix")
    args = ap.parse_args(argv)

    if args.cmd == "archive":
        try:
            raw = (
                sys.stdin.read()
                if str(args.record) == "-"
                else args.record.read_text(encoding="utf-8")
            )
            record = json.loads(raw)
            target = archive(record, args.name)
        except (OSError, ValueError, ConfigurationError) as exc:
            print(f"rulings: {exc}", file=sys.stderr)
            return 2
        found = read(target)
        try:
            shown = target.relative_to(current_project().root)
        except ValueError:
            shown = target
        print(
            f"rulings: archived {len(found.items)} item(s)"
            f"{' and notes' if found.notes else ''} -> {shown}  "
            f"(session {session_id(target)})"
        )
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
