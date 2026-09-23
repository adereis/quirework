"""`coldread triage` lists the untracked sessions and only those."""

import subprocess

from quirework.project import current_project
from quirework.provenance import coldread


def _git(root, *args):
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-C",
            str(root),
            *args,
        ],
        check=True,
        capture_output=True,
    )


def test_triage_lists_untracked_sessions_by_their_reference(monkeypatch, capsys):
    """The untracked set and the rows must be keyed alike.

    It once joined three sets with `+`, which raised before printing
    anything. Behind that, the set held absolute paths while each row is
    keyed by its project-relative reference, so no row ever matched: every
    pending session was dropped from the list and reported as below the
    render floor instead.
    """
    root = current_project().root
    sessions = root / ".quirework/provenance/cc-sessions"
    sessions.mkdir(parents=True)
    _git(root, "init", "-q")
    (sessions / "tracked.jsonl").write_text("{}\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "fixture")
    (sessions / "pending.jsonl").write_text("{}\n")

    body = "x" * (coldread.MIN_RENDER_CHARS + 1)
    monkeypatch.setattr(coldread.FLT, "filter_session", lambda path: (body, {}))
    monkeypatch.setattr(coldread, "_edit_targets", lambda p: (1, 0, {"chapter.qmd": 1}))

    coldread.cmd_triage([])
    out = capsys.readouterr().out

    listed = [line for line in out.splitlines() if "pending.jsonl" in line]
    assert len(listed) == 1 and listed[0].startswith("keep")
    assert ".quirework/provenance/cc-sessions/pending.jsonl" in listed[0]
    assert str(root) not in listed[0]
    assert "tracked.jsonl" not in out
    assert "render floor" not in out
