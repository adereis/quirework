"""Run an explicitly configured project check suite."""

import argparse
import ast
import shutil
import subprocess
from quirework.project import current_project, extension


def default_checks(argv=None):
    parser = argparse.ArgumentParser(prog="quirework dev check")
    parser.add_argument("--staged", action="store_true")
    args = parser.parse_args(argv)
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

    Silent where the formatter is absent, because a project that does not use
    one has nothing here to be wrong about, and a check that fails on a
    missing tool teaches people to ignore it.
    """
    if not paths or not shutil.which("ruff"):
        return 0
    result = subprocess.run(
        ["ruff", "format", "--check", *paths],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        print("Formatting: passed")
        return 0
    drifted = sorted(
        {
            line.split("--> ")[1].split(":")[0]
            for line in result.stdout.splitlines()
            if "--> " in line
        }
    )
    for rel in drifted:
        print(f"unformatted: {rel}")
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
