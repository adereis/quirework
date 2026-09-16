"""Corpus scope: which question a session is evidence *for*.

The corpus is curated by *presence* -- a session in the working tree is part
of it -- and presence answers "is this evidence?" but not "evidence for
what?". Two artifacts now read the same corpus and want different halves.
The **book** cold-read asks who originated the book's ideas and prose; the
**making** cold-read asks the same of the instruments, guards and process
the book's Part Three describes. A session spent on the ingest tooling is
real evidence for the second and pure dilution in the first, and before this
module existed the only way to keep it out of the first was to delete it,
which took it out of ``search``, ``show`` and ``metrics`` too. One switch was
doing three jobs.

So scope is a second axis, orthogonal to presence. ``making-sessions.txt``
lists the apparatus half, in the same house form as the ``removed-sessions.txt``
beside it; anything not listed is ``book``. Being listed is **not** a
demotion and gates nothing outside the cold-read: ``search``, ``show`` and
``metrics`` read the whole corpus, which is the entire point of keeping both
halves on disk. Era-A web dialogues have no scope entry and are always
``book`` -- they predate every instrument.

Entries are matched by session **id**, never by literal filename, for the
reason ``sync_sessions.load_removed`` documents at length: the two list
readers must agree about what a line names, or one silently disagrees with
the other and neither can say so.
"""

from __future__ import annotations

import re
import sys

from quirework.project import current_project

BOOK = "book"
MAKING = "making"
SCOPES = (BOOK, MAKING)


# a scope-list entry: a session uuid, with or without the `.jsonl` suffix
_ENTRY = re.compile(r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?:\.jsonl)?", re.I)


def _settings():
    """Resolve paths from the current operation, without import-time state."""
    from types import SimpleNamespace

    MAKING_CC = (
        current_project().path(
            "provenance.claude_sessions", ".quirework/provenance/cc-sessions"
        )
        / "making-sessions.txt"
    )
    MAKING_AGY = (
        current_project().path(
            "provenance.agy_sessions", ".quirework/provenance/agy-sessions"
        )
        / "making-sessions.txt"
    )
    MAKING_CODEX = (
        current_project().path(
            "provenance.codex_sessions", ".quirework/provenance/codex-sessions"
        )
        / "making-sessions.txt"
    )
    return SimpleNamespace(
        MAKING_AGY=MAKING_AGY, MAKING_CC=MAKING_CC, MAKING_CODEX=MAKING_CODEX
    )


def _parse(path):
    """Session ids listed in one scope file, with reasons.

    A line naming no session is reported and skipped, never guessed at --
    the same discipline the removed-list reader uses, and for the same
    reason: a silently-dropped entry is a session that quietly changes
    scope on the next run.
    """
    out = {}
    if not path.exists():
        return out
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        entry, _, reason = line.partition("#")
        entry = entry.strip()
        if not entry:
            continue
        m = _ENTRY.fullmatch(entry)
        if not m:
            print(
                f"{path.name}:{lineno}: not a session id, ignoring {entry!r}",
                file=sys.stderr,
            )
            continue
        out[m.group(1).lower()] = reason.strip()
    return out


def making_ids():
    """Ids of every session scoped to the apparatus, across CLI corpora."""
    return {
        **_parse(_settings().MAKING_CC),
        **_parse(_settings().MAKING_AGY),
        **_parse(_settings().MAKING_CODEX),
    }


def scope_of_id(sid, making=None):
    """``'making'`` or ``'book'`` for a session id."""
    ids = making_ids() if making is None else making
    return MAKING if str(sid).lower() in ids else BOOK


def scope_of_path(path, making=None):
    """``'making'`` or ``'book'`` for a source path (web sources are always book)."""
    from pathlib import Path

    return scope_of_id(Path(path).stem, making)


def wants(path, scope, making=None):
    """True if ``path`` belongs in a run of ``scope`` (``None`` means every scope)."""
    return scope is None or scope_of_path(path, making) == scope
