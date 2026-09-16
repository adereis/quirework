#!/usr/bin/env python3
"""Normalize and read Codex rollout JSONL as provenance-facing records.

Codex stores a verbatim rollout, not a ready-made conversation export.  The
file contains system/developer context, the repository's injected
``AGENTS.md`` card, turn state, encrypted reasoning, tool traffic, and the
visible dialogue.  Corpus consumers need one shared interpretation of that
shape or, most seriously, ``quirework provenance search`` and ``quirework provenance input`` can
mistake injected context (and a subagent's machine-written task) for the
human's own words.

The raw rollout remains in Codex's own state directory.  ``codex-sessions/``
stores a stable, normalized provenance record instead: actual dialogue,
readable reasoning when supplied, compact action markers, and source-integrity
metadata.  The same module owns that import boundary and the deterministic
reading surface used by catalog, cold-read, show, search/input, metrics, and
triage.  OpenAI documents ``transcript_path`` as convenient but explicitly not
a stable hook interface, so the raw reader is tolerant: unknown records are
skipped and malformed lines do not make the rest of a capture unreadable.

**Only ``response_item`` records carry dialogue here, and that is deliberate**
-- the normalized format uses that stable envelope, and the two raw kinds that
also hold message text are duplicates:

- ``event_msg`` (``user_message`` / ``agent_message``) is the TUI's own echo of
  a turn already recorded as a ``response_item``;
- ``compacted`` carries a ``replacement_history`` that repeats earlier user
  messages **verbatim** when the context is compacted, so a reader that took
  message text from wherever it found it would count a compacted session's
  human turns twice, and count them again on every later compaction.

``agent_message`` is the exception that proves the rule: it is kept in the
capture and rendered by nothing.  See ``normalize_capture``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from quirework.provenance import ccfilter as FLT


FILTER_VERSION = 2
# v2 added the ``agent_message`` payload. v3 makes nested-tool discovery
# syntax-aware enough not to mistake a tool name quoted inside a patch or test
# fixture for a call the outer orchestration script actually made. A schema
# bump makes every existing capture pass through the corrected boundary; the
# intended render is unchanged, so ``FILTER_VERSION`` -- which keys cold-read
# reports -- does not move with it.
CAPTURE_SCHEMA = "quirework-codex-v3"
_TEXT_TYPES = {"input_text", "output_text", "text"}
_META_FIELDS = (
    "id",
    "session_id",
    "timestamp",
    "cwd",
    "source",
    "thread_source",
    "originator",
    "cli_version",
    "model_provider",
    "context_window",
    "git",
    "forked_from_id",
    "parent_thread_id",
    "agent_nickname",
    "agent_path",
    "multi_agent_version",
    "subagent_history_start_ordinal",
)
# A harness block opens with its tag alone on the first line. Nobody types that,
# and every injected pseudo-user message in the corpus has the shape:
# `<environment_context>`, `<recommended_plugins>`, `<skill>` (the expanded body
# of a skill -- the human's own `[$name](path)` invocation is a separate turn and
# stays), `<turn_aborted>`. Matching the shape rather than the list is what keeps
# this honest against a beta runtime that adds a tag next week.
_LONE_TAG = re.compile(r"<[a-z][a-z0-9_-]*>\Z")
# In Codex's custom ``exec`` call the patch is usually a JavaScript string, so
# its line breaks are literal ``\n`` characters rather than physical JSONL
# line breaks. Stop at either representation (a backslash or a real newline).
_PATCH_FILE = re.compile(r"\*\*\*\s+(?:Add|Update|Delete)\s+File:\s*([^\\\r\n]+)")
_NESTED_TOOL = re.compile(r"\btools\.([A-Za-z][A-Za-z0-9_]*)\s*\(")
# The shell command inside an ``exec`` snippet: `{"cmd":"git status …"` or
# `{"cmd":["git","status"]`. The key's quotes are optional because the snippet
# is JavaScript, not JSON, and Codex writes it both ways. Captured as a JSON
# literal so quoting survives the lift, and the string branch honors `\"`
# because commands quote things constantly.
_EXEC_CMD = re.compile(r'"?\bcmd"?\s*:\s*("(?:[^"\\]|\\.)*"|\[[^\]]*\])')
# The other shape Codex writes: a JS array of `[label, command]` pairs looped
# over in one call. There is no `cmd` key at all, so fall back to string
# literals that read like a command line -- long, spaced, and not a bare path.
_JS_STRING = re.compile(r'"((?:[^"\\]|\\.){12,})"')


def _objects(path):
    """Yield valid JSON objects from a rollout, tolerating a torn live tail."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, dict):
                yield obj


