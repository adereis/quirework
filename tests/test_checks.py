"""The default check suite; no project corpus and no live tooling required."""

import subprocess

from quirework.core import checks
from quirework.project import Project, project_scope

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
    assert "Markdown wrapping" in printed


def test_listing_a_suite_runs_nothing(monkeypatch, capsys):
    """Describing the checks must not be a way to accidentally run them."""

    def refuse(*args, **kwargs):
        raise AssertionError("--list ran a check")

    monkeypatch.setattr(checks.subprocess, "run", refuse)
    assert checks.default_checks(["--list"]) == 0
    assert "passed" not in capsys.readouterr().out


def test_staged_files_are_named_from_the_project_not_the_repository(tmp_path, capsys):
    """A book kept inside a larger repository is checked, and only it is.

    Git names staged files from the repository's top, and the check reads
    them from the project's root. For such a book the two differ, so every
    staged path named a file that was not there and the suite passed
    having read nothing at all.
    """
    repo = tmp_path / "repo"
    book = repo / "book"
    book.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (book / "broken.py").write_text("def (:\n")
    (repo / "outside.py").write_text("def (:\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    with project_scope(Project.from_settings(book, {})):
        assert checks.default_checks(["--staged"]) == 1
    printed = capsys.readouterr().out
    assert "broken.py" in printed
    assert "outside.py" not in printed


WRAPPED = "A guide wrapped the way the wrapper wraps it.\n"
HAND_WRAPPED = "A guide\nwrapped by hand.\n"
SENTENCES = "One sentence on its line.\nAnother on the next.\n"


def git_project(root, settings=None):
    """A project at the top of a repository with nothing committed yet."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return Project.from_settings(root, settings or {})


def test_a_guide_off_the_wrapper_s_fixed_point_fails_and_is_named(tmp_path, capsys):
    """A guide off it is reflowed whole the first time somebody wraps it."""
    project = git_project(tmp_path / "repo")
    (project.root / "docs").mkdir()
    (project.root / "docs" / "wrapped.md").write_text(WRAPPED)
    (project.root / "docs" / "drifted.md").write_text(HAND_WRAPPED)
    with project_scope(project):
        assert checks.default_checks([]) == 1
    printed = capsys.readouterr().out
    assert "needs wrapping: docs/drifted.md\n" in printed
    assert "docs/wrapped.md" not in printed
    assert "committed on its own" in printed
    assert (project.root / "docs" / "drifted.md").read_text() == HAND_WRAPPED


def test_the_failure_says_where_a_saved_record_belongs(tmp_path, capsys):
    """Wrapping an undeclared record would rewrite what a model was sent."""
    project = git_project(tmp_path / "repo")
    (project.root / "room" / "records").mkdir(parents=True)
    (project.root / "room" / "records" / "prompt.md").write_text(HAND_WRAPPED)
    with project_scope(project):
        assert checks.wrapping_check(project.root, ["room/records/prompt.md"]) == 1
    printed = capsys.readouterr().out
    assert "saved run record belongs under `runs.evidence_roots`" in printed


def test_a_tree_at_the_wrapper_s_fixed_point_passes(tmp_path, capsys):
    project = git_project(tmp_path / "repo")
    (project.root / "README.md").write_text(WRAPPED)
    with project_scope(project):
        assert checks.wrapping_check(project.root, ["README.md"]) == 0
    assert "Markdown wrapping: passed" in capsys.readouterr().out


def test_the_project_s_own_modes_choose_the_convention(tmp_path):
    """One sentence per line is drift for a guide and the fixed point for a post."""
    modes = {"text": {"modes": [["content/**/*.md", "sentence"]]}}
    project = git_project(tmp_path / "repo", modes)
    (project.root / "content").mkdir()
    (project.root / "content" / "post.md").write_text(SENTENCES)
    with project_scope(project):
        assert checks.wrapping_check(project.root, ["content/post.md"]) == 0
    with project_scope(Project.from_settings(project.root, {})):
        assert checks.wrapping_check(project.root, ["content/post.md"]) == 1


def test_a_saved_run_record_is_never_asked_to_move(tmp_path):
    """A record is evidence and stays exactly as the run wrote it."""
    project = git_project(tmp_path / "repo", {"runs": {"evidence_roots": ["runs"]}})
    (project.root / "runs" / "r001").mkdir(parents=True)
    (project.root / "runs" / "r001" / "answer.md").write_text(HAND_WRAPPED)
    with project_scope(project):
        assert checks.wrapping_check(project.root, ["runs/r001/answer.md"]) == 0


def test_a_project_inside_the_project_answers_to_its_own_rules(tmp_path, capsys):
    """An example book keeps its own wrap modes, and the outer rules never judge them.

    Its chapters are one sentence per line, which the outer project's
    default would call drift. Only the outer project's own file is named.
    """
    project = git_project(tmp_path / "repo")
    book = project.root / "examples" / "book"
    book.mkdir(parents=True)
    (book / "quirework.toml").write_text("schema = 2\n")
    (book / "chapters").mkdir()
    (book / "chapters" / "one.md").write_text(SENTENCES)
    (project.root / "examples" / "README.md").write_text(HAND_WRAPPED)
    with project_scope(project):
        assert checks.default_checks([]) == 1
    printed = capsys.readouterr().out
    assert "needs wrapping: examples/README.md\n" in printed
    assert "one.md" not in printed
