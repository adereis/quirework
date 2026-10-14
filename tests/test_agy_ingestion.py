"""The Antigravity sweep's pure helpers, on synthetic records."""

from quirework.provenance.ingestion import agy


def _calls(*values):
    return [
        {"tool_calls": [{"name": "run_command", "args": {"CommandLine": value}}]}
        for value in values
    ]


def test_the_tail_of_a_relative_path_is_not_where_a_session_opened():
    """`.venv/bin/activate` is not `/bin/activate`, which is outside every
    checkout and used to skip a working session as a review run."""
    assert agy.working_context(_calls("source .venv/bin/activate && make")) is None
    assert agy.working_context(_calls("ls docs/chapters/opening.md")) is None
    assert agy.working_context(_calls("find . -path '**/AGENTS.md'")) is None
    assert agy.working_context(_calls("cat ~/notes/today.md")) is None


def test_the_first_absolute_path_still_decides():
    rows = _calls(
        "source .venv/bin/activate",
        "cat /srv/projects/side-quest/manuscript/one.md && ls /tmp",
    )
    assert agy.working_context(rows) == "/srv/projects/side-quest/manuscript/one.md"
    assert agy.working_context(_calls('view "/srv/a b"')) == "/srv/a"


def test_a_clean_capture_says_nothing_and_a_repaired_one_says_how():
    assert agy._damage({}, []) == ""
    note = agy._damage({"<unparseable>": 2}, [0, 7])
    assert "2 line(s) did not parse" in note
    assert "step(s) 0, 7 taken from transcript.jsonl" in note
