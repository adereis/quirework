#!/usr/bin/env python3
"""Render a provenance session as a clean, readable transcript.

The read-half companion to ``quirework provenance search``: search tells you *which*
session mentioned X (by short id); show prints that session's human/assistant
turns in order, with tool calls, tool-result echoes, thinking, and harness
plumbing stripped, so a session can be *read* without hand-grepping the raw
JSONL. (Hand-rolling that extraction is exactly what this command exists to
retire.)

    quirework provenance show 31cf63ed                # cc-session, human + assistant prose
    quirework provenance search kettle --show         # find the session, then:
    quirework provenance show 31cf63ed --thinking     # include the assistant's thinking
    quirework provenance show 31cf63ed --role human   # only the human's turns
    quirework provenance show 31cf63ed --raw          # keep harness text, labeled as such
    quirework provenance show 31cf63ed --utc          # stamp turns in UTC, not local time
    quirework provenance show 31cf63ed --from 15:47 --to 15:52   # turns in a time window
    quirework provenance show 31cf63ed --turn 14      # the author's turn 14 and its replies
    quirework provenance show c00a7331                # a claude.ai web dialogue (both speakers)

The id is the short id search prints (an 8-hex prefix, or the full ``gemini-…``
name); a full uuid or a path works too. It resolves across the corpus families,
pending captures included, so a session can be read before it is accepted; the
source line says when it has not been:
local CLI sessions (Claude Code, Antigravity, and Codex), claude.ai
``*.raw.json`` web dialogues, the already-rendered ``gemini-*.md`` reviews,
where ``provenance.redpen`` declares the archive, red-pen rounds, read as
the author's marks one turn each and named ``redpen-`` plus the file's name,
and, where ``provenance.rulings`` declares one, review-sheet rulings, read as
the author's ruling on each item one turn each, with what the sheet presented
as the text the turn answered, and named ``rulings-`` plus the file's name.

Every turn carries the timestamp the raw log recorded for it (already present in
the stored transcripts — cc ``timestamp``, agy/web ``created_at`` — so nothing
needed re-capturing), and the header states the session's span and duration.
Times render in the machine's local zone by default; ``--utc`` renders them
in UTC. The already-rendered ``gemini-*.md`` reviews carry no per-turn stamps,
so those pass through unstamped.

``--from`` and ``--to`` bound a time window and keep the turns logged inside
it, both ends included. A time needs no turn at it: ``--from 00:00`` starts at
the first turn of the day. Any stamp the transcript prints works as a bound,
and a bound covers the whole unit it is written to, so ``--to 15:52`` keeps a
turn at 15:52:59. A bare time takes the session's day; in a session that
crosses midnight it takes the day the session was running at that time. Only
a session logged over more than a day can be running at one time on two days;
there a typed time asks for its date, and the transcript prints the date on
every turn whose time alone would be ambiguous.

A bound is read in the zone the transcript is shown in, so a time copied from
another machine's transcript can name a different moment here. A bound that
ends in its own zone cannot: ``--from 16:45:26Z`` and ``--from
'2026-09-16 13:45:26-03:00'`` mean one moment everywhere, and a log's own
stamp, such as ``2026-09-16T16:45:26.123Z``, is read as the millisecond it
names.

The author's turns are numbered from 1, as ``quirework provenance search``
numbers them, so a hit's ``turn 14`` is ``## Human 14`` here and ``--turn 14``
opens it with the replies that follow, up to the author's next turn. A time
can be shared by turns logged in the same second and shifts with the zone; the
number does neither, and --role and --raw leave it alone.

(``--thinking`` is wired but usually inert. A thinking block's *text* is only
persisted when the session ran against a **direct API** (Vertex, or an Anthropic
API key); **subscription**-authenticated Claude Code sessions record the block
as a bare signature with no text, and that is most of this corpus. So
``--thinking`` recovers reasoning only for the handful of direct-API sessions.)

PRIVATE: like ``quirework provenance input``, the output is verbatim corpus text (the
firehose), so it stays inside the private workspace / ~/tmp and is never
promoted. Curated, short quotes are fine; the whole rendered session is not.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from quirework.project import current_project
from quirework.provenance import ccfilter as FLT
from quirework.provenance import rulings as RULINGS
from quirework.provenance import store
from quirework.provenance import codexfilter as CODEX_FLT
from quirework.provenance import catalog as CAT
from quirework.provenance.metrics import HARNESS

ROLE_LABEL = {
    "human": "Human",
    "assistant": "Assistant",
    "agent": "Agent prompt (machine-written)",
    "subagent": "Sub-agent (machine-written)",
    "harness": "Harness context",
}

# The tool a Claude Code sub-agent hands its final report back through. The
# report is the sub-agent's own text, so `--raw` shows it with the sub-agent's
# prose rather than dropping it with the other tool calls.
_HANDBACK = "SubagentHandback"


def _human_slot(text, raw):
    """``(role, text)`` for a message logged in the human's slot, or ``None``.

    The harness writes into that slot too: slash-command wrappers, system
    reminders, interrupt notices, and the report a background subagent sends
    back when it finishes. A message with the author's prose in it is
    ``human``, with its wrappers stripped. One with none is dropped, or with
    ``raw`` kept as ``harness``, so it never reads as the author's words. With
    ``raw`` every message keeps its text exactly as logged.
    """
    text = (text or "").strip()
    cleaned = FLT.strip_wrappers(text)
    if cleaned and not cleaned.startswith(HARNESS):
        return "human", text if raw else cleaned
    if raw and text:
        return "harness", text
    return None


# How an author turn reached a Claude Code log, in the order the reader
# learned each path. A reader that predates a path did not count its turns,
# so a turn's origin says which readers saw it: the answer to a question the
# model asked joined the author's turns first, then a prompt typed while the
# model worked, then a prompt logged as a list of blocks, which is how one
# sent with a pasted image is logged.
ORIGINS = ("typed", "answer", "queued", "listed")


def _origin(turn, origin):
    """``origin`` for a ``_human_slot`` row in the author's role, else ``None``."""
    return origin if turn[0] == "human" else None


