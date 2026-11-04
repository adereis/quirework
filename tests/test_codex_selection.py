"""Codex capture: what a sweep selects, skips and reports, on synthetic
rollouts in a synthetic Codex home and store."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import capture_project, linked_worktree
from quirework.project import Project, project_scope
from quirework.provenance import codexfilter, store
from quirework.provenance.ingestion import codex

SID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def selection(tmp_path):
    """A project whose store sits outside it, and the pattern it selects by."""
    root = tmp_path / "book"
    project = Project.from_settings(
        root, {"provenance": {"enabled": True, "store_dir": str(tmp_path / "store")}}
    )
    with project_scope(project):
        yield root, tmp_path / "store", codex._capture_pattern()


def rollout(tmp_path, payloads):
    path = tmp_path / "rollout.jsonl"
    path.write_text(
        "".join(
            json.dumps({"type": "response_item", "payload": p}) + "\n" for p in payloads
        )
    )
    return path


def delegated():
    return {"source": {"subagent": {"thread_spawn": {"agent_path": "/root/reader"}}}}


FAMILIES = ["cc-sessions", "agy-sessions", "codex-sessions"]


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("spelling", ["pending", "accepted", "bare"])
def test_a_tool_action_on_a_stored_capture_is_an_evaluation(
    selection, tmp_path, family, spelling
):
    _root, store, pattern = selection
    target = {
        "pending": store / "pending" / family / f"{SID}.jsonl",
        "accepted": store / "accepted" / family / "0123456789abcdef" / f"{SID}.jsonl",
        "bare": Path(family) / f"{SID}.jsonl",
    }[spelling]
    path = rollout(
        tmp_path, [{"type": "function_call", "input": {"cmd": f"read {target}"}}]
    )
    assert codex.evaluation_seat(path, delegated(), pattern)
    assert not codex.evaluation_seat(path, {"source": "cli"}, pattern)


def test_a_store_inside_the_project_is_matched_from_its_root(tmp_path):
    root = tmp_path / "book"
    project = Project.from_settings(
        root, {"provenance": {"enabled": True, "store_dir": "records"}}
    )
    with project_scope(project):
        pattern = codex._capture_pattern()
    target = Path("records") / "pending" / "cc-sessions" / f"{SID}.jsonl"
    path = rollout(
        tmp_path, [{"type": "function_call", "input": {"cmd": f"read {target}"}}]
    )
    assert codex.evaluation_seat(path, delegated(), pattern)


def test_another_projects_absolute_path_is_not_this_corpus(selection, tmp_path):
    root, _store, pattern = selection
    target = root.parent / "unrelated" / "cc-sessions" / f"{SID}.jsonl"
    path = rollout(tmp_path, [{"type": "custom_tool_call", "input": f"read {target}"}])
    assert not codex.evaluation_seat(path, delegated(), pattern)


def test_discussing_a_capture_without_a_tool_action_is_kept(selection, tmp_path):
    _root, store, pattern = selection
    text = str(store / "pending" / "cc-sessions" / f"{SID}.jsonl")
    path = rollout(
        tmp_path,
        [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            }
        ],
    )
    assert not codex.evaluation_seat(path, delegated(), pattern)


def test_tool_inputs_keep_call_order_and_ignore_prose(tmp_path):
    path = rollout(
        tmp_path,
        [
            {"type": "custom_tool_call", "input": "first"},
            {"type": "message", "role": "assistant", "content": "not a call"},
            {"type": "function_call", "input": {"cmd": "second"}},
        ],
    )
    assert list(codexfilter.tool_inputs(path)) == ["first", '{"cmd": "second"}']


# --------------------------------------------------------------------------- #
# the sweep, on a synthetic Codex home
# --------------------------------------------------------------------------- #
ROOT = "0c0dec00-0000-4000-8000-000000000001"
SIDE = "0c0dec00-0000-4000-8000-000000000002"


@pytest.fixture
def rollouts(tmp_path, monkeypatch):
    """A book with a linked worktree, a Codex home, and a store."""
    base = tmp_path.resolve()
    repo = base / "book"
    (repo / ".git").mkdir(parents=True)
    worktree = linked_worktree(repo, base / "book-draft")
    home = base / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    with capture_project(repo, base / "store"):
        yield SimpleNamespace(
            base=base,
            repo=repo,
            worktree=worktree,
            home=home,
            day=home / "sessions" / "2031" / "04" / "02",
            pending=store.open_store().pending_dir(store.CODEX),
        )


def saved(directory, sid, cwd, source="cli"):
    """A rollout as Codex saves it; ``cwd`` of ``None`` records none."""
    payload = {"id": sid, "session_id": sid, "source": source}
    if cwd is not None:
        payload["cwd"] = str(cwd)
    path = directory / f"rollout-2031-04-02T10-00-00-{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "session_meta", "payload": payload}) + "\n")
    return path


def test_a_rollout_recording_no_working_directory_is_counted(rollouts, capsys):
    """It belongs to no project anyone can name, so it is counted rather than
    passed over as another project's."""
    placed = saved(rollouts.day, ROOT, rollouts.repo)
    bare = saved(rollouts.day, SIDE, None)
    keep, _system, _evaluation, unplaced = codex.sweep()
    assert keep == [placed]
    assert unplaced == [bare]
    assert codex.main([]) == 0
    assert "1 recording no working directory" in capsys.readouterr().out


