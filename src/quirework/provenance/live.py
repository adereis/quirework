"""Sessions the harness holds more of than the corpus does.

The corpus is what a sweep captured and an author accepted. A conversation
still going on, or one that went on after a sweep captured it, has turns that
exist only in the harness's own transcript under the user's home. Two readers
need to know about them: the spec trail, which points at a turn the author
made a minute ago, and corpus search, which must say that it did not read
them, so that a miss is never taken for proof that a turn does not exist.

Read-only. Claude Code and Codex list their transcripts without writing
anything; Antigravity's live logs are laid out differently from its captures,
so its sessions are found once captured.
"""

from __future__ import annotations

from pathlib import Path

from quirework.project import current_project


def transcripts():
    """Session id -> ``(family, path)`` for every live transcript of this project.

    A transcript belongs when it was recorded in any checkout of the project,
    linked worktrees included, since every checkout shares the one corpus.
    """
    from quirework.provenance import worktrees
    from quirework.provenance.ingestion import claude, codex

    roots = worktrees.project_trees(current_project().root)
    found = {}
    for directory in claude.sibling_session_dirs():
        for path in sorted(directory.glob("*.jsonl")):
            if claude.belongs(path, roots):
                found.setdefault(path.stem, ("cc", path))
    for path in codex.sweep()[0]:
        sid = codex.session_id(path)
        if sid:
            found.setdefault(sid, ("codex", path))
    return found


def grown(family, path, captured_family, capture):
    """Whether a live transcript holds dialogue its newest capture lacks.

    Read off the fingerprint each capture records of its source, so a settled
    session costs a stat and a short read. A capture that records none is
    treated as behind, which only means its transcript is read instead: a
    transcript holds everything a capture made from it does.
    """
    if family != captured_family:
        return False
    path, capture = Path(path), Path(capture)
    if family == "cc":
        from quirework.provenance.ingestion import claude

        return not claude.dialogue_unchanged(path, capture)
    from quirework.provenance.ingestion import codex

    recorded = codex.captured_source_fingerprint(capture)
    return recorded is None or codex.source_fingerprint(path) != recorded


def ahead(captured):
    """The live sessions holding turns no capture in ``captured`` has.

    ``captured`` maps a session id to ``(family, path)`` of its newest
    capture. A session joins when no capture holds it, and also when its
    dialogue has grown since that capture: a session captured while it was
    still going on keeps going, and the turns after the capture exist only in
    its transcript.
    """
    return {
        sid: where
        for sid, where in transcripts().items()
        if sid not in captured or grown(*where, *captured[sid])
    }
