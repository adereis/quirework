"""One store kept on two machines, meeting at a backup target they share.

These pin what makes that safe. Neither machine's run deletes the other's
captures. A sync brings in what the other accepted, rejected and captured,
and a pending file goes away, here or there, only once a decision covers it.
A version is checked against its own name before it is taken, and a reader
that lacks one fetches it rather than failing.

The two machines are two stores under one identity. They share one ledger,
which stands for a ledger that git already carried from one to the other.
Every session here is fiction written for the purpose.
"""

import io
import os
import shutil
import time
from argparse import Namespace

import pytest

from conftest import corpus_settings, keep_session
from quirework.project import ConfigurationError, Project, project_scope
from quirework.provenance import backup, store
from quirework.provenance.backup import exchange
from quirework.provenance.backup.base import BackupError

KETTLE = "0badc0de-0000-4000-8000-000000000011"
STORM = "0badc0de-0000-4000-8000-000000000012"
LANTERN = "0badc0de-0000-4000-8000-000000000013"
RCLONE = shutil.which("rclone")
needs_rclone = pytest.mark.skipif(RCLONE is None, reason="needs the rclone program")

# Times well apart, so each copy is plainly earlier or later than another.
EARLY, LATER, LATEST = 1_790_000_000, 1_790_003_600, 1_790_007_200


def _turns(*said):
    return "".join(
        '{"type": "user", "message": {"content": "%s"}}\n' % text for text in said
    )


class Machine:
    """One machine's copy of the store, with its target already declared."""

    def __init__(self, tmp_path, name, ledger_root, target):
        self.project = Project.from_settings(
            ledger_root,
            {"provenance": corpus_settings(store=str(tmp_path / f"{name}-store"))},
        )
        with project_scope(self.project):
            opened, _ = store.init(self.project)
        self.source = backup.Source(opened.root, opened.identity)
        (opened.root / "backup.toml").write_text(target, encoding="utf-8")
        (self.driver,) = backup.targets(opened.root)

    @property
    def root(self):
        return self.source.root

    def scope(self):
        return project_scope(self.project)

    def capture(self, session, text, mtime):
        return keep_session(
            self.project,
            f"cc-sessions/{session}.jsonl",
            _turns(*text),
            pending=True,
            mtime=mtime,
        )

    def pending(self):
        return sorted(
            p.name for p in (self.root / "pending" / "cc-sessions").glob("*.jsonl")
        )

    def backup(self):
        return backup.back_up(self.source, self.driver)

    def sync(self):
        return backup.back_up(self.source, self.driver, pull=True)


def _directory_target(nas):
    return f'[targets.nas]\ndriver = "directory"\ndestination = "{nas}"\n'


@pytest.fixture
def nas(tmp_path):
    destination = tmp_path / "nas"
    destination.mkdir()
    return destination


@pytest.fixture
def machines(tmp_path, nas):
    ledger_root = tmp_path / "book"
    laptop = Machine(tmp_path, "laptop", ledger_root, _directory_target(nas))
    desktop = Machine(tmp_path, "desktop", ledger_root, _directory_target(nas))
    assert laptop.source.identity == desktop.source.identity
    return laptop, desktop


def _at(nas, session):
    """Where a session is at the target: 'pending', 'accepted', or both."""
    places = set()
    if (nas / "pending" / "cc-sessions" / f"{session}.jsonl").exists():
        places.add("pending")
    if list((nas / "accepted" / "cc-sessions").glob(f"*/{session}.jsonl")):
        places.add("accepted")
    return places


# --------------------------------------------------------------------------- #
# what a run may delete
# --------------------------------------------------------------------------- #
def test_two_machines_backing_up_never_delete_each_others_captures(machines, nas):
    # The defect this module exists for: each machine's run deleted every
    # pending file at the target that its own copy lacked.
    laptop, desktop = machines
    laptop.capture(KETTLE, ["a kettle sings"], EARLY)
    desktop.capture(STORM, ["a storm turns"], EARLY)
    laptop.backup()
    for machine in (desktop, laptop, desktop, laptop):
        outcome = machine.backup()
        assert outcome.removed == 0
        assert _at(nas, KETTLE) == {"pending"} and _at(nas, STORM) == {"pending"}


def test_a_backup_never_overwrites_a_later_copy_with_an_earlier_one(machines, nas):
    laptop, desktop = machines
    desktop.capture(STORM, ["a storm"], EARLY)
    desktop.sync()
    laptop.sync()
    desktop.capture(STORM, ["a storm", "it passes"], LATER)
    desktop.sync()
    # The laptop's copy is the earlier one; pushing it would undo the growth.
    laptop.backup()
    copy = nas / "pending" / "cc-sessions" / f"{STORM}.jsonl"
    assert "it passes" in copy.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# what a sync brings in