def _content_text(content):
    """Visible text from a Responses-style message content array."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in _TEXT_TYPES:
            continue
        text = block.get("text")
        if not isinstance(text, str):
            text = block.get("input_text") or block.get("output_text") or ""
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _visible_content(content):
    """Message content with text preserved and attachments omitted.

    Runtime message items may carry input images or files inline.  The corpus
    records the dialogue, not another copy of those assets, so only blocks that
    contribute visible text survive normalization.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return []
    out = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in _TEXT_TYPES:
            continue
        text = block.get("text")
        if not isinstance(text, str):
            text = block.get("input_text") or block.get("output_text") or ""
        if text:
            out.append({"type": block.get("type"), "text": text})
    return out


def _plain_reasoning(value):
    """Plaintext reasoning parts, excluding opaque encrypted state."""
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, str) and item:
            out.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            if item["text"]:
                out.append(item["text"])
    return out


def _reasoning_text(payload):
    """Readable reasoning summary/content sections from one reasoning item."""
    sections = []
    summary = _plain_reasoning(payload.get("summary"))
    content = _plain_reasoning(payload.get("content"))
    if summary:
        sections.append("[reasoning summary]\n" + "\n\n".join(summary))
    if content:
        sections.append("[reasoning]\n" + "\n\n".join(content))
    return "\n\n".join(sections)


def _nested_tool_names(source):
    """Tool calls in JavaScript code, excluding strings and comments.

    Codex's outer ``exec`` payload is JavaScript, and patches routinely contain
    source text such as ``tools.request_user_input(...)`` inside a quoted
    string. A regex over the whole payload mistakes that text for an executed
    call and can then preserve the outer command's result as if the human had
    answered an interactive question. This small lexer walks only executable
    code. Template interpolation is executable too, so ``${...}`` re-enters
    code mode while the rest of a template remains data.
    """
    names = []
    stack = [["code", None]]
    i = 0
    while i < len(source):
        state, detail = stack[-1]
        char = source[i]

        if state in ("single", "double"):
            if char == "\\":
                i += 2
            elif char == detail:
                stack.pop()
                i += 1
            else:
                i += 1
            continue

        if state == "line-comment":
            if char in "\r\n":
                stack.pop()
            i += 1
            continue

        if state == "block-comment":
            if source.startswith("*/", i):
                stack.pop()
                i += 2
            else:
                i += 1
            continue

        if state == "template":
            if char == "\\":
                i += 2
            elif char == "`":
                stack.pop()
                i += 1
            elif source.startswith("${", i):
                stack.append(["template-expression", 1])
                i += 2
            else:
                i += 1
            continue

        # ``code`` and ``template-expression`` share lexical rules. The latter
        # additionally returns to its template when its matching brace closes.
        if char in ("'", '"'):
            stack.append(["single" if char == "'" else "double", char])
            i += 1
            continue
        if char == "`":
            stack.append(["template", None])
            i += 1
            continue
        if source.startswith("//", i):
            stack.append(["line-comment", None])
            i += 2
            continue
        if source.startswith("/*", i):
            stack.append(["block-comment", None])
            i += 2
            continue
        if state == "template-expression":
            if char == "{":
                stack[-1][1] += 1
                i += 1
                continue
            if char == "}":
                stack[-1][1] -= 1
                i += 1
                if stack[-1][1] == 0:
                    stack.pop()
                continue
        match = _NESTED_TOOL.match(source, i)
        if match:
            names.append(match.group(1))
            i = match.end()
            continue
        i += 1
    return list(dict.fromkeys(names))


def _tool_names(payload):
    """Concrete nested tool names represented by one runtime call."""
    names = _nested_tool_names(_tool_input(payload))
    if not names and payload.get("name"):
        names = [str(payload["name"])]
    return list(dict.fromkeys(names or ["tool"]))


