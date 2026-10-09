"""The jail's shape, the free flag probes, and the pruning of seats'
private runner homes.

No model is called. Two tests start a real bubblewrap jail, and one of
them a real bus proxy, when the machine has them.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

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
    assert not home.is_dir()
    assert blind.tombstone(home)["freed_bytes"] == report["freed"] > 0
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


# --------------------------------------------------------------------------- #
# homes in the cache
# --------------------------------------------------------------------------- #
def linked(root, name):
    """A workspace whose private homes were made where every new one is."""
    work = root / name
    work.mkdir(parents=True)
    link = blind.make_state_root(work)
    (link / "codex" / "sessions").mkdir(parents=True)
    (link / "codex" / "sessions" / "0.jsonl").write_bytes(b"x" * 2048)
    return work, link


def age(path, seconds=7200):
    old = time.time() - seconds
    for item in [path, *path.rglob("*")]:
        os.utime(item, (old, old))


def test_a_home_lives_in_the_tagged_cache_and_is_linked_beside_the_run(tmp_path):
    """Backups keep a state directory and skip a tagged cache; the records
    stay where they were and only the home moves."""
    work, link = linked(tmp_path, "mara")
    target = link.resolve()
    assert link.is_symlink() and link == blind.state_root(work)
    assert target.parent == blind.homes_root()
    assert blind.homes_root().is_relative_to(Path(os.environ["XDG_CACHE_HOME"]))
    tag = (blind.homes_root() / "CACHEDIR.TAG").read_text()
    assert tag.startswith("Signature: 8a477f597d28d172789f06886806bc55\n")
    assert oct(target.stat().st_mode & 0o777) == "0o700"
    assert blind.make_state_root(work) == link and link.resolve() == target


def test_a_home_made_before_the_move_follows_its_run_into_the_cache(tmp_path):
    """A run already under way leaves the backed-up state at its next turn,
    sessions and all."""
    work, home = seat(tmp_path, "mara")
    sessions = sorted(p.name for p in (home / "codex" / "sessions").iterdir())
    blind.make_state_root(work)
    assert home.is_symlink() and home.resolve().parent == blind.homes_root()
    assert sorted(p.name for p in (home / "codex" / "sessions").iterdir()) == sessions


def test_a_home_on_another_filesystem_stays_where_it_is(tmp_path, monkeypatch):
    work, home = seat(tmp_path, "mara")

    def across(source, target):
        raise OSError(blind.errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(blind.os, "rename", across)
    assert blind.make_state_root(work) == home
    assert home.is_dir() and not home.is_symlink()
    stem = f"{tmp_path.name}.mara-"
    assert not [p for p in blind.homes_root().iterdir() if p.name.startswith(stem)]


def test_a_cleared_or_pruned_home_gives_way_to_a_fresh_one(tmp_path):
    work, link = linked(tmp_path, "mara")
    shutil.rmtree(link.resolve())
    fresh = blind.make_state_root(work)
    assert fresh.is_dir() and not list(fresh.iterdir())
    blind.bury(fresh, {"pruned_at": "2026-01-01T00:00:00Z"})
    assert blind.make_state_root(work).is_dir()


def test_discarding_a_home_removes_the_cache_and_the_link(tmp_path):
    work, link = linked(tmp_path, "mara")
    target = link.resolve()
    blind.discard_state_root(work)
    assert not target.exists() and not link.is_symlink()
    blind.discard_state_root(work)  # nothing left, and nothing to refuse


def test_prune_removes_the_cache_and_leaves_a_tombstone(tmp_path):
    work, link = linked(tmp_path / "room" / "seats", "mara")
    target = link.resolve()
    age(target)
    assert blind.private_homes(tmp_path) == [link]
    report = blind.prune([tmp_path], grace=3600)
    assert report["removed"] == [(link, report["freed"], "removed")]
    assert not target.exists() and not link.is_symlink()
    assert blind.tombstone(link)["freed_bytes"] == report["freed"] > 0
    assert blind.private_homes(tmp_path) == []


def test_prune_never_follows_a_link_out_of_the_homes_directory(tmp_path):
    """A link is only a name; what it leads to is checked before removal."""
    keep = tmp_path / "elsewhere"
    (keep / "codex").mkdir(parents=True)
    (keep / "codex" / "notes.txt").write_text("not a home")
    age(keep)
    blind.state_root(tmp_path / "runs" / "mara").parent.mkdir(parents=True)
    blind.state_root(tmp_path / "runs" / "mara").symlink_to(keep)
    report = blind.prune([tmp_path / "runs"], grace=0, force=True)
    assert (keep / "codex" / "notes.txt").exists()
    assert "outside the homes directory" in report["kept"][0][2]
    blind.discard_state_root(tmp_path / "runs" / "mara")
    assert (keep / "codex" / "notes.txt").exists()


def test_a_running_seat_keeps_its_home_through_the_link(tmp_path, monkeypatch):
    """A process standing in the cache directory is in that seat's home."""
    _, link = linked(tmp_path, "mara")
    here = link.resolve() / "codex"
    iterdir, readlink = Path.iterdir, Path.readlink
    monkeypatch.setattr(
        Path,
        "iterdir",
        lambda self: (
            iter([Path("/proc/4242")]) if self == Path("/proc") else iterdir(self)
        ),
    )
    monkeypatch.setattr(
        Path,
        "readlink",
        lambda self: here if self == Path("/proc/4242/cwd") else readlink(self),
    )
    assert blind.occupied(link) == 4242


