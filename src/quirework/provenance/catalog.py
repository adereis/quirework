#!/usr/bin/env python3
"""Generated catalog (index) of the provenance corpus.

One row per *tracked* source transcript. A session is added to git only after
the keep/remove read (see the session-corpus READMEs), so **tracked == curated-in**:
the catalog inherits curation and needs no inclusion policy of its own. Pending
captures sitting uncommitted in the working tree are absent by construction.

Mechanical fields (title, date, model, tokens, edits, sha, and the cold-read /
highlight cross-links) are recomputed wholesale from the corpus on every build --
a pure function of the files, so the index can never silently drift. That
includes the edit counts. A path is measured from the project trees recorded
beside the corpus (``trees``), never from the worktrees this machine happens to
have, so removing a worktree cannot change a row. The only preserved state is
the small editorial layer (``note``, ``tags``), merged by path across rebuilds,
so a hand-written gloss survives a regenerate.

It is the complete, mechanical layer *beneath* the hand-curated ``highlights.md``
(the narrative of the most significant moments); the ``★`` column links the two.

Outputs (both committed, both private -- they point at raw logs, like the
cold-read manifest):

    .quirework/provenance/index.json   canonical, machine source of truth
    .quirework/provenance/INDEX.md     rendered human table

Commands:
    (no args)     rebuild index.json + INDEX.md from the tracked corpus
    --check       exit non-zero if either is stale (used by the pre-commit hook)

There is no wall-clock anywhere in the output, so ``--check`` is deterministic:
same corpus in, byte-identical index out.
"""

from __future__ import annotations

import json
import os
import re
import sys

from quirework.project import current_project
from quirework.provenance.sources import source_reference
from quirework.provenance import ccfilter as FLT
from quirework.provenance import codexfilter as CODEX_FLT
from quirework.provenance import coldread as CR
from quirework.provenance import scope as SCOPE


SCHEMA = 1
EDITORIAL = ("note", "tags")  # human-authored, preserved across rebuilds


# --------------------------------------------------------------------------- #
# sources: the *tracked* corpus only (curated-in == in git)
# --------------------------------------------------------------------------- #


def _settings():
    """Resolve paths from the current operation, without import-time state."""
    from types import SimpleNamespace

    INDEX_JSON = (
        current_project().path("provenance.root", ".quirework/provenance")
        / "index.json"
    )
    INDEX_MD = (
        current_project().path("provenance.root", ".quirework/provenance") / "INDEX.md"
    )
    HIGHLIGHTS = (
        current_project().path("provenance.root", ".quirework/provenance")
        / "highlights.md"
    )
    REMOVED_CC = (
        current_project().path(
            "provenance.claude_sessions", ".quirework/provenance/cc-sessions"
        )
        / "removed-sessions.txt"
    )
    REMOVED_AGY = (
        current_project().path(
            "provenance.agy_sessions", ".quirework/provenance/agy-sessions"
        )
        / "removed-sessions.txt"
    )
    REMOVED_CODEX = (
        current_project().path(
            "provenance.codex_sessions", ".quirework/provenance/codex-sessions"
        )
        / "removed-sessions.txt"
    )
    return SimpleNamespace(
        HIGHLIGHTS=HIGHLIGHTS,
        INDEX_JSON=INDEX_JSON,
        INDEX_MD=INDEX_MD,
        REMOVED_AGY=REMOVED_AGY,
        REMOVED_CC=REMOVED_CC,
        REMOVED_CODEX=REMOVED_CODEX,
    )


def _git_tracked(subdir, suffix):
    from .sources import accepted_files

    return accepted_files(subdir, suffix, current_project())


def _short_model(m):
    if not m or m == "<synthetic>":
        return "?"
    return m[len("claude-") :] if m.startswith("claude-") else m


def _short_id(sid):
    """Compact id for the rendered table: 8 hex for uuids, full for gemini."""
    return sid if sid.startswith("gemini-") else sid[:8]


