"""Pruning finished seats' private runner homes; no live harness involved."""

import os
import time

import pytest

from quirework.providers import blind


def seat(root, name, *, bytes_each=2048, files=3):
    """A workspace and the private runner home `state_root` would give it."""
    work = root / name
    work.mkdir(parents=True)
    home = blind.state_root(work)
    (home / "codex" / "sessions").mkdir(parents=True)
    for n in range(files):
        (home / "codex" / "sessions" / f"{n}.jsonl").write_bytes(b"x" * bytes_each)
    return work, home


def test_a_private_home_is_found_by_the_name_state_root_gives_it(tmp_path):
    _, home = seat(tmp_path / "room" / "seats", "mara")
    (tmp_path / "room" / "board").mkdir(parents=True)
    assert blind.private_homes(tmp_path) == [home]


def test_a_recently_touched_home_is_kept(tmp_path):
    seat(tmp_path, "mara")
    report = blind.prune([tmp_path], grace=3600)
    assert report["removed"] == []
    assert "touched" in report["kept"][0][2]


def test_an_old_home_goes_and_its_records_stay(tmp_path):
    work, home = seat(tmp_path, "mara")
    (work / "drafts").mkdir()
    (work / "drafts" / "post.md").write_text("the evidence")
    old = time.time() - 7200
    for item in [home, *home.rglob("*")]:
        os.utime(item, (old, old))
    report = blind.prune([tmp_path], grace=3600)
    assert not home.exists()
    assert report["freed"] > 0
    # The seat's own workspace and its drafts are untouched.
    assert (work / "drafts" / "post.md").read_text() == "the evidence"


def test_a_dry_run_removes_nothing_but_reports_the_same(tmp_path):
    _, home = seat(tmp_path, "mara")
    report = blind.prune([tmp_path], grace=0, dry_run=True)
    assert home.exists()
    assert report["removed"][0][2] == "would remove"
    assert report["freed"] > 0


def test_force_overrides_age_but_not_a_live_seat(tmp_path, monkeypatch):
    _, home = seat(tmp_path, "mara")
    monkeypatch.setattr(blind, "occupied", lambda h: 4242)
    report = blind.prune([tmp_path], grace=0, force=True)
    assert home.exists()
    assert "pid 4242" in report["kept"][0][2]


def test_nothing_that_is_not_a_private_home_is_ever_removed(tmp_path):
    """The suffix is the whole safety rule, so it is the whole test."""
    keep = tmp_path / "records"
    keep.mkdir()
    (keep / "receipt.json").write_text("{}")
    old = time.time() - 7200
    os.utime(keep, (old, old))
    blind.prune([tmp_path], grace=0, force=True)
    assert (keep / "receipt.json").exists()


def test_disk_is_counted_once_per_inode(tmp_path):
    """A runner install is heavily hardlinked; counting paths overstated it 20x."""
    _, home = seat(tmp_path, "mara", bytes_each=8192, files=1)
    original = next(home.rglob("*.jsonl"))
    once = blind._bytes(home)
    for n in range(9):
        os.link(original, original.parent / f"link-{n}.jsonl")
    assert blind._bytes(home) == pytest.approx(once, rel=0.01)
