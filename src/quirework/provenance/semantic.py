#!/usr/bin/env python3
"""Meaning-based search over the human's own turns (``quirework provenance search --semantic``).

Keyword search answers *"which sessions did I say this word in?"*. It cannot
answer *"where did I first get uneasy about the steepness guard?"* -- that
question has no word to grep for, because the unease was expressed in whatever
words came to hand that day. This module closes that gap without giving up the
one property that makes ``search`` worth having.

**It inherits the subtractive filter.** The index is built from
``humaninput.all_turns()`` -- the same rigorous extraction keyword mode uses --
so it embeds the human's own turns and nothing else: not the assistant's words,
not injected document context, not tool I/O. That is the whole point. A
retrieval layer pointed at the raw corpus would return the hundreds of places
the *book* discusses a topic; this one returns the handful where the human
actually raised it. The 420 MB corpus reduces to ~2,400 turns and ~254k words,
which is why no vector database is needed: a brute-force cosine over a few
thousand rows in numpy is instant, and there is no ANN index to tune or corrupt.

**It stays local.** Embeddings are computed on this machine by a local model;
no corpus text is sent anywhere. That is not incidental -- it is the reason this
exists instead of a hosted notebook: ``private/`` is the project's public/private
boundary (AGENTS.md), and the corpus is also the evidence base for the
provenance artifact, so a lookup has to be re-derivable rather than sourced to a
third party's non-deterministic retriever. The index is a file in ``private/``
that can be deleted and rebuilt.

**It is a cache, so it maintains itself.** The index records the corpus
fingerprint, a per-file size map, and the model that built it; a mismatch
rebuilds rather than silently answering from stale or model-incompatible
vectors. The rebuild is *incremental* -- embedding is the whole cost, and the
corpus grows one session at a time, so a session whose file has not moved keeps
its vectors and is never re-embedded. A model change is the exception that
rebuilds everything, because vectors from two models are not comparable and
mixing them would corrupt the ranking silently.

Three backends, in preference order; the first one installed wins, and
``QUIREWORK_EMBED`` (``fastembed``/``sentence-transformers``/``ollama``) forces
one. If none is available the command says so and exits -- it never falls back
to keyword matching, which would answer a different question under the same flag.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

import numpy as np

from quirework.project import current_project


# A turn is embedded in windows, not truncated. The long tail here is real --
# a handful of STRUCTURED turns run to thousands of words -- and every embedding
# model has a hard input ceiling, so truncating would silently drop the back half
# of exactly the turns most worth finding. Windows are scored independently and a
# turn takes its best window's score. ~180 words sits safely under the 512-token
# ceiling of the default model with room for the query prefix.
CHUNK_WORDS = 180
CHUNK_OVERLAP = 45

# Acks, one-liners and slash-commands ("yes", "/clear", "perfect, commit it").
# They carry no retrievable meaning and would only dilute the neighbourhood.
SKIP_CLASSES = ("TRIVIAL",)


def _settings():
    """Resolve paths from the current operation, without import-time state."""
    from types import SimpleNamespace

    INDEX = (
        current_project().path("provenance.root", ".quirework/provenance")
        / "semantic-index.npz"
    )
    return SimpleNamespace(INDEX=INDEX)


class BackendUnavailable(RuntimeError):
    """No local embedding backend is installed (or the forced one is missing)."""


# --------------------------------------------------------------------------- #
# backends -- each exposes .id, .encode(texts), .encode_query(text)
#
# Retrieval models are asymmetric: the document side and the query side want
# different prefixes, and getting that wrong costs real recall. Each backend
# therefore owns its own query handling rather than the caller guessing.
# --------------------------------------------------------------------------- #
class _FastEmbed:
    """ONNX runtime, no torch. The default: best quality per megabyte installed."""

    MODEL = "BAAI/bge-small-en-v1.5"
    # fastembed's own default is 256, and the ONNX runtime sizes its arena from
    # the largest batch it ever sees and then keeps it. A first full build of a
    # few thousand windows took the process to 8.6 GB resident and was killed
    # by the kernel on a 16 GB machine.
    #
    # The batch is purely a memory knob here; the vectors are identical either
    # way, and on a CPU it buys no speed at all. Measured on one machine, same
    # corpus, same windows:
    #
    #     batch    8    1.1 windows/s      426 MB peak
    #     batch   32    1.1 windows/s    1,093 MB peak
    #     batch  128    0.8 windows/s    3,811 MB peak
    #
    # Throughput is flat and then falls while memory grows about linearly, so
    # the smallest workable batch is simply the best one. Raise it through the
    # environment on a machine with room and a runtime that rewards batching;
    # a GPU provider is the case that would.
    BATCH = int(os.environ.get("QUIREWORK_EMBED_BATCH", "8"))

    def __init__(self):
        from fastembed import TextEmbedding

        self._m = TextEmbedding(model_name=self.MODEL)
        self.id = f"fastembed:{self.MODEL}"

    def encode(self, texts):
        return np.asarray(
            list(self._m.embed(texts, batch_size=self.BATCH)), dtype=np.float32
        )

    def encode_query(self, text):
        # query_embed applies the model's own query prefix; using plain embed()
        # for a query is the classic bge misuse and measurably worse.
        return np.asarray(list(self._m.query_embed([text])), dtype=np.float32)[0]


class _SentenceTransformers:
    MODEL = "BAAI/bge-small-en-v1.5"
    QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

    def __init__(self):
        from sentence_transformers import SentenceTransformer

        self._m = SentenceTransformer(self.MODEL)
        self.id = f"sentence-transformers:{self.MODEL}"

    def encode(self, texts):
        return np.asarray(self._m.encode(list(texts), batch_size=64), dtype=np.float32)

    def encode_query(self, text):
        return np.asarray(self._m.encode(self.QUERY_PREFIX + text), dtype=np.float32)


class _Ollama:
    """A local ollama server. Zero pip dependencies -- just HTTP to localhost."""

    MODEL = os.environ.get("QUIREWORK_EMBED_MODEL", "nomic-embed-text")
    URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

    def __init__(self):
        import urllib.request

        self._req = urllib.request
        with self._req.urlopen(f"{self.URL}/api/tags", timeout=3) as r:
            json.load(r)
        self.id = f"ollama:{self.MODEL}"

    def _one(self, text):
        body = json.dumps({"model": self.MODEL, "prompt": text}).encode()
        req = self._req.Request(
            f"{self.URL}/api/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with self._req.urlopen(req, timeout=120) as r:
            return json.load(r)["embedding"]

    def encode(self, texts):
        return np.asarray(
            [self._one("search_document: " + t) for t in texts], dtype=np.float32
        )

    def encode_query(self, text):
        return np.asarray(self._one("search_query: " + text), dtype=np.float32)


BACKENDS = {
    "fastembed": _FastEmbed,
    "sentence-transformers": _SentenceTransformers,
    "ollama": _Ollama,
}
ORDER = ("fastembed", "sentence-transformers", "ollama")

INSTALL_HELP = """no local embedding backend found. Install one (all run offline):

  pip install fastembed              ~200MB, ONNX, no torch          (recommended)
  pip install sentence-transformers  ~2.5GB, pulls torch
  ollama serve && ollama pull nomic-embed-text                       (no pip deps)