def _human_tool_text(value):
    """Human-authored answer returned by an interactive question tool."""
    if isinstance(value, str):
        return value
    text = _content_text(value)
    if text:
        return text
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def normalize_capture(path):
    """Return a stable, compact provenance capture for one raw rollout.

    The raw Codex rollout is a transport/event log.  A provenance capture keeps
    actual dialogue, readable reasoning, minimal action records, inter-agent
    messages, and the small amount of session context downstream tools consume.
    Tool results, inline files/images, encrypted reasoning state, event echoes,
    context snapshots, and compaction replays are deliberately absent.

    ``agent_message`` is kept although **no reader renders it**, and that is the
    point: it is the one payload here whose exclusion is a *curation* call
    rather than a storage one, and a curation call belongs to the reader, not to
    the archive.  Claude Code's corpus already works that way -- a subagent
    side-thread stays in the capture and every render drops it -- and the
    asymmetry was real: `records`, `filter_session`, `edit_paths` and `scan` all
    dispatch on an allowlist of payload kinds, so they drop this one by
    construction, whereas dropping it at import put it beyond recall once the
    rollout left ``~/.codex``.

    The census says the same thing from the other side.  Of the 45 in the corpus
    when this landed, two are ``NEW_TASK`` dispatches and 43 are ``FINAL_ANSWER``
    returns; 41 of those 43 are ingest evaluation seats (``read_<session-id>``),
    which is exactly the exhaust curation exists to keep out, and of the two
    doing real work one already has a capture of its own.  So the material at
    stake is one subagent's verdict -- small enough that keeping it costs 25kB,
    and pointed enough that losing it silently was the wrong default.  The
    encrypted half of a dispatch's content is still dropped, like every other
    opaque blob.

    The source digest and byte count cover every input byte, including malformed
    or torn tail bytes.  Valid records before such a tail remain recoverable and
    a later sweep will normalize the grown source again.
    """
    path = Path(path)
    meta = session_meta(path)
    kept = []
    interactive_calls = {}
    source_hash = hashlib.sha256()
    source_bytes = 0
    header_timestamp = None
    last_timestamp = meta.get("timestamp")

    with path.open("rb") as fh:
        for raw_line in fh:
            source_hash.update(raw_line)
            source_bytes += len(raw_line)
            try:
                obj = json.loads(raw_line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(obj, dict):
                continue
            timestamp = obj.get("timestamp")
            if timestamp:
                last_timestamp = timestamp
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                continue
            typ = obj.get("type")
            kind = payload.get("type")

            if typ == "session_meta" and header_timestamp is None:
                header_timestamp = timestamp
                continue

            if typ == "turn_context" and payload.get("model"):
                context = {"model": payload["model"]}
                if payload.get("effort") is not None:
                    context["effort"] = payload["effort"]
                kept.append(
                    {
                        "timestamp": timestamp,
                        "type": "turn_context",
                        "payload": context,
                    }
                )
                continue

            if typ != "response_item":
                continue

            if kind == "message":
                role = payload.get("role")
                if role not in ("user", "assistant"):
                    continue
                content = _visible_content(payload.get("content"))
                text = _content_text(content)
                if not text or (role == "user" and _injected_user(text)):
                    continue
                message = {"type": "message", "role": role, "content": content}
                if payload.get("phase") is not None:
                    message["phase"] = payload["phase"]
                kept.append(
                    {
                        "timestamp": timestamp,
                        "type": "response_item",
                        "payload": message,
                    }
                )
                continue

            if kind == "agent_message":
                content = _visible_content(payload.get("content"))
                if not _content_text(content).strip():
                    continue
                message = {"type": "agent_message", "content": content}
                for field in ("author", "recipient"):
                    if payload.get(field) is not None:
                        message[field] = payload[field]
                kept.append(
                    {
                        "timestamp": timestamp,
                        "type": "response_item",
                        "payload": message,
                    }
                )
                continue

            if kind == "reasoning":
                reasoning = {"type": "reasoning"}
                summary = _plain_reasoning(payload.get("summary"))
                content = _plain_reasoning(payload.get("content"))
                if summary:
                    reasoning["summary"] = summary
                if content:
                    reasoning["content"] = content
                if len(reasoning) > 1:
                    kept.append(
                        {
                            "timestamp": timestamp,
                            "type": "response_item",
                            "payload": reasoning,
                        }
                    )
                continue

            if kind in ("custom_tool_call", "function_call"):
                names = _tool_names(payload)
                raw_input = _tool_input(payload)
                action = {
                    "type": "action",
                    "names": names,
                    "markers": _tool_markers(payload),
                    "edit_paths": [
                        target.strip().strip("\"'")
                        for target in _PATCH_FILE.findall(raw_input)
                        if target.strip().strip("\"'") != "/dev/null"
                    ],
                }
                call_id = payload.get("call_id")
                if "request_user_input" in names:
                    action["interactive_input"] = raw_input
                    if call_id:
                        interactive_calls[call_id] = "request_user_input"
                kept.append(
                    {
                        "timestamp": timestamp,
                        "type": "response_item",
                        "payload": action,
                    }
                )
                continue

            if kind in ("custom_tool_call_output", "function_call_output"):
                tool = interactive_calls.get(payload.get("call_id"))
                if not tool:
                    continue
                text = _human_tool_text(payload.get("output"))
                if text:
                    kept.append(
                        {
                            "timestamp": timestamp,
                            "type": "response_item",
                            "payload": {
                                "type": "human_tool_output",
                                "tool": tool,
                                "content": text,
                            },
                        }
                    )

    capture_meta = {key: meta[key] for key in _META_FIELDS if key in meta}
    capture_meta.update(
        {
            "capture_schema": CAPTURE_SCHEMA,
            "source_sha256": source_hash.hexdigest(),
            "source_bytes": source_bytes,
            "source_last_timestamp": last_timestamp,
        }
    )
    header = {
        "timestamp": header_timestamp or meta.get("timestamp"),
        "type": "session_meta",
        "payload": capture_meta,
    }
    rows = [header, *kept]
    return b"".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n"
        for row in rows
    )