def test_a_held_home_is_kept_however_old_it_is(tmp_path):
    """A room between rounds looks idle, and its next round resumes the
    session the home holds."""
    work, link = linked(tmp_path, "mara")
    blind.hold(work, "room r resumes seat mara's session until it finishes")
    age(link.resolve())
    report = blind.prune([tmp_path], grace=0)
    assert link.resolve().is_dir()
    assert report["kept"][0][2] == (
        "held: room r resumes seat mara's session until it finishes"
    )


def test_a_released_home_goes_by_age_alone(tmp_path):
    work, link = linked(tmp_path, "mara")
    blind.hold(work, "a reason")
    blind.release(work)
    blind.release(work)  # releasing twice is not an error
    age(link.resolve())
    assert blind.prune([tmp_path], grace=3600)["removed"]


def test_a_forced_prune_records_what_the_home_was_held_for(tmp_path):
    work, link = linked(tmp_path, "mara")
    blind.hold(work, "review r can be continued at any time")
    blind.prune([tmp_path], grace=0, force=True)
    assert blind.tombstone(link)["held"] == "review r can be continued at any time"
    with pytest.raises(
        blind.SessionGone, match="by force, while held: review r can be continued"
    ):
        blind.require_session(work, "codex", SESSION)


def test_the_hold_is_out_of_every_seats_sight(tmp_path):
    """Only a runner's own directory is bound over its home in the jail."""
    work, link = linked(tmp_path, "mara")
    blind.hold(work, "a reason")
    assert (link / blind.HOLD).is_file()
    assert not list((link / "codex").rglob(blind.HOLD))


def test_holding_a_run_that_has_no_homes_makes_none(tmp_path):
    blind.hold(tmp_path / "never-launched", "a reason")
    blind.release(tmp_path / "never-launched")
    assert not blind.state_root(tmp_path / "never-launched").exists()


def test_an_unreadable_hold_still_holds(tmp_path):
    work, link = linked(tmp_path, "mara")
    (link / blind.HOLD).write_text("not json")
    assert "can no longer be read" in blind.held(link)


SESSION = "0199c0de-0000-7000-8000-00000000abcd"