def _answers(content, asks, raw, ts):
    """``("human", text, ts)`` for each answer in a tool-result echo.

    Only the result of a question the model asked, an id in ``asks``, is the
    author's; every other tool result stays dropped as tool output.
    """
    out = []
    for b in content if isinstance(content, list) else ():
        if not isinstance(b, dict) or b.get("type") != "tool_result":
            continue
        if b.get("tool_use_id") not in asks:
            continue
        text = FLT.answer_text(b, asks[b.get("tool_use_id")], raw=raw)
        if text:
            out.append(("human", text, ts))
    return out


# --------------------------------------------------------------------------- #
# turn extraction -> a flat list of (role, text), in conversation order
# --------------------------------------------------------------------------- #
def cc_records(path, *, thinking=False, raw=False, origins=False):
    """``(role, text, ts)`` turns from a cc-session JSONL.

    ``ts`` is the record's ISO-8601 ``timestamp`` (or ``None`` if absent).
    Assistant messages collapse their content blocks into one turn: ``text``
    blocks as prose, ``thinking`` blocks (only with ``thinking``) prefixed and
    set off, ``tool_use`` blocks dropped. Human turns are the messages the
    author typed: a string, or the text blocks of a prompt sent with a
    pasted image, which the harness logs as a list (``ccfilter.typed_text``).
    A list is otherwise a tool-result echo and is skipped, except the result
    of a question the model asked with ``AskUserQuestion``, which is the
    author's pick and so a human turn, as the capture and the Codex reader
    already treat it, with the option previews the model wrote cut from it
    (``ccfilter.answer_text``). Records the
    harness flags as its own (a meta injection, the summary a compacted
    session resumes from, or the notice of a failed request written in the
    model's slot) and ``isSidechain`` (subagent side-threads) records are
    dropped unless ``raw``, which keeps them under the role of whoever wrote
    them: a flagged record is ``harness``, a side-thread's prompt is
    ``agent``, the task the parent handed its subagent, and what the subagent
    wrote back, its prose and the report it hands back, is ``subagent``.
    A prompt the author typed while the model was working is a human turn
    too, though the harness logs it only as a queue operation, at the time
    he typed it (``ccfilter.queued_prompt``). With ``origins`` each row
    carries a fourth item: how a human turn reached the log (``ORIGINS``),
    or ``None`` for any other row.
    """
    out = []
    asks = {}  # each question the model asked the author: id -> its input
    queued = {}  # queued text -> when it was enqueued
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            typ = o.get("type")
            prompt = FLT.queued_prompt(o, queued)
            if prompt:
                out.append(("human", prompt[0], prompt[1], "queued"))
                continue
            if typ not in ("user", "assistant"):
                continue
            if not raw and (FLT.harness_flagged(o) or o.get("isSidechain")):
                continue
            ts = o.get("timestamp")
            content = (o.get("message") or {}).get("content")
            if typ == "user":
                if not isinstance(content, str):
                    # A tool-result echo, or a prompt sent with a pasted image.
                    if not (o.get("isSidechain") or FLT.harness_flagged(o)):
                        turn = _human_slot(FLT.typed_text(content, raw), raw)
                        if turn:
                            out.append((*turn, ts, _origin(turn, "listed")))
                        out.extend(
                            (*row, "answer") for row in _answers(content, asks, raw, ts)
                        )
                    continue
                if o.get("isSidechain") or FLT.harness_flagged(o):
                    # Reached only with raw; neither was typed by the author.
                    # The harness writes into a subagent's slot as it does
                    # into the session's, so its flag wins over the thread's.
                    role = "harness" if FLT.harness_flagged(o) else "agent"
                    if content.strip():
                        out.append((role, content.strip(), ts, None))
                    continue
                turn = _human_slot(content, raw)
                if turn:
                    out.append((*turn, ts, _origin(turn, "typed")))
                continue
            # assistant: merge this message's blocks into one turn
            if isinstance(content, str):
                blocks = [{"type": "text", "text": content}]
            elif isinstance(content, list):
                blocks = content
            else:
                blocks = []
            parts = []
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text":
                    t = (b.get("text") or "").strip()
                    if t:
                        parts.append(t)
                elif bt == "thinking" and thinking:
                    t = (b.get("thinking") or b.get("text") or "").strip()
                    if t:
                        parts.append("[thinking]\n" + t)
                elif bt == "tool_use" and b.get("name") == FLT.ASK_TOOL:
                    asks[b.get("id")] = b.get("input")
                elif (
                    bt == "tool_use"
                    and b.get("name") == _HANDBACK
                    and o.get("isSidechain")
                ):
                    report = (b.get("input") or {}).get("message")
                    if isinstance(report, str) and report.strip():
                        parts.append(report.strip())
                # other tool_use / tool_result: dropped
            body = "\n\n".join(parts).strip()
            if body:
                # Reached flagged or side-thread only with raw: an error
                # notice is the harness's, and a side-thread's reply is the
                # subagent's, never the session's own assistant.
                role = (
                    "harness"
                    if FLT.harness_flagged(o)
                    else "subagent"
                    if o.get("isSidechain")
                    else "assistant"
                )
                out.append((role, body, ts, None))
    return out if origins else [row[:3] for row in out]


