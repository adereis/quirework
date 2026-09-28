from conftest import patch_project_path
import json

from quirework.provenance import humaninput as hi


def test_clean_strips_harness_wrappers():
    assert (
        hi._clean("<system-reminder>noise</system-reminder>real prose here")
        == "real prose here"
    )
    assert hi._clean("") == ""
    # a turn that is nothing but a harness wrapper collapses to empty
    assert hi._clean("<command-name>/clear</command-name>") == ""


def test_clean_drops_harness_prefixed_turns():
    # cc-session lines that open with a harness marker are not authored prose
    assert hi._clean("<command-message>foo</command-message>") == ""


def test_trivial_acks_and_slash_commands():
    for s in ("yes", "ok", "go on", "perfect, thanks", "/clear", "do it please"):
        cls, _w, _a = hi._classify_turn(s)
        assert cls == "TRIVIAL", s


def test_substantive_prose_is_typed():
    cls, w, authored = hi._classify_turn(
        "I think a harbor is best modeled as a tide of tides, "
        "a basin whose own level is part of what it records."
    )
    assert cls == "TYPED"
    assert w > 4
    assert authored == w  # no quoted/fenced lines -> all authored


def test_structured_paste_flagged():
    cls, _w, _a = hi._classify_turn(
        "# Heading\n## Sub\n### Another\nbody text follows here"
    )
    assert cls == "STRUCTURED"


def test_split_authored_excludes_quotes_and_fences():
    text = (
        "This is my own argument and it runs on for a while.\n"
        "> a quoted line I am responding to\n"
        "```\npasted code I did not write\n```\n"
        "And here is more of my own composed commentary."
    )
    authored, quoted = hi._split_authored(text)
    assert quoted > 0
    assert authored == hi.words(text) - quoted


def test_review_is_authored_commentary_around_a_quote():
    # a course-correction: mostly the human's own words, with a short quote of
    # the passage being critiqued. Must be REVIEW (authored), never RELAY.
    own = (
        "I went through this section and I do not think it holds. The claim "
        "conflates two different notions and the example undercuts the point "
        "rather than supporting it, so we should restructure it as follows. "
    ) * 4
    cls, w, authored = hi._classify_turn(
        own + "\n> the short passage I am quoting here\n"
    )
    assert cls == "REVIEW"
    assert authored / w >= 0.5


def test_relay_when_quoted_material_dominates():
    quoted = "\n".join(
        "> " + ("ferried sentence number %d here" % i) for i in range(40)
    )
    cls, _w, _a = hi._classify_turn("Here is the feedback:\n" + quoted)
    assert cls == "RELAY"


def test_long_plain_prose_is_ambiguous():
    cls, _w, _a = hi._classify_turn("word " * 300)
    assert cls == "AMBIGUOUS"


def _turn(cls, words, text="x"):
    return {"cls": cls, "words": words, "text": text}


def test_filter_typed_only():
    turns = [
        _turn("TYPED", 10),
        _turn("REVIEW", 400),
        _turn("RELAY", 200),
        _turn("STRUCTURED", 300),
        _turn("AMBIGUOUS", 280),
        _turn("TRIVIAL", 1),
    ]
    kept = hi._filter(turns, typed_only=True, include_trivial=False, min_words=0)
    assert {t["cls"] for t in kept} == {"TYPED", "REVIEW", "AMBIGUOUS"}


def test_filter_drops_trivial_by_default():
    turns = [_turn("TYPED", 10), _turn("TRIVIAL", 1)]
    assert len(hi._filter(turns, False, False, 0)) == 1
    assert len(hi._filter(turns, False, True, 0)) == 2


def test_filter_min_words():
    turns = [_turn("TYPED", 3), _turn("TYPED", 50)]
    kept = hi._filter(turns, False, False, min_words=10)
    assert len(kept) == 1 and kept[0]["words"] == 50


def test_sessions_order_dated_before_undated():
    from datetime import datetime, timezone

    t_old = {
        "source": "web",
        "file": "a",
        "sid": "a",
        "words": 5,
        "cls": "TYPED",
        "ts": datetime(2025, 1, 1, tzinfo=timezone.utc),
    }
    t_new = {
        "source": "cc",
        "file": "b",
        "sid": "b",
        "words": 5,
        "cls": "TYPED",
        "ts": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    t_undated = {
        "source": "gemini",
        "file": "c",
        "sid": "c",
        "words": 5,
        "cls": "TYPED",
        "ts": None,
    }
    sess = hi._sessions([t_new, t_undated, t_old])
    assert [s["file"] for s in sess] == ["a", "b", "c"]


def test_codex_turns_exclude_injected_context_and_subagent_prompt(
    tmp_path, monkeypatch
):
    corpus = tmp_path / "codex-sessions"
    corpus.mkdir()
    main = corpus / "0c0dec0d-0000-7000-8000-000000000000.jsonl"
    sub = corpus / "0c0dec0d-0000-7000-8000-000000000001.jsonl"

    def rows(sid, source, prompt):
        return [
            {
                "timestamp": "2026-08-17T22:00:00Z",
                "type": "session_meta",
                "payload": {"id": sid, "source": source},
            },
            {
                "timestamp": "2026-08-17T22:00:01Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                    ],
                },
            },
        ]

    injected = "# AGENTS.md instructions for /repo\n<environment_context>x"
    main_rows = (
        rows(main.stem, "cli", injected)
        + rows(
            main.stem, "cli", "This actual human prompt carries enough words to count."
        )[1:]
    )
    sub_rows = rows(
        sub.stem,
        {"subagent": {"other": "reviewer"}},
        "This task came from the parent agent, not the human.",
    )
    main.write_text("\n".join(json.dumps(r) for r in main_rows) + "\n")
    sub.write_text("\n".join(json.dumps(r) for r in sub_rows) + "\n")
    patch_project_path(monkeypatch, hi, "provenance.root", tmp_path)
    patch_project_path(
        monkeypatch, hi, "provenance.codex_sessions", tmp_path / "codex-sessions"
    )

    turns = hi._codex_turns()
    assert [turn["text"] for turn in turns] == [
        "This actual human prompt carries enough words to count."
    ]
    assert turns[0]["source"] == "codex"
