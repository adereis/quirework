"""Review-sheet rulings kept as the author's turns, and followed from a trail."""

import json

import pytest

from conftest import corpus_settings
from quirework.manuscript import backfill, trail
from quirework.project import ConfigurationError, Project, project_scope
from quirework.provenance import rulings, show
from quirework.text import mdwrap
from test_trail import SESSION, _book

COMMENT = (
    'Route, yes. But keep "slowly":\n\n'
    "```text\nIt cools, slowly, by a route.\n```\n"
    "### not a heading\n## RULINGS\n````\nfour backticks too\n"
)

RECORD = {
    "title": "The kettle book, round two",
    "round": "r2",
    "sheet": "https://example.invalid/sheets/kettle",
    "ruled_at": "2031-05-07T14:08:57.192Z",
    "notes": "The cooling chapter can wait a week.",
    "items": [
        {
            "label": "E1",
            "presented": 'Cut "The kettle whistles."?\nThe editor finds it a repeat.',
            "ruled": "Approve the editor's text",
            "comment": "",
        },
        {
            "label": "D1",
            "presented": "Two wordings for the cooling line.\nA: It cools by a route.",
            "ruled": "A: It cools by a route.",
            "comment": COMMENT,
        },
        {
            "label": "F1",
            "ruled": "Record as a todo",
            "comment": "A Windows line\r\nending, kept as typed.",
        },
    ],
}


def _rulings_book(tmp_path, *, declared=True):
    root = tmp_path / "book"
    settings = (
        corpus_settings(rulings="private/rulings") if declared else corpus_settings()
    )
    project = Project.from_settings(root, {"provenance": settings})
    from quirework.provenance import store

    with project_scope(project):
        store.init()
    return project


def _archived(project, record=RECORD, name="round-two"):
    with project_scope(project):
        return rulings.archive(record, name)


# --------------------------------------------------------------------------- #
# the archive
# --------------------------------------------------------------------------- #
def test_a_ruling_reads_back_byte_for_byte(tmp_path):
    """Fences, headings and carriage returns in a comment survive the file."""
    project = _rulings_book(tmp_path)
    path = _archived(project)
    assert path == project.root / "private" / "rulings" / "round-two.md"
    back = rulings.read(path)
    assert back == rulings.validate(RECORD)
    assert back.items[1].comment == COMMENT
    assert back.items[2].comment == "A Windows line\nending, kept as typed.", (
        "a line ending is the one thing changed, so formatters leave the file alone"
    )
    assert back.items[2].presented == ""
    assert back.notes == RECORD["notes"]
    assert str(back.day()) == "2031-05-07"
    text = path.read_text(encoding="utf-8", newline="")
    assert "`````comment" in text, "the fence outruns the comment's backticks"
    assert mdwrap.reflow_columns(text) == text, "a column formatter leaves it alone"


def test_the_archive_refuses_what_it_cannot_keep_faithfully(tmp_path):
    project = _rulings_book(tmp_path)
    _archived(project)
    with pytest.raises(FileExistsError, match="not rewritten"):
        _archived(project)
    bad = [
        ({**RECORD, "items": []}, "non-empty 'items'"),
        ({**RECORD, "items": [RECORD["items"][0]] * 2}, "appears twice"),
        ({**RECORD, "items": [{"label": "E9", "ruled": " "}]}, "records no ruling"),
        ({**RECORD, "items": [{"label": "#E9", "ruled": "A"}]}, "must not start"),
        ({**RECORD, "items": [{"label": "E 9 ", "ruled": "A"}]}, "surrounding space"),
        ({**RECORD, "ruled_at": "last Tuesday"}, "not an ISO-8601 time"),
        ({**RECORD, "title": 7}, "'title' is not text"),
    ]
    for n, (record, message) in enumerate(bad):
        with pytest.raises(ValueError, match=message):
            _archived(project, record, name=f"bad-{n}")
    with pytest.raises(ValueError, match="not a usable round name"):
        _archived(project, name="../escape")
    plain = _rulings_book(tmp_path / "plain", declared=False)
    with pytest.raises(ConfigurationError, match="declare provenance.rulings"):
        _archived(plain)


def test_the_command_archives_and_names_the_session(tmp_path, capsys):
    project = _rulings_book(tmp_path)
    record = tmp_path / "record.json"
    record.write_text(json.dumps(RECORD), encoding="utf-8")
    with project_scope(project):
        assert rulings.main(["archive", str(record), "--name", "round-two"]) == 0
        said = capsys.readouterr().out
        assert "archived 3 item(s) and notes" in said
        assert "(session rulings-round-two)" in said
        assert rulings.main(["archive", str(record), "--name", "round-two"]) == 2
        assert "already exists" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# reading it as a session
