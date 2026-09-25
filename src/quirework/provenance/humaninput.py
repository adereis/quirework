#!/usr/bin/env python3
"""Compile the human's *own verbatim words* from the whole provenance corpus.

Purpose is evidentiary, not analytic. The published work is human-directed and
AI-articulated; for the prose to be cleanly copyrightable the human-authored
contribution has to be documentable in the human's *literal language*. This
walks every source transcript and extracts every human turn, verbatim, tagged
with where and when it was said and whether it reads as typed-original or
pasted/relayed -- a raw compilation the human then rewrites, by hand, into the
copyrighted source-of-truth file. (That hand-rewrite is deliberately NOT done
here: an AI-generated "restatement" would defeat the very authorship claim the
compilation exists to support.)

It reuses the same parsing/cleaning substrate as the rest of the toolchain:
  - claude.ai web dialogues   : conversations/*.raw.json   (timestamps)
  - local Claude Code sessions: cc-sessions/*.jsonl        (timestamps)
  - local Codex sessions      : codex-sessions/*.jsonl     (timestamps)
  - cross-model Gemini reviews: conversations/gemini-*.md  (turn ordinals only)

Two design points worth noting:
  * harness noise (system-reminders, slash-command plumbing, shell I/O, a
    finished subagent's report) is stripped with ccfilter.strip_wrappers, and
    the records the harness flags as its own are skipped with
    ccfilter.harness_flagged -- the SAME rules the cold-read uses -- so what
    survives is conversational prose, not bookkeeping;
  * each surviving turn is classified by its *authored residual* (the words the
    human composed, excluding quoted/fenced material) into TYPED / REVIEW /
    AMBIGUOUS / RELAY / STRUCTURED / TRIVIAL. This is purpose-built and the
    INVERSE of the effort audit's bias: where metrics errs toward flagging paste
    (the right call for "how much was typed"), this errs toward capturing the
    human's words (the right call for "what are the human's own words"). It
    matters most for course-corrections: a review that quotes the passage it
    argues against is overwhelmingly authored, and is kept (REVIEW), not demoted.

UNLIKE the cold-read, the text is NOT contamination-redacted: the point is the
human's exact words, and the output stays inside the private corpus / ~/tmp.

Subcommands:
    compile   (default) write the verbatim compilation + a stats header
    stats     print the summary counts only (no file written)

Flags (compile):
    --out PATH      where to write (default the project cache human-input/compilation.md)
    --typed-only    keep the authored set: TYPED + REVIEW + AMBIGUOUS (drop paste/trivial)
    --all           include TRIVIAL turns (acks, slash-commands, one-liners)
    --min N         drop turns under N words (default 0; TRIVIAL handles acks)

NOTE FOR THE OPERATOR: this is the data-collection half. The deliverable is the
human's hand-authored file, not this dump. Follow the project's procedure.
"""

import argparse
import glob
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from quirework.project import current_project
from quirework.provenance import ccfilter as FLT
from quirework.provenance import codexfilter as CODEX_FLT
from quirework.provenance.metrics import words
from quirework.provenance.metrics import parse_ts
from quirework.provenance.metrics import _features
from quirework.provenance.metrics import HARNESS

# Bump when what counts as one of the human's turns changes, so that a cache
# built from these turns knows its windows are not the ones it would cut now.
# 2: a background subagent's report and a compacted session's summary are no
#    longer the human's turns.
EXTRACTION_VERSION = 2

# A turn this short (or a bare slash-command / one-word ack) is procedural, not
# authored thought: "yes", "go on", "/clear", "perfect, commit it". Marked
# TRIVIAL and dropped by default (re-include with --all).
TRIVIAL_MAX_WORDS = 4
_ACK = re.compile(
    r"""(?ix)^\s*(
        (yes|yep|yeah|no|nope|ok|okay|sure|thanks|thank\s+you|perfect|great|
         nice|good|cool|right|correct|exactly|agreed|continue|go\s+on|go\s+ahead|
         proceed|do\s+it|please|done|next|stop|wait)
        [\s.!,]*
    )+$"""
)


def _is_trivial(text, w):
    s = text.strip()
    if s.startswith("/"):  # slash-command line
        return True
    if w <= TRIVIAL_MAX_WORDS:
        return True
    return bool(_ACK.match(s))


