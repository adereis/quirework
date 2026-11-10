"""Which spec sections a range of commits changed, and which commit changed each.

A section is the unit everything else attaches to: bindings, the trail, the
review that asks "what did this round change?". Git answers in lines and
files, so a reader who needs the sections reads the diff and maps it by hand.
This reads every spec file at both ends of the range and compares the
sections themselves:

    quirework spec diff v1..HEAD
    quirework spec diff v1..HEAD --by-commit

A section is compared as the trail's reminder compares it, with its comments
dropped and its whitespace collapsed, so a rewrap or a mark changes nothing,
and with no regard to its file, so a section moved unchanged is the same
section. A change that touches only ``#slug`` pointers, as renaming another
section does, is reported apart from a change to what the section says,
because the two call for different follow-up: the second is a change to a
commitment, the first is bookkeeping.

Each end is read through the configuration it was committed with, so a spec
file that moved, or split into several, is found where that revision kept it,
and its sections compare as the same sections (`manuscript.history`). Where a
revision's configuration cannot be read, the project's map of moved paths
places its spec files, and the comparison says so; where neither does, it
stops and names the file rather than report every section as new.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass

from quirework.manuscript import history, notes

# Pointers to sections, as the spec writes them in its prose.
_POINTER = re.compile(r"#[a-z0-9][a-z0-9/_-]*")

STATES = {
    "new": "added",
    "changed": "what it says changed",
    "pointers": "only its #slug pointers changed",
    "gone": "removed",
}


@dataclass(frozen=True)
class Change:
    state: str
    slug: str
    title: str


@dataclass(frozen=True)
class Commit:
    sha: str
    day: str
    subject: str
    changes: list


def _git(top, *args):
    return subprocess.run(
        ["git", "-C", str(top), *args], capture_output=True, text=True, check=True
    ).stdout


def _read(top, ref, said):
    """``{slug: (title, body)}`` at ``ref``, bodies normalized for comparison.

    A revision read through the map of moved paths, rather than its own
    configuration, is noted in ``said`` once, so the reader knows how its
    files were placed."""
    try:
        spec = history.spec_at(ref, top)
    except LookupError:
        if ref.endswith("^"):
            return {}  # a root commit has no parent: nothing came before it
        raise
    if spec.how != "configuration" and ref not in said:
        said[ref] = f"note: {ref} was read through the moved paths, because {spec.why}"
    return {
        slug: (title, " ".join(notes._COMMENT.sub(" ", body).split()))
        for slug, (title, body) in history.sections_at(ref, top, spec).items()
    }


def compare(before, after):
    """The changes between two ``{slug: (title, body)}`` readings, in spec order."""
    changes = []
    for slug, (title, body) in after.items():
        if slug not in before:
            changes.append(Change("new", slug, title))
        elif before[slug][1] != body:
            old = _POINTER.sub("#", before[slug][1])
            state = "pointers" if old == _POINTER.sub("#", body) else "changed"
            changes.append(Change(state, slug, title))
    changes += [
        Change("gone", slug, title)
        for slug, (title, _) in before.items()
        if slug not in after
    ]
    return changes


def _range(spec):
    base, sep, head = spec.partition("..")
    if not base:
        raise ValueError(f"'{spec}' names no starting commit: give A..B or A")
    return base, (head or "HEAD") if sep else "HEAD"


def diff(spec, said=None):
    """The sections changed between the two ends of ``spec`` (``A..B`` or ``A``).

    ``said`` collects a note for each end read through the moved paths."""
    base, head = _range(spec)
    top, said = history.top(), {} if said is None else said
    return compare(_read(top, base, said), _read(top, head, said))


def _paths(top, base, head):
    """Every path a spec file was kept at in the range: the files at each end
    and both sides of every moved path, so the log misses no commit that
    changed a section while it lived somewhere in between."""
    prefix = history._prefix(top)
    names = {n for ref in (base, head) for n, _ in history.spec_at(ref, top).files}
    for old, new in history.moved_paths().items():
        names |= {history._join(prefix, old), history._join(prefix, new)}
    return sorted(names)


def by_commit(spec, said=None):
    """Each non-merge commit in the range that changed a section, oldest first.

    A commit is read against its first parent, each through its own
    configuration. A merge is left out, because what it brings in is already
    listed under the commits it merged.
    """
    base, head = _range(spec)
    top, said = history.top(), {} if said is None else said
    log = _git(
        top,
        "log",
        "--reverse",
        "--no-merges",
        "--format=%H%x1f%as%x1f%s",
        f"{base}..{head}",
        "--",
        *_paths(top, base, head),
    )
    out = []
    for line in log.splitlines():
        sha, day, subject = line.split("\x1f", 2)
        changes = compare(_read(top, f"{sha}^", said), _read(top, sha, said))
        if changes:
            out.append(Commit(sha, day, subject, changes))
    return out


def _row(change):
    return f"  {change.state:<9} #{change.slug} — {change.title}"


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="quirework spec diff",
        description="List the spec sections a range of commits changed: added, "
        "changed in what they say, changed only in their #slug pointers, or "
        "removed.",
        epilog="states:\n" + "\n".join(f"  {k:<9} {v}" for k, v in STATES.items()),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("range", help="A..B, or A for A..HEAD")
    ap.add_argument(
        "--by-commit",
        action="store_true",
        help="each commit that changed a section, with the sections it changed",
    )
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    said = {}
    try:
        if args.by_commit:
            commits = by_commit(args.range, said)
            lines = []
            for commit in commits:
                lines.append(f"{commit.sha[:8]} {commit.day} {commit.subject}")
                lines += [_row(change) for change in commit.changes]
            if not commits:
                lines = [f"no commit in {args.range} changed a spec section"]
        else:
            changes = diff(args.range, said)
            lines = [_row(change) for change in changes] or [
                f"no spec section changed in {args.range}"
            ]
    except (LookupError, ValueError) as exc:
        print(f"spec diff: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"spec diff: git refused: {exc.stderr.strip()}", file=sys.stderr)
        return 2
    for note in said.values():
        print(note, file=sys.stderr)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
