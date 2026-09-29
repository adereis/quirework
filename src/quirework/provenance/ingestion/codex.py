#!/usr/bin/env python3
"""Mirror this repo's Codex rollout transcripts into ``codex-sessions``.

Codex writes rollouts below ``$CODEX_HOME/sessions/YYYY/MM/DD/`` (normally
``~/.codex/sessions``). A rollout's ``session_meta`` records the literal working
directory it ran in, so the sweep selects this repository by that recorded cwd
rather than by the date-shaped storage path -- which picks up sessions launched
from a subdirectory and from every `git worktree` checkout in one pass
(`worktrees.py` owns which directories those are).

**This is a manual tool, deliberately.** Codex on Linux is beta and its rollout
schema is explicitly not a stable interface, so nothing here runs on a lifecycle
hook: the author runs it when he wants the backlog imported, which is also when
he is present to see what it did. (Claude Code's `sync_sessions.py` is wired to
`SessionEnd`; this one is not, and that asymmetry is the point.)

Captures are normalized provenance records and preserve source mtime. The
normalizer keeps human prompts, visible assistant prose, plaintext reasoning,
and compact action metadata; runtime transport, tool outputs, inline files, and
opaque reasoning state stay in the source rollout. A sweep may read a rollout
that is still being written, so the capture records the source state it consumed
and a later sweep refreshes it when the source grows. Sessions listed in
``removed-sessions.txt`` are never re-imported. Rollouts belonging to a *system*
subagent are skipped and counted (see ``SYSTEM_SUBAGENTS``). Delegated seats
that act on a configured raw capture are skipped and reported as evaluations,
so an ingest does not seed the next ingest. Main sessions stay for curation.
Capturing never stages or commits sessions.

Pure stdlib and runnable under a bare ``python3`` with no venv and no installed
package.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path
from quirework.project import current_project

# tools/session-sync/<this> -> repo root

from quirework.provenance import codexfilter, trees
from quirework.provenance import worktrees  # noqa: E402  (path set above)
from quirework.runs.storage import shown


SESSION_ID = re.compile(
    r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?:\.jsonl)?", re.I
)

# Subagents the *runtime* spawns for its own housekeeping, not delegations the
# author or the agent made. `guardian` is Codex's action-risk judge: it fires on
# planned tool calls, so it runs once per main session and its rollout is a
# replay of that session's transcript wrapped in a security-policy prompt. Half
# the rollouts on disk are guardians. Capturing them would duplicate the real
# session's content under a machine-written prompt, inflate every metric, and
# feed the blind cold-read a copy of the project's own instructions. This is not
# a curation call the ingest read should have to make thirteen times a day; it
# is the Codex equivalent of a hook firing. Author-directed subagents are NOT in
# this set and are captured as sessions in their own right.
SYSTEM_SUBAGENTS = {"guardian"}


def codex_home() -> Path:
    """Codex state root, honoring its documented environment override."""
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def load_removed() -> set[str]:
    """Session ids deliberately curated out of the Codex corpus."""
    if not (
        current_project().path("provenance.codex_sessions") / "removed-sessions.txt"
    ).exists():
        return set()
    ids = set()
    for lineno, line in enumerate(
        (current_project().path("provenance.codex_sessions") / "removed-sessions.txt")
        .read_text(encoding="utf-8")
        .splitlines(),
        1,
    ):
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        match = SESSION_ID.fullmatch(entry)
        if not match:
            print(
                f"codex-sessions: {(current_project().path('provenance.codex_sessions') / 'removed-sessions.txt').name}:{lineno}: not a session id, "
                f"ignoring {entry!r}",
                file=sys.stderr,
            )
            continue
        ids.add(match.group(1).lower())
    return ids


def read_meta(path: Path) -> dict:
    """Best-effort ``session_meta.payload`` from a rollout."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if obj.get("type") == "session_meta" and isinstance(
                    obj.get("payload"), dict
                ):
                    return obj["payload"]
    except OSError:
        pass
    return {}


def session_id(path: Path) -> str | None:
    """Stable id from metadata, falling back to the rollout filename."""
    meta = read_meta(path)
    value = meta.get("id") or meta.get("session_id")
    if value and SESSION_ID.fullmatch(str(value)):
        return str(value).lower()
    match = SESSION_ID.search(path.name)
    return match.group(1).lower() if match else None


def system_subagent(meta: dict) -> str | None:
    """The system-subagent kind of a rollout, or ``None`` for anything to keep.

    The metadata form is ``source: {"subagent": {"other": "guardian"}}``; older
    and newer shapes put a bare string there. Anything not named in
    ``SYSTEM_SUBAGENTS`` -- including every author-directed subagent -- is not a
    system rollout and is captured normally.
    """
    source = (meta or {}).get("source")
    if not isinstance(source, dict):
        return None
    sub = source.get("subagent")
    names = []
    if isinstance(sub, str):
        names = [sub]
    elif isinstance(sub, dict):
        names = [str(v) for v in sub.values() if isinstance(v, str)]
        names += [str(k) for k in sub]
    for name in names:
        if name.lower() in SYSTEM_SUBAGENTS:
            return name.lower()
    return None


