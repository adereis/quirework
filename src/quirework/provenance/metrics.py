#!/usr/bin/env python3
"""Effort/contribution metrics across the whole provenance corpus.

Reads the configured provenance sources and reports human-vs-AI effort:
  - claude.ai web dialogues   : conversations/*.raw.json   (structured, timestamps)
  - local Claude Code sessions: cc-sessions/*.jsonl        (structured, timestamps)
  - local Codex sessions      : codex-sessions/*.jsonl     (structured, timestamps)
  - cross-model Gemini reviews: conversations/gemini-*.md  (rendered; no timestamps)

MODEL METADATA IS NOT TRUSTWORTHY, IN ANY SOURCE. Read this before using
any model id from this report for attribution. Three independent ways the
recorded model can name a model that did not write the text:
  * WEB RE-STAMP (claude.ai). The export stores one conversation-level
    last-selected model, and merely OPENING an old thread with a newer model
    re-stamps it. Observed: a 2025 Sonnet-era session came back tagged Fable,
    a model that did not exist when the session ran. Anachronism is the only
    reason it was caught; a plausible-but-wrong re-stamp would not be.
  * MID-SESSION SAFETY HOT-SWAP (Claude Code / API). A Fable guardrail can
    flag a turn, reload the whole context into Opus, warn the user, and the
    client then shows Opus from that point on -- while "fable" persists in
    some of the metadata. So a per-record model id can name the model that was
    *selected*, not the one that *served*. This means the cc-era per-record
    ids below are unreliable too, not just the web ones.
  * THE MODEL'S OWN SELF-REPORT. An assistant's session context can assert the
    wrong model, and the assistant has no way to check: its only evidence is
    the same context that may be wrong. Observed 2026-07-12, when a session
    serving as Opus asserted it was Fable and committed a wrong Co-Authored-By
    trailer. Never ask the model which model it is.
  RULE: treat every model id here as a WEAK INDICATOR of the mix, never as
  attribution. Do not attribute a turn, a claim, or a piece of prose to a
  model on the strength of metadata. Where the model matters (the cold-read,
  the effort artifact, a commit trailer), take it from the human or the
  harness, and say so.

Metrics are deliberately conservative and labeled with their caveats:
  * "human prompt" = a turn the human typed/sent (NOT tool output, NOT AI turns).
  * AI text counts response prose AND thinking blocks (separately reported).
  * Tool I/O (file reads, command output) is excluded from text volume — it is
    environment plumbing, not authored conversation.
  * Tokens are a chars/4 estimate, not a tokenizer count.
  * "Engaged wall-clock" sums each session's first->last message gap; it ignores
    idle gaps between sessions (those show up in the calendar span instead).

Pasted content (e.g. the human pasting the whole spec into a turn) inflates
"human words" and cannot be reliably separated from typed input. Rather than
ignore it, the `paste` sub-report estimates it (see below), and the metrics
report prints a paste-corrected AI:human ratio alongside the raw one.

Three metric families, selectable from the command line:
  1. CONVERSATION metrics, from the provenance logs above (effort spent thinking
     the work into existence).
  2. REPO metrics, from `git` in the repo root (the document's own refinement
     history). These two cover different, only-partly-overlapping spans: the
     conversations begin months before the git repo was even created, so the
     calendar span comes from the logs and the commit history is a recent slice.
  3. PASTE audit: of the raw "human words", how many were typed vs pasted/relayed
     (whole document versions, essay plans, text ferried from other AI sessions).
     Classifies large human turns by structural signals (markdown headings /
     bullets / blockquotes / tables / fences, incl. the backslash-escaped
     markdown the chat client emits on paste) and relay-phrase signals; reports a
     banded estimate, erring toward flagging paste and surfacing the uncertain
     volume as an explicit error margin. Signals, not ground truth.

Usage:
  quirework provenance metrics                 # everything (metrics + repo + paste summary)
  quirework provenance metrics metrics         # conversation + repo metrics only
  quirework provenance metrics paste           # the paste audit (table + summary)
  quirework provenance metrics paste --show 8  # + dump the 8 largest flagged turns
  quirework provenance metrics paste --min 120 --ambig 250

"Manual confirmations" cannot be counted exactly: the transcript records the
permission *mode* per message (`default` vs `acceptEdits`) but no explicit
"user approved" event, and whether a call actually prompted also depends on the
machine-local allowlist (which is git-ignored and changes over time). So we
report a reproducible *proxy*: tool calls issued under `default` mode (a loose
upper bound on prompts) vs under `acceptEdits` (auto-approved edits). Flagged as
such, not as a true count.

NOTE FOR THE OPERATOR: this script is the *data-collection* half. The published
effort/contribution artifact is not its raw dump -- you run it, read the flagged
paste cases (`paste --show`), sanity-check the numbers, then write the report in
prose with the caveats kept. Follow the project's reporting procedure.
"""

import argparse, glob, json, re, subprocess

