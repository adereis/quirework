"""Every advertised command must expose its real help without doing work."""

import os
import subprocess
import sys

import pytest
from quirework.cli import COMMANDS


@pytest.mark.parametrize("command", list(COMMANDS))
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_command_help(command, flag, tmp_path):
    env = dict(os.environ)
    env.pop("QUIREWORK_PROJECT", None)
    p = subprocess.run(
        [sys.executable, "-m", "quirework", *command, flag],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert "usage:" in p.stdout.lower()
    assert "Traceback" not in p.stderr


@pytest.mark.parametrize(
    "arguments",
    [
        ["publish", "redpen", "build", "--help"],
        ["publish", "redpen", "read", "--help"],
        ["provenance", "coldread", "analyze", "--help"],
        ["provenance", "input", "compile", "--help"],
        ["sop", "runs", "prepare", "--help"],
        ["sop", "runs", "start", "--help"],
        ["sop", "skills", "--help"],
    ],
)
def test_nested_help(arguments, tmp_path):
    p = subprocess.run(
        [sys.executable, "-m", "quirework", *arguments],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert "usage:" in p.stdout.lower()
    if arguments[:3] == ["publish", "redpen", "build"]:
        assert "--gray" in p.stdout
        assert "--chapter" in p.stdout


@pytest.mark.parametrize(
    "arguments, described",
    [
        (
            ["provenance", "store", "--help"],
            ["init", "status", "verify", "missed", "backup", "backup.toml"],
        ),
        (["provenance", "store", "backup", "--help"], ["--target", "--all"]),
        (["provenance", "store", "status", "--help"], ["Writes nothing"]),
        (["provenance", "ingest", "--help"], ["whose sessions to capture"]),
        (["provenance", "ingest", "claude", "--help"], ["--hook", "SessionEnd"]),
    ],
)
def test_the_store_and_capture_say_what_each_choice_does(
    arguments, described, tmp_path
):
    # An action or flag listed with no words beside it leaves the reader to
    # guess, and these are the commands a failed capture sends people to.
    p = subprocess.run(
        [sys.executable, "-m", "quirework", *arguments],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    for words in described:
        assert words in p.stdout


def test_the_top_level_help_names_every_group(capsys):
    from quirework.cli import main

    with pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0
    output = capsys.readouterr().out
    for group in ("spec", "publish", "text", "provenance", "sop", "dev", "config"):
        assert group in output


def test_a_checkout_shows_no_vendoring_note(tmp_path):
    # The suite runs from this checkout, as a project's vendored one does.
    from quirework import __version__
    from quirework.cli import INSTALL_GUIDE

    run = [sys.executable, "-m", "quirework"]
    shown = subprocess.run(
        [*run, "--help"], cwd=tmp_path, capture_output=True, text=True, timeout=15
    )
    assert shown.returncode == 0, shown.stderr
    assert INSTALL_GUIDE not in shown.stdout
    version = subprocess.run(
        [*run, "--version"], cwd=tmp_path, capture_output=True, text=True, timeout=15
    )
    assert version.stdout.strip() == f"quirework {__version__}"


def test_an_installed_package_recommends_a_vendored_checkout(monkeypatch, capsys):
    from quirework import __version__, cli

    monkeypatch.setattr(cli, "source_checkout", lambda package=None: None)
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.strip() == (
        f"quirework {__version__} (installed package)"
    )
    # A bare `quirework` prints the same help as `--help`.
    assert cli.main([]) == 0
    assert cli.VENDORING_NOTE in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert cli.VENDORING_NOTE in capsys.readouterr().out


def test_source_checkout_tells_a_checkout_from_an_installed_package(tmp_path):
    from quirework.cli import source_checkout

    checkout = tmp_path / "vendor" / "quirework"
    (checkout / "src" / "quirework").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert source_checkout(checkout / "src" / "quirework") == checkout
    installed = tmp_path / "lib" / "python3.14" / "site-packages" / "quirework"
    installed.mkdir(parents=True)
    assert source_checkout(installed) is None
    # A `src` directory that nothing builds is not a checkout.
    stray = tmp_path / "elsewhere" / "src" / "quirework"
    stray.mkdir(parents=True)
    assert source_checkout(stray) is None
