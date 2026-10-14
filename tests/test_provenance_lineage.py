"""A Claude Code session resumed under a new id is one conversation, counted once.

Claude Code resumes or forks a session under a new id, and the new transcript
opens with the whole conversation it continues, every record copied with its
id. Both captures can be accepted, so a reader summing every record of every
capture booked the shared opening twice: the effort ledger doubled its turns
and active time, and the blind cold-read read it twice and weighed it double.
These pin which capture owns a shared record, and that each reader keeps only
what a capture owns.
"""

import json

import pytest

from conftest import corpus_settings, keep_session
from quirework.project import Project, project_scope
from quirework.provenance import coldread, lineage, metrics, scope, store

ORIGINAL = "0c0c0c0c-2222-4333-8444-000000000001"
COPY = "0d0d0d0d-2222-4333-8444-000000000002"


def _turn(n, kind, text, session, minute, **extra):
    content = text if kind == "user" else [{"type": "text", "text": text}]
    return {
        "type": kind,
        "uuid": f"00000000-0000-4000-8000-{n:012d}",
        "sessionId": session,
        "timestamp": f"2031-05-06T10:{minute:02d}:00Z",
        "message": {"content": content},
        **extra,
    }


def _opening(session):
    """The conversation both captures hold, stamped with ``session``'s id."""
    return [
        _turn(1, "user", "Draft the lighthouse scene.", session, 0),
        _turn(2, "assistant", "Drafted it in four paragraphs.", session, 5),
        _turn(3, "user", "Cut the second paragraph.", session, 10),
        _turn(4, "assistant", "Cut.", session, 15),
    ]


SUBAGENT = [
    {
        "type": "user",
        "isSidechain": True,
        "agentId": "f00d",
        "uuid": "00000000-0000-4000-8000-000000000901",
        "timestamp": "2031-05-06T10:06:00Z",
        "message": {"content": "Find every lighthouse."},
    },
    {
        "type": "assistant",
        "isSidechain": True,
        "agentId": "f00d",
        "uuid": "00000000-0000-4000-8000-000000000902",
        "timestamp": "2031-05-06T10:07:00Z",
        "message": {"content": [{"type": "text", "text": "Two of them."}]},
    },
]


def _continuation():
    return [
        _turn(5, "user", "Now the storm.", COPY, 40),
        _turn(6, "assistant", "The storm arrives at dusk.", COPY, 45),
    ]


def _jsonl(records):
    return "\n".join(json.dumps(r) for r in records) + "\n"