# --------------------------------------------------------------------------- #
# per-source metadata (all cheap; no model calls)
# --------------------------------------------------------------------------- #
def _cc_scan(path):
    """Title / date span / primary model from a cc-session JSONL (one pass)."""
    title = slug = first = last = None
    models = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("aiTitle"):
                title = o["aiTitle"]
            if o.get("slug"):
                slug = o["slug"]
            ts = o.get("timestamp")
            if ts:
                first = first or ts
                last = ts
            msg = o.get("message")
            if isinstance(msg, dict):
                mm = msg.get("model")
                if mm and mm != "<synthetic>":
                    models[mm] = models.get(mm, 0) + 1
    model = max(models, key=models.get) if models else None
    if not title:
        title = slug.replace("-", " ") if slug else "(untitled)"
    return {
        "title": title,
        "date": (first or "")[:10] or "?",
        "date_end": (last or "")[:10] or "?",
        "model": _short_model(model),
    }


def _web_scan(path):
    """Title / created-date / model / message-count from a rendered web .md."""
    text = path.read_text(encoding="utf-8", errors="replace")
    title = "(untitled)"
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break

    def row(label):
        m = re.search(rf"(?m)^\|\s*{re.escape(label)}[^|]*\|\s*(.+?)\s*\|\s*$", text)
        return m.group(1).strip().strip("`") if m else None

    # claude.ai headers carry Created/Model/Messages; the gemini DOM scrape
    # carries Date/Turns and the model after a "·" in Platform. Try both.
    date = row("Created") or row("Date")
    model = row("Model")
    if not model:
        platform = row("Platform") or ""
        model = platform.split("·")[-1].strip() if "·" in platform else None
    count = row("Messages") or row("Turns")
    nmsg = None
    if count:
        mm = re.match(r"\d+", count)
        nmsg = int(mm.group(0)) if mm else None
    return {
        "title": title,
        "date": date[:10] if date else "?",
        "model": _short_model(model),
        "messages": nmsg,
    }


def _agy_scan(path):
    """Title / date span / model from an Antigravity session JSONL."""
    title = first = last = model = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = o.get("created_at")
            if ts:
                first = first or ts
                last = ts
            if not title and o.get("type") == "USER_INPUT":
                c = o.get("content") or ""
                m = re.search(
                    r"<USER_REQUEST>\s*(.*?)(?:\s*</USER_REQUEST>|$)", c, re.DOTALL
                )
                raw = (m.group(1) if m else c).strip()
                raw = raw.split("\n")[0].strip()
                title = (raw[:57] + "...") if len(raw) > 60 else raw
            if not model and o.get("type") == "USER_INPUT":
                c = o.get("content") or ""
                m = re.search(r"Model Selection.*?to\s+(.+?)\.?\s*(?:No need|$)", c)
                if m:
                    model = m.group(1).strip()
    return {
        "title": title or "(untitled)",
        "date": (first or "")[:10] or "?",
        "date_end": (last or "")[:10] or "?",
        "model": model or "?",
    }


_AGY_EDIT_TOOLS = {
    "write_to_file",
    "replace_file_content",
    "multi_replace_file_content",
}


def _agy_edit_targets(path):
    """Count doc vs meta file edits and tally basenames in an agy-session."""
    doc = meta = 0
    files = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            for tc in obj.get("tool_calls") or []:
                if tc.get("name") not in _AGY_EDIT_TOOLS:
                    continue
                fp = (tc.get("args") or {}).get("TargetFile") or ""
                if isinstance(fp, str) and fp.startswith('"'):
                    fp = fp.strip('"')
                if not fp:
                    continue
                if CR._classify_edit(fp) == "doc":
                    doc += 1
                else:
                    meta += 1
                bn = os.path.basename(fp)
                files[bn] = files.get(bn, 0) + 1
    return doc, meta, files


def _agy_render(path):
    """Lightweight render of an agy-session for token estimation."""
    parts = []
    human_n = asst_n = 0
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
            if typ == "USER_INPUT":
                c = o.get("content") or ""
                m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", c, re.DOTALL)
                parts.append(m.group(1) if m else c)
                human_n += 1
            elif typ == "PLANNER_RESPONSE" and o.get("content"):
                parts.append(o["content"])
                asst_n += 1
    text = "\n\n".join(parts)
    return text, {"chars": len(text), "human_turns": human_n, "assistant_turns": asst_n}


