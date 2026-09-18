"""The default check suite; no project corpus and no live tooling required."""

from quirework.core import checks

SOURCE = "def keep(value):\n    return value\n"
DRIFTED = "def keep( value ):\n  return  value\n"


def test_a_tree_at_the_formatter_s_fixed_point_passes(tmp_path, capsys):
    (tmp_path / "clean.py").write_text(SOURCE)
    assert checks.formatting_check(tmp_path, ["clean.py"]) == 0
    assert "Formatting: passed" in capsys.readouterr().out


def test_drift_fails_and_names_the_file(tmp_path, capsys):
    """The stray hunk is invisible in review; the check is where it surfaces."""
    (tmp_path / "drifted.py").write_text(DRIFTED)
    assert checks.formatting_check(tmp_path, ["drifted.py"]) == 1
    printed = capsys.readouterr().out
    assert "unformatted: drifted.py" in printed
    assert "commit that on its own" in printed


def test_a_missing_formatter_is_silent_rather_than_a_failure(
    tmp_path, capsys, monkeypatch
):
    """A check that fails on a missing tool teaches people to ignore it."""
    (tmp_path / "drifted.py").write_text(DRIFTED)
    monkeypatch.setattr(checks.shutil, "which", lambda name: None)
    assert checks.formatting_check(tmp_path, ["drifted.py"]) == 0
    assert capsys.readouterr().out == ""


def test_a_project_with_no_python_is_not_checked(tmp_path, capsys):
    assert checks.formatting_check(tmp_path, []) == 0
    assert capsys.readouterr().out == ""
