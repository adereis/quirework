"""The cold-read cache's validity semantics.

These gate whether paid blind analysis is skipped or silently redone, so
they pin the key exactly: source bytes + filter version + prompt version,
with a filter bump revalidating against the recorded render hash first --
a recipe change that did not alter what the analyst read must not re-buy
the report (the v2->v3 bump nominally invalidated 217 sources and had
actually changed 64).
The model is provenance, never validity — a mixed-model corpus (Sonnet for
most, Opus where depth earns its tokens) must be stable, not converge back
to the default model by silent re-analysis.
"""

from conftest import patch_project_path
from conftest import project_at, patch_setting
from quirework.runs import commands as run_commands
from quirework.provenance import coldread
from quirework.provenance import scope as SCOPE


def _manifest(tmp_path, entry):
    (tmp_path / "reports").mkdir(exist_ok=True)
    (tmp_path / entry["report"]).write_text("a report\n")
    return {"sources": {"src.jsonl": entry}}


def _entry(**over):
    e = {
        "sha256": "abc",
        # the recipe identity, not the bare reader version: the redaction
        # policy is part of what the analyst saw, so it is part of the key
        "filter_version": coldread.filter_version_for("src.jsonl"),
        "prompt_version": coldread.PROMPT_VERSIONS[SCOPE.WORK],
        "model": coldread._options().MODEL,
        "report": "reports/r.md",
    }
    e.update(over)
    return e


def test_hit_when_sha_filter_prompt_match(tmp_path, monkeypatch):
    patch_project_path(monkeypatch, coldread, "provenance.coldread", tmp_path)
    m = _manifest(tmp_path, _entry())
    assert coldread.is_hit(m, "src.jsonl", "abc")


def test_stale_on_sha_filter_or_prompt_change(tmp_path, monkeypatch):
    patch_project_path(monkeypatch, coldread, "provenance.coldread", tmp_path)
    assert not coldread.is_hit(_manifest(tmp_path, _entry()), "src.jsonl", "OTHER")
    assert not coldread.is_hit(
        _manifest(tmp_path, _entry(filter_version="an-older-recipe")),
        "src.jsonl",
        "abc",
    )
    assert not coldread.is_hit(
        _manifest(
            tmp_path, _entry(prompt_version=coldread.PROMPT_VERSIONS[SCOPE.WORK] - 1)
        ),
        "src.jsonl",
        "abc",
    )


def test_a_different_model_is_not_stale(tmp_path, monkeypatch):
    """The regression that made a mixed corpus unstable: an Opus-read source
    counted as stale under the Sonnet default, so the next sweep silently
    re-analyzed and OVERWROTE the Opus report. Model is provenance only."""
    patch_project_path(monkeypatch, coldread, "provenance.coldread", tmp_path)
    m = _manifest(tmp_path, _entry(model="opus"))
    assert coldread.is_hit(m, "src.jsonl", "abc")


def test_missing_report_file_is_a_miss(tmp_path, monkeypatch):
    patch_project_path(monkeypatch, coldread, "provenance.coldread", tmp_path)
    m = _manifest(tmp_path, _entry())
    (tmp_path / "reports" / "r.md").unlink()
    assert not coldread.is_hit(m, "src.jsonl", "abc")


def test_model_id_records_the_selection():
    assert coldread._model_id(None) == coldread._options().MODEL
    assert (
        coldread._model_id(coldread._options().CLI_MODEL) == coldread._options().MODEL
    )
    assert coldread._model_id("opus") == "opus"


# --------------------------------------------------------------------------- #
# revalidation across a filter bump
# --------------------------------------------------------------------------- #
def _web_source(tmp_path, text="# A dialogue\n\nHuman said a thing.\n"):
    """A web-era source: read straight off disk, so no filter can alter it."""
    p = tmp_path / "conv.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_filter_bump_revalidates_when_the_render_is_unchanged(tmp_path, monkeypatch):
    """The whole point: a filter change that did not reach this render must not
    cost a re-analysis. Era A is the pure case -- a web .md is read as-is."""
    patch_project_path(monkeypatch, coldread, "provenance.coldread", tmp_path)
    src = _web_source(tmp_path)
    m = _manifest(
        tmp_path,
        _entry(
            filter_version="an-older-recipe", render_sha256=coldread.render_sha256(src)
        ),
    )
    assert coldread.is_hit(m, "src.jsonl", "abc", src)
    # revalidated in place, and the manifest is marked for saving
    assert m["sources"]["src.jsonl"]["filter_version"] == coldread.filter_version_for(
        "src.jsonl"
    )
    assert m.get("_dirty")


