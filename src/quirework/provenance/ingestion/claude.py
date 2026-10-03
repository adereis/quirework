#!/usr/bin/env python3
"""Capture a project's local Claude Code sessions into its provenance corpus.

Claude Code keys transcript directories to the literal working directory. The
sweep therefore considers project subdirectories and every linked checkout.
Encoded directory names are lossy, so a transcript is captured only when its
recorded working directory belongs to one of those checkouts.

Captures are **normalized**, not verbatim: `ccfilter.normalize_capture` keeps
the human's prompts, the assistant's prose and thinking, one action record per
tool call, and session identity, and leaves the file contents read and written,
the tool output, and the harness bookkeeping in the source transcript (and, for
anything that landed, in git). That module owns the rule and the reasoning. A
capture preserves source mtime and records the source fingerprint it was made
from, which is what a later sweep compares -- so a live session that has since
grown is re-normalized, and a settled one is left alone.

Captures land in the store's pending area (`store.py`): a session is evidence
only once `quirework provenance accept` takes it. A session already accepted is
captured again only when its transcript has grown since, and then as a pending
capture beside the accepted version, which stays as it was.

Modes:
  quirework provenance ingest claude          sweep every project dir for this
                                              repo (root + subdirectory
                                              launches + worktrees)
  quirework provenance ingest claude --hook   same sweep, quiet unless something
                                              new was captured (what SessionEnd
                                              invokes)

`quirework provenance store status` asks the same selection, through `survey`,
without writing: a session whose hook failed is listed there until a sweep
captures it, rather than waiting unseen for Claude Code's cleanup.

Sessions on the ledger's `removed-sessions.txt` are never (re-)imported: the
corpus is curated, and the originals stay in ~/.claude, so without the list the
hook would keep re-materializing sessions deliberately removed.

Pure stdlib. Never accepts anything: that stays a reviewed step.
"""

import json, os, re, sys, tempfile
from dataclasses import dataclass
from pathlib import Path
from quirework.project import current_project

from quirework.provenance import ccfilter, store, trees
from quirework.provenance import worktrees
from quirework.runs.storage import shown


def encoded_project_dir(project_path: Path) -> Path:
    """Claude Code's on-disk dir for a project: abspath with /and . -> -."""
    slug = re.sub(r"[/.]", "-", str(project_path.resolve()))
    return (
        current_project().path(
            "providers.claude.sessions", str(Path.home() / ".claude/projects")
        )
        / slug
    )


def sibling_session_dirs() -> list[Path]:
    """Every Claude Code project dir Claude Code has ever used for this book:
    for each checkout of the repository (main plus every `git worktree`), the
    project directory's own encoded dir plus one per subdirectory a session was
    launched from (each gets its own encoded dir; see the module docstring).
    Matches by prefix on the encoded name, so a session launched from any depth
    under the project is found without enumerating paths in advance.

    The roots come from `worktrees.project_trees`, not from the checkout list,
    so a book occupying a subdirectory of a larger repository collects its own
    sessions and not the surrounding repository's."""
    dirs = []
    for checkout in worktrees.project_trees(current_project().root):
        root_dir = encoded_project_dir(checkout)
        parent = root_dir.parent
        if root_dir.is_dir():
            dirs.append(root_dir)
        if parent.is_dir():
            prefix = root_dir.name + "-"
            dirs += [
                p for p in parent.iterdir() if p.is_dir() and p.name.startswith(prefix)
            ]
    return sorted(set(dirs))


