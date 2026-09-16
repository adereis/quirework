#!/usr/bin/env python3
"""Render a provenance session as a clean, readable transcript.

The read-half companion to ``quirework provenance search``: search tells you *which*
session mentioned X (by short id); show prints that session's human/assistant
turns in order, with tool calls, tool-result echoes, thinking, and harness
plumbing stripped, so a session can be *read* without hand-grepping the raw
JSONL. (Hand-rolling that extraction is exactly what this command exists to
retire.)

    quirework provenance show 31cf63ed                # cc-session, human + assistant prose
    quirework provenance search Mabel --show          # find the session, then:
    quirework provenance show 31cf63ed --thinking     # include the assistant's thinking
    quirework provenance show 31cf63ed --role human   # only the human's turns
    quirework provenance show 31cf63ed --raw          # keep harness wrappers + meta/sidechain
    quirework provenance show 31cf63ed --utc          # stamp turns in UTC, not local time
    quirework provenance show 31cf63ed --from '15:47:04' --to '15:52:10'   # a slice by mark
    quirework provenance show c00a7331                # a claude.ai web dialogue (both speakers)

The id is the short id search prints (an 8-hex prefix, or the full ``gemini-…``
name); a full uuid or a path works too. It resolves across the corpus families:
local CLI sessions (Claude Code, Antigravity, and Codex), claude.ai
``*.raw.json`` web dialogues, and the already-rendered ``gemini-*.md`` reviews.

Every turn carries the timestamp the raw log recorded for it (already present in
the stored transcripts — cc ``timestamp``, agy/web ``created_at`` — so nothing
needed re-capturing), and the header states the session's span and duration.
Times render in the machine's local zone by default; ``--utc`` keeps them as
logged. The already-rendered ``gemini-*.md`` reviews carry no per-turn stamps,
so those pass through unstamped.

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
import sys
from datetime import datetime
from pathlib import Path

from quirework.project import current_project
from quirework.provenance import ccfilter as FLT
from quirework.provenance import codexfilter as CODEX_FLT
from quirework.provenance import catalog as CAT
from quirework.provenance.metrics import HARNESS

ROLE_LABEL = {
    "human": "Human",
    "assistant": "Assistant",
    "agent": "Agent prompt (machine-written)",
    "harness": "Harness context",
}


def _clean(text, raw):
    """Strip harness wrappers from a human turn; '' if nothing prose survives.

    With ``raw`` the text is returned untouched (slash-commands, system
    reminders and all), for when you want the session exactly as logged.
    """
    if raw:
        return text or ""
    cleaned = FLT.strip_wrappers(text or "")
    if not cleaned or cleaned.startswith(HARNESS):
        return ""
    return cleaned


# --------------------------------------------------------------------------- #
# turn extraction -> a flat list of (role, text), in conversation order
# --------------------------------------------------------------------------- #
def cc_records(path, *, thinking=False, raw=False):
    """``(role, text, ts)`` turns from a cc-session JSONL.

    ``ts`` is the record's ISO-8601 ``timestamp`` (or ``None`` if absent).
    Assistant messages collapse their content blocks into one turn: ``text``
    blocks as prose, ``thinking`` blocks (only with ``thinking``) prefixed and
    set off, ``tool_use`` blocks dropped. Human turns are the string-content
    messages; list content is a tool-result echo and is skipped. ``isMeta`` /
    ``isSidechain`` (harness injections, subagent side-threads) are dropped
    unless ``raw``.
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
            if typ not in ("user", "assistant"):
                continue
            if not raw and (o.get("isMeta") or o.get("isSidechain")):
                continue
            ts = o.get("timestamp")
            content = (o.get("message") or {}).get("content")
            if typ == "user":
                if not isinstance(content, str):  # list content = tool_result echo
                    continue
                text = _clean(content, raw).strip()
                if text:
                    out.append(("human", text, ts))
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
                # tool_use / tool_result: dropped
            body = "\n\n".join(parts).strip()
            if body:
                out.append(("assistant", body, ts))
    return out


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
        txt = (_clean(txt, raw) if role == "human" else (txt or "")).strip()
        # Web captures DO persist thinking text (unlike most cc sessions), so
        # this is the source where --thinking actually recovers content.
        if thinking and role == "assistant":
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
    import re as _re

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
                m = _re.search(
                    r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", c, _re.DOTALL
                )
                text = (m.group(1) if m else c).strip()
                if not raw:
                    text = FLT.strip_wrappers(text)
                    if not text or text.startswith(HARNESS):
                        continue
                if text:
                    out.append(("human", text, ts))
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


