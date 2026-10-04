"""Backing a provenance store up: every session stays somewhere, always.

These pin what a backup has to guarantee to be worth having. An accepted file
at the destination is never overwritten or deleted. A pending file there is
removed only once its accepted version has arrived, even when an accept lands
in the middle of a run, and a run that fails has removed nothing. A
destination belongs to one store, and one that is not there is refused rather
than created. Every run leaves a record, and status reads it.

Every session here is fiction written for the purpose.
"""

import io
import os
import shutil
import stat
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from conftest import corpus_settings, keep_session
from quirework.project import ConfigurationError, Project, project_scope
from quirework.provenance import backup, store
from quirework.provenance.backup import base
from quirework.provenance.backup.base import ACCEPTED, PENDING, BackupError

KETTLE = "0badc0de-0000-4000-8000-000000000001"
STORM = "0badc0de-0000-4000-8000-000000000002"
LANTERN = "0badc0de-0000-4000-8000-000000000003"
RCLONE = shutil.which("rclone")
needs_rclone = pytest.mark.skipif(RCLONE is None, reason="needs the rclone program")


def _turns(*said):
    return "".join(
        '{"type": "user", "message": {"content": "%s"}}\n' % text for text in said
    )


def _book(tmp_path, name="book"):
    project = Project.from_settings(
        tmp_path / name,
        {"provenance": corpus_settings(store=str(tmp_path / f"{name}-store"))},
    )
    with project_scope(project):
        store.init(project)
    return project


def _source(project):
    with project_scope(project):
        opened = store.open_store(project)
    return backup.Source(opened.root, opened.identity)


def _declare(source, text):
    (source.root / "backup.toml").write_text(text, encoding="utf-8")


def _directory(source, destination, name="nas"):
    _declare(
        source,
        f'[targets.{name}]\ndriver = "directory"\ndestination = "{destination}"\n',
    )
    (driver,) = backup.targets(source.root)
    return driver


