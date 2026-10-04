"""The jail: what `quirework sop blind run` binds, blanks and refuses.

These are the invariants that make a confined seat different from a seat that
merely started somewhere tidy. The live end of it -- that each runner's real
binary still starts inside the jail, and that the tree is genuinely unreachable
-- is `quirework sop blind check`, which needs the runners installed and so is not
a unit test; it is the thing to run after upgrading one.
"""

import json
from pathlib import Path

import pytest

from quirework.providers import blind


def _argv(tmp_path, **kw):
    # These tests read the jail's shape; no seat is launched in it.
    kw.setdefault("canary", blind.PROBE)
    return blind.jail_argv(tmp_path, **kw)


def _pairs(argv, flag):
    """Every (src, dst) for a bwrap flag, in order."""
    return [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a == flag]


# --------------------------------------------------------------------------- #
# what it blanks and binds
# --------------------------------------------------------------------------- #
def test_home_is_blanked_wholesale(tmp_path):
    """An allowlist, not a denylist: the leak is always a dir nobody listed."""
    tmpfs = [
        argvv
        for i, argvv in enumerate(_argv(tmp_path))
        if _argv(tmp_path)[i - 1] == "--tmpfs"
    ]
    assert str(Path.home()) in tmpfs
    assert "/tmp" in tmpfs and "/var/tmp" in tmpfs


def test_run_dir_is_bound_writable_and_is_the_cwd(tmp_path):
    argv = _argv(tmp_path)
    assert (str(tmp_path), str(tmp_path)) in _pairs(argv, "--bind")
    assert argv[argv.index("--chdir") + 1] == str(tmp_path)


def test_run_dir_bind_comes_after_every_read_only_bind(tmp_path):
    """bwrap applies operations in order, so a later ro-bind would shadow the
    one directory the seat must be able to write."""
    argv = _argv(tmp_path, runners=["claude"])
    assert argv.index("--bind", argv.index("--chdir") - 3) > max(
        i for i, a in enumerate(argv) if a == "--ro-bind"
    )


def test_pwd_is_rewritten_and_oldpwd_dropped(tmp_path):
    """--chdir moves the process, not the inherited shell variables; a seat
    that echoes $PWD would otherwise read back the launcher's path."""
    argv = _argv(tmp_path)
    i = argv.index("PWD")
    assert argv[i - 1] == "--setenv" and argv[i + 1] == str(tmp_path)
    assert "OLDPWD" in argv and argv[argv.index("OLDPWD") - 1] == "--unsetenv"


# --------------------------------------------------------------------------- #
# the private per-runner home
# --------------------------------------------------------------------------- #
def test_runner_gets_a_private_home_bound_over_the_real_one(tmp_path):
    argv = _argv(tmp_path, runners=["claude"])
    private = blind.state_root(tmp_path) / "claude"
    real = Path(blind.os.path.expanduser("~/.claude")).resolve()
    assert (str(private), str(real)) in _pairs(argv, "--bind")


def test_private_home_lives_outside_the_run_dir(tmp_path):
    """Jail furniture the seat can see tells the seat it is in a jail."""
    root = blind.state_root(tmp_path)
    assert tmp_path not in root.parents and root != tmp_path


def test_private_home_persists_across_invocations(tmp_path):
    """Two `blind run` calls are two processes with two fresh tmpfs homes, so
    a multi-turn seat resuming a session needs its state dir to be the same
    directory both times -- else turn 2 silently starts a new session."""
    a = blind.state_root(tmp_path) / "codex"
    _argv(tmp_path, runners=["codex"])
    assert a.is_dir()
    assert blind.state_root(tmp_path) / "codex" == a


def test_credentials_are_bound_after_the_private_home(tmp_path):
    """They live *inside* it, so binding them first is silently undone."""
    argv = _argv(tmp_path, runners=["claude"])
    cred = Path(blind.os.path.expanduser("~/.claude/.credentials.json"))
    if not cred.exists():
        pytest.skip("no claude credentials on this machine")
    home_at = argv.index(str(Path(blind.os.path.expanduser("~/.claude")).resolve()))
    assert argv.index(str(cred)) > home_at


# --------------------------------------------------------------------------- #
# runner detection
# --------------------------------------------------------------------------- #
def test_detects_the_runner_when_it_is_not_argv0():
    assert blind.detect_runners(["stdbuf", "-oL", "agy", "-p", "x"]) == ["agy"]
    assert blind.detect_runners(["timeout", "900", "codex", "exec"]) == ["codex"]


def test_a_prompt_mentioning_a_runner_is_not_a_runner():
    """Whole-token match: a prompt arrives as ONE argument, never a bare name."""
    assert blind.detect_runners(
        ["claude", "-p", "compare this with claude and codex"]
    ) == ["claude"]


# --------------------------------------------------------------------------- #
# refusing, rather than degrading
# --------------------------------------------------------------------------- #
def test_confine_raises_when_it_cannot_confine(tmp_path, monkeypatch):
    """An unconfined seat produces evidence indistinguishable from a confined
    seat's, so this is never a warning."""
    monkeypatch.setattr(blind, "backend_error", lambda: "no bwrap here")
    with pytest.raises(blind.BlindnessError) as e:
        blind.confine(["claude", "-p", "x"], tmp_path)
    assert "unconfined" in str(e.value)


def test_receipt_records_confinement_and_lands_outside_the_run_dir(tmp_path):
    run = tmp_path / "seat"
    run.mkdir()
    r = run.parent / f"{run.name}.blind-receipt.json"
    blind.write_receipt(
        r,
        confined=True,
        cmd=["claude", "-p", "x"],
        run_dir=run,
        runners=["claude"],
        binds=[],
    )
    assert r.exists() and r.parent != run
    d = json.loads(r.read_text())
    assert d["confined"] is True and d["backend"] == "bwrap"
    assert str(Path.home()) in d["blanked"]


# --------------------------------------------------------------------------- #
# the caller that is not confining a seat to a packet
# --------------------------------------------------------------------------- #
def test_expose_run_dir_false_leaves_the_workspace_to_the_caller(tmp_path):
    """An editorial reviewer stands in the repository, not in a frozen packet.

    It still wants the blanked `$HOME` and the private runner home -- that is
    what keeps the operator's memory out -- so it reuses this jail and appends
    its own binds and chdir. Nothing here may bind or enter the run dir, which
    for that caller holds only private state.
    """
    argv = _argv(tmp_path, expose_run_dir=False)
    assert (str(tmp_path), str(tmp_path)) not in _pairs(argv, "--bind")
    assert "--chdir" not in argv
    assert str(Path.home()) in [
        argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"
    ]


def test_expose_run_dir_false_still_unsets_the_inherited_cwd(tmp_path):
    """OLDPWD names the launcher's directory whoever sets the chdir."""
    assert "--unsetenv" in _argv(tmp_path, expose_run_dir=False)


def test_exposing_the_run_dir_stays_the_default(tmp_path):
    argv = _argv(tmp_path)
    assert argv[argv.index("--chdir") + 1] == str(tmp_path)
    assert (str(tmp_path), str(tmp_path)) in _pairs(argv, "--bind")