# --------------------------------------------------------------------------- #
def test_a_sync_brings_in_what_the_other_machine_captured_and_accepted(machines, nas):
    laptop, desktop = machines
    with laptop.scope():
        keep_session(laptop.project, f"cc-sessions/{KETTLE}.jsonl", _turns("a kettle"))
    laptop.capture(STORM, ["a storm"], EARLY)
    laptop.sync()

    outcome = desktop.sync()
    assert outcome.fetched == 1 and list(outcome.arrived) == [
        f"cc-sessions/{STORM}.jsonl"
    ]
    with desktop.scope():
        assert store.verify(desktop.project) == []
        (path,) = store.files(store.CC)
        assert "a kettle" in path.read_text(encoding="utf-8")
    assert desktop.pending() == [f"{STORM}.jsonl"]
    # Nothing new the next time, in either direction.
    again = desktop.sync()
    assert again.fetched == 0 and not again.arrived


def test_a_later_capture_reaches_the_machine_holding_an_earlier_copy(machines):
    laptop, desktop = machines
    desktop.capture(STORM, ["a storm"], EARLY)
    desktop.sync()
    laptop.sync()
    desktop.capture(STORM, ["a storm", "it passes"], LATER)
    desktop.sync()
    laptop.sync()
    held = laptop.root / "pending" / "cc-sessions" / f"{STORM}.jsonl"
    assert "it passes" in held.read_text(encoding="utf-8")
    assert int(held.stat().st_mtime) == LATER


def test_an_accept_on_one_machine_settles_the_capture_on_every_copy(machines, nas):
    laptop, desktop = machines
    desktop.capture(STORM, ["a storm"], EARLY)
    desktop.sync()
    laptop.sync()
    assert laptop.pending() == [f"{STORM}.jsonl"]

    with laptop.scope():
        store.accept([STORM], live=True)
    assert laptop.sync().removed == 1
    assert _at(nas, STORM) == {"accepted"}

    outcome = desktop.sync()
    assert outcome.dropped == 1 and outcome.fetched == 1
    assert desktop.pending() == []
    with desktop.scope():
        assert store.verify(desktop.project) == []
    # The accepted copy is the same capture, so neither machine sends the
    # pending one back.
    laptop.sync()
    assert _at(nas, STORM) == {"accepted"}


def test_a_capture_that_grew_after_its_accept_stays_pending(machines, nas):
    laptop, desktop = machines
    desktop.capture(STORM, ["a storm"], EARLY)
    desktop.sync()
    laptop.sync()
    with laptop.scope():
        store.accept([STORM], live=True)
    laptop.sync()
    # The session went on running on the desktop after the laptop accepted it.
    desktop.capture(STORM, ["a storm", "it passes"], LATER)
    desktop.sync()
    assert _at(nas, STORM) == {"pending", "accepted"}
    laptop.sync()
    assert laptop.pending() == [f"{STORM}.jsonl"]


def test_an_earlier_copy_than_the_accepted_one_is_settled(machines, nas):
    laptop, desktop = machines
    desktop.capture(STORM, ["a storm"], EARLY)
    desktop.sync()
    laptop.sync()
    # The desktop grows the capture and the laptop, having synced it, accepts
    # it; the laptop's earlier copy must not come back as pending anywhere.
    desktop.capture(STORM, ["a storm", "it passes"], LATER)
    desktop.sync()
    laptop.sync()
    with laptop.scope():
        store.accept([STORM], live=True)
    stale = desktop.root / "pending" / "cc-sessions" / f"{STORM}.jsonl"
    stale.write_text(_turns("a storm"), encoding="utf-8")
    os.utime(stale, (EARLY, EARLY))
    laptop.sync()
    desktop.sync()
    assert desktop.pending() == [] and _at(nas, STORM) == {"accepted"}


def test_a_rejection_reaches_every_copy_and_keeps_the_capture_out(machines, nas):
    laptop, desktop = machines
    desktop.capture(STORM, ["a storm"], EARLY)
    desktop.sync()
    laptop.sync()
    with laptop.scope():
        store.reject([STORM], "a stub")
    removal = exchange.removal_path(laptop.root, store.CC, STORM)
    assert exchange.read_removal(removal) is not None

    assert laptop.sync().removed == 1
    assert _at(nas, STORM) == set()
    assert (nas / store.REMOVALS / "cc-sessions" / f"{STORM}.toml").is_file()

    assert desktop.sync().dropped == 1
    assert desktop.pending() == []
    for machine in (laptop, desktop):
        machine.sync()
        assert machine.pending() == []
    assert _at(nas, STORM) == set()


