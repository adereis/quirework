"""Catalog unit tests — pure helpers only, synthetic inputs, no private corpus.

The build()/check() flow reads git + the real corpus, so it is exercised by
running `quirework provenance catalog` for real; here we cover the deterministic pieces
that make `--check` trustworthy: metadata scanners, the editorial merge, the
removed-list parser, and stable rendering.
"""

from conftest import corpus_settings
from quirework.runs import commands as run_commands
import json

from quirework.project import Project, project_scope
from quirework.provenance import catalog


def test_short_model_strips_prefix_and_handles_synthetic():
    assert catalog._short_model("claude-opus-4-8") == "opus-4-8"
    assert catalog._short_model("Gemini Flash") == "Gemini Flash"
    assert catalog._short_model("<synthetic>") == "?"
    assert catalog._short_model(None) == "?"


def test_short_id_keeps_gemini_whole_but_truncates_uuids():
    assert catalog._short_id("4a4a4a4a-1111-4222-8333-444455556666") == "4a4a4a4a"
    assert catalog._short_id("gemini-1051738817f6bf35") == "gemini-1051738817f6bf35"


def test_tok_compacts_thousands():
    assert catalog._tok(64) == "64"
    assert catalog._tok(999) == "999"
    assert catalog._tok(1000) == "1k"
    assert catalog._tok(31999) == "31k"


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_cc_scan_reads_title_dates_and_primary_model(tmp_path):
    p = tmp_path / "s.jsonl"
    _write_jsonl(
        p,
        [
            {
                "type": "assistant",
                "timestamp": "2026-06-20T01:00:00Z",
                "message": {"model": "claude-opus-4-8"},
            },
            {"type": "ai-title", "aiTitle": "Rewrite the voice"},
            {
                "type": "assistant",
                "timestamp": "2026-06-21T05:00:00Z",
                "message": {"model": "claude-opus-4-8"},
            },
            {"type": "assistant", "message": {"model": "<synthetic>"}},  # ignored
        ],
    )
    sc = catalog._cc_scan(p)
    assert sc["title"] == "Rewrite the voice"
    assert sc["date"] == "2026-06-20"  # first timestamp wins
    assert sc["date_end"] == "2026-06-21"  # last timestamp wins
    assert sc["model"] == "opus-4-8"


def test_cc_scan_falls_back_to_slug_then_untitled(tmp_path):
    p = tmp_path / "slug.jsonl"
    _write_jsonl(p, [{"type": "x", "slug": "draft-next-section"}])
    assert catalog._cc_scan(p)["title"] == "draft next section"

    p2 = tmp_path / "bare.jsonl"
    _write_jsonl(p2, [{"type": "x"}])
    sc = catalog._cc_scan(p2)
    assert sc["title"] == "(untitled)"
    assert sc["date"] == "?" and sc["model"] == "?"


def test_web_scan_claude_header(tmp_path):
    p = tmp_path / "c.md"
    p.write_text(
        "# Harbor Discussion\n\n"
        "| | |\n|---|---|\n"
        "| Source | https://claude.ai/chat/abc (private) |\n"
        "| Model (final setting) | `claude-opus-4-6` |\n"
        "| Created | 2026-04-04 13:39:45 UTC |\n"
        "| Messages | 84 (42 human / 42 assistant) |\n",
        encoding="utf-8",
    )
    sc = catalog._web_scan(p)
    assert sc == {
        "title": "Harbor Discussion",
        "date": "2026-04-04",
        "model": "opus-4-6",
        "messages": 84,
    }


def test_web_scan_gemini_header_fallbacks(tmp_path):
    p = tmp_path / "gemini-x.md"
    p.write_text(
        "# Google Gemini\n\n"
        "| | |\n|---|---|\n"
        "| Platform | Google Gemini · Gemini Flash |\n"
        "| Turns | 2 exchanges |\n"
        "| Date | 2026-04-04 (author-supplied; not exposed by Gemini DOM) |\n",
        encoding="utf-8",
    )
    sc = catalog._web_scan(p)
    assert sc["date"] == "2026-04-04"
    assert sc["model"] == "Gemini Flash"
    assert sc["messages"] == 2


