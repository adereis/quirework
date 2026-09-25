import json

from quirework.project import current_project
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
]


def _place_session():
    folder = current_project().path(
        "provenance.claude_sessions", ".quirework/provenance/cc-sessions"
    )
    folder.mkdir(parents=True)
    path = folder / "0badc0de-0000-4000-8000-000000000001.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in SESSION) + "\n", encoding="utf-8")


def test_the_ledger_credits_the_author_only_with_what_they_typed():
    _place_session()
    acc = metrics.Acc("cc")
    metrics.claude_code(acc)
    assert acc.human_turns == 1
    assert acc.human_chars == len(TYPED)


def test_the_paste_audit_reads_only_the_authors_turns():
    _place_session()
    assert [text for _, text in metrics.human_turns()] == [TYPED]