def _write(tmp_path, name, records):
    path = tmp_path / f"{name}.jsonl"
    path.write_text(_jsonl(records), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# ownership
# --------------------------------------------------------------------------- #
def test_a_resumed_copy_owns_the_conversation_it_continues(tmp_path):
    original = _write(tmp_path, ORIGINAL, _opening(ORIGINAL) + SUBAGENT)
    copy = _write(tmp_path, COPY, _opening(COPY) + _continuation())
    own = lineage.owners([original, copy])
    assert own.held_by(original) == {str(copy)}
    assert not own.held_by(copy)
    assert own.inherited(original) == 4 and own.inherited(copy) == 0
    # A sub-agent's records are its own session's: no copy carries them.
    assert own.owns(original, SUBAGENT[0]["uuid"])


def test_ownership_does_not_depend_on_the_order_captures_are_listed(tmp_path):
    original = _write(tmp_path, ORIGINAL, _opening(ORIGINAL))
    copy = _write(tmp_path, COPY, _opening(COPY) + _continuation())
    assert lineage.owners([copy, original]).held_by(original) == {str(copy)}
    # Two copies holding the same conversation: the one that ran later owns
    # it, whichever is listed first.
    twin = _write(tmp_path, "0e0e0e0e-2222-4333-8444-000000000003", _opening(COPY))
    later = [
        dict(r, timestamp=r["timestamp"].replace("10:", "11:")) for r in _opening(COPY)
    ]
    late = _write(tmp_path, "0a0a0a0a-2222-4333-8444-000000000004", later)
    for order in ([twin, late], [late, twin]):
        assert lineage.owners(order).held_by(twin) == {str(late)}


def test_a_turn_the_copy_left_behind_keeps_the_original_a_session(tmp_path):
    """A turn that drew an API error stays in the original only.

    The original then holds dialogue the copy lacks, so it is not absorbed,
    but the opening the two share is still owned once, by the copy.
    """
    unanswered = _turn(7, "user", "And the keeper?", ORIGINAL, 20)
    original = _write(tmp_path, ORIGINAL, _opening(ORIGINAL) + [unanswered])
    copy = _write(tmp_path, COPY, _opening(COPY) + _continuation())
    own = lineage.owners([original, copy])
    assert not own.held_by(original)
    assert own.owns(original, unanswered["uuid"])
    assert own.inherited(original) == 4


def test_a_harness_notice_left_behind_is_no_dialogue_of_its_own(tmp_path):
    notice = _turn(
        7, "user", "The fork runs as its own session.", ORIGINAL, 20, isMeta=True
    )
    original = _write(tmp_path, ORIGINAL, _opening(ORIGINAL) + [notice])
    copy = _write(tmp_path, COPY, _opening(COPY) + _continuation())
    assert lineage.owners([original, copy]).held_by(original) == {str(copy)}


def test_a_record_with_no_id_is_always_its_own_captures(tmp_path):
    bare = [{k: v for k, v in r.items() if k != "uuid"} for r in _opening(ORIGINAL)]
    original = _write(tmp_path, ORIGINAL, bare)
    copy = _write(tmp_path, COPY, _opening(COPY))
    own = lineage.owners([original, copy])
    assert own.owns(original, None)
    assert not own.held_by(original)


# --------------------------------------------------------------------------- #
# the readers
# --------------------------------------------------------------------------- #
@pytest.fixture
def corpus(tmp_path):
    """A corpus holding a session and the copy that continued it, both accepted."""
    project = Project.from_settings(tmp_path, {"provenance": corpus_settings()})
    keep_session(
        project,
        f"cc-sessions/{ORIGINAL}.jsonl",
        _jsonl(_opening(ORIGINAL) + SUBAGENT),
    )
    keep_session(
        project,
        f"cc-sessions/{COPY}.jsonl",
        _jsonl(_opening(COPY) + _continuation()),
    )
    with project_scope(project):
        yield project


def _making(project, session):
    with project_scope(project):
        path = store.ledger(project) / store.MAKING
    path.write_text(f"cc {session}  # fictitious\n", encoding="utf-8")


def test_the_ledger_counts_a_resumed_conversation_once(corpus):
    acc = metrics.Acc("cc")
    metrics.claude_code(acc)
    assert (acc.sessions, acc.resumed) == (1, 1)
    assert (acc.human_turns, acc.ai_turns) == (3, 3)
    # The original's sub-agent is no copy's, and still counts.
    assert acc.subagents == 1
    # Active time: the copy's span alone, 10:00 to 10:45 with no gap over the cap.
    assert acc.engaged == 45 * 60


def test_the_paste_audit_reads_a_resumed_turn_once(corpus):
    texts = [text for _, text in metrics.human_turns()]
    assert sorted(texts) == sorted(
        ["Draft the lighthouse scene.", "Cut the second paragraph.", "Now the storm."]
    )


def test_the_scopes_still_add_up_to_the_total(corpus):
    _making(corpus, ORIGINAL)
    halves = []
    for s in scope.SCOPES:
        acc = metrics.Acc(s)
        metrics.claude_code(acc, s)
        halves.append(acc.human_turns)
    whole = metrics.Acc("all")
    metrics.claude_code(whole)
    assert sum(halves) == whole.human_turns == 3


def test_the_cold_read_reads_a_resumed_conversation_once(corpus):
    read, resumed = coldread.sources(None)
    stems = [p.stem for p, _ in read]
    assert stems == [COPY]
    [(path, holders)] = resumed
    assert path.stem == ORIGINAL and [h.stem for h in holders] == [COPY]
    # Discards, which walks the same sources, sees the copy alone.
    assert [p.stem for p, _ in coldread.iter_sources(None)] == [COPY]


def test_a_run_keeps_a_session_whose_copy_it_does_not_read(corpus):
    """The original is the apparatus half and its copy the work's.

    Each run asks its own question, so each reads its own capture whole, and
    neither drops the original because the other scope holds a copy.
    """
    _making(corpus, ORIGINAL)
    making, folded = coldread.sources(scope.MAKING)
    assert [p.stem for p, _ in making] == [ORIGINAL] and not folded
    work, folded = coldread.sources(scope.WORK)
    assert [p.stem for p, _ in work] == [COPY] and not folded


def test_status_names_the_capture_that_reads_a_resumed_session(corpus, capsys):
    coldread.cmd_status([])
    out = capsys.readouterr().out
    assert "resumed: 1 session(s) are read once" in out
    line = next(ln for ln in out.splitlines() if ln.strip().startswith("cc-sessions/"))
    assert ORIGINAL in line.split("->")[0] and COPY in line.split("->")[1]
