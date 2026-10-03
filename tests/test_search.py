"""Search unit tests — pure logic, synthetic turns, no private corpus.

`search()` takes injectable `turns`/`highlights` so the matching, grouping,
counting, whole-word vs substring behavior, and catalog-metadata reporting are
all exercised without touching the real corpus or git.
"""

from datetime import datetime, timezone

import numpy as np
import pytest

from conftest import keep_session
from quirework.provenance import search

pytestmark = pytest.mark.usefixtures("corpus_project")


def _dt(day):
    return datetime(2026, 6, day, 12, 0, tzinfo=timezone.utc)


def _turn(source, file, text, ts=None):
    return {"source": source, "file": file, "text": text, "ts": ts}


def test_resolve_maps_each_source_to_id_path_era(corpus_project):
    def kept(reference):
        return keep_session(corpus_project, reference, "{}\n")

    cc = kept("cc-sessions/x.jsonl")
    gemini = kept("conversations/gemini-abc.md")
    codex = kept("codex-sessions/z.jsonl")
    md = kept("conversations/y.md")
    assert search._resolve("cc", "x.jsonl") == ("x", cc, "c-refinement")
    assert search._resolve("gemini", "gemini-abc.md") == ("gemini-abc", gemini, "a-web")
    assert search._resolve("codex", "z.jsonl") == ("z", codex, "d-codex")
    # web turns come from the .raw.json, but the catalog titles the .md sibling.
    assert search._resolve("web", "y.raw.json") == ("y", md, "a-web")
    # a session the store does not hold has no file to title it by
    assert search._resolve("cc", "w.jsonl") == ("w", None, "c-refinement")


def test_compile_whole_word_vs_substring():
    ((_, ww),) = search._compile(["Nansen"], substr=False, regex=False)
    assert ww.search("the Nansen passage")
    assert not ww.search("Nansenesque musings")  # whole-word: no partial hit
    ((_, ss),) = search._compile(["Nansen"], substr=True, regex=False)
    assert ss.search("Nansenesque musings")  # substring: partial hits
    ((_, rx),) = search._compile([r"sval\w+"], substr=False, regex=True)
    assert rx.search("Svalbard straight")


def test_snippet_trims_and_marks_elision():
    text = "x" * 50 + " TARGET " + "y" * 50
    ((_, rx),) = search._compile(["TARGET"], substr=False, regex=False)
    snip = search._snippet(text, rx.search(text), width=10)
    assert "TARGET" in snip and snip.startswith("…") and snip.endswith("…")


def test_search_counts_groups_stars_and_orders():
    turns = [
        _turn(
            "cc",
            "sess-a.jsonl",
            "I think the Nansen passage is the key example",
            _dt(20),
        ),
        _turn(
            "cc", "sess-a.jsonl", "More on Nansen, and the Svalbard strait too", _dt(21)
        ),
        _turn("cc", "sess-b.jsonl", "nothing relevant in this turn", _dt(19)),
        _turn(
            "web", "c.raw.json", "I want to bring in ABC by the harbor pilot", _dt(18)
        ),
        _turn(
            "cc", "sess-a.jsonl", "Nansenesque tangents do not count", _dt(22)
        ),  # whole-word
    ]
    hits, n_searched = search.search(
        ["Nansen", "Svalbard", "ABC"],
        turns=turns,
        highlights="featured session: sess-a is a highlight",
    )
    assert n_searched == 3  # three (source, file) groups
    assert [h["id"] for h in hits] == ["sess-a", "c"]  # date desc, sess-b unmatched

    a = hits[0]
    assert a["counts"] == {"Nansen": 2, "Svalbard": 1}  # 'Nansenesque' excluded
    assert a["star"] is True
    assert a["date"] == "2026-06-20"  # session's earliest turn (day 20/21/22)
    assert a["era"] == "c-refinement"

    web = hits[1]
    assert (
        web["counts"] == {"ABC": 1} and web["era"] == "a-web" and web["star"] is False
    )


def test_search_no_keywords_returns_usage_error():
    assert search.main(["--show"]) == 2


# --------------------------------------------------------------------------- #
# option parsing
# --------------------------------------------------------------------------- #
def test_parse_collects_keywords_and_flags():
    kws, o = search._parse(["Nansen", "--substr", "Svalbard", "--show"])
    assert kws == ["Nansen", "Svalbard"]
    assert o["substr"] and o["show"]
    assert not o["regex"] and not o["semantic"]
    assert o["top"] == search.DEFAULT_TOP


@pytest.mark.parametrize("argv", [["q", "--top", "3"], ["q", "--top=3"]])
def test_parse_accepts_both_top_spellings(argv):
    _, o = search._parse(argv)
    assert o["top"] == 3


