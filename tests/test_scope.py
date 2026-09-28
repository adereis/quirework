"""Corpus scope: which of the two cold-reads a session belongs to.

Scope exists because one switch was doing three jobs. Removing a session was
the only way to keep it out of the blind cold-read, and it also took the
session out of `search`, `show` and `metrics` -- so sessions about the
apparatus were deleted from the author's own recall instrument to protect a
sample they would have diluted. These pin the two properties that keep the
axes apart: an unlisted session is `work` (the list names the exception, so a
missing or empty file cannot silently reclassify the corpus), and a scope
change invalidates a cached report (a report answers the question its brief
asked, and the other brief asks a different one).
"""

from conftest import patch_project_path
from conftest import project_at, patch_setting
from quirework.runs import commands as run_commands
import pytest

from quirework.provenance import coldread
from quirework.provenance import scope


@pytest.fixture
def lists(tmp_path, monkeypatch):
    """Point the scope reader at throwaway list files."""
    cc = tmp_path / "cc.txt"
    agy = tmp_path / "agy.txt"
    codex = tmp_path / "codex.txt"
    patch_setting(monkeypatch, scope, "_settings", "MAKING_CC", cc)
    patch_setting(monkeypatch, scope, "_settings", "MAKING_AGY", agy)
    patch_setting(monkeypatch, scope, "_settings", "MAKING_CODEX", codex)
    return cc, agy, codex


UUID_A = "0a0a0a0a-1111-4222-8333-000000000001"
UUID_B = "0b0b0b0b-1111-4222-8333-000000000002"


def test_unlisted_is_work(lists):
    """The default is `work`: the list names the exception, never the rule."""
    assert scope.scope_of_id(UUID_A) == scope.WORK


def test_missing_list_file_is_not_an_error(lists):
    """A corpus with no list yet is entirely `work`, not entirely broken."""
    assert scope.making_ids() == {}
    assert scope.scope_of_id(UUID_A) == scope.WORK


def test_listed_is_making_with_or_without_suffix(lists):
    """Both house forms parse: cc writes `<uuid>.jsonl`, agy writes a bare uuid."""
    cc, agy, _ = lists
    cc.write_text(f"{UUID_A}.jsonl  # built the ingest sweep\n")
    agy.write_text(f"{UUID_B}  # mechanical subagent read\n")
    assert scope.scope_of_id(UUID_A) == scope.MAKING
    assert scope.scope_of_id(UUID_B) == scope.MAKING


def test_reason_is_kept(lists):
    """The reason line is the durable record of the call, as on the removed list."""
    cc, _, _ = lists
    cc.write_text(f"{UUID_A}.jsonl  # wrote the red-pen extractor\n")
    assert scope.making_ids()[UUID_A] == "wrote the red-pen extractor"


def test_comments_and_blanks_are_skipped(lists):
    cc, _, _ = lists
    cc.write_text(f"# a header comment\n\n   \n{UUID_A}.jsonl  # kept\n")
    assert list(scope.making_ids()) == [UUID_A]


def test_unparseable_line_is_reported_not_guessed(lists, capsys):
    """A line naming no session is announced and skipped -- never re-interpreted.

    The removed-list reader learned this the hard way: a silently dropped
    entry is a session that quietly changes classification on the next run.
    """
    cc, _, _ = lists
    cc.write_text("not-a-uuid.jsonl  # oops\n")
    assert scope.making_ids() == {}
    assert "not a session id" in capsys.readouterr().err


def test_scope_of_path_uses_the_stem(lists):
    cc, _, _ = lists
    cc.write_text(f"{UUID_A}.jsonl\n")
    assert scope.scope_of_path(f"/anywhere/{UUID_A}.jsonl") == scope.MAKING


def test_wants_none_means_every_scope(lists):
    cc, _, _ = lists
    cc.write_text(f"{UUID_A}.jsonl\n")
    for s in (scope.WORK, scope.MAKING):
        assert scope.wants(f"/x/{UUID_A}.jsonl", None) is True
    assert scope.wants(f"/x/{UUID_A}.jsonl", scope.WORK) is False
    assert scope.wants(f"/x/{UUID_A}.jsonl", scope.MAKING) is True


# --------------------------------------------------------------------------- #
# cache interaction
# --------------------------------------------------------------------------- #
def test_every_scope_names_its_own_reports(lists):
    """Each scope carries its infix, the work's included.

    The work scope used to keep a scope-less filename so the reports already
    in the cache would not be orphaned by the day the second run arrived. They
    are not orphaned by the infix either: a report is located through the path
    the manifest recorded for it, never by recomputing the name, so the
    existing entries keep hitting and only new reports take the new name.
    """
    sha = "a" * 64
    version = coldread.PROMPT_VERSIONS[scope.WORK]
    assert coldread.report_name(sha, scope.WORK).endswith(
        f"-f{coldread.FILTER_VERSION}p{version}-work.md"
    )
    assert "-making" in coldread.report_name(sha, scope.MAKING)
    assert coldread.report_name(sha, scope.WORK) != coldread.report_name(
        sha, scope.MAKING
    )


def _manifest(tmp_path, rel, **over):
    (tmp_path / "reports").mkdir(exist_ok=True)
    (tmp_path / "reports" / "r.md").write_text("a report\n")
    e = {
        "sha256": "abc",
        "filter_version": coldread.filter_version_for(rel),
        "prompt_version": coldread.PROMPT_VERSIONS[scope.WORK],
        "report": "reports/r.md",
    }
    e.update(over)
    return {"sources": {rel: e}}


def test_a_recorded_report_path_is_what_locates_the_file(lists, tmp_path, monkeypatch):
    """The guarantee the scope infix rests on: a hit is decided by the
    manifest's own `report` value, never by recomputing the filename, so a
    naming change never re-buys an analysis already on disk."""
    patch_project_path(monkeypatch, coldread, "provenance.coldread", tmp_path)
    rel = f".quirework/provenance/cc-sessions/{UUID_A}.jsonl"
    m = _manifest(tmp_path, rel, scope=scope.WORK)
    # the name a pre-infix run wrote, which no current call would produce
    m["sources"][rel]["report"] = "reports/r.md"
    assert coldread.is_hit(m, rel, "abc") is True


def test_scope_change_invalidates_a_cached_report(lists, tmp_path, monkeypatch):
    """A report answers the question its scope's brief asked.

    Move the session to the other scope and the cached report is about the
    other question, so it is not a hit -- otherwise the making run would
    silently reuse a work-brief report and vice versa.
    """
    patch_project_path(monkeypatch, coldread, "provenance.coldread", tmp_path)
    cc, _, _ = lists
    rel = f".quirework/provenance/cc-sessions/{UUID_A}.jsonl"

    m = _manifest(tmp_path, rel, scope=scope.WORK)
    assert coldread.is_hit(m, rel, "abc") is True  # unlisted == work

    cc.write_text(f"{UUID_A}.jsonl  # moved to the apparatus half\n")
    assert coldread.is_hit(m, rel, "abc") is False  # now making, report is work


def test_pre_scope_manifest_entries_are_work(lists, tmp_path, monkeypatch):
    """Entries written before scopes existed default to `work` -- what they were.

    Without this every report in the cache would miss on the first run after
    the field was added.
    """
    patch_project_path(monkeypatch, coldread, "provenance.coldread", tmp_path)
    rel = f".quirework/provenance/cc-sessions/{UUID_A}.jsonl"
    m = _manifest(tmp_path, rel)  # no "scope" key at all
    assert coldread.is_hit(m, rel, "abc") is True