def test_filter_bump_still_misses_when_the_render_changed(tmp_path, monkeypatch):
    patch_project_path(monkeypatch, coldread, "provenance.coldread", tmp_path)
    src = _web_source(tmp_path)
    m = _manifest(
        tmp_path,
        _entry(
            filter_version="an-older-recipe", render_sha256="a-hash-of-some-other-text"
        ),
    )
    assert not coldread.is_hit(m, "src.jsonl", "abc", src)
    assert m["sources"]["src.jsonl"]["filter_version"] != coldread.filter_version_for(
        "src.jsonl"
    )


def test_filter_bump_misses_when_no_render_hash_was_recorded(tmp_path, monkeypatch):
    """Pre-migration entries cannot prove their render; they miss, as before."""
    patch_project_path(monkeypatch, coldread, "provenance.coldread", tmp_path)
    src = _web_source(tmp_path)
    m = _manifest(tmp_path, _entry(filter_version="an-older-recipe"))
    assert not coldread.is_hit(m, "src.jsonl", "abc", src)


def test_a_changed_source_never_revalidates(tmp_path, monkeypatch):
    """The render hash is a second gate, never a bypass of the source hash."""
    patch_project_path(monkeypatch, coldread, "provenance.coldread", tmp_path)
    src = _web_source(tmp_path)
    m = _manifest(
        tmp_path,
        _entry(
            filter_version="an-older-recipe", render_sha256=coldread.render_sha256(src)
        ),
    )
    assert not coldread.is_hit(m, "src.jsonl", "DIFFERENT-SOURCE", src)


def test_source_date_reads_the_data_not_the_mtime(tmp_path):
    """Ordering must not depend on when a session was mirrored to disk."""
    j = tmp_path / "s.jsonl"
    j.write_text(
        '{"type":"user","timestamp":"2026-06-13T07:27:06.656Z"}\n'
        '{"type":"user","timestamp":"2026-06-14T09:00:00.000Z"}\n',
        encoding="utf-8",
    )
    assert coldread.source_date(j) == "2026-06-13"
    assert coldread.source_date(tmp_path / "absent.jsonl") == ""