def web_records(path, *, thinking=False, raw=False, **_):
    """``(role, text, ts)`` turns from a claude.ai ``*.raw.json`` dialogue.

    ``ts`` is the message's ISO-8601 ``created_at`` (or ``None`` if absent).
    """
    o = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    out = []
    for m in o.get("chat_messages", []):
        sender = m.get("sender")
        role = "human" if sender == "human" else "assistant"
        blocks = m.get("content") or []
        txt = " ".join(
            b.get("text", "") for b in blocks if b.get("type") == "text"
        ) or m.get("text", "")
        if role == "human":
            turn = _human_slot(txt, raw)
            if turn:
                out.append((*turn, m.get("created_at")))
            continue
        txt = (txt or "").strip()
        # Web captures DO persist thinking text (unlike most cc sessions), so
        # this is the source where --thinking actually recovers content.
        if thinking:
            th = "\n\n".join(
                (b.get("thinking") or b.get("text") or "").strip()
                for b in blocks
                if b.get("type") == "thinking"
                if (b.get("thinking") or b.get("text") or "").strip()
            )
            if th:
                txt = f"[thinking]\n{th}\n\n{txt}".strip()
        if txt:
            out.append((role, txt, m.get("created_at")))
    return out


def agy_records(path, *, thinking=False, raw=False, **_):
    """``(role, text, ts)`` turns from an Antigravity session JSONL.

    ``ts`` is the record's ISO-8601 ``created_at`` (or ``None`` if absent).
    """
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            typ = o.get("type")
            ts = o.get("created_at")
            if typ == "USER_INPUT":
                c = o.get("content") or ""
                m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", c, re.DOTALL)
                turn = _human_slot(m.group(1) if m else c, raw)
                if turn:
                    out.append((*turn, ts))
            elif typ == "PLANNER_RESPONSE":
                parts = []
                if thinking and o.get("thinking"):
                    parts.append("[thinking]\n" + o["thinking"].strip())
                c = o.get("content")
                if isinstance(c, str) and c.strip():
                    parts.append(c.strip())
                body = "\n\n".join(parts).strip()
                if body:
                    out.append(("assistant", body, ts))
    return out


def codex_records(path, *, thinking=False, raw=False, **_):
    """Dialogue from a Codex rollout, with pseudo-user records relabeled.

    The injected AGENTS/environment message is omitted (or shown as
    ``harness`` under ``--raw``). A subagent's parent-written task is ``agent``,
    never ``human``; that distinction is what keeps search/input evidentiary.
    """
    return CODEX_FLT.records(path, thinking=thinking, raw=raw)


_READERS = {
    "cc": cc_records,
    "agy": agy_records,
    "codex": codex_records,
    "web": web_records,
}

# --------------------------------------------------------------------------- #
# red-pen rounds: the author's marks on a rendered copy of the book
# --------------------------------------------------------------------------- #
# A round archived by `publish redpen archive` is not a conversation, but every
# mark in it is the author's own words, typed on the page. It is read as a
# session of author turns so the tools that follow the author's words (the
# trail, its backfill) can reach them. The marks channel holds each comment,
# tracked edit and review-notes edit as a blank-line-separated entry; the
# verbatim channel after it repeats the review-notes blocks and is not read.
_REDPEN_MARKS = re.compile(r"^## MARKS\b.*$", re.M)
_REDPEN_END = re.compile(r"^## (?!MARKS\b)", re.M)
_REDPEN_DATED = re.compile(r"^marks-dated:\s*(\d{4}-\d{2}-\d{2})", re.M)
_REDPEN_BYLINE = re.compile(r"^\[[^\]\n]+\]\s+(\d{4}-\d{2}-\d{2})\b")


def is_redpen_round(path):
    """Whether ``path`` is an archived red-pen round rather than its README."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(_REDPEN_MARKS.search(text))


def redpen_marks(path):
    """``(text, day)`` for each mark in a red-pen round, in the archive's order.

    ``day`` is the date a comment carries in its byline, or the round's own
    ``marks-dated``; the archive records no time of day for any mark.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    start = _REDPEN_MARKS.search(text)
    if not start:
        return []
    body = text[start.end() :]
    end = _REDPEN_END.search(body)
    body = body[: end.start()] if end else body
    dated = _REDPEN_DATED.search(text)
    round_day = date.fromisoformat(dated.group(1)) if dated else None
    out = []
    for block in re.split(r"\n\s*\n", body):
        block = block.strip("\n")
        if not block.strip() or block.lstrip().startswith("### "):
            continue
        byline = _REDPEN_BYLINE.match(block)
        day = date.fromisoformat(byline.group(1)) if byline else round_day
        out.append((block, day))
    return out


def _redpen_title(path):
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return Path(path).stem


def author_turns(kind, path):
    """``(number, logged, text)`` for each of the author's turns in a session.

    Numbered as ``render`` numbers them, and so as search does. ``logged`` is
    the aware instant the log recorded, or ``None`` where it recorded none: a
    pre-rendered gemini review never does, and its ``## You`` sections are the
    turns, each counted even when empty.
    """
    return [(n, logged, text) for n, logged, text, _ in exchanges(kind, path)]


