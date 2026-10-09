#!/usr/bin/env python3
"""Filter a Claude Code ``.jsonl`` session into a clean, blind-safe transcript.

A raw session file is ~95% noise for authorship analysis: withheld
``thinking`` blocks (signature blobs, no content), file-read dumps the agent
re-reads dozens of times, and harness bookkeeping. This keeps only the
conversational *signal* a blind analyst needs to judge who drove the work:

  * human turns, verbatim;
  * assistant prose, verbatim;
  * a one-line marker for each action (Edit/Write/Read/Bash/Agent/...),
    with a short snippet of any prose that was written.

It also *enforces analyst blindness*. The project's own CLAUDE.md (which states
the "human-directed, AI-articulated" thesis the analysis is supposed to reach
independently) is injected into every session inside ``<system-reminder>``
blocks. Those wrappers, the slash-command plumbing, and the local-shell I/O are
stripped here so they never reach a render. ``quirework provenance coldread verify-blind``
greps the output for residual fingerprints as a backstop.

Deterministic: same input + same FILTER_VERSION -> byte-identical output, so a
content hash over the source is a sound cache key.
"""

import collections
import hashlib
import heapq
import json
import os
import re
import sys

# Bump when the filtering logic changes in a way that should invalidate cached
# analyses (it is part of the cold-read cache key).
# v2: drop isMeta/isSidechain records (subagent side-threads read as
#     main-thread turns to a blind analyst; every other corpus reader already
#     dropped them), drop harness-prefixed pseudo-human turns ("Caveat:",
#     "<command-name>", ...), and drop "[Request interrupted ...]" markers.
# v3: recover two channels of human authorship the filter was deleting.
#     (a) RED-PEN MARKS. `redpen read`/`notes` print to stdout, so the author's
#         marks entered a session only as a Bash tool_result -- which the
#         `user` branch drops as tool output. Measured over the corpus: 20
#         invocations across 8 sessions, 58,321 characters of author marks,
#         0% surviving into a render; of 262 candidate mark lines checked
#         against the largest session's filtered transcript, 0 appeared. The
#         blind analysts flagged the hole unprompted ("we only see the AI's
#         extraction of it"). Worse, the rounds whose agents correctly
#         redirected the dump to a scratch file lost it most completely, so
#         allowlisting the tool_result would not fix them. The marks are now
#         spliced from the configured review archive, joined on the .docx
#         filename in the command line (17/17 real extractions resolve), which
#         survives the redirect and the deletion of the .docx itself.
#     (b) AskUserQuestion -- 76 calls across 50 sessions, every one rendering
#         as the bare string "[AskUserQuestion]": no question, no options, no
#         answer. The human's decision was invisible; now the AI's framing
#         shows on the assistant side and the human's pick as a human turn.
# v4: drop two kinds of model-written text the harness logs in the human's
#     slot. A background subagent's report arrives as a <task-notification>
#     message, and a compacted session resumes from a summary flagged
#     isCompactSummary. Both rendered as "### Human", so a blind analyst read
#     a subagent's findings and a model's summary as the author's own words.
# v5: drop the notice the harness writes in the model's slot when a request
#     fails (isApiErrorMessage: "API Error: 529 Overloaded"). It rendered as
#     the model's turn, so a blind analyst read a harness notice as something
#     the model said.
FILTER_VERSION = 5

# Human-turn *content* the harness wrote, not the human (mirrors
# metrics.HARNESS; duplicated because this module stays import-free so the
# hook and the .venv-cdp workflow can run it standalone).
_HARNESS_PREFIXES = (
    "<command-name>",
    "<local-command",
    "Caveat:",
    "<command-message>",
    "[Request interrupted",
    "<system-reminder>",
    "<task-notification>",
)

# Harness-injected wrappers removed wholesale: they carry CLAUDE.md/memory,
# slash-command plumbing, local shell I/O, and the report a background subagent
# sends back when it finishes -- never the author's prose, whoever wrote it.
_WRAPPERS = [
    "task-notification",
    "system-reminder",
    "command-message",
    "command-name",
    "command-args",
    "local-command-stdout",
    "local-command-stderr",
    "bash-input",
    "bash-stdout",
    "bash-stderr",
]
_WRAP_RE = [re.compile(rf"<{w}>.*?</{w}>", re.DOTALL) for w in _WRAPPERS]


