"""Run an explicitly configured project check suite."""

import argparse
import ast
import contextlib
import io
import json
import shutil
import subprocess
from pathlib import Path
from quirework.project import current_project, extension
from quirework.text import mdwrap


SUITE = (
    ("Python source checks", "every selected .py file parses"),
    ("Formatting", "`ruff format` would leave the tree unchanged"),
    ("Markdown wrapping", "`quirework text wrap` would leave the tree unchanged"),
)

# What `text wrap` reads. Selecting by suffix before asking the project's
# rules keeps a binary file away from a classifier written for prose.
MARKDOWN = (".md", ".qmd")


def default_checks(argv=None):
    parser = argparse.ArgumentParser(prog="quirework dev check")
    parser.add_argument("--staged", action="store_true")
    # The dispatcher offers --list for any suite, so the default one has to
    # answer it too. It reached this parser and was rejected as unknown,
    # which made the option unusable in exactly the projects that have no
    # configured suite to describe.
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        print("Default suite:")
        for name, description in SUITE:
            print(f"  {name:22} {description}")
        return 0
    root = current_project().root
    # Both listings name files from the project's root and stop at its edge.
    # `ls-files` does that on its own; `diff` names them from the repository's
    # top unless told otherwise, which for a book kept inside a larger
    # repository pointed every path at a file that was not there.
    if args.staged:
        staged = _git(
            root, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "--relative"
        )
        return _suite(root, staged)
    # Tracked files, which include a new one the moment it is added, so
    # nothing reaches a commit unjudged. A file nobody added is the author's
    # scratch until then, and a red that only means "you keep notes here"
    # teaches people to ignore red.
    status = _suite(root, _git(root, "ls-files", "--cached"))
    _advise(root, _git(root, "ls-files", "--others", "--exclude-standard"))
    return status


def _git(root, *args):
    command = ["git", "-C", str(root), *args]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return result.stdout.splitlines()


def _suite(root, files):
    """Run every check over ``files``, relative to ``root``; 1 if any fails."""
    failed, sources, markdown = [], [], []
    for rel in files:
        path = root / rel
        if path.suffix in MARKDOWN and path.is_file() and _own(root, rel):
            markdown.append(rel)
        if path.suffix == ".py" and path.is_file():
            sources.append(rel)
            try:
                ast.parse(path.read_text(), filename=rel)
            except SyntaxError as exc:
                failed.append(str(exc))
    for message in failed:
        print(message)
    print(f"Python source checks: {'failed' if failed else 'passed'}")
    return (
        int(bool(failed))
        | formatting_check(root, sources)
        | wrapping_check(root, markdown)
    )


def _advise(root, files):
    """Judge untracked files, and say what would fail, without failing.

    The same checks as the suite, so a new chapter not yet added is warned
    about early; only the files they name are shown, under a heading that
    says they are not counted.
    """
    if not files:
        return
    report = io.StringIO()
    try:
        with contextlib.redirect_stdout(report):
            status = _suite(root, files)
    except SystemExit as exc:  # the wrapper refusing a file it cannot reflow
        status = 1
        report.write(f"{exc}\n")
    if not status:
        return
    print("Untracked, so not counted until `git add` (or ignore them):")
    for line in report.getvalue().splitlines():
        if not any(line.startswith(f"{name}:") for name, _ in SUITE):
            print(f"  {line}")


def _own(root, rel):
    """Whether a file belongs to this project rather than to one inside it.

    A directory holding its own `quirework.toml` is a project of its own, as
    an example book inside this repository is, and its configuration chooses
    how its Markdown wraps. Judged by the outer project's rules, a book that
    keeps its chapters one sentence per line would be told to hard-wrap
    them. It answers to its own check instead.
    """
    return not any(
        (root / folder / "quirework.toml").is_file()
        for folder in Path(rel).parents[:-1]
    )


