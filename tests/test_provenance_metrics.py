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


def _reply(model, effort=None, **extra):
    """One assistant record naming its model, and its effort when given."""
    record = {
        "type": "assistant",
        "timestamp": "2031-04-03T10:00:00Z",
        "message": {"model": model, "content": [{"type": "text", "text": "Done."}]},
        **extra,
    }
    if effort is not None:
        record["effort"] = effort
    return record


def _keep_cc(project, n, records):
    keep_session(
        project,
        f"cc-sessions/0badc0de-0000-4000-8000-00000000001{n}.jsonl",
        "\n".join(json.dumps(r) for r in records) + "\n",
    )


def _report(capsys):
    code = metrics.Acc("code")
    metrics.claude_code(code)
    accs = (metrics.Acc("web"), code, metrics.Acc("agy"), metrics.Acc("codex"))
    accs += (metrics.Acc("gemini"),)
    total = metrics.Acc("TOTAL")
    for acc in accs:
        total.merge(acc)
    metrics.report(accs, total)
    return capsys.readouterr().out


def test_effort_is_counted_beside_the_model_it_was_asked_of(corpus):
    _keep_cc(
        corpus,
        1,
        [
            _reply("fixture-large", "high"),
            _reply("fixture-large", "high"),
            _reply("fixture-large", "low"),
            # A release that recorded no effort leaves the record uncounted.
            _reply("fixture-small"),
            # A sub-agent's record is delegated work, outside the model table.
            _reply("fixture-small", "low", isSidechain=True, agentId="d4e5f6"),
        ],
    )
    acc = metrics.Acc("cc")
    metrics.claude_code(acc)
    assert acc.models == {"fixture-large": 3, "fixture-small": 1}
    assert acc.efforts == {("fixture-large", "high"): 2, ("fixture-large", "low"): 1}


def test_codex_counts_the_effort_of_each_turn(corpus):
    rows = [
        {"type": "turn_context", "payload": {"model": "fixture-codex", "effort": e}}
        for e in ("xhigh", "xhigh", None)
    ]
    keep_session(
        corpus,
        "codex-sessions/0c0dec0d-0000-7000-8000-000000000001.jsonl",
        "\n".join(json.dumps(r) for r in rows) + "\n",
    )
    acc = metrics.Acc("codex")
    metrics.codex_sessions(acc)
    assert acc.models == {"fixture-codex": 3}
    assert acc.efforts == {("fixture-codex", "xhigh"): 2}


def test_merging_buckets_adds_their_efforts():
    one, two = metrics.Acc("one"), metrics.Acc("two")
    one.model("fixture-large", "high")
    two.model("fixture-large", "high")
    two.model("fixture-large", "low")
    one.merge(two)
    assert one.efforts == {("fixture-large", "high"): 2, ("fixture-large", "low"): 1}


def test_the_report_shows_effort_by_model_with_its_coverage(corpus, capsys):
    _keep_cc(
        corpus,
        1,
        [
            _reply("fixture-large", "high"),
            _reply("fixture-large", "high"),
            _reply("fixture-large", "low"),
            _reply("fixture-small"),
        ],
    )
    out = _report(capsys)
    table = out[out.index("Effort (") :].splitlines()
    assert table[1].split() == ["fixture-large", "high", "2"]
    # The model is named once, on the first of its rows.
    assert table[2].split() == ["low", "1"]
    assert "3 of 4 records" in out


def test_a_corpus_without_effort_prints_the_report_it_always_did(corpus, capsys):
    _keep_cc(corpus, 1, [_reply("fixture-large"), _reply("fixture-small")])
    out = _report(capsys)
    assert "Models (" in out
    assert "Effort (" not in out
    # The model table's closing caveat is still the report's last line.
    assert out.rstrip().endswith("take it from the human or the harness, and say so.")