# --------------------------------------------------------------------------- #
# resolution: short id / full id / path  ->  (kind, path)
# --------------------------------------------------------------------------- #
def _jsonl_kind(p):
    """``cc`` / ``agy`` / ``codex`` by corpus directory or record shape."""
    try:
        if (
            p.resolve().parent
            == current_project()
            .path("provenance.agy_sessions", ".quirework/provenance/agy-sessions")
            .resolve()
        ):
            return "agy"
        if (
            p.resolve().parent
            == current_project()
            .path("provenance.claude_sessions", ".quirework/provenance/cc-sessions")
            .resolve()
        ):
            return "cc"
        if (
            p.resolve().parent
            == current_project()
            .path("provenance.codex_sessions", ".quirework/provenance/codex-sessions")
            .resolve()
        ):
            return "codex"
    except OSError:
        pass
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
            # cc reader and it yields no turns at all, which surfaces as a
            # baffling `--from None falls after --to None` from the slicer.
            return [(_jsonl_kind(p), p)]
        if name.endswith(".raw.json"):
            return [("web", p)]
        if name.endswith(".md"):
            return [("gemini", p)]
    found = []
    for f in sorted(
        current_project()
        .path("provenance.claude_sessions", ".quirework/provenance/cc-sessions")
        .glob("*.jsonl")
    ):
        if f.stem == idarg or f.stem.startswith(idarg):
            found.append(("cc", f))
    for f in sorted(
        current_project()
        .path("provenance.agy_sessions", ".quirework/provenance/agy-sessions")
        .glob("*.jsonl")
    ):
        if f.stem == idarg or f.stem.startswith(idarg):
            found.append(("agy", f))
    for f in sorted(
        current_project()
        .path("provenance.codex_sessions", ".quirework/provenance/codex-sessions")
        .glob("*.jsonl")
    ):
        if f.stem == idarg or f.stem.startswith(idarg):
            found.append(("codex", f))
    for f in sorted(
        current_project()
        .path("provenance.conversations", ".quirework/provenance/conversations")
        .glob("*.raw.json")
    ):
        base = f.name[: -len(".raw.json")]
        if base == idarg or base.startswith(idarg):
            found.append(("web", f))
    for f in sorted(
        current_project()
        .path("provenance.conversations", ".quirework/provenance/conversations")
        .glob("gemini-*.md")
    ):
        if f.stem == idarg or f.stem.startswith(idarg):
            found.append(("gemini", f))
    return found


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
        md = current_project().path(
            "provenance.conversations", ".quirework/provenance/conversations"
        ) / (base + ".md")
        meta = (
            CAT._web_scan(md)
            if md.exists()
            else {"title": "(untitled)", "date": "?", "model": "?"}
        )
        return base, meta["title"], meta["date"], meta.get("model", "?")
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


def _local(dt, utc):
    """``dt`` in UTC (``utc``) or the machine's local zone."""
    return dt if utc else dt.astimezone()


def _span_line(dts, date, model, nturns, utc):
    """The header meta line: time span + duration + model + turn count.

    Falls back to the bare ``(date)`` the catalog scan found when no turn
    carried a parseable timestamp (e.g. a gemini review or a stampless log).
    """
    tail = f"{model} · {nturns} turns"
    if not dts:
        return f"({date}) · {tail}"
    lo, hi = _local(min(dts), utc), _local(max(dts), utc)
    tz = lo.strftime("%Z") or ("UTC" if utc else "local")
    start = lo.strftime("%Y-%m-%d %H:%M")
    end = hi.strftime("%H:%M" if lo.date() == hi.date() else "%Y-%m-%d %H:%M")
    dur = _fmt_dur((hi - lo).total_seconds())
    return f"{start} → {end} {tz} · {dur} · {tail}"