def _tree(root):
    """``{rel: bytes}`` for every file under ``root``."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(Path(root).rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def book(tmp_path):
    project = _book(tmp_path)
    with project_scope(project):
        yield project


@pytest.fixture
def nas(tmp_path):
    destination = tmp_path / "nas"
    destination.mkdir()
    return destination


# --------------------------------------------------------------------------- #
# what backup.toml says
# --------------------------------------------------------------------------- #
def test_a_store_without_backup_toml_declares_nothing(book):
    assert backup.targets(_source(book).root) is None


@pytest.mark.parametrize(
    "text, said",
    [
        ("", "declares no targets"),
        ("[target.nas]\ndriver = 'directory'\n", "unknown setting target"),
        ("[targets.nas]\ndriver = 'ftp'\n", "driver must be one of"),
        ("[targets.nas]\ndriver = 'directory'\n", "needs destination"),
        (
            "[targets.nas]\ndriver = 'directory'\ndestination = 'backups/book'\n",
            "absolute path",
        ),
        (
            "[targets.nas]\ndriver = 'directory'\ndestination = '/srv/b'\n"
            "colour = 'red'\n",
            "unknown setting colour",
        ),
        ("[targets.'a b']\ndriver = 'git'\n", "plain name"),
        ("[targets.nas]\ndriver = 'git'\nbranch = 'a..b'\n", "plain branch name"),
        ("[targets.nas]\ndriver = 'command'\ncommand = 'go.sh'\n", "list"),
        (
            "[targets.nas]\ndriver = 'command'\ncommand = ['../go.sh']\n",
            "top level",
        ),
        ("[targets.nas]\ndriver = 'rclone'\ndestination = ' '\n", "non-empty"),
        ("[targets.nas]\ndriver = 'rclone'\ndestination = 'book'\n", "remote:path"),
        ("[targets.nas]\ndriver = 'rclone'\ndestination = '/'\n", "remote:path"),
        ("[targets\n", "cannot read"),
    ],
)
def test_backup_toml_is_read_strictly(book, text, said):
    # A misspelled key would otherwise leave a default in force, and the
    # first sign of it would be the day the backup was needed.
    source = _source(book)
    _declare(source, text)
    with pytest.raises(ConfigurationError, match=said):
        backup.targets(source.root)


def test_a_target_can_be_selected_by_name(book, nas):
    source = _source(book)
    _declare(
        source,
        f'[targets.nas]\ndriver = "directory"\ndestination = "{nas}"\n'
        '[targets.drive]\ndriver = "rclone"\ndestination = "drive:book"\n',
    )
    drivers = backup.targets(source.root)
    assert [d.target for d in backup.select(drivers, ["drive"], source.root)] == [
        "drive"
    ]
    with pytest.raises(ConfigurationError, match="declares no target attic"):
        backup.select(drivers, ["attic"], source.root)


def test_the_bundled_template_is_a_backup_toml_quirework_reads(book):
    from importlib.resources import files

    text = files("quirework").joinpath("templates", "backup.toml").read_text()
    assert text.startswith("# ")
    source = _source(book)
    _declare(source, text)
    (driver,) = backup.targets(source.root)
    assert driver.name == "rclone"
    # Every commented example is one a person can uncomment as written.
    uncommented = "\n".join(
        line[2:] if line.startswith("# [targets") or _example(line) else line
        for line in text.splitlines()
    )
    _declare(source, uncommented)
    names = [d.name for d in backup.targets(source.root)]
    assert names == ["rclone", "directory", "git", "command"]


def _example(line):
    return line.startswith("# ") and " = " in line and line[2:3].islower()


# --------------------------------------------------------------------------- #
# the copy itself
# --------------------------------------------------------------------------- #
def test_a_backup_carries_the_store_and_restores_it_whole(book, nas, tmp_path):
    keep_session(book, f"cc-sessions/{KETTLE}.jsonl", _turns("a kettle sings"))
    keep_session(
        book, f"cc-sessions/{STORM}.jsonl", _turns("a storm turns"), pending=True
    )
    source = _source(book)
    outcome = backup.back_up(source, _directory(source, nas))

    assert outcome.pending == 1 and len(outcome.versions) == 1
    copied = _tree(nas)
    assert copied == _tree(source.root)
    assert "store.toml" in copied and "backup.toml" in copied

    # A restore is a copy back into an empty store under the same identity,
    # and the ledger proves every accepted file returned intact.
    restored = tmp_path / "restored"
    shutil.copytree(nas, restored)
    elsewhere = Project.from_settings(
        book.root, {"provenance": corpus_settings(store=str(restored))}
    )
    assert store.verify(elsewhere) == []


def test_names_starting_with_a_dot_are_never_copied(book, nas):
    keep_session(book, f"cc-sessions/{KETTLE}.jsonl", _turns("a kettle"))
    source = _source(book)
    half_built = source.root / "accepted" / "cc-sessions" / ".accepting-x1"
    half_built.mkdir()
    (half_built / "partial.jsonl").write_text("{}\n")
    writing = source.root / "pending" / "cc-sessions" / f".{STORM}.jsonl.x1"
    writing.write_text("{}\n")
    backup.back_up(source, _directory(source, nas))
    assert not [rel for rel in _tree(nas) if "/." in f"/{rel}"]


def test_a_destination_that_is_not_there_is_refused_never_created(book, tmp_path):
    # An unmounted share leaves its mount point behind; filling it would put
    # the "backup" on the laptop's own disk.
    source = _source(book)
    absent = tmp_path / "share" / "book"
    with pytest.raises(BackupError, match="does not exist"):
        backup.back_up(source, _directory(source, absent))
    assert not absent.exists() and not absent.parent.exists()


def test_a_destination_holding_another_store_is_refused(tmp_path, nas):
    first, second = _book(tmp_path, "book"), _book(tmp_path, "site")
    keep_session(first, f"cc-sessions/{KETTLE}.jsonl", _turns("a kettle"))
    keep_session(second, f"cc-sessions/{STORM}.jsonl", _turns("a storm"), pending=True)
    one, other = _source(first), _source(second)
    backup.back_up(one, _directory(one, nas))
    before = _tree(nas)
    with pytest.raises(BackupError, match="holds store"):
        backup.back_up(other, _directory(other, nas))
    assert _tree(nas) == before


def test_a_destination_holding_anything_else_is_refused(book, nas):
    (nas / "holiday.jpg").write_bytes(b"not a session")
    source = _source(book)
    with pytest.raises(BackupError, match="no store.toml"):
        backup.back_up(source, _directory(source, nas))
    assert sorted(p.name for p in nas.iterdir()) == ["holiday.jpg"]


def test_an_accepted_file_is_never_rewritten_at_the_destination(book, nas):
    accepted = keep_session(book, f"cc-sessions/{KETTLE}.jsonl", _turns("a kettle"))
    source = _source(book)
    driver = _directory(source, nas)
    backup.back_up(source, driver)
    copy = nas / accepted.relative_to(source.root)
    copy.write_bytes(copy.read_bytes() + b"damage\n")
    with pytest.raises(BackupError, match="never changes"):
        backup.back_up(source, driver)
    # Neither side is chosen: the damaged copy stays for a person to judge.
    assert copy.read_bytes().endswith(b"damage\n")


def test_a_grown_capture_replaces_its_copy(book, nas):
    pending = keep_session(
        book, f"cc-sessions/{STORM}.jsonl", _turns("a storm"), pending=True
    )
    source = _source(book)
    driver = _directory(source, nas)
    backup.back_up(source, driver)
    pending.write_text(_turns("a storm", "it passes"), encoding="utf-8")
    later = pending.stat().st_mtime + 60
    os.utime(pending, (later, later))
    backup.back_up(source, driver)
    copy = nas / "pending" / "cc-sessions" / f"{STORM}.jsonl"
    assert copy.read_bytes() == pending.read_bytes()


def test_a_rejected_capture_leaves_the_destination(book, nas):
    # `reject` deletes a capture on purpose; a backup that kept it would keep
    # a session the author curated out.
    keep_session(book, f"cc-sessions/{STORM}.jsonl", _turns("a storm"), pending=True)
    source = _source(book)
    driver = _directory(source, nas)
    backup.back_up(source, driver)
    store.reject([STORM], "a stub")
    outcome = backup.back_up(source, driver)
    assert outcome.removed == 1
    assert not (nas / "pending" / "cc-sessions" / f"{STORM}.jsonl").exists()
    assert not (nas / "pending" / "cc-sessions").exists()


def _at_destination(nas, session):
    """Where a session is at the destination: 'pending', 'accepted', or both."""
    places = set()
    if (nas / "pending" / "cc-sessions" / f"{session}.jsonl").exists():
        places.add("pending")
    if list((nas / "accepted" / "cc-sessions").glob(f"*/{session}.jsonl")):
        places.add("accepted")
    return places


def test_an_accept_in_the_middle_of_a_run_never_leaves_a_session_missing(
    book, nas, monkeypatch
):
    keep_session(book, f"cc-sessions/{STORM}.jsonl", _turns("a storm"), pending=True)
    source = _source(book)
    driver = _directory(source, nas)
    backup.back_up(source, driver)
    assert _at_destination(nas, STORM) == {"pending"}

    seen = []
    put, remove = type(driver).put, type(driver).remove

    def put_then_accept(self, src, area, *, overwrite, within=(), log):
        put(self, src, area, overwrite=overwrite, within=within, log=log)
        seen.append(_at_destination(nas, STORM))
        if area == ACCEPTED and not within:
            # The person accepts just after the run listed accepted/: the new
            # version is not in this copy, and the capture leaves pending.
            store.accept([STORM], live=True)

    def remove_and_watch(self, area, rels, log):
        assert "accepted" in _at_destination(nas, STORM)
        remove(self, area, rels, log)
        seen.append(_at_destination(nas, STORM))

    monkeypatch.setattr(type(driver), "put", put_then_accept)
    monkeypatch.setattr(type(driver), "remove", remove_and_watch)
    outcome = backup.back_up(source, driver)

    assert all(seen), seen
    assert _at_destination(nas, STORM) == {"accepted"}
    assert outcome.removed == 1


def test_accept_puts_the_version_in_place_before_clearing_the_capture(
    book, monkeypatch
):
    # The ordering above rests on this: a session is always in pending,
    # accepted, or both, never in neither.
    keep_session(book, f"cc-sessions/{STORM}.jsonl", _turns("a storm"), pending=True)
    root = _source(book).root
    clear = store._clear_pending

    def clear_after_checking(corpus, family, waiting, taken):
        assert list((root / "accepted" / family).glob(f"*/{STORM}.jsonl"))
        clear(corpus, family, waiting, taken)

    monkeypatch.setattr(store, "_clear_pending", clear_after_checking)
    store.accept([STORM], live=True)


def test_a_run_that_fails_has_removed_nothing(book, nas, monkeypatch):
    keep_session(book, f"cc-sessions/{STORM}.jsonl", _turns("a storm"), pending=True)
    source = _source(book)
    driver = _directory(source, nas)
    backup.back_up(source, driver)
    store.accept([STORM], live=True)
    put = type(driver).put

    def fail_on_accepted(self, src, area, *, overwrite, within=(), log):
        if area == ACCEPTED:
            raise BackupError("the share went away")
        put(self, src, area, overwrite=overwrite, within=within, log=log)

    monkeypatch.setattr(type(driver), "put", fail_on_accepted)
    with pytest.raises(BackupError, match="went away"):
        backup.back_up(source, driver)
    assert _at_destination(nas, STORM) == {"pending"}


def test_two_runs_of_one_target_never_overlap(book, nas):
    source = _source(book)
    driver = _directory(source, nas)
    directory = backup.state_dir(source.identity)
    directory.mkdir(parents=True, exist_ok=True)
    with backup._running(directory, driver.target, source.root):
        with pytest.raises(BackupError, match="already running"):
            backup.back_up(source, driver)


# --------------------------------------------------------------------------- #
# records and status
# --------------------------------------------------------------------------- #
def test_status_follows_each_target_from_never_to_behind(book, nas, monkeypatch):
    keep_session(book, f"cc-sessions/{KETTLE}.jsonl", _turns("a kettle"))
    source = _source(book)
    driver = _directory(source, nas)

    lines, due = backup.report(source)
    assert lines[0].startswith("backup    nas: directory")
    assert "never backed up" in lines[1] and due

    backup.back_up(source, driver)
    lines, due = backup.report(source)
    assert "last succeeded" in lines[1] and "not yet" not in lines[1] and not due

    keep_session(book, f"cc-sessions/{STORM}.jsonl", _turns("a storm"), pending=True)
    keep_session(book, f"cc-sessions/{LANTERN}.jsonl", _turns("a lantern"))
    lines, due = backup.report(source)
    assert "1 accepted version(s) and 1 pending session(s)" in lines[1] and due

    monkeypatch.setattr(
        type(driver),
        "put",
        lambda *a, **k: (_ for _ in ()).throw(BackupError("the share went away")),
    )
    with pytest.raises(BackupError):
        backup.back_up(source, driver)
    lines, due = backup.report(source)
    assert "last run failed" in lines[1] and "the share went away" in lines[1]
    assert "last succeeded" in lines[2] and due


def test_a_run_cut_off_midway_reads_as_unfinished(book, nas, monkeypatch):
    # A power cut or a timer's SIGTERM raises nothing; the record written
    # before the run began is all that is left to say so.
    source = _source(book)
    driver = _directory(source, nas)
    backup.back_up(source, driver)
    seen = {}

    def cut_off(self, src, log):
        seen.update(backup.read_record(src.identity, "nas")["attempt"])
        raise SystemExit("killed")

    monkeypatch.setattr(type(driver), "run", cut_off)
    with pytest.raises(SystemExit):
        backup.back_up(source, driver)
    assert seen["ok"] is None and seen["finished"] is None
    lines, due = backup.report(source)
    assert "has not finished" in lines[1] and "last succeeded" in lines[2] and due


def test_a_run_keeps_its_tools_output_in_a_log(book, nas):
    source = _source(book)
    backup.back_up(source, _directory(source, nas))
    log = backup.log_of(source.identity, "nas").read_text()
    assert "# finished" in log and "directory: copied" in log


def test_records_live_under_the_state_directory_keyed_by_the_store(book, nas):
    source = _source(book)
    backup.back_up(source, _directory(source, nas))
    state = Path(os.environ["XDG_STATE_HOME"])
    record = state / "quirework" / "backup" / source.identity / "nas.json"
    assert record.is_file()
    assert backup.read_record(source.identity, "nas")["attempt"]["ok"] is True


def test_a_store_without_a_backup_is_told_it_is_the_only_copy(book):
    source = _source(book)
    assert backup.report(source) == (None, False)
    assert "only copy" in backup.unconfigured(source)


def test_a_store_inside_a_repository_is_told_the_repository_keeps_it(tmp_path):
    project = Project.from_settings(
        tmp_path / "book", {"provenance": corpus_settings(store="logs/store")}
    )
    (tmp_path / "book" / ".git").mkdir(parents=True)
    with project_scope(project):
        store.init(project)
        assert "versions it" in backup.unconfigured(_source(project))


def test_status_prints_the_backup_and_what_to_do(book, nas, capsys):
    source = _source(book)
    _directory(source, nas)
    store.cmd_status(None)
    out = capsys.readouterr().out
    assert "backup    nas: directory" in out
    assert "next: back the store up" in out


def test_status_without_a_backup_says_so(book, capsys):
    store.cmd_status(None)
    assert "backup    none configured" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# the command
# --------------------------------------------------------------------------- #
def _command(**options):
    defaults = {"target": None, "every_store": False, "check": False}
    return Namespace(**{**defaults, **options})


def test_the_command_backs_up_this_projects_store(book, nas, capsys):
    keep_session(book, f"cc-sessions/{KETTLE}.jsonl", _turns("a kettle"))
    source = _source(book)
    _directory(source, nas)
    assert backup.cmd_backup(_command()) == 0
    out = capsys.readouterr().out
    assert "nas" in out and "it holds 1 accepted version(s)" in out
    assert (nas / "store.toml").is_file()


def test_the_command_refuses_a_store_with_no_backup(book):
    with pytest.raises(ConfigurationError, match="no backup is configured"):
        backup.cmd_backup(_command())


def test_the_command_refuses_a_target_the_store_does_not_declare(book, nas):
    _directory(_source(book), nas)
    with pytest.raises(ConfigurationError, match="declares no target attic"):
        backup.cmd_backup(_command(target=["attic"]))


def test_a_failed_target_fails_the_command_and_names_its_log(book, tmp_path, capsys):
    source = _source(book)
    _directory(source, tmp_path / "unmounted")
    assert backup.cmd_backup(_command()) == 1
    out = capsys.readouterr().out
    assert "failed after" in out and "the log is" in out


def test_check_reaches_each_destination_and_copies_nothing(book, nas, tmp_path, capsys):
    source = _source(book)
    _declare(
        source,
        f'[targets.nas]\ndriver = "directory"\ndestination = "{nas}"\n'
        f'[targets.attic]\ndriver = "directory"\n'
        f'destination = "{tmp_path / "attic"}"\n',
    )
    assert backup.cmd_backup(_command(check=True)) == 1
    out = capsys.readouterr().out
    assert "ready" in out and "not ready" in out and "does not exist" in out
    assert list(nas.iterdir()) == []
    assert backup.read_record(source.identity, "nas") is None


def test_all_backs_up_every_store_under_the_home(tmp_path, monkeypatch, capsys):
    home = tmp_path / "stores"
    monkeypatch.setenv("QUIREWORK_PROVENANCE_HOME", str(home))
    projects = {}
    for name in ("kettle-book", "storm-site"):
        projects[name] = Project.from_settings(
            tmp_path / name, {"provenance": {"enabled": True, "store": name}}
        )
        with project_scope(projects[name]):
            store.init(projects[name])
    nas = tmp_path / "nas"
    nas.mkdir()
    _directory(_source(projects["kettle-book"]), nas)

    # A timer runs this from the home directory, with no project at all.
    assert backup.cmd_backup(_command(every_store=True)) == 0
    out = capsys.readouterr().out
    assert "kettle-book" in out and "storm-site" in out
    assert "none configured" in out
    assert (nas / "store.toml").is_file()


def test_all_with_no_store_under_the_home_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QUIREWORK_PROVENANCE_HOME", str(tmp_path / "empty"))
    assert backup.cmd_backup(_command(every_store=True)) == 1
    assert "no provenance store is there" in capsys.readouterr().err


def test_all_with_no_store_declaring_a_backup_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QUIREWORK_PROVENANCE_HOME", str(tmp_path / "stores"))
    project = Project.from_settings(
        tmp_path / "book", {"provenance": {"enabled": True, "store": "kettle-book"}}
    )
    with project_scope(project):
        store.init(project)
    assert backup.cmd_backup(_command(every_store=True)) == 1
    assert "nothing was backed up" in capsys.readouterr().err


def test_the_store_command_dispatches_backup(book, nas):
    _directory(_source(book), nas)
    assert store.main(["backup", "--target", "nas"]) == 0
    assert (nas / "store.toml").is_file()


def test_the_backup_help_describes_every_driver_and_flag(tmp_path):
    p = subprocess.run(
        [sys.executable, "-m", "quirework", "provenance", "store", "backup", "-h"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert p.returncode == 0, p.stderr
    for words in (
        "backup.toml",
        "rclone",
        "directory",
        "git",
        "command",
        "module:function",
        "--target",
        "--all",
        "--check",
        "Exit status",
    ):
        assert words in p.stdout


# --------------------------------------------------------------------------- #
# rclone
# --------------------------------------------------------------------------- #
def _rclone(source, destination):
    _declare(
        source,
        f'[targets.cloud]\ndriver = "rclone"\ndestination = "{destination}"\n',
    )
    (driver,) = backup.targets(source.root)
    return driver


@needs_rclone
def test_rclone_carries_the_store_and_honors_every_rule(book, tmp_path):
    # rclone treats a plain path as a destination, so the driver is tested
    # end to end without a network or a configured remote.
    keep_session(book, f"cc-sessions/{KETTLE}.jsonl", _turns("a kettle"))
    keep_session(book, f"cc-sessions/{STORM}.jsonl", _turns("a storm"), pending=True)
    source = _source(book)
    cloud = tmp_path / "cloud"
    driver = _rclone(source, cloud)
    (source.root / "pending" / "cc-sessions" / ".half.jsonl.x1").write_text("{}\n")

    outcome = backup.back_up(source, driver)
    assert outcome.pending == 1 and len(outcome.versions) == 1
    held = _tree(source.root)
    held.pop("pending/cc-sessions/.half.jsonl.x1")
    assert _tree(cloud) == held

    store.reject([STORM], "a stub")
    assert backup.back_up(source, driver).removed == 1
    assert not (cloud / "pending" / "cc-sessions").exists()

    (copy,) = (cloud / "accepted").rglob(f"{KETTLE}.jsonl")
    copy.chmod(0o644)
    copy.write_bytes(copy.read_bytes() + b"damage\n")
    with pytest.raises(BackupError, match="immutable"):
        backup.back_up(source, driver)
    assert copy.read_bytes().endswith(b"damage\n")


@needs_rclone
def test_rclone_catches_up_with_an_accept_before_pruning(book, tmp_path, monkeypatch):
    keep_session(book, f"cc-sessions/{STORM}.jsonl", _turns("a storm"), pending=True)
    source = _source(book)
    cloud = tmp_path / "cloud"
    driver = _rclone(source, cloud)
    backup.back_up(source, driver)
    put = type(driver).put

    def put_then_accept(self, src, area, *, overwrite, within=(), log):
        put(self, src, area, overwrite=overwrite, within=within, log=log)
        if area == ACCEPTED and not within:
            store.accept([STORM], live=True)

    monkeypatch.setattr(type(driver), "put", put_then_accept)
    backup.back_up(source, driver)
    assert _at_destination(cloud, STORM) == {"accepted"}


@needs_rclone
def test_rclone_refuses_a_destination_of_another_store(tmp_path):
    first, second = _book(tmp_path, "book"), _book(tmp_path, "site")
    cloud = tmp_path / "cloud"
    one, other = _source(first), _source(second)
    backup.back_up(one, _rclone(one, cloud))
    with pytest.raises(BackupError, match="holds store"):
        backup.back_up(other, _rclone(other, cloud))


@needs_rclone
def test_rclone_names_a_remote_it_does_not_have(book):
    # The suite points the configuration directory at an empty one, so this
    # rclone has no remotes at all, and never reads the contributor's.
    source = _source(book)
    driver = _rclone(source, "nowhere:quirework/book")
    with pytest.raises(BackupError, match="no remote named nowhere"):
        driver.check(io.StringIO())


def test_rclone_lists_whole_trees_at_once(book, monkeypatch):
    # Without --fast-list, Google Drive answers one request per directory,
    # and every accepted version is a directory: a run with nothing to copy
    # took minutes. Nothing would fail if the flag were dropped, so pin it.
    from quirework.provenance.backup import rclone

    calls = []

    def record(argv, log, *, what, ok=(0,), **_):
        calls.append([str(a) for a in argv])
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(rclone, "run_tool", record)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/rclone")
    source = _source(book)
    driver = _rclone(source, "/srv/cloud")
    driver.put(source, PENDING, overwrite=True, log=io.StringIO())
    driver.put(source, ACCEPTED, overwrite=False, log=io.StringIO())
    driver.listing(PENDING, io.StringIO())
    assert len(calls) == 3
    assert all("--fast-list" in argv for argv in calls)


def test_rclone_absent_is_said_with_how_to_install_it(book, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    driver = _rclone(_source(book), "drive:book")
    with pytest.raises(BackupError, match="rclone is not installed"):
        driver.check(io.StringIO())


# --------------------------------------------------------------------------- #
# git
# --------------------------------------------------------------------------- #
@pytest.fixture
def git_identity(tmp_path, monkeypatch):
    """A git with no global or system configuration, and an author of its own."""
    empty = tmp_path / "gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Ada Kettle")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "ada@example.org")


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout


def test_git_commits_the_store_and_pushes_it(book, tmp_path, git_identity):
    keep_session(book, f"cc-sessions/{KETTLE}.jsonl", _turns("a kettle"))
    source = _source(book)
    (source.root / "pending" / "cc-sessions" / ".half.jsonl.x1").write_text("{}\n")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
    _declare(
        source,
        f'[targets.history]\ndriver = "git"\nremote = "{remote}"\n',
    )
    (driver,) = backup.targets(source.root)

    assert "committed" in backup.back_up(source, driver).detail
    tracked = _git(source.root, "ls-files").split()
    assert "store.toml" in tracked and not [t for t in tracked if "/." in f"/{t}"]
    assert _git(remote, "rev-parse", "main") == _git(source.root, "rev-parse", "HEAD")

    assert "nothing changed" in backup.back_up(source, driver).detail
    assert len(_git(source.root, "log", "--oneline").splitlines()) == 1


def test_git_is_not_steered_by_a_hooks_repository(
    book, tmp_path, git_identity, monkeypatch
):
    # Run from inside a git hook, the process inherits GIT_DIR; the backup
    # must still commit into the store's repository.
    elsewhere = tmp_path / "other"
    subprocess.run(["git", "init", "--quiet", str(elsewhere)], check=True)
    monkeypatch.setenv("GIT_DIR", str(elsewhere / ".git"))
    source = _source(book)
    _declare(source, '[targets.history]\ndriver = "git"\n')
    (driver,) = backup.targets(source.root)
    backup.back_up(source, driver)
    monkeypatch.delenv("GIT_DIR")
    assert _git(source.root, "log", "--oneline")
    untouched = subprocess.run(
        ["git", "-C", str(elsewhere), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
    )
    assert untouched.returncode != 0, "the hook's repository gained a commit"


def test_git_refuses_a_store_another_repository_versions(tmp_path, git_identity):
    project = Project.from_settings(
        tmp_path / "book", {"provenance": corpus_settings(store="logs/store")}
    )
    subprocess.run(["git", "init", "--quiet", str(tmp_path / "book")], check=True)
    with project_scope(project):
        store.init(project)
        source = _source(project)
        _declare(source, '[targets.history]\ndriver = "git"\n')
        (driver,) = backup.targets(source.root)
        with pytest.raises(BackupError, match="nest a second repository"):
            backup.back_up(source, driver)
    assert not (source.root / ".git").exists()


def test_a_store_that_is_its_own_repository_reads_as_outside_version_control(
    book, git_identity
):
    source = _source(book)
    _declare(source, '[targets.history]\ndriver = "git"\n')
    (driver,) = backup.targets(source.root)
    backup.back_up(source, driver)
    opened = store.open_store(book)
    assert store.describe_location(opened).endswith("(outside version control)")


# --------------------------------------------------------------------------- #
# command
# --------------------------------------------------------------------------- #
SCRIPT = """#!/bin/sh
printf '%s %s %s %s\\n' "$QUIREWORK_STORE" "$QUIREWORK_STORE_ID" \\
  "$QUIREWORK_BACKUP_TARGET" "$1" > "$2"