def session_meta(path):
    """The rollout's ``session_meta.payload`` mapping, or an empty mapping."""
    for obj in _objects(path):
        if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
            return obj["payload"]
    return {}


def session_id(path):
    """Stable session id from metadata, falling back to the rollout filename."""
    meta = session_meta(path)
    sid = meta.get("id") or meta.get("session_id")
    if sid:
        return str(sid)
    m = re.search(
        r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?:\.jsonl)?$",
        Path(path).name,
        re.I,
    )
    return m.group(1) if m else Path(path).stem


def subagent_kind(meta):
    """Subagent label from session metadata, or ``None`` for a main thread."""
    source = (meta or {}).get("source")
    if not isinstance(source, dict):
        return None
    sub = source.get("subagent")
    if isinstance(sub, str):
        return sub
    if isinstance(sub, dict):
        for key in ("other", "type", "name"):
            if sub.get(key):
                return str(sub[key])
        if sub:
            key, value = next(iter(sub.items()))
            return str(value or key)
    return "subagent" if sub is not None else None


def _injected_user(text):
    """True for a pseudo-user message the harness wrote, not the human.

    Codex delivers its own framing through the `user` role: the repository card
    (`# AGENTS.md instructions for <path>`), the environment block, the expanded
    body of a skill, the plugin catalog, an interrupt notice. Left alone they
    enter `search`, `input` and `metrics` as the author's own words, which is
    the one mistake this whole module exists to prevent.

    Two rules: the repository card by its heading, and every other block by its
    shape -- a first line that is nothing but a lone opening tag. Shape rather
    than a list of known tags, because Codex is beta and the set moves; and the
    tag must be the *whole* first line, so a human turn that merely mentions one
    ("why is <skill> showing up as one of my turns?") is still the human's.
    """
    stripped = (text or "").lstrip()
    if not stripped:
        return False
    if stripped.startswith("# AGENTS.md instructions for "):
        return True
    return bool(_LONE_TAG.match(stripped.splitlines()[0].strip()))


