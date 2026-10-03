"""Capture selection honors the project's store and delegated actions."""

import json
from pathlib import Path

import pytest

from quirework.project import Project, project_scope
from quirework.provenance import codexfilter
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
