#!/usr/bin/env python3
"""Normalize an Antigravity (Gemini) transcript into a provenance capture.

The repository copy of a session is a *provenance record*, not a resumable
Antigravity checkpoint: nothing here replays a transcript, and the runtime keeps
its own copy under ``~/.gemini/antigravity-cli/brain/``.  So the capture keeps
what a reader of this corpus can ask for and no other artifact can answer, and
leaves behind what git already holds or what says nothing.

Measured over the corpus when this landed, 8.58MB across nineteen sessions:
``VIEW_FILE`` bodies alone are **43%** of it, and the injected harness
reminders, the compaction summaries, the shell output and the ``AGENTS.md``
Antigravity re-injects as ``DIRECTORY_RULES`` take it to about three quarters.
The dialogue everything is kept for -- the author's prompts, the model's prose,
its *thinking* -- is under 13%.

**Antigravity is the one corpus where thinking survives as readable text.**
Claude Code records 19,625 signature-only blocks out of 19,758 and Codex
encrypts its reasoning outright, so both keep almost none; here 748
``PLANNER_RESPONSE`` records carry 0.8MB of plain thinking, which is a tenth of
the whole corpus and the most distinctive thing in it.  It is kept verbatim.

WHAT SURVIVES

  * ``USER_INPUT`` -- the author's prompts, verbatim;
  * ``PLANNER_RESPONSE`` -- the model's prose and its thinking, verbatim, and
    its ``tool_calls`` with every parameter, so the doc/meta edit split and the
    tool counts still compute from the capture;
  * ``SYSTEM_MESSAGE`` and ``ERROR_MESSAGE`` -- see below;
  * **every other record's envelope**: its type, its ``created_at``, its
    source, status and step index.  Only the body goes.

That last point is the design.  Dropping whole records would have been simpler
and is what the Codex normalizer does, but ``catalog`` and ``metrics`` read
``created_at`` off *every* record, and two sessions in this corpus have a
dropped record at one end of their span -- the transcript is not written in
timestamp order, so the first record is not the earliest.  Keeping a
hollowed-out envelope costs about 0.2MB across the corpus and makes the capture
exactly equivalent for every reader instead of nearly so.  It also leaves the
*shape* of the session legible: that a file was read here, that the context was
compacted there, and when.

WHY ``SYSTEM_MESSAGE`` KEEPS ITS BODY

Sixteen of the twenty-two are truncated shell output and would go with the
rest.  The other six are the runtime relaying a subagent's own words back to
the parent -- ``Subagent <id> has gone idle. This was the subagent's most
recent response: I have completed the blind rewrite of ...`` -- which is
readable prose a reader could use.  Excluding *that* would be a curation call,
and a curation call belongs to the reader rather than to the archive, because
made at import it cannot be revisited: the runtime's copy ages out and the
capture becomes the only one.  Splitting the twenty-two by their ``sender=``
field would be a content sniff on a beta runtime's message format, so the whole
type keeps its body for 62kB and no reader renders any of it.  (The same
question, asked of the Codex corpus, is what put ``agent_message`` back into
its captures; ``codexfilter.normalize_capture`` carries that reasoning.)

``ERROR_MESSAGE`` keeps its body for the same reason it does in the Claude Code
captures: it is short, and it is the only record of what went wrong.

WHAT IS NOT DONE HERE

This module owns the *import boundary* only.  The agy reading surface is still
spread across ``catalog._agy_scan`` / ``_agy_edit_targets`` / ``_agy_render``,
``metrics.agy_sessions``, ``humaninput._agy_turns`` and ``show.agy_records``,
which is where it was before and is a separate consolidation.  Nothing below
renders anything.

Pure stdlib, and importable with nothing installed: the sweep is a bash script
run from a lifecycle hook under the system ``python3``.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import sys
import tempfile

from quirework.provenance.ccfilter import marker_prefix

CAPTURE_SCHEMA = "mindwright-agy-v1"
CAPTURE_TYPE = "CAPTURE_META"

# Record kinds whose ``content`` is somebody's words rather than tool output,
# injected context, or a replay of the conversation so far.
KEEP_CONTENT = ("USER_INPUT", "PLANNER_RESPONSE", "SYSTEM_MESSAGE", "ERROR_MESSAGE")

# ``tool_calls[].args`` keys that carry a *file body* rather than a parameter.
# The written half is in git and the read half was never in the argument, so
# these are truncated to a prefix; every other argument -- paths, commands,
# instructions, descriptions -- is kept whole.
FILE_BODY_ARGS = (
    "CodeContent",
    "ReplacementContent",
    "TargetContent",
    "ReplacementChunks",
)


def _prune_tool_calls(calls, dropped):
    """Tool calls with file bodies cut back to a marker prefix."""
    if not isinstance(calls, list):
        return calls
    out = []
    for call in calls:
        if not isinstance(call, dict):
            out.append(call)
            continue
        args = call.get("args")
        if not isinstance(args, dict) or not any(k in args for k in FILE_BODY_ARGS):
            out.append(call)
            continue
        pruned = dict(call)
        pruned["args"] = {}
        for key, value in args.items():
            if key not in FILE_BODY_ARGS:
                pruned["args"][key] = value
                continue
            dropped[f"args.{key}"] += 1
            # ReplacementChunks is a list of edit objects, not a string; JSON
            # is the only shape a prefix rule can take there, and it is only
            # ever read back by eye.
            pruned["args"][key] = marker_prefix(
                value
                if isinstance(value, str)
                else json.dumps(value, ensure_ascii=False)
            )
        out.append(pruned)
    return out


def normalize_capture(path):
    """Return the bytes of a normalized provenance capture for one transcript.

    Deterministic: the same source bytes give the same capture bytes, which is
    what lets the sweep decide by fingerprint and lets a migration prove itself
    by re-rendering.

    A line that does not parse is counted and dropped, which is what every
    reader in the package already does with it.  In this corpus all five are
    torn halves of a ``VIEW_FILE`` or ``EPHEMERAL_MESSAGE`` body whose content
    contained a raw newline -- freight either way, and never anything a reader
    has seen.
    """
    dropped = collections.Counter()
    source = hashlib.sha256()
    source_bytes = 0
    kept = []
    with open(path, "rb") as fh:
        for raw in fh:
            source.update(raw)
            source_bytes += len(raw)
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
                dropped["<unparseable>"] += 1
                continue
            if not isinstance(obj, dict):
                dropped["<not-an-object>"] += 1
                continue
            kind = obj.get("type")
            rec = dict(obj)
            if kind not in KEEP_CONTENT and "content" in rec:
                del rec["content"]
                dropped[f"{kind}.content"] += 1
            if "tool_calls" in rec:
                rec["tool_calls"] = _prune_tool_calls(rec["tool_calls"], dropped)
            kept.append(rec)

    # No ``created_at`` on the header, deliberately: `catalog._agy_scan` dates a
    # session from the first record that carries one, and `metrics` feeds every
    # one into the session span. A timestamp here would silently become the
    # session's start.
    header = {
        "type": CAPTURE_TYPE,
        "capture_schema": CAPTURE_SCHEMA,
        "source_sha256": source.hexdigest(),
        "source_bytes": source_bytes,
        "records": len(kept),
        "dropped": dict(sorted(dropped.items())),
    }
    return b"".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n"
        for row in [header, *kept]
    )


def capture_header(path):
    """The capture header of a normalized transcript, or ``None`` if it is raw."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            first = fh.readline().strip()
    except OSError:
        return None
    if not first:
        return None
    try:
        obj = json.loads(first)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(obj, dict) and obj.get("capture_schema") == CAPTURE_SCHEMA:
        return obj
    return None