from quirework.provenance import scope as SCOPE
from quirework.provenance import codexfilter as CODEX_FLT
from datetime import datetime, timezone
from pathlib import Path

from quirework.project import current_project

WORDS = re.compile(r"\S+")


def _configured_relative(key):
    """A configured book path as the project names it."""
    configured = current_project().path(key)
    try:
        return str(configured.relative_to(current_project().root))
    except ValueError:
        return str(configured)


def _options():
    """Read policy from the selected project for this operation."""
    from types import SimpleNamespace

    OUTPUT_COUNTS = dict(
        current_project().data("provenance.metrics_policy", {}).get("output_counts", {})
    )
    SOURCE_OF_TRUTH = (
        current_project()
        .data("provenance.metrics_policy", {})
        .get("source_of_truth", None)
    )
    # The book configuration answers where the canon lives. Policy may narrow
    # the history paths when a project needs a different measurement boundary.
    CANONICAL_DIR = (
        current_project()
        .data("provenance.metrics_policy", {})
        .get("canonical_dir", _configured_relative("book.chapters"))
    )
    ESSAY_PATHS = (
        current_project()
        .data("provenance.metrics_policy", {})
        .get("essay_paths", [_configured_relative("book.source")])
    )
    return SimpleNamespace(
        CANONICAL_DIR=CANONICAL_DIR,
        ESSAY_PATHS=ESSAY_PATHS,
        OUTPUT_COUNTS=OUTPUT_COUNTS,
        SOURCE_OF_TRUTH=SOURCE_OF_TRUTH,
    )


def words(s):
    return len(WORDS.findall(s or ""))


class Acc:
    """One bucket of effort (a corpus, or the grand total)."""

    def __init__(self, name):
        self.name = name
        self.sessions = 0
        self.human_turns = 0
        self.ai_turns = 0
        self.human_chars = self.human_words = 0
        self.ai_text_chars = self.ai_text_words = 0
        self.think_chars = self.think_words = 0
        self.tool_calls = 0
        self.gated_tools = 0  # tool calls issued under permissionMode=default
        self.auto_tools = 0  # tool calls issued under acceptEdits/bypass
        self.think_blocks = 0
        self.models = {}  # model id -> assistant-turn count
        self.times = []  # all message timestamps (datetime, tz-aware)
        self.engaged = 0.0  # seconds, summed per-session first->last

    def human(self, text):
        self.human_turns += 1
        self.human_chars += len(text)
        self.human_words += words(text)

    def ai(self, text):
        self.ai_turns += 1
        self.ai_text_chars += len(text)
        self.ai_text_words += words(text)

    def think(self, text):
        self.think_blocks += 1
        self.think_chars += len(text)
        self.think_words += words(text)

    def model(self, mid):
        if mid:
            self.models[mid] = self.models.get(mid, 0) + 1

    def merge(self, o):
        for k in (
            "sessions",
            "human_turns",
            "ai_turns",
            "human_chars",
            "human_words",
            "ai_text_chars",
            "ai_text_words",
            "think_chars",
            "think_words",
            "tool_calls",
            "gated_tools",
            "auto_tools",
            "think_blocks",
            "engaged",
        ):
            setattr(self, k, getattr(self, k) + getattr(o, k))
        self.times += o.times
        for mid, n in o.models.items():
            self.models[mid] = self.models.get(mid, 0) + n


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


ACTIVE_GAP_CAP = 1800  # 30 min: a longer gap between messages counts as idle


def session_span(ts_list, acc):
    """Calendar timestamps feed the global span; active time is the sum of
    consecutive in-session gaps capped at ACTIVE_GAP_CAP, so reopening a thread
    days later doesn't book days of 'effort'."""
    ts = sorted(t for t in ts_list if t)
    if not ts:
        return
    acc.times += ts
    for a, b in zip(ts, ts[1:]):
        acc.engaged += min((b - a).total_seconds(), ACTIVE_GAP_CAP)


# ---- claude.ai *.raw.json -------------------------------------------------
def claude_web(acc):
    for fp in sorted(
        glob.glob(
            str(
                current_project().path(
                    "provenance.conversations", ".quirework/provenance/conversations"
                )
                / "*.raw.json"
            )
        )
    ):
        o = json.load(open(fp))
        acc.sessions += 1
        ts = []
        for m in o.get("chat_messages", []):
            t = parse_ts(m.get("created_at"))
            ts.append(t)
            blocks = m.get("content") or []
            if m.get("sender") == "human":
                txt = " ".join(
                    b.get("text", "") for b in blocks if b.get("type") == "text"
                ) or m.get("text", "")
                acc.human(txt)
            else:
                acc.model(m.get("model"))
                first_text = True
                for b in blocks:
                    bt = b.get("type")
                    if bt == "text":
                        if first_text:
                            acc.ai(b.get("text", ""))
                            first_text = False
                        else:
                            acc.ai_text_chars += len(b.get("text", ""))
                            acc.ai_text_words += words(b.get("text", ""))
                    elif bt == "thinking":
                        acc.think(b.get("thinking", "") or b.get("text", ""))
                    elif bt == "tool_use":
                        acc.tool_calls += 1
                if first_text:  # assistant turn with no text block
                    acc.ai_turns += 1
        session_span(ts, acc)