def turn_origins(kind, path):
    """How each of the author's turns reached the log, in ``author_turns`` order.

    One of ``ORIGINS``. They are told apart only in a Claude Code log, the
    one source whose reader learned new paths after it was first used; every
    turn of another source is ``typed``, a Codex answer included.
    """
    if kind != "cc":
        return ["typed"] * len(exchanges(kind, path))
    return [row[3] for row in cc_records(path, origins=True) if row[0] == "human"]


def exchanges(kind, path):
    """``(number, logged, text, reply)``: each author turn and what it answered.

    ``reply`` is the model's text between the author's previous turn and this
    one, the reply the turn was answering, or empty where there was none. It
    is the context a bare "yes, go" needs before it means anything. The turns
    are exactly ``author_turns``'s, which is built from this.
    """
    if kind == "redpen":
        return [
            (n, None, text, "") for n, (text, _) in enumerate(redpen_marks(path), 1)
        ]
    if kind == "rulings":
        return [
            (n, None, text, presented)
            for n, (text, presented) in enumerate(RULINGS.read(path).turns(), 1)
        ]
    if kind == "gemini":
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        starts = [i for i, line in enumerate(lines) if _GEMINI_SPEAKER.match(line)]
        ends = dict(zip(starts, starts[1:] + [len(lines)]))
        out, number, reply = [], 0, []
        for i in starts:
            body = "\n".join(lines[i + 1 : ends[i]]).strip()
            if _GEMINI_SPEAKER.match(lines[i]).group(1) == "You":
                number += 1
                out.append((number, None, body, "\n\n".join(reply)))
                reply = []
            elif body:
                reply.append(body)
        return out
    out, number, reply = [], 0, []
    for role, text, ts in _READERS[kind](path):
        if role == "human":
            number += 1
            out.append((number, _parse_ts(ts), text, "\n\n".join(reply)))
            reply = []
        elif role == "assistant":
            reply.append(text)
    return out


# --------------------------------------------------------------------------- #
# resolution: short id / full id / path  ->  (kind, path)
# --------------------------------------------------------------------------- #
def _jsonl_kind(p):
    """``cc`` / ``agy`` / ``codex`` by the store family holding it, or by shape."""
    family = store.family_of(p)
    if family in (store.AGY, store.CC, store.CODEX):
        return store.KIND_OF[family]
    with p.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                break
            if isinstance(rec, dict) and "step_index" in rec:
                return "agy"
            if (
                isinstance(rec, dict)
                and rec.get("type") == "session_meta"
                and isinstance(rec.get("payload"), dict)
            ):
                return "codex"
            return "cc"
    return "cc"


def _candidates(idarg):
    """All (kind, path) whose id prefix-matches ``idarg``, across the sources."""
    p = Path(idarg)
    if p.is_file():
        name = p.name
        if name.endswith(".jsonl"):
            # Both session corpora use `.jsonl`, so the suffix cannot say which
            # reader to use -- the directory can. Read an agy transcript with the
            # cc reader and it yields no turns at all: an empty transcript that
            # looks like a session in which nothing was said.
            return [(_jsonl_kind(p), p)]
        if name.endswith(".raw.json"):
            return [("web", p)]
        if name.endswith(".md"):
            redpen = _redpen_directory()
            if redpen and p.resolve().parent == redpen.resolve():
                return [("redpen", p)]
            rulings = RULINGS.directory()
            if rulings and p.resolve().parent == rulings.resolve():
                return [("rulings", p)]
            return [("gemini", p)]
    return [
        (kind, path)
        for kind, path in sessions(pending=True)
        if session_id(kind, path).startswith(idarg)
    ]


# Each family's sessions as `show` names them. A web conversation's session is
# its raw export; a gemini review's is its rendered transcript.
_FAMILIES = (
    ("cc", store.CC, "*.jsonl"),
    ("agy", store.AGY, "*.jsonl"),
    ("codex", store.CODEX, "*.jsonl"),
    ("web", store.WEB, "*.raw.json"),
    ("gemini", store.WEB, "gemini-*.md"),
)


def sessions(pending=False):
    """Every session as ``(kind, path)``, family by family.

    The accepted corpus, which is what search, metrics and the catalog read.
    With ``pending``, a session captured since it was accepted is read at its
    pending capture instead, and one never accepted joins: that is the view an
    author reviewing a capture needs, and a trail following a turn made after
    the last acceptance.
    """
    found = []
    for kind, family, pattern in _FAMILIES:
        chosen = {p.name: p for p in store.files(family, pattern)}
        if pending:
            chosen.update({p.name: p for p in store.pending(family, pattern)})
        found += [(kind, chosen[name]) for name in sorted(chosen)]
    redpen = _redpen_directory()
    if redpen is not None:
        found += [
            ("redpen", f) for f in sorted(redpen.glob("*.md")) if is_redpen_round(f)
        ]
    found += [("rulings", f) for f in RULINGS.rounds()]
    return found


def _redpen_directory():
    """The red-pen archive, only where the project declares one.

    The rounds are private and their author's, so they join the sessions
    only by the project's explicit ``provenance.redpen``, never by default.
    """
    project = current_project()
    if project.get("provenance.redpen") is None:
        return None
    return project.path("provenance.redpen")


REDPEN_PREFIX = "redpen-"