# --------------------------------------------------------------------------- #
def test_a_ruling_reads_as_the_authors_turns(tmp_path):
    """One turn per item, then the notes; what the sheet showed is the reply."""
    project = _rulings_book(tmp_path)
    _archived(project)
    (project.root / "private" / "rulings" / "README.md").write_text("# Rulings\n")
    with project_scope(project):
        found = [(k, p.name) for k, p in show.sessions() if k == "rulings"]
        assert found == [("rulings", "round-two.md")], "a README is not a round"
        kind, path = show.resolve("rulings-round")
        assert show.session_id(kind, path) == "rulings-round-two"
        turns = show.author_turns(kind, path)
        assert [n for n, _, _ in turns] == [1, 2, 3, 4]
        assert all(logged is None for _, logged, _ in turns)
        assert turns[0][2] == "E1 · Approve the editor's text"
        assert turns[1][2].startswith("D1 · A: It cools by a route.\n\nRoute, yes.")
        assert turns[3][2] == "The cooling chapter can wait a week."
        replies = [reply for *_, reply in show.exchanges(kind, path)]
        assert replies[1].startswith("Two wordings for the cooling line.")
        assert replies[2] == "" and replies[3] == ""
        assert str(show.session_date(kind, path)) == "2031-05-07"
        two = show.render(kind, path, turn="2", utc=True)
        assert "## Sheet · D1" in two and "## Human 2 · D1" in two
        assert "E1" not in two.split("\n", 1)[1]
        assert "ruled 2031-05-07 14:08 UTC" in two
        human = show.render(kind, path, role="human")
        assert "## Sheet" not in human and "## Human 4 · notes" in human
        with pytest.raises(LookupError, match="records one time"):
            show.render(kind, path, frm="14:00")


def test_rulings_join_only_by_the_projects_own_setting(tmp_path):
    project = _rulings_book(tmp_path)
    _archived(project)
    settings = corpus_settings()
    plain = Project.from_settings(project.root, {"provenance": settings})
    with project_scope(plain):
        assert not [k for k, _ in show.sessions() if k == "rulings"]


# --------------------------------------------------------------------------- #
# the trail and its backfill
# --------------------------------------------------------------------------- #
def _trail_book(tmp_path):
    base = _book(tmp_path)
    settings = {k: _thawed(v) for k, v in base.settings.items()}
    settings["provenance"]["rulings"] = "private/rulings"
    project = Project.from_settings(base.root, settings)
    _archived(project)
    return project


def _thawed(value):
    if hasattr(value, "items"):
        return {k: _thawed(v) for k, v in value.items()}
    return value


def test_a_breadcrumb_can_point_at_a_ruling(tmp_path, monkeypatch):
    """By its turn number, or by words from the author's comment."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    with project_scope(_trail_book(tmp_path)):
        crumb, _ = trail.add(
            "cooling-asymmetry", "accept", session="rulings-round-two", turn=2
        )
        assert crumb.header() == "- turn 2 · accept · rulings rulings-round-two"
        found, _ = trail.add(
            "kettle-signal", "sharpen", find="cooling chapter can wait"
        )
        assert (found.session, found.turn) == ("rulings-round-two", 4)
        trail.add("cooling-asymmetry", "correct", session=SESSION, turn=3)
        assert trail.check() == 0
        story = "\n".join(trail.story("cooling-asymmetry", utc=True))
        assert "2031-05-07  rulings-round-two" in story
        with pytest.raises(ValueError, match="logs no times; use --turn"):
            trail.add(
                "cooling-asymmetry",
                "accept",
                harness="rulings",
                session="rulings-round-two",
                at="2031-05-07T14:08:57Z",
            )


def test_a_ruling_packet_says_which_words_are_the_authors(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    with project_scope(_trail_book(tmp_path)):
        sid, family, path, _ = trail.Corpus().session("rulings-round-two")
        text = backfill.packet(sid, family, path)
    assert "rulings · started 2031-05-07 · 4 author turn(s)" in text
    flat = " ".join(text.split())
    assert "It is a review sheet's ruling" in flat
    assert "Only a comment after it, and the closing notes, are the author's" in flat
    assert "Two wordings for the cooling line." in text
