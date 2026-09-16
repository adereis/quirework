#!/usr/bin/env python3
"""Every working tree of this repository, read from git's own plumbing.

A repository can be checked out more than once at a time: `git worktree add`
gives a branch its own directory, and this project is worked that way -- a long
canon pass in one checkout while tooling moves in another, or a second agent on
a second branch. Nothing about that arrangement is visible from inside a single
checkout, and the capture sweeps derive their repo root from where their own
file sits (`Path(__file__).parents[2]`). A sweep run from one checkout therefore
sees only that checkout, and every session launched from the others is silently
missed -- which is exactly what happened here: 11 Codex rollouts recorded the
worktree as their cwd and 3 recorded the main checkout, and no single run of the
old sweep could see both sets.

Read that against `_paths.py`, which calls the same `__file__`-derived
root deliberate and correct, and which refuses outright to run when the
working directory sits in a different checkout. Both are right, about
different questions: that one is which checkout a process reads and
writes, where exactly one answer is correct; this one is which checkouts
sessions were launched from, where every answer is. The bug was using
the first to answer the second.

This module is the one owner of the answer. Git records the relationship in two
plain files, which is all that is read:

    <linked>/.git                         "gitdir: <main>/.git/worktrees/<name>"
    <main>/.git/worktrees/<name>/gitdir   "<linked>/.git"

so from any checkout we reach the common `.git` directory, and from there every
linked checkout. No subprocess and no `git` on PATH: the capture sweeps run
under bare `python3` from a hook or a shell, with no venv and no environment to
speak of. `checkouts()` never raises -- a repo with no worktrees, a plain
directory that is not a repo at all, and a stale `worktrees/<name>` entry whose
directory has been deleted but not pruned all degrade to the obvious answer.

Three consumers, three ways in, one rule:

  - package code imports `from .worktrees import checkouts`;
  - the standalone sync scripts add this directory to `sys.path` and
    `import worktrees` (no package import, so no dependency on an installed
    `mindwright`);
  - `sync_agy_sessions.sh` runs this file as a script, which prints one
    absolute checkout path per line.
"""

from __future__ import annotations

import sys
from pathlib import Path


def common_git_dir(start):
    """The repository's shared `.git` directory, from any checkout inside it.

    In the main checkout `.git` is that directory. In a linked worktree `.git`
    is a *file* pointing at `<main>/.git/worktrees/<name>`, whose grandparent is
    the shared directory. Returns ``None`` when `start` is not in a repository
    (or is a bare one), which callers read as "just this directory".
    """
    here = Path(start).resolve()
    for d in (here, *here.parents):
        dotgit = d / ".git"
        if dotgit.is_dir():
            return dotgit
        if dotgit.is_file():
            try:
                text = dotgit.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                return None
            if not text.startswith("gitdir:"):
                return None
            gitdir = Path(text.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = (d / gitdir).resolve()
            # .git/worktrees/<name> -> .git
            if gitdir.parent.name == "worktrees":
                return gitdir.parent.parent
            return gitdir
    return None


def checkouts(start):
    """Absolute paths of every working tree of `start`'s repository.

    The main checkout first, then each linked worktree, deduplicated and sorted.
    When `start` is not inside a repository at all, the answer is `start` itself
    -- so a caller can use this unconditionally in place of its own root. When
    it *is* inside one, `start` may be a subdirectory (`<checkout>/docs/canon`)
    and only the checkouts are returned, never the subdirectory.
    """
    root = Path(start).resolve()
    common = common_git_dir(root)
    if common is None:
        return [root]
    found = set()
    main = common.parent
    if main.is_dir():
        found.add(main)
    wt_dir = common / "worktrees"
    if wt_dir.is_dir():
        for entry in wt_dir.iterdir():
            pointer = entry / "gitdir"
            if not pointer.is_file():
                continue
            try:
                target = Path(
                    pointer.read_text(encoding="utf-8", errors="replace").strip()
                )
            except OSError:
                continue
            # The pointer names the worktree's own `.git` file; a checkout that
            # has been deleted but not `git worktree prune`d still has its entry
            # here, so require the directory to exist.
            checkout = target.parent if target.name == ".git" else target
            if checkout.is_dir():
                found.add(checkout.resolve())
    return sorted(found) or [root]


def contains(checkout_list, path):
    """Whether `path` is one of the checkouts or sits below one.

    Comparison is on resolved path *components*, never on string prefixes: a
    sibling directory whose name merely extends a checkout's
    (`example-book-worktree` beside `example-book`) is not
    inside it, and a substring test says it is. That bug is the reason the old
    sweeps appeared to work across the worktree.
    """
    if not path:
        return False
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    for root in checkout_list:
        root = Path(root)
        if resolved == root or root in resolved.parents:
            return True
    return False


def main(argv=None):
    """Print one absolute checkout path per line (the shell entry point)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    start = Path(argv[0]) if argv else Path(__file__).resolve().parents[2]
    for path in checkouts(start):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