"""


def _script(source, text=SCRIPT, mode=0o700, name="backup.sh"):
    path = source.root / name
    path.write_text(text)
    path.chmod(mode)
    return path


def test_a_command_runs_with_the_store_described_in_its_environment(book, tmp_path):
    source = _source(book)
    _script(source)
    said = tmp_path / "said.txt"
    _declare(
        source,
        '[targets.custom]\ndriver = "command"\n'
        f'command = ["backup.sh", "--quiet", "{said}"]\n',
    )
    (driver,) = backup.targets(source.root)
    outcome = backup.back_up(source, driver)
    assert said.read_text().split() == [
        str(source.root),
        source.identity,
        "custom",
        "--quiet",
    ]
    # Quirework knows what it offered the script, not what arrived.
    assert outcome.summary().startswith("the store held")


def test_a_command_that_fails_fails_the_run_with_its_last_words(book):
    source = _source(book)
    _script(source, "#!/bin/sh\necho 'the share went away' >&2\nexit 3\n")
    _declare(source, '[targets.custom]\ndriver = "command"\ncommand = ["backup.sh"]\n')
    (driver,) = backup.targets(source.root)
    with pytest.raises(BackupError, match="status 3: the share went away"):
        backup.back_up(source, driver)
    assert backup.read_record(source.identity, "custom")["attempt"]["ok"] is False


@pytest.mark.parametrize(
    "mode, said",
    [
        (0o720, "writable by others"),
        (0o706, "writable by others"),
        (0o600, "not executable"),
    ],
)
def test_a_command_script_anyone_else_could_change_is_refused(book, mode, said):
    source = _source(book)
    script = _script(source)
    script.chmod(mode)
    _declare(source, '[targets.custom]\ndriver = "command"\ncommand = ["backup.sh"]\n')
    (driver,) = backup.targets(source.root)
    with pytest.raises(BackupError, match=said):
        driver.check(io.StringIO())


def test_a_command_script_that_is_a_link_is_refused(book, tmp_path):
    source = _source(book)
    real = tmp_path / "elsewhere.sh"
    real.write_text(SCRIPT)
    real.chmod(0o700)
    (source.root / "backup.sh").symlink_to(real)
    _declare(source, '[targets.custom]\ndriver = "command"\ncommand = ["backup.sh"]\n')
    (driver,) = backup.targets(source.root)
    with pytest.raises(BackupError, match="not a regular file"):
        driver.check(io.StringIO())
    assert stat.S_ISLNK(os.lstat(source.root / "backup.sh").st_mode)


# --------------------------------------------------------------------------- #
# extensions
# --------------------------------------------------------------------------- #
EXTENSION = """
from quirework.provenance.backup.directory import Directory