def records(path, *, thinking=False, raw=False):
    """Return clean ``(role, text, timestamp)`` dialogue records.

    Roles are ``human``, ``assistant``, and ``agent``.  ``agent`` is the task
    passed into a subagent: the raw format labels it ``user``, but it was
    machine-written by the parent and must never enter the human-turn stream.
    In ``raw`` mode the initial injected repository card is exposed as
    ``harness`` rather than falsely relabeled as human.
    """
    meta = session_meta(path)
    subagent = subagent_kind(meta)
    out = []
    interactive_calls = set()
    for obj in _objects(path):
        typ = obj.get("type")
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        ts = obj.get("timestamp")
        if typ == "response_item" and payload.get("type") == "message":
            role = payload.get("role")
            if role not in ("user", "assistant"):
                continue
            text = _content_text(payload.get("content")).strip()
            if not text:
                continue
            if role == "user":
                if _injected_user(text):
                    if raw:
                        out.append(("harness", text, ts))
                    continue
                out.append(("agent" if subagent else "human", text, ts))
            else:
                out.append(("assistant", text, ts))
        elif thinking and typ == "response_item" and payload.get("type") == "reasoning":
            visible = _reasoning_text(payload).strip()
            if visible:
                out.append(("assistant", visible, ts))
        elif typ == "response_item" and payload.get("type") == "human_tool_output":
            text = _human_tool_text(payload.get("content")).strip()
            if text:
                out.append(("agent" if subagent else "human", text, ts))
        elif typ == "response_item" and payload.get("type") in (
            "custom_tool_call",
            "function_call",
        ):
            if "request_user_input" in _tool_names(payload) and payload.get("call_id"):
                interactive_calls.add(payload["call_id"])
        elif (
            typ == "response_item"
            and payload.get("type")
            in ("custom_tool_call_output", "function_call_output")
            and payload.get("call_id") in interactive_calls
        ):
            interactive_calls.discard(payload.get("call_id"))
            text = _human_tool_text(payload.get("output")).strip()
            if text:
                out.append(("agent" if subagent else "human", text, ts))
    return out


def human_records(path):
    """Actual human turns only; injected context and subagent tasks are absent."""
    return [row for row in records(path) if row[0] == "human"]


def _tool_input(payload):
    value = payload.get("input")
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def edit_paths(path):
    """Files changed through ``apply_patch`` calls recorded in the rollout."""
    out = []
    for obj in _objects(path):
        payload = obj.get("payload")
        if obj.get("type") != "response_item" or not isinstance(payload, dict):
            continue
        if payload.get("type") == "action":
            out.extend(str(target) for target in payload.get("edit_paths") or [])
            continue
        if payload.get("type") not in ("custom_tool_call", "function_call"):
            continue
        raw = _tool_input(payload)
        for target in _PATCH_FILE.findall(raw):
            target = target.strip().strip("\"'")
            if target and target != "/dev/null":
                out.append(target)
    return out


def edit_targets(path, classify):
    """``(doc, meta, basename_counts)`` for triage/catalog."""
    doc = meta = 0
    files = {}
    for target in edit_paths(path):
        if classify(target) == "doc":
            doc += 1
        else:
            meta += 1
        base = os.path.basename(target)
        files[base] = files.get(base, 0) + 1
    return doc, meta, files


def _tool_markers(payload):
    """Compact one-line action markers for one recorded tool call.

    Codex funnels almost everything through a single custom ``exec`` tool whose
    input is a JavaScript snippet, so the *tool name* carries no information: an
    unadorned rendering is a hundred identical ``[exec_command]`` lines, which
    is what a blind analyst would have been asked to read. The shell command is
    right there in the snippet, so lift it -- matching the Claude Code renderer,
    where a shell call renders as ``[Bash: <command>]`` and is worth reading.

    A patched file yields one ``[Edit <name>]`` per file, as before; several
    files in one ``apply_patch`` therefore yield several markers.
    """
    raw = _tool_input(payload)
    targets = [
        os.path.basename(t.strip().strip("\"'")) for t in _PATCH_FILE.findall(raw)
    ]
    if targets:
        return [f"[Edit {target}]" for target in targets]
    names = _nested_tool_names(raw)
    name = names[0] if names else (payload.get("name") or "tool")
    command = _command_text(raw)
    return [f"[{name}: {command}]" if command else f"[{name}]"]


def _command_text(raw):
    """The shell command an ``exec`` snippet runs, trimmed for one line."""
    match = _EXEC_CMD.search(raw)
    if match:
        shown = _decode(match.group(1))
    else:
        # The `[label, command]`-array shape: take the longest literal that
        # reads like a command line rather than a path or a workdir.
        candidates = [_decode(f'"{m}"') for m in _JS_STRING.findall(raw)]
        candidates = [
            c
            for c in candidates
            if " " in c and not c.startswith("/") and "\n" not in c
        ]
        shown = max(candidates, key=len, default="")
    if not isinstance(shown, str):
        shown = " ".join(str(part) for part in shown)
    shown = " ".join(shown.split())
    return (shown[:87] + "...") if len(shown) > 90 else shown