def _stamp_rows(shown, utc):
    """Attach each turn's displayed stamp: ``(role, text, dt, stamp)``.

    The date is repeated only when it rolls over from the prior turn, so ``stamp``
    is exactly the string the header prints -- the *mark* ``--from`` / ``--to``
    match against. Stamps are computed over the whole shown list before any mark
    slicing, so a turn's mark is stable no matter how the range is cut.
    """
    rows, prev_day = [], None
    for r, text, dt in shown:
        stamp = ""
        if dt is not None:
            loc = _local(dt, utc)
            day = loc.strftime("%Y-%m-%d")
            stamp = loc.strftime("%H:%M:%S" if day == prev_day else "%Y-%m-%d %H:%M:%S")
            prev_day = day
        rows.append((r, text, dt, stamp))
    return rows


def _slice_marks(rows, frm, to):
    """Inclusive slice of ``rows`` between the ``--from`` and ``--to`` marks.

    A mark must equal a turn's displayed stamp exactly: ``--from`` anchors on its
    first match, ``--to`` on its last. Raises ``LookupError`` if a supplied mark
    matches no shown turn, or if the two cross into an empty range.
    """
    lo, hi = 0, len(rows)
    if frm:
        hits = [i for i, row in enumerate(rows) if row[3] == frm]
        if not hits:
            raise LookupError(f"--from mark {frm!r} matches no shown turn")
        lo = hits[0]
    if to:
        hits = [i for i, row in enumerate(rows) if row[3] == to]
        if not hits:
            raise LookupError(f"--to mark {to!r} matches no shown turn")
        hi = hits[-1] + 1
    if lo >= hi:
        raise LookupError(f"--from {frm!r} falls after --to {to!r} (empty range)")
    return rows[lo:hi]


def render(
    kind, path, *, role="all", thinking=False, raw=False, utc=False, frm=None, to=None
):
    """The full transcript as one string."""
    sid, title, date, model = _header(kind, path)

    if kind == "gemini":
        if frm or to:
            raise LookupError(
                "--from/--to need per-turn timestamps; the gemini "
                "reviews are pre-rendered and carry none"
            )
        # The gemini .md is already a rendered transcript; pass it through.
        lines = [f"# {sid} — {title}  ({date})", f"<source: {path.name}>", ""]
        lines.append(Path(path).read_text(encoding="utf-8", errors="replace").rstrip())
        return "\n".join(lines) + "\n"

    recfn = {
        "cc": cc_records,
        "agy": agy_records,
        "codex": codex_records,
        "web": web_records,
    }[kind]
    records = recfn(path, thinking=thinking, raw=raw)
    shown = [
        (r, text, _parse_ts(ts))
        for r, text, ts in records
        if role == "all" or r == role
    ]
    rows = _slice_marks(_stamp_rows(shown, utc), frm, to)
    dts = [dt for _, _, dt, _ in rows if dt]

    span = _span_line(dts, date, model, len(rows), utc)
    if frm or to:
        span += " · slice"
    lines = [f"# {sid} — {title}", span, f"<source: {path.name}>", ""]
    for r, text, _dt, stamp in rows:
        head = f"## {ROLE_LABEL[r]}" + (f" · {stamp}" if stamp else "")
        lines.append(head)
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
        choices=["all", "human", "assistant", "agent"],
        default="all",
        help="restrict to one speaker (default: all)",
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
        help="keep harness wrappers and meta/sidechain turns",
    )
    ap.add_argument(
        "--utc",
        action="store_true",
        help="stamp turns in UTC as logged (default: machine local time)",
    )
    ap.add_argument(
        "--from",
        dest="frm",
        metavar="MARK",
        default=None,
        help="start at the turn whose shown timestamp equals MARK exactly "
        "(e.g. --from '15:47:04'); pair with the same --utc setting",
    )
    ap.add_argument(
        "--to",
        dest="to",
        metavar="MARK",
        default=None,
        help="end at (and include) the turn whose shown timestamp equals MARK",
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