# ---- Claude Code *.jsonl ---------------------------------------------------
HARNESS = (
    "<command-name>",
    "<local-command",
    "Caveat:",
    "<command-message>",
    "[Request interrupted",
    "<system-reminder>",
)


def _in_scope(fp, scope, making):
    """Scope filter for a session path (see quirework.provenance.scope).

    Default is every scope: the effort ledger counts the whole corpus on
    purpose. Half this project was instruments, and a ledger that silently
    dropped that half understated the human's effort and misdescribed the
    shape of the work -- which is what deleting those sessions used to do.
    """
    if scope is None:
        return True
    return SCOPE.scope_of_path(fp, making) == scope


def claude_code(acc, scope=None, making=None):
    for fp in sorted(
        glob.glob(
            str(
                current_project().path(
                    "provenance.claude_sessions", ".quirework/provenance/cc-sessions"
                )
                / "*.jsonl"
            )
        )
    ):
        if not _in_scope(fp, scope, making):
            continue
        acc.sessions += 1
        ts = []
        # permissionMode is carried by `permission-mode` markers and user
        # records, NOT by the assistant records that hold tool_use. So track the
        # mode as running state and attribute each tool_use to the mode in force.
        mode = "default"
        for line in open(fp):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn line (the mirror can copy a live session mid-write)
            t = o.get("type")
            if "permissionMode" in o:
                mode = o["permissionMode"]
            if t not in ("user", "assistant"):
                continue
            if o.get("isMeta") or o.get("isSidechain"):
                continue
            ts.append(parse_ts(o.get("timestamp")))
            c = (o.get("message") or {}).get("content")
            if t == "user":
                if isinstance(c, str):
                    if not c.lstrip().startswith(HARNESS):
                        acc.human(c)
                # list-content user lines are tool_result echoes -> skip
            else:
                acc.model((o.get("message") or {}).get("model"))
                gated = mode == "default"
                if isinstance(c, list):
                    first_text = True
                    for b in c:
                        bt = b.get("type")
                        if bt == "text":
                            if first_text:
                                acc.ai(b.get("text", ""))
                                first_text = False
                            else:
                                acc.ai_text_chars += len(b.get("text", ""))
                                acc.ai_text_words += words(b.get("text", ""))
                        elif bt == "thinking":
                            acc.think(b.get("thinking", ""))
                        elif bt == "tool_use":
                            acc.tool_calls += 1
                            if gated:
                                acc.gated_tools += 1
                            else:
                                acc.auto_tools += 1
                elif isinstance(c, str):
                    acc.ai(c)
        session_span(ts, acc)


# ---- Antigravity (Gemini sessions) *.jsonl --------------------------------
_USER_REQ = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


def agy_sessions(acc, scope=None, making=None):
    for fp in sorted(
        glob.glob(
            str(
                current_project().path(
                    "provenance.agy_sessions", ".quirework/provenance/agy-sessions"
                )
                / "*.jsonl"
            )
        )
    ):
        if not _in_scope(fp, scope, making):
            continue
        acc.sessions += 1
        ts = []
        for line in open(fp):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts.append(parse_ts(o.get("created_at")))
            typ = o.get("type")
            if typ == "USER_INPUT":
                c = o.get("content") or ""
                m = _USER_REQ.search(c)
                acc.human(m.group(1) if m else c)
            elif typ == "PLANNER_RESPONSE":
                c = o.get("content")
                if isinstance(c, str) and c.strip():
                    acc.ai(c)
                for tc in o.get("tool_calls") or []:
                    acc.tool_calls += 1
                if o.get("thinking"):
                    acc.think(o["thinking"])
        session_span(ts, acc)


# ---- Codex rollout *.jsonl -------------------------------------------------
def codex_sessions(acc, scope=None, making=None):
    for fp in sorted(
        glob.glob(
            str(
                current_project().path(
                    "provenance.codex_sessions", ".quirework/provenance/codex-sessions"
                )
                / "*.jsonl"
            )
        )
    ):
        if not _in_scope(fp, scope, making):
            continue
        acc.sessions += 1
        ts = []
        for role, text, stamp in CODEX_FLT.records(fp):
            ts.append(parse_ts(stamp))
            if role == "human":
                acc.human(text)
            elif role == "assistant":
                acc.ai(text)
            # A subagent's `user` record is the parent agent's delegated task,
            # not another human prompt. It is intentionally absent here.
        for obj in CODEX_FLT._objects(fp):
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                continue
            if obj.get("type") == "turn_context" and payload.get("model"):
                acc.model(payload["model"])
            if obj.get("type") == "response_item" and payload.get("type") in (
                "custom_tool_call",
                "function_call",
                "action",
            ):
                acc.tool_calls += 1
        session_span(ts, acc)