def mirror(target, settings, root):
    return Directory(target, {"destination": settings["destination"]}, root)


def stranger(target, settings, root):
    return object()
"""


def test_an_installed_extension_can_be_a_driver(book, nas, tmp_path, monkeypatch):
    (tmp_path / "kettle_backup.py").write_text(EXTENSION)
    monkeypatch.syspath_prepend(str(tmp_path))
    source = _source(book)
    _declare(
        source,
        f'[targets.mine]\ndriver = "kettle_backup:mirror"\ndestination = "{nas}"\n',
    )
    (driver,) = backup.targets(source.root)
    backup.back_up(source, driver)
    assert (nas / "store.toml").is_file()

    _declare(source, '[targets.mine]\ndriver = "kettle_backup:stranger"\n')
    with pytest.raises(ConfigurationError, match="must return a quirework backup"):
        backup.targets(source.root)


def test_hidden_names_are_any_dot_component():
    assert base.hidden(".git")
    assert base.hidden("cc-sessions/.accepting-1/a.jsonl")
    assert not base.hidden(f"cc-sessions/{KETTLE}.jsonl")
    assert PENDING == "pending"


def test_config_check_names_each_target_and_refuses_a_broken_file(
    tmp_path, nas, capsys
):
    from quirework.cli import main

    root = tmp_path / "book"
    root.mkdir()
    (root / "quirework.toml").write_text(
        "schema = 2\n[provenance]\nenabled = true\n"
        f'root = "private/corpus"\nstore_dir = "{tmp_path / "book-store"}"\n'
    )
    project = Project.load(root)
    with project_scope(project):
        store.init(project)
    source = _source(project)
    _directory(source, nas)
    assert main(["--project", str(root), "config", "check"]) == 0
    assert "Provenance backup: nas, directory" in capsys.readouterr().out

    _declare(source, "[targets.nas]\ndriver = 'ftp'\n")
    assert main(["--project", str(root), "config", "check"]) == 2
    assert "driver must be one of" in capsys.readouterr().err