def _capture_pattern(roots):
    project = current_project()
    prefixes = set()
    for key in ("claude_sessions", "agy_sessions", "codex_sessions"):
        folder = project.path(f"provenance.{key}")
        prefixes.add(str(folder))
        prefixes.add(folder.name)
        try:
            relative = folder.relative_to(project.root)
        except ValueError:
            continue
        prefixes.add(str(relative))
        prefixes.update(str(root / relative) for root in roots)
    # Relative spellings must start a path, not match the tail of another
    # project's absolute path. Each project supplies its own corpus layout.
    return re.compile(
        r"(?<![\w./~-])(?:"
        + "|".join(re.escape(p) for p in sorted(prefixes, key=len, reverse=True))
        + r")/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.jsonl\b",
        re.I,
    )


def evaluation_seat(path, meta, pattern):
    """A delegated seat acted on a configured raw capture, not just prose."""
    if not codexfilter.subagent_kind(meta):
        return False
    return any(pattern.search(value) for value in codexfilter.tool_inputs(path))


def sweep(repo=None):
    """Every saved rollout recorded as running in one of this project's trees.

    Returns ``(keep, system, evaluation)``: selected rollouts, runtime
    subagents, and delegated capture-evaluation seats. Exclusions remain
    available to the caller so every selection rule can be reported.
    """
    roots = worktrees.project_trees(repo or current_project().root)
    excluding = worktrees.excluded_trees(current_project(), roots)
    home = codex_home()
    keep, system, evaluation = [], [], []
    pattern = _capture_pattern(roots)
    for root in (home / "sessions", home / "archived_sessions"):
        if not root.is_dir():
            continue
        for path in root.rglob("*.jsonl"):
            meta = read_meta(path)
            if not worktrees.contains(roots, meta.get("cwd"), excluding):
                continue
            if system_subagent(meta):
                system.append(path)
            elif evaluation_seat(path, meta, pattern):
                evaluation.append(path)
            else:
                keep.append(path)
    return sorted(set(keep)), sorted(set(system)), sorted(set(evaluation))


def captured_source_fingerprint(dst: Path) -> tuple[int, str] | None:
    """Raw source ``(byte count, SHA-256)`` recorded by a capture."""
    if not dst.exists():
        return None
    meta = codexfilter.session_meta(dst)
    if meta.get("capture_schema") != codexfilter.CAPTURE_SCHEMA:
        return None
    try:
        return int(meta["source_bytes"]), str(meta["source_sha256"])
    except (KeyError, TypeError, ValueError):
        return None


def source_fingerprint(path: Path) -> tuple[int, str]:
    """Current byte count and SHA-256 for a raw rollout."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def write_capture(src: Path, dst: Path) -> None:
    """Normalize ``src`` into ``dst`` atomically, preserving source mtime."""
    data = codexfilter.normalize_capture(src)
    source = src.stat()
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=dst.parent, prefix=f".{dst.name}.", delete=False
        ) as fh:
            tmp = Path(fh.name)
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.utime(tmp, ns=(source.st_atime_ns, source.st_mtime_ns))
        os.replace(tmp, dst)
        tmp = None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def copy_if_changed(src: Path, sid: str) -> bool:
    """Normalize a new/grown rollout, preserving source state and mtime."""
    dst = current_project().path("provenance.codex_sessions") / f"{sid}.jsonl"
    captured = captured_source_fingerprint(dst)
    if captured is not None and source_fingerprint(src) == captured:
        return False
    write_capture(src, dst)
    return True


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="quirework provenance ingest codex")
    parser.parse_args(argv)

    current_project().path("provenance.codex_sessions").mkdir(
        parents=True, exist_ok=True
    )
    for tree in trees.record(current_project()):
        print(f"codex-sessions: recorded project tree {shown(tree)}")
    sources, system, evaluation = sweep()
    removed = load_removed()
    copied = []
    unchanged = skipped = invalid = 0
    for src in sources:
        sid = session_id(src)
        if not sid:
            invalid += 1
            continue
        if sid in removed:
            skipped += 1
            continue
        if copy_if_changed(src, sid):
            copied.append((src, sid))
        else:
            unchanged += 1

    for src in evaluation:
        sid = session_id(src)
        label = f"{sid}.jsonl" if sid else src.name
        print(f"  - {label} was sent a session capture to evaluate -- not captured")
    for src, sid in copied:
        print(f"  + {sid}.jsonl  ({src.stat().st_size} bytes)")
    print(
        f"codex-sessions: {len(copied)} updated, {unchanged} unchanged, "
        f"{skipped} on removed-list, {len(system)} system subagent(s), "
        f"{len(evaluation)} evaluation seat(s), "
        f"{invalid} without a usable id -> {current_project().path('provenance.codex_sessions')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
