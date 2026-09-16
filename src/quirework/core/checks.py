"""Run an explicitly configured project check suite."""

import argparse
import ast
import subprocess
from pathlib import Path
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
    failed = []
    for rel in result.stdout.splitlines():
        path = root / rel
        if path.suffix == ".py" and path.is_file():
            try:
                ast.parse(path.read_text(), filename=rel)
            except SyntaxError as exc:
                failed.append(str(exc))
    for message in failed:
        print(message)
    print(f"Python source checks: {'failed' if failed else 'passed'}")
    return int(bool(failed))


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