def test_a_rejection_covers_captures_from_a_clock_running_ahead(machines, nas):
    # A capture keeps the time of the machine that made it, which may run
    # ahead of the clock of the machine that rejects it.
    laptop, desktop = machines
    desktop.capture(STORM, ["a storm"], time.time() + 86_400)
    desktop.sync()
    laptop.sync()
    with laptop.scope():
        store.reject([STORM], "a stub")
    laptop.sync()
    desktop.sync()
    assert desktop.pending() == [] and _at(nas, STORM) == set()


def test_a_capture_made_after_its_rejection_is_not_covered_by_it(machines):
    laptop, desktop = machines
    with laptop.scope():
        laptop.capture(STORM, ["a storm"], EARLY)
        store.reject([STORM], "a stub")
    laptop.sync()
    # Taken off the removed list and captured again later, on purpose.
    future = (exchange.removals(laptop.root)[(store.CC, STORM)]) + 3600
    desktop.capture(STORM, ["a storm", "worth keeping"], future)
    desktop.sync()
    assert desktop.pending() == [f"{STORM}.jsonl"]


def test_a_rejection_only_moves_forward():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = exchange.record_removal(root, store.CC, STORM, 2_000)
        exchange.record_removal(root, store.CC, STORM, 1_000)
        assert exchange.read_removal(path) == 2_000
        exchange.record_removal(root, store.CC, STORM, 3_000)
        assert exchange.read_removal(path) == 3_000
        assert path.stat().st_mtime_ns == 3_000


# --------------------------------------------------------------------------- #
# what is checked before it is taken
# --------------------------------------------------------------------------- #
def test_a_damaged_version_at_the_target_is_refused_whole(machines, nas):
    laptop, desktop = machines
    with laptop.scope():
        keep_session(laptop.project, f"cc-sessions/{KETTLE}.jsonl", _turns("a kettle"))
    laptop.sync()
    (copy,) = (nas / "accepted").rglob(f"{KETTLE}.jsonl")
    copy.chmod(0o644)
    copy.write_bytes(copy.read_bytes() + b"damage\n")

    with pytest.raises(BackupError, match="do not hash to that name"):
        desktop.sync()
    accepted = desktop.root / "accepted" / "cc-sessions"
    assert not any(accepted.iterdir())


def test_copies_a_second_apart_are_the_same_capture():
    # SFTP keeps times to the second, so a copy that went there and back
    # reads a fraction earlier than the store's.
    exact = exchange.Sig(100, 1_790_000_000.75)
    truncated = exchange.Sig(100, 1_790_000_000.0)
    assert not exchange.newer(exact, truncated)
    assert not exchange.newer(truncated, exact)
    grown = exchange.Sig(140, 1_790_000_000.0)
    assert exchange.newer(grown, exact)
    decided = exchange.Decided({(store.CC, f"{STORM}.jsonl"): [exact]}, {})
    assert decided.covers(f"cc-sessions/{STORM}.jsonl", truncated)
    assert not decided.covers(f"cc-sessions/{STORM}.jsonl", grown)
    later = exchange.Sig(100, 1_790_000_100.0)
    assert not decided.covers(f"cc-sessions/{STORM}.jsonl", later)


def test_rclone_times_parse_past_the_microsecond():
    assert exchange.parse_time("2026-11-12T12:08:10.123456789-05:00") == pytest.approx(
        1_794_503_290.123456
    )
    assert exchange.parse_time("2026-11-12T17:08:10Z") == 1_794_503_290.0


# --------------------------------------------------------------------------- #
# readers
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _fresh_fetch_memory(monkeypatch):
    monkeypatch.setattr(backup, "_FETCHED", set())


def test_a_reader_fetches_an_accepted_version_this_copy_lacks(machines, nas, capsys):
    laptop, desktop = machines
    with laptop.scope():
        keep_session(laptop.project, f"cc-sessions/{KETTLE}.jsonl", _turns("a kettle"))
    laptop.backup()
    with desktop.scope():
        (path,) = store.files(store.CC)
    assert "a kettle" in path.read_text(encoding="utf-8")
    assert "fetched 1 accepted version(s) from nas" in capsys.readouterr().err


def test_a_reader_says_what_the_target_does_not_hold_yet(machines):
    laptop, desktop = machines
    with laptop.scope():
        keep_session(laptop.project, f"cc-sessions/{KETTLE}.jsonl", _turns("a kettle"))
    # The laptop accepted it but has not synced since.
    with desktop.scope(), pytest.raises(ConfigurationError) as refused:
        store.files(store.CC)
    said = str(refused.value)
    assert "nas does not hold 1 of the version(s) yet" in said
    assert "store sync" in said