# ---- Gemini rendered *.md -------------------------------------------------
def gemini(acc):
    hdr = re.compile(r"^##\s+(You|Gemini)\s+·")
    for fp in sorted(
        glob.glob(
            str(
                current_project().path(
                    "provenance.conversations", ".quirework/provenance/conversations"
                )
                / "gemini-*.md"
            )
        )
    ):
        acc.sessions += 1
        who = None
        buf = {"You": [], "Gemini": []}
        for line in open(fp):
            m = hdr.match(line)
            if m:
                who = m.group(1)
                continue
            if who:
                buf[who].append(line)
        for chunk in _segments(buf["You"]):
            acc.human(chunk)
        for chunk in _segments(buf["Gemini"]):
            acc.ai(chunk)


def _segments(lines):
    """Re-split a speaker's accumulated lines back into its individual turns is
    not possible without the headers we consumed; count it as one blob per file
    side. Good enough — Gemini is 2 turns/side and timestamp-less anyway."""
    text = "".join(lines).strip()
    return [text] if text else []


# ---- repo / git metrics ---------------------------------------------------


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=current_project().root, capture_output=True, text=True
    ).stdout


def _count(*args):
    out = _git(*args).strip()
    return len([x for x in out.splitlines() if x])


def git_metrics():
    g = {}
    g["commits_total"] = _count("rev-list", "--count", "HEAD") and int(
        _git("rev-list", "--count", "HEAD").strip() or 0
    )
    g["commits_sot"] = _count("log", "--format=%H", "--", *_options().ESSAY_PATHS)
    # net churn on the source of truth
    add = rem = 0
    for ln in _git(
        "log", "--numstat", "--format=", "--", *_options().ESSAY_PATHS
    ).splitlines():
        p = ln.split("\t")
        if len(p) == 3 and p[0].isdigit() and p[1].isdigit():
            add += int(p[0])
            rem += int(p[1])
    g["sot_added"], g["sot_removed"] = add, rem
    # repo calendar span
    first = _git("log", "--reverse", "--format=%ai").splitlines()
    last = _git("log", "-1", "--format=%ai").strip()
    g["repo_first"] = first[0][:10] if first else "?"
    g["repo_last"] = last[:10] if last else "?"
    return g


# ---- output / distillation -------------------------------------------------
def output_metrics():
    o = {}
    chapters = sorted(
        glob.glob(str(current_project().root / _options().CANONICAL_DIR / "*.qmd"))
    )
    if chapters:
        o["essay_words"] = sum(
            words(re.sub(r"<!--.*?-->", "", open(c).read(), flags=re.DOTALL))
            for c in chapters
        )
    else:
        # A book with no chapter files may still be a single document. Without
        # one declared there is simply nothing to count, which is a report of
        # zero rather than a reason to stop.
        named = _options().SOURCE_OF_TRUTH
        sot = current_project().root / named if named else None
        o["essay_words"] = words(sot.read_text()) if sot and sot.exists() else 0
    # What else a project made alongside the book is the project's own shape:
    # notes, exercises, reading rounds, a derived edition. The policy names
    # each count and the glob that finds it, because a fixed list of directory
    # names counts one book's tree and reports zero for every other, which
    # reads as "nothing was made" rather than "nothing was asked for". A
    # pattern ending in a slash counts directories.
    o["counts"] = {}
    for label, pattern in _options().OUTPUT_COUNTS.items():
        found = current_project().root.glob(str(pattern).rstrip("/"))
        o["counts"][label] = sum(
            1 for p in found if p.is_dir() or not str(pattern).endswith("/")
        )
    return o


# ---- paste / relay audit --------------------------------------------------
# Phrases that announce or carry text from elsewhere (a paste/relay tell).
RELAY = re.compile(r"""(?ix)
    (from \s+ (the \s+)? (other|another|that) \s+ session)
  | (the \s+ other \s+ session)
  | (another \s+ session)
  | (here'?s \s+ where \s+ i \s+ am)
  | (here'?s \s+ (the|what|some|my|a) )
  | (here \s+ (it|they) \s+ (is|are))
  | (here \s+ are \s+ (the|some|a|my))
  | (the \s+ response \s+ from)
  | ((sonnet|opus|gemini|the \s+ model)('?s)? \s+
        (take|response|reply|feedback|review|answer|version|notes?))
  | (i \s+ asked \s+ (the \s+)? (other|another|a) )
  | (feedback \s+ i \s+ (agree|got|received))
  | (review \s+ (from|by|i))
  | (what \s+ i \s+ wrote \s+ (there|in))
  | (notes \s+ of \s+ a \s+ specification)
""")
# After stripping leading whitespace and the backslash escapes the chat client
# inserts on paste (\>, \-, \---), does a line open like markdown structure?
_DEESC = re.compile(r"^[\s\\]+")
STRUCT_LINE = re.compile(r"""(?x)
      \#{1,6}\s            # heading
    | ([-*+]|\d+\.)\s      # bullet / ordered item
    | >                    # blockquote
    | \|.*\|               # table row
    | (```|~~~)            # code fence
    | (---+|\*\*\*+|___+)\s*$   # horizontal rule
""")


