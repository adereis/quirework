"""Which Claude Code capture owns a record that several captures hold.

Claude Code resumes a session, or forks one, under a new session id, and the
new transcript opens with the conversation it continues: every record copied
over with its id, re-stamped with the new session's. Both transcripts are
captured, and a curator may accept both, because each can hold something the
other lacks. The copy holds what came after. The original keeps its sub-agents,
and any branch the copy left behind, such as a turn that drew an API error. A
reader that counts every record of every capture therefore counts the shared
opening once per copy: the effort ledger books its turns, words and active time
twice, and the blind cold-read reads it twice and weighs it double.

This module decides, for a set of captures, which one owns each record that
several of them hold. The record's id is the identity, so only Claude Code is
covered. It is the one harness whose records carry a stable id, and the Codex
reader already starts a forked rollout at its fork point.

The owner is the capture holding most of the conversation, counted in its
dialogue records (`dialogue_id`). Ties go to the one that ran later, then to the
file name, so the answer never depends on the order the captures were listed.
For a resume, the owner is always the copy, which holds the original's whole
dialogue and then some. A reader that keeps only the records a capture owns
therefore sees the conversation once, and whole.

Ownership is a property of the set it was computed over. The effort ledger asks
it of the whole corpus, so that per-scope counts still add up to the total; the
cold-read asks it of one run's sources, so a session never leaves a run because
its copy was curated into the other scope.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict

from quirework.provenance import ccfilter as FLT


def dialogue_id(obj):
    """The id a Claude Code record is shared by, or ``None``.

    Only a dialogue record has one: a turn of the conversation itself, in the
    human's slot or the model's. A sub-agent's record belongs to the session
    that started it, and a copy never carries it. A record the harness marks
    as its own (`ccfilter.harness_flagged`) is no one's turn, so a capture that
    differs from another only in such records holds no dialogue of its own.
    """
    if obj.get("type") not in ("user", "assistant") or obj.get("isSidechain"):
        return None
    if FLT.harness_flagged(obj):
        return None
    return obj.get("uuid") or None


def _read(path):
    """``(dialogue ids, last timestamp)`` of one capture."""
    ids, last = [], ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn line, as every other corpus reader allows
            if not isinstance(obj, dict):
                continue
            rid = dialogue_id(obj)
            if rid:
                ids.append(rid)
                last = max(last, obj.get("timestamp") or "")
    return ids, last


class Owners:
    """The owner of every record that more than one capture holds."""

    def __init__(self, ids, owner):
        self._ids = ids  # capture -> its dialogue ids, in order
        self._owner = owner  # shared record id -> the capture that owns it

    def owns(self, path, record_id):
        """Whether ``path`` counts the record with this id.

        A record no other capture holds is always its own capture's, and so is
        one with no id, which no copy can be matched by.
        """
        return self._owner.get(record_id, str(path)) == str(path)

    def held_by(self, path):
        """The captures that own all of ``path``'s dialogue, else an empty set.

        Empty whenever ``path`` owns a dialogue record of its own, or holds
        none: only a capture whose whole conversation is counted elsewhere is
        absorbed by another.
        """
        ids = self._ids.get(str(path), ())
        if not ids or any(self.owns(path, rid) for rid in ids):
            return frozenset()
        return frozenset(self._owner[rid] for rid in ids)

    def inherited(self, path):
        """How many of ``path``'s dialogue records another capture owns."""
        return sum(
            1 for rid in self._ids.get(str(path), ()) if not self.owns(path, rid)
        )


# The last answer, keyed by every capture's path, size and modification time.
# One report asks the same corpus more than once (the effort ledger and its
# paste audit), and reading every capture again costs seconds. A key that
# names each file's state means a file rewritten in place is never answered
# from before; one entry means nothing accumulates across projects.
_LAST = {}


def owners(paths):
    """Decide ownership over the Claude Code captures at ``paths``."""
    stamp = []
    for path in paths:
        st = os.stat(path)
        stamp.append((str(path), st.st_size, st.st_mtime_ns))
    stamp = tuple(stamp)
    if stamp in _LAST:
        return _LAST[stamp]
    result = _decide(paths)
    _LAST.clear()
    _LAST[stamp] = result
    return result


def _decide(paths):
    ids, last = {}, {}
    for path in paths:
        key = str(path)
        ids[key], last[key] = _read(key)
    holders = defaultdict(set)
    for key, seen in ids.items():
        for rid in seen:
            holders[rid].add(key)

    def rank(key):
        return (len(set(ids[key])), last[key], os.path.basename(key))

    owner = {rid: max(keys, key=rank) for rid, keys in holders.items() if len(keys) > 1}
    return Owners(ids, owner)
