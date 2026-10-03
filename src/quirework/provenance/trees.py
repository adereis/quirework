"""The directories a provenance corpus's sessions were launched in.

A session records absolute paths from the checkout it ran in. Whether an edit
was document work depends on where its path sits inside the project, so a
reader measures the path from that checkout's copy of the project directory.
The checkout may be long gone by then: a `git worktree` removed after its
sessions were captured takes the answer with it. Asking git at read time made
the catalog a function of which worktrees happen to exist today. The same
corpus counted edits differently before and after `git worktree remove`, and
in a fresh clone or in CI, so `catalog --check` failed on commits that changed
no session.

So the answer is recorded rather than looked up. `project-trees.txt`, beside
the catalog, lists every directory this project's sessions may have been
launched in, one absolute path per line. Capture runs while those checkouts
exist, so each sweep adds the live ones and never removes a line: a removed
worktree's sessions still name it. Readers consult this file alone, which makes
it part of the ledger the catalog certifies.

The entries are the *project's* directory in each checkout
(`worktrees.project_trees`), not the checkouts themselves. For a book at its
checkout's root that is the same list. For a book nested inside a larger
repository it is what makes `manuscript/` mean the book's manuscript rather
than a directory at the repository's root.

A checkout removed before its tree was first recorded can only be added by
hand. The catalog refuses to build while an accepted session was launched
outside every listed tree, and names the directories, so an incomplete list is
a failed check rather than a silently different count.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePosixPath

from quirework.provenance import worktrees

FILENAME = "project-trees.txt"

_PREAMBLE = (
    "# Every directory this project's sessions were launched in, one absolute\n"
    "# path per line. Capture adds each live checkout's copy of the project and\n"
    "# never removes one; add a checkout that was removed before then by hand.\n"
)


def location(project):
    """Where a project's list lives: beside its provenance catalog."""
    return project.path("provenance.root", ".quirework/provenance") / FILENAME


def recorded(project):
    """The listed trees, in file order; empty when nothing has been recorded."""
    try:
        text = location(project).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    trees = []
    for line in text.splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            trees.append(entry.rstrip("/") or "/")
    return tuple(trees)


def record(project):
    """Add every live project tree missing from the list; return those added.

    Appends rather than rewriting, so an author's comments and order survive,
    and writes through a temporary file so a concurrent reader never sees half
    a list.
    """
    path = location(project)
    known = set(recorded(project))
    added = [
        str(tree)
        for tree in worktrees.project_trees(project.root)
        if str(tree) not in known
    ]
    if not added:
        return []
    text = path.read_text(encoding="utf-8") if path.exists() else _PREAMBLE
    if text and not text.endswith("\n"):
        text += "\n"
    text += "".join(f"{tree}\n" for tree in added)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{FILENAME}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return added


def _pure(path):
    return PurePosixPath(os.path.normpath(path))


def within(trees, path):
    """Whether `path` is one of `trees` or sits below one.

    Compared on path components and never resolved against this machine's
    filesystem, which is what keeps the answer the same wherever the corpus is
    read. (`worktrees.contains` resolves, correctly for capture, which asks
    about directories that exist.)
    """
    if not path:
        return False
    target = _pure(path)
    for tree in trees:
        root = _pure(tree)
        if target == root or root in target.parents:
            return True
    return False


def relative(trees, path):
    """`path` made relative to the deepest listed tree holding it.

    Deepest first, because a worktree can live inside another checkout, and the
    enclosing checkout would otherwise claim the worktree's files as its own
    `.claude/worktrees/<name>/...`. A path under no listed tree comes back
    without its leading slash, which no document prefix matches.
    """
    for tree in sorted(trees, key=len, reverse=True):
        if path.startswith(tree.rstrip("/") + "/"):
            return path[len(tree.rstrip("/")) + 1 :]
    return path.lstrip("/")