def _features(text):
    lines = text.splitlines()
    nonblank = [ln for ln in lines if ln.strip()]
    n = max(1, len(nonblank))
    struct_words = bq = 0
    for ln in nonblank:
        core = _DEESC.sub("", ln)
        if STRUCT_LINE.match(core):
            struct_words += words(ln)
            if core.startswith(">"):
                bq += 1
    return {
        "heads": sum(bool(re.match(r"[\s\\]*#{1,6}\s", ln)) for ln in lines),
        "bullets": sum(bool(re.match(r"[\s\\]*([-*+]|\d+\.)\s", ln)) for ln in lines),
        "fences": text.count("```"),
        "tables": sum(bool(re.match(r"[\s\\]*\|.*\|", ln)) for ln in lines),
        "bq_frac": bq / n,
        "relay": bool(RELAY.search(text)),
        "struct_words": struct_words,
    }


def _classify(w, f, ambig):
    """STRUCTURED / RELAY / AMBIGUOUS / TYPED for one human turn."""
    if (
        f["fences"] > 0
        or f["heads"] >= 3
        or f["tables"] >= 3
        or (f["bullets"] >= 10 and w >= 200)
    ):
        return "STRUCTURED"
    if (f["relay"] and w >= 120) or f["bq_frac"] >= 0.30:
        return "RELAY"
    if w >= ambig:
        return "AMBIGUOUS"
    return "TYPED"


def human_turns():
    """Every human turn as (source-tag, text), one entry per turn -- including
    per-turn Gemini 'You' segments (the metrics path lumps those into one blob)."""
    out = []
    for fp in sorted(
        glob.glob(
            str(
                current_project().path(
                    "provenance.conversations", ".quirework/provenance/conversations"
                )
                / "*.raw.json"
            )
        )
    ):
        o = json.load(open(fp))
        for m in o.get("chat_messages", []):
            if m.get("sender") == "human":
                blocks = m.get("content") or []
                txt = " ".join(
                    b.get("text", "") for b in blocks if b.get("type") == "text"
                ) or m.get("text", "")
                out.append(("web/" + Path(fp).name[:8], txt))
    for fp in sorted(
        glob.glob(
            str(
                current_project().path(
                    "provenance.claude_sessions", ".quirework/provenance/cc-sessions"
                )
                / "*.jsonl"
            )
        )
    ):
        for line in open(fp):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn line; every other corpus reader shrugs too
            if o.get("type") != "user" or o.get("isMeta") or o.get("isSidechain"):
                continue
            c = (o.get("message") or {}).get("content")
            if isinstance(c, str) and not c.lstrip().startswith(HARNESS):
                out.append(("cc/" + Path(fp).name[:8], c))
    for fp in sorted(
        glob.glob(
            str(
                current_project().path(
                    "provenance.agy_sessions", ".quirework/provenance/agy-sessions"
                )
                / "*.jsonl"
            )
        )
    ):
        for line in open(fp):
            try:
                o = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if o.get("type") != "USER_INPUT":
                continue
            c = o.get("content") or ""
            m = _USER_REQ.search(c)
            txt = m.group(1) if m else c
            if txt.strip() and not txt.lstrip().startswith(HARNESS):
                out.append(("agy/" + Path(fp).stem[:8], txt))
    for fp in sorted(
        glob.glob(
            str(
                current_project().path(
                    "provenance.codex_sessions", ".quirework/provenance/codex-sessions"
                )
                / "*.jsonl"
            )
        )
    ):
        for _role, txt, _stamp in CODEX_FLT.human_records(fp):
            if txt.strip() and not txt.lstrip().startswith(HARNESS):
                out.append(("codex/" + Path(fp).stem[:8], txt))
    hdr = re.compile(r"^##\s+(You|Gemini)\s+·")
    for fp in sorted(
        glob.glob(
            str(
                current_project().path(
                    "provenance.conversations", ".quirework/provenance/conversations"
                )
                / "gemini-*.md"
            )
        )
    ):
        who = None
        cur = []
        for line in open(fp):
            m = hdr.match(line)
            if m:
                if who == "You" and cur:
                    out.append(("gem/" + Path(fp).name[7:15], "".join(cur)))
                who = m.group(1)
                cur = []
            elif who == "You":
                cur.append(line)
        if who == "You" and cur:
            out.append(("gem/" + Path(fp).name[7:15], "".join(cur)))
    return out


