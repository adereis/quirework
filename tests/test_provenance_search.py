import json

import pytest

from conftest import keep_session
from quirework.project import current_project
from quirework.provenance import search, show

pytestmark = pytest.mark.usefixtures("corpus_project")

# At UTC-3 this session opens with a reply at 23:50 on April 2nd, and the
# author's first turn comes after midnight; in UTC both fall on April 3rd.
LATE_EVENING = [
    {
        "type": "assistant",
        "timestamp": "2031-04-03T02:50:00Z",
        "message": {"content": [{"type": "text", "text": "Welcome back."}]},
    },
    {
        "type": "user",
        "timestamp": "2031-04-03T03:10:00Z",
        "message": {"content": "The kettle scene again."},
    },
]


def _cc_session(records):
    return keep_session(
        current_project(),
        "cc-sessions/0badc0de-0000-4000-8000-000000000001.jsonl",
        "\n".join(json.dumps(r) for r in records) + "\n",
    )


def test_keyword_search_dates_a_session_as_show_heads_it(three_hours_behind_utc):
    """The session's first turn, not the author's, and in show's zone."""
    path = _cc_session(LATE_EVENING)
    assert show.render("cc", path).splitlines()[1].startswith("2031-04-02 23:50")
    (hit,), _ = search.search(["kettle"], highlights="")
    assert hit["date"] == "2031-04-02"
    (hit,), _ = search.search(["kettle"], highlights="", utc=True)
    assert hit["date"] == "2031-04-03"


def test_semantic_search_dates_a_session_as_show_heads_it(three_hours_behind_utc):
    np = pytest.importorskip("numpy")
    path = _cc_session(LATE_EVENING)
    index = {
        "vecs": np.array([[1.0, 0.0]], dtype=np.float32),
        "meta": [
            {
                "source": "cc",
                "file": path.name,
                "ordinal": 1,
                "chunk": 0,
                "ts": "2031-04-03T03:10:00+00:00",
                "text": "The kettle scene again.",
            }
        ],
    }

    class Backend:
        def encode_query(self, text):
            return np.array([1.0, 0.0], dtype=np.float32)

    for utc, day in ((False, "2031-04-02"), (True, "2031-04-03")):
        (hit,), _ = search.semantic_search(
            "kettle", index=index, backend=Backend(), highlights="", utc=utc
        )
        assert (hit["date"], hit["ordinal"]) == (day, 1)


def test_the_utc_flag_reaches_the_printed_row(three_hours_behind_utc, capsys):
    _cc_session(LATE_EVENING)
    assert search.main(["kettle", "--utc"]) == 0
    rows = [line for line in capsys.readouterr().out.splitlines() if "0badc0de" in line]
    assert len(rows) == 1 and rows[0].startswith("2031-04-03")


def test_a_review_with_no_times_keeps_the_date_it_states(tmp_path):
    review = tmp_path / "gemini-fictitious-review.md"
    review.write_text("## You · #1\n\nIs it long?\n", encoding="utf-8")
    dated = search._session_date(
        "gemini", review, review.name, False, undated="2031-04-02"
    )
    assert dated == "2031-04-02"


# --------------------------------------------------------------------------- #
# what a search leaves out
# --------------------------------------------------------------------------- #
WAITING = "0badc0de-0000-4000-8000-0000000000a1"
ONGOING = "0badc0de-0000-4000-8000-0000000000a2"


def _jsonl(records):
    return "\n".join(json.dumps(r) for r in records) + "\n"


def test_a_search_says_which_pending_captures_it_did_not_read(capsys):
    _cc_session(LATE_EVENING)
    keep_session(
        current_project(),
        f"cc-sessions/{WAITING}.jsonl",
        _jsonl(LATE_EVENING),
        pending=True,
    )
    assert search.main(["whistle"]) == 0
    out = capsys.readouterr().out
    assert "no matches in 1 sessions searched." in out
    assert "not searched: 1 pending capture(s) (cc 0badc0de)" in out
    assert "A miss is no proof a turn does not exist there" in out
    assert "spec trail" not in out, "this project keeps no trail"


def test_a_search_names_a_live_transcript_no_capture_holds(capsys):
    """A conversation still going on has turns only its transcript holds."""
    from quirework.provenance.ingestion import claude

    project = current_project()
    directory = claude.encoded_project_dir(project.root)
    directory.mkdir(parents=True)
    records = [{**r, "cwd": str(project.root)} for r in LATE_EVENING]
    (directory / f"{ONGOING}.jsonl").write_text(_jsonl(records))
    _cc_session(LATE_EVENING)
    assert search.unsearched() == ([], [("cc", ONGOING)])
    assert search.main(["kettle"]) == 0
    out = capsys.readouterr().out
    assert "1 session(s) matched of 1 searched." in out
    assert "not searched: 1 live transcript(s) holding turns no capture has" in out


def test_a_corpus_with_nothing_waiting_adds_no_notice(capsys):
    _cc_session(LATE_EVENING)
    assert search.unsearched() == ([], [])
    assert search.main(["kettle"]) == 0
    assert "not searched" not in capsys.readouterr().out


def test_the_notice_points_at_the_trail_where_the_project_keeps_one():
    lines = search.unsearched_lines([("cc", WAITING)], [])
    assert "spec trail add" not in " ".join(lines)
    from conftest import corpus_settings
    from quirework.project import Project, project_scope

    with project_scope(
        Project.from_settings(
            current_project().root,
            {
                "provenance": corpus_settings(),
                "manuscript": {"spec_trail": "private/trail.md"},
            },
        )
    ):
        lines = search.unsearched_lines([("cc", WAITING)], [])
    assert "`quirework spec trail add --find PHRASE` reaches" in " ".join(lines)
