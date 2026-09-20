#!/usr/bin/env python3
"""Content-addressed cache for the authorship cold-read analysis.

The expensive step in the cold-read is a *blind* sub-agent reading one
transcript and writing a report. The corpus is almost entirely append-only
(the web conversations are frozen; a finished cc-session never changes), so a
refresh should only (re)analyze sources whose analysis is missing or stale.

This indexes each source by a cache key and tracks which have a current
report::

    key = sha256(source bytes) + filter_version_for(source) + PROMPT_VERSIONS[scope]
          (one recipe version per *reader*, not one for the corpus, so
           bumping the Codex parser leaves Claude Code reports alone;
           a bump revalidates against the recorded render hash first --
           a recipe change that did not alter what the analyst read
           does not re-buy the report)

Anything that changes what an analysis *means* -- the source content, the
filter recipe, or the analyst prompt -- changes the key and misses the cache
deliberately. The analyst **model** is recorded per source as provenance but
is not part of the key: model choice is an economic per-run decision (Sonnet
for most, Opus where depth is worth the tokens), and a mixed-model corpus
must be stable, not converge back to the default by silent re-analysis.

What is cached and committed:
    private/provenance/coldread/manifest.json      index (private: points at raw logs)
    private/provenance/coldread/reports/<key>.md   one blind-analyst report per source

What is NOT committed (regenerated on demand into ~/tmp):
    the filtered transcript renders the analysts read -- they contain verbatim
    prompts (firehose-adjacent) and are deterministic from raw + the filter.

Subcommands:
    status              dry-run: hit / MISS / UNSEALED per source + tokens
    triage              free, mechanical relevance prioritizer over local sessions
                        (by files edited): 'keep' if it edited the document,
                        'review' (needs an intelligent read) if not
                        (--review-list prints just the review-set paths)
    render              write filtered renders for every miss into the staging
                        dir; a long tail of small sealed sessions is packed into
                        batch files (--batch-under N, --batch-max M)
    verify-blind        scan staged renders for CLAUDE.md fingerprints
    analyze             run each staged render through a sandboxed, blind
                        `claude -p` (neutral CWD, tools off, transcript inlined),
                        gate on its blindness-check, and auto-record clean reports
                        (--only SUBSTR, --model M, --limit N, --oldest,
                         --dry-run, --timeout S)
    record SRC R        register report R for a single source SRC
    record-batch R SRC...  register one shared report R across several sources
    discards            per session: commits made vs commits that reached main
                        (a discrepancy detector for authority the transcript
                        cannot record; --min N to set the flag threshold)
"""

import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from quirework.project import current_project
from quirework.provenance.sources import source_reference
from quirework.provenance.sources import kind_of_path
from quirework.providers.blind import BLIND_SETTINGS
from quirework.providers.blind import NO_SETTING_SOURCES
from quirework.providers.blind import MIN_TOOLS
from quirework.providers.blind import NO_MCP
from quirework.providers.blind import confine
from quirework.providers.blind import seat_dir
from quirework.provenance import scope as SCOPE


def _render_base():
    return current_project().path(
        "provenance.rendered", str(current_project().cache_root / "coldread/rendered")
    )


# Parts of the cache key that live outside the source bytes: one prompt version
# per scope, tracking that scope's brief. Bump a scope's number when its brief
# changes in a way that should invalidate its cached reports.
#
# Per-scope, NOT one shared number, and the distinction is not cosmetic: the two
# briefs are edited independently and read disjoint halves of the corpus, so a
# single counter would have made every edit to the making brief silently
# re-buy ~180 book reports that the edit never touched. That is the same
# over-invalidation the render-hash revalidation exists to undo (see
# `render_sha256`), reintroduced one layer up.
PROMPT_VERSIONS = {
    SCOPE.BOOK: 2,
    # 3: states the standing-instructions baseline -- the assistant works under
    # rules it did not author in-session, so conformance to a convention reads
    # as prior human direction and only the edges are diagnostic (2026-08-13).
    SCOPE.MAKING: 3,
}
PROMPT_VERSION = PROMPT_VERSIONS[SCOPE.BOOK]  # back-compat alias
# The analysis model, selected by the author: Sonnet reads-and-classifies well
# at a fraction of Opus cost, and the id here is what the CLI alias below
# resolves to (recorded per the take-it-from-the-human doctrine).


# A session whose mtime is the newest, or within this window, is treated as the
# live/active log: never a cache hit, since it is still growing.
UNSEALED_WINDOW_S = 2 * 3600

# Renders shorter than this (after filtering) are stub sessions -- skipped.
MIN_RENDER_CHARS = 500

# Render-time batching: sealed sources whose render is below this many tokens
# are concatenated (within an era) into batch files, capped at batch-max, so a
# long tail of tiny sessions does not become a long tail of agents. Only sealed
# sources batch -- the live session always renders standalone -- so a batch of
# unchanging sessions never goes stale.
DEFAULT_BATCH_UNDER = 8000
DEFAULT_BATCH_MAX = 120000


# Blind analysis runs as a headless `claude -p` from a neutral CWD (no project
# CLAUDE.md or path-keyed memory loads there) with all file/exec tools disabled
# and the transcript inlined, so the analyst is structurally unable to read the
# project's own framing. (Agent-tool sub-agents inherit CLAUDE.md/MEMORY.md and
# are NOT blind -- confirmed by test; do not use them here.)
def _reports_tmp():
    return current_project().path(
        "provenance.reports", str(current_project().cache_root / "coldread/reports")
    )


# One brief per scope: the book run asks who originated the book's ideas and
# prose, the making run asks the same of the instruments and the process rules
# (see `scope.py`). Each carries its own PROMPT_VERSIONS entry, and the scope
# is folded into the cache key so a session that changes scope is re-analyzed
# under the brief it will actually be read with, rather than cache-hitting on a
# report written to answer the other question.

# back-compat alias
# alias resolves to MODEL (claude-sonnet-5), recorded in cache
ANALYZE_TIMEOUT_S = 600


from quirework.provenance import ccfilter as FLT

FILTER_VERSION = FLT.FILTER_VERSION


def _options():
    """Read policy from the selected project for this operation."""
    from types import SimpleNamespace

    MODEL = current_project().data("provenance.coldread_policy", {}).get("model", None)
    CLI_MODEL = (
        current_project().data("provenance.coldread_policy", {}).get("cli_model", None)
    )
    _DOC_PREFIXES = tuple(
        current_project().data("provenance.coldread_policy", {}).get("doc_prefixes", ())
    )
    _DOC_DIRS = tuple(
        current_project()
        .data("provenance.coldread_policy", {})
        .get("doc_dirs", ("docs/",))
    )
    return SimpleNamespace(
        CLI_MODEL=CLI_MODEL,
        MODEL=MODEL,
        _DOC_DIRS=_DOC_DIRS,
        _DOC_PREFIXES=_DOC_PREFIXES,
    )


def _settings():
    """Resolve paths from the current operation, without import-time state."""
    from types import SimpleNamespace

    REPORTS = (
        current_project().path("provenance.coldread", ".quirework/provenance/coldread")
        / "reports"
    )
    MANIFEST = (
        current_project().path("provenance.coldread", ".quirework/provenance/coldread")
        / "manifest.json"
    )
    ANALYST_PROMPTS = {
        SCOPE.BOOK: current_project().path(
            "provenance.coldread", ".quirework/provenance/coldread"
        )
        / "analyst-prompt.md",
        SCOPE.MAKING: current_project().path(
            "provenance.coldread", ".quirework/provenance/coldread"
        )
        / "analyst-prompt-making.md",
    }
    ANALYST_PROMPT = ANALYST_PROMPTS[SCOPE.BOOK]
    LOCK = (
        current_project().path("provenance.coldread", ".quirework/provenance/coldread")
        / ".lock"
    )
    SWEEP_LOCK = (
        current_project().path("provenance.coldread", ".quirework/provenance/coldread")
        / ".sweep.lock"
    )
    return SimpleNamespace(
        ANALYST_PROMPT=ANALYST_PROMPT,
        ANALYST_PROMPTS=ANALYST_PROMPTS,
        LOCK=LOCK,
        MANIFEST=MANIFEST,
        REPORTS=REPORTS,
        SWEEP_LOCK=SWEEP_LOCK,
    )