# Distinctive strings from the project's own framing. They must never reach a
# blind analyst. Each occurrence is redacted in place (visible placeholder), so
# the *act* of stating project context is preserved while the contaminating
# wording is removed; a long human turn that is a wholesale context dump is
# dropped entirely. verify-blind scans renders for any residual.
#
# Two categories, both of which would bias the verdict:
#   * identity / authority -- so the analyst weighs behavior, not credentials;
#   * authorship-attribution verdicts -- so the analyst is not handed the
#     conclusion (who originated the ideas) it is supposed to reach.
# The book's subject matter is deliberately NOT redacted: an analyst
# must see the dialogue's content to judge who drove it. Redaction applies to
# assistant text too, because an AI *reciting its injected instructions* is the
# system prompt leaking, not authorship evidence (unlike an AI parroting the
# human's live argument, which is kept).
def contamination_markers():
    from quirework.project import current_project

    project = current_project()
    value = project.get("provenance.redaction_markers")
    if value is None:
        value = project.data("provenance.redaction_policy", {}).get("markers", [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError("redaction markers must be a list of nonempty strings")
    return value


def policy_version(base):
    """Identify the declared redaction recipe."""
    import hashlib

    markers = contamination_markers()
    digest = hashlib.sha256(
        json.dumps(markers, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return f"{base}-policy-{digest[:16]}"


REDACT_PLACEHOLDER = "«redacted: project context»"

# A human turn this long that still trips a fingerprint after wrapper-stripping
# is an injected context dump, not something typed -- drop it whole.
_DUMP_CHARS = 1500


def _has_fingerprint(text):
    low = text.lower()
    return any(m.lower() in low for m in contamination_markers())


def redact(text):
    """Replace each project-framing fingerprint with a visible placeholder."""
    for m in sorted(contamination_markers(), key=len, reverse=True):
        text = re.sub(re.escape(m), REDACT_PLACEHOLDER, text, flags=re.IGNORECASE)
    return text


def strip_wrappers(text):
    """Remove harness-injected wrapper spans; return the surviving prose."""
    if not text:
        return ""
    for rx in _WRAP_RE:
        text = rx.sub("", text)
    return text.strip()


def harness_flagged(obj):
    """Whether a Claude Code record is one the harness marks as its own.

    ``isMeta`` marks an injection, and ``isCompactSummary`` the model-written
    summary a compacted session resumes from. Both are logged in the human's
    slot, and neither was typed by the author, whatever its text says.
    ``isApiErrorMessage`` marks the notice the harness writes in the model's
    slot when a request fails ("API Error: 529 Overloaded"): no model wrote
    it, and the turn before it drew no reply. Every reader of a session asks
    this one question, so that a new flag is learned in one place rather than
    by each reader in turn.
    """
    return bool(
        obj.get("isMeta") or obj.get("isCompactSummary") or obj.get("isApiErrorMessage")
    )


def snippet(s, n=110):
    """One-line, whitespace-collapsed, truncated preview of ``s``."""
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _base(path):
    return os.path.basename(path) if path else "?"


# --------------------------------------------------------------------------- #
# red-pen marks: the human authorship that used to be deleted
# --------------------------------------------------------------------------- #
def redpen_directory():
    from quirework.project import current_project

    project = current_project()
    return project.path(
        "provenance.redpen",
        str(project.path("provenance.root", ".quirework/provenance") / "redpen"),
    )


_REDPEN_CMD = re.compile(r"redpen\s+(?:read|notes|archive)\b")
_DOCX_RE = re.compile(r'"([^"]+\.docx)"|\'([^\']+\.docx)\'|(\S+\.docx)')
# The artifact opens with a provenance header in a fenced block (extraction
# SHAs, checksums, counts). That is metadata about the extraction, not the
# author's words, so it is not part of what the analyst reads.
_HEADER_FENCE = re.compile(r"\A.*?^```\n.*?^```\n", re.S | re.M)
# "[Example Writer] 2026-07-03  (on text)" -> "2026-07-03  (on text)". Analyst
# blindness forbids naming the author; the date and channel carry the meaning.
_BYLINE = re.compile(r"^\[[^\]\n]+\]\s+(?=\d{4}-\d{2}-\d{2})", re.M)


def redpen_index():
    """Read the selected project's immutable archive without cross-project cache state."""
    directory = redpen_directory()
    if not directory.exists():
        from quirework.project import current_project

        if current_project().get("provenance.redpen") is not None:
            raise FileNotFoundError(
                f"configured review archive is missing: {directory}"
            )
        return {}
    idx = {}
    for path in sorted(directory.glob("*.md")):
        if path.name == "README.md":
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"source-docx:\s*(.+?)\s*$", line)
            if match:
                idx[match.group(1)] = str(path)
                break
    return idx


def docx_names(command):
    """Every ``.docx`` basename named in a shell command."""
    out = []
    for m in _DOCX_RE.finditer(command or ""):
        hit = next((g for g in m.groups() if g), None)
        if hit:
            out.append(os.path.basename(hit))
    return out


def redpen_marks(command):
    """``(artifact_path, blind_safe_text)`` for a red-pen command, or ``None``.

    The text is the author's marks with the extraction header and the byline
    removed, and the usual contamination redaction applied -- the review-notes
    blocks state the book's own authorship disclosures, which a blind analyst
    must not be handed.
    """
    idx = redpen_index()
    for name in docx_names(command):
        path = idx.get(name)
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
        except OSError:
            return None
        body = _HEADER_FENCE.sub("", body, count=1)
        body = _BYLINE.sub("", body)
        return path, redact(body.strip())
    return None


def format_ask(inp):
    """Render an AskUserQuestion's questions and options (the AI's framing)."""
    lines = []
    for q in (inp or {}).get("questions") or []:
        if not isinstance(q, dict):
            continue
        lines.append(f"[AskUserQuestion] {snippet(q.get('question'), 200)}")
        for o in q.get("options") or []:
            if isinstance(o, dict):
                lines.append(
                    f"    - {snippet(o.get('label'), 60)}: "
                    f"{snippet(o.get('description'), 140)}"
                )
    return "\n".join(lines) if lines else "[AskUserQuestion]"


def _result_text(block):
    """Flatten a tool_result's content to text."""
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(
            b.get("text", "")
            for b in c
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def tool_marker(block):
    """Render a tool_use block as a compact one-line action marker."""
    name = block.get("name", "?")
    inp = block.get("input") or {}
    if name == "Edit":
        return f'[Edit {_base(inp.get("file_path"))}: "{snippet(inp.get("new_string"), 90)}"]'
    if name in ("Write", "NotebookEdit"):
        body = inp.get("content") or inp.get("new_source") or ""
        return f'[Write {_base(inp.get("file_path") or inp.get("notebook_path"))}: "{snippet(body, 90)}"]'
    if name == "Read":
        return f"[Read {_base(inp.get('file_path'))}]"
    if name == "Bash":
        return f"[Bash: {snippet(inp.get('description') or inp.get('command'), 80)}]"
    if name == "Agent":
        return f"[Agent → {inp.get('subagent_type', '?')}: {snippet(inp.get('description'), 80)}]"
    if name == "AskUserQuestion":
        return format_ask(inp)
    if name in ("Grep", "Glob"):
        return f"[{name}: {snippet(inp.get('pattern') or inp.get('query'), 60)}]"
    return f"[{name}]"


def _blocks(content):
    """Normalize a message ``content`` field to a list of block dicts."""
    if isinstance(content, list):
        return content
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


# --------------------------------------------------------------------------- #
# import-time normalization: what a capture keeps
# --------------------------------------------------------------------------- #
# The repository copy of a session is a *provenance record*, not a resumable
# Claude Code checkpoint -- nothing here ever replays a transcript, and the
# harness keeps its own copy until `cleanupPeriodDays` sweeps it. Measured over
# the whole corpus, four fields nothing here reads carry 80% of the
# bytes: `toolUseResult` (31%), the `tool_result` bodies that restate it (25%),
# `thinking.signature` (15%) and `message.usage` (5%). The dialogue everything
# is kept *for* -- human prompts, assistant prose, readable thinking -- is 1.8%.
#
# So a capture is normalized on import. What survives is what a reader of this
# corpus can ask for and no other artifact can answer:
#
#   * every human turn, verbatim, including the three channels that arrive as
#     tool output rather than as a typed turn (an AskUserQuestion answer, a
#     plan the author approved or edited, and the words he gave when he
#     rejected a tool call);
#   * every assistant text block, verbatim, and thinking text where the
#     harness recorded any;
#   * one action record per tool call, with its parameters -- so `[Edit
#     a-chapter.qmd: "..."]`, the doc/meta edit split, and the tool counts all
#     still compute from the capture;
#   * session identity, timestamps, model ids and permission mode.
#
# What goes is what git already holds or what says nothing: file contents read
# and written, ordinary tool output, harness bookkeeping, pasted image bytes,
# and the opaque `thinking.signature` blob. The author drew that line:
#
#     "We don't need files read/transferred. These can be recovered from git
#      if needed for some reason."
#
# The capture stays *shaped like a session*: same record envelope, same
# `message.content` blocks, so every reader in the package works on a capture
# and on a raw transcript without knowing which it has. The header record
# records the source fingerprint (so a sweep can tell a grown live session from
# a settled one) and a census of what was dropped (so a later reader can see
# what the file no longer holds instead of inferring it from absence).
#
# A session's sub-agents are part of the session. Claude Code writes each to
# its own transcript (`subagent_sources`), and the capture folds them in under
# the same rule, so what a sub-agent said and did is kept and what it was given
# and what it read is not: its injected instructions arrive as `attachment`
# records and its reading as tool output, both dropped above. Its records keep
# their `isSidechain` stamp, which every reader already uses to keep a
# side-thread out of the author's turns, the conversation metrics and the blind
# render. That was the shape of a session when the harness still wrote its
# sub-agents into the main transcript.
CAPTURE_SCHEMA = "quirework-cc-v1"
CAPTURE_TYPE = "capture-meta"
SUBAGENT_DIR = "subagents"

# Record types worth keeping. An allowlist rather than a blocklist: a harness
# that invents a record type next release should be dropped by default and
# counted in the header, not silently carried at whatever size it happens to
# be. The four non-dialogue types earn their place by being read --
# `permission-mode` is how `metrics` attributes a tool call to gated or auto
# mode, `ai-title` is `catalog`'s session title, and `queue-operation` is the
# one type outside `user` that can hold words the human typed (a prompt queued
# mid-turn, sometimes withdrawn before it was ever sent). It is not only his:
# the report a sub-agent hands back and a background task's notice queue the
# same way, each inside its own `<agent-message>` or `<task-notification>`
# wrapper, so a reader of this type must tell them apart. `system` earns its
# place on a degenerate case the corpus actually contains: one session
# (09e3caa7) opened, errored and closed without a single dialogue record, so
# its `system` records carry the only timestamps `catalog` can date it by.
KEEP_TYPES = (
    "user",
    "assistant",
    "system",
    "permission-mode",
    "ai-title",
    "queue-operation",
)

# Record-level fields that restate content held elsewhere in the same record.
# `toolUseResult` is the structured twin of the `tool_result` block beside it.
_DROP_RECORD = ("toolUseResult",)
# Token accounting. Nothing in the corpus reads it; the effort ledger counts
# characters and words, not billed tokens.
_DROP_MESSAGE = ("usage",)
# `tool_use.input` keys that carry a *file body* rather than a parameter. These
# are the "files transferred" half of the author's line, and the written half
# is in git; the read half was never in the input anyway. Truncated rather than
# deleted, so `tool_marker` still prints the opening words of an edit.
_FILE_BODY_KEYS = ("content", "new_string", "old_string", "new_source")
# Tools whose *result* is the human speaking, not tool output. AskUserQuestion
# returns his pick; ExitPlanMode returns the plan as he approved it (the
# harness records his edits inline) or the words he rejected it with.
_HUMAN_RESULT_TOOLS = ("AskUserQuestion", "ExitPlanMode")

# The longest snippet any marker asks for is 200 characters
# (`format_ask`); keeping a prefix that survives `snippet` at that width makes
# every marker in this module byte-identical on a capture and on its source.
_MARKER_CHARS = 200
_WS = re.compile(r"\s+")


def marker_prefix(s, limit=_MARKER_CHARS):
    """The shortest prefix of ``s`` that ``snippet`` cannot tell from ``s``.

    ``snippet`` collapses whitespace before it truncates, so a fixed character
    cut is not safe: a thousand characters of indented code can collapse to
    forty and change what a marker prints. This grows the prefix until the
    *collapsed* text is longer than any snippet width in the module, which
    makes ``snippet(marker_prefix(s), n) == snippet(s, n)`` for every ``n <=
    limit`` -- collapsing a prefix yields a prefix of the collapsed whole, so
    once both sides are past the cut they agree character for character.
    """
    if not isinstance(s, str) or len(s) <= limit:
        return s
    end = limit + 1
    while end < len(s):
        if len(_WS.sub(" ", s[:end]).strip()) > limit:
            return s[:end]
        end = min(len(s), end * 2)
    return s


def _prune_tool_use(block):
    """A tool_use block with file bodies cut back to a marker prefix."""
    inp = block.get("input")
    if not isinstance(inp, dict):
        return block
    if not any(k in inp for k in _FILE_BODY_KEYS):
        return block
    out = dict(block)
    out["input"] = {
        k: (marker_prefix(v) if k in _FILE_BODY_KEYS else v) for k, v in inp.items()
    }
    return out


def _prune_image(block):
    """An image block with the base64 payload dropped, the paste recorded.

    Every image in the corpus is one the *human* pasted (111 of them, 9.1MB of
    base64, none of them tool output), so the block itself is evidence and is
    kept; the bytes are not readable by anything here and no reader looks at
    them.
    """
    src = block.get("source")
    if not isinstance(src, dict) or "data" not in src:
        return block
    out = dict(block)
    out["source"] = {k: v for k, v in src.items() if k != "data"}
    return out


def _keep_result_body(block, record, tool):
    """Whether a tool_result's content is the human, or an error worth keeping.

    Ordinary tool output is dropped. Three things that arrive through the same
    channel are not: the two tools that transcribe the human into the session,
    a call he rejected with instructions (`toolDenialKind == 'user-rejected'`
    -- 45 in the corpus, and no reader surfaces them yet), and an error, which
    is short and is the only record of what went wrong.
    """
    return bool(
        tool in _HUMAN_RESULT_TOOLS
        or block.get("is_error")
        or record.get("userFeedback")
        or record.get("toolDenialKind") == "user-rejected"
    )


def _prune_blocks(content, record, tools, dropped):
    """Prune one message's content blocks in place-order."""
    if not isinstance(content, list):
        return content
    out = []
    for b in content:
        if not isinstance(b, dict):
            out.append(b)
            continue
        bt = b.get("type")
        if bt == "tool_use":
            tools[b.get("id")] = b.get("name")
            out.append(_prune_tool_use(b))
        elif bt == "thinking":
            if "signature" in b:
                dropped["thinking.signature"] += 1
                b = {k: v for k, v in b.items() if k != "signature"}
            out.append(b)
        elif bt == "redacted_thinking":
            dropped["redacted_thinking"] += 1
            out.append({k: v for k, v in b.items() if k != "data"})
        elif bt == "image":
            dropped["image.data"] += 1
            out.append(_prune_image(b))
        elif bt == "tool_result":
            tool = tools.get(b.get("tool_use_id"))
            if _keep_result_body(b, record, tool):
                out.append(b)
            else:
                dropped["tool_result.content"] += 1
                out.append({k: v for k, v in b.items() if k != "content"})
        else:
            out.append(b)
    return out


def subagent_sources(path):
    """The sub-agent transcripts of the session at ``path``, in name order.

    Claude Code writes each sub-agent a session starts to its own transcript,
    in a ``subagents`` directory named after the session's file: every record
    stamped ``isSidechain`` and with the sub-agent's ``agentId``, and beside it
    a small ``.meta.json`` naming the agent type, the parent's description of
    the task and the model asked for. Nothing in the session's own transcript
    holds that work, only the call that started it and the report it sent
    back.
    """
    folder = os.path.join(os.path.splitext(str(path))[0], SUBAGENT_DIR)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return [
        os.path.join(folder, n)
        for n in sorted(names)
        if n.startswith("agent-") and n.endswith(".jsonl")
    ]


def _subagent_meta(path):
    """The ``.meta.json`` beside a sub-agent transcript, or ``{}``."""
    try:
        with open(os.path.splitext(path)[0] + ".meta.json", encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _normalize_stream(path, dropped, *, sidechain_only=False):
    """``(records kept, source SHA-256, source bytes)`` for one transcript.

    ``sidechain_only`` is for a sub-agent's transcript: a record there that is
    not stamped as a side-thread would read as the main dialogue to every
    reader, so it is dropped and counted rather than relabelled.
    """
    source = hashlib.sha256()
    source_bytes = 0
    kept = []
    tools = {}  # tool_use id -> tool name, for the results that follow
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
            if kind not in KEEP_TYPES:
                dropped[str(kind)] += 1
                continue
            if sidechain_only and obj.get("isSidechain") is not True:
                dropped["<not-a-side-thread>"] += 1
                continue
            rec = {k: v for k, v in obj.items() if k not in _DROP_RECORD}
            for field in _DROP_RECORD:
                if field in obj:
                    dropped[field] += 1
            message = obj.get("message")
            if isinstance(message, dict):
                for field in _DROP_MESSAGE:
                    if field in message:
                        dropped["message." + field] += 1
                msg = {k: v for k, v in message.items() if k not in _DROP_MESSAGE}
                # Only ever prune what is there: adding a `content` key to a
                # record that had none would be this module inventing structure.
                if "content" in msg:
                    msg["content"] = _prune_blocks(msg["content"], rec, tools, dropped)
                rec["message"] = msg
            kept.append(rec)
    return kept, source.hexdigest(), source_bytes


def _interleave(streams):
    """Merge record streams by time, keeping each stream in its own order.

    Each sub-agent's work lands where it happened in the session, which is
    where the harness used to write it. A record without a timestamp sorts with
    the stamped record before it in its own stream (or the first after it, at
    the start), and the main thread, listed first, wins a tie, so the merge is
    deterministic. Taking only ever the head of a stream means no stream is
    reordered, even where its own timestamps are not monotonic.
    """

    def keyed(rank, stream):
        stamps = [r.get("timestamp") for r in stream]
        first = next((t for t in stamps if isinstance(t, str) and t), "")
        last = first
        for rec, ts in zip(stream, stamps):
            if isinstance(ts, str) and ts:
                last = ts
            yield last, rank, rec

    merged = heapq.merge(
        *(keyed(rank, s) for rank, s in enumerate(streams)),
        key=lambda k: (k[0], k[1]),
    )
    return [rec for _, _, rec in merged]


def normalize_capture(path):
    """Return the bytes of a normalized provenance capture for one session.

    The capture holds the session's own transcript and its sub-agents'
    (``subagent_sources``), each normalized by the same rule and interleaved by
    time. The header records every source's fingerprint, so a sweep can tell a
    session that grew, or gained a sub-agent, from one that is settled.

    Deterministic: same source bytes give the same capture bytes, which is what
    lets the sweep decide by fingerprint and lets a migration prove itself by
    re-rendering. Malformed lines are counted and dropped -- the mirror can copy
    a session mid-write, and one torn line must not cost the rest of the file.
    """
    dropped = collections.Counter()
    kept, source_sha, source_bytes = _normalize_stream(path, dropped)
    streams = [kept]
    agents = []
    for sub in subagent_sources(path):
        sub_dropped = collections.Counter()
        records, sha, size = _normalize_stream(sub, sub_dropped, sidechain_only=True)
        streams.append(records)
        agents.append(
            {
                "agent": os.path.splitext(os.path.basename(sub))[0],
                "source_sha256": sha,
                "source_bytes": size,
                "records": len(records),
                "dropped": dict(sorted(sub_dropped.items())),
                "meta": _subagent_meta(sub),
            }
        )
    rows = _interleave(streams)

    header = {
        "type": CAPTURE_TYPE,
        "capture_schema": CAPTURE_SCHEMA,
        "filter_version": FILTER_VERSION,
        "source_sha256": source_sha,
        "source_bytes": source_bytes,
        "records": len(rows),
        "dropped": dict(sorted(dropped.items())),
    }
    # Only a session that has sub-agents names them, so a capture of one that
    # has none is byte for byte what it was before sub-agents were captured. A
    # missing list therefore means none, in an old capture and a new one alike.
    if agents:
        header["subagents"] = agents
    return b"".join(
        json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        for r in [header, *rows]
    )


def capture_header(path):
    """The capture header of a normalized session, or ``None`` if it is raw."""
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


def filter_session(path):
    """Return ``(transcript_markdown, stats)`` for a session ``.jsonl``."""
    human = assistant = edits = 0
    out = []
    # tool_use id -> what its result carries back from the human. Two tools
    # transcribe human authorship into a session rather than producing tool
    # output; their results are recovered below instead of being dropped.
    pending = {}
    seen_rounds = set()  # a round is spliced once, however often it is re-read
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = obj.get("type")
            if kind not in ("user", "assistant"):
                continue  # mode/attachment/file-history-snapshot/... = bookkeeping
            if harness_flagged(obj) or obj.get("isSidechain"):
                # Meta records and subagent side-threads are not the main
                # dialogue; to a blind analyst they read as extra main-thread
                # turns (the [Agent → ...] marker already shows the delegation).
                continue
            blocks = _blocks((obj.get("message") or {}).get("content"))

            if kind == "user":
                texts = []
                recovered = []  # human authorship arriving via a tool_result
                for b in blocks:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_result":
                        # Tool output is not the human -- except for the two
                        # tools that transcribe the human INTO the session.
                        job = pending.pop(b.get("tool_use_id"), None)
                        if not job or b.get("is_error"):
                            continue
                        if job[0] == "redpen":
                            hit = redpen_marks(job[1])
                            if hit and hit[0] not in seen_rounds:
                                seen_rounds.add(hit[0])
                                recovered.append(
                                    (
                                        "Human (red-pen marks, transcribed from "
                                        "the review docx)",
                                        hit[1],
                                    )
                                )
                        elif job[0] == "ask":
                            answer = strip_wrappers(_result_text(b))
                            if answer:
                                recovered.append(
                                    (
                                        "Human (answer to AskUserQuestion)",
                                        redact(answer),
                                    )
                                )
                        continue
                    if b.get("type") != "text":
                        continue
                    cleaned = strip_wrappers(b.get("text", ""))
                    if not cleaned:
                        continue
                    if cleaned.lstrip().startswith(_HARNESS_PREFIXES):
                        continue  # the harness wrote this, not the human
                    if len(cleaned) > _DUMP_CHARS and _has_fingerprint(cleaned):
                        continue  # injected context dump, not a typed turn
                    texts.append(redact(cleaned))
                if texts:
                    human += 1
                    out.append("### Human\n\n" + "\n\n".join(texts))
                for header, body in recovered:
                    human += 1
                    out.append(f"### {header}\n\n{body}")
            else:  # assistant
                parts = []
                for b in blocks:
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt == "text":
                        txt = strip_wrappers(b.get("text", ""))
                        if txt:
                            parts.append(redact(txt))
                    elif bt == "tool_use":
                        parts.append(redact(tool_marker(b)))
                        name = b.get("name")
                        if name in ("Edit", "Write"):
                            edits += 1
                        # Mark the results worth recovering (see the user branch).
                        if name == "AskUserQuestion":
                            pending[b.get("id")] = ("ask", None)
                        elif name == "Bash":
                            cmd = (b.get("input") or {}).get("command") or ""
                            if _REDPEN_CMD.search(cmd):
                                pending[b.get("id")] = ("redpen", cmd)
                    # thinking blocks: signature-only, dropped
                if parts:
                    assistant += 1
                    out.append("### Assistant\n\n" + "\n\n".join(parts))

    body = "\n\n".join(out)
    stats = {
        "human_turns": human,
        "assistant_turns": assistant,
        "edits": edits,
        "chars": len(body),
    }
    return body, stats


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv):
    if not argv:
        print(
            "usage: python3 -m quirework.provenance.ccfilter SESSION.jsonl [> out.md]",
            file=sys.stderr,
        )
        return 2
    path = argv[0]
    body, stats = filter_session(path)
    sha = sha256_file(path)
    header = (
        f"<!-- source:{os.path.basename(path)} sha256:{sha[:16]} "
        f"filter_version:{FILTER_VERSION} "
        f"human:{stats['human_turns']} assistant:{stats['assistant_turns']} "
        f"edits:{stats['edits']} chars:{stats['chars']} -->\n"
    )
    sys.stdout.write(header + "\n" + body + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