@pytest.mark.parametrize(
    "runner, relative",
    [
        ("codex", f"sessions/2026/10/06/rollout-2026-10-06T20-08-06-{SESSION}.jsonl"),
        ("claude", f"projects/-seat/{SESSION}.jsonl"),
        (
            "agy",
            f"antigravity-cli/brain/{SESSION}/.system_generated/logs/"
            "transcript_full.jsonl",
        ),
    ],
)
def test_a_resume_finds_its_session_where_the_runner_keeps_it(
    tmp_path, runner, relative
):
    work = tmp_path / "seat"
    work.mkdir()
    path = blind.state_root(work) / runner / relative
    path.parent.mkdir(parents=True)
    path.write_text("{}\n")
    assert blind.require_session(work, runner, SESSION) == path


def test_a_resume_is_refused_once_the_home_is_gone(tmp_path):
    with pytest.raises(blind.SessionGone, match="private home .* is gone"):
        blind.require_session(tmp_path / "seat", "codex", SESSION)


def test_a_resume_is_refused_when_the_home_lost_the_session(tmp_path):
    work, _ = seat(tmp_path, "mara")
    with pytest.raises(blind.SessionGone, match="no longer holds it"):
        blind.require_session(work, "codex", SESSION)


def test_a_resume_says_when_its_home_was_pruned(tmp_path):
    work, link = linked(tmp_path, "mara")
    age(link.resolve())
    blind.prune([tmp_path], grace=3600)
    with pytest.raises(blind.SessionGone, match=r"pruned at \d{4}-\d\d-\d\dT"):
        blind.require_session(work, "codex", SESSION)


def test_a_resume_says_the_cache_was_cleared(tmp_path):
    """The cache cleaner takes a home untouched for a month, link and all
    left behind, and that is a different cause from a prune."""
    work, link = linked(tmp_path, "mara")
    shutil.rmtree(link.resolve())
    with pytest.raises(
        blind.SessionGone, match="the cache it lived in, .*, was cleared"
    ):
        blind.require_session(work, "codex", SESSION)


def test_a_session_id_is_matched_literally(tmp_path):
    """An id is spliced into a glob, so a stray `*` must not match another."""
    work = tmp_path / "seat"
    other = blind.state_root(work) / "claude" / "projects" / "-seat" / "x.jsonl"
    other.parent.mkdir(parents=True)
    other.write_text("{}\n")
    with pytest.raises(blind.SessionGone):
        blind.require_session(work, "claude", "*")


def test_every_runner_says_where_its_sessions_live():
    assert all("{id}" in runner.session for runner in blind.RUNNERS.values())


def test_the_root_is_an_allow_list_of_host_trees(tmp_path):
    fake = tmp_path / "root"
    for name in ("usr/bin", "etc", "opt", "var/lib", "var/mnt", "home/me", "nas"):
        (fake / name).mkdir(parents=True)
    (fake / "run/systemd/resolve").mkdir(parents=True)
    (fake / "lib32").mkdir()
    (fake / "bin").symlink_to("usr/bin")
    stub = fake / "run/systemd/resolve/stub-resolv.conf"
    stub.write_text("nameserver 127.0.0.53\n")
    (fake / "etc/resolv.conf").symlink_to("../run/systemd/resolve/stub-resolv.conf")
    triples = blind.system_root(fake)
    bound = [t[1] for t in triples if t[0] == "--ro-bind"]
    assert bound[:2] == [str(fake / "usr"), str(fake / "etc")]
    assert str(fake / "opt") in bound and str(fake / "var/lib") in bound
    assert str(fake / "lib32") in bound, "a real directory alias is bound"
    assert ("--symlink", "usr/bin", str(fake / "bin")) in triples
    assert str(stub) in bound, "the resolver's real file, or nothing resolves"
    for absent in ("home", "nas", "var/mnt", "var", "run"):
        assert str(fake / absent) not in bound
    assert all(t[1] == t[2] for t in triples if t[0] == "--ro-bind")
    trees = blind.bound_trees(triples)
    assert blind.visible(fake / "opt/src/book", trees)
    assert not blind.visible(fake / "home/me/book", trees)