def filter_version_for(path):
    """The recipe version that produced this source's render.

    One number per *reader*, not one for the corpus. Each session format has
    its own parser, and they move independently: bumping the Codex reader must
    not invalidate three hundred Claude Code reports, and a `ccfilter` change
    must not silently leave Codex reports validated against a recipe that no
    longer exists -- which it did, because the version folded into the key was
    always `ccfilter`'s. A web `.md` is read straight off disk, so no parser
    version can alter it and `ccfilter`'s number is the harmless default.
    """
    from quirework.provenance import codexfilter as CODEX_FLT

    return FLT.policy_version(
        CODEX_FLT.FILTER_VERSION
        if kind_of_path(path, current_project()) == "codex"
        else FLT.FILTER_VERSION
    )


# --------------------------------------------------------------------------- #
# sources
# --------------------------------------------------------------------------- #
def _untracked(subdir):
    project = current_project()
    if project.get("provenance.curation", "tracked") == "presence":
        return set()
    root = project.path("provenance.tracking_root", str(project.root))
    subdir = Path(subdir).resolve()
    if not subdir.is_relative_to(root):
        raise ValueError(
            "external corpora require provenance.tracking_root or presence curation"
        )
    result = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            str(subdir.relative_to(root)),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return {root / rel for rel in result.stdout.splitlines()}


def _untracked_cc():
    return _untracked(
        current_project().path(
            "provenance.claude_sessions", ".quirework/provenance/cc-sessions"
        )
    )


def _untracked_agy():
    return _untracked(
        current_project().path(
            "provenance.agy_sessions", ".quirework/provenance/agy-sessions"
        )
    )


def _untracked_codex():
    return _untracked(
        current_project().path(
            "provenance.codex_sessions", ".quirework/provenance/codex-sessions"
        )
    )


def iter_sources(scope=SCOPE.BOOK):
    """Yield ``(path, era)`` for every analyzable source transcript on disk.

    The corpus is curated by *presence*: a session that should not be in it at
    all is removed from the repo working tree (it lives on in ~/.claude), so
    whatever remains here is, by decision, evidence.

    Presence does not say evidence *for what*, and ``scope`` does: ``'book'``
    (the default, and every era-A web dialogue) or ``'making'`` for the
    apparatus half, listed in ``making-sessions.txt``. Pass ``None`` for the
    whole corpus regardless of scope -- which is what ``metrics`` wants, since
    the effort ledger that only counts the book half understates a project that
    was about half instruments.
    """
    making = SCOPE.making_ids()
    for p in sorted(
        current_project()
        .path("provenance.conversations", ".quirework/provenance/conversations")
        .glob("*.md")
    ):
        if p.name == "README.md":
            continue
        if SCOPE.wants(p, scope, making):
            yield p, "a-web"
    for p in sorted(
        current_project()
        .path("provenance.agy_sessions", ".quirework/provenance/agy-sessions")
        .glob("*.jsonl")
    ):
        if SCOPE.wants(p, scope, making):
            yield p, "b-agy"
    for p in sorted(
        current_project()
        .path("provenance.claude_sessions", ".quirework/provenance/cc-sessions")
        .glob("*.jsonl")
    ):
        if SCOPE.wants(p, scope, making):
            yield p, "c-refinement"
    for p in sorted(
        current_project()
        .path("provenance.codex_sessions", ".quirework/provenance/codex-sessions")
        .glob("*.jsonl")
    ):
        if SCOPE.wants(p, scope, making):
            yield p, "d-codex"


def unsealed_set():
    """Resolved paths of CLI sessions that may still be growing."""
    now = time.time()
    sealed_off = set()
    for corpus in (
        current_project().path(
            "provenance.claude_sessions", ".quirework/provenance/cc-sessions"
        ),
        current_project().path(
            "provenance.codex_sessions", ".quirework/provenance/codex-sessions"
        ),
    ):
        js = list(corpus.glob("*.jsonl"))
        if not js:
            continue
        sealed_off.add(max(js, key=lambda p: p.stat().st_mtime).resolve())
        for p in js:
            if now - p.stat().st_mtime < UNSEALED_WINDOW_S:
                sealed_off.add(p.resolve())
    return sealed_off


def render_source(path):
    """Return ``(text, stats)``: filtered for jsonl, as-is for web ``.md``.

    Web dialogues deliberately stay unredacted: they predate and are the source
    of the project framing, so a marker there is usually the human coining it
    live (evidence) — verify-blind flags them REVIEW, never FAIL."""
    if path.suffix == ".jsonl":
        if path.resolve().is_relative_to(
            current_project().path(
                "provenance.codex_sessions", ".quirework/provenance/codex-sessions"
            )
        ):
            from quirework.provenance import codexfilter as CODEX_FLT

            return CODEX_FLT.filter_session(path)
        if path.resolve().is_relative_to(
            current_project().path(
                "provenance.agy_sessions", ".quirework/provenance/agy-sessions"
            )
        ):
            from quirework.provenance import catalog as CAT

            text, stats = CAT._agy_render(path)
            # The agy render has no filter pass of its own, and GEMINI.md
            # injects the same project framing CLAUDE.md does — so the same
            # wrapper-strip + redaction ccfilter applies to cc sessions runs
            # here (verify-blind caught the leak as a FAIL).
            text = FLT.redact(FLT.strip_wrappers(text))
            return text, {**stats, "chars": len(text)}
        return FLT.filter_session(str(path))
    text = path.read_text(encoding="utf-8", errors="replace")
    return text, {"chars": len(text)}


# --------------------------------------------------------------------------- #
# manifest + keys
# --------------------------------------------------------------------------- #
def load_manifest():
    if _settings().MANIFEST.exists():
        return json.loads(_settings().MANIFEST.read_text())
    return {
        "schema": 1,
        "filter_version": FILTER_VERSION,
        "prompt_version": PROMPT_VERSION,
        "default_model": _options().MODEL,
        "updated": None,
        "sources": {},
        "integrated_into": "private/provenance/authorship-coldread.md",
        "integrated_keys": [],
    }


# The manifest is one JSON blob rewritten whole, and `analyze` is a long
# sequence of read-modify-write cycles against it. Nothing shards the work --
# two concurrent sweeps walk the same `sorted(renders)` in the same order --
# so without a lock they interleave: A loads, B loads, A records, B records,
# and B's whole-file write silently drops A's entry. The report file survives
# on disk (its name is content-addressed) but is orphaned from the manifest,
# so it reads as a miss and gets paid for again. Nothing is corrupted and a
# later serial run self-heals; the cost is money and bookkeeping.
# Two locks, deliberately distinct files. A single long-held lock would
# self-deadlock: `analyze` would hold it for the whole sweep and then call
# `cmd_record`, whose own `open()` creates a second file description, and
# flock would block the process against itself.
#   .sweep.lock   held for a whole `analyze` run, non-blocking -> a second
#                 sweep fails fast instead of racing.
#   .lock         held for the few ms of one manifest read-modify-write, so a
#                 hand-run `record` during a sweep cannot clobber an entry.


@contextlib.contextmanager
def _flock(path, blocking=True):
    """Hold an exclusive flock on ``path`` (its own file, never the manifest).

    The lock files are separate from the manifest because `save_manifest`
    replaces it via `os.replace`, which swaps the inode -- a lock held on the
    old one would silently stop conflicting with anything.
    """
    current_project().path(
        "provenance.coldread", ".quirework/provenance/coldread"
    ).mkdir(parents=True, exist_ok=True)
    flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    with open(path, "a+") as fh:
        try:
            fcntl.flock(fh.fileno(), flags)
        except BlockingIOError:
            raise AlreadyRunning(_lock_holder(fh)) from None
        fh.seek(0)
        fh.truncate()
        fh.write(
            f"{os.getpid()} {datetime.datetime.now().isoformat(timespec='seconds')}\n"
        )
        fh.flush()
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def manifest_lock():
    """Serialize one manifest read-modify-write across processes."""
    return _flock(_settings().LOCK, blocking=True)