Force a specific one with QUIREWORK_EMBED=fastembed|sentence-transformers|ollama.
Corpus text is embedded locally and never leaves this machine."""


def get_backend(name=None):
    """First available backend in preference order, or the one named."""
    name = name or os.environ.get("QUIREWORK_EMBED")
    if name:
        if name not in BACKENDS:
            raise BackendUnavailable(
                f"unknown backend {name!r}; choose from {', '.join(BACKENDS)}"
            )
        try:
            return BACKENDS[name]()
        except BackendUnavailable:
            raise
        except Exception as e:
            # A *forced* backend that fails is an error, never a quiet demotion
            # to another one: the index it would build is not interchangeable.
            raise BackendUnavailable(f"backend {name!r} unavailable: {e}") from e
    for n in ORDER:
        try:
            return BACKENDS[n]()
        except Exception:
            continue
    raise BackendUnavailable(INSTALL_HELP)


# --------------------------------------------------------------------------- #
# index
# --------------------------------------------------------------------------- #
def corpus_fingerprint():
    """Cheap change-detector over the corpus: names + sizes, no reads.

    Session logs are append-only, so a size change is a reliable "this file
    moved on" signal, and stat() over a few hundred files costs nothing --
    where re-extracting every turn (the only exact check) would cost a corpus
    scan on every query and defeat the point of an index.
    """
    h = hashlib.sha256()
    for d, pat in (
        (
            current_project().path(
                "provenance.claude_sessions", ".quirework/provenance/cc-sessions"
            ),
            "*.jsonl",
        ),
        (
            current_project().path(
                "provenance.agy_sessions", ".quirework/provenance/agy-sessions"
            ),
            "*.jsonl",
        ),
        (
            current_project().path(
                "provenance.codex_sessions", ".quirework/provenance/codex-sessions"
            ),
            "*.jsonl",
        ),
        (
            current_project().path(
                "provenance.conversations", ".quirework/provenance/conversations"
            ),
            "*.raw.json",
        ),
        (
            current_project().path(
                "provenance.conversations", ".quirework/provenance/conversations"
            ),
            "gemini-*.md",
        ),
    ):
        for p in sorted(d.glob(pat)):
            h.update(f"{p.name}:{p.stat().st_size}\n".encode())
    return h.hexdigest()


def _path(source, file):
    """Corpus path for a turn's (source, file). Web and gemini share a dir."""
    return {
        "cc": current_project().path(
            "provenance.claude_sessions", ".quirework/provenance/cc-sessions"
        ),
        "agy": current_project().path(
            "provenance.agy_sessions", ".quirework/provenance/agy-sessions"
        ),
        "codex": current_project().path(
            "provenance.codex_sessions", ".quirework/provenance/codex-sessions"
        ),
    }.get(
        source,
        current_project().path(
            "provenance.conversations", ".quirework/provenance/conversations"
        ),
    ) / file


