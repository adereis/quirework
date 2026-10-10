"""Side Quest's own checks, run by `quirework dev check`.

Everything Quirework's default suite checks, plus the book's first house
rule: no em dash anywhere in the manuscript. That covers every Markdown,
Quarto, YAML and SVG file under `manuscript.source`: the chapters, the
spec, the sidecars, the Quarto file, whose title reaches the page too,
and the figures, whose text does. Then the figures' own rule, the book's
two inks, from `figures`.

An em dash reaches a rendered page by more routes than the character
itself. Pandoc's smart typography turns three hyphens into one, and an
HTML entity renders as one, so both are refused as well. A line that is
nothing but hyphens is a rule or a YAML delimiter, and passes.

The derived editions under `publish.variants` are the book too, in
another shape, so their composed documents, sidecars and Quarto files are
held to the same rule.

A saved run record, under `runs.evidence_roots`, is what a model wrote,
kept as it wrote it, and so is an edition's `_raw/` drafts. The rule is
for the book, not for the evidence, so it never reads either, wherever it
lies.
"""

from __future__ import annotations

import argparse
import re
import subprocess

from quirework.core.artifact_paths import run_roots
from quirework.core.checks import default_checks
from quirework.project import current_project

from sq_checks import figures

RULE = "No em dash"
RULE_DESCRIPTION = "nothing in the manuscript renders as an em dash"

SUFFIXES = (".qmd", ".md", ".yml", ".yaml", ".svg")

# Spelled as an escape, so this file never holds the character it refuses.
EM_DASH = chr(0x2014)

_ENTITY = re.compile(r"&mdash;|&#8212;|&#x2014;", re.IGNORECASE)
_TRIPLE = re.compile(r"(?<!-)---(?!-)")
_ONLY_HYPHENS = re.compile(r"^\s*-{3,}\s*$")


def _manuscript_files(root, *sources):
    # The same listing the default suite reads: tracked and untracked
    # files, and never anything git ignores.
    result = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            *sources,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    evidence = tuple(f"{r}/" for r in run_roots())
    for rel in result.stdout.splitlines():
        if rel.startswith(evidence) or "/_raw/" in rel:
            continue
        path = root / rel
        if path.suffix in SUFFIXES and path.is_file():
            yield rel, path


def em_dashes():
    project = current_project()
    sources = [project.path("manuscript.source"), project.path("publish.variants")]
    problems = []
    for rel, path in _manuscript_files(project.root, *sources):
        smart = path.suffix == ".qmd"
        lines = path.read_text(encoding="utf-8").splitlines()
        for n, line in enumerate(lines, 1):
            if EM_DASH in line:
                problems.append(f"{rel}:{n}: em dash")
            if _ENTITY.search(line):
                problems.append(f"{rel}:{n}: an em dash entity")
            if smart and not _ONLY_HYPHENS.match(line) and _TRIPLE.search(line):
                problems.append(f"{rel}:{n}: three hyphens, an em dash once rendered")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(prog="quirework dev check")
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        default_checks(["--list"])
        print("Side Quest:")
        print(f"  {RULE:22} {RULE_DESCRIPTION}")
        print(f"  {figures.RULE:22} {figures.RULE_DESCRIPTION}")
        return 0
    # The default suite takes --staged as it always does. The em dash rule
    # reads the whole manuscript either way: it is small, and a staged-only
    # read would pass a commit that leaves an older em dash standing.
    status = default_checks(["--staged"] if args.staged else [])
    problems = em_dashes()
    for problem in problems:
        print(problem)
    print(f"{RULE}: {'failed' if problems else 'passed'}")
    inks = figures.two_inks()
    for problem in inks:
        print(problem)
    print(f"{figures.RULE}: {'failed' if inks else 'passed'}")
    return status | int(bool(problems)) | int(bool(inks))
