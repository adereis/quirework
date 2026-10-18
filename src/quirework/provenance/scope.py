"""Corpus scope: which question a session is evidence *for*.

A session is in the corpus once it is accepted, and acceptance answers "is
this evidence?" but not "evidence for what?". Two questions read the same
corpus and want different halves. The **work** cold-read asks who originated
the work's ideas and prose; the **making** cold-read asks the same of the
instruments, guards and process the work was made with. A session spent on the
ingest tooling is real evidence for the second and pure dilution in the first,
and before this module existed the only way to keep it out of the first was to
delete it, which took it out of ``search``, ``show`` and ``metrics`` too. One
switch was doing three jobs.

So scope is a second axis, orthogonal to acceptance. The ledger's
``making-sessions.txt`` lists the apparatus half, in the same form as the
``removed-sessions.txt`` beside it; anything not listed is ``work``. Being
listed is **not** a demotion and gates nothing outside the cold-read:
``search``, ``show`` and ``metrics`` read the whole corpus, which is the entire
point of keeping both halves on disk. Web dialogues have no scope entry and are
always ``work``.

Both lists are read by `store.listed`, so no two readers can disagree about
what a line names.

The work's scope was once called ``book``. A cached report or a catalog row
that still says so is read as ``work`` (`normalize`), because a cache that
missed on a renamed label would buy every report again.
"""

from __future__ import annotations

WORK = "work"
MAKING = "making"
SCOPES = (WORK, MAKING)

# Labels a scope was recorded under before it was renamed.
_LEGACY = {"book": WORK}


def normalize(value):
    """A recorded scope label as the scope it names today."""
    return _LEGACY.get(value, value)


def making_ids():
    """Ids of every session scoped to the apparatus, across CLI corpora."""
    from quirework.provenance import store

    return {e.session: e.reason for e in store.listed(store.MAKING)}


def scope_of_id(sid, making=None):
    """``'making'`` or ``'work'`` for a session id."""
    ids = making_ids() if making is None else making
    return MAKING if str(sid).lower() in ids else WORK


def scope_of_path(path, making=None):
    """``'making'`` or ``'work'`` for a source path (web sources are always work).

    A review-sheet ruling is scoped by its session id, ``rulings-`` and its
    file's stem, which is the name it is listed under, never the bare stem.
    """
    from pathlib import Path

    from quirework.provenance import rulings

    sid = rulings.session_id(path) if rulings.holds(path) else Path(path).stem
    return scope_of_id(sid, making)


def wants(path, scope, making=None):
    """True if ``path`` belongs in a run of ``scope`` (``None`` means every scope)."""
    return scope is None or scope_of_path(path, making) == scope
