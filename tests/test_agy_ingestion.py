"""The Antigravity sweep: its pure helpers, and its rules on a synthetic
Antigravity home and store."""

import json
from pathlib import Path
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


def _asked(text, index=0):
    """An opening request, the turn the evaluation-seat rule reads."""
    return {
        "step_index": index,
        "source": "USER_EXPLICIT",
        "type": "USER_INPUT",
        "status": "DONE",
        "created_at": "2031-04-02T10:00:00Z",
        "content": f"<USER_REQUEST>\n{text}\n</USER_REQUEST>",
    }


def _session_id(n):
    return f"7a1de000-0000-4000-8000-{n:012d}"


def _stored(area, family, sid):
    """A capture's path in the store, spelled absolutely, as a seat is handed it."""
    version = ("0123456789abcdef",) if area == "accepted" else ()
    return Path(store.location(), area, family, *version, f"{sid}.jsonl")


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


def test_a_session_in_either_checkout_is_captured(brain, capsys):
    _conversation(brain, TIDE, [_step(opened=brain.repo / "chapters" / "one.md")])
    _conversation(brain, DUNE, [_step(opened=brain.worktree / "chapters" / "two.md")])
    out = _sweep(capsys)
    assert (brain.pending / f"{TIDE}.jsonl").exists(), out
    assert (brain.pending / f"{DUNE}.jsonl").exists(), out
    assert "2 new" in out


def test_a_review_run_from_a_neutral_directory_is_named_and_not_captured(brain, capsys):
    """A blind read of the book names the book's path throughout, so naming
    it cannot decide; where the session first opened a file does."""
    _conversation(
        brain,
        TIDE,
        [
            _step(text=f"Reading the book at {brain.repo}, a chapter at a time."),
            _step(opened=brain.base / "reader-kit" / "chapter-six.pdf"),
        ],
    )
    out = _sweep(capsys)
    assert not (brain.pending / f"{TIDE}.jsonl").exists()
    assert f"{TIDE} opened in" in out and "review run, not captured" in out


def test_where_a_session_opened_comes_from_a_tool_call_not_its_prose(brain, capsys):
    """A model can narrate a workspace that does not exist; the first path a
    tool was handed is where it really worked."""
    _conversation(
        brain,
        TIDE,
        [
            _step(text=f"My workspace is `{brain.base / 'boo'}`."),
            _step(opened=brain.repo / "notes" / "plan.md"),
        ],
    )
    _sweep(capsys)
    assert (brain.pending / f"{TIDE}.jsonl").exists()


def test_a_transcript_that_opens_nothing_is_kept(brain, capsys):
    """Missing a session is the failure the sweep exists to prevent."""
    _conversation(brain, TIDE, [_step(text=f"Thinking aloud about {brain.repo}.")])
    _sweep(capsys)
    assert (brain.pending / f"{TIDE}.jsonl").exists()


@pytest.mark.parametrize("area", ["pending", "accepted"])
@pytest.mark.parametrize("first_opened", ["checkout", "capture"])
def test_an_evaluation_seat_is_named_and_not_captured(
    brain, capsys, area, first_opened
):
    """A seat handed a stored capture to judge is no session. One that opens
    the capture first is already a review run, since the store lies outside
    every checkout; one that reads the book's procedure first looks like the
    author working, and only its assignment gives it away."""
    target = _stored(area, store.CC, _session_id(99))
    opened = {
        "checkout": brain.repo / "procedures" / "ingest.md",
        "capture": target,
    }[first_opened]
    _conversation(
        brain,
        TIDE,
        [_asked(f"Judge {target}; the book is at {brain.repo}"), _step(opened=opened)],
    )
    out = _sweep(capsys)
    assert not (brain.pending / f"{TIDE}.jsonl").exists(), out
    said = {
        "checkout": "sent a transcript to evaluate -- not a session",
        "capture": "review run, not captured",
    }[first_opened]
    assert said in out and TIDE in out


