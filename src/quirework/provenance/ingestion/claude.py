#!/usr/bin/env python3
"""Capture a project's local Claude Code sessions into its provenance corpus.

Claude Code keys transcript directories to the literal working directory. The
sweep therefore considers project subdirectories and every linked checkout.
Encoded directory names are lossy, so a transcript is accepted only when its
recorded working directory belongs to one of those checkouts.

Captures are **normalized**, not verbatim: `ccfilter.normalize_capture` keeps
the human's prompts, the assistant's prose and thinking, one action record per
tool call, and session identity, and leaves the file contents read and written,
the tool output, and the harness bookkeeping in the source transcript (and, for
anything that landed, in git). That module owns the rule and the reasoning. A
capture preserves source mtime and records the source fingerprint it was made
from, which is what a later sweep compares -- so a live session that has since
grown is re-normalized, and a settled one is left alone.

Modes:
  sync_sessions.py            sweep every project dir for this repo
                              (root + subdirectory launches + worktrees)
  sync_sessions.py --hook     same sweep, quiet unless something new
                              was captured (what SessionEnd invokes)

Sessions listed in `removed-sessions.txt` are never (re-)imported: the corpus is
curated, and the originals stay in ~/.claude, so without this the auto-sync hook
would keep re-materializing sessions we deliberately removed. Entries are matched
by session **id**, never by literal filename -- see `load_removed()` for why.

Pure stdlib; no venv needed. Never commits — that stays a manual, reviewed step.
"""

import json, os, re, sys, tempfile
from pathlib import Path
from quirework.project import current_project

# tools/session-sync/<this> -> repo root

from quirework.provenance import ccfilter
from quirework.provenance import worktrees  # noqa: E402  (path set above)


# a removed-list entry: a session uuid, with or without the `.jsonl` suffix
REMOVED_ENTRY = re.compile(
    r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?:\.jsonl)?", re.I
)


def load_removed() -> set:
    """Session ids curated out of the corpus; never (re-)import these.

    Matching is on the session **id**, not on the literal line: the documented
    form is `<uuid>.jsonl  # reason`, but a bare uuid is accepted too (the
    agy-sessions list is written that way, and `catalog.py` reads both). Match
    the raw string against the filename instead, and a suffix-less entry matches
    nothing and is re-imported on the next sweep, while `catalog.py` -- which
    strips the suffix -- goes on reporting it as curated out. The two readers
    disagree and neither can say so; it has happened, to three entries. A line
    naming no session is reported and skipped, never guessed at.
    """
    if not (
        current_project().path("provenance.claude_sessions") / "removed-sessions.txt"
    ).exists():
        return set()
    ids = set()
    for lineno, line in enumerate(
        (current_project().path("provenance.claude_sessions") / "removed-sessions.txt")
        .read_text()
        .splitlines(),
        1,
    ):
        entry = line.split("#", 1)[0].strip()  # strip comments / reasons
        if not entry:
            continue
        m = REMOVED_ENTRY.fullmatch(entry)
        if not m:
            print(
                f"cc-sessions: {(current_project().path('provenance.claude_sessions') / 'removed-sessions.txt').name}:{lineno}: not a session id, "
                f"ignoring {entry!r}",
                file=sys.stderr,
            )
            continue
        ids.add(m.group(1).lower())
    return ids


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


def is_current(src: Path, dst: Path) -> bool:
    """Whether the capture at ``dst`` was made from the source at ``src``.

    Two stages, because this runs from a `SessionEnd` hook on every session and
    the corpus is hundreds of files. A capture is a seventh the size of what it
    was made from, so the old `src.st_size == dst.st_size` shortcut cannot
    survive normalization -- but the *recorded* source size can stand in for it,
    and the capture carries the source's mtime, so a settled session is still
    settled after one stat and one short read.

    Anything the cheap stage cannot vouch for falls through to the recorded
    SHA-256, which also catches an edit that leaves the byte count unchanged.
    When that agrees, the capture is current and only its mtime had drifted --
    which is the normal state after a fresh clone or `git worktree add`, where
    every file is stamped with the checkout time. Restoring it there re-arms the
    cheap stage instead of leaving every later sweep to re-read the corpus.
    """
    captured = captured_fingerprint(dst)
    if captured is None:
        return False
    source_bytes, source_sha = captured
    st = src.stat()
    if st.st_size == source_bytes and st.st_mtime_ns == dst.stat().st_mtime_ns:
        return True
    if (st.st_size, ccfilter.source_fingerprint(src)[1]) != (source_bytes, source_sha):
        return False
    os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
    return True


def copy_if_changed(src: Path) -> bool:
    """Normalize src into DEST iff it is new or has changed. True if captured."""
    dst = current_project().path("provenance.claude_sessions") / src.name
    if is_current(src, dst):
        return False
    write_capture(src, dst)
    return True


def belongs(path: Path, roots) -> bool:
    """Whether a candidate transcript really ran in one of this project's trees.

    The encoded directory name cannot answer this on its own (see the module
    docstring), so the recorded `cwd` decides. A transcript that records none is
    kept: the directory heuristic already chose it, and missing a session is the
    failure this tool exists to prevent."""
    cwd = recorded_cwd(path)
    return True if cwd is None else worktrees.contains(roots, cwd)


def sweep() -> list[Path]:
    dirs = sibling_session_dirs()
    if not dirs:
        raise SystemExit(
            f"no Claude Code session dir for {current_project().root}: {encoded_project_dir(current_project().root)}"
        )
    files = [p for d in dirs for p in d.glob("*.jsonl")]
    return sorted(files)


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="quirework provenance ingest claude")
    parser.add_argument("--hook", action="store_true")
    args = parser.parse_args(argv)
    current_project().path("provenance.claude_sessions").mkdir(
        parents=True, exist_ok=True
    )
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
    sources = sweep()

    removed = load_removed()
    skipped = [p for p in sources if p.stem.lower() in removed]
    sources = [p for p in sources if p.stem.lower() not in removed]

    # Verify the recorded cwd only for transcripts that would actually be
    # captured: on a settled corpus that is nothing, so the steady state stays
    # a stat and a header read per file rather than a read of all five hundred.
    roots = worktrees.project_trees(current_project().root)
    foreign = unchanged = 0
    copied = []
    for p in sources:
        if is_current(p, current_project().path("provenance.claude_sessions") / p.name):
            unchanged += 1
            continue
        if not belongs(p, roots):
            foreign += 1
            continue
        if copy_if_changed(p):
            copied.append(p)
        else:
            unchanged += 1
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
            f"{unchanged} unchanged, "
            f"{len(skipped)} on removed-list, "
            f"{foreign} from another project -> {current_project().path('provenance.claude_sessions')}"
        )


if __name__ == "__main__":
    main()
