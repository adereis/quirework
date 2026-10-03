from conftest import corpus_settings, keep_session
from quirework.project import Project, project_scope
import json

from quirework.provenance import metrics


def _feat(**over):
    base = {
        "fences": 0,
        "heads": 0,
        "tables": 0,
        "bullets": 0,
        "bq_frac": 0.0,
        "relay": False,
        "struct_words": 0,
    }
    base.update(over)
    return base


def test_words():
    assert metrics.words("a b  c\nd") == 4
    assert metrics.words("") == 0


def test_classify_structured_on_fences():
    assert metrics._classify(300, _feat(fences=2), 250) == "STRUCTURED"


def test_classify_structured_on_headings():
    assert metrics._classify(50, _feat(heads=3), 250) == "STRUCTURED"


def test_classify_relay_needs_length():
    assert metrics._classify(150, _feat(relay=True), 250) == "RELAY"
    # a relay phrase in a short turn is not enough
    assert metrics._classify(50, _feat(relay=True), 250) == "TYPED"


def test_classify_relay_on_heavy_blockquote():
    assert metrics._classify(80, _feat(bq_frac=0.5), 250) == "RELAY"


def test_classify_ambiguous_vs_typed():
    assert metrics._classify(300, _feat(), 250) == "AMBIGUOUS"
    assert metrics._classify(100, _feat(), 250) == "TYPED"


def test_features_counts_escaped_blockquotes():
    # the chat client escapes pasted markdown as \> ; the detector must see it
    f = metrics._features("\\> a quoted line\n\\> another quoted line")
    assert f["bq_frac"] == 1.0
    assert f["struct_words"] > 0


def test_features_counts_headings_and_relay():
    f = metrics._features("# A\n## B\n### C\nfrom the other session he said")
    assert f["heads"] == 3
    assert f["relay"] is True


def test_codex_metrics_count_visible_dialogue_not_agent_prompt(tmp_path, monkeypatch):
    corpus = tmp_path / "codex-sessions"
    corpus.mkdir()
    session = corpus / "0c0dec0d-0000-7000-8000-000000000000.jsonl"
    rows = [
        {
            "timestamp": "2026-08-17T22:00:00Z",
            "type": "session_meta",
            "payload": {
                "id": session.stem,
                "source": {"subagent": {"other": "reviewer"}},
            },
        },
        {
            "timestamp": "2026-08-17T22:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Parent-written task"},
                ],
            },
        },
        {
            "timestamp": "2026-08-17T22:00:02Z",
            "type": "turn_context",
            "payload": {"model": "gpt-test"},
        },
        {
            "timestamp": "2026-08-17T22:00:03Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Visible agent answer"},
                ],
            },
        },
        {
            "timestamp": "2026-08-17T22:00:04Z",
            "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec", "input": "x"},
        },
    ]
    session.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    project = Project.from_settings(tmp_path, {"provenance": corpus_settings()})
    keep_session(project, f"codex-sessions/{session.name}", session.read_text())
    acc = metrics.Acc("codex")

    with project_scope(project):
        metrics.codex_sessions(acc)

    assert acc.sessions == 1
    assert acc.human_words == 0
    assert acc.ai_text_words == 3
    assert acc.tool_calls == 1
    assert acc.models == {"gpt-test": 1}