def said(path, payload):
    """One more record in a rollout."""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "response_item", "payload": payload}) + "\n")


THIRD = "0c0dec00-0000-4000-8000-000000000003"
FOURTH = "0c0dec00-0000-4000-8000-000000000004"


def test_the_sweep_reaches_every_checkout_and_stops_at_a_lookalike(rollouts):
    """The linked worktree is a sibling directory, found because git says it
    is a checkout; a sibling whose name merely extends the book's is another
    project."""
    root = saved(rollouts.day, ROOT, rollouts.repo)
    nested = saved(rollouts.day, SIDE, rollouts.worktree / "chapters")
    saved(rollouts.day, THIRD, rollouts.base / "book-elsewhere")
    keep, system, evaluation, unplaced = codex.sweep()
    assert keep == sorted([root, nested])
    assert (system, evaluation, unplaced) == ([], [], [])


def test_codexs_own_risk_judge_is_skipped_and_a_delegation_is_kept(rollouts):
    """A `guardian` rollout is the harness judging a command, not a session;
    a sub-agent the author directed is evidence like any other."""
    root = saved(rollouts.day, ROOT, rollouts.repo)
    guard = saved(
        rollouts.day, SIDE, rollouts.repo, {"subagent": {"other": "guardian"}}
    )
    helper = saved(
        rollouts.day, THIRD, rollouts.repo, {"subagent": {"other": "proofreader"}}
    )
    keep, system, evaluation, _ = codex.sweep()
    assert keep == sorted([root, helper])
    assert system == [guard]
    assert evaluation == []


@pytest.mark.parametrize("area", ["pending", "accepted"])
def test_an_evaluation_seat_is_reported_and_never_captured(rollouts, capsys, area):
    """A delegated seat handed a stored capture would seed the next ingest
    with one transcript per session it judged. The session that delegated
    it opens captures too, and stays."""
    root = store.location()
    target = {
        "pending": root / "pending" / store.CC / f"{FOURTH}.jsonl",
        "accepted": root
        / "accepted"
        / store.CC
        / "0123456789abcdef"
        / f"{FOURTH}.jsonl",
    }[area]
    seat = saved(rollouts.day, SIDE, rollouts.repo, delegated()["source"])
    said(
        seat,
        {
            "type": "function_call",
            "name": "exec_command",
            "input": {"cmd": f"quirework provenance show {target}"},
        },
    )
    driver = saved(rollouts.day, ROOT, rollouts.repo)
    said(
        driver,
        {
            "type": "custom_tool_call",
            "name": "exec",
            "input": f'await tools.exec_command({{"cmd":"cat {target}"}})',
        },
    )
    keep, system, evaluation, _ = codex.sweep()
    assert (keep, system, evaluation) == ([driver], [], [seat])
    assert codex.main([]) == 0
    out = capsys.readouterr().out
    assert f"{SIDE}.jsonl was sent a session capture to evaluate" in out
    assert "1 evaluation seat(s)" in out
    assert not (rollouts.pending / f"{SIDE}.jsonl").exists()


