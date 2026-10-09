"""Rename a spec section everywhere its slug is written.

A section's slug is its permanent name, and the spec is only one of the
places it is written. Each sidecar that binds the section names it, a
companion entry cites it, the spec links to it, the trail heads its history
with it, and the backfill ledger files its flags and rulings under it.
`spec notes --check` fails on any of those a hand rename misses, so renaming
by hand is safe; it is also tedious enough that a slug saying the opposite of
its section, after the section was reversed, stays as it is.

The rename is planned whole before anything is written: every file it would
change is read, rewritten in memory and compared, and a refusal leaves the
project as it was. Each file is then replaced atomically, keeping its
permissions, since the trail and the ledger are private.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from quirework.manuscript import editions, notes, trail, units
from quirework.project import current_project

_ANCHOR = re.compile(r"[A-Za-z][\w-]*")


@dataclass(frozen=True)
class Edit:
    path: Path
    before: str
    after: str  # "" with ``move`` set: the file moves rather than changes
    what: str
    move: Path | None = None


def _rel(path: Path) -> str:
    try:
        return (
            Path(path)
            .resolve()
            .relative_to(current_project().root.resolve())
            .as_posix()
        )
    except ValueError:
        return str(path)


def _target(old: str, new: str) -> tuple[notes.Section, str]:
    """The section ``old`` names, and the full slug ``new`` gives it."""
    sections = {s.slug: s for s in notes.spec_sections() if s.slug}
    section = sections.get(old)
    if section is None:
        close = difflib.get_close_matches(old, sorted(sections), n=3)
        hint = f" (close: {', '.join(close)})" if close else ""
        raise LookupError(f"the spec has no section '#{old}'{hint}")
    owner = notes.spec_owner(section.path)
    if owner is not None:
        # A unit's own section stays in its unit's spec: the new name may be
        # written bare or in full, never under another unit.
        if "/" in new and not new.startswith(f"{owner}/"):
            raise LookupError(
                f"'#{old}' lives in {owner}'s own spec, so its new name is "
                f"'{owner}/…' or a bare anchor, not '{new}'"
            )
        new = new if "/" in new else f"{owner}/{new}"
    elif "/" in new:
        raise LookupError(
            f"'#{old}' is a section of the shared spec, so its new name is a "
            f"bare anchor, not '{new}'"
        )
    if not _ANCHOR.fullmatch(notes.anchor_of(new)):
        raise LookupError(
            f"'{notes.anchor_of(new)}' is not an anchor: start with a letter, "
            f"then letters, digits, '-' or '_'"
        )
    if new == old:
        raise LookupError(f"'#{old}' already has that name")
    if new in sections:
        raise LookupError(f"the spec already has a section '#{new}'")
    return section, new


def _heading(text: str, anchor: str, new_anchor: str) -> tuple[str, int]:
    """``text`` with the first level-2 heading anchored ``anchor`` re-anchored."""
    token = re.compile(rf"(?<=[{{\s])#{re.escape(anchor)}(?=[\s}}])")
    pattern = re.compile(r"(?m)^##[ \t]+.*\{[^{}\n]*\}[ \t]*$")
    for m in pattern.finditer(text):
        line = m.group(0)
        if token.search(line):
            line = token.sub(f"#{new_anchor}", line, count=1)
            return text[: m.start()] + line + text[m.end() :], 1
    return text, 0


def _links(text: str, names: dict[str, str]) -> tuple[str, int]:
    """Markdown links to a section, ``](#name)``, renamed per ``names``."""
    count = 0

    def swap(m):
        nonlocal count
        count += 1
        return f"](#{names[m.group(1)]})"

    alternatives = "|".join(re.escape(n) for n in names)
    return re.sub(rf"\]\(#({alternatives})\)", swap, text), count


def _binds(text: str, unit: str | None, old: str, new: str, known: set[str]):
    """``@bind`` tokens that resolve to ``old``, written as they were written:
    in full when they were, bare when a unit bound its own section bare."""
    count = 0

    def swap(m):
        nonlocal count
        written = m.group(1).strip()
        if notes.resolve(written, unit, known) != old:
            return m.group(0)
        count += 1
        name = new if "/" in written else notes.anchor_of(new)
        return f"@bind({name})"

    return notes._BIND_TOKEN.sub(swap, text), count


def _omits(text: str, old: str, new: str):
    """An edition's ``@omit`` tokens naming ``old``, which are always in full."""
    count = 0

    def swap(m):
        nonlocal count
        if m.group(1).strip() != old:
            return m.group(0)
        count += 1
        return f"@omit({new})"

    return notes._OMIT_TOKEN.sub(swap, text), count


def _code_refs(text: str, old: str, new: str) -> tuple[str, int]:
    """Spec citations ``#slug`` inside code spans, as companion entries write them."""
    count = 0

    def span(m):
        nonlocal count

        def ref(r):
            nonlocal count
            if r.group(1) != old:
                return r.group(0)
            count += 1
            return f"#{new}"

        return notes._SPEC_REF.sub(ref, m.group(0))

    return notes._BACKTICKED.sub(span, text), count


def plan(old: str, new: str) -> tuple[str, list[Edit]]:
    """The full new slug, and every edit the rename makes; nothing is written."""
    section, new = _target(old, new)
    known = notes.spec_slugs()
    old_anchor, new_anchor = notes.anchor_of(old), notes.anchor_of(new)
    edits: list[Edit] = []
    texts: dict[Path, tuple[str, str, list[str]]] = {}

    def change(path, fn, what):
        path = Path(path)
        before, current, whats = texts.get(path) or (
            path.read_text(encoding="utf-8"),
            None,
            [],
        )
        current = before if current is None else current
        after, count = fn(current)
        if count:
            whats = whats + [f"{what} ×{count}" if count > 1 else what]
        texts[path] = (before, after, whats)

    change(section.path, lambda t: _heading(t, old_anchor, new_anchor), "heading")
    for spec in notes.spec_files():
        names = {old: new}
        if spec == section.path:
            names[old_anchor] = new_anchor
        change(spec, lambda t, n=names: _links(t, n), "link")
    for f in notes.mark_files():
        # An edition's marks name sections in full, so its unit is the
        # edition's own name, which no bare name resolves under.
        unit = editions.unit_of(f) or units.slug_of(notes._chapter_for(f))
        change(f, lambda t, u=unit: _binds(t, u, old, new, known), "@bind")
        change(f, lambda t: _omits(t, old, new), "@omit")
    for f in notes.companion_files():
        change(f, lambda t: _binds(t, None, old, new, known), "@bind")
        change(f, lambda t: _code_refs(t, old, new), "citation")
    if trail.configured() and trail.sidecar_path().exists():
        change(trail.sidecar_path(), lambda t: _heading(t, old, new), "trail heading")

    for path, (before, after, whats) in texts.items():
        if after != before:
            edits.append(Edit(path, before, after, ", ".join(whats)))
    edits += _ledger(old, new)
    return new, edits


def _ledger(old: str, new: str) -> list[Edit]:
    """The backfill ledger's flags and rulings, filed under the new name."""
    project = current_project()
    if project.get("manuscript.spec_trail_leads") is None:
        return []
    root = project.path("manuscript.spec_trail_leads")
    if not root.is_dir():
        return []
    edits: list[Edit] = []
    for path in sorted((root / "reads").glob("*/*.json")):
        before = path.read_text(encoding="utf-8")
        read = json.loads(before)
        count = 0
        for candidate in read.get("candidates") or []:
            for item in candidate.get("sections") or []:
                if item.get("slug") == old:
                    item["slug"] = new
                    count += 1
        if count:
            after = json.dumps(read, indent=2) + "\n"
            edits.append(Edit(path, before, after, f"flag ×{count}"))
    ruling = root / "rulings" / f"{old}.json"
    if ruling.exists():
        moved = root / "rulings" / f"{new}.json"
        if moved.exists():
            raise LookupError(
                f"the ledger already holds rulings for '#{new}' "
                f"({_rel(moved)}); merge them by hand"
            )
        before = ruling.read_text(encoding="utf-8")
        body = json.loads(before)
        body["slug"] = new
        edits.append(
            Edit(ruling, before, json.dumps(body, indent=2) + "\n", "rulings", moved)
        )
    return edits


def apply(edits: list[Edit]) -> None:
    """Write every edit. Each file is replaced atomically, keeping its mode."""
    sidecar = trail.sidecar_path() if trail.configured() else None
    for edit in edits:
        if edit.path == sidecar:
            with trail._locked(edit.path):
                if edit.path.read_text(encoding="utf-8") != edit.before:
                    raise RuntimeError(
                        f"{_rel(edit.path)} changed while the rename was "
                        f"planned; run it again"
                    )
                trail._write(edit.path, edit.after)
        elif edit.move is not None:
            trail._write(edit.move, edit.after)
            edit.path.unlink()
        else:
            trail._write(edit.path, edit.after)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="quirework spec rename",
        description="Rename a spec section everywhere its slug is written: the "
        "spec's heading and its links, every @bind that names the section, "
        "companion citations, the trail's heading and the backfill ledger. "
        "Nothing is written unless every change can be made.",
    )
    parser.add_argument("old", help="the section's slug now")
    parser.add_argument(
        "new", help="its new slug; a unit's own section may be named bare"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="list the changes and write nothing"
    )
    args = parser.parse_args(argv)
    old, new = args.old.lstrip("#"), args.new.lstrip("#")
    try:
        new, edits = plan(old, new)
    except LookupError as exc:
        print(f"spec rename: {exc}", file=sys.stderr)
        return 2
    for edit in edits:
        where = (
            f"{_rel(edit.path)} -> {_rel(edit.move)}" if edit.move else _rel(edit.path)
        )
        print(f"  {where}: {edit.what}")
    if args.dry_run:
        print(f"dry run: #{old} would become #{new} in {len(edits)} file(s)")
        return 0
    apply(edits)
    print(f"renamed #{old} to #{new} in {len(edits)} file(s)")
    print("next: quirework spec notes --check")
    return 0