def test_a_missing_required_tree_is_named(tmp_path):
    (tmp_path / "bare/etc").mkdir(parents=True)
    with pytest.raises(blind.BlindnessError, match="usr is missing"):
        blind.system_root(tmp_path / "bare")


def test_only_a_resolver_under_run_is_followed(tmp_path):
    fake = tmp_path / "root"
    (fake / "etc").mkdir(parents=True)
    (fake / "etc/resolv.real").write_text("")
    (fake / "etc/resolv.conf").symlink_to("resolv.real")
    assert blind.resolver_paths(fake / "etc/resolv.conf", fake) == []
    (fake / "etc/dangling").symlink_to("../run/gone")
    assert blind.resolver_paths(fake / "etc/dangling", fake) == []
    (fake / "home/me").mkdir(parents=True)
    (fake / "home/me/resolv.conf").write_text("")
    (fake / "etc/stray").symlink_to("../home/me/resolv.conf")
    with pytest.raises(blind.BlindnessError, match="outside /etc and /run"):
        blind.resolver_paths(fake / "etc/stray", fake)


def test_the_jail_binds_no_host_root_devices_or_runtime_dir(tmp_path, monkeypatch):
    run_dir = tmp_path / "seat"
    run_dir.mkdir()
    argv = blind.jail_argv(run_dir, project=tmp_path / "project")
    joined = " ".join(argv)
    for flag in blind.NAMESPACES + ("--die-with-parent",):
        assert flag in argv
    assert "--ro-bind /usr /usr" in joined and "--ro-bind /etc /etc" in joined
    assert "--bind / /" not in joined, "the root is an allow-list, never bound whole"
    assert "--dev /dev" in joined and "--dev-bind" not in argv
    assert "/run/user" not in joined and "--sync-fd" not in argv
    tmpfs = argv.index("--tmpfs")
    assert argv[tmpfs - 2 : tmpfs] == ["--size", str(blind.TMPFS_BYTES)]
    # The project sits under the home here, which is already blanked.
    assert str(tmp_path / "project") not in argv


def test_a_project_is_hidden_only_where_a_bound_tree_would_show_it(
    tmp_path, monkeypatch
):
    run_dir = tmp_path / "seat"
    run_dir.mkdir()
    outside = tmp_path.parent / "elsewhere"
    # Outside every bound tree there is nothing to cover.
    monkeypatch.setattr(blind, "BLANKED", ("/tmp", "/var/tmp"))
    assert str(outside) not in blind.jail_argv(run_dir, project=outside)
    # Inside one (a checkout under `/opt`, say) it would be readable.
    monkeypatch.setattr(blind, "OPTIONAL_TREES", (str(outside.parent),))
    argv = blind.jail_argv(run_dir, project=outside)
    assert argv[argv.index(str(outside)) - 1] == "--tmpfs"
    assert argv.index(str(outside)) < argv.index(str(run_dir))


def test_the_offline_probe_shares_the_seat_root(tmp_path):
    argv = blind.offline_argv(tmp_path)
    joined = " ".join(argv)
    assert "--unshare-net" in argv
    assert "--ro-bind /usr /usr" in joined
    assert f"--bind {tmp_path} {tmp_path}" in joined
    assert "--bind / /" not in joined


FAKE_PROXY = """#!/usr/bin/env python3
# Stands in for xdg-dbus-proxy: listens on the socket path, signals readiness
# on --fd, and exits when every reader of that pipe is gone. Proxies nothing.
import os, select, socket, sys
args = sys.argv[1:]
fd = int(next(a for a in args if a.startswith("--fd="))[5:])
path = next(a for a in args if not a.startswith("--") and not a.startswith("unix:"))
listener = socket.socket(socket.AF_UNIX)
listener.bind(path)
os.write(fd, b"\\0")
watch = select.poll()
watch.register(fd, select.POLLERR | select.POLLHUP)
watch.poll()
os.unlink(path)
"""