def _agy_entry(path, manifest, hl_text):
    rel = str(source_reference(path, current_project()))
    body, stats = _agy_render(path)
    doc, meta, files = _agy_edit_targets(path)
    top = [f"{n}x{c}" for n, c in sorted(files.items(), key=lambda x: -x[1])[:3]]
    sha = FLT.sha256_file(str(path))
    sc = _agy_scan(path)
    sid = path.stem
    return rel, {
        "id": sid,
        "era": "b-agy",
        "title": sc["title"],
        "date": sc["date"],
        "date_end": sc["date_end"],
        "model": sc["model"],
        "human_turns": stats.get("human_turns", 0),
        "assistant_turns": stats.get("assistant_turns", 0),
        "tokens": len(body) // 4,
        "doc_edits": doc,
        "meta_edits": meta,
        "top_edited": top,
        "sha256": sha,
        "coldread": CR.is_hit(manifest, rel, sha),
        "scope": SCOPE.scope_of_id(sid),
        "highlight": sid in hl_text or _short_id(sid) in hl_text,
        "note": "",
        "tags": [],
    }


def _codex_entry(path, manifest, hl_text):
    rel = str(source_reference(path, current_project()))
    body, stats = CODEX_FLT.filter_session(path)
    doc, meta, files = CODEX_FLT.edit_targets(path, CR._classify_edit)
    top = [f"{n}×{c}" for n, c in sorted(files.items(), key=lambda x: -x[1])[:3]]
    sha = FLT.sha256_file(str(path))
    sc = CODEX_FLT.scan(path)
    sid = path.stem
    return rel, {
        "id": sid,
        "era": "d-codex",
        "title": sc["title"],
        "date": sc["date"],
        "date_end": sc["date_end"],
        "model": _short_model(sc["model"]),
        "source_kind": sc["source_kind"],
        "human_turns": stats.get("human_turns", 0),
        "assistant_turns": stats.get("assistant_turns", 0),
        "tokens": len(body) // 4,
        "doc_edits": doc,
        "meta_edits": meta,
        "top_edited": top,
        "sha256": sha,
        "coldread": CR.is_hit(manifest, rel, sha),
        "scope": SCOPE.scope_of_id(sid),
        "highlight": sid in hl_text or _short_id(sid) in hl_text,
        "note": "",
        "tags": [],
    }


def _cc_entry(path, manifest, hl_text):
    rel = str(source_reference(path, current_project()))
    body, stats = CR.render_source(path)
    doc, meta, files = CR._edit_targets(path)
    top = [f"{n}×{c}" for n, c in sorted(files.items(), key=lambda x: -x[1])[:3]]
    sha = FLT.sha256_file(str(path))
    sc = _cc_scan(path)
    sid = path.stem
    return rel, {
        "id": sid,
        "era": "c-refinement",
        "title": sc["title"],
        "date": sc["date"],
        "date_end": sc["date_end"],
        "model": sc["model"],
        "human_turns": stats.get("human_turns", 0),
        "assistant_turns": stats.get("assistant_turns", 0),
        "tokens": len(body) // 4,
        "doc_edits": doc,
        "meta_edits": meta,
        "top_edited": top,
        "sha256": sha,
        "coldread": CR.is_hit(manifest, rel, sha),
        "scope": SCOPE.scope_of_id(sid),
        "highlight": sid in hl_text or _short_id(sid) in hl_text,
        "note": "",
        "tags": [],
    }


def _web_entry(path, manifest, hl_text):
    rel = str(source_reference(path, current_project()))
    body, _ = CR.render_source(path)
    sha = FLT.sha256_file(str(path))
    sc = _web_scan(path)
    sid = path.stem
    return rel, {
        "id": sid,
        "era": "a-web",
        "title": sc["title"],
        "date": sc["date"],
        "model": sc["model"],
        "messages": sc["messages"],
        "tokens": len(body) // 4,
        "sha256": sha,
        "coldread": CR.is_hit(manifest, rel, sha),
        "scope": SCOPE.scope_of_id(sid),
        "highlight": sid in hl_text or _short_id(sid) in hl_text,
        "note": "",
        "tags": [],
    }


def _parse_removed(path, corpus):
    """Parse either house form in a corpus's ``removed-sessions.txt``."""
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        name, _, reason = s.partition("#")
        name = name.strip()
        if name.endswith(".jsonl"):
            name = name[: -len(".jsonl")]
        if name:
            out.append({"id": name, "reason": reason.strip(), "corpus": corpus})
    return out


