"""Capture Antigravity sessions while preserving curation and replacements.

Captures land in the store's pending area. A session captured before, pending
or accepted, is captured again only with ``--force``, because an Antigravity
transcript that differs from its capture has as often been rewritten under it
as grown.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from quirework.project import current_project
from quirework.provenance import agyfilter, store, trees, worktrees
from quirework.runs.storage import shown


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


# An absolute path starts a word. Without the look-behind, the tail of a
# relative path read as one: `source .venv/bin/activate` "opened"
# `/bin/activate`, `docs/AGENTS.md` opened `/AGENTS.md`, and either is outside
# every checkout, so a working session that began with one was skipped as a
# review run. Many such skips were right by accident: evaluation seats from
# when captures lived inside the project, read by a relative path that the
# evaluation-seat rule, which looks for the store, does not recognize. A
# project with seats like that lists them as removed before relying on this.
_ABSOLUTE = re.compile(r"(?<![\w.~*/-])/[A-Za-z0-9._/-]+")


def working_context(rows):
    """First absolute path actually passed to a tool, excluding runtime state."""
    own = str(Path.home() / ".gemini")
    for row in rows:
        for value in _strings(row.get("tool_calls", [])):
            for path in _ABSOLUTE.findall(value):
                if not path.startswith(own):
                    return path
    return None


def evaluates_corpus(rows, root):
    """Whether the first human turn hands the session a capture from the store."""
    project = current_project()
    prefixes = [str(root / area) for area in ("pending", "accepted")]
    if root.is_relative_to(project.root):
        prefixes += [
            str((root / a).relative_to(project.root)) for a in ("pending", "accepted")
        ]
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


def _damage(dropped, recovered):
    """A capture line's note on what was wrong with its transcript, or nothing."""
    notes = []
    torn = dropped.get("<unparseable>", 0)
    if torn:
        notes.append(f"{torn} line(s) did not parse")
    if recovered:
        steps = ", ".join(str(step) for step in recovered)
        notes.append(f"step(s) {steps} taken from {agyfilter.CUT}")
    return f" ({'; '.join(notes)})" if notes else ""


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
    corpus = store.open_store()
    dest = corpus.pending_dir(store.AGY)
    dest.mkdir(parents=True, exist_ok=True)
    removed = store.removed_ids("agy")
    from quirework.provenance.ingestion.claude import accepted_versions

    accepted = accepted_versions(store.AGY)
    roots = worktrees.project_trees(project.root)
    excluding = worktrees.excluded_trees(project, roots)
    for tree in trees.record(project):
        print(f"agy-sessions: recorded project tree {shown(tree)}")
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
        # A damaged transcript is read around, not skipped. Antigravity can
        # write a record over another one, leaving a line that does not parse,
        # and `agyfilter.records` takes the destroyed step from the cut-down
        # copy beside it. These rows decide whether to EXCLUDE the session, so
        # a tell lost with a destroyed record could let a review run or a seat
        # through. Skipping such a transcript traded that for something worse:
        # it left a session uncaptured behind a printed "needs a look" nobody
        # was obliged to act on, while the runtime's copy, the only one, aged.
        # A wrong capture costs one read and a rejection at the next ingest; a
        # skipped session is lost. So the session is captured, and its line
        # says how it was damaged, for the read that judges it.
        rows, dropped, recovered = agyfilter.records(source)
        if not rows:
            counts["unreadable"] += 1
            print(
                f"  ? {directory.name} has no record that parses as JSON "
                "-- not captured, needs a look"
            )
            continue
        context = working_context(rows)
        if context and not worktrees.contains(roots, context, excluding):
            counts["review"] += 1
            print(
                f"  - {directory.name} opened in {context} -- review run, not captured"
            )
            continue
        if evaluates_corpus(rows, corpus.root):
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
        captures = [c for c in (output, *accepted.get(output.name, ())) if c.is_file()]
        damage = _damage(dropped, recovered)
        if not captures:
            agyfilter.write_capture(source, output)
            counts["new"] += 1
            print(f"  + {sid}{damage}")
        elif any(agyfilter.is_current(source, c) for c in captures):
            counts["unchanged"] += 1
        elif args.force is not None and (not args.force or sid in args.force):
            agyfilter.write_capture(source, output)
            counts["replaced"] += 1
            print(f"  ~ {sid} (replaced){damage}")
        else:
            counts["differ"] += 1
            why = agyfilter.why_not_current(source, captures[0])
            print(f"  ! {sid} is not current: {why} (kept)")
    print(
        "agy-sessions: {new} new, {replaced} replaced, {unchanged} unchanged, {differ} differ, {curated} curated out, {review} review run(s), {evals} evaluation seat(s), {unreadable} unreadable -> ".format(
            **counts
        )
        + shown(dest)
    )
    return 0