def test_archived_rollouts_are_swept_too(rollouts):
    archived = saved(rollouts.home / "archived_sessions", ROOT, rollouts.repo)
    assert codex.sweep()[0] == [archived]


def test_a_capture_is_named_by_its_id_honours_the_removed_list_and_waits(
    rollouts, capsys
):
    source = saved(rollouts.day, ROOT, rollouts.repo)
    saved(rollouts.day, SIDE, rollouts.repo)
    (store.ledger() / store.REMOVED).write_text(f"codex {SIDE}  # a dry run\n")
    assert codex.main([]) == 0
    out = capsys.readouterr().out
    assert "1 updated" in out and "1 on removed-list" in out
    captured = rollouts.pending / f"{ROOT}.jsonl"
    meta = codexfilter.session_meta(captured)
    assert meta["capture_schema"] == codexfilter.CAPTURE_SCHEMA
    assert meta["source_bytes"] == source.stat().st_size
    assert not (rollouts.pending / f"{SIDE}.jsonl").exists()
    assert store.files(store.CODEX) == [], "captured, never accepted"


def test_an_unknown_argument_is_refused_rather_than_swept_past(rollouts, capsys):
    with pytest.raises(SystemExit) as stopped:
        codex.main(["--hook"])
    assert stopped.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_a_grown_rollout_is_captured_again_and_a_settled_one_is_not(rollouts):
    source = saved(rollouts.day, ROOT, rollouts.repo)
    assert codex.copy_if_changed(source, ROOT) is True
    assert codex.copy_if_changed(source, ROOT) is False
    said(source, {"type": "message", "role": "user", "content": "One more."})
    assert codex.copy_if_changed(source, ROOT) is True
    captured = rollouts.pending / f"{ROOT}.jsonl"
    assert codexfilter.session_meta(captured)["source_bytes"] == source.stat().st_size
    assert not list(rollouts.pending.glob(f".{ROOT}.jsonl.*")), "no temporary file"


def test_an_edit_that_keeps_the_size_is_still_seen(rollouts):
    source = saved(rollouts.day, ROOT, rollouts.repo)
    assert codex.copy_if_changed(source, ROOT) is True
    captured = rollouts.pending / f"{ROOT}.jsonl"
    before = codexfilter.session_meta(captured)["source_sha256"]
    raw = source.read_text()
    source.write_text(raw.replace('"source": "cli"', '"source": "tui"'))
    assert source.stat().st_size == len(raw.encode())
    assert codex.copy_if_changed(source, ROOT) is True
    assert codexfilter.session_meta(captured)["source_sha256"] != before


def test_a_capture_of_an_older_schema_is_made_again(rollouts):
    """Nothing in an older capture's bytes says what it misreads, so its
    schema is the tell: it is no capture, and the rollout is normalized
    again rather than called current."""
    source = saved(rollouts.day, ROOT, rollouts.repo)
    assert codex.copy_if_changed(source, ROOT) is True
    captured = rollouts.pending / f"{ROOT}.jsonl"
    current = captured.read_bytes()
    schema = codexfilter.CAPTURE_SCHEMA.encode()
    captured.write_bytes(current.replace(schema, b"quirework-codex-v0"))
    assert codex.captured_source_fingerprint(captured) is None
    assert codex.copy_if_changed(source, ROOT) is True
    assert captured.read_bytes() == current
