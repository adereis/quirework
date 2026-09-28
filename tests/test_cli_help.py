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


def test_the_top_level_help_names_every_group(capsys):
    from quirework.cli import main

    with pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0
    output = capsys.readouterr().out
    for group in ("spec", "publish", "text", "provenance", "sop", "dev", "config"):
        assert group in output