def paste_stats(ambig=250):
    rows = []
    tot = {"STRUCTURED": 0, "RELAY": 0, "AMBIGUOUS": 0, "TYPED": 0}
    total_words = struct_words = 0
    for src, txt in human_turns():
        w = words(txt)
        total_words += w
        f = _features(txt)
        struct_words += f["struct_words"]
        cls = _classify(w, f, ambig)
        tot[cls] += w
        rows.append((w, src, cls, f, txt))
    rows.sort(reverse=True, key=lambda r: r[0])
    paste_pt = tot["STRUCTURED"] + tot["RELAY"]  # point estimate
    return {
        "rows": rows,
        "tot": tot,
        "total_words": total_words,
        "struct_words": struct_words,
        "paste_lo": tot["STRUCTURED"],  # confident floor
        "paste_pt": paste_pt,
        "paste_hi": paste_pt + tot["AMBIGUOUS"],  # err-toward-counting
        "margin": tot["AMBIGUOUS"],  # genuinely uncertain band
        "typed_pt": total_words - paste_pt,
    }


def paste_report(st, min_words=150, show=0):
    print("=" * 78)
    print("HUMAN-PASTE AUDIT  (large human turns; is the volume typed or pasted?)")
    print("=" * 78)
    print(
        f"{'words':>6} {'source':12} {'class':10} {'h':>3}{'bul':>4}{'fen':>4}"
        f"{'tbl':>4}{'bq%':>5}{'rel':>4}  opening"
    )
    print("-" * 78)
    for w, src, cls, f, txt in st["rows"]:
        if w < min_words:
            continue
        opening = re.sub(r"\s+", " ", txt.strip())[:30]
        print(
            f"{w:6} {src:12} {cls:10} {f['heads']:>3}{f['bullets']:>4}"
            f"{f['fences']:>4}{f['tables']:>4}{int(f['bq_frac'] * 100):>4}%"
            f"{('Y' if f['relay'] else '-'):>4}  {opening}"
        )
    tot, tw = st["tot"], st["total_words"]
    print("\n" + "-" * 78)
    print(f"total human words: {tw:,}")
    print("\nTurn-level classification (whole turns, by dominant signal):")
    print(f"  STRUCTURED (formatted-doc paste)      : {tot['STRUCTURED']:>8,} w")
    print(f"  RELAY      (relayed / quoted text)    : {tot['RELAY']:>8,} w")
    print(f"  AMBIGUOUS  (long plain prose, unsure) : {tot['AMBIGUOUS']:>8,} w")
    print(f"  TYPED      (ordinary prose)           : {tot['TYPED']:>8,} w")
    print(
        f"\nLine-level structural/quoted volume   : {st['struct_words']:>8,} w  "
        f"({100 * st['struct_words'] / tw:.0f}% of human words)"
    )
    print("  (headings, bullets, blockquotes, tables, fences, rules -- the concrete")
    print("   'well-formatted/quoted markdown' floor; catches quotes in mixed turns.)")
    print(
        f"\nEstimated PASTED/RELAYED words:  {st['paste_pt']:,}  "
        f"(range {st['paste_lo']:,}–{st['paste_hi']:,};  "
        f"error margin ±{st['margin']:,} w = AMBIGUOUS)"
    )
    lo_t, hi_t = tw - st["paste_hi"], tw - st["paste_lo"]
    print(
        f"Estimated TYPED human authoring: ~{st['typed_pt']:,} w  "
        f"(range {lo_t:,}–{hi_t:,}; {100 * lo_t / tw:.0f}–{100 * hi_t / tw:.0f}% of raw)"
    )
    print("\nCaveats: signals, not ground truth, so we err toward flagging paste and")
    print("report the band. Known soft spots found in review: (a) RELAY counts whole")
    print("mixed turns as paste even when the human interleaves real typed comments")
    print(
        "between quotes, so the point estimate slightly OVER-counts paste; (b) plain-"
    )
    print(
        "prose pastes (a draft essay pasted with no markdown) only trip the AMBIGUOUS"
    )
    print("or RELAY-phrase nets, never the line-level one -- so the line-level is a")
    print("floor. Large plain-prose pastes may remain in the ambiguous band.")
    if show:
        print(
            "\n" + "=" * 78 + "\nLARGEST FLAGGED TURNS (for manual review)\n" + "=" * 78
        )
        shown = 0
        for w, src, cls, f, txt in st["rows"]:
            if cls == "TYPED":
                continue
            print(
                f"\n----- {src}  {w}w  [{cls}]  heads={f['heads']} "
                f"bullets={f['bullets']} fences={f['fences']} tables={f['tables']}"
                f" bq={int(f['bq_frac'] * 100)}% relay={'Y' if f['relay'] else 'N'} -----"
            )
            print(re.sub(r"\n{3,}", "\n\n", txt.strip())[:1400])
            shown += 1
            if shown >= show:
                break


def fmt_dur(sec):
    h = sec / 3600
    if h < 48:
        return f"{h:.1f} h"
    return f"{h / 24:.1f} d ({h:.0f} h)"


