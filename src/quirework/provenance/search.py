#!/usr/bin/env python3
"""Keyword search across the human's own turns in the provenance corpus.

Answers the content question the catalog (metadata only) cannot: *which sessions
did I, the human, mention X in?* It searches the human's **own** turns -- not the
assistant's words, not injected document context -- by reusing ``humaninput``'s
rigorous turn extraction/classification across every source (claude.ai web,
Claude Code, Gemini, and Codex). Matching that distinction matters: the essay is *about*
Mabel's kettle, so the assistant and the injected text say "Mabel" constantly; a
raw grep drowns in those, while the human-turn search returns the handful of
sessions where the human actually raised it.

Hits are reported *through* the catalog's metadata (date, title, highlight ★),
so a fistful of opaque session ids reads as named, dated rows.

Live search, no persistent index: at corpus scale (tens of MB) a scan over the
filtered stream is instant, and there is no second index to keep in sync -- the
right call until the corpus is genuinely gigabytes, which it never will be.

``--semantic`` answers the question keyword mode structurally cannot: *where did
I first get uneasy about the steepness guard?* has no word to grep for. It ranks
by meaning against a locally-built embedding index over the same human-turn
stream (``semantic.py`` owns the index and the local-only rationale). Both modes
emit the same session rows and pair with ``quirework provenance show``, so the retriever
changes and nothing downstream does. Keyword mode stays the default because it
is exact and exhaustive; semantic mode is recall-shaped, not proof.

    quirework provenance search Tea Mabel Teapot      # OR across keywords, whole-word
    quirework provenance search --substr mabel        # substring match (default is whole-word)
    quirework provenance search --show "China Brain"  # also print the matching turn snippets
    quirework provenance search --semantic "unease about the moral floor's line of kind"
"""

from __future__ import annotations

import re
import sys

from quirework.project import current_project
from quirework.provenance import humaninput as HI
from quirework.provenance import catalog as CAT


def _settings():
    """Resolve paths from the current operation, without import-time state."""
    from types import SimpleNamespace

    HIGHLIGHTS = (
        current_project().path("provenance.root", ".quirework/provenance")
        / "highlights.md"
    )
    return SimpleNamespace(HIGHLIGHTS=HIGHLIGHTS)


def _compile(keywords, substr, regex):
    out = []
    for kw in keywords:
        if regex:
            rx = re.compile(kw, re.I)
        elif substr:
            rx = re.compile(re.escape(kw), re.I)
        else:
            rx = re.compile(rf"\b{re.escape(kw)}\b", re.I)
        out.append((kw, rx))
    return out


def _snippet(text, m, width=70):
    a, b = max(0, m.start() - width), min(len(text), m.end() + width)
    core = " ".join(text[a:b].split())
    return ("…" if a > 0 else "") + core + ("…" if b < len(text) else "")


def _resolve(source, file):
    """(catalog_id, file_to_scan_for_title, era) for a humaninput turn's source."""
    if source == "cc":
        sid = file[: -len(".jsonl")] if file.endswith(".jsonl") else file
        return (
            sid,
            current_project().path(
                "provenance.claude_sessions", ".quirework/provenance/cc-sessions"
            )
            / file,
            "c-refinement",
        )
    if source == "agy":
        sid = file[: -len(".jsonl")] if file.endswith(".jsonl") else file
        return (
            sid,
            current_project().path(
                "provenance.agy_sessions", ".quirework/provenance/agy-sessions"
            )
            / file,
            "b-agy",
        )
    if source == "codex":
        sid = file[: -len(".jsonl")] if file.endswith(".jsonl") else file
        return (
            sid,
            current_project().path(
                "provenance.codex_sessions", ".quirework/provenance/codex-sessions"
            )
            / file,
            "d-codex",
        )
    if source == "gemini":
        sid = file[: -len(".md")] if file.endswith(".md") else file
        return (
            sid,
            current_project().path(
                "provenance.conversations", ".quirework/provenance/conversations"
            )
            / file,
            "a-web",
        )
    base = file
    for suf in (".raw.json", ".md"):
        if base.endswith(suf):
            base = base[: -len(suf)]
            break
    return (
        base,
        current_project().path(
            "provenance.conversations", ".quirework/provenance/conversations"
        )
        / (base + ".md"),
        "a-web",
    )


