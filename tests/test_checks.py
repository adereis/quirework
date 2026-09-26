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


def test_the_caller_s_colour_and_format_settings_do_not_reach_the_report(
    tmp_path, capsys, monkeypatch
):
    """A session that forced colour got the file named after an escape code.

    FORCE_COLOR outranks NO_COLOR in the formatter, and RUFF_OUTPUT_FORMAT
    changes the layout outright, so the check has to ask for a report no
    environment setting can reshape.
    """
    monkeypatch.setenv("FORCE_COLOR", "3")
    monkeypatch.setenv("RUFF_OUTPUT_FORMAT", "grouped")
    (tmp_path / "drifted.py").write_text(DRIFTED)
    assert checks.formatting_check(tmp_path, ["drifted.py"]) == 1
    printed = capsys.readouterr().out
    assert "unformatted: drifted.py\n" in printed
    assert "\x1b" not in printed


def test_a_root_reached_through_a_symlink_still_names_files_relative_to_it(
    tmp_path, capsys
):
    """The formatter reports resolved paths; the check prints repository ones."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "drifted.py").write_text(DRIFTED)
    (tmp_path / "link").symlink_to(real)
    assert checks.formatting_check(tmp_path / "link", ["drifted.py"]) == 1
    assert "unformatted: drifted.py\n" in capsys.readouterr().out


def test_a_file_the_formatter_cannot_parse_is_named_not_dropped(tmp_path, capsys):
    (tmp_path / "broken.py").write_text("def (:\n")
    assert checks.formatting_check(tmp_path, ["broken.py"]) == 1
    assert "invalid-syntax: broken.py: " in capsys.readouterr().out


def test_a_formatter_that_refuses_the_call_says_why(tmp_path, capsys, monkeypatch):
    """No report means the formatter never checked anything; that is no pass."""

    def refused(*args, **kwargs):
        return checks.subprocess.CompletedProcess(
            args, 2, stdout="", stderr="error: unexpected argument '--output-format'"
        )

    monkeypatch.setattr(checks.subprocess, "run", refused)
    (tmp_path / "clean.py").write_text(SOURCE)
    assert checks.formatting_check(tmp_path, ["clean.py"]) == 1
    printed = capsys.readouterr().out
    assert "unexpected argument '--output-format'" in printed
    assert "the formatter did not run" in printed


def test_a_missing_formatter_is_reported_rather_than_a_failure(
    tmp_path, capsys, monkeypatch
):
    """A check that fails on a missing tool teaches people to ignore it.

    It still has to say so. Printing nothing reads exactly like passing, so
    a machine without the formatter would report a drifted tree as clean.
    """
    (tmp_path / "drifted.py").write_text(DRIFTED)
    monkeypatch.setattr(checks.shutil, "which", lambda name: None)
    assert checks.formatting_check(tmp_path, ["drifted.py"]) == 0
    printed = capsys.readouterr().out
    assert "skipped" in printed
    assert "passed" not in printed


def test_a_project_with_no_python_is_not_checked(tmp_path, capsys):
    assert checks.formatting_check(tmp_path, []) == 0
    assert capsys.readouterr().out == ""


def test_the_default_suite_can_describe_itself(capsys):
    """`--list` is offered for any suite, so the default one has to answer."""
    assert checks.default_checks(["--list"]) == 0
    printed = capsys.readouterr().out
    assert "Formatting" in printed
    assert "Python source checks" in printed


def test_listing_a_suite_runs_nothing(monkeypatch, capsys):
    """Describing the checks must not be a way to accidentally run them."""

    def refuse(*args, **kwargs):
        raise AssertionError("--list ran a check")

    monkeypatch.setattr(checks.subprocess, "run", refuse)
    assert checks.default_checks(["--list"]) == 0
    assert "passed" not in capsys.readouterr().out