def report(accs, total, pst=None, scope=None):
    span = ""
    ts = [t for t in total.times if t]
    if ts:
        lo, hi = min(ts), max(ts)
        days = (hi - lo).days
        span = f"{lo.date()} -> {hi.date()}  ({days} days, {days / 30.4:.1f} months)"

    print("=" * 70)
    print("PROVENANCE EFFORT METRICS")
    print("=" * 70)
    bd = scope_breakdown()
    print(f"Corpus: the configured curated corpus, scope={scope or 'all'}.")
    print(
        f"  book={bd.get(SCOPE.BOOK, 0)} session(s) (the book's ideas and prose) · "
        f"making={bd.get(SCOPE.MAKING, 0)} (the apparatus: tools, guards, process)."
    )
    print("  Both halves are counted here by default. Scope decides which blind")
    print("  cold-read reads a session, never whether it is real effort; filter with")
    print("  `--scope book|making` to attribute the halves separately.")
    print(
        "Still a FLOOR, not the whole: stubs, aborted starts and synthetic experiment"
    )
    print("runs are curated out entirely -- see removed-sessions.txt.")
    print()
    print(f"Calendar span (timestamped sources): {span}")
    print("  (Gemini reviews carry no timestamps; author-dated 2026-04-04/05.)")
    print()
    hdr = (
        f"{'':22}{'web':>10}{'code':>10}{'agy':>10}{'codex':>10}"
        f"{'gemini':>10}{'TOTAL':>11}"
    )
    print(hdr)
    print("-" * len(hdr))

    def row(label, fn):
        cells = "".join(f"{fn(a):>10,}" for a in accs)
        print(f"{label:22}{cells}{fn(total):>11,}")

    row("sessions", lambda a: a.sessions)
    row("human prompts", lambda a: a.human_turns)
    row("AI responses", lambda a: a.ai_turns)
    row("thinking blocks", lambda a: a.think_blocks)
    row("tool calls", lambda a: a.tool_calls)
    if total.human_turns:
        print(f"\n  human prompts / session = {total.human_turns / total.sessions:.1f}")
    print()
    row("human words", lambda a: a.human_words)
    row("AI words (resp)", lambda a: a.ai_text_words)
    row("AI words (think)", lambda a: a.think_words)
    print()
    print("Text volume (chars) and token estimate (chars/4):")
    h = total.human_chars
    a_resp = total.ai_text_chars
    a_think = total.think_chars
    ai = a_resp + a_think
    print(f"  human authored : {h:>12,} chars  ~{h // 4:>10,} tok")
    print(f"  AI responses   : {a_resp:>12,} chars  ~{a_resp // 4:>10,} tok")
    print(f"  AI thinking    : {a_think:>12,} chars  ~{a_think // 4:>10,} tok")
    print(f"  AI total       : {ai:>12,} chars  ~{ai // 4:>10,} tok")
    print(f"  grand total    : {h + ai:>12,} chars  ~{(h + ai) // 4:>10,} tok")
    print("  (Authored-text tokens only. ACTUAL compute/billed tokens are far")
    print("   larger: every turn resends the growing context window, so a long")
    print("   session bills the transcript-so-far many times over.)")
    aw = total.ai_text_words + total.think_words
    if h:
        print(
            f"\n  AI:human authored-word ratio (raw)            = "
            f"{aw / total.human_words:.1f} : 1"
        )
        if pst and pst["typed_pt"]:
            print(
                f"  AI:human ratio vs TYPED human words           = "
                f"{aw / pst['typed_pt']:.0f} : 1   (~{pst['paste_pt']:,} of "
                f"{pst['total_words']:,} human words are pasted/relayed; see"
                f" `paste`)"
            )
    print(
        f"\nActive engagement (consecutive-message gaps, capped at 30m): "
        f"{fmt_dur(total.engaged)}"
    )
    for a in accs:
        print(f"    {a.name:8}: {fmt_dur(a.engaged)}")
    print(
        "  (A keyboard-time floor: idle gaps >30m are dropped, so a thread"
        " reopened days later doesn't book days of effort.)"
    )

    # ---- manual-confirmation proxy (Claude Code only) ----
    code = next((a for a in accs if a.name == "code"), None)
    if code:
        print("\nManual-confirmation proxy (Claude Code tool calls, by mode):")
        print(
            f"  gated  (permissionMode=default, ~upper bound on prompts): "
            f"{code.gated_tools:,}"
        )
        print(
            f"  auto   (acceptEdits, not prompted)                      : "
            f"{code.auto_tools:,}"
        )
        print(
            "  (Not an exact confirmation count: no 'approved' event is logged"
            " and the allowlist is machine-local. Lower bound on auto, loose"
            " upper bound on prompts.)"
        )

    # ---- models ----
    # See the MODEL METADATA IS NOT TRUSTWORTHY block in the module docstring.
    # The printed warning below is deliberately loud: this table is the one part
    # of the report a reader is most tempted to quote as attribution, and it is
    # the part least able to bear it.
    if total.models:
        print("\nModels (cc-era assistant message-records by model id):")
        for mid, n in sorted(total.models.items(), key=lambda x: -x[1]):
            print(f"    {mid:24} {n:>6,}")
        print(
            "  (Raw record count, not logical turns: one turn spans several"
            " text/thinking/tool records.)"
        )
        print(
            "  *** DO NOT USE THIS TABLE FOR ATTRIBUTION. Model metadata is"
            " unreliable in every source: ***"
        )
        print(
            "    - Web era is omitted entirely. The claude.ai export stores one"
            " conversation-level last-selected model, and merely OPENING an old"
            " thread with a newer model RE-STAMPS it -- a Sonnet-era session has"
            " come back tagged with a model that did not exist when it ran."
        )
        print(
            "    - The cc-era ids above are ALSO unreliable. A safety guardrail"
            " can flag a turn mid-session and reload the whole context into"
            " another model; the client switches, but the original model can"
            " persist in the metadata. The id may name the model SELECTED, not"
            " the one that SERVED."
        )
        print(
            "    - The model cannot be asked either: an assistant's own context"
            " can assert the wrong model, and it has no way to check."
        )
        print(
            "    Read this as a rough sense of the mix. Where the model actually"
            " matters, take it from the human or the harness, and say so."
        )


