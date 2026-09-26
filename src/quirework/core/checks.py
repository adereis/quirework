"""Run an explicitly configured project check suite."""

import argparse
import ast
import json
import shutil
import subprocess
from pathlib import Path
from quirework.project import current_project, extension


SUITE = (
    ("Python source checks", "every selected .py file parses"),
    ("Formatting", "`ruff format` would leave the tree unchanged"),
)


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
    command = (
        [
            "git",
            "-C",
            str(root),
            "diff",
            "--cached",
            "--name-only",
            "--diff-filter=ACMR",
        ]
        if args.staged
        else [
            "git",
            "-C",
            str(root),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
        ]
    )
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    failed, sources = [], []
    for rel in result.stdout.splitlines():
        path = root / rel
        if path.suffix == ".py" and path.is_file():
            sources.append(rel)
            try:
                ast.parse(path.read_text(), filename=rel)
            except SyntaxError as exc:
                failed.append(str(exc))
    for message in failed:
        print(message)
    print(f"Python source checks: {'failed' if failed else 'passed'}")
    return int(bool(failed)) | formatting_check(root, sources)


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