# --------------------------------------------------------------------------- #
# cross-process locking
# --------------------------------------------------------------------------- #
def test_a_second_sweep_refuses_instead_of_racing(tmp_path, monkeypatch, capsys):
    """Nothing shards the work -- two sweeps walk the same renders in the same
    order, pay for the same analysts, and clobber each other's entries."""
    patch_project_path(monkeypatch, coldread, "provenance.coldread", tmp_path)
    patch_setting(
        monkeypatch, coldread, "_settings", "SWEEP_LOCK", tmp_path / ".sweep.lock"
    )
    with coldread.sweep_lock():
        rc = coldread._analyze_locked(["--dry-run"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "already running" in err


def test_the_sweep_lock_is_released_on_exit(tmp_path, monkeypatch):
    patch_project_path(monkeypatch, coldread, "provenance.coldread", tmp_path)
    patch_setting(
        monkeypatch, coldread, "_settings", "SWEEP_LOCK", tmp_path / ".sweep.lock"
    )
    with coldread.sweep_lock():
        pass
    with coldread.sweep_lock():  # re-acquirable, so no leaked hold
        pass


def test_manifest_lock_does_not_deadlock_against_the_sweep_lock(tmp_path, monkeypatch):
    """They are separate files on purpose: `analyze` holds the sweep lock for
    its whole run and then calls `record`, which takes the manifest lock."""
    patch_project_path(monkeypatch, coldread, "provenance.coldread", tmp_path)
    patch_setting(
        monkeypatch, coldread, "_settings", "SWEEP_LOCK", tmp_path / ".sweep.lock"
    )
    patch_setting(monkeypatch, coldread, "_settings", "LOCK", tmp_path / ".lock")
    with coldread.sweep_lock():
        with coldread.manifest_lock():
            pass


def test_save_manifest_is_atomic(tmp_path, monkeypatch):
    """A kill mid-write must not leave a truncated manifest, and a concurrent
    reader must never see a half-written one."""
    patch_project_path(monkeypatch, coldread, "provenance.coldread", tmp_path)
    patch_setting(
        monkeypatch, coldread, "_settings", "MANIFEST", tmp_path / "manifest.json"
    )
    coldread.save_manifest({"sources": {"a": {"sha256": "x"}}})
    import json

    assert json.loads((tmp_path / "manifest.json").read_text())["sources"]["a"]
    assert not (tmp_path / "manifest.json.tmp").exists()  # temp cleaned up


def test_persist_revalidations_does_not_clobber_a_concurrent_record(
    tmp_path, monkeypatch
):
    """It re-reads under the lock and copies only the revalidation fields, so a
    report recorded by another process meanwhile survives."""
    patch_project_path(monkeypatch, coldread, "provenance.coldread", tmp_path)
    patch_setting(
        monkeypatch, coldread, "_settings", "MANIFEST", tmp_path / "manifest.json"
    )
    patch_setting(monkeypatch, coldread, "_settings", "LOCK", tmp_path / ".lock")
    fv = coldread.filter_version_for("a")
    # on disk: 'a' is stale, and 'b' was just recorded by another process
    coldread.save_manifest(
        {
            "sources": {
                "a": {"sha256": "x", "filter_version": "an-older-recipe"},
                "b": {"sha256": "y", "filter_version": fv, "report": "reports/new.md"},
            }
        }
    )
    # in memory: only 'a', revalidated; no knowledge of 'b'
    stale = {
        "_dirty": True,
        "sources": {"a": {"sha256": "x", "filter_version": fv, "render_sha256": "rh"}},
    }
    coldread.persist_revalidations(stale)
    import json

    out = json.loads((tmp_path / "manifest.json").read_text())["sources"]
    assert out["a"]["filter_version"] == fv and out["a"]["render_sha256"] == "rh"
    assert out["b"]["report"] == "reports/new.md"  # not clobbered


# --------------------------------------------------------------------------- #
# discards: commits made in a session vs commits that reached main
# --------------------------------------------------------------------------- #
def _jsonl(tmp_path, lines):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_session_commits_reads_git_commit_echo_not_bare_shas(tmp_path):
    """A session that *reviews* commits quotes their SHAs constantly; only
    `git commit`'s own echo proves the commit was made here. An earlier pass
    that matched any SHA credited a review session with 13 commits it never
    made."""
    p = _jsonl(
        tmp_path,
        [
            '{"t":"[main 1a2b3c4] canon: the harbor holds\\n 2 files changed"}',
            '{"t":"check the latest commit deadbee and also 9998887"}',
        ],
    )
    made = coldread.session_commits(p)
    assert made == [("1a2b3c4", "canon: the harbor holds")]


def test_session_commits_dedupes_and_ignores_empty_subjects(tmp_path):
    p = _jsonl(
        tmp_path,
        [
            '{"t":"[main 1a2b3c4] same commit echoed twice"}',
            '{"t":"[main 1a2b3c4] same commit echoed twice"}',
        ],
    )
    assert len(coldread.session_commits(p)) == 1


def test_a_folded_commit_counts_as_surviving(tmp_path, monkeypatch):
    """The project soft-resets and re-commits, so a KEPT commit routinely
    changes SHA. Matching the subject too is what separates a rewrite from a
    rejection -- SHA-only scored 150 discards where 47 were real."""
    monkeypatch.setattr(
        coldread, "_main_index", lambda: ({"9999999"}, {"canon: the harbor holds"})
    )
    made = [("1a2b3c4", "canon: the harbor holds")]  # different SHA, same subject
    shas, subjects = coldread._main_index()
    gone = [(s, j) for s, j in made if s not in shas and j not in subjects]
    assert gone == []


def test_a_genuinely_discarded_commit_is_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(
        coldread, "_main_index", lambda: ({"9999999"}, {"something else entirely"})
    )
    made = [("1a2b3c4", "canon: the prediction is losable, not winnable")]
    shas, subjects = coldread._main_index()
    gone = [(s, j) for s, j in made if s not in shas and j not in subjects]
    assert len(gone) == 1


def test_the_recipe_version_is_per_reader_not_per_corpus():
    """Bumping one format's parser must not invalidate another's reports.

    The version folded into the cache key used to be `ccfilter`'s for every
    source. With a second parser in the tree that is two errors at once: a
    `ccfilter` change would re-buy Codex reports it never touched, and a
    `codexfilter` change would leave Codex reports validated against a recipe
    that no longer produced their render.

    Each reader's number is carried inside a recipe identity that also
    fingerprints the project's redaction markers, since editing the markers
    changes what the analyst saw just as surely as editing a parser does.
    """
    from quirework.provenance import coldread
    from quirework.provenance import ccfilter
    from quirework.provenance import codexfilter

    # Sources as a report records them: by family and name in the store.
    cc = "cc-sessions/a.jsonl"
    codex = "codex-sessions/b.jsonl"
    web = "conversations/c.md"
    assert coldread.filter_version_for(cc) == ccfilter.policy_version(
        ccfilter.FILTER_VERSION
    )
    assert coldread.filter_version_for(codex) == ccfilter.policy_version(
        codexfilter.FILTER_VERSION
    )
    assert coldread.filter_version_for(cc) != coldread.filter_version_for(codex)
    # a web .md is read straight off disk; no parser version can alter it
    assert coldread.filter_version_for(web) == coldread.filter_version_for(cc)


def test_a_report_name_carries_its_own_readers_version():
    from quirework.provenance import coldread

    sha = "0" * 64
    assert coldread.report_name(sha, filter_version=7).startswith(f"{sha[:12]}-f7p")