def session_id(kind, path):
    """The full id a session is known by: its file name without the suffix.

    A red-pen round is named for its archive file with a ``redpen-`` prefix,
    and a review-sheet ruling with ``rulings-``, as a gemini review's file
    already carries ``gemini-``, so the id says what it names wherever it is
    printed.
    """
    name = Path(path).name
    if kind == "web":
        return name[: -len(".raw.json")]
    if kind == "redpen":
        return REDPEN_PREFIX + Path(path).stem
    if kind == "rulings":
        return RULINGS.session_id(path)
    return Path(path).stem


def resolve(idarg):
    """``(kind, path)`` for an id, or raise ``LookupError`` (none / ambiguous)."""
    found = _candidates(idarg)
    if not found:
        raise LookupError(f"no session matches '{idarg}'")
    if len(found) > 1:
        names = ", ".join(f"{k}:{pp.name}" for k, pp in found)
        raise LookupError(f"'{idarg}' is ambiguous ({len(found)} matches): {names}")
    return found[0]


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def session_title(kind, path):
    """A session's title as its transcript header shows it, best-effort."""
    return _header(kind, Path(path))[1]


def _header(kind, path):
    """``(short_id, title, date, model)`` for the transcript header, best-effort."""
    if kind == "cc":
        meta = CAT._cc_scan(path)
        return CAT._short_id(path.stem), meta["title"], meta["date"], meta["model"]
    if kind == "agy":
        meta = CAT._agy_scan(path)
        return CAT._short_id(path.stem), meta["title"], meta["date"], meta["model"]
    if kind == "codex":
        meta = CODEX_FLT.scan(path)
        return CAT._short_id(path.stem), meta["title"], meta["date"], meta["model"]
    if kind == "web":
        base = path.name[: -len(".raw.json")]
        # Accepted together, the rendered transcript sits beside its export.
        md = path.parent / (base + ".md")
        if not md.exists():
            md = store.newest(store.WEB, base + ".md") or md
        meta = (
            CAT._web_scan(md)
            if md.exists()
            else {"title": "(untitled)", "date": "?", "model": "?"}
        )
        return base, meta["title"], meta["date"], meta.get("model", "?")
    if kind == "redpen":
        day = session_date(kind, path)
        return (
            session_id(kind, path),
            _redpen_title(path),
            day.isoformat() if day else "?",
            "red-pen round",
        )
    if kind == "rulings":
        day = session_date(kind, path)
        return (
            session_id(kind, path),
            RULINGS.read(path).title,
            day.isoformat() if day else "?",
            "review-sheet ruling",
        )
    meta = (
        CAT._web_scan(path)
        if path.exists()
        else {"title": "(untitled)", "date": "?", "model": "?"}
    )
    return path.stem, meta["title"], meta["date"], meta.get("model", "?")


def _parse_ts(ts):
    """ISO-8601 stamp (``Z`` or offset) -> aware ``datetime``, or ``None``."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _fmt_dur(seconds):
    """Human-compact duration: ``42s`` / ``6m21s`` / ``2h05m``."""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _local(dt, tz):
    """``dt`` in the zone ``tz``, or the machine's local zone when it is ``None``."""
    return dt.astimezone(tz)


def session_start(kind, path):
    """When a session's first turn was logged, as the transcript header gives it.

    ``None`` for a pre-rendered gemini review, or a log that records no times.
    """
    if kind not in _READERS:
        return None
    dts = [dt for dt in (_parse_ts(ts) for _, _, ts in _READERS[kind](path)) if dt]
    return min(dts) if dts else None


def session_date(kind, path):
    """The UTC day a session began, or ``None`` where nothing records one.

    A log's first logged time decides it. A pre-rendered gemini review logs no
    times, but its header carries the date the author supplied at capture; a
    red-pen round carries the date of its marks. Those dates are how a
    timeless session takes its place in date order instead of sorting first.
    """
    start = session_start(kind, path)
    if start is not None:
        return start.astimezone(timezone.utc).date()
    if kind == "redpen":
        days = [day for _, day in redpen_marks(path) if day]
        return min(days) if days else None
    if kind == "rulings":
        return RULINGS.read(path).day()
    if kind == "gemini":
        written = CAT._web_scan(Path(path)).get("date") or ""
        try:
            return date.fromisoformat(written[:10])
        except ValueError:
            return None
    return None


def shown_date(dt, utc=False):
    """``dt``'s calendar date in the zone a transcript is shown in."""
    return _local(dt, timezone.utc if utc else None).strftime("%Y-%m-%d")


def _span_line(dts, date, model, nturns, tz):
    """The header meta line: time span + duration + model + turn count.

    Falls back to the bare ``(date)`` the catalog scan found when no turn
    carried a parseable timestamp (e.g. a gemini review or a stampless log).
    """
    tail = f"{model} · {nturns} turns"
    if not dts:
        return f"({date}) · {tail}"
    lo, hi = _local(min(dts), tz), _local(max(dts), tz)
    zone = lo.strftime("%Z") or ("UTC" if tz else "local")
    start = lo.strftime("%Y-%m-%d %H:%M")
    end = hi.strftime("%H:%M" if lo.date() == hi.date() else "%Y-%m-%d %H:%M")
    dur = _fmt_dur((hi - lo).total_seconds())
    return f"{start} → {end} {zone} · {dur} · {tail}"