def source_fingerprint(path):
    """``(byte count, SHA-256)`` of a file, read in one pass."""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def is_current(src, dst):
    """Whether the capture at ``dst`` was normalized from the source at ``src``."""
    header = capture_header(dst)
    if not header:
        return False
    try:
        recorded = (int(header["source_bytes"]), str(header["source_sha256"]))
    except (KeyError, TypeError, ValueError):
        return False
    return source_fingerprint(src) == recorded


def write_capture(src, dst):
    """Normalize ``src`` into ``dst`` atomically, preserving the source's mtime.

    Atomic because the sweep runs from a lifecycle hook that can be killed with
    the terminal, and a half-written capture that still parsed would be
    indistinguishable from a short session.
    """
    data = normalize_capture(src)
    st = os.stat(src)
    directory = os.path.dirname(os.path.abspath(dst)) or "."
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=directory,
            prefix="." + os.path.basename(dst) + ".",
            delete=False,
        ) as fh:
            tmp = fh.name
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, dst)
        tmp = None
    finally:
        if tmp is not None and os.path.exists(tmp):
            os.unlink(tmp)


USAGE = """usage: agyfilter.py {capture|current} SOURCE CAPTURE

  capture   normalize SOURCE into CAPTURE, atomically, keeping SOURCE's mtime
  current   exit 0 if CAPTURE was normalized from SOURCE exactly, else 1

The sweep that drives this is bash (`tools/session-sync/sync_agy_sessions.sh`),
so the format lives here and the shell only calls it -- the same arrangement it
already has with `worktrees.py`."""


def main(argv):
    if len(argv) != 3 or argv[0] not in ("capture", "current"):
        print(USAGE, file=sys.stderr)
        return 2
    cmd, src, dst = argv
    if cmd == "current":
        return 0 if is_current(src, dst) else 1
    write_capture(src, dst)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