def _title_date(source, path):
    if not path.exists():
        return {"title": "(unknown)", "date": "?"}
    if source == "cc":
        return CAT._cc_scan(path)
    if source == "agy":
        return CAT._agy_scan(path)
    if source == "codex":
        from quirework.provenance import codexfilter as CODEX_FLT

        return CODEX_FLT.scan(path)
    return CAT._web_scan(path)


def search(keywords, substr=False, regex=False, turns=None, highlights=None):
    """Return (hits, n_searched). ``turns``/``highlights`` injectable for tests."""
    pats = _compile(keywords, substr, regex)
    hl = (
        highlights
        if highlights is not None
        else (
            _settings().HIGHLIGHTS.read_text(encoding="utf-8")
            if _settings().HIGHLIGHTS.exists()
            else ""
        )
    )
    if turns is None:
        turns = HI.all_turns()

    sessions = {}
    for t in turns:
        s = sessions.setdefault(
            (t["source"], t["file"]), {"counts": {}, "snippets": {}, "ts": []}
        )
        if t["ts"]:
            s["ts"].append(t["ts"])
        for kw, rx in pats:
            for m in rx.finditer(t["text"]):
                s["counts"][kw] = s["counts"].get(kw, 0) + 1
                s["snippets"].setdefault(kw, _snippet(t["text"], m))

    hits = []
    for (source, file), s in sessions.items():
        if not s["counts"]:
            continue
        sid, path, era = _resolve(source, file)
        meta = _title_date(source, path)
        date = min(s["ts"]).strftime("%Y-%m-%d") if s["ts"] else meta["date"]
        hits.append(
            {
                "id": sid,
                "era": era,
                "date": date,
                "title": meta["title"],
                # A highlight entry may cite the full id or the 8-hex short id the
                # tools print; matching only the full id silently lost the star.
                "star": sid in hl or CAT._short_id(sid) in hl,
                "counts": s["counts"],
                "snippets": s["snippets"],
            }
        )
    hits.sort(key=lambda h: (h["date"], h["id"]), reverse=True)
    return hits, len(sessions)


def semantic_search(
    text, top=12, rebuild=False, index=None, backend=None, highlights=None
):
    """Meaning-ranked counterpart to ``search()``, in the same hit shape.

    Returns ``(hits, n_indexed)``. Hits carry ``score``/``windows``/``snippet``
    where keyword hits carry ``counts``/``snippets``; everything the caller uses
    to *identify* a session (id, era, date, title, star) is identical, which is
    what lets one row printer serve both. ``index``/``backend``/``highlights``
    are injectable for tests, as ``search()``'s ``turns`` is.
    """
    from quirework.provenance import semantic as SEM

    be = backend or SEM.get_backend()
    idx = index if index is not None else SEM.load(backend=be, rebuild=rebuild)
    hl = (
        highlights
        if highlights is not None
        else (
            _settings().HIGHLIGHTS.read_text(encoding="utf-8")
            if _settings().HIGHLIGHTS.exists()
            else ""
        )
    )

    hits = []
    for (source, file), s in SEM.query(text, idx, be, top=top):
        sid, path, era = _resolve(source, file)
        meta = _title_date(source, path)
        hits.append(
            {
                "id": sid,
                "era": era,
                "date": meta["date"],
                "title": meta["title"],
                "star": sid in hl or CAT._short_id(sid) in hl,
                "score": s["score"],
                "windows": s["hits"],
                "snippet": s["snippet"],
                "ordinal": s["ordinal"],
            }
        )
    return hits, len(idx["meta"])