@pytest.fixture
def fake_bus(tmp_path, monkeypatch, request):
    """A private runtime dir, a dead bus address and the stand-in proxy.

    No test here dials the operator's session bus. The runtime dir is short
    on purpose: a socket path over 107 bytes is refused, and pytest's own
    directory names alone come close to that.
    """
    import tempfile

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    runtime = Path(tempfile.mkdtemp(prefix="rt", dir=Path.home() / "tmp"))
    request.addfinalizer(lambda: shutil.rmtree(runtime, ignore_errors=True))
    proxy = bin_dir / blind.BUS_PROXY
    proxy.write_text(FAKE_PROXY)
    proxy.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/no-bus")
    return runtime


@pytest.fixture
def scratch_runner(monkeypatch):
    """Codex given a scratch directory, so no session bus is needed."""
    from dataclasses import replace

    runner = replace(blind.RUNNERS["codex"], scratch=("unpacked/bin",))
    monkeypatch.setitem(blind.RUNNERS, "codex", runner)
    monkeypatch.setattr(blind, "runner_binds", lambda names: ([], []))
    return blind._expand(runner.home) / "unpacked" / "bin"


def test_a_scratch_directory_is_a_fresh_tmpfs_over_the_home(tmp_path, scratch_runner):
    """What a runner unpacks for one launch never reaches the disk."""
    argv = blind.jail_argv(tmp_path / "seat", runners=["codex"], canary=blind.PROBE)
    home = argv.index(str(blind._expand(blind.RUNNERS["codex"].home)))
    mount = argv.index(str(scratch_runner))
    assert argv[mount - 1] == "--tmpfs" and argv[mount - 3] == "--size"
    assert mount > home, "mounted over the bound home, so after it"


def test_a_copy_unpacked_before_is_removed_at_launch(tmp_path, scratch_runner):
    stale = blind.make_state_root(tmp_path / "seat") / "codex" / "unpacked" / "bin"
    stale.mkdir(parents=True)
    (stale / "recorder").write_bytes(b"x" * 4096)
    blind.jail_argv(tmp_path / "seat", runners=["codex"], canary=blind.PROBE)
    assert not stale.exists()


def test_a_link_a_seat_left_there_is_removed_and_never_followed(
    tmp_path, scratch_runner
):
    """The home is the seat's to write, and the cleanup runs on the host."""
    outside = tmp_path / "operator"
    outside.mkdir()
    (outside / "keep.txt").write_text("the operator's")
    planted = blind.make_state_root(tmp_path / "seat") / "codex" / "unpacked" / "bin"
    planted.parent.mkdir(parents=True)
    planted.symlink_to(outside)
    blind.jail_argv(tmp_path / "seat", runners=["codex"], canary=blind.PROBE)
    assert not planted.is_symlink()
    assert (outside / "keep.txt").read_text() == "the operator's"


def test_agy_unpacks_its_recorder_into_scratch():
    assert "antigravity-cli/bin" in blind.RUNNERS["agy"].scratch


def test_agy_gets_a_filtered_bus_and_nothing_else_of_the_runtime_dir(
    tmp_path, fake_bus, monkeypatch
):
    monkeypatch.setattr(blind, "runner_binds", lambda names: ([], []))
    run_dir = tmp_path / "seat"
    run_dir.mkdir()
    with pytest.raises(blind.BlindnessError, match="org.freedesktop.secrets"):
        blind.jail_argv(
            run_dir, runners=["agy"], bind_credentials=False, canary=blind.PROBE
        )
    with blind.session_bus(["agy"]) as bus:
        argv = blind.jail_argv(
            run_dir,
            runners=["agy"],
            bind_credentials=False,
            bus=bus,
            canary=blind.PROBE,
        )
        runtime = f"/run/user/{os.getuid()}"
        socket_path = argv[argv.index(f"{runtime}/bus") - 1]
        assert socket_path.startswith(str(fake_bus))
        assert os.path.exists(socket_path), "the proxy listens before the jail starts"
        assert bus.pass_fds == (int(argv[argv.index("--sync-fd") + 1]),)
        env = argv.index("DBUS_SESSION_BUS_ADDRESS")
        assert argv[env + 1] == f"unix:path={runtime}/bus"
        assert argv.index("--sync-fd") < argv.index(str(run_dir))
        assert bus.receipt() == {
            "names": ["org.freedesktop.secrets"],
            "proxy": blind.BUS_PROXY,
            "filtered": True,
        }
        directory = bus.directory
    assert bus.pass_fds == (), "the pipe a launch never took is released"
    assert not directory.exists(), "closing reaps the proxy and its socket dir"
    with blind.session_bus(["claude", "codex"]) as none:
        assert none is None