# Words inside blockquote lines or fenced code blocks are quoted/relayed -- not
# composed in this turn. The rest is the human's *authored residual*, which is
# what the copyright question turns on: a course-correction that quotes the very
# passage it argues against is overwhelmingly authored, even though it contains a
# quote. (The effort audit's classifier instead errs toward flagging such a turn
# as paste -- the right bias for "how much was typed", the wrong one for "what
# are the human's own words". So this classifier is purpose-built, not inherited.)
_DEESC = re.compile(r"^[\s\\]+")
AMBIG_WORDS = 250


def _split_authored(text):
    """``(authored_words, quoted_words)``; quoted = blockquote lines + fenced blocks."""
    quoted = 0
    in_fence = False
    for ln in text.splitlines():
        core = _DEESC.sub("", ln)
        if core.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or core.startswith(">"):
            quoted += words(ln)
    return max(0, words(text) - quoted), quoted


def _is_document(w, f):
    """Shaped like a pasted/handoff *document* rather than conversational prose.

    Structure alone cannot tell an authored doc from a pasted one, so a
    document-shaped turn is never part of the confident typed core -- it is
    surfaced (STRUCTURED) for the human to claim or discard by hand.
    """
    return (
        f["fences"] > 0
        or f["heads"] >= 3
        or f["tables"] >= 3
        or (f["bullets"] >= 10 and w >= 200)
    )


def _classify_turn(text):
    """Return ``(class, words, authored_words)``.

    Keys on the authored residual and errs toward CAPTURING the human's words:
      TRIVIAL    acks / one-liners / slash-commands
      STRUCTURED document-shaped (could be authored or pasted -- review)
      RELAY      ferried quotes dominate (authored residual < half the turn)
      REVIEW     the human's own commentary wrapped around a quote (course-correction)
      AMBIGUOUS  long plain authored prose, no quoting (very likely typed)
      TYPED      ordinary authored prose
    """
    w = words(text)
    authored, quoted = _split_authored(text)
    if _is_trivial(text, w):
        return "TRIVIAL", w, authored
    f = _features(text)
    if _is_document(w, f):
        return "STRUCTURED", w, authored
    if w and authored / w < 0.5:
        return "RELAY", w, authored
    if quoted > 0:
        return "REVIEW", w, authored
    if w >= AMBIG_WORDS:
        return "AMBIGUOUS", w, authored
    return "TYPED", w, authored


def classify(text):
    """The class of one of the human's turns, as ``_classify_turn`` names it."""
    return _classify_turn(text)[0]


def _clean(text):
    """Strip harness wrappers; return prose, or '' if nothing authored survives."""
    cleaned = FLT.strip_wrappers(text or "")
    if not cleaned or cleaned.startswith(HARNESS):
        return ""
    return cleaned


# --------------------------------------------------------------------------- #
# extraction -> a flat list of turn records, one dict per human turn
#   {ts, source, sid, file, ordinal, text, cls, words}
# --------------------------------------------------------------------------- #
def _web_turns():
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
        sid = Path(fp).stem[:8]
        o = json.load(open(fp))
        n = 0
        for m in o.get("chat_messages", []):
            if m.get("sender") != "human":
                continue
            blocks = m.get("content") or []
            txt = " ".join(
                b.get("text", "") for b in blocks if b.get("type") == "text"
            ) or m.get("text", "")
            txt = _clean(txt)
            if not txt:
                continue
            n += 1
            cls, w, authored = _classify_turn(txt)
            out.append(
                {
                    "ts": parse_ts(m.get("created_at")),
                    "source": "web",
                    "sid": sid,
                    "file": str(Path(fp).name),
                    "ordinal": n,
                    "text": txt,
                    "cls": cls,
                    "words": w,
                    "authored": authored,
                }
            )
    return out


def _cc_turns():
    out = []
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
        sid = Path(fp).stem[:8]
        n = 0
        for line in open(fp):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                o.get("type") != "user"
                or FLT.harness_flagged(o)
                or o.get("isSidechain")
            ):
                continue
            c = (o.get("message") or {}).get("content")
            if not isinstance(c, str):  # list content = tool_result echoes
                continue
            txt = _clean(c)
            if not txt:
                continue
            n += 1
            cls, w, authored = _classify_turn(txt)
            out.append(
                {
                    "ts": parse_ts(o.get("timestamp")),
                    "source": "cc",
                    "sid": sid,
                    "file": str(Path(fp).name),
                    "ordinal": n,
                    "text": txt,
                    "cls": cls,
                    "words": w,
                    "authored": authored,
                }
            )
    return out


