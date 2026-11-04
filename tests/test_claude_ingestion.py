"""The Claude Code sweep: which transcripts it finds, which it captures, and
how it tells a settled capture from a grown session, on a synthetic Claude
Code directory and store.

Every session here is fiction written for the purpose.
"""

import json
import os
import re
import time
from types import SimpleNamespace

import pytest

from conftest import capture_project, linked_worktree
from quirework.provenance import ccfilter, store
from quirework.provenance.ingestion import claude

KETTLE = "c1a0de00-0000-4000-8000-000000000001"
STORM = "c1a0de00-0000-4000-8000-000000000002"


@pytest.fixture
def checkouts(tmp_path):
    """A book with a linked worktree, a Claude Code directory, and a store."""
    base = tmp_path.resolve()
    repo = base / "book"
    (repo / ".git").mkdir(parents=True)
    worktree = linked_worktree(repo, base / "book-draft")
    projects = base / "claude-projects"
    with capture_project(repo, base / "store", claude=projects):
        yield SimpleNamespace(
            base=base,
            repo=repo,
            worktree=worktree,
            projects=projects,
            pending=store.open_store().pending_dir(store.CC),
        )


def _launched_from(checkouts, directory):
    """The directory Claude Code keeps a session launched in ``directory``."""
    kept = checkouts.projects / re.sub(r"[/.]", "-", str(directory))
    kept.mkdir(parents=True, exist_ok=True)
    return kept


def _transcript(path, cwd, said="Boil the water first.", freight="Z" * 5000):
    """A session that said one thing and read one long file, long settled."""
    rows = [
        {"type": "user", "cwd": str(cwd), "message": {"content": said}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Read",
                        "input": {"file_path": f"{cwd}/chapters/one.md"},
                    }
                ]
            },
        },
        {
            "type": "user",
            "toolUseResult": {"file": {"content": freight}},
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": freight}
                ]
            },
        },
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    # Written long ago, so a later rewrite carries a later modification time
    # however coarse the filesystem's clock.
    settled = time.time() - store.SETTLE_SECONDS - 3600
    os.utime(path, (settled, settled))
    return path


def _sweep(capsys):
    capsys.readouterr()
    claude.main([])
    return capsys.readouterr().out


# --------------------------------------------------------------------------- #
# which transcripts are this project's
# --------------------------------------------------------------------------- #
def test_the_sweep_looks_in_every_checkout_and_every_launch_below_one(checkouts):
    """Claude Code keeps one directory per directory a session was launched
    from, so a session started in a chapter folder, or in the worktree,
    lives apart from the rest."""
    want = {
        _launched_from(checkouts, checkouts.repo),
        _launched_from(checkouts, checkouts.repo / "chapters"),
        _launched_from(checkouts, checkouts.worktree),
        _launched_from(checkouts, checkouts.worktree / "notes" / "drafts"),
    }
    _launched_from(checkouts, checkouts.base / "unrelated")
    assert set(claude.sibling_session_dirs()) == want


def test_the_recorded_working_directory_decides(checkouts, tmp_path):
    """The encoded name is lossy: a sibling whose name extends the book's
    lands beside the book's own, so the cwd a transcript records decides.
    One that records none is kept, since missing a session is worse than a
    stray one the ingest read rejects."""
    roots = [checkouts.repo, checkouts.worktree]

    def recording(name, cwd):
        path = tmp_path / name
        rows = [{"type": "mode"}] + ([{"type": "user", "cwd": str(cwd)}] if cwd else [])
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return path

    assert claude.belongs(recording("a.jsonl", checkouts.repo / "chapters"), roots)
    assert claude.belongs(recording("b.jsonl", checkouts.worktree), roots)
    lookalike = checkouts.base / "book-elsewhere"
    assert not claude.belongs(recording("c.jsonl", lookalike), roots)
    assert claude.belongs(recording("d.jsonl", None), roots)


