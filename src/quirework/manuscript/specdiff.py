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

The spec files are the ones the project configures now. A unit's own spec that
the current tree no longer holds is not read at either end.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from quirework.manuscript import notes
from quirework.project import current_project

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


def _top():
    root = current_project().root
    try:
        return Path(_git(root, "rev-parse", "--show-toplevel").strip())
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LookupError("the project is not in a git repository") from exc


def _files():
    """Every spec file the project configures, shared files first."""
    docs = current_project().path("manuscript.source")
    shared = notes._configured_book_files(docs, "manuscript.spec")
    return list(dict.fromkeys([*shared, *notes.spec_files()]))


def _commit(top, ref):
    """``ref`` once git confirms it names a commit, or ``LookupError``.

    Checked first, because a spec file ``git show`` cannot read at a commit is
    a file that commit does not hold, and an unknown ref must not read as an
    empty spec.
    """
    try:
        _git(top, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    except subprocess.CalledProcessError:
        raise LookupError(f"'{ref}' names no commit in this repository") from None
    return ref


def _sections(top, ref, files):
    """``{slug: (title, body)}`` for every spec section at ``ref``, a commit."""
    out = {}
    for path in files:
        name = Path(path).resolve().relative_to(top).as_posix()
        try:
            text = _git(top, "show", f"{ref}:{name}")
        except subprocess.CalledProcessError:
            continue
        for section in notes._sections_of(Path(path), text):
            if section.slug:
                body = notes._COMMENT.sub(" ", section.body)
                out[section.slug] = (section.title, " ".join(body.split()))
    return out


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


def diff(spec):
    """The sections changed between the two ends of ``spec`` (``A..B`` or ``A``)."""
    base, head = _range(spec)
    top, files = _top(), _files()
    base, head = _commit(top, base), _commit(top, head)
    return compare(_sections(top, base, files), _sections(top, head, files))


def by_commit(spec):
    """Each non-merge commit in the range that changed a section, oldest first.

    A commit is read against its first parent. A merge is left out, because
    what it brings in is already listed under the commits it merged.
    """
    base, head = _range(spec)
    top, files = _top(), _files()
    base, head = _commit(top, base), _commit(top, head)
    names = [Path(f).resolve().relative_to(top).as_posix() for f in files]
    log = _git(
        top,
        "log",
        "--reverse",
        "--no-merges",
        "--format=%H%x1f%as%x1f%s",
        f"{base}..{head}",
        "--",
        *names,
    )
    out = []
    for line in log.splitlines():
        sha, day, subject = line.split("\x1f", 2)
        changes = compare(_sections(top, f"{sha}^", files), _sections(top, sha, files))
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
    try:
        if args.by_commit:
            commits = by_commit(args.range)
            lines = []
            for commit in commits:
                lines.append(f"{commit.sha[:8]} {commit.day} {commit.subject}")
                lines += [_row(change) for change in commit.changes]
            if not commits:
                lines = [f"no commit in {args.range} changed a spec section"]
        else:
            changes = diff(args.range)
            lines = [_row(change) for change in changes] or [
                f"no spec section changed in {args.range}"
            ]
    except (LookupError, ValueError) as exc:
        print(f"spec diff: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"spec diff: git refused: {exc.stderr.strip()}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