def test_removed_parses_kind_id_and_reason(tmp_path):
    (tmp_path / "ledger").mkdir()
    (tmp_path / "ledger" / "removed-sessions.txt").write_text(
        "# header comment\n"
        "cc 7e7e7e7e-99a8-4f4d-ae91-f1380f5ece9f      # web-capture pipeline (pure tooling)\n"
        "\n"
        'cc e9bf8a21-6a2c-402c-a1f8-b9452033523c      # stub: single "log" message\n'
        "agy d617165b-e873-40c1-aec4-206cf4eedc23\n",
        encoding="utf-8",
    )
    project = Project.from_settings(
        tmp_path, {"provenance": corpus_settings(root="ledger")}
    )
    with project_scope(project):
        out = catalog._removed()
    assert out == [
        {
            "id": "7e7e7e7e-99a8-4f4d-ae91-f1380f5ece9f",
            "reason": "web-capture pipeline (pure tooling)",
            "corpus": "cc",
        },
        {
            "id": "e9bf8a21-6a2c-402c-a1f8-b9452033523c",
            "reason": 'stub: single "log" message',
            "corpus": "cc",
        },
        {"id": "d617165b-e873-40c1-aec4-206cf4eedc23", "reason": "", "corpus": "agy"},
    ]


def test_merge_editorial_preserves_human_fields():
    entries = {"p": {"id": "p", "note": "", "tags": []}}
    existing = {"p": {"note": "the whistle", "tags": ["relational"], "tokens": 999}}
    catalog._merge_editorial(entries, existing)
    assert entries["p"]["note"] == "the whistle"
    assert entries["p"]["tags"] == ["relational"]
    assert "tokens" not in entries["p"]  # only EDITORIAL keys cross over


def _sample_index():
    return {
        "schema": 1,
        "filter_version": 1,
        "codex_filter_version": 1,
        "sources": {
            ".quirework/provenance/cc-sessions/abcd1234-….jsonl": {
                "id": "abcd1234-0000-0000-0000-000000000000",
                "era": "c-refinement",
                "title": "Rewrite | voice",
                "date": "2026-06-20",
                "date_end": "2026-06-20",
                "model": "opus-4-8",
                "human_turns": 1,
                "assistant_turns": 2,
                "tokens": 31000,
                "doc_edits": 5,
                "meta_edits": 2,
                "top_edited": [],
                "sha256": "x",
                "coldread": True,
                "highlight": True,
                "note": "",
                "tags": [],
            },
            ".quirework/provenance/conversations/5e5e5e5e-….md": {
                "id": "5e5e5e5e-0000-0000-0000-000000000000",
                "era": "a-web",
                "title": "Tide tables",
                "date": "2025-12-15",
                "model": "fable-5",
                "messages": 96,
                "tokens": 82000,
                "sha256": "y",
                "coldread": False,
                "highlight": False,
                "note": "",
                "tags": [],
            },
            ".quirework/provenance/codex-sessions/0c0dec0d-….jsonl": {
                "id": "0c0dec0d-0000-0000-0000-000000000000",
                "era": "d-codex",
                "title": "Review the harbor",
                "date": "2026-08-17",
                "date_end": "2026-08-17",
                "model": "gpt-5.6-sol",
                "source_kind": "reviewer",
                "human_turns": 0,
                "assistant_turns": 2,
                "tokens": 1200,
                "doc_edits": 1,
                "meta_edits": 0,
                "top_edited": [],
                "sha256": "z",
                "coldread": False,
                "highlight": False,
                "note": "",
                "tags": [],
            },
        },
        "removed": [{"id": "7e7e7e7e-0000", "reason": "pure tooling"}],
    }


def test_render_md_is_deterministic_and_complete():
    idx = _sample_index()
    md = catalog.render_md(idx)
    assert md == catalog.render_md(idx)  # stable: no clock, no ordering wobble
    assert "31k" in md and "82k" in md  # compact tokens
    assert "Rewrite \\| voice" in md  # pipe escaped for the table
    assert "★" in md  # highlighted cc row
    assert "| — |" in md or "—" in md  # web row not cold-read
    assert "pure tooling" in md  # curated-out reason rendered
    assert "Codex sessions" in md and "reviewer" in md


def test_dump_json_is_deterministic():
    idx = _sample_index()
    assert catalog._dump_json(idx) == catalog._dump_json(idx)
    assert catalog._dump_json(idx).endswith("\n")


def test_main_rejects_unknown_args():
    assert catalog.main(["bogus"]) == 2