def _stamp_rows(shown, tz, session):
    """``(role, text, dt, stamp, number)`` rows from ``(role, text, dt, number)``.

    The date is printed when it rolls over from the prior turn, and wherever
    the time alone could fall on another day of ``session`` too, which happens
    only in a session logged over more than a day. So every printed stamp read
    back as ``--from`` / ``--to`` names its own turn. Stamps are computed over
    the whole shown list before any time window is applied, so a turn prints
    the same stamp however the window is cut.
    """
    rows, prev_day = [], None
    for r, text, dt, number in shown:
        stamp = ""
        if dt is not None:
            loc = _local(dt, tz)
            day = loc.strftime("%Y-%m-%d")
            clock = loc.time().replace(microsecond=0)
            bare = (
                day == prev_day and len(_bare_spans(clock, _SECOND, tz, session)) == 1
            )
            stamp = loc.strftime("%H:%M:%S" if bare else "%Y-%m-%d %H:%M:%S")
            prev_day = day
        rows.append((r, text, dt, stamp, number))
    return rows


_SECOND = timedelta(seconds=1)

# A mark: a date, a time, or a date and a time; a time may name its zone.
_MARK = re.compile(
    r"(?:(?P<date>\d{4}-\d{2}-\d{2})(?:[ T](?=\d)|$))?"
    r"(?:(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    r"(?::(?P<second>\d{2})(?:\.(?P<fraction>\d{1,6}))?)?"
    r"(?P<zone>Z|[+-]\d{2}:?\d{2})?)?"
)


def _offset(zone):
    """The fixed zone a mark names: ``Z`` or ``+HH:MM`` / ``-HHMM``."""
    if zone == "Z":
        return timezone.utc
    sign = -1 if zone[0] == "-" else 1
    digits = zone[1:].replace(":", "")
    return timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))


def _zoned(naive, tz):
    """A wall-clock ``naive`` datetime read in ``tz``, or in the machine's zone."""
    return naive.astimezone() if tz is None else naive.replace(tzinfo=tz)


def _days(session, tz):
    """The calendar days, in ``tz``, that ``session`` was logged on."""
    first, last = (_local(dt, tz).date() for dt in session)
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def _bare_spans(clock, unit, tz, session):
    """``(day, start, end)`` for each day a time without a date could name.

    ``session`` is the ``(first, last)`` instants the whole session was logged
    at, and days are counted in ``tz``. A session on one day offers that day.
    Across midnight, a day qualifies only if the session was running at
    ``clock`` on it, so more than one qualifies only in a session logged over
    more than a day.
    """
    spans = []
    for day in _days(session, tz):
        start = datetime.combine(day, clock)
        spans.append((day, _zoned(start, tz), _zoned(start + unit, tz)))
    if len(spans) > 1:
        spans = [(d, s, e) for d, s, e in spans if s <= session[1] and e > session[0]]
    return spans


def _mark_span(flag, mark, tz, session):
    """``(start, end)``: the aware instants a ``--from`` / ``--to`` mark covers.

    A mark covers the whole unit it is written to, so ``15:52`` is the minute
    up to ``15:52:59``, ``2026-09-16`` is the day, and a log's own stamp such
    as ``2026-09-16T15:52:10.123Z`` is its millisecond. It is read in ``tz``,
    the zone the stamps are shown in, unless it names its own with ``Z`` or
    ``+HH:MM``; then it means the same moment on any machine. A bare time takes
    its date from ``session`` as ``_bare_spans`` describes, counting days in
    the mark's zone. Raises ``LookupError`` when the mark is not a time, or
    when a bare time fits no single day.
    """
    m = _MARK.fullmatch(mark.strip())
    try:
        if not m or not (m["date"] or m["hour"]):
            raise ValueError
        day = date.fromisoformat(m["date"]) if m["date"] else None
        fraction = m["fraction"] or ""
        clock = time(
            int(m["hour"] or 0),
            int(m["minute"] or 0),
            int(m["second"] or 0),
            int(fraction.ljust(6, "0")),
        )
        if m["zone"]:
            tz = _offset(m["zone"])
    except ValueError:
        raise LookupError(
            f"{flag} {mark!r} is not a time; write HH:MM[:SS], optionally "
            "after a date as 'YYYY-MM-DD HH:MM[:SS]' and before a zone as "
            "Z or +HH:MM"
        ) from None
    if fraction:
        unit = timedelta(microseconds=10 ** (6 - len(fraction)))
    elif m["second"]:
        unit = _SECOND
    elif m["hour"]:
        unit = timedelta(minutes=1)
    else:
        unit = timedelta(days=1)
    if day:
        start = datetime.combine(day, clock)
        return _zoned(start, tz), _zoned(start + unit, tz)

    spans = _bare_spans(clock, unit, tz, session)
    if len(spans) != 1:
        # None qualifies when the session was not running at that time on any
        # of its days; then each of them is as good a guess as the other.
        days = [d for d, _, _ in spans] or _days(session, tz)
        first, last = (_local(dt, tz) for dt in session)
        options = " or ".join(f"'{d:%Y-%m-%d} {mark.strip()}'" for d in days)
        raise LookupError(
            f"{flag} {mark!r} could fall on more than one day of a session "
            f"logged from {first:%Y-%m-%d %H:%M:%S} to "
            f"{last:%Y-%m-%d %H:%M:%S}; put the date in front: {options}"
        )
    return spans[0][1:]