def test_a_seat_handed_a_batch_of_captures_by_their_shape_is_not_captured(
    brain, capsys
):
    """Several sessions to one seat arrive as the capture path's shape and
    then bare ids, so the path is matched loosely."""
    shape = _stored("pending", store.AGY, "<uuid>")
    _conversation(
        brain,
        TIDE,
        [
            _asked(f"Read each with `quirework provenance show {shape}`: 0a1b2c3d."),
            _step(opened=brain.repo / "procedures" / "ingest.md"),
        ],
    )
    out = _sweep(capsys)
    assert not (brain.pending / f"{TIDE}.jsonl").exists(), out


def test_a_session_about_the_corpus_and_the_one_driving_an_ingest_are_kept(
    brain, capsys
):
    """The rule reads the assignment, not the subject: talking about the
    corpus, or driving the seats that judge it, is the author working."""
    _conversation(
        brain,
        TIDE,
        [
            _asked("Why does the sweep pull in sessions I never ran?"),
            _step(opened=brain.repo / "tools" / "sweep.py"),
        ],
    )
    _conversation(
        brain,
        DUNE,
        [
            _asked("Ingest the pending sessions."),
            _step(opened=brain.repo / "procedures" / "ingest.md"),
        ],
    )
    out = _sweep(capsys)
    assert (brain.pending / f"{TIDE}.jsonl").exists(), out
    assert (brain.pending / f"{DUNE}.jsonl").exists(), out


def test_a_removed_session_is_never_captured_again(brain, capsys):
    """Antigravity keeps a session after curation removes it, so only the
    ledger's list keeps every sweep from bringing it back."""
    _conversation(brain, TIDE, [_step(opened=brain.repo / "chapters" / "one.md")])
    (store.ledger() / store.REMOVED).write_text(f"agy {TIDE}  # a stub\n")
    out = _sweep(capsys)
    assert "1 curated out" in out
    assert not (brain.pending / f"{TIDE}.jsonl").exists()


def _source(brain, sid):
    return _logs(brain, sid) / "transcript_full.jsonl"


def test_a_capture_is_normalized_and_records_its_source(brain, capsys):
    freight = {
        "step_index": 1,
        "source": "MODEL",
        "type": "VIEW_FILE",
        "status": "DONE",
        "created_at": "2031-04-02T10:00:01Z",
        "content": "Z" * 8000,
    }
    _conversation(
        brain, TIDE, [_step(opened=brain.repo / "chapters" / "one.md"), freight]
    )
    out = _sweep(capsys)
    source, capture = _source(brain, TIDE), brain.pending / f"{TIDE}.jsonl"
    assert capture.stat().st_size < source.stat().st_size // 4, out
    header = json.loads(capture.read_text().splitlines()[0])
    # Spelled out, not read from `agyfilter.CAPTURE_SCHEMA`: a sweep rebuilds
    # every capture whose schema it does not know, so a changed string would
    # quietly remake the whole corpus. This is the tripwire for that.
    assert header["capture_schema"] == "quirework-agy-v1"
    assert header["source_bytes"] == source.stat().st_size
    assert header["dropped"]["VIEW_FILE.content"] == 1
    assert capture.stat().st_mtime == source.stat().st_mtime
    assert store.files(store.AGY) == [], "captured, never accepted"


def test_a_changed_source_is_reported_and_replaced_only_when_forced(brain, capsys):
    """A rewritten transcript is as often damage as growth, so replacing a
    capture is the operator's decision. A settled one is not reported."""
    opened = brain.repo / "chapters" / "one.md"
    _conversation(brain, TIDE, [_step(opened=opened)])
    _sweep(capsys)
    out = _sweep(capsys)
    assert "1 unchanged" in out and "0 differ" in out, out
    before = (brain.pending / f"{TIDE}.jsonl").read_bytes()
    source = _source(brain, TIDE)
    source.write_text(
        source.read_text()
        + json.dumps(_step(text="one more turn", opened=opened))
        + "\n"
    )
    out = _sweep(capsys)
    assert "1 differ" in out and f"{TIDE} is not current" in out, out
    assert (brain.pending / f"{TIDE}.jsonl").read_bytes() == before
    out = _sweep(capsys, "--force")
    assert "1 replaced" in out, out
    assert "one more turn" in (brain.pending / f"{TIDE}.jsonl").read_text()


