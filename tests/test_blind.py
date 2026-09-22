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


def test_prune_refuses_where_it_cannot_tell_a_live_seat_from_an_idle_one(
    tmp_path, monkeypatch
):
    """Without /proc, ``occupied`` has no answer. That used to read as "not
    in use", so on such a machine `--force` would remove a running seat's
    home; the only safe reading of no answer is to keep the home."""
    _, home = seat(tmp_path, "mara")
    monkeypatch.setattr(blind, "liveness_known", lambda: False)
    report = blind.prune([tmp_path], grace=0, force=True)
    assert home.exists()
    assert report["removed"] == []
    assert "liveness unknown" in report["kept"][0][2]


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


def test_excluded_mounts_reads_fstypes_and_escaped_paths(tmp_path):
    table = tmp_path / "mounts"
    table.write_text(
        "rootfs / ext4 rw 0 0\n"
        "/etc/auto.nas /nas autofs rw 0 0\n"
        "//srv/home /nas/me cifs rw 0 0\n"
        "srv:/x /mnt/share\\040two nfs4 rw 0 0\n"
        "gvfsd /run/user/1000/gvfs fuse.gvfsd-fuse rw 0 0\n"
        "tmpfs /tmp tmpfs rw 0 0\n"
    )
    from pathlib import Path

    assert blind.excluded_mounts(table) == [
        Path("/nas"),
        Path("/mnt/share two"),
        Path("/nas/me"),
        Path("/run/user/1000/gvfs"),
    ]
    assert blind.excluded_mounts(tmp_path / "absent") == []


def test_root_binds_leave_out_excluded_mounts_and_keep_special_files(tmp_path):
    import socket

    fake = tmp_path / "root"
    for name in ("usr", "etc", "mnt/share two", "mnt/other", "nas/me", "home"):
        (fake / name).mkdir(parents=True)
    (fake / "bin").symlink_to("usr/bin")
    (fake / "vmlinuz").write_text("")
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(fake / "mnt/bus"))
    try:
        binds = blind.root_binds([fake / "nas", fake / "mnt/share two"], fake)
    finally:
        listener.close()
    targets = [b[1] for b in binds]
    assert str(fake / "nas") not in targets
    assert str(fake / "mnt/share two") not in targets
    assert ("--bind", str(fake / "mnt/other"), str(fake / "mnt/other")) in binds
    assert ("--bind", str(fake / "mnt"), str(fake / "mnt")) not in binds
    assert ("--bind", str(fake / "usr"), str(fake / "usr")) in binds
    assert ("--symlink", "usr/bin", str(fake / "bin")) in binds
    assert ("--ro-bind", str(fake / "vmlinuz"), str(fake / "vmlinuz")) in binds
    assert ("--bind", str(fake / "mnt/bus"), str(fake / "mnt/bus")) in binds
    # Nothing excluded: the whole root in one bind, as the jail always did.
    assert blind.root_binds([], fake) == [("--bind", str(fake), str(fake))]


def test_jail_assembles_the_root_and_hides_an_outside_project(tmp_path, monkeypatch):
    from pathlib import Path

    run_dir = tmp_path / "seat"
    run_dir.mkdir()
    monkeypatch.setattr(blind, "excluded_mounts", lambda path=None: [])
    argv = blind.jail_argv(run_dir, project=tmp_path / "project")
    assert argv[:4] == [blind.BWRAP, "--bind", "/", "/"]
    # The project sits under the home here, which is already blanked.
    assert "--tmpfs" in argv and str(tmp_path / "project") not in argv
    outside = Path("/srv/checkout")
    argv = blind.jail_argv(run_dir, project=outside)
    assert argv[argv.index(str(outside)) - 1] == "--tmpfs"
    assert argv.index(str(outside)) < argv.index(str(run_dir))
    # An excluded share: the root is assembled entry by entry instead.
    monkeypatch.setattr(
        blind, "excluded_mounts", lambda path=None: [Path("/no-such-share")]
    )
    argv = blind.jail_argv(run_dir, project=None)
    assert argv[1:4] != ["--bind", "/", "/"]
    assert ("--bind", "/usr", "/usr") in zip(argv, argv[1:], argv[2:])
    assert "--dev-bind" in argv and "--proc" in argv
