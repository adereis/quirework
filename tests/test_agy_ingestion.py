"""The Antigravity sweep: its pure helpers, and its rules on a synthetic
Antigravity home and store."""

import json
from types import SimpleNamespace

import pytest

from conftest import capture_project, linked_worktree
from quirework.provenance import store
from quirework.provenance.ingestion import agy

TIDE = "7a1de000-0000-4000-8000-000000000001"
DUNE = "7a1de000-0000-4000-8000-000000000002"


def _calls(*values):
    return [
        {"tool_calls": [{"name": "run_command", "args": {"CommandLine": value}}]}
        for value in values
    ]


def test_the_tail_of_a_relative_path_is_not_where_a_session_opened():
    """`.venv/bin/activate` is not `/bin/activate`, which is outside every
    checkout and used to skip a working session as a review run."""
    assert agy.working_context(_calls("source .venv/bin/activate && make")) is None
    assert agy.working_context(_calls("ls docs/chapters/opening.md")) is None
    assert agy.working_context(_calls("find . -path '**/AGENTS.md'")) is None
    assert agy.working_context(_calls("cat ~/notes/today.md")) is None


def test_the_first_absolute_path_still_decides():
    rows = _calls(
        "source .venv/bin/activate",
        "cat /srv/projects/side-quest/manuscript/one.md && ls /tmp",
    )
    assert agy.working_context(rows) == "/srv/projects/side-quest/manuscript/one.md"
    assert agy.working_context(_calls('view "/srv/a b"')) == "/srv/a"


def test_a_clean_capture_says_nothing_and_a_repaired_one_says_how():
    assert agy._damage({}, []) == ""
    note = agy._damage({"<unparseable>": 2}, [0, 7])
    assert "2 line(s) did not parse" in note
    assert "step(s) 0, 7 taken from transcript.jsonl" in note


# --------------------------------------------------------------------------- #
# the sweep
# --------------------------------------------------------------------------- #
@pytest.fixture
def brain(tmp_path):
    """A book with a linked worktree, an Antigravity home, and a store."""
    base = tmp_path.resolve()
    repo = base / "book"
    (repo / ".git").mkdir(parents=True)
    worktree = linked_worktree(repo, base / "book-draft")
    sessions = base / "gemini" / "brain"
    sessions.mkdir(parents=True)
    with capture_project(repo, base / "store", agy=sessions):
        yield SimpleNamespace(
            base=base,
            repo=repo,
            worktree=worktree,
            sessions=sessions,
            pending=store.open_store().pending_dir(store.AGY),
        )


def _logs(brain, sid):
    logs = brain.sessions / sid / ".system_generated" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return logs


def _conversation(brain, sid, rows, name="transcript_full.jsonl"):
    """A session as Antigravity keeps it; ``name`` picks the cut-down copy."""
    path = _logs(brain, sid) / name
    lines = (r if isinstance(r, str) else json.dumps(r) for r in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _step(text=None, opened=None, index=0):
    row = {
        "step_index": index,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "created_at": "2031-04-02T10:00:00Z",
    }
    if text is not None:
        row["content"] = text
    if opened is not None:
        row["tool_calls"] = [{"name": "view_file", "args": {"Path": str(opened)}}]
    return row


def _sweep(capsys, *argv):
    capsys.readouterr()
    assert agy.main(list(argv)) == 0
    return capsys.readouterr().out


def test_a_session_that_can_be_placed_nowhere_is_counted(brain, capsys):
    """Antigravity records no working directory, so a transcript naming no
    checkout, or a session with no transcript, could be this project's
    session lost without a word."""
    _conversation(brain, TIDE, [_step(opened=brain.base / "elsewhere" / "main.py")])
    (brain.sessions / DUNE).mkdir()
    out = _sweep(capsys)
    assert "1 naming no checkout of this project" in out
    assert "1 without a transcript" in out
    assert not list(brain.pending.iterdir())