def _sizes(keys):
    """``{"source/file": size}`` -- the per-file half of the fingerprint.

    The whole-corpus fingerprint says *something* moved; this says *which*
    files did, which is what makes a rebuild incremental instead of total.
    """
    out = {}
    for source, file in keys:
        p = _path(source, file)
        out[f"{source}/{file}"] = p.stat().st_size if p.exists() else -1
    return out


def chunk(text, size=CHUNK_WORDS, overlap=CHUNK_OVERLAP):
    """Split into overlapping word windows. Overlap keeps a thought that
    straddles a boundary findable from either side."""
    w = text.split()
    if len(w) <= size:
        return [text]
    step = size - overlap
    return [
        " ".join(w[i : i + size]) for i in range(0, len(w), step) if w[i : i + size]
    ]


def build(backend=None, turns=None, verbose=True, previous=None):
    """Embed the human's turns and write the index. Returns the index.

    Incremental when ``previous`` is given: a session whose file has not changed
    size keeps its existing vectors and is never re-embedded. Embedding is the
    entire cost here (a few texts per second per core), so a corpus that grows
    one session at a time must not pay for the whole corpus each time.
    """
    from quirework.provenance import humaninput as HI

    be = backend or get_backend()
    if turns is None:
        if verbose:
            print("scanning corpus…", file=sys.stderr)
        turns = HI.all_turns()
    turns = [t for t in turns if t["cls"] not in SKIP_CLASSES]

    by_file = {}
    for t in turns:
        for ci, c in enumerate(chunk(t["text"])):
            by_file.setdefault((t["source"], t["file"]), []).append(
                {
                    "source": t["source"],
                    "file": t["file"],
                    "ordinal": t["ordinal"],
                    "chunk": ci,
                    "ts": t["ts"].isoformat() if t["ts"] else None,
                    "text": c,
                }
            )
    if not by_file:
        raise RuntimeError("semantic: no human turns found to index")

    sizes = _sizes(by_file)
    old_rows, old_sizes = {}, {}
    if previous:
        old_sizes = previous.get("sizes") or {}
        for i, m in enumerate(previous["meta"]):
            old_rows.setdefault((m["source"], m["file"]), []).append(i)

    # Reuse a session's vectors only when its file is byte-identical in size AND
    # produced the same number of windows; either mismatch means re-embed.
    reuse, fresh = {}, []
    for key, windows in by_file.items():
        rows = old_rows.get(key)
        k = f"{key[0]}/{key[1]}"
        if (
            rows is not None
            and old_sizes.get(k) == sizes[k]
            and len(rows) == len(windows)
        ):
            reuse[key] = previous["vecs"][rows]
        else:
            fresh.append(key)

    made = {}
    if fresh:
        texts = [w["text"] for key in fresh for w in by_file[key]]
        if verbose:
            kept = len(by_file) - len(fresh)
            note = f" ({kept} session(s) reused)" if kept else ""
            print(
                f"embedding {len(texts):,} windows from {len(fresh)} "
                f"session(s) with {be.id}{note}…",
                file=sys.stderr,
            )
        v = be.encode(texts)
        v = v / np.linalg.norm(v, axis=1, keepdims=True).clip(min=1e-12)
        off = 0
        for key in fresh:
            n = len(by_file[key])
            made[key] = v[off : off + n]
            off += n
    elif verbose:
        print("index already current for every session", file=sys.stderr)

    meta, mats = [], []
    for key in sorted(by_file):  # stable order across builds
        meta.extend(by_file[key])
        mats.append(reuse[key] if key in reuse else made[key])
    vecs = np.vstack(mats).astype(np.float32)

    _settings().INDEX.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        _settings().INDEX,
        vecs=vecs,
        meta=np.array(json.dumps(meta)),
        model=np.array(be.id),
        fingerprint=np.array(corpus_fingerprint()),
        sizes=np.array(json.dumps(sizes)),
    )
    if verbose:
        print(
            f"wrote {_settings().INDEX} ({vecs.shape[0]:,}×{vecs.shape[1]})",
            file=sys.stderr,
        )
    return {"vecs": vecs, "meta": meta, "model": be.id, "sizes": sizes}


