"""What a receipt claims about a seat, and where each claim comes from.

A receipt is only as good as its evidence. Effort and context use are read
from the harness's own session record where it keeps them, and the receipt
says so; how a seat was paid for is stated, because two ways of paying look
alike in a usage block and cost very different amounts.
"""

import json

import pytest

from quirework.providers import harness

POLICY = dict(
    tools="write", read_roots=[], allow_compaction=False, minimum_context_window=0
)
CONFIG = dict(harness="claude", model="fixture-model", effort="high")


def claude_record(tmp_path, *, efforts=("high", "high"), billing=None):
    record = tmp_path / "attempt"
    record.mkdir()
    stream = [
        dict(
            type="system",
            subtype="init",
            session_id="sid",
            mcp_servers=[],
            output_style="default",
        ),
        dict(
            type="assistant",
            message=dict(model="fixture-model", content=[dict(type="text", text="Hi")]),
        ),
        dict(
            type="result",
            subtype="success",
            is_error=False,
            session_id="sid",
            result="The umbrella was green.",
            modelUsage={"fixture-model": dict(outputTokens=40, costUSD=0.25)},
        ),
    ]
    (record / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in stream))
    if billing is not None:
        (record / "launch.json").write_text(json.dumps(dict(billing=billing)))
    private = tmp_path / "private"
    native = private / "projects" / "work" / "sid.jsonl"
    native.parent.mkdir(parents=True)
    rows = []
    for i, effort in enumerate(efforts):
        row = dict(
            type="assistant",
            message=dict(
                usage=dict(
                    input_tokens=10,
                    cache_read_input_tokens=1000 * (i + 1),
                    cache_creation_input_tokens=5,
                )
            ),
        )
        if effort:
            row["effort"] = effort
        rows.append(row)
    native.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return record, private


def test_claude_effort_and_peak_input_come_from_the_session(tmp_path):
    record, private = claude_record(tmp_path, billing="subscription")
    receipt = harness.receipt(CONFIG, POLICY, record, private)
    assert receipt["effort_evidence"] == "native session"
    assert receipt["peak_input"] == 10 + 2000 + 5
    assert receipt["cost_usd"] == 0.25
    assert receipt["billing"] == "subscription"


def test_a_session_run_at_another_effort_is_refused(tmp_path):
    record, private = claude_record(tmp_path, efforts=("high", "medium"))
    with pytest.raises(RuntimeError, match="effort drift"):
        harness.receipt(CONFIG, POLICY, record, private)


def test_a_session_that_records_no_effort_says_it_took_the_arguments(tmp_path):
    record, private = claude_record(tmp_path, efforts=(None, None))
    assert (
        harness.receipt(CONFIG, POLICY, record, private)["effort_evidence"]
        == "CLI selection"
    )


@pytest.mark.parametrize(
    "mode, expected",
    [("apikey", "metered spend"), ("chatgpt", "ChatGPT plan"), (None, "unknown")],
)
def test_codex_billing_names_the_credential_kind_and_never_the_key(
    tmp_path, monkeypatch, mode, expected
):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    auth = {"OPENAI_API_KEY": "sk-fictitious-0000"}
    if mode:
        auth["auth_mode"] = mode
    (home / ".codex" / "auth.json").write_text(json.dumps(auth))
    monkeypatch.setattr(harness.Path, "home", lambda: home)
    said = harness.billing("codex")
    assert expected in said
    assert "sk-" not in said


def test_the_seat_does_not_find_itself_in_a_directory_named_for_the_tool():
    assert "quirework" not in str(harness.VIRTUAL)
    assert not str(harness.VIRTUAL).startswith(str(harness.Path.home()))