def _gemini_turns():
    hdr = re.compile(r"^##\s+(You|Gemini)\s+·")
    out = []
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
        sid = Path(fp).stem[7:15]
        who, cur, n = None, [], 0

        def flush():
            nonlocal cur, n
            txt = _clean("".join(cur))
            if txt:
                n_local = n
                cls, w, authored = _classify_turn(txt)
                out.append(
                    {
                        "ts": None,
                        "source": "gemini",
                        "sid": sid,
                        "file": str(Path(fp).name),
                        "ordinal": n_local,
                        "text": txt,
                        "cls": cls,
                        "words": w,
                        "authored": authored,
                    }
                )
            cur = []

        for line in open(fp):
            m = hdr.match(line)
            if m:
                if who == "You":
                    n += 1
                    flush()
                who = m.group(1)
                cur = []
            elif who == "You":
                cur.append(line)
        if who == "You":
            n += 1
            flush()
    return out


def _agy_turns():
    _ur = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)
    out = []
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
        sid = Path(fp).stem[:8]
        n = 0
        for line in open(fp):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("type") != "USER_INPUT":
                continue
            c = o.get("content") or ""
            m = _ur.search(c)
            txt = _clean(m.group(1) if m else c)
            if not txt:
                continue
            n += 1
            cls, w, authored = _classify_turn(txt)
            out.append(
                {
                    "ts": parse_ts(o.get("created_at")),
                    "source": "agy",
                    "sid": sid,
                    "file": str(Path(fp).name),
                    "ordinal": n,
                    "text": txt,
                    "cls": cls,
                    "words": w,
                    "authored": authored,
                }
            )
    return out


def _codex_turns():
    out = []
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
        sid = Path(fp).stem[:8]
        for n, (_role, raw, stamp) in enumerate(CODEX_FLT.human_records(fp), 1):
            txt = _clean(raw)
            if not txt:
                continue
            cls, w, authored = _classify_turn(txt)
            out.append(
                {
                    "ts": parse_ts(stamp),
                    "source": "codex",
                    "sid": sid,
                    "file": Path(fp).name,
                    "ordinal": n,
                    "text": txt,
                    "cls": cls,
                    "words": w,
                    "authored": authored,
                }
            )
    return out


def all_turns():
    return _web_turns() + _cc_turns() + _agy_turns() + _codex_turns() + _gemini_turns()


# --------------------------------------------------------------------------- #
# grouping + ordering
# --------------------------------------------------------------------------- #
SOURCE_LABEL = {
    "web": "claude.ai web dialogue",
    "cc": "Claude Code session",
    "agy": "Antigravity (Gemini) session",
    "codex": "Codex session",
    "gemini": "Gemini cross-model review",
}


def _sessions(turns):
    """Group turns by (source, file); order sessions by earliest timestamp,
    undated (Gemini) sessions last in filename order."""
    groups = {}
    for t in turns:
        groups.setdefault((t["source"], t["file"]), []).append(t)
    sess = []
    for (source, file), ts_list in groups.items():
        stamps = [t["ts"] for t in ts_list if t["ts"]]
        start = min(stamps) if stamps else None
        sess.append(
            {
                "source": source,
                "file": file,
                "sid": ts_list[0]["sid"],
                "start": start,
                "turns": ts_list,
            }
        )
    # dated first (chronological), then undated by filename
    sess.sort(
        key=lambda s: (
            s["start"] is None,
            s["start"] or datetime.max.replace(tzinfo=timezone.utc),
            s["file"],
        )
    )
    return sess


# --------------------------------------------------------------------------- #
# stats
# --------------------------------------------------------------------------- #
CLASSES = ["TYPED", "REVIEW", "AMBIGUOUS", "RELAY", "STRUCTURED", "TRIVIAL"]
# Predominantly the human's own composed words -- the authored set kept by
# --typed-only and counted in the authoring band.
AUTHORED_CLASSES = ("TYPED", "REVIEW", "AMBIGUOUS")


def _summary(turns):
    by_cls = {c: {"turns": 0, "words": 0, "authored": 0} for c in CLASSES}
    by_src = {}
    for t in turns:
        by_cls[t["cls"]]["turns"] += 1
        by_cls[t["cls"]]["words"] += t["words"]
        by_cls[t["cls"]]["authored"] += t["authored"]
        s = by_src.setdefault(t["source"], {"turns": 0, "words": 0})
        s["turns"] += 1
        s["words"] += t["words"]
    return by_cls, by_src