def _window(rows, frm, to, tz, session):
    """The ``rows`` whose logged time falls between ``--from`` and ``--to``.

    Both bounds are inclusive. Returns ``(kept, unstamped)``, where
    ``unstamped`` counts shown turns left out because no time was logged for
    them. Raises ``LookupError`` if the session has no times at all, if the
    bounds cross, or if no shown turn falls inside them.
    """
    if session is None:
        raise LookupError("--from/--to need per-turn timestamps; this log has none")
    lo = _mark_span("--from", frm, tz, session)[0] if frm else None
    hi = _mark_span("--to", to, tz, session)[1] if to else None
    if lo is not None and hi is not None and lo >= hi:
        raise LookupError(f"--from {frm!r} falls after --to {to!r} (empty range)")
    kept = [
        row
        for row in rows
        if row[2] is not None
        and (lo is None or row[2] >= lo)
        and (hi is None or row[2] < hi)
    ]
    if not kept:
        bounds = (f" from {frm!r}" if frm else "") + (f" to {to!r}" if to else "")
        raise LookupError(f"no shown turn falls in the window{bounds}")
    return kept, sum(1 for row in rows if row[2] is None)


def _turn_range(spec, count):
    """``(first, last)`` author-turn numbers named by ``--turn N`` or ``N-M``.

    Raises ``LookupError`` unless the numbers are a range within the
    ``count`` turns the author took in the session.
    """
    m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", spec)
    if not m:
        raise LookupError(f"--turn {spec!r} is not a turn number; write N or N-M")
    first, last = int(m.group(1)), int(m.group(2) or m.group(1))
    if first < 1 or last < first:
        raise LookupError(f"--turn {spec!r} is not a range of turns counted from 1")
    if last > count:
        took = f"{count} turn{'' if count == 1 else 's'}" if count else "no turns"
        raise LookupError(f"--turn {spec!r}: the author took {took} in this session")
    return first, last


# A gemini review's speaker lines; its replies carry `## ` headings of their own.
_GEMINI_SPEAKER = re.compile(r"^##\s+(You|Gemini)\s+·")


def _gemini_turns(body, spec):
    """The lines of a pre-rendered gemini review from ``--turn`` ``spec``.

    The author's turns are its ``## You`` sections, numbered as ``search``
    numbers them: every section counts, even one with nothing left to say.
    """
    lines = body.splitlines()
    starts = [i for i, line in enumerate(lines) if _GEMINI_SPEAKER.match(line)]
    yours = [i for i in starts if _GEMINI_SPEAKER.match(lines[i]).group(1) == "You"]
    first, last = _turn_range(spec, len(yours))
    end = yours[last] if last < len(yours) else len(lines)
    return "\n".join(lines[yours[first - 1] : end]).rstrip()


_GEMINI_ROLE = {"human": "You", "assistant": "Gemini"}


def _gemini_role(body, role):
    """Only the speaker sections of a gemini review that ``role`` names.

    The review is pre-rendered, so its sections are its speakers: ``## You``
    is the author and ``## Gemini`` the model. It holds nothing an agent or
    the harness wrote, so those roles leave it empty.
    """
    speaker = _GEMINI_ROLE.get(role)
    lines = body.splitlines()
    starts = [i for i, line in enumerate(lines) if _GEMINI_SPEAKER.match(line)]
    kept = []
    for i, end in zip(starts, starts[1:] + [len(lines)]):
        if speaker and _GEMINI_SPEAKER.match(lines[i]).group(1) == speaker:
            kept += lines[i:end]
    return "\n".join(kept).rstrip()


def _source(path):
    """The source line, which says when the capture has not been accepted."""
    pending = store.area(path) == "pending"
    return f"<source: {Path(path).name}{' · pending, not accepted' if pending else ''}>"