def _removed():
    """The curated-out list from all local session corpora."""
    return (
        _parse_removed(_settings().REMOVED_CC, "cc")
        + _parse_removed(_settings().REMOVED_AGY, "agy")
        + _parse_removed(_settings().REMOVED_CODEX, "codex")
    )


def unplaced(cc_paths=(), codex_paths=()):
    """Sessions started outside every recorded project tree, keyed by directory.

    Each edit a session records is measured from the listed tree that holds it
    (see `trees`). A session started under no listed tree has nothing to be
    measured from, so every edit it made would count as metadata. Such a count
    is wrong without looking wrong, so the catalog refuses to build over one and
    triage warns. Antigravity records no starting directory and is not checked.
    """
    from quirework.provenance import trees
    from quirework.provenance.ingestion.claude import recorded_cwd

    listed = trees.recorded(current_project())
    found = {}
    started = [(p, recorded_cwd(p)) for p in cc_paths]
    started += [(p, CODEX_FLT.session_meta(p).get("cwd")) for p in codex_paths]
    for path, cwd in started:
        if cwd and not trees.within(listed, cwd):
            found.setdefault(cwd, []).append(path)
    return found


def report_unplaced(found, file=None):
    """Name each unplaced starting directory and how to place it."""
    from quirework.provenance import trees
    from quirework.runs.storage import shown

    file = file or sys.stderr
    total = sum(len(v) for v in found.values())
    print(
        f"{total} session(s) started outside every recorded project tree:",
        file=file,
    )
    for cwd, paths in sorted(found.items()):
        example = source_reference(paths[0], current_project())
        print(f"  {shown(cwd)}  ({len(paths)}, e.g. {example})", file=file)
    listing = source_reference(trees.location(current_project()), current_project())
    print(
        "Their edits would all count as metadata. A capture sweep records this "
        "machine's checkouts; add a removed checkout's project directory to "
        f"{listing} by hand.",
        file=file,
    )


def _refuse_unplaced():
    """Report tracked sessions no recorded tree places; True if there are any."""
    found = unplaced(
        _git_tracked(
            current_project().path(
                "provenance.claude_sessions", ".quirework/provenance/cc-sessions"
            ),
            ".jsonl",
        ),
        _git_tracked(
            current_project().path(
                "provenance.codex_sessions", ".quirework/provenance/codex-sessions"
            ),
            ".jsonl",
        ),
    )
    if found:
        print("provenance catalog: cannot count edits.", file=sys.stderr)
        report_unplaced(found)
    return bool(found)


def _merge_editorial(entries, existing):
    """Carry the editorial layer (note/tags) forward from a prior index, by path."""
    for rel, e in entries.items():
        prev = existing.get(rel, {})
        for k in EDITORIAL:
            if prev.get(k):
                e[k] = prev[k]
    return entries


# --------------------------------------------------------------------------- #
# build / render / serialize
# --------------------------------------------------------------------------- #
def build():
    manifest = CR.load_manifest()
    hl_text = (
        _settings().HIGHLIGHTS.read_text(encoding="utf-8")
        if _settings().HIGHLIGHTS.exists()
        else ""
    )
    existing = {}
    if _settings().INDEX_JSON.exists():
        try:
            existing = json.loads(
                _settings().INDEX_JSON.read_text(encoding="utf-8")
            ).get("sources", {})
        except json.JSONDecodeError:
            existing = {}

    entries = {}
    for path in _git_tracked(
        current_project().path(
            "provenance.conversations", ".quirework/provenance/conversations"
        ),
        ".md",
    ):
        if path.name == "README.md":
            continue
        rel, e = _web_entry(path, manifest, hl_text)
        entries[rel] = e
    for path in _git_tracked(
        current_project().path(
            "provenance.agy_sessions", ".quirework/provenance/agy-sessions"
        ),
        ".jsonl",
    ):
        rel, e = _agy_entry(path, manifest, hl_text)
        entries[rel] = e
    for path in _git_tracked(
        current_project().path(
            "provenance.claude_sessions", ".quirework/provenance/cc-sessions"
        ),
        ".jsonl",
    ):
        rel, e = _cc_entry(path, manifest, hl_text)
        entries[rel] = e
    for path in _git_tracked(
        current_project().path(
            "provenance.codex_sessions", ".quirework/provenance/codex-sessions"
        ),
        ".jsonl",
    ):
        rel, e = _codex_entry(path, manifest, hl_text)
        entries[rel] = e

    _merge_editorial(entries, existing)

    def era_group(era):
        return sorted(
            (kv for kv in entries.items() if kv[1]["era"] == era),
            key=lambda kv: (kv[1]["date"], kv[1]["id"]),
            reverse=True,
        )

    ordered = (
        era_group("a-web")
        + era_group("b-agy")
        + era_group("c-refinement")
        + era_group("d-codex")
    )
    return {
        "schema": SCHEMA,
        "filter_version": FLT.FILTER_VERSION,
        "codex_filter_version": CODEX_FLT.FILTER_VERSION,
        "sources": {rel: e for rel, e in ordered},
        "removed": _removed(),
    }


