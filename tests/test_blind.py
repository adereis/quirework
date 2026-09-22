"""The jail's shape and the pruning of seats' private runner homes.

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


def test_agy_gets_a_filtered_bus_and_nothing_else_of_the_runtime_dir(
    tmp_path, fake_bus, monkeypatch
):
    monkeypatch.setattr(blind, "runner_binds", lambda names: ([], []))
    run_dir = tmp_path / "seat"
    run_dir.mkdir()
    with pytest.raises(blind.BlindnessError, match="org.freedesktop.secrets"):
        blind.jail_argv(run_dir, runners=["agy"], bind_credentials=False)
    with blind.session_bus(["agy"]) as bus:
        argv = blind.jail_argv(
            run_dir, runners=["agy"], bind_credentials=False, bus=bus
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
            run_dir, runners=["agy"], bind_credentials=False, bus=bus
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