def _stats_lines(turns, sess):
    by_cls, by_src = _summary(turns)
    tot_turns = len(turns)
    tot_words = sum(t["words"] for t in turns)
    # The copyright-relevant figure is *authored words* (turn words minus quoted
    # lines/fenced blocks). The confident floor is the authored words in TYPED +
    # REVIEW (plain prose + course-corrections that quote the doc); AMBIGUOUS adds
    # the long-plain-prose turns that are very likely typed but not provable.
    floor = by_cls["TYPED"]["authored"] + by_cls["REVIEW"]["authored"]
    band = floor + by_cls["AMBIGUOUS"]["authored"]
    L = []
    L.append(f"Sessions               : {len(sess)}")
    L.append(f"Human turns (total)    : {tot_turns:,}")
    L.append(f"Human words (total)    : {tot_words:,}")
    L.append("")
    L.append("By source:")
    for src in ("web", "cc", "agy", "codex", "gemini"):
        if src in by_src:
            d = by_src[src]
            L.append(
                f"  {SOURCE_LABEL[src]:28} {d['turns']:>5} turns  {d['words']:>8,} words"
            )
    L.append("")
    L.append("By classification (turns / words / authored words):")
    for c in CLASSES:
        d = by_cls[c]
        L.append(
            f"  {c:11} {d['turns']:>5} turns  {d['words']:>8,} w  {d['authored']:>8,} authored"
        )
    L.append("")
    L.append(f"Original-authoring band (authored words): {floor:,}–{band:,}")
    L.append("  (Floor = TYPED + REVIEW authored words: ordinary prose plus the")
    L.append(
        "   course-corrections/reviews that quote the doc. +AMBIGUOUS = long plain"
    )
    L.append("   prose, very likely typed. RELAY = ferried quotes dominate the turn;")
    L.append(
        "   STRUCTURED = document-shaped (authored OR pasted -- the human claims or"
    )
    L.append(
        "   discards those by hand). 'authored' excludes quoted lines and fences.)"
    )
    return L


# --------------------------------------------------------------------------- #
# compilation document
# --------------------------------------------------------------------------- #
def _fmt_ts(ts):
    return ts.strftime("%Y-%m-%d %H:%M UTC") if ts else "undated"


def _fmt_date(ts):
    return ts.strftime("%Y-%m-%d") if ts else "undated"


