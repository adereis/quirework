import pytest

np = pytest.importorskip("numpy")

from quirework.provenance import humaninput  # noqa: E402
from quirework.provenance import semantic  # noqa: E402

pytestmark = pytest.mark.usefixtures("corpus_project")


class CountingBackend:
    """Deterministic vectors, recording every text it was asked to embed."""

    id = "fake:counting-v1"

    def __init__(self):
        self.encoded = []

    def encode(self, texts):
        self.encoded.extend(texts)
        return np.ones((len(texts), 3), dtype=np.float32)

    def encode_query(self, text):
        return np.ones(3, dtype=np.float32)


TURNS = [
    {
        "source": "cc",
        "file": "0badc0de-0000-4000-8000-000000000001.jsonl",
        "text": "the kettle whistles too early",
        "ordinal": 1,
        "cls": "TYPED",
        "ts": None,
    }
]


def test_an_index_cut_by_another_extraction_is_rebuilt(monkeypatch):
    """No file moved, so the fingerprint alone would serve the old windows."""
    monkeypatch.setattr(humaninput, "all_turns", lambda: TURNS)
    backend = CountingBackend()
    semantic.build(backend=backend, turns=TURNS, verbose=False)

    backend.encoded.clear()
    semantic.load(backend=backend, verbose=False)
    assert backend.encoded == []

    monkeypatch.setattr(
        humaninput, "EXTRACTION_VERSION", humaninput.EXTRACTION_VERSION + 1
    )
    semantic.load(backend=backend, verbose=False)
    assert backend.encoded == ["the kettle whistles too early"]
