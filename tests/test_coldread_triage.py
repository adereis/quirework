"""`coldread triage` lists the pending captures and only those."""

from conftest import corpus_settings, keep_session
from quirework.project import Project, project_scope
from quirework.provenance import coldread


def test_triage_lists_pending_sessions_by_their_reference(
    tmp_path, monkeypatch, capsys
):
    """The pending set and the rows must be keyed alike.

    It once joined three sets with `+`, which raised before printing
    anything. Behind that, the set held absolute paths while each row is
    keyed by its reference, so no row ever matched: every pending session
    was dropped from the list and reported as below the render floor
    instead.
    """
    project = Project.from_settings(tmp_path, {"provenance": corpus_settings()})
    keep_session(project, "cc-sessions/accepted.jsonl", "{}\n")
    keep_session(project, "cc-sessions/pending.jsonl", "{}\n", pending=True)

    body = "x" * (coldread.MIN_RENDER_CHARS + 1)
    monkeypatch.setattr(coldread.FLT, "filter_session", lambda path: (body, {}))
    monkeypatch.setattr(coldread, "_edit_targets", lambda p: (1, 0, {"chapter.qmd": 1}))

    with project_scope(project):
        coldread.cmd_triage([])
    out = capsys.readouterr().out

    listed = [line for line in out.splitlines() if "pending.jsonl" in line]
    assert len(listed) == 1 and listed[0].startswith("keep")
    assert "cc-sessions/pending.jsonl" in listed[0]
    assert str(tmp_path) not in listed[0]
    assert "accepted.jsonl" not in out
    assert "render floor" not in out


def test_triage_all_reads_accepted_sessions_too(tmp_path, monkeypatch, capsys):
    project = Project.from_settings(tmp_path, {"provenance": corpus_settings()})
    keep_session(project, "cc-sessions/accepted.jsonl", "{}\n")
    keep_session(project, "cc-sessions/pending.jsonl", "{}\n", pending=True)
    body = "x" * (coldread.MIN_RENDER_CHARS + 1)
    monkeypatch.setattr(coldread.FLT, "filter_session", lambda path: (body, {}))
    monkeypatch.setattr(coldread, "_edit_targets", lambda p: (0, 1, {"notes.md": 1}))

    with project_scope(project):
        coldread.cmd_triage(["--all"])
    out = capsys.readouterr().out
    assert "cc-sessions/accepted.jsonl" in out and "cc-sessions/pending.jsonl" in out


def test_an_edit_is_the_works_in_every_recorded_checkout_and_nowhere_else(
    tmp_path, monkeypatch
):
    """A session records absolute paths, from whichever checkout it ran in,
    so each is read relative to the deepest recorded tree that holds it: a
    worktree inside a checkout claims its own files. The trees are the ones
    the corpus recorded, so a removed worktree still classifies. A sibling
    whose name merely extends a checkout's is no checkout, and a path that
    climbs out of one is not the work."""
    from quirework.provenance import trees

    (tmp_path / "coldread.toml").write_text(
        'doc_dirs = ["chapters/"]\ndoc_prefixes = ["outline"]\n'
    )
    settings = {"provenance": {**corpus_settings(), "coldread_policy": "coldread.toml"}}
    recorded = ("/w/book", "/w/book-draft", "/w/book/.claude/worktrees/side")
    monkeypatch.setattr(trees, "recorded", lambda project: recorded)
    with project_scope(Project.from_settings(tmp_path, settings)):
        assert coldread._classify_edit("chapters/one.md") == "doc"
        assert coldread._classify_edit("outline-second-pass.md") == "doc"
        assert coldread._classify_edit("README.md") == "meta"
        assert coldread._classify_edit("/w/book/chapters/one.md") == "doc"
        assert coldread._classify_edit("/w/book-draft/chapters/one.md") == "doc"
        side = "/w/book/.claude/worktrees/side/chapters/one.md"
        assert coldread._classify_edit(side) == "doc"
        assert coldread._classify_edit("/w/book-draft/../tool/src/x.py") == "meta"
        assert coldread._classify_edit("/w/book-elsewhere/chapters/one.md") == "meta"
