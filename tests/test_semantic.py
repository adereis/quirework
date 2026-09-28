"""Semantic-search unit tests — pure logic, synthetic turns, no corpus, no network.

The embedding backend is injectable, so chunking, index round-tripping,
staleness/model invalidation and the session ranking are all exercised against a
deterministic toy embedder. Nothing here downloads a model or reads `private/`.
"""

from conftest import project_at, patch_setting, patch_project_path
from quirework.runs import commands as run_commands
import json

import numpy as np
import pytest

from quirework.provenance import semantic as SEM


class FakeBackend:
    """Toy embeddings: one dimension per marker word, so similarity is legible.

    A text's vector is its marker-word counts, normalized. That makes the
    expected ranking arithmetic rather than a matter of model behavior.
    """

    id = "fake:test-v1"
    VOCAB = ("tide", "nansen", "kelp")

    def _vec(self, text):
        low = text.lower()
        v = np.array([low.count(w) for w in self.VOCAB], dtype=np.float32)
        if not v.any():
            v = np.full(len(self.VOCAB), 0.01, dtype=np.float32)
        return v / np.linalg.norm(v)

    def encode(self, texts):
        return np.asarray([self._vec(t) for t in texts], dtype=np.float32)

    def encode_query(self, text):
        return self._vec(text)


