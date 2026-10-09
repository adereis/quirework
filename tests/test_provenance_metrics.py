import json

import pytest

from conftest import corpus_settings, keep_session
from quirework.project import Project, project_scope
from quirework.provenance import metrics

TYPED = "Tighten the kettle scene."

# One typed turn among the model-written text the harness logs in the human's
# slot: a background subagent's report, and a compacted session's summary.
SESSION = [
    {
        "type": "user",
        "timestamp": "2031-04-02T10:00:00Z",
        "message": {"content": TYPED},
    },
    {
        "type": "user",
        "origin": {"kind": "task-notification"},
        "timestamp": "2031-04-02T10:01:00Z",
        "message": {
            "content": "<task-notification>\n<status>completed</status>\n"
            "<result>The kettle whistles twice in chapter 1.</result>\n"
            "</task-notification>"
        },
    },
    {
        "type": "user",
        "isCompactSummary": True,
        "timestamp": "2031-04-02T10:02:00Z",
        "message": {
            "content": "This session is being continued from a previous "
            "conversation. The author wants the kettle scene shorter."
        },
    },
    {
        "type": "assistant",
        "timestamp": "2031-04-02T10:03:00Z",
        "message": {"content": [{"type": "text", "text": "Tightened."}]},
    },
    # A sub-agent the assistant sent to look: its task, its reading, its report.
    {
        "type": "user",
        "isSidechain": True,
        "agentId": "a1b2c3",
        "timestamp": "2031-04-02T10:02:30Z",
        "message": {"content": "Find every kettle scene."},
    },
    {
        "type": "assistant",
        "isSidechain": True,
        "agentId": "a1b2c3",
        "timestamp": "2031-04-02T10:02:40Z",
        "message": {
            "content": [
                {"type": "text", "text": "Two scenes."},
                {"type": "tool_use", "name": "Read", "input": {}},
                {
                    "type": "tool_use",
                    "name": "SubagentHandback",
                    "input": {"message": "Chapters one and four."},
                },
            ]
        },
    },
]


@pytest.fixture(autouse=True)
def corpus(tmp_path):
    """A project whose corpus holds the one session above, accepted."""
    project = Project.from_settings(tmp_path, {"provenance": corpus_settings()})
    keep_session(
        project,
        "cc-sessions/0badc0de-0000-4000-8000-000000000001.jsonl",
        "\n".join(json.dumps(r) for r in SESSION) + "\n",
    )
    with project_scope(project):
        yield project


def test_the_ledger_credits_the_author_only_with_what_they_typed():
    acc = metrics.Acc("cc")
    metrics.claude_code(acc)
    assert acc.human_turns == 1
    assert acc.human_chars == len(TYPED)


def test_the_paste_audit_reads_only_the_authors_turns():
    assert [text for _, text in metrics.human_turns()] == [TYPED]


def test_a_subagents_work_is_counted_apart_from_the_conversation():
    acc = metrics.Acc("cc")
    metrics.claude_code(acc)
    assert (acc.subagents, acc.subagent_tool_calls, acc.subagent_words) == (1, 2, 6)
    # None of it reaches the conversation's own figures.
    assert (acc.human_turns, acc.ai_turns, acc.tool_calls) == (1, 1, 0)
    assert acc.ai_text_words == 1