def report_repo(g, o, total):
    print("\n" + "=" * 70)
    print("REPO METRICS (git) + OUTPUT")
    print("=" * 70)
    print(
        f"Repo history span : {g['repo_first']} -> {g['repo_last']}  "
        "(this repository's own history, which may start long after the "
        "writing did)"
    )
    print(f"Commits, repo total                : {g['commits_total']:,}")
    essay = ", ".join(str(path) for path in _options().ESSAY_PATHS)
    print(f"Commits touching the book ({essay}): {g['commits_sot']:,}")
    print(
        f"Source-of-truth churn              : +{g['sot_added']:,} / "
        f"-{g['sot_removed']:,} lines (semantic-line-wrapped, ~1 sentence/line)"
    )
    print()
    print(f"Final essay length                 : {o['essay_words']:,} words")
    for label, count in o["counts"].items():
        print(f"{label:<35}: {count}")
    aw = total.ai_text_words + total.think_words
    if o["essay_words"]:
        print(
            f"\nDistillation ratio (all authored words : final essay words) = "
            f"{(total.human_words + aw) / o['essay_words']:.0f} : 1"
        )
        print(
            "  (How much exploration compressed into the artifact; conversation"
            " words in, essay words out.)"
        )


def run_metrics(pst, scope=None):
    making = SCOPE.making_ids()
    web, code, agy, codex, gem = (
        Acc("web"),
        Acc("code"),
        Acc("agy"),
        Acc("codex"),
        Acc("gemini"),
    )
    claude_web(web)
    claude_code(code, scope, making)
    agy_sessions(agy, scope, making)
    codex_sessions(codex, scope, making)
    gemini(gem)
    total = Acc("TOTAL")
    for a in (web, code, agy, codex, gem):
        total.merge(a)
    report((web, code, agy, codex, gem), total, pst, scope)
    report_repo(git_metrics(), output_metrics(), total)


def scope_breakdown():
    """Session counts per scope, for the header line."""
    making = SCOPE.making_ids()
    out = {}
    for s in SCOPE.SCOPES:
        n = 0
        for sub in ("claude_sessions", "agy_sessions", "codex_sessions"):
            n += sum(
                1
                for fp in glob.glob(
                    str(current_project().path("provenance." + sub) / "*.jsonl")
                )
                if SCOPE.scope_of_path(fp, making) == s
            )
        out[s] = n
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="quirework provenance metrics",
        description="Provenance effort/contribution metrics.",
    )
    sub = ap.add_subparsers(dest="cmd")
    ap.add_argument(
        "--scope",
        choices=(*SCOPE.SCOPES, "all"),
        default="all",
        help="corpus half to count: book, making, or all (default)",
    )
    sub.add_parser("metrics", help="conversation + repo metrics (default)")
    sub.add_parser("all", help="metrics + the full paste audit")
    p = sub.add_parser("paste", help="paste/relay audit of human words")
    p.add_argument(
        "--min",
        type=int,
        default=150,
        help="list turns >= this many words (default 150)",
    )
    p.add_argument(
        "--ambig",
        type=int,
        default=250,
        help="plain-prose turns >= this are AMBIGUOUS (default 250)",
    )
    p.add_argument(
        "--show",
        type=int,
        default=0,
        help="dump full text of the N largest flagged turns",
    )
    args = ap.parse_args(argv)

    if args.cmd == "paste":
        paste_report(paste_stats(args.ambig), min_words=args.min, show=args.show)
        return

    pst = paste_stats()  # for the corrected ratio
    run_metrics(pst, None if args.scope == "all" else args.scope)
    if args.cmd == "all":
        print("\n")
        paste_report(pst)


if __name__ == "__main__":
    main()