def _dump_json(index):
    return json.dumps(index, indent=2, ensure_ascii=False) + "\n"


def _esc(s):
    return str(s).replace("|", "\\|")


def _tok(n):
    return f"{n // 1000}k" if n >= 1000 else str(n)


def render_md(index):
    src = index["sources"]
    web = [e for e in src.values() if e["era"] == "a-web"]
    agy = [e for e in src.values() if e["era"] == "b-agy"]
    cc = [e for e in src.values() if e["era"] == "c-refinement"]
    codex = [e for e in src.values() if e["era"] == "d-codex"]
    removed = index["removed"]
    L = [
        "<!-- generated by `quirework provenance catalog` -- do not hand-edit the mechanical columns. -->",
        "",
        "# Provenance catalog",
        "",
        "Complete index of the *tracked* provenance corpus -- one row per session,",
        "regenerated from the files on every import.",
        "A session is committed only after the keep/remove read, so tracked == curated-in;",
        "pending captures awaiting triage are absent by construction.",
        *(
            [
                "For the curated *narrative* of the most significant moments see",
                "[`highlights.md`](./highlights.md) -- the ★ column marks rows that "
                "have an entry there.",
            ]
            # A project that keeps no narrative beside the index should not be
            # handed a link to a file it has never had, in a document generated
            # for it. The star column is still filled in when the file exists.
            if _settings().HIGHLIGHTS.exists()
            else []
        ),
        "",
        f"**{len(cc)}** Claude Code sessions · **{len(agy)}** Antigravity sessions · "
        f"**{len(codex)}** Codex sessions · **{len(web)}** web dialogues · "
        f"**{len(removed)}** curated out.",
        "",
        f"Of those, **{sum(1 for e in src.values() if e.get('scope', SCOPE.WORK) == SCOPE.MAKING)}** "
        f"are scope `making` (the apparatus: tools, guards, process rules) and the "
        f"rest are scope `work`. Scope decides which blind cold-read reads a "
        f"session, nothing else -- see each session directory's "
        f"`making-sessions.txt`.",
        "",
        "## Claude Code sessions (`cc-sessions/`)",
        "",
        "| date | id | title | model | ~tok | doc/meta | scope | cold | ★ |",
        "| --- | --- | --- | --- | --: | --- | --- | :-: | :-: |",
    ]
    for e in cc:
        L.append(
            f"| {e['date']} | `{_short_id(e['id'])}` | {_esc(e['title'])} | {e['model']} "
            f"| {_tok(e['tokens'])} | {e['doc_edits']}/{e['meta_edits']} | {e.get('scope', SCOPE.WORK)} "
            f"| {'✓' if e['coldread'] else '—'} | {'★' if e['highlight'] else ''} |"
        )
    L += [
        "",
        "## Antigravity sessions (`agy-sessions/`)",
        "",
        "| date | id | title | model | ~tok | doc/meta | scope | cold | ★ |",
        "| --- | --- | --- | --- | --: | --- | --- | :-: | :-: |",
    ]
    for e in agy:
        L.append(
            f"| {e['date']} | `{_short_id(e['id'])}` | {_esc(e['title'])} | {e['model']} "
            f"| {_tok(e['tokens'])} | {e['doc_edits']}/{e['meta_edits']} | {e.get('scope', SCOPE.WORK)} "
            f"| {'✓' if e['coldread'] else '—'} | {'★' if e['highlight'] else ''} |"
        )
    L += [
        "",
        "## Codex sessions (`codex-sessions/`)",
        "",
        "| date | id | title | model | thread | ~tok | doc/meta | scope | cold | ★ |",
        "| --- | --- | --- | --- | --- | --: | --- | --- | :-: | :-: |",
    ]
    for e in codex:
        L.append(
            f"| {e['date']} | `{_short_id(e['id'])}` | {_esc(e['title'])} | {e['model']} "
            f"| {e.get('source_kind', 'main')} | {_tok(e['tokens'])} "
            f"| {e['doc_edits']}/{e['meta_edits']} | {e.get('scope', SCOPE.WORK)} "
            f"| {'✓' if e['coldread'] else '—'} | {'★' if e['highlight'] else ''} |"
        )
    L += [
        "",
        "## Web dialogues (`conversations/`)",
        "",
        "| date | id | title | model | ~tok | msgs | cold | ★ |",
        "| --- | --- | --- | --- | --: | --: | :-: | :-: |",
    ]
    for e in web:
        L.append(
            f"| {e['date']} | `{_short_id(e['id'])}` | {_esc(e['title'])} | {e['model']} "
            f"| {_tok(e['tokens'])} | {e['messages'] if e['messages'] is not None else '?'} "
            f"| {'✓' if e['coldread'] else '—'} | {'★' if e['highlight'] else ''} |"
        )
    L += [
        "",
        "## Curated out",
        "",
        "Captured but deliberately excluded as non-canonical-text work",
        "(see each session directory's `removed-sessions.txt`).",
        "",
    ]
    if removed:
        L += ["| id | corpus | reason |", "| --- | --- | --- |"]
        L += [
            f"| `{_short_id(r['id'])}` | {r.get('corpus', 'cc')} | {_esc(r['reason'])} |"
            for r in removed
        ]
    else:
        L.append("_none_")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def write():
    if _refuse_unplaced():
        return 1
    index = build()
    _settings().INDEX_JSON.write_text(_dump_json(index), encoding="utf-8")
    _settings().INDEX_MD.write_text(render_md(index), encoding="utf-8")
    print(
        f"wrote {source_reference(_settings().INDEX_JSON, current_project())} and {source_reference(_settings().INDEX_MD, current_project())} "
        f"({len(index['sources'])} sources, {len(index['removed'])} curated out)"
    )
    return 0