def load(backend=None, rebuild=False, verbose=True):
    """Load the index, rebuilding when missing, stale, or built by another model.

    Three paths, cheapest first: fingerprint matches, serve from disk with no
    corpus scan at all; corpus moved, scan and re-embed only the sessions that
    changed; model changed, rebuild from scratch because vectors from two models
    are not comparable and mixing them would silently corrupt the ranking.
    """
    be = backend or get_backend()
    if not rebuild and _settings().INDEX.exists():
        z = np.load(_settings().INDEX, allow_pickle=False)
        model = str(z["model"])
        if model == be.id:
            prev = {
                "vecs": z["vecs"],
                "meta": json.loads(str(z["meta"])),
                "sizes": json.loads(str(z["sizes"])) if "sizes" in z else {},
                "model": model,
            }
            if str(z["fingerprint"]) == corpus_fingerprint():
                return prev
            if verbose:
                print("corpus changed; re-embedding only what moved…", file=sys.stderr)
            return build(backend=be, verbose=verbose, previous=prev)
        if verbose:
            print(
                f"index was built by {model}, now using {be.id} — vectors "
                "are not comparable, rebuilding…",
                file=sys.stderr,
            )
    return build(backend=be, verbose=verbose)


# --------------------------------------------------------------------------- #
# query
# --------------------------------------------------------------------------- #
POOL = 400


def query(text, index, backend, top=12, pool=POOL):
    """Rank sessions by their best-matching window.

    Scores are per-window; a session takes its best window, which is the shape
    keyword mode already reports (a session row, plus the evidence that earned
    it) so the two modes stay interchangeable for the caller.

    ``hits`` counts how many of the ``pool`` most-similar windows in the whole
    corpus came from that session -- a concentration signal, distinct from the
    best-match score: one glancing mention scores like a sustained argument, and
    this is what tells them apart.
    """
    q = backend.encode_query(text)
    q = q / max(float(np.linalg.norm(q)), 1e-12)
    sims = index["vecs"] @ q

    sessions = {}
    # Descending, so the first window seen for a session is its best -- score,
    # snippet and ordinal are all taken from that one.
    for i in np.argsort(-sims)[:pool]:
        m = index["meta"][int(i)]
        key = (m["source"], m["file"])
        s = sessions.get(key)
        if s is None:
            sessions[key] = {
                "score": float(sims[int(i)]),
                "ordinal": m["ordinal"],
                "snippet": m["text"],
                "ts": m["ts"],
                "hits": 1,
            }
        else:
            s["hits"] += 1
    return sorted(sessions.items(), key=lambda kv: -kv[1]["score"])[:top]