def persist_revalidations(m):
    """Write back entries `is_hit` carried across a filter bump.

    Without this the revalidation is recomputed on every run -- and it renders
    each candidate to do so, which is the expensive part. Re-reads the manifest
    under the lock and copies over only the revalidation fields, so a report
    recorded by another process in the meantime is not clobbered.
    """
    if not m.pop("_dirty", None):
        return
    today = datetime.date.today().isoformat()
    with manifest_lock():
        cur = load_manifest()
        groups = [(m.get("sources", {}), cur["sources"])]
        groups += [
            (entries, cur.get("persona_sources", {}).get(key, {}))
            for key, entries in m.get("persona_sources", {}).items()
        ]
        for observed, current_entries in groups:
            for rel, e in observed.items():
                current = filter_version_for(rel)
                if e.get("filter_version") != current or "render_sha256" not in e:
                    continue
                ce = current_entries.get(rel)
                # Another process may have recorded a different source while
                # this process was rendering. Merge only the same evidence.
                if (
                    ce is not None
                    and ce.get("filter_version") != current
                    and all(
                        ce.get(k) == e.get(k)
                        for k in ("sha256", "prompt_version", "scope", "report")
                    )
                ):
                    ce["filter_version"] = current
                    ce["render_sha256"] = e["render_sha256"]
                    ce.setdefault("revalidated", today)
        save_manifest(cur)


def sweep_lock():
    """Refuse to start a second concurrent `analyze` (nothing shards the work)."""
    return _flock(_settings().SWEEP_LOCK, blocking=False)


class AlreadyRunning(RuntimeError):
    """Another process holds the cold-read lock."""


def _lock_holder(fh):
    try:
        fh.seek(0)
        return fh.read().strip() or "unknown"
    except OSError:
        return "unknown"


def save_manifest(m):
    m.pop("_dirty", None)
    m["filter_version"] = FILTER_VERSION
    m["prompt_version"] = PROMPT_VERSION
    # The corpus is mixed-model by design; only the per-source entries say
    # which model read which source. The top level records the default.
    m.pop("model", None)
    m["default_model"] = _options().MODEL
    m["updated"] = datetime.date.today().isoformat()
    # Atomic: a kill mid-write must not leave a truncated manifest, and a
    # concurrent reader must never see a half-written one (observed as a
    # JSONDecodeError while a sweep was running).
    tmp = _settings().MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, _settings().MANIFEST)


def sha256_file(p):
    return FLT.sha256_file(str(p))


_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def source_date(path):
    """Earliest date in a source transcript, as ``YYYY-MM-DD`` (or ``""``).

    Read from the data itself rather than the catalog or the file mtime: a
    mirrored session's mtime is when it was copied, not when it was held.
    """
    try:
        if path.suffix == ".jsonl":
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = _DATE_RE.search(line)
                    if m:
                        return m.group(1)
            return ""
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
        m = _DATE_RE.search(head)
        return m.group(1) if m else ""
    except OSError:
        return ""


def render_date(render_path):
    """Earliest source date across a render (a batch holds several)."""
    dates = []
    try:
        text = render_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for rel in re.findall(r"<!-- coldread-source: (\S+)", text):
        d = source_date(current_project().root / rel)
        if d:
            dates.append(d)
    return min(dates) if dates else ""


def render_sha256(path):
    """Hash of the text an analyst actually reads for ``path``.

    The cache used to key on the *source* bytes plus a global
    ``FILTER_VERSION``, which meant any filter change invalidated every
    report — including the ones whose render the change never touched. The
    v2->v3 bump made that concrete: 218 sources, 64 renders actually changed,
    and 154 reports would have been re-bought identical. Era A is the clearest
    case, since a web ``.md`` is read straight off disk and no filter version
    can alter it.

    So the render hash is the honest key: it is exactly what the analyst saw.
    Recorded on every report, and used to revalidate across a filter bump."""
    text, _ = render_source(path)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def report_name(sha, scope=SCOPE.BOOK, filter_version=None, persona=None):
    """Cache filename for one source's report under one scope.

    The book scope keeps the historical, scope-less name on purpose: folding
    a scope token into it would orphan every report in the cache at the moment
    the second run was added, re-buying two hundred analyses to rename them.
    Only the making scope carries an infix.
    """
    tag = "" if scope == SCOPE.BOOK else f"-{scope}"
    version = FILTER_VERSION if filter_version is None else filter_version
    variant = "-persona-" + persona["sha256"] if persona else ""
    return f"{sha[:12]}-f{version}p{PROMPT_VERSIONS[scope]}{tag}{variant}.md"


def _persona_entries(manifest, persona=None):
    if persona is None:
        return manifest["sources"]
    return manifest.setdefault("persona_sources", {}).setdefault(persona["sha256"], {})


def is_hit(manifest, relpath, sha, path=None, persona=None):
    """A report is current iff source bytes, filter, and prompt all match.

    The model is deliberately NOT a validity criterion (author ruling): model
    choice is an economic decision made per run — some sources read by Sonnet,
    others by Opus — so it is recorded per source as *provenance* and never
    invalidates. When it was part of the key, a mixed corpus was unstable:
    any non-default report read as stale, and the next default sweep silently
    re-analyzed and overwrote it. Redoing a source under a different model is
    an explicit act (delete its manifest entry, or `analyze --only` after
    doing so), never a side effect."""
    e = _persona_entries(manifest, persona).get(relpath)
    if not e:
        return False
    # A report answers the question its scope's brief asked. If the source has
    # since changed scope, the cached report is about the other question and is
    # not a hit -- entries written before scopes existed are book by default,
    # which is what they were. The prompt version is then checked against *that
    # scope's* brief, so an edit to one brief never invalidates the other's.
    scope = SCOPE.scope_of_id(Path(relpath).stem)
    if e.get("scope", SCOPE.BOOK) != scope:
        return False
    if e.get("sha256") != sha or e.get("prompt_version") != PROMPT_VERSIONS[scope]:
        return False
    if not (
        current_project().path("provenance.coldread", ".quirework/provenance/coldread")
        / e.get("report", "")
    ).exists():
        return False
    if e.get("filter_version") == filter_version_for(relpath):
        return True
    # The filter recipe moved. That only invalidates this report if it changed
    # what the analyst read -- so ask, rather than assuming (see
    # `render_sha256`). Rendering is the expensive part, so it happens only on
    # this path: an otherwise-current entry after a filter bump.
    recorded = e.get("render_sha256")
    if recorded and path is not None and recorded == render_sha256(path):
        e["filter_version"] = filter_version_for(relpath)  # revalidated, not re-bought
        manifest["_dirty"] = True
        return True
    return False


def classify(path, manifest, unsealed, persona=None):
    """Return one of 'hit' / 'MISS' / 'UNSEALED' for a source path."""
    rel = str(source_reference(path, current_project()))
    sha = sha256_file(path)
    if path.resolve() in unsealed:
        return "UNSEALED", rel, sha
    return (
        ("hit" if is_hit(manifest, rel, sha, path, persona=persona) else "MISS"),
        rel,
        sha,
    )