def _tidy(text, width):
    one = " ".join(text.split())
    return one if len(one) <= width else one[: width - 1] + "…"


def _print_hits(hits, semantic, show):
    """One row printer for both retrievers: date, short id, ★, title, evidence."""
    width = max(len(h["title"]) for h in hits)
    for h in hits:
        star = "★" if h["star"] else " "
        if semantic:
            tally = f"{h['score']:.3f}  ×{h['windows']}"
        else:
            tally = " ".join(f"{kw}×{c}" for kw, c in sorted(h["counts"].items()))
        print(
            f"{h['date']}  {CAT._short_id(h['id']):8}  {star}  "
            f"{h['title']:{width}}  {tally}"
        )
        if semantic:
            # A bare similarity score is not readable evidence, so the matching
            # passage always prints; --show widens it from a line to the window.
            print(
                f"        turn {h['ordinal']}: "
                f"{_tidy(h['snippet'], 1000 if show else 150)}"
            )
        elif show:
            for kw in sorted(h["snippets"]):
                print(f"        {kw}: {h['snippets'][kw]}")


USAGE = """usage: quirework provenance search KEYWORD [KEYWORD ...] [--substr|--regex] [--show]
       quirework provenance search --semantic QUERY [--top N] [--rebuild] [--show]

Find which sessions the *human* raised something in. Both modes read the human's
own turns only -- not the assistant's words, not injected document context -- so
they return the handful of sessions where you actually raised it, not the
hundreds where the book or the assistant echoes it.

  keyword (default)  exact and exhaustive. Multiple keywords are OR'd.
  --semantic         ranks by meaning against a locally-built embedding index,
                     for the questions with no word to grep for. Recall-shaped,
                     not proof: it can miss, so it does not replace keyword mode
                     when the check has to be exhaustive.

options:
  -h, --help  show this help message and exit
  --substr    substring match (default is whole-word)       [keyword]
  --regex     treat each keyword as a regular expression    [keyword]
  --semantic  rank by meaning instead of by matching text
  --top N     how many sessions to return (default 12)      [semantic]
  --rebuild   force a rebuild of the embedding index        [semantic]
  --show      keyword: print matching snippets; semantic: the whole window

examples:
  quirework provenance search Tea Mabel Teapot      OR across keywords, whole-word
  quirework provenance search --substr mabel        substring match
  quirework provenance search --show "China Brain"  with the matching snippets
  quirework provenance search --semantic "why I resisted giving the spout a threshold"

The semantic index is built and stored locally under private/; corpus text is
never sent anywhere. It rebuilds itself when the corpus or the model changes.

read one:  quirework provenance show <id>
"""

FLAGS = ("--substr", "--regex", "--show", "--semantic", "--rebuild")
DEFAULT_TOP = 12


def _parse(argv):
    """``(keywords, opts)``; raises ValueError on a bad or unknown option.

    An unrecognized flag is an error, never a silently dropped keyword:
    `--substring` used to run a whole-word search with no warning.
    """
    opts = {f[2:]: False for f in FLAGS}
    opts["top"] = DEFAULT_TOP
    kws = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in FLAGS:
            opts[a[2:]] = True
        elif a == "--top" or a.startswith("--top="):
            if a == "--top":
                i += 1
                if i >= len(argv):
                    raise ValueError("--top needs a number")
                v = argv[i]
            else:
                v = a.split("=", 1)[1]
            try:
                opts["top"] = int(v)
            except ValueError:
                raise ValueError(f"--top needs a number, got {v!r}") from None
            if opts["top"] < 1:
                raise ValueError("--top must be at least 1")
        elif a.startswith("--"):
            raise ValueError(f"unknown option: {a}")
        else:
            kws.append(a)
        i += 1
    return kws, opts