def recorded_cwd(path: Path):
    """The working directory a transcript records, or ``None`` if it names none.

    Claude Code stamps `cwd` on each conversational record but not on the
    session-setup records that open a file, so this reads down until it finds
    one rather than trusting the first line.
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(obj, dict) and obj.get("cwd"):
                    return obj["cwd"]
    except OSError:
        pass
    return None


def captured_fingerprint(dst: Path):
    """The source ``(byte count, SHA-256)`` a capture was normalized from.

    ``None`` for a file that is not a capture -- which is how a raw transcript
    left over from before normalization gets re-captured rather than mistaken
    for a current one.
    """
    header = ccfilter.capture_header(dst)
    if not header:
        return None
    try:
        return int(header["source_bytes"]), str(header["source_sha256"])
    except (KeyError, TypeError, ValueError):
        return None


def write_capture(src: Path, dst: Path) -> None:
    """Normalize src into dst atomically, preserving the source's mtime.

    Atomically because the sweep runs from a `SessionEnd` hook that can be
    killed with the terminal: a half-written capture that still parsed would be
    indistinguishable from a short session. The mtime is the source's so the
    sweep clock keeps meaning what it meant when captures were plain copies.
    """
    data = ccfilter.normalize_capture(src)
    st = src.stat()
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=dst.parent, prefix=f".{dst.name}.", delete=False
        ) as fh:
            tmp = Path(fh.name)
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, dst)
        tmp = None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def captured_from(src: Path, dst: Path) -> bool:
    """Whether the capture at ``dst`` was made from ``src`` as it stands now.

    Two stages, because this runs from a `SessionEnd` hook on every session and
    the corpus is hundreds of files. A capture is a seventh the size of what it
    was made from, so comparing sizes says nothing -- but the *recorded* source
    size can stand in for it, and the capture carries the source's mtime, so a
    settled session is still settled after one stat and one short read.
    Anything the cheap stage cannot vouch for falls through to the recorded
    SHA-256, which also catches an edit that leaves the byte count unchanged.
    """
    captured = captured_fingerprint(dst)
    if captured is None:
        return False
    source_bytes, source_sha = captured
    st = src.stat()
    if st.st_size == source_bytes and st.st_mtime_ns == dst.stat().st_mtime_ns:
        return True
    return (st.st_size, ccfilter.source_fingerprint(src)[1]) == (
        source_bytes,
        source_sha,
    )


def is_current(src: Path, versions) -> bool:
    """Whether any capture in ``versions`` was made from ``src`` as it stands.

    ``versions`` are the pending capture, if any, and every accepted version of
    the session in the store, whichever branch's ledger accepted it: a session
    accepted anywhere and unchanged since has nothing new to capture.
    """
    return any(dst.is_file() and captured_from(src, dst) for dst in versions)


def accepted_versions(family):
    """``{file name: [accepted paths]}`` for one family, across the whole store."""
    found = {}
    base = store.open_store().accepted_dir(family)
    if base.is_dir():
        for version in sorted(base.iterdir()):
            if version.is_dir() and not version.name.startswith("."):
                for path in version.iterdir():
                    found.setdefault(path.name, []).append(path)
    return found


def belongs(path: Path, roots, excluding=()) -> bool:
    """Whether a candidate transcript really ran in one of this project's trees.

    The encoded directory name cannot answer this on its own (see the module
    docstring), so the recorded `cwd` decides. A transcript that records none is
    kept: the directory heuristic already chose it, and missing a session is the
    failure this tool exists to prevent."""
    cwd = recorded_cwd(path)
    return True if cwd is None else worktrees.contains(roots, cwd, excluding)


@dataclass(frozen=True)
class Survey:
    """This project's transcripts, sorted by what a sweep would do with each."""

    due: tuple[Path, ...]  # new or grown since every capture of it
    grown: frozenset[Path]  # those of `due` that an older capture exists for
    unchanged: int  # captured as they stand
    removed: int  # on the ledger's removed list, never captured
    foreign: int  # recorded as running in another project


def survey() -> Survey | None:
    """What a sweep would capture, found without writing anything.

    The sweep's own selection, so `store status` can name the sessions no
    capture holds as they stand: a session whose hook failed, or never ran,
    stays here until a sweep succeeds. ``None`` when Claude Code keeps no
    session directory for this project.
    """
    dirs = sibling_session_dirs()
    if not dirs:
        return None
    sources = sorted(p for d in dirs for p in d.glob("*.jsonl"))
    removed = store.removed_ids("cc")
    kept = [p for p in sources if p.stem.lower() not in removed]

    # Verify the recorded cwd only for transcripts that would actually be
    # captured: on a settled corpus that is nothing, so the steady state stays
    # a stat and a header read per file rather than a read of all five hundred.
    roots = worktrees.project_trees(current_project().root)
    excluding = worktrees.excluded_trees(current_project(), roots)
    pending = store.open_store().pending_dir(store.CC)
    accepted = accepted_versions(store.CC)
    due, grown = [], set()
    unchanged = foreign = 0
    for p in kept:
        versions = [pending / p.name, *accepted.get(p.name, ())]
        if is_current(p, versions):
            unchanged += 1
        elif not belongs(p, roots, excluding):
            foreign += 1
        else:
            due.append(p)
            if any(v.is_file() for v in versions):
                grown.add(p)
    return Survey(
        tuple(due), frozenset(grown), unchanged, len(sources) - len(kept), foreign
    )


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="quirework provenance ingest claude")
    parser.add_argument("--hook", action="store_true")
    args = parser.parse_args(argv)
    pending = store.open_store().pending_dir(store.CC)
    pending.mkdir(parents=True, exist_ok=True)
    hook = args.hook
    if hook:
        # Consume the SessionEnd payload to be a well-behaved hook consumer;
        # its transcript_path is redundant with the sweep below, which is the
        # actual capture mechanism (and the only one that also reaches
        # subdirectory-launched sessions the payload can't name on its own).
        try:
            json.load(sys.stdin)
        except json.JSONDecodeError:
            pass
    # Recorded before anything is captured: a session is only ever captured
    # from a tree that exists now, and this is the last moment that is known.
    for tree in trees.record(current_project()):
        print(f"cc-sessions: recorded project tree {shown(tree)}")
    found = survey()
    if found is None:
        raise SystemExit(
            f"no Claude Code session dir for {current_project().root}: "
            f"{encoded_project_dir(current_project().root)}"
        )
    copied = found.due
    for p in copied:
        write_capture(p, pending / p.name)
    if hook:
        # Stay quiet on the happy path so SessionEnd doesn't spam the transcript.
        if copied:
            noun = "session" if len(copied) == 1 else "sessions"
            print(f"cc-sessions: captured {len(copied)} {noun}")
    else:
        for p in copied:
            print(f"  + {p.name}  ({p.stat().st_size} bytes)")
        print(
            f"cc-sessions: {len(copied)} updated, "
            f"{found.unchanged} unchanged, "
            f"{found.removed} on removed-list, "
            f"{found.foreign} from another project -> {shown(pending)}"
        )


if __name__ == "__main__":
    main()