def test_a_launch_hands_the_pipe_over_and_drops_its_own_copy(tmp_path, fake_bus):
    with blind.session_bus(["agy"]) as bus:
        fd = bus.fd
        proc = blind.popen(["true"], bus)
        assert bus.fd is None
        with pytest.raises(OSError):
            os.fstat(fd)
        assert proc.wait() == 0


def test_a_missing_proxy_is_a_missing_prerequisite(monkeypatch):
    real = blind.shutil.which
    monkeypatch.setattr(
        blind.shutil,
        "which",
        lambda name, *a, **k: None if name == blind.BUS_PROXY else real(name, *a, **k),
    )
    assert "xdg-dbus-proxy not found" in blind.bus_proxy_missing()
    _, missing = blind.runner_binds(["agy"])
    assert any("xdg-dbus-proxy" in m for m in missing)
    _, missing = blind.runner_binds(["claude"])
    assert not any("xdg-dbus-proxy" in m for m in missing)


REAL_BWRAP = sys.platform.startswith("linux") and shutil.which("bwrap")


@pytest.mark.skipif(not REAL_BWRAP, reason="needs the real bubblewrap")
def test_inside_a_real_jail_the_root_is_the_allow_list(tmp_path):
    run_dir = tmp_path / "seat"
    run_dir.mkdir()
    script = (
        "ls /; echo RUNTIME=$(test -e /run/user && echo yes || echo no); "
        "echo PIDS=$(ls /proc | grep -c '^[0-9]')"
    )
    done = subprocess.run(
        blind.jail_argv(run_dir) + ["/bin/sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    lines = done.stdout.split()
    entries = {x for x in lines if "=" not in x}
    assert entries <= {
        "bin",
        "dev",
        "etc",
        "home",
        "lib",
        "lib32",
        "lib64",
        "libx32",
        "nix",
        "opt",
        "proc",
        "run",
        "sbin",
        "snap",
        "sys",
        "tmp",
        "usr",
        "var",
    }, entries
    assert "RUNTIME=no" in lines, "no /run/user, so no session bus to escape by"
    assert int(next(x for x in lines if x.startswith("PIDS="))[5:]) < 16


# Captured at import, before any fixture replaces it: the one test that
# dials the operator's real bus needs the real address.
HOST_BUS = {
    k: os.environ[k]
    for k in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
    if k in os.environ
}


def host_bus_answers(name):
    if not (shutil.which("dbus-send") and HOST_BUS.get("DBUS_SESSION_BUS_ADDRESS")):
        return False
    done = subprocess.run(
        [
            "dbus-send",
            "--session",
            "--print-reply",
            f"--dest={name}",
            "/",
            "org.freedesktop.DBus.Peer.Ping",
        ],
        capture_output=True,
        timeout=30,
    )
    return done.returncode == 0


@pytest.mark.skipif(
    not (
        REAL_BWRAP
        and shutil.which(blind.BUS_PROXY)
        and host_bus_answers("org.freedesktop.secrets")
    ),
    reason="needs bubblewrap, xdg-dbus-proxy and a session bus with a keyring",
)
def test_the_real_proxy_lets_through_exactly_the_granted_name(tmp_path, monkeypatch):
    for k, v in HOST_BUS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(blind, "runner_binds", lambda names: ([], []))
    run_dir = tmp_path / "seat"
    run_dir.mkdir()
    ping = (
        "dbus-send --session --print-reply --dest={} / "
        "org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1; echo {}=$?"
    )
    script = "; ".join(
        [
            ping.format("org.freedesktop.secrets", "SECRETS"),
            ping.format("org.freedesktop.systemd1", "SYSTEMD"),
            "ls /run/user/*/ | tr '\\n' ' '",
        ]
    )
    with blind.session_bus(["agy"]) as bus:
        argv = blind.jail_argv(
            run_dir,
            runners=["agy"],
            bind_credentials=False,
            bus=bus,
            canary=blind.PROBE,
        )
        proc = blind.popen(
            argv + ["/bin/sh", "-c", script],
            bus,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out, err = proc.communicate(timeout=60)
    text = out.decode()
    assert "SECRETS=0" in text, (text, err.decode())
    assert "SYSTEMD=1" in text, "the filter refuses every name but the granted one"
    assert text.strip().splitlines()[-1].strip() == "bus"


FEATURES = """\
apps                                     stable             false
apps_mcp_path_override                   removed            false
image_generation                         stable             true
"""


def test_every_codex_seat_setting_has_a_free_probe():
    """A setting added to CODEX_SEAT that no probe covers fails here, so
    `blind check` cannot quietly stop watching one."""
    labels = {p.label for p in blind.RUNNERS["codex"].probes}
    for arg in blind.CODEX_SEAT[1::2]:
        assert arg.partition("=")[0] in labels


def test_a_seat_switch_must_be_listed_and_false():
    assert blind._reads_false("apps")(FEATURES) is None
    assert "reads true" in blind._reads_false("image_generation")(FEATURES)
    # A prefix of another row's name is not that row.
    assert "names nothing" in blind._reads_false("app")(FEATURES)


def test_a_probe_verdict_fails_an_accepted_flag(monkeypatch):
    """`features list` exits 0 whether or not a switch took, so only the
    verdict can tell a live switch from a dead one."""
    from dataclasses import replace

    probe = next(p for p in blind.RUNNERS["codex"].probes if p.verdict)
    monkeypatch.setattr(
        blind.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, FEATURES, ""),
    )
    on = replace(probe, offline=False, verdict=blind._reads_false("image_generation"))
    off = replace(probe, offline=False, verdict=blind._reads_false("apps"))
    assert blind.run_probe("codex", on) == (
        False,
        "image_generation reads true under the override",
    )
    assert blind.run_probe("codex", off) == (True, "")


def test_a_keyring_login_needs_no_login_file_and_a_file_login_does(
    tmp_path, monkeypatch
):
    """Antigravity signs in through the keyring, so a fresh home with none of
    the files an older release kept still runs it; the canary proves the
    login. Claude and Codex sign in with a file, and a home without it is
    refused before any seat starts."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("agy", "codex", "claude"):
        launcher = bin_dir / name
        launcher.write_text("#!/bin/sh\n")
        launcher.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "fresh-home"))

    _, missing = blind.runner_binds(["agy"])
    assert not [m for m in missing if ".gemini" in m]
    _, missing = blind.runner_binds(["codex"])
    assert missing == ["codex: ~/.codex/auth.json not found"]
    _, missing = blind.runner_binds(["claude"])
    assert missing == ["claude: ~/.claude/.credentials.json not found"]

    # A machine that kept the old files still binds them into the jail.
    gemini = tmp_path / "fresh-home" / ".gemini"
    gemini.mkdir(parents=True)
    (gemini / "oauth_creds.json").write_text("{}")
    assert blind.RUNNERS["agy"].login == "keyring"
    assert "~/.gemini/oauth_creds.json" in blind.RUNNERS["agy"].creds