@pytest.mark.parametrize(
    "argv, msg",
    [
        (["q", "--top"], "needs a number"),
        (["q", "--top", "many"], "needs a number"),
        (["q", "--top", "0"], "at least 1"),
        (["q", "--substring"], "unknown option"),
    ],
)
def test_parse_rejects_bad_options(argv, msg):
    with pytest.raises(ValueError, match=msg):
        search._parse(argv)


def test_semantic_rejects_the_text_matching_flags():
    """--substr/--regex mean nothing against an embedding; honoring them
    silently would answer a different question than the one asked."""
    assert search.main(["--semantic", "--substr", "the harbor"]) == 2
    assert search.main(["--semantic", "--regex", "the harbor"]) == 2


# --------------------------------------------------------------------------- #
# semantic hits share the keyword hit shape
# --------------------------------------------------------------------------- #
class _Backend:
    id = "fake:test"

    def encode_query(self, text):
        return np.array([1.0, 0.0], dtype=np.float32)


def _fake_index():
    return {
        "vecs": np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
        "meta": [
            {
                "source": "cc",
                "file": "sess-a.jsonl",
                "ordinal": 3,
                "chunk": 0,
                "ts": None,
                "text": "the passage that matched",
            },
            {
                "source": "web",
                "file": "c.raw.json",
                "ordinal": 1,
                "chunk": 0,
                "ts": None,
                "text": "unrelated",
            },
        ],
    }


def test_semantic_search_returns_identifying_fields_like_keyword_mode():
    hits, n = search.semantic_search(
        "anything",
        index=_fake_index(),
        backend=_Backend(),
        highlights="sess-a is a highlight",
    )
    assert n == 2
    top = hits[0]
    assert top["id"] == "sess-a" and top["era"] == "c-refinement"
    assert top["star"] is True
    assert top["score"] == pytest.approx(1.0)
    assert top["snippet"] == "the passage that matched" and top["ordinal"] == 3
    # Same identifying keys as a keyword hit, so one printer serves both.
    assert {"id", "era", "date", "title", "star"} <= set(top)


def test_print_hits_shows_score_and_evidence_in_semantic_mode(capsys):
    hits, _ = search.semantic_search(
        "anything", index=_fake_index(), backend=_Backend(), highlights=""
    )
    search._print_hits(hits[:1], semantic=True, show=False)
    out = capsys.readouterr().out
    assert "1.000" in out  # the score
    assert "turn 3: the passage that matched" in out  # readable evidence


def _ranked(scores):
    return [
        {
            "id": f"s{i}",
            "era": "c-refinement",
            "date": "2026-06-01",
            "title": "t",
            "star": False,
            "score": s,
            "windows": 1,
            "snippet": "x",
            "ordinal": 1,
        }
        for i, s in enumerate(scores)
    ]


def _run(monkeypatch, hits):
    monkeypatch.setattr(search, "semantic_search", lambda *a, **k: (hits, 3288))
    return search._run_semantic(
        "q", {"top": len(hits), "rebuild": False, "show": False, "utc": False}
    )


def test_a_degenerate_ranking_says_it_barely_separated(monkeypatch, capsys):
    """A near-flat band means the order approaches arbitrary — say so, rather
    than let a confident-looking list stand in for an answer. (Real query:
    an abstract one about the author's own doubts, spread 0.016.)"""
    _run(monkeypatch, _ranked([0.722, 0.717, 0.709, 0.706]))
    assert "barely separated" in capsys.readouterr().out


@pytest.mark.parametrize(
    "scores, case",
    [
        ([0.711, 0.665, 0.652, 0.624], "nansen's passage, spread 0.087"),
        ([0.815, 0.811, 0.805, 0.778], "the china brain, spread 0.037"),
    ],
)
def test_a_discriminating_ranking_gets_no_note(monkeypatch, capsys, scores, case):
    """Both of these returned genuinely on-topic sessions, so neither may be
    flagged: a false caution teaches the operator to ignore the real one."""
    _run(monkeypatch, _ranked(scores))
    assert "barely separated" not in capsys.readouterr().out, case


def test_too_few_hits_to_judge_spread_gets_no_note(monkeypatch, capsys):
    _run(monkeypatch, _ranked([0.72, 0.71]))
    assert "barely separated" not in capsys.readouterr().out


def test_tidy_collapses_whitespace_and_elides():
    assert search._tidy("a   b\n\nc", 40) == "a b c"
    assert search._tidy("x" * 50, 10).endswith("…")
    assert len(search._tidy("x" * 50, 10)) == 10