def check():
    if _refuse_unplaced():
        return 1
    index = build()
    stale = []
    if not _settings().INDEX_JSON.exists() or _settings().INDEX_JSON.read_text(
        encoding="utf-8"
    ) != _dump_json(index):
        stale.append(str(source_reference(_settings().INDEX_JSON, current_project())))
    if not _settings().INDEX_MD.exists() or _settings().INDEX_MD.read_text(
        encoding="utf-8"
    ) != render_md(index):
        stale.append(str(source_reference(_settings().INDEX_MD, current_project())))
    if stale:
        print(f"provenance catalog stale: {', '.join(stale)}", file=sys.stderr)
        print(
            "regenerate and re-stage:  quirework provenance catalog && git add "
            f"{source_reference(_settings().INDEX_JSON, current_project())} {source_reference(_settings().INDEX_MD, current_project())}",
            file=sys.stderr,
        )
        return 1
    print(
        f"provenance catalog OK ({len(index['sources'])} sources, "
        f"{len(index['removed'])} curated out)"
    )
    return 0


USAGE = """usage: quirework provenance catalog [--check]

Regenerate the provenance catalog from the tracked corpus.

Writes both outputs (private -- they point at raw logs):
  .quirework/provenance/index.json   canonical, machine source of truth
  .quirework/provenance/INDEX.md     rendered human table

Edits are counted against the project directories that capture recorded in
.quirework/provenance/project-trees.txt, never against the worktrees that
exist right now. Both forms refuse while a tracked session started outside
every recorded directory.

options:
  -h, --help  show this help message and exit
  --check     exit non-zero if either output is stale; write nothing
              (this is what the pre-commit hook runs)
"""


def main(argv):
    if any(a in ("-h", "--help") for a in argv):
        print(USAGE, end="")
        return 0
    if "--check" in argv:
        return check()
    if argv:
        print(
            f"catalog: unexpected args {argv} (use no args, or --check)",
            file=sys.stderr,
        )
        return 2
    return write()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
