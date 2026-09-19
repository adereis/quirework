"""Capture selection honors configured corpora and delegated actions."""

import json
from pathlib import Path

import pytest

from quirework.project import Project, project_scope
from quirework.provenance import codexfilter
from quirework.provenance.ingestion import codex

SID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def selection(tmp_path):
    root = tmp_path / "book"
    sibling = tmp_path / "sibling"
    project = Project.from_settings(
        root,
        {
            "provenance": {
                "claude_sessions": "records/first",
                "agy_sessions": "records/second",
                "codex_sessions": "records/third",
            }
        },
    )
    with project_scope(project):
        yield root, sibling, codex._capture_pattern([root, sibling])


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


@pytest.mark.parametrize("folder", ["first", "second", "third"])
@pytest.mark.parametrize("location", ["absolute", "relative", "sibling"])
def test_a_tool_action_on_configured_captures_is_an_evaluation(
    selection, tmp_path, folder, location
):
    root, sibling, pattern = selection
    base = {"absolute": root, "relative": Path(), "sibling": sibling}[location]
    target = str(base / "records" / folder / f"{SID}.jsonl")
    path = rollout(
        tmp_path, [{"type": "function_call", "input": {"cmd": f"read {target}"}}]
    )
    assert codex.evaluation_seat(path, delegated(), pattern)
    assert not codex.evaluation_seat(path, {"source": "cli"}, pattern)


def test_another_projects_absolute_path_is_not_this_corpus(selection, tmp_path):
    root, _sibling, pattern = selection
    target = root.parent / "unrelated" / "records" / "first" / f"{SID}.jsonl"
    path = rollout(tmp_path, [{"type": "custom_tool_call", "input": f"read {target}"}])
    assert not codex.evaluation_seat(path, delegated(), pattern)


def test_discussing_a_capture_without_a_tool_action_is_kept(selection, tmp_path):
    root, _sibling, pattern = selection
    text = str(root / "records" / "first" / f"{SID}.jsonl")
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