def render(
    kind,
    path,
    *,
    role="all",
    thinking=False,
    raw=False,
    utc=False,
    frm=None,
    to=None,
    turn=None,
):
    """The full transcript as one string."""
    if turn and (frm or to):
        raise LookupError("--turn names turns by number; drop --from/--to")
    sid, title, date, model = _header(kind, path)
    scope = f"turn{'s' * ('-' in turn)} {turn.strip()}" if turn else None

    if kind == "gemini":
        if frm or to:
            raise LookupError(
                "--from/--to need per-turn timestamps; the gemini "
                "reviews are pre-rendered and carry none"
            )
        # The gemini .md is already a rendered transcript; pass it through.
        body = Path(path).read_text(encoding="utf-8", errors="replace").rstrip()
        head = f"# {sid} — {title}  ({date})"
        if turn:
            body = _gemini_turns(body, turn)
            head += f" · {scope}"
        if role != "all":
            body = _gemini_role(body, role)
        return "\n".join([head, _source(path), "", body]) + "\n"

    if kind == "redpen":
        if frm or to:
            raise LookupError(
                "--from/--to need per-turn timestamps; a red-pen round "
                "records only the day of each mark"
            )
        marks = redpen_marks(path)
        first, last = _turn_range(turn, len(marks)) if turn else (1, len(marks))
        head = f"# {sid} — {title}  ({date})" + (f" · {scope}" if turn else "")
        lines = [head, _source(path), ""]
        if role in ("all", "human"):
            for n, (text, day) in enumerate(marks, 1):
                if first <= n <= last:
                    stamp = f" · {day.isoformat()}" if day else ""
                    lines += [f"## {ROLE_LABEL['human']} {n}{stamp}", "", text, ""]
        return "\n".join(lines).rstrip() + "\n"

    if kind == "rulings":
        if frm or to:
            raise LookupError(
                "--from/--to need per-turn timestamps; a review-sheet ruling "
                "records one time, when it was marked final"
            )
        ruling = RULINGS.read(path)
        rows = exchanges(kind, path)
        first, last = _turn_range(turn, len(rows)) if turn else (1, len(rows))
        head = f"# {sid} — {title}  ({date})" + (f" · {scope}" if turn else "")
        moment = ruling.instant()
        ruled = (
            f"ruled {_local(moment, timezone.utc if utc else None):%Y-%m-%d %H:%M %Z}"
            if moment
            else "ruled at an unrecorded time"
        )
        lines = [head, f"{model} · {ruled} · {len(rows)} turns", _source(path), ""]
        labels = [item.label for item in ruling.items] + ["notes"]
        for n, _logged, text, presented in rows:
            if not first <= n <= last:
                continue
            if presented and role in ("all", "assistant"):
                lines += [f"## Sheet · {labels[n - 1]}", "", presented, ""]
            if role in ("all", "human"):
                lines += [
                    f"## {ROLE_LABEL['human']} {n} · {labels[n - 1]}",
                    "",
                    text,
                    "",
                ]
        return "\n".join(lines).rstrip() + "\n"

    # Each turn carries the number of the author's latest turn at or before
    # it: their own on an author's turn, and theirs on the replies that follow.
    # Counted over the whole session before --role, it is search's numbering.
    records, number = [], 0
    for r, text, ts in _READERS[kind](path, thinking=thinking, raw=raw):
        number += r == "human"
        records.append((r, text, _parse_ts(ts), number))
    shown = [rec for rec in records if role == "all" or rec[0] == role]
    # A bare time picks its day from the whole session, never from the shown
    # turns, so --role cannot change what a time means or how it is printed.
    logged = [rec[2] for rec in records if rec[2]]
    session = (min(logged), max(logged)) if logged else None
    tz = timezone.utc if utc else None
    rows = _stamp_rows(shown, tz, session)
    if frm or to:
        rows, unstamped = _window(rows, frm, to, tz, session)
    if turn:
        first, last = _turn_range(turn, number)
        rows = [row for row in rows if first <= row[4] <= last]
        if not rows:
            raise LookupError(f"--turn {turn!r} holds no {role} turn")
    dts = [row[2] for row in rows if row[2]]

    span = _span_line(dts, date, model, len(rows), tz)
    if frm or to:
        span += " · slice"
        if unstamped:
            noun = "turn" if unstamped == 1 else "turns"
            span += f" ({unstamped} {noun} with no time left out)"
    if turn:
        span += f" · {scope}"
    lines = [f"# {sid} — {title}", span, _source(path), ""]
    for r, text, _dt, stamp, n in rows:
        label = f"{ROLE_LABEL[r]} {n}" if r == "human" else ROLE_LABEL[r]
        lines.append(f"## {label}" + (f" · {stamp}" if stamp else ""))
        lines.append("")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="quirework provenance show",
        description="Render a provenance session as a clean transcript.",
    )
    ap.add_argument("session", help="short id, full id, gemini-… name, or path")
    ap.add_argument(
        "--role",
        choices=["all", "human", "assistant", "agent", "subagent", "harness"],
        default="all",
        help="restrict to one writer (default: all): human is the author; "
        "assistant is the model's reply; agent is a task one model wrote "
        "for a subagent, and subagent is what the subagent wrote back; "
        "harness is text the tool injected; agent, subagent and harness are "
        "shown only with --raw",
    )
    ap.add_argument(
        "--thinking",
        action="store_true",
        help="include assistant thinking where the capture holds its "
        "text: web captures and agy sessions do; cc sessions "
        "only for direct-API runs (subscription ones persist "
        "an opaque signature, so there it is usually empty)",
    )
    ap.add_argument(
        "--raw",
        action="store_true",
        help="keep the session as logged: harness wrappers stay on human "
        "turns, and injected text and subagent threads appear under the "
        "harness and agent roles",
    )
    ap.add_argument(
        "--utc",
        action="store_true",
        help="stamp turns in UTC (default: the machine's local zone)",
    )
    ap.add_argument(
        "--from",
        dest="frm",
        metavar="TIME",
        default=None,
        help="show only turns logged at or after TIME: HH:MM[:SS], or "
        "'YYYY-MM-DD HH:MM[:SS]' when the session crosses midnight. TIME "
        "is read in the zone the turns are stamped in, so pair it with "
        "the same --utc setting, unless it ends in its own zone as Z or "
        "+HH:MM (as in --from 16:45:26Z); then it means the same moment "
        "on any machine",
    )
    ap.add_argument(
        "--to",
        dest="to",
        metavar="TIME",
        default=None,
        help="show only turns logged at or before TIME, which covers its "
        "whole last unit: --to 15:52 keeps a turn at 15:52:59",
    )
    ap.add_argument(
        "--turn",
        metavar="N[-M]",
        default=None,
        help="show the author's turn N, numbered from 1 as search numbers "
        "it, with the replies up to their next turn; N-M shows a run of "
        "them. Not combined with --from/--to",
    )
    ap.add_argument(
        "--out", type=Path, default=None, help="write to a file instead of stdout"
    )
    args = ap.parse_args(argv)

    try:
        kind, path = resolve(args.session)
    except LookupError as e:
        print(str(e), file=sys.stderr)
        return 2

    try:
        text = render(
            kind,
            path,
            role=args.role,
            thinking=args.thinking,
            raw=args.raw,
            utc=args.utc,
            frm=args.frm,
            to=args.to,
            turn=args.turn,
        )
    except LookupError as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