def formatting_check(root, paths):
    """Whether the formatter would still change any of these files.

    Not a style opinion. It asserts the tree already sits at the formatter's
    fixed point, so a contributor who runs the formatter out of tidiness gets
    an empty diff instead of reformatting a neighbour's code inside a commit
    about something else. That drift is how this repository came to hold
    ninety-five formatted files and ten that were not, and the stray hunks it
    produces are invisible in review because they look like the change they
    are hiding in.

    Tolerant of an absent formatter, because a project that does not use one
    has nothing here to be wrong about, and a check that fails on a missing
    tool teaches people to ignore it. It says so rather than saying nothing:
    printing no line at all is indistinguishable from passing, which is how a
    machine without the formatter reports a clean tree over a drifted one.
    """
    if not paths:
        return 0
    if not shutil.which("ruff"):
        print("Formatting: skipped, no formatter installed")
        return 0
    # The report is read as JSON, never as the text a person would see. That
    # text follows the caller's environment: FORCE_COLOR puts escape codes
    # between a label and the file it names, and outranks NO_COLOR, so a
    # session that forced colour got a check naming "\x1b[0mdrifted.py".
    # RUFF_OUTPUT_FORMAT can change the layout altogether. The flag outranks
    # both, and JSON carries no colour.
    result = subprocess.run(
        ["ruff", "format", "--check", "--output-format", "json", *paths],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        print("Formatting: passed")
        return 0
    try:
        diagnostics = json.loads(result.stdout)
    except json.JSONDecodeError:
        # No report at all: the formatter refused the invocation itself (an
        # unreadable configuration, or a ruff too old for --output-format).
        # Its own words are the only diagnosis there is.
        print(result.stderr.strip())
        print("Formatting: failed; the formatter did not run")
        return 1
    base = Path(root).resolve()
    for item in sorted(diagnostics, key=lambda d: (d["filename"], d["code"])):
        path = Path(item["filename"])
        rel = path.relative_to(base) if path.is_relative_to(base) else path
        if item["code"] == "unformatted":
            print(f"unformatted: {rel}")
        else:
            print(f"{item['code']}: {rel}: {item['message']}")
    print("Formatting: failed; run `ruff format` and commit that on its own")
    return 1


def wrapping_check(root, paths):
    """Whether `text wrap` would still change any of these Markdown files.

    The same fixed point as `formatting_check`, held for prose. A guide off
    it is reflowed whole the first time somebody wraps it to fit a
    one-paragraph edit, so a change made with the project's own tool
    carries hundreds of lines that are not the change. The project's
    `text.modes` or `text.classifier` chooses each file's convention, and
    a file inside a saved run record is never asked to move.

    That exemption reaches only the records a project declares. A room's
    prompts and replies, saved where the project never said, are named here
    like any guide, and wrapping them would rewrite what a model was sent
    and what it said. So the failure says where such a file belongs before
    it says how to wrap one.

    Unlike the formatter, the wrapper ships with Quirework, so there is no
    missing tool to tolerate and no reason to say "skipped".
    """
    if not paths:
        return 0
    drifted = [rel for rel in paths if not mdwrap.at_fixed_point(str(root / rel))]
    for rel in drifted:
        print(f"needs wrapping: {rel}")
    if drifted:
        print(
            "Markdown wrapping: failed; a saved run record belongs under "
            "`runs.evidence_roots`, and anything else wants `quirework text "
            "wrap`, committed on its own"
        )
        return 1
    print("Markdown wrapping: passed")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="quirework dev check", description=__doc__)
    parser.add_argument(
        "--staged", action="store_true", help="check the paths selected for commit"
    )
    parser.add_argument("--list", action="store_true", help="list the configured suite")
    if argv is not None and any(a in ("-h", "--help") for a in argv):
        parser.parse_args(argv)
        return 0
    reference = current_project().get("checks.command")
    if reference is None:
        return default_checks(argv)
    if reference == "quirework.core.checks:main":
        raise ValueError("checks.command recursively selects its own dispatcher")
    return extension(reference)(argv)