def build_document(turns, sess):
    out = []
    out.append("# Original human input — verbatim compilation")
    out.append("")
    out.append(
        "> **PRIVATE / never-public.** Generated by `quirework provenance input` from the raw"
    )
    out.append(
        "> provenance corpus. This is firehose-adjacent (verbatim human prompts) and"
    )
    out.append("> stays inside the private workspace. Do not promote it.")
    out.append("")
    out.append(
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    out.append("")
    out.append("## What this is")
    out.append("")
    out.append(
        "Every turn the human typed/sent across the whole corpus, verbatim, with"
    )
    out.append(
        "provenance (source, session, timestamp) and a classification of how original"
    )
    out.append(
        "the wording is. Harness noise (system-reminders, slash-command plumbing,"
    )
    out.append(
        "shell output) is stripped; the human's prose is otherwise untouched and"
    )
    out.append(
        "**unredacted**. It is the evidence base for the human-authored contribution"
    )
    out.append(
        "to a human-directed, AI-articulated work — raw material for a hand-written,"
    )
    out.append(
        "copyrighted source-of-truth file. That rewrite is done **by hand, by the"
    )
    out.append(
        "human**: an AI restatement would defeat the authorship claim this supports."
    )
    out.append("")
    out.append(
        "Each turn shows its word count and, when they differ, its **authored** word"
    )
    out.append(
        "count — the words the human actually composed here, excluding lines they"
    )
    out.append(
        "quoted (blockquotes) or pasted in fenced blocks. That residual is what the"
    )
    out.append(
        "copyright question turns on, so the classifier keys on it and errs toward"
    )
    out.append("**capturing** the human's words, not toward flagging paste.")
    out.append("")
    out.append("### Classification legend")
    out.append("")
    out.append(
        "- **TYPED** — ordinary prose composed at the keyboard. Strongest evidence of original authorship."
    )
    out.append(
        "- **REVIEW** — the human's own commentary/course-correction wrapped around a quoted passage (they quote the doc, then argue). Authored, and load-bearing."
    )
    out.append(
        "- **AMBIGUOUS** — long plain prose, very likely typed but not provably so."
    )
    out.append(
        "- **RELAY** — ferried/quoted text dominates the turn (authored residual under half). The human's own framing may still be in it; review."
    )
    out.append(
        "- **STRUCTURED** — document-shaped (headings/fences/tables/long lists): a handoff or draft. Could be the human's writing or a paste — claim or discard by hand."
    )
    out.append(
        "- **TRIVIAL** — acknowledgments, one-liners, slash-commands. Not authored thought (shown only with `--all`)."
    )
    out.append("")
    out.append(
        "`--typed-only` keeps TYPED + REVIEW + AMBIGUOUS (the predominantly-authored"
    )
    out.append(
        "set). Signals, not ground truth: a RELAY/STRUCTURED tag is a prompt to look,"
    )
    out.append("not a verdict that it isn't yours.")
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append("```")
    out += _stats_lines(turns, sess)
    out.append("```")
    out.append("")
    out.append("## Session index")
    out.append("")
    out.append("| # | date | source | session | turns |")
    out.append("| --- | --- | --- | --- | --- |")
    for i, s in enumerate(sess, 1):
        out.append(
            f"| {i} | {_fmt_date(s['start'])} | {SOURCE_LABEL[s['source']]} "
            f"| `{s['sid']}` | {len(s['turns'])} |"
        )
    out.append("")
    out.append("---")
    out.append("")
    for i, s in enumerate(sess, 1):
        out.append(
            f"## {i}. {_fmt_date(s['start'])} · {SOURCE_LABEL[s['source']]} · `{s['sid']}`"
        )
        out.append("")
        out.append(f"<sub>source: `{s['file']}`</sub>")
        out.append("")
        for t in s["turns"]:
            wc = f"{t['words']}w"
            if t["authored"] < t["words"]:
                wc += f" ({t['authored']} authored)"
            out.append(
                f"#### `[{t['source']}/{t['sid']}#{t['ordinal']}]` · "
                f"{_fmt_ts(t['ts'])} · **{t['cls']}** · {wc}"
            )
            out.append("")
            out.append(t["text"].rstrip())
            out.append("")
        out.append("---")
        out.append("")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def _default_out():
    """Where the compilation lands when the project does not choose.

    Deliberately not the project cache. The compilation is every verbatim
    human prompt in the corpus, it is rebuilt from the corpus in seconds, and
    it is read once and handed on; nothing collects `~/.cache`, so leaving it
    there keeps a copy indefinitely for no gain. A project that wants it kept
    sets `provenance.human_input` at a path it controls.
    """
    configured = current_project().get("provenance.human_input")
    if configured is not None:
        return current_project().path("provenance.human_input")
    return current_project().scratch_dir("human-input") / "compilation.md"


def _filter(turns, typed_only, include_trivial, min_words):
    keep = []
    for t in turns:
        if t["cls"] == "TRIVIAL" and not include_trivial:
            continue
        if typed_only and t["cls"] not in AUTHORED_CLASSES:
            continue
        if t["words"] < min_words:
            continue
        keep.append(t)
    return keep


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="quirework provenance input",
        description="Compile the human's verbatim words from the provenance corpus.",
    )
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("stats", help="print summary counts only (no file written)")
    c = sub.add_parser("compile", help="write the verbatim compilation (default)")
    for p in (c,):
        # The default is shown tilde-form, not expanded: help text is read in a
        # shared repo and should not carry one machine's home directory.
        p.add_argument(
            "--out",
            type=Path,
            default=None,
            help="write here instead of a private per-run directory",
        )
        p.add_argument(
            "--typed-only",
            action="store_true",
            help="keep the authored set: TYPED + REVIEW + AMBIGUOUS",
        )
        p.add_argument(
            "--all",
            dest="include_trivial",
            action="store_true",
            help="include TRIVIAL turns (acks, slash-commands)",
        )
        p.add_argument(
            "--min",
            type=int,
            default=0,
            dest="min_words",
            help="drop turns under N words",
        )
    args = ap.parse_args(argv)

    turns = all_turns()

    if args.cmd == "stats":
        sess = _sessions(turns)
        print("\n".join(_stats_lines(turns, sess)))
        return 0

    # default = compile
    kept = _filter(
        turns,
        getattr(args, "typed_only", False),
        getattr(args, "include_trivial", False),
        getattr(args, "min_words", 0),
    )
    sess = _sessions(kept)
    doc = build_document(kept, sess)
    out = getattr(args, "out", None) or _default_out()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    by_cls, _ = _summary(kept)
    print(f"wrote {out}")
    print(f"  {len(kept):,} human turns across {len(sess)} sessions")
    parts = [
        f"{c} {by_cls[c]['turns']}"
        for c in CLASSES
        if by_cls[c]["turns"] or c in AUTHORED_CLASSES
    ]
    print("  " + "  ".join(parts))
    return 0


if __name__ == "__main__":
    main()