def _era_for_rel(rel):
    if kind_of_path(rel, current_project()) == "codex":
        return "d-codex"
    if kind_of_path(rel, current_project()) == "agy":
        return "b-agy"
    if "conversations" in rel:
        return "a-web"
    return "c-refinement"


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #
def cmd_status(_args):
    from quirework.sops import personas

    scope, _args = _parse_scope(_args)
    if _args:
        if len(_args) != 2 or _args[0] != "--persona":
            raise ValueError("status accepts --scope and --persona ID")
        persona = personas.resolve(_args[1])
    else:
        persona = None
    manifest = load_manifest()
    unsealed = unsealed_set()
    n = {"hit": 0, "MISS": 0, "UNSEALED": 0}
    miss_tokens = 0
    rows = []
    for path, era in iter_sources(scope):
        state, rel, sha = classify(path, manifest, unsealed, persona=persona)
        n[state] += 1
        est = 0
        if state != "hit":
            body, _ = render_source(path)
            est = len(body) // 4
            miss_tokens += est
        # Which model read this source (provenance; the corpus is mixed-model
        # by design, so the column is how the mix stays visible).
        model = (_persona_entries(manifest, persona).get(rel) or {}).get("model", "")
        model = model.replace("claude-", "") if state == "hit" else "-"
        rows.append((state, era, rel, sha[:10], est, model))

    persist_revalidations(manifest)

    width = max((len(r[2]) for r in rows), default=10)
    mwidth = max((len(r[5]) for r in rows), default=5)
    print(
        f"{'state':9} {'era':13} {'source':{width}} {'sha':10} {'~tok':>8} {'model':{mwidth}}"
    )
    print("-" * (9 + 13 + width + 10 + 9 + mwidth + 5))
    for state, era, rel, sha, est, model in rows:
        tok = f"{est:,}" if est else "-"
        print(f"{state:9} {era:13} {rel:{width}} {sha:10} {tok:>8} {model}")

    print()
    untracked_cc = _untracked_cc()
    untracked_agy = _untracked_agy()
    untracked_codex = _untracked_codex()
    if untracked_cc:
        print(
            f"pending: {len(untracked_cc)} untracked cc-session(s) -- triage, then commit "
            f"(keep) or remove (run `triage`)"
        )
    if untracked_agy:
        print(
            f"pending: {len(untracked_agy)} untracked agy-session(s) -- triage, then commit "
            f"(keep) or remove (run `triage`)"
        )
    if untracked_codex:
        print(
            f"pending: {len(untracked_codex)} untracked codex-session(s) -- triage, "
            f"then commit (keep) or remove (run `triage`)"
        )
    print(
        f"scope={scope or 'all'}  "
        f"hits={n['hit']}  misses={n['MISS']}  unsealed={n['UNSEALED']}  "
        f"to-analyze tokens≈{miss_tokens:,}  "
        f"(≈{max(1, -(-miss_tokens // 150000))} blind sub-agent(s) @150k ctx)"
    )
    # The other scope is a separate artifact, not a subset -- say so, or a run
    # that reports "0 misses" reads as a finished corpus when half of it was
    # never in this run's frame.
    if scope is not None:
        other = SCOPE.MAKING if scope == SCOPE.BOOK else SCOPE.BOOK
        n_other = sum(1 for _ in iter_sources(other))
        print(
            f"note: {n_other} source(s) are scope={other} and are not in this "
            f"run; see `coldread status --scope {other}`."
        )
    if n["UNSEALED"]:
        print(
            "note: the newest Claude Code and Codex sessions (and any modified "
            "in the last 2h) are always UNSEALED — treated as live, "
            "still-growing logs; they never cache-hit, even on an idle corpus."
        )
    if n["MISS"] or n["UNSEALED"]:
        flag = "" if scope in (None, SCOPE.BOOK) else f" --scope {scope}"
        print(
            f"next: quirework provenance coldread render{flag}   then spawn blind "
            f"analysts from",
            _render_out(scope or SCOPE.BOOK),
        )


def _render_out(scope=SCOPE.BOOK):
    """Staging dir for one scope's renders.

    The book scope keeps the historical path so existing habits and any
    half-finished sweep still work; the making run stages beside it. Separate
    dirs, not one mixed dir, because `analyze` sweeps whatever it finds and a
    leftover render from the other run would be analyzed under this run's
    brief.
    """
    return (
        _render_base()
        if scope == SCOPE.BOOK
        else _render_base().with_name(_render_base().name + f"-{scope}")
    )


def _parse_scope(args):
    """Pull `--scope book|making|all` out of an arg list; returns (scope, rest)."""
    scope, rest, i = SCOPE.BOOK, [], 0
    while i < len(args):
        if args[i] == "--scope":
            want = args[i + 1]
            if want == "all":
                scope = None
            elif want in SCOPE.SCOPES:
                scope = want
            else:
                raise SystemExit(
                    f"coldread: --scope must be one of "
                    f"{', '.join(SCOPE.SCOPES)}, all (got {want!r})"
                )
            i += 2
        else:
            rest.append(args[i])
            i += 1
    return scope, rest


def _parse_render_flags(args):
    bu, bm = DEFAULT_BATCH_UNDER, DEFAULT_BATCH_MAX
    i = 0
    while i < len(args):
        if args[i] == "--batch-under":
            bu = int(args[i + 1])
            i += 2
        elif args[i] == "--batch-max":
            bm = int(args[i + 1])
            i += 2
        else:
            print(f"render: ignoring unknown arg {args[i]}", file=sys.stderr)
            i += 1
    return bu, bm


def _write_batch(outname, era, members, out=None):
    if out is None:
        out = _render_base()
    parts = [
        f"<!-- batch:{era} sessions:{len(members)} -->",
        "",
        f"# Batched short sessions ({era})",
        "",
        "Several short, independent sessions are concatenated below.",
        "Analyze EACH session on its own and give one short report per session,",
        "in the order they appear. Do not blend them into a single narrative.",
        "",
    ]
    for i, (stem, _rel, sha, body, _est) in enumerate(members, 1):
        parts.append("=" * 70)
        parts.append(
            f"## SESSION {i}/{len(members)} — {era} / {stem}  (sha {sha[:12]})"
        )
        parts.append("")
        parts.append(body)
        parts.append("")
    members_hdr = " ".join(f"{rel}={sha}" for _stem, rel, sha, _b, _e in members)
    header = f"<!-- coldread-batch: {members_hdr} -->\n\n"
    (out / outname).write_text(header + "\n".join(parts), encoding="utf-8")