def test_a_lookalike_projects_session_is_counted_as_another_projects(checkouts, capsys):
    lookalike = checkouts.base / "book-elsewhere"
    assert _launched_from(checkouts, lookalike) in claude.sibling_session_dirs()
    _transcript(_launched_from(checkouts, lookalike) / f"{KETTLE}.jsonl", lookalike)
    out = _sweep(capsys)
    assert "0 updated" in out and "1 from another project" in out
    assert store.pending(store.CC) == []


# --------------------------------------------------------------------------- #
# capture, not copy
# --------------------------------------------------------------------------- #
@pytest.fixture
def sessions(checkouts):
    """Where Claude Code keeps sessions launched in the book's main checkout."""
    return _launched_from(checkouts, checkouts.repo)


def test_a_capture_is_normalized_and_records_what_it_came_from(
    checkouts, sessions, capsys
):
    source = _transcript(sessions / f"{KETTLE}.jsonl", checkouts.repo)
    assert "1 updated" in _sweep(capsys)
    capture = checkouts.pending / source.name
    assert capture.stat().st_size < source.stat().st_size // 4
    header = ccfilter.capture_header(capture)
    assert (header["source_bytes"], header["source_sha256"]) == (
        ccfilter.source_fingerprint(source)
    )
    assert capture.stat().st_mtime_ns == source.stat().st_mtime_ns
    assert not list(checkouts.pending.glob(f".{source.name}.*")), "no temporary file"
    assert store.files(store.CC) == [], "captured, never accepted"


def test_a_settled_session_is_left_and_a_grown_one_captured_again(
    checkouts, sessions, capsys
):
    """A capture is a fraction of its source's size, so sizes cannot be
    compared; the source fingerprint the capture records is."""
    source = _transcript(sessions / f"{KETTLE}.jsonl", checkouts.repo)
    assert "1 updated" in _sweep(capsys)
    assert "0 updated, 1 unchanged" in _sweep(capsys)
    turn = {"type": "user", "message": {"content": "Then let it cool."}}
    source.write_text(source.read_text() + json.dumps(turn) + "\n")
    assert "1 updated" in _sweep(capsys)
    assert "Then let it cool." in (checkouts.pending / source.name).read_text()


def test_an_edit_that_keeps_the_size_is_still_seen(checkouts, sessions, capsys):
    source = _transcript(sessions / f"{KETTLE}.jsonl", checkouts.repo, said="cold")
    assert "1 updated" in _sweep(capsys)
    before = source.stat().st_size
    source.write_text(source.read_text().replace("cold", "warm"))
    assert source.stat().st_size == before
    assert "1 updated" in _sweep(capsys)
    assert "warm" in (checkouts.pending / source.name).read_text()


def test_a_raw_transcript_in_the_pending_area_is_replaced_by_a_capture(
    checkouts, sessions, capsys
):
    """A file without a capture header records no source fingerprint, so it
    is never current, and the sweep normalizes the source over it."""
    source = _transcript(sessions / f"{KETTLE}.jsonl", checkouts.repo)
    raw = checkouts.pending / source.name
    raw.write_bytes(source.read_bytes())
    assert claude.captured_fingerprint(raw) is None
    assert "1 updated" in _sweep(capsys)
    assert ccfilter.capture_header(raw) is not None


def test_a_removed_session_is_never_captured(checkouts, sessions, capsys):
    """Claude Code keeps a session after curation removes it, so only the
    ledger's list keeps every sweep from bringing it back."""
    _transcript(sessions / f"{KETTLE}.jsonl", checkouts.repo)
    _transcript(sessions / f"{STORM}.jsonl", checkouts.repo)
    (store.ledger() / store.REMOVED).write_text(f"cc {KETTLE}  # a dry run\n")
    assert "1 on removed-list" in _sweep(capsys)
    assert [p.name for p in store.pending(store.CC)] == [f"{STORM}.jsonl"]