def test_a_reader_with_no_target_is_refused_as_before(tmp_path):
    ledger_root = tmp_path / "book"
    first = Project.from_settings(
        ledger_root, {"provenance": corpus_settings(store=str(tmp_path / "one"))}
    )
    keep_session(first, f"cc-sessions/{KETTLE}.jsonl", _turns("a kettle"))
    second = Project.from_settings(
        ledger_root, {"provenance": corpus_settings(store=str(tmp_path / "two"))}
    )
    with project_scope(second):
        store.init(second)
        with pytest.raises(ConfigurationError, match="lacks 1 accepted file"):
            store.files(store.CC)


# --------------------------------------------------------------------------- #
# the command and the other drivers
# --------------------------------------------------------------------------- #
def test_the_store_command_dispatches_sync(machines, capsys):
    laptop, desktop = machines
    laptop.capture(STORM, ["a storm"], EARLY)
    laptop.sync()
    with desktop.scope():
        assert store.main(["sync"]) == 0
    assert "brought in 0 accepted version(s) and 1 pending file(s)" in (
        capsys.readouterr().out
    )
    assert desktop.pending() == [f"{STORM}.jsonl"]


def test_sync_all_needs_no_project(machines, monkeypatch, capsys):
    laptop, desktop = machines
    laptop.capture(STORM, ["a storm"], EARLY)
    laptop.sync()
    home = desktop.root.parent
    monkeypatch.setenv("QUIREWORK_PROVENANCE_HOME", str(home))
    args = Namespace(target=None, every_store=True, check=False, pull=True)
    assert backup.cmd_backup(args) == 0
    assert desktop.pending() == [f"{STORM}.jsonl"]


def test_a_command_target_only_receives(machines, tmp_path):
    laptop, _ = machines
    script = laptop.root / "backup.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o700)
    (laptop.root / "backup.toml").write_text(
        '[targets.mine]\ndriver = "command"\ncommand = ["backup.sh"]\n'
    )
    (driver,) = backup.targets(laptop.root)
    outcome = backup.back_up(laptop.source, driver, pull=True)
    assert "a command target only receives" in outcome.summary()


def test_status_counts_what_a_sync_brought_in_as_backed_up(machines):
    laptop, desktop = machines
    laptop.capture(STORM, ["a storm"], EARLY)
    laptop.sync()
    desktop.sync()
    lines, due = backup.report(desktop.source)
    assert not due, lines


# --------------------------------------------------------------------------- #
# rclone
# --------------------------------------------------------------------------- #
@needs_rclone
def test_rclone_carries_two_machines_through_capture_accept_and_reject(tmp_path):
    cloud = tmp_path / "cloud"
    target = f'[targets.cloud]\ndriver = "rclone"\ndestination = "{cloud}"\n'
    ledger_root = tmp_path / "book"
    laptop = Machine(tmp_path, "laptop", ledger_root, target)
    desktop = Machine(tmp_path, "desktop", ledger_root, target)

    laptop.capture(KETTLE, ["a kettle"], EARLY)
    desktop.capture(STORM, ["a storm"], EARLY)
    desktop.capture(LANTERN, ["a lantern"], EARLY)
    for machine in (laptop, desktop, laptop):
        machine.sync()
    assert (
        laptop.pending()
        == desktop.pending()
        == sorted(f"{s}.jsonl" for s in (KETTLE, STORM, LANTERN))
    )

    with laptop.scope():
        store.accept([STORM], live=True)
        store.reject([LANTERN], "a stub")
    laptop.sync()
    assert _at(cloud, STORM) == {"accepted"} and _at(cloud, LANTERN) == set()
    desktop.sync()
    assert desktop.pending() == [f"{KETTLE}.jsonl"]
    with desktop.scope():
        assert store.verify(desktop.project) == []


@needs_rclone
def test_rclone_lists_sizes_and_times(tmp_path):
    cloud = tmp_path / "cloud"
    target = f'[targets.cloud]\ndriver = "rclone"\ndestination = "{cloud}"\n'
    laptop = Machine(tmp_path, "laptop", tmp_path / "book", target)
    path = laptop.capture(STORM, ["a storm"], EARLY)
    laptop.backup()
    sigs = laptop.driver.signatures("pending", io.StringIO())
    sig = sigs[f"cc-sessions/{STORM}.jsonl"]
    assert sig.size == path.stat().st_size and int(sig.mtime) == EARLY