def _turn(file, text, ordinal=1, source="cc", cls="TYPED"):
    return {
        "source": source,
        "file": file,
        "text": text,
        "ordinal": ordinal,
        "cls": cls,
        "ts": None,
    }


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point the index and the fingerprint's corpus globs at a temp dir.

    ``humaninput.all_turns`` is stubbed empty as well: it resolves the corpus
    from ``_paths`` directly, so without this a test that triggers a rebuild
    reads the real ``private/`` corpus -- slow, and not a unit test.
    """
    from quirework.provenance import humaninput as HI

    for name in ("CC", "AGY", "CODEX", "CONV"):
        d = tmp_path / name
        d.mkdir()
        patch_project_path(
            monkeypatch,
            SEM,
            {
                "CC": "provenance.claude_sessions",
                "AGY": "provenance.agy_sessions",
                "CODEX": "provenance.codex_sessions",
                "CONV": "provenance.conversations",
            }[name],
            d,
        )
    patch_setting(monkeypatch, SEM, "_settings", "INDEX", tmp_path / "index.npz")
    monkeypatch.setattr(HI, "all_turns", lambda: [])
    return tmp_path


# --------------------------------------------------------------------------- #
# chunking
# --------------------------------------------------------------------------- #
def test_chunk_leaves_short_text_whole():
    assert SEM.chunk("three little words") == ["three little words"]


def test_chunk_windows_long_text_with_overlap_and_loses_nothing():
    words = [f"w{i}" for i in range(500)]
    chunks = SEM.chunk(" ".join(words), size=100, overlap=25)
    assert len(chunks) > 1
    assert chunks[0].split()[0] == "w0"
    # Every word survives somewhere: truncation is the failure this prevents.
    assert set(" ".join(chunks).split()) == set(words)
    # Consecutive windows overlap, so a thought on a boundary is findable twice.
    assert set(chunks[0].split()) & set(chunks[1].split())


def test_chunk_covers_the_tail_of_an_uneven_split():
    chunks = SEM.chunk(" ".join(f"w{i}" for i in range(230)), size=100, overlap=25)
    assert chunks[-1].split()[-1] == "w229"


# --------------------------------------------------------------------------- #
# index build / load
# --------------------------------------------------------------------------- #
def test_build_indexes_turns_and_skips_trivial(sandbox):
    turns = [
        _turn("a.jsonl", "the tide and the tide again"),
        _turn("b.jsonl", "nansen in the passage", ordinal=2),
        _turn("c.jsonl", "yes", cls="TRIVIAL"),
    ]
    idx = SEM.build(backend=FakeBackend(), turns=turns, verbose=False)
    assert idx["vecs"].shape == (2, 3)  # TRIVIAL dropped
    assert {m["file"] for m in idx["meta"]} == {"a.jsonl", "b.jsonl"}
    # Vectors are stored normalized, so the query is a plain dot product.
    assert np.allclose(np.linalg.norm(idx["vecs"], axis=1), 1.0)


def test_codex_turn_resolves_to_codex_corpus(sandbox):
    codex_file = (
        SEM.current_project().path("provenance.codex_sessions") / "session.jsonl"
    )
    codex_file.write_text("codex rollout")
    assert SEM._path("codex", codex_file.name) == codex_file
    assert SEM._sizes([("codex", codex_file.name)]) == {
        "codex/session.jsonl": len("codex rollout")
    }


def test_build_raises_when_nothing_indexable(sandbox):
    with pytest.raises(RuntimeError, match="no human turns"):
        SEM.build(
            backend=FakeBackend(),
            turns=[_turn("a.jsonl", "ok", cls="TRIVIAL")],
            verbose=False,
        )


def test_load_round_trips_without_rebuilding(sandbox):
    turns = [_turn("a.jsonl", "the tide")]
    SEM.build(backend=FakeBackend(), turns=turns, verbose=False)
    # No `turns` passed: a rebuild here would scan the (empty) corpus and raise,
    # so returning an index at all proves it was read from disk.
    idx = SEM.load(backend=FakeBackend(), verbose=False)
    assert idx["model"] == "fake:test-v1"
    assert len(idx["meta"]) == 1


def test_load_rebuilds_when_the_model_changed(sandbox):
    SEM.build(
        backend=FakeBackend(), turns=[_turn("a.jsonl", "the tide")], verbose=False
    )

    class Other(FakeBackend):
        id = "fake:test-v2"

    # Vectors from two models are not comparable, so this must not be served
    # from cache -- with an empty corpus, the rebuild it forces raises.
    with pytest.raises(RuntimeError, match="no human turns"):
        SEM.load(backend=Other(), verbose=False)


def test_load_rebuilds_when_the_corpus_changed(sandbox):
    SEM.build(
        backend=FakeBackend(), turns=[_turn("a.jsonl", "the tide")], verbose=False
    )
    (
        SEM.current_project().path("provenance.claude_sessions") / "new-session.jsonl"
    ).write_text("{}\n")  # fingerprint moves
    with pytest.raises(RuntimeError, match="no human turns"):
        SEM.load(backend=FakeBackend(), verbose=False)


class CountingBackend(FakeBackend):
    """FakeBackend that records exactly which texts it was asked to embed."""

    def __init__(self):
        self.encoded = []

    def encode(self, texts):
        self.encoded.extend(texts)
        return super().encode(texts)


def _corpus(**files):
    for name, body in files.items():
        (
            SEM.current_project().path("provenance.claude_sessions")
            / name.replace("_", ".")
        ).write_text(body)


def test_rebuild_re_embeds_only_the_sessions_that_moved(sandbox):
    be = CountingBackend()
    turns = [_turn("a.jsonl", "the tide"), _turn("b.jsonl", "nansen in a passage")]
    _corpus(a_jsonl="x", b_jsonl="y")

    prev = SEM.build(backend=be, turns=turns, verbose=False)
    assert len(be.encoded) == 2
    be.encoded.clear()

    _corpus(b_jsonl="yy")  # b grows; a is untouched
    idx = SEM.build(backend=be, turns=turns, verbose=False, previous=prev)

    assert be.encoded == ["nansen in a passage"]  # a was reused, not re-embedded
    assert len(idx["meta"]) == 2
    # The reused vector is carried over intact, not recomputed differently.
    keep = [i for i, m in enumerate(idx["meta"]) if m["file"] == "a.jsonl"][0]
    was = [i for i, m in enumerate(prev["meta"]) if m["file"] == "a.jsonl"][0]
    assert np.allclose(idx["vecs"][keep], prev["vecs"][was])


def test_rebuild_drops_a_session_that_left_the_corpus(sandbox):
    be = CountingBackend()
    both = [_turn("a.jsonl", "the tide"), _turn("b.jsonl", "nansen in a passage")]
    _corpus(a_jsonl="x", b_jsonl="y")
    prev = SEM.build(backend=be, turns=both, verbose=False)

    be.encoded.clear()
    idx = SEM.build(backend=be, turns=both[:1], verbose=False, previous=prev)
    assert {m["file"] for m in idx["meta"]} == {"a.jsonl"}
    assert be.encoded == []  # nothing re-embedded to remove one


def test_rebuild_with_nothing_changed_embeds_nothing(sandbox):
    be = CountingBackend()
    turns = [_turn("a.jsonl", "the tide")]
    _corpus(a_jsonl="x")
    prev = SEM.build(backend=be, turns=turns, verbose=False)
    be.encoded.clear()
    SEM.build(backend=be, turns=turns, verbose=False, previous=prev)
    assert be.encoded == []


def test_load_re_embeds_incrementally_rather_than_wholesale(sandbox, monkeypatch):
    """The corpus-changed path must reuse, not restart from zero."""
    from quirework.provenance import humaninput as HI

    be = CountingBackend()
    turns = [_turn("a.jsonl", "the tide"), _turn("b.jsonl", "nansen in a passage")]
    _corpus(a_jsonl="x", b_jsonl="y")
    monkeypatch.setattr(HI, "all_turns", lambda: turns)
    SEM.build(backend=be, turns=turns, verbose=False)

    be.encoded.clear()
    _corpus(b_jsonl="yy")  # fingerprint moves, only b changed
    SEM.load(backend=be, verbose=False)
    assert be.encoded == ["nansen in a passage"]


def test_fingerprint_tracks_name_and_size(sandbox):
    before = SEM.corpus_fingerprint()
    f = SEM.current_project().path("provenance.claude_sessions") / "s.jsonl"
    f.write_text("a")
    added = SEM.corpus_fingerprint()
    f.write_text("ab")  # same name, new size
    grown = SEM.corpus_fingerprint()
    assert len({before, added, grown}) == 3


def test_index_is_written_without_pickle(sandbox):
    """Loadable with allow_pickle=False — the index is data, never code."""
    SEM.build(
        backend=FakeBackend(), turns=[_turn("a.jsonl", "the tide")], verbose=False
    )
    z = np.load(SEM._settings().INDEX, allow_pickle=False)
    assert json.loads(str(z["meta"]))[0]["file"] == "a.jsonl"


# --------------------------------------------------------------------------- #
# query
# --------------------------------------------------------------------------- #
def _index(be, turns):
    metas, texts = [], []
    for t in turns:
        for ci, c in enumerate(SEM.chunk(t["text"])):
            texts.append(c)
            metas.append(
                {
                    "source": t["source"],
                    "file": t["file"],
                    "ordinal": t["ordinal"],
                    "chunk": ci,
                    "ts": None,
                    "text": c,
                }
            )
    v = be.encode(texts)
    return {"vecs": v / np.linalg.norm(v, axis=1, keepdims=True), "meta": metas}


def test_query_ranks_by_meaning_and_groups_by_session():
    be = FakeBackend()
    idx = _index(
        be,
        [
            _turn("tide.jsonl", "the tide, the tide, the tide"),
            _turn("nansen.jsonl", "nansen nansen nansen in the passage", ordinal=4),
            _turn("mixed.jsonl", "the tide and also kelp", ordinal=7),
        ],
    )
    ranked = SEM.query("tide", idx, be, top=3)
    assert [k[1] for k, _ in ranked][0] == "tide.jsonl"  # pure tide first
    assert [k[1] for k, _ in ranked][-1] == "nansen.jsonl"  # unrelated last
    assert ranked[0][1]["score"] > ranked[1][1]["score"]
    assert ranked[0][1]["snippet"].startswith("the tide")


def test_query_reports_the_best_window_and_counts_the_rest():
    be = FakeBackend()
    long_turn = _turn("a.jsonl", ("kelp " * 200) + ("tide " * 200), ordinal=9)
    idx = _index(be, [long_turn])
    assert len(idx["meta"]) > 1  # genuinely chunked
    ((_, hit),) = SEM.query("tide", idx, be, top=5)
    assert "tide" in hit["snippet"]  # the tide-heavy window, not kelp
    assert hit["ordinal"] == 9  # provenance survives chunking
    assert hit["hits"] == len(idx["meta"])  # every window of this session pooled


def test_query_honors_top():
    be = FakeBackend()
    idx = _index(be, [_turn(f"s{i}.jsonl", "the tide") for i in range(6)])
    assert len(SEM.query("tide", idx, be, top=2)) == 2


def test_query_pool_bounds_the_sessions_considered():
    be = FakeBackend()
    idx = _index(be, [_turn(f"s{i}.jsonl", "the tide") for i in range(10)])
    assert len(SEM.query("tide", idx, be, top=10, pool=3)) == 3


# --------------------------------------------------------------------------- #
# backend selection
# --------------------------------------------------------------------------- #
def test_unknown_backend_name_is_an_error(monkeypatch):
    monkeypatch.delenv("QUIREWORK_EMBED", raising=False)
    with pytest.raises(SEM.BackendUnavailable, match="unknown backend"):
        SEM.get_backend("nope")


def test_forced_backend_never_falls_back_to_another(monkeypatch):
    """A forced backend that fails must raise, not quietly demote.

    Falling through to a different model would build an index whose vectors are
    not comparable with the one the operator asked for.
    """
    monkeypatch.setitem(
        SEM.BACKENDS, "boom", lambda: (_ for _ in ()).throw(ImportError("absent"))
    )
    with pytest.raises(SEM.BackendUnavailable, match="unavailable"):
        SEM.get_backend("boom")


def test_no_backend_available_explains_how_to_install(monkeypatch):
    monkeypatch.delenv("QUIREWORK_EMBED", raising=False)
    monkeypatch.setattr(SEM, "ORDER", ())
    with pytest.raises(SEM.BackendUnavailable, match="pip install fastembed"):
        SEM.get_backend()