def _run_keyword(kws, o):
    try:
        hits, n_searched = search(kws, substr=o["substr"], regex=o["regex"])
    except re.error as e:
        print(f"search: bad regular expression: {e}", file=sys.stderr)
        return 2
    mode = "regex" if o["regex"] else ("substring" if o["substr"] else "whole-word")
    print(f"human turns matching {', '.join(kws)}  ({mode}, case-insensitive)\n")
    if not hits:
        print(f"no matches in {n_searched} sessions searched.")
        return 0
    _print_hits(hits, semantic=False, show=o["show"])
    tail = "" if o["show"] else "  (--show for snippets)"
    print(f"\n{len(hits)} session(s) matched of {n_searched} searched.{tail}")
    print("read one: quirework provenance show <id>")
    return 0


# A ranking whose top and bottom scores are this close found little to
# discriminate on: every session in a topically homogeneous corpus is roughly
# this similar to any query about it, so the order approaches arbitrary. The
# *band* is the signal, not the top score -- an unrelated query can still score
# 0.72 here. Calibrated on three real queries, which is a small sample and the
# reason the floor is set well below the lowest good one rather than between:
#   0.087  "Mabel's kettle, the boiler left to sing"      -> on-topic hits
#   0.037  "the china brain, a pot made of pennies"       -> on-topic hits
#   0.016  an abstract query about the author's own doubts -> unrelated hits
# So this catches the degenerate case and stays quiet otherwise; it is a hint,
# never a verdict on the results.
FLAT_SPREAD = 0.025


def _run_semantic(q, o):
    try:
        from quirework.provenance import semantic as SEM
    except ImportError as e:
        # numpy backs the index and is not a core dependency, so a missing one
        # names the extra rather than surfacing a bare traceback.
        print(
            f"search: --semantic needs numpy ({e}). Install the extra:\n"
            "  pip install -e '.[semantic]'",
            file=sys.stderr,
        )
        return 2
    try:
        hits, n_windows = semantic_search(q, top=o["top"], rebuild=o["rebuild"])
    except SEM.BackendUnavailable as e:
        print(f"search: {e}", file=sys.stderr)
        return 2
    print(f"human turns closest in meaning to: {q}\n")
    if not hits:
        print("no sessions indexed.")
        return 0
    _print_hits(hits, semantic=True, show=o["show"])
    spread = hits[0]["score"] - hits[-1]["score"]
    print(
        f"\n{len(hits)} session(s) ranked, best window first, of {n_windows:,} indexed."
    )
    if len(hits) > 2 and spread < FLAT_SPREAD:
        # Say so rather than let a flat list read as a confident answer.
        print(
            f"\nnote: scores span only {spread:.3f}, so this ranking barely "
            "separated them.\n  The whole corpus is about this subject, so an "
            "abstract query matches\n  everything weakly. Naming the concrete "
            "thing (a scene, an example, a\n  term you'd have used at the "
            "time) discriminates better; so does keyword mode."
        )
    print("read one: quirework provenance show <id>")
    return 0


def main(argv):
    if any(a in ("-h", "--help") for a in argv):
        print(USAGE, end="")
        return 0
    try:
        kws, o = _parse(argv)
    except ValueError as e:
        print(f"search: {e}", file=sys.stderr)
        print(USAGE, end="", file=sys.stderr)
        return 2
    if not kws:
        print(USAGE, end="", file=sys.stderr)
        return 2

    if o["semantic"]:
        # The text-matching flags mean nothing against an embedding, and
        # honoring them silently would answer a different question than asked.
        bad = [f for f in ("substr", "regex") if o[f]]
        if bad:
            print(
                f"search: --{' and --'.join(bad)} cannot be combined with "
                "--semantic, which ranks by meaning, not by matching text",
                file=sys.stderr,
            )
            return 2
        return _run_semantic(" ".join(kws), o)
    return _run_keyword(kws, o)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
