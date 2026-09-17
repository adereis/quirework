"""Capture Antigravity sessions while preserving curation and replacements."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from quirework.project import current_project
from quirework.provenance import agyfilter, worktrees


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def working_context(rows):
    """First absolute path actually passed to a tool, excluding runtime state."""
    own = str(Path.home() / ".gemini")
    for row in rows:
        for value in _strings(row.get("tool_calls", [])):
            for path in re.findall(r"/[A-Za-z0-9._/-]+", value):
                if not path.startswith(own):
                    return path
    return None


def evaluates_corpus(rows, corpus):
    project = current_project()
    prefixes = {str(corpus)}
    try:
        prefixes.add(str(corpus.relative_to(project.root)))
    except ValueError:
        pass
    for row in rows:
        if str(row.get("source", "")).startswith("USER"):
            text = " ".join(_strings(row))
            return any(
                re.search(
                    re.escape(prefix) + r"/(?:cc|agy|codex)-sessions/[^\s\"]+\.jsonl",
                    text,
                )
                for prefix in prefixes
            )
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(prog="quirework provenance ingest agy")
    parser.add_argument(
        "--force",
        nargs="*",
        metavar="SESSION",
        help="replace changed captures, optionally restricted to IDs",
    )
    args = parser.parse_args(argv)
    project = current_project()
    brain = project.path(
        "providers.agy.sessions", str(Path.home() / ".gemini/antigravity-cli/brain")
    )
    if not brain.is_dir():
        raise ValueError(f"Antigravity session directory is missing: {brain}")
    dest = project.path("provenance.agy_sessions")
    dest.mkdir(parents=True, exist_ok=True)
    removed_file = dest / "removed-sessions.txt"
    removed = (
        {
            line.split("#", 1)[0].strip().removesuffix(".jsonl")
            for line in removed_file.read_text().splitlines()
        }
        if removed_file.exists()
        else set()
    )
    roots = worktrees.project_trees(project.root)
    counts = dict(
        new=0,
        replaced=0,
        unchanged=0,
        differ=0,
        curated=0,
        review=0,
        evals=0,
        unreadable=0,
    )
    for directory in sorted(brain.iterdir()):
        if not directory.is_dir():
            continue
        logs = directory / ".system_generated/logs"
        source = logs / "transcript_full.jsonl"
        if not source.is_file():
            source = logs / "transcript.jsonl"
        if not source.is_file():
            continue
        text = source.read_text()
        if not any(str(root) in text for root in roots):
            continue
        try:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError as exc:
            # Antigravity writes some records with a literal newline inside a
            # string value, so one record arrives as several lines and none of
            # the fragments parse. Nine of 308 transcripts were like this, and
            # one of them aborted the whole sweep: 299 readable transcripts
            # went unswept because the loop raised on the first bad one.
            #
            # The transcript is skipped whole rather than parsed around. What
            # these rows decide is whether to EXCLUDE this session -- a review
            # run, or a seat that was handed a transcript to evaluate -- and
            # both are positive detections, so a tell lost with an unparsed
            # fragment is a capture that should never have happened. Reading
            # part of a transcript to decide whether to keep all of it is the
            # wrong trade against a private corpus.
            counts["unreadable"] += 1
            print(
                f"  ? {directory.name} transcript does not parse as JSONL "
                f"({exc}) -- not captured, needs a look"
            )
            continue
        context = working_context(rows)
        if context and not worktrees.contains(roots, context):
            counts["review"] += 1
            print(
                f"  - {directory.name} opened in {context} -- review run, not captured"
            )
            continue
        if evaluates_corpus(rows, project.path("provenance.root")):
            counts["evals"] += 1
            print(
                f"  - {directory.name} was sent a transcript to evaluate -- not a session, not captured"
            )
            continue
        sid = directory.name
        if sid in removed:
            counts["curated"] += 1
            continue
        output = dest / f"{sid}.jsonl"
        if not output.exists():
            agyfilter.write_capture(source, output)
            counts["new"] += 1
            print(f"  + {sid}")
        elif agyfilter.is_current(source, output):
            counts["unchanged"] += 1
        elif args.force is not None and (not args.force or sid in args.force):
            agyfilter.write_capture(source, output)
            counts["replaced"] += 1
            print(f"  ~ {sid} (replaced)")
        else:
            counts["differ"] += 1
            print(f"  ! {sid} differs from its source (kept)")
    print(
        "agy-sessions: {new} new, {replaced} replaced, {unchanged} unchanged, {differ} differ, {curated} curated out, {review} review run(s), {evals} evaluation seat(s), {unreadable} unreadable -> ".format(
            **counts
        )
        + str(dest)
    )
    return 0