def test_a_raw_transcript_in_the_pending_area_is_not_a_current_capture(brain, capsys):
    _conversation(brain, TIDE, [_step(opened=brain.repo / "chapters" / "one.md")])
    (brain.pending / f"{TIDE}.jsonl").write_bytes(_source(brain, TIDE).read_bytes())
    assert "1 differ" in _sweep(capsys)


def test_a_transcript_with_no_record_that_parses_is_named_and_the_rest_swept(
    brain, capsys
):
    """One unreadable transcript once stopped the sweep with a bare decode
    error naming no file. There is nothing in it to capture, so it is named,
    and the next one is still captured."""
    _conversation(brain, TIDE, [_step(opened=brain.repo / "chapters" / "one.md")])
    _conversation(
        brain,
        DUNE,
        [
            '{"step_index":0,"source":"MODEL","content":"opened '
            f"{brain.repo / 'chapters' / 'one.md'}",
            '   and the sentence goes on below"}',
        ],
    )
    out = _sweep(capsys)
    assert (brain.pending / f"{TIDE}.jsonl").exists(), out
    assert not (brain.pending / f"{DUNE}.jsonl").exists(), out
    assert f"{DUNE} has no record that parses" in out and "1 unreadable" in out


def test_a_request_written_over_is_captured_from_the_cut_copy(brain, capsys):
    """Antigravity can write a late step over the first record, leaving a
    line that does not parse where the opening request was. The cut-down
    copy beside it still holds the request, so the session is captured with
    it restored, and the sweep says what it repaired."""
    request = _asked("Tighten the opening of chapter one.")
    late = _step(text="done", opened=brain.repo / "chapters" / "one.md", index=52)
    following = _step(text="next", index=1)
    _conversation(
        brain, TIDE, [json.dumps(late), 'ols you may call."}', json.dumps(following)]
    )
    _conversation(brain, TIDE, [request, following, late], name="transcript.jsonl")
    out = _sweep(capsys)
    capture = (brain.pending / f"{TIDE}.jsonl").read_text().splitlines()
    assert "Tighten the opening" in capture[1], out
    assert json.loads(capture[0])["recovered"]["steps"] == [0]
    assert "1 line(s) did not parse" in out and "step(s) 0 taken from" in out


def test_a_session_that_starts_from_a_relative_path_is_not_a_review_run(brain, capsys):
    first = _step(text=f"Working in {brain.repo}.")
    first["tool_calls"] = [
        {
            "name": "run_command",
            "args": {
                "CommandLine": "source .venv/bin/activate && quirework spec notes",
                "Cwd": str(brain.repo),
            },
        }
    ]
    _conversation(brain, TIDE, [first])
    out = _sweep(capsys)
    assert (brain.pending / f"{TIDE}.jsonl").exists(), out
    assert "0 review run(s)" in out


def test_a_capture_lacking_a_step_its_cut_copy_holds_is_reported(brain, capsys):
    """A capture made before the cut copy was read is current by its own
    fingerprint, so the sweep asks the copy too, and replaces the capture
    when forced by its id."""
    kept = _step(opened=brain.repo / "chapters" / "one.md", index=1)
    _conversation(brain, TIDE, [kept])
    _sweep(capsys)
    _conversation(
        brain,
        TIDE,
        [_asked("the request the full copy lost"), kept],
        name="transcript.jsonl",
    )
    out = _sweep(capsys)
    assert "1 differ" in out and "holds steps the capture lacks" in out, out
    out = _sweep(capsys, "--force", DUNE)
    assert "1 differ" in out and "0 replaced" in out, "another id forces nothing"
    out = _sweep(capsys, "--force", TIDE)
    assert "1 replaced" in out, out
    assert (
        "the request the full copy lost"
        in (brain.pending / f"{TIDE}.jsonl").read_text()
    )