def _write_corpus_index(singles, batches, out=None):
    if out is None:
        out = _render_base()
    lines = [
        "# Blind cold-read staging corpus",
        "",
        "Each analyst reads ONE file below and nothing else in this repo.",
        "Batch files hold several short sessions; analyze each independently.",
        "Register results with `quirework provenance coldread record` (singles) or",
        "`record-batch` (batches).",
        "",
        "## Single-session renders",
        "",
        "| render | ~tokens | source | sha |",
        "| --- | --- | --- | --- |",
    ]
    for outname, _state, rel, sha, est in singles:
        lines.append(f"| {outname} | {est:,} | {rel} | {sha[:12]} |")
    lines += ["", "## Batches", ""]
    if not batches:
        lines.append("_none_")
    for outname, _era, toks, members in batches:
        rels = " ".join(m[1] for m in members)
        lines.append(f"### {outname}  (~{toks:,} tok, {len(members)} sessions)")
        lines.append("")
        for stem, rel, sha, _body, est in members:
            lines.append(f"- {rel}  (~{est:,} tok, sha {sha[:12]})")
        lines.append("")
        lines.append("After analysis, register the shared report:")
        lines.append("")
        lines.append("```")
        lines.append(f"quirework provenance coldread record-batch <REPORT.md> {rels}")
        lines.append("```")
        lines.append("")
    (out / "corpus-index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_render(args):
    scope, args = _parse_scope(args)
    batch_under, batch_max = _parse_render_flags(args)
    out = _render_out(scope or SCOPE.BOOK)
    manifest = load_manifest()
    unsealed = unsealed_set()
    out.mkdir(parents=True, exist_ok=True)

    singles = []  # (outname, state, rel, sha, est)
    smalls = {}  # era -> [(stem, rel, sha, body, est)]
    for path, era in iter_sources(scope):
        state, rel, sha = classify(path, manifest, unsealed)
        if state == "hit":
            continue
        body, _ = render_source(path)
        if len(body.strip()) < MIN_RENDER_CHARS:
            continue  # stub session
        est = len(body) // 4
        sealed = path.resolve() not in unsealed
        if batch_under and sealed and est < batch_under:
            smalls.setdefault(era, []).append((path.stem, rel, sha, body, est))
        else:
            outname = f"{era}__{path.stem}.md"
            header = f"<!-- coldread-source: {rel} {sha} -->\n\n"
            (out / outname).write_text(header + body, encoding="utf-8")
            singles.append((outname, state, rel, sha, est))

    batches = []  # (outname, era, tokens, members)
    for era, items in smalls.items():
        items.sort(key=lambda x: -x[4])  # largest-first bin packing
        bins = []  # [tokens, [members]]
        for it in items:
            for b in bins:
                if b[0] + it[4] <= batch_max:
                    b[0] += it[4]
                    b[1].append(it)
                    break
            else:
                bins.append([it[4], [it]])
        for n, (toks, members) in enumerate(bins, 1):
            outname = f"{era}__batch-{n:02d}.md"
            _write_batch(outname, era, members, out)
            batches.append((outname, era, toks, members))

    _write_corpus_index(singles, batches, out)
    batched = sum(len(m) for *_, m in batches)
    print(
        f"staged {len(singles)} single render(s) + {len(batches)} batch(es) "
        f"covering {batched} short session(s) -> {out}"
    )
    print(f"index: {out / 'corpus-index.md'}")
    print(
        f"verify blindness before spawning: quirework provenance coldread verify-blind {out}"
    )


def cmd_verify_blind(args):
    """Scan renders for project-framing fingerprints.

    Local-session hits are a hard FAIL: project guidance is injected there, so
    a fingerprint means filtering missed an injection. Web (era-a) hits are a
    REVIEW warning, not a failure: those transcripts predate and are the source
    of the guidance, so the phrases are usually the human coining the framing
    live (evidence), not contamination.
    """
    target = Path(args[0]).expanduser() if args else _render_base()
    files = [target] if target.is_file() else sorted(target.glob("*.md"))
    if not files:
        print(f"no renders found in {target}", file=sys.stderr)
        return 1
    fails = reviews = 0
    for f in files:
        if f.name == "corpus-index.md":
            continue
        low = f.read_text(encoding="utf-8", errors="replace").lower()
        hits = sorted({m for m in FLT.CONTAMINATION_MARKERS if m.lower() in low})
        if not hits:
            continue
        era = f.name.split("__", 1)[0]  # render names are <era>__<stem>.md
        if era == "a-web":
            reviews += 1
            print(
                f"REVIEW {f.name}: web dialogue contains {hits} (likely evidence; eyeball)"
            )
        else:
            fails += 1
            print(f"FAIL   {f.name}: injected framing leaked: {hits}")
    if fails:
        print(f"FAIL: {fails} local-session render(s) carry leaked project framing")
        return 1
    msg = f"OK: no injection leaks in {len(files)} render(s)"
    if reviews:
        msg += f"; {reviews} web render(s) flagged for manual review"
    print(msg)
    return 0


def _model_id(cli_model=None):
    """The provenance id recorded for a run selected as `cli_model`.

    The default alias resolves to the configured MODEL; any other selection is
    recorded verbatim — the id records what the operator *selected*, the
    strongest attribution available here ("Model metadata is not
    trustworthy"). Provenance only: the model never affects cache validity
    (see `is_hit`)."""
    return (
        _options().MODEL
        if cli_model in (None, _options().CLI_MODEL, _options().MODEL)
        else cli_model
    )


def cmd_record(args, model=None, persona=None):
    if len(args) != 2:
        print(
            "usage: quirework provenance coldread record SOURCE_PATH REPORT_FILE",
            file=sys.stderr,
        )
        return 2
    src, report = args
    path = Path(src)
    if not path.is_absolute():
        path = current_project().root / src
    rel = str(source_reference(path, current_project()))
    sha = sha256_file(path)
    scope = SCOPE.scope_of_path(path)
    _settings().REPORTS.mkdir(parents=True, exist_ok=True)
    dest = _settings().REPORTS / report_name(
        sha, scope, filter_version_for(path), persona
    )
    shutil.copyfile(report, dest)
    with manifest_lock():
        manifest = load_manifest()
        era = _era_for_rel(rel)
        _persona_entries(manifest, persona)[rel] = {
            "sha256": sha,
            "render_sha256": render_sha256(path),
            "era": era,
            "scope": scope,
            "report": f"reports/{dest.name}",
            "filter_version": filter_version_for(path),
            "prompt_version": PROMPT_VERSIONS[scope],
            "model": _model_id(model),
            "analyzed": datetime.date.today().isoformat(),
            **({"persona": persona} if persona else {}),
        }
        save_manifest(manifest)
    print(
        f"recorded {rel}\n  -> {dest.relative_to(current_project().path('provenance.coldread', '.quirework/provenance/coldread'))}"
    )
    return 0


def cmd_record_batch(args, model=None, persona=None):
    if len(args) < 2:
        print(
            "usage: quirework provenance coldread record-batch REPORT_FILE SOURCE...",
            file=sys.stderr,
        )
        return 2
    report, srcs = args[0], args[1:]
    paths = [
        (Path(s) if Path(s).is_absolute() else current_project().root / s) for s in srcs
    ]
    shas = {str(source_reference(p, current_project())): sha256_file(p) for p in paths}
    batch_id = hashlib.sha256("".join(sorted(shas.values())).encode()).hexdigest()[:12]
    # A batch is rendered within one era *and* one scope (see `cmd_render`), so
    # the members agree; take the scope from the first and tag the file with it.
    scope = SCOPE.scope_of_path(paths[0])
    tag = "" if scope == SCOPE.BOOK else f"-{scope}"
    # A batch is one era, so one reader and one recipe version (see above).
    version = filter_version_for(paths[0])
    _settings().REPORTS.mkdir(parents=True, exist_ok=True)
    variant = "-persona-" + persona["sha256"] if persona else ""
    dest = (
        _settings().REPORTS
        / f"batch-{batch_id}-f{version}p{PROMPT_VERSIONS[scope]}{tag}{variant}.md"
    )
    shutil.copyfile(report, dest)
    with manifest_lock():
        manifest = load_manifest()
        today = datetime.date.today().isoformat()
        for rel, sha in shas.items():
            _persona_entries(manifest, persona)[rel] = {
                "sha256": sha,
                "render_sha256": render_sha256(current_project().root / rel),
                "era": _era_for_rel(rel),
                "scope": scope,
                "report": f"reports/{dest.name}",
                "batch_id": batch_id,
                "filter_version": version,
                "prompt_version": PROMPT_VERSIONS[scope],
                "model": _model_id(model),
                "analyzed": today,
                **({"persona": persona} if persona else {}),
            }
        save_manifest(manifest)
    print(
        f"recorded batch {batch_id} ({len(shas)} sources)\n  -> {dest.relative_to(current_project().path('provenance.coldread', '.quirework/provenance/coldread'))}"
    )
    return 0


def _load_brief(scope=SCOPE.BOOK):
    text = _settings().ANALYST_PROMPTS[scope].read_text(encoding="utf-8")
    # Everything before the first '---' rule is maintainer-facing preamble (it
    # names the meta-purpose of the analysis) and must NOT reach the analyst.
    # Send only the analyst-facing brief after it.
    parts = re.split(r"(?m)^---\s*$", text, maxsplit=1)
    return (parts[1] if len(parts) > 1 else text).strip()


def _parse_render_header(path):
    """Return {kind, members:[(rel,sha)], body} from a staged render's header."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return None
    head, body = lines[0], "\n".join(lines[1:]).lstrip("\n")
    m = re.match(r"<!-- coldread-source:\s+(\S+)\s+(\S+)\s+-->", head)
    if m:
        return {"kind": "single", "members": [(m.group(1), m.group(2))], "body": body}
    m = re.match(r"<!-- coldread-batch:\s+(.*?)\s*-->", head)
    if m:
        members = []
        for tok in m.group(1).split():
            rel, _, sha = tok.partition("=")
            members.append((rel, sha))
        return {"kind": "batch", "members": members, "body": body}
    return None


def _blindness_ok(report):
    """Gate: a report is usable only if its analyst declared no prior context.

    Reads the answer on the BLINDNESS CHECK line itself (not a keyword scan of
    the whole head, which mistook a "yes" anywhere for a contamination claim).
    """
    m = re.search(r"blindness check\b\s*:?\s*\n?\s*(.+)", report, flags=re.IGNORECASE)
    if not m:
        return False, "no blindness-check line"
    answer = m.group(1).strip()[:120].lower()
    if re.match(r"\(?\s*(yes|partial)\b", answer):
        return False, "analyst declared prior context"
    if re.search(
        r"\b(no|none|no prior|no context|no knowledge|no background|uncontaminated)\b",
        answer,
    ):
        return True, "clean"
    return False, "blindness declaration unclear"


def cmd_analyze(args):
    import argparse
    from quirework.sops import personas

    scope, args = _parse_scope(args)
    stage = _render_out(scope or SCOPE.BOOK)
    parser = argparse.ArgumentParser(prog="quirework provenance coldread analyze")
    parser.add_argument("--only")
    parser.add_argument("--model", default=_options().CLI_MODEL)
    parser.add_argument("--persona")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--oldest", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=int, default=ANALYZE_TIMEOUT_S)
    selected = parser.parse_args(args)
    only, model, dry, oldest = (
        selected.only,
        selected.model,
        selected.dry_run,
        selected.oldest,
    )
    limit, tmo = selected.limit, selected.timeout
    if (limit is not None and limit < 0) or tmo <= 0:
        raise ValueError("limit must be nonnegative and timeout must be positive")
    persona = (
        personas.resolve(selected.persona) if selected.persona is not None else None
    )
    if persona and persona["profile"]["id"] == personas._options().DEFAULT:
        raise ValueError(
            "an independent authorship analyst cannot use the co-author persona"
        )

    renders = sorted(p for p in stage.glob("*.md") if p.name != "corpus-index.md")
    if oldest:
        # Walk the project's history in order, so an incremental sweep covers a
        # contiguous stretch of the arc rather than an alphabetical scatter of
        # it. Undated renders sort last, keeping the era prefix as the fallback.
        renders.sort(key=lambda p: (render_date(p) or "9999", p.name))
    if only:
        renders = [p for p in renders if only in p.name]
    if not renders:
        print(f"no renders to analyze in {stage}; run `render` first", file=sys.stderr)
        return 1

    # One brief per scope, loaded once. The brief a render is analyzed under is
    # taken from the render's own members below, not from the run's flag: a
    # render staged under one scope and analyzed after the session changed scope
    # would otherwise be asked the wrong question, silently.
    briefs = {s: _load_brief(s) for s in SCOPE.SCOPES}
    _reports_tmp().mkdir(parents=True, exist_ok=True)
    done = 0
    # Circuit breaker: a run of consecutive `claude` failures is not this
    # render's problem, it is the account's (the 5-hour usage cap, observed
    # 2026-07-28: one capped run churned through 106 renders, every one exit 1).
    # Abort the sweep instead; the cache resumes exactly where it stopped.
    consec_fail = 0
    MAX_CONSEC_FAIL = 5
    for rp in renders:
        if consec_fail >= MAX_CONSEC_FAIL:
            print(
                f"\nSTOP: {consec_fail} consecutive claude failures — likely "
                f"the usage cap, not the renders. Re-run "
                f"`quirework provenance coldread analyze` when the window resets; "
                f"recorded reports are cache hits and are skipped."
            )
            return 1
        info = _parse_render_header(rp)
        if not info:
            print(f"SKIP {rp.name}: no coldread header (re-run render)")
            continue
        manifest = load_manifest()
        present = all(
            (current_project().root / rel).exists() for rel, _ in info["members"]
        )
        cached = present and all(
            is_hit(
                manifest,
                rel,
                sha256_file(current_project().root / rel),
                current_project().root / rel,
                persona=persona,
            )
            for rel, _ in info["members"]
        )
        persist_revalidations(manifest)
        if cached:
            print(f"cached  {rp.name} (skip)", flush=True)
            continue
        if limit is not None and done >= limit:
            print(f"limit {limit} reached; stopping")
            break
        if dry:
            print(
                f"would analyze {rp.name} [{info['kind']}, {len(info['members'])} source(s)]"
            )
            done += 1
            continue

        rscope = SCOPE.scope_of_path(info["members"][0][0])
        brief = briefs[rscope]
        prompt = (
            brief.replace("{TRANSCRIPT}", info["body"])
            if "{TRANSCRIPT}" in brief
            else brief + "\n\nTRANSCRIPT(S) TO ANALYZE:\n\n" + info["body"]
        )
        if persona:
            prompt = personas.compose(prompt, persona)
        print(
            f"analyzing {rp.name} [{info['kind']}, {len(info['members'])} src] "
            f"via claude -p --model {model} ...",
            flush=True,
        )
        try:
            sd = seat_dir()
            res = subprocess.run(
                confine(
                    [
                        "claude",
                        "-p",
                        "--model",
                        model,
                        *BLIND_SETTINGS,
                        *NO_SETTING_SOURCES,
                        *MIN_TOOLS,
                        *NO_MCP,
                    ],
                    sd,
                    runners=["claude"],
                ),
                input=prompt,
                text=True,
                capture_output=True,
                cwd=str(sd),
                timeout=tmo,
            )
        except subprocess.TimeoutExpired:
            print(f"  TIMEOUT after {tmo}s -- skipped", flush=True)
            continue
        if res.returncode != 0:
            # A capped/errored run may say nothing on stderr; show stdout's
            # tail too so the reason is not a blank.
            why = (res.stderr.strip() or res.stdout.strip())[:200]
            print(f"  claude exited {res.returncode}: {why} -- skipped", flush=True)
            consec_fail += 1
            continue
        report = res.stdout.strip()
        if not report:
            print("  empty report -- skipped", flush=True)
            consec_fail += 1
            continue
        consec_fail = 0

        report_path = _reports_tmp() / (rp.stem + ".report.md")
        report_path.write_text(report + "\n", encoding="utf-8")
        ok, reason = _blindness_ok(report)
        if not ok:
            bad = _reports_tmp() / (rp.stem + ".CONTAMINATED.md")
            report_path.replace(bad)
            print(
                f"  REFUSED ({reason}) -- quarantined {bad.name}, NOT recorded",
                flush=True,
            )
            continue

        rels = [rel for rel, _ in info["members"]]
        if info["kind"] == "single":
            cmd_record([rels[0], str(report_path)], model=model, persona=persona)
        else:
            cmd_record_batch([str(report_path), *rels], model=model, persona=persona)
        done += 1

    print(
        f"\n{'would analyze' if dry else 'analyzed and recorded'} {done} render(s) this run"
    )
    return 0


# Paths that ARE the document (edits to these mean the session did authorship
# work); everything else is tooling/config/provenance = "meta".
# NB: the illustrations directory is deliberately excluded -- figures are
# mechanical execution, not authorship. Counting them as "doc" auto-keeps
# plumbing sessions and hides them from the intelligent read.

# exercises/ and cases/ are the reader-facing companion collections — tracked,
# gate-checked canon companions, so editing them is authorship work, not
# plumbing (author ruling; the docstring above always said so).


def _repo_relative(fp):
    """A recorded edit path, made relative to whichever checkout it names.

    A session records absolute paths, and the checkout it ran in is not
    necessarily the one this process was started from: with a `git worktree` in
    play the same file is `<main>/docs/canon/x.qmd` in one session and
    `<worktree>/docs/canon/x.qmd` in the next. Stripping only `ROOT` left the
    other checkout's paths absolute, so every one of them classified `meta` --
    a whole worktree's canon work reading as plumbing in triage and the catalog.
    Longest checkout first, because one checkout's path can be a string prefix
    of another's.
    """
    from quirework.provenance import worktrees

    checkouts = sorted(
        (str(p) for p in worktrees.checkouts(current_project().root)),
        key=len,
        reverse=True,
    )
    for root in checkouts:
        if fp.startswith(root + "/"):
            return fp[len(root) + 1 :]
    return fp.lstrip("/")


def _classify_edit(fp):
    s = _repo_relative(fp).lower()
    if s.startswith(_options()._DOC_PREFIXES) or s.startswith(_options()._DOC_DIRS):
        return "doc"
    return "meta"


def _edit_targets(jsonl_path):
    """Count doc vs meta file edits in a session, and tally edited basenames."""
    doc = meta = 0
    files = {}
    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "assistant":
                continue
            content = (obj.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                    continue
                if b.get("name") not in ("Edit", "Write", "NotebookEdit"):
                    continue
                inp = b.get("input") or {}
                fp = inp.get("file_path") or inp.get("notebook_path")
                if not fp:
                    continue
                if _classify_edit(fp) == "doc":
                    doc += 1
                else:
                    meta += 1
                bn = os.path.basename(fp)
                files[bn] = files.get(bn, 0) + 1
    return doc, meta, files


def cmd_triage(args):
    """Mechanical relevance prioritizer over local sessions (free, no model calls).

    A session that edits document files (the manuscript, the spec, retained
    analysis, scratch notes, the companion collections) is 'keep'.
    A session that does not is 'review':
    it might still be a substantive discussion that only touched tooling, so it
    needs an intelligent read before any removal -- the mechanics alone never
    drop a file.

    'keep' is a PRIORITIZATION signal, not a final verdict: confirm the
    top-edited column shows canonical content before trusting it, since a single
    incidental edit near the essay can auto-keep an otherwise plumbing session.
    """
    from quirework.provenance import catalog as CAT
    from quirework.provenance import codexfilter as CODEX_FLT

    review_only = "--review-list" in args
    show_all = "--all" in args
    if not show_all:
        untracked = set(_untracked_cc() + _untracked_agy() + _untracked_codex())
    rows = []
    for p in sorted(
        current_project()
        .path("provenance.claude_sessions", ".quirework/provenance/cc-sessions")
        .glob("*.jsonl")
    ):
        rel = str(source_reference(p, current_project()))
        body, _ = FLT.filter_session(str(p))
        if len(body.strip()) < MIN_RENDER_CHARS:
            continue
        doc, meta, files = _edit_targets(p)
        verdict = "keep" if doc > 0 else "review"
        rows.append((rel, len(body) // 4, doc, meta, verdict, files))
    for p in sorted(
        current_project()
        .path("provenance.agy_sessions", ".quirework/provenance/agy-sessions")
        .glob("*.jsonl")
    ):
        rel = str(source_reference(p, current_project()))
        body, _ = CAT._agy_render(p)
        if len(body.strip()) < MIN_RENDER_CHARS:
            continue
        doc, meta, files = CAT._agy_edit_targets(p)
        verdict = "keep" if doc > 0 else "review"
        rows.append((rel, len(body) // 4, doc, meta, verdict, files))
    for p in sorted(
        current_project()
        .path("provenance.codex_sessions", ".quirework/provenance/codex-sessions")
        .glob("*.jsonl")
    ):
        rel = str(source_reference(p, current_project()))
        body, _ = CODEX_FLT.filter_session(p)
        if len(body.strip()) < MIN_RENDER_CHARS:
            continue
        doc, meta, files = CODEX_FLT.edit_targets(p, _classify_edit)
        verdict = "keep" if doc > 0 else "review"
        rows.append((rel, len(body) // 4, doc, meta, verdict, files))
    short_untracked = []
    if not show_all:
        kept_ids = {r[0] for r in rows}
        short_untracked = sorted(untracked - kept_ids)
        rows = [r for r in rows if r[0] in untracked]
    rows.sort(key=lambda r: -r[1])

    if review_only:
        for rel, _t, _d, _m, verdict, _f in rows:
            if verdict == "review":
                print(rel)
        return 0

    if not rows:
        if not show_all:
            if short_untracked:
                print(
                    f"nothing above the {MIN_RENDER_CHARS}-char render floor, but "
                    f"{len(short_untracked)} untracked session(s) are pending below "
                    "it -- these can still be a real stub, a crashed run, or "
                    "evaluation-seat exhaust; read each before curating:"
                )
                for rel in short_untracked:
                    print(f"  {rel}")
            else:
                print(
                    "nothing pending triage: every session on disk is already "
                    "tracked (--all lists them anyway)."
                )
        else:
            print("no sessions to triage.")
        return 0

    w = max(len(r[0]) for r in rows)
    print("=== session relevance triage (mechanical prioritizer: by files edited) ===")
    if not show_all:
        print("(untracked sessions pending triage only; --all includes tracked ones)")
    print(
        f"{'verdict':7} {'~tok':>7} {'doc':>4} {'meta':>5}  {'session':{w}}  top edited"
    )
    for rel, toks, doc, meta, verdict, files in rows:
        top = ", ".join(
            f"{n}x{c}" for n, c in sorted(files.items(), key=lambda x: -x[1])[:3]
        )
        print(
            f"{verdict:7} {toks:>7,} {doc:>4} {meta:>5}  {rel:{w}}  {top or '(no edits)'}"
        )
    nkeep = sum(r[4] == "keep" for r in rows)
    nrev = sum(r[4] == "review" for r in rows)
    print(
        f"\nkeep={nkeep} (edited the document)  review={nrev} (no doc edits -> need an "
        f"intelligent read before removal)"
    )
    print(
        "'keep' is a prioritization signal, not a final verdict: confirm the top-edited"
    )
    print("column shows canonical content (docs/canon/**, _map.md, the legacy spec),")
    print("not just README/AGENTS/SOP plumbing -- a plumbing-only keep is removed too.")
    print("web/era-a sessions are not shown; judge those manually.")
    if short_untracked:
        print(
            f"\n{len(short_untracked)} more untracked session(s) fell below the "
            f"{MIN_RENDER_CHARS}-char render floor and are NOT in the table above "
            "-- still pending, read each before curating:"
        )
        for rel in short_untracked:
            print(f"  {rel}")
    return 0


USAGE = """usage: quirework provenance coldread [SUBCOMMAND] [OPTIONS]

Blind authorship cold-read: a content-addressed cache over per-source analyst
reports. The cache key folds in the source bytes, the filter recipe, the prompt
and the model, so changing any of them misses deliberately -- a report is never
silently reused under a different recipe.

subcommands (default: status):
  status                    per-source cache state (hit / MISS / UNSEALED)
  triage                    mechanical keep/review prioritizer over the local
                            cc-, agy-, and codex-sessions (free, no model calls)
      --review-list           print only the 'review' sessions
      --all                   include already-tracked sessions (default:
                              only untracked ones pending triage)
  render                    write the filtered transcripts the analysts read
      --batch-under N         batch sources under N tokens
      --batch-max N           cap a batch at N tokens
  verify-blind              scan renders for project-framing fingerprints
                            (a cc-session hit is a hard failure)
  analyze                   fan out one blind `claude -p` per render
      --only SUBSTR           restrict to renders matching SUBSTR
      --model M               analyst model (default: the configured one)
      --persona ID            reusable analyst profile; separate cache namespace
  status --persona ID         inspect that profile's cache without changing legacy results
      --limit N               analyze at most N renders
      --oldest                walk the corpus oldest-first (incremental sweeps
                              then cover a contiguous stretch of the arc)
      --timeout S             per-analyst timeout in seconds
      --dry-run               print what would run, run nothing
  record SOURCE REPORT      cache one report against its source
  record-batch REPORT SRC.. cache one batched report against its sources

options:
  -h, --help                show this help message and exit
"""


# --------------------------------------------------------------------------- #
# discards -- the authority a transcript cannot record
# --------------------------------------------------------------------------- #
# A blind analyst reads a session and judges who drove it. But work is also
# rejected *after* the session, in git, and that leaves no trace in any
# transcript: the author let an agent run with little review, several commits
# landed, and he discarded them with a hard reset. No filter version can fix
# this; the transcript records the talk, not what became of the artifact.
#
# WHAT THIS ACTUALLY FOUND, recorded because it is a negative result and the
# temptation is to quietly keep the tool and drop the finding. Over this corpus
# it flags 28 of 495 commits (6%) across 16 sessions -- and those sessions'
# reports already read as human-led ("The human drove the substance
# throughout", "human-orchestrated throughout"). It does NOT surface sessions
# where an analyst over-credited the AI, which is what it was built to catch.
#
# Worse for the premise: of the 15 canon commits discarded on 2026-07-12, only
# 3 are attributable to a session still in the corpus. The rest were made by
# sessions since curated out, or outside a session entirely. So the detector
# cannot see the very event that motivated it. Keep it as a cheap check, and
# do not mistake a clean run for evidence that nothing was thrown away.
#
# (An earlier attribution pass matched any SHA appearing in a transcript and
# scored 7e133544 with 13 discarded commits. It created none -- it was a
# session *reviewing* another's commits and quoting their SHAs. Mentioning is
# not making, which is why only `git commit`'s own echo is counted below.)
#
# So this reports the outcome beside the report, and deliberately does no more.
# It is a DISCREPANCY DETECTOR, not a weight: it flags sessions worth a second
# look and never adjusts a verdict. Two reasons for that restraint. Handing an
# analyst "12 of 15 commits were discarded" leads the witness -- the fact
# carries its interpretation with it. And letting the *summarizer* reweight on
# it hands the corpus's best-documented failure mode (an AI narrating the
# collaboration as more human-directed than the transcript supports) a lever
# pointing straight at the flattering conclusion.
#
# Read the numbers as a question, never as an answer, and mind the caveats:
#   * survival != endorsement. A commit in main may mean it was approved, or
#     that nobody looked. A discard may be rejection, supersession, or an
#     abandoned branch.
#   * volume != weight. A one-word "no" that stops a chapter outweighs fifteen
#     discarded commits and shows up here as nothing.
#   * it only sees authority exercised AFTER a commit. Most correction happens
#     in the dialogue, before anything is committed.

# `git commit` echoes "[main 1a2b3c4] subject", which is proof the commit was
# MADE here -- unlike a bare SHA, which a session that merely *reviews* commits
# quotes constantly.
_COMMIT_OUT = re.compile(
    r"\[(?:detached HEAD|[\w./-]+)\s+([0-9a-f]{7,40})\]\s+([^\\\"\n]+)"
)


def _main_index():
    """``(short-sha prefixes, subjects)`` for everything reachable from main."""

    def run(fmt):
        try:
            return subprocess.run(
                [
                    "git",
                    "-C",
                    str(current_project().root),
                    "log",
                    "--format=" + fmt,
                    "main",
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines()
        except Exception:
            return []

    shas = {h[:7] for h in run("%H")}
    return shas, {s.strip() for s in run("%s")}


def session_commits(path):
    """``[(sha, subject)]`` for commits created in this session's transcript."""
    out, seen = [], set()
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return out
    for sha, subj in _COMMIT_OUT.findall(text):
        subj = subj.strip().rstrip("\\n").strip()
        if sha[:7] in seen or not subj:
            continue
        seen.add(sha[:7])
        out.append((sha[:7], subj))
    return out


def cmd_discards(args):
    """Per session: commits made, how many survive in main, how many were cut."""
    threshold = 1
    for i, a in enumerate(args):
        if a == "--min" and i + 1 < len(args):
            threshold = int(args[i + 1])
    main_shas, main_subjects = _main_index()
    if not main_shas:
        print("discards: cannot read `main`; is this a git repo?", file=sys.stderr)
        return 2
    manifest = load_manifest()
    rows = []
    # Whole corpus, both scopes: this counts commits a session made and how many
    # survive, which is as true of a session that built an instrument as of one
    # that wrote a chapter. Scope decides which cold-read *reads* a session, not
    # which tools may count it.
    for path, era in iter_sources(None):
        if path.suffix != ".jsonl":
            continue
        made = session_commits(path)
        if not made:
            continue
        # The project folds follow-up fixes into the previous commit (soft reset
        # + re-commit), so a KEPT commit routinely changes SHA. Matching the
        # subject as well is what separates a rewrite from a rejection: over
        # this corpus, SHA-only scored 150 discards where 47 were real.
        gone = [
            (s, subj)
            for s, subj in made
            if s not in main_shas and subj not in main_subjects
        ]
        rel = str(source_reference(path, current_project()))
        rows.append(
            (
                len(gone),
                len(made),
                path.stem[:8],
                source_date(path),
                (manifest["sources"].get(rel) or {}).get("report", "-"),
                gone,
            )
        )
    rows.sort(key=lambda r: (-r[0], r[3]))

    flagged = [r for r in rows if r[0] >= threshold]
    print(f"{'session':10} {'date':11} {'made':>5} {'cut':>4}  report")
    print("-" * 74)
    for gone_n, made_n, sess, date, report, _ in flagged:
        print(f"{sess:10} {date:11} {made_n:>5} {gone_n:>4}  {report.split('/')[-1]}")
    tot_made = sum(r[1] for r in rows)
    tot_gone = sum(r[0] for r in rows)
    print(
        f"\n{len(rows)} session(s) committed; {tot_made} commit(s) made, "
        f"{tot_gone} never reached main ({tot_gone / tot_made:.0%})."
    )
    print(f"{len(flagged)} session(s) at or above the --min {threshold} threshold.")
    if flagged:
        print(
            "\nThis is a QUESTION, not an adjustment. Where a session's report credits\n"
            "the AI with substance that was later cut, the report is not wrong about\n"
            "the transcript -- the transcript could not see the reset. Read the two\n"
            "together; do not silently reweight the verdict."
        )
    return 0


def _analyze_locked(args):
    """`analyze` under the sweep lock: one at a time, and say so if not."""
    try:
        with sweep_lock():
            return cmd_analyze(args)
    except AlreadyRunning as e:
        print(
            f"analyze: another cold-read sweep is already running ({e}).\n"
            f"  Nothing shards the work -- two sweeps walk the same renders in\n"
            f"  the same order, pay for the same analysts, and clobber each\n"
            f"  other's manifest entries. Wait for it, or stop it first.\n"
            f"  Lock: {_settings().SWEEP_LOCK}",
            file=sys.stderr,
        )
        return 1


def main(argv):
    cmds = {
        "status": cmd_status,
        "triage": cmd_triage,
        "render": cmd_render,
        "verify-blind": cmd_verify_blind,
        "analyze": _analyze_locked,
        "record": cmd_record,
        "record-batch": cmd_record_batch,
        "discards": cmd_discards,
    }
    # Checked across the whole argv, not just argv[0]: every subcommand parses
    # flags with an "ignore unknown arg" loop, so a `coldread analyze --help`
    # that reached the subcommand would shrug off the flag and *run the
    # analysis*. There is no positional here that can legitimately be `-h`.
    if any(a in ("-h", "--help") for a in argv):
        print(USAGE, end="")
        return 0
    cmd = argv[0] if argv else "status"
    if cmd not in cmds:
        print(
            f"unknown command: {cmd}\nsubcommands: {', '.join(cmds)}", file=sys.stderr
        )
        return 2
    try:
        return cmds[cmd](argv[1:]) or 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"coldread: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