def _decode(literal):
    """A JSON literal from inside the snippet, or its raw text if unparseable."""
    try:
        return json.loads(literal)
    except (json.JSONDecodeError, ValueError):
        return literal


def scan(path):
    """Title, date span, model, and source kind for catalog/show headers."""
    meta = session_meta(path)
    subagent = subagent_kind(meta)
    title = first = last = None
    models = {}
    for obj in _objects(path):
        ts = obj.get("timestamp")
        if ts:
            first = first or ts
            last = ts
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        if obj.get("type") == "turn_context" and payload.get("model"):
            model = str(payload["model"])
            models[model] = models.get(model, 0) + 1
        if (
            title is None
            and obj.get("type") == "response_item"
            and payload.get("type") == "message"
            and payload.get("role") == "user"
        ):
            text = _content_text(payload.get("content")).strip()
            if text and not _injected_user(text):
                raw = text.splitlines()[0].strip()
                title = (raw[:57] + "...") if len(raw) > 60 else raw
    model = max(models, key=models.get) if models else None
    if subagent:
        title = f"[{subagent}] {title or '(untitled)'}"
    return {
        "title": title or "(untitled)",
        "date": (first or meta.get("timestamp") or "")[:10] or "?",
        "date_end": (
            meta.get("source_last_timestamp")
            or last
            or first
            or meta.get("timestamp")
            or ""
        )[:10]
        or "?",
        "model": model or "?",
        "source_kind": subagent or "main",
    }


def filter_session(path):
    """Blind-safe conversation with action markers, in the order they happened.

    Markers are interleaved rather than collected into a trailing block, which
    is how the Claude Code renderer reads and the only form that carries the
    thing a cold-read analyst is judging: *who moved first*. A trailing block
    says a hundred commands ran; interleaved, it says the human asked, the agent
    ran these, then said this. Runs of an identical marker collapse to
    ``[... × N]`` so a retry loop costs one line rather than forty.
    """
    meta = session_meta(path)
    subagent = subagent_kind(meta)
    out = []
    human = assistant = edits = 0
    pending = []  # markers seen since the last emitted message
    interactive_calls = set()

    def flush():
        if not pending:
            return
        lines, run, count = [], None, 0
        for marker in pending:
            if marker == run:
                count += 1
                continue
            if run is not None:
                lines.append(run if count == 1 else f"{run} × {count}")
            run, count = marker, 1
        lines.append(run if count == 1 else f"{run} × {count}")
        out.append("\n".join(lines))
        pending.clear()

    for obj in _objects(path):
        typ = obj.get("type")
        payload = obj.get("payload")
        if typ != "response_item" or not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        if kind in ("custom_tool_call", "function_call", "action"):
            if kind == "action":
                markers = payload.get("markers") or []
            else:
                markers = _tool_markers(payload)
                if "request_user_input" in _tool_names(payload) and payload.get(
                    "call_id"
                ):
                    interactive_calls.add(payload["call_id"])
            edits += sum(1 for m in markers if m.startswith("[Edit "))
            pending.extend(markers)
            continue
        if kind in (
            "human_tool_output",
            "custom_tool_call_output",
            "function_call_output",
        ):
            if kind == "human_tool_output":
                value = payload.get("content")
            elif payload.get("call_id") in interactive_calls:
                interactive_calls.discard(payload.get("call_id"))
                value = payload.get("output")
            else:
                continue
            text = _human_tool_text(value).strip()
            if not text:
                continue
            flush()
            head = "Agent prompt (machine-written)" if subagent else "Human"
            if head == "Human":
                human += 1
            else:
                assistant += 1
            out.append(f"### {head}\n\n{FLT.redact(text)}")
            continue
        if kind != "message":
            continue
        role = payload.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _content_text(payload.get("content")).strip()
        if not text:
            continue
        if role == "user":
            if _injected_user(text):
                continue
            head = "Agent prompt (machine-written)" if subagent else "Human"
        else:
            head = "Assistant"
        cleaned = FLT.strip_wrappers(text)
        if not cleaned:
            continue
        flush()
        if head == "Human":
            human += 1
        else:
            assistant += 1
        out.append(f"### {head}\n\n{FLT.redact(cleaned)}")
    flush()
    body = "\n\n".join(out)
    return body, {
        "human_turns": human,
        "assistant_turns": assistant,
        "edits": edits,
        "chars": len(body),
    }
