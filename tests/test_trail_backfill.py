"""Backfilling a spec trail: packets for cheap readers, and a ledger of leads.

The book is invented: a two-section spec about a kettle, one chapter binding
it, and two Claude Code sessions written for the purpose. A reader's answer is
banked through the run engine's own external import, so every test that
imports one goes through a real prepared record, never a hand-built stand-in.
"""

import json

import pytest

from quirework.book import backfill, trail
from quirework.project import ConfigurationError, Project, project_scope
from quirework.runs import engine

SPEC = """# What the notebook holds

## A kettle signals without thinking {#kettle-signal}

<!-- @todo decide whether a whistle is a report -->
A whistle reports a state. Reporting a state is not having a thought.

## Cooling is not the reverse of boiling {#cooling-asymmetry}

A kettle reaches the boil by one route and cools by another.
"""

MORNING = "0badc0de-0000-4000-8000-000000000001"
EVENING = "0badc0de-0000-4000-8000-000000000002"

MORNING_TURNS = [
    ("human", "The kettle whistles and nobody thinks. Put that first.", "10:00:05.120"),
    ("assistant", "Opening on the whistle. Shall cooling get a section?", "10:01:00"),
    ("human", "Yes. Cooling is a different route, not boiling backwards.", "10:05:30"),
    ("assistant", "Noted as its own section.", "10:06:00"),
    ("human", "No, not slower. A route, not a speed.", "10:09:12.500"),
]

EVENING_TURNS = [
    ("human", "Commit the kettle chapter.", "20:00:00"),
    ("assistant", "Committed.", "20:00:30"),
]


def _session(directory, sid, day, turns):
    directory.mkdir(parents=True, exist_ok=True)
    lines = []
    for role, text, clock in turns:
        if role == "human":
            record = {"type": "user", "message": {"content": text}}
        else:
            record = {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": text}]},
            }
        record["timestamp"] = f"{day}T{clock}Z"
        lines.append(json.dumps(record))
    (directory / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _book(tmp_path, *, leads="private/spec-trail-leads"):
    root = tmp_path / "book"
    sections = root / "manuscript" / "sections"
    (sections / "_marks").mkdir(parents=True)
    (root / "manuscript" / "_quarto.yml").write_text(
        "project:\n  type: book\nbook:\n  chapters:\n    - sections/opening.qmd\n"
    )
    (sections / "_spec.md").write_text(SPEC)
    (sections / "opening.qmd").write_text("# The opening {#sec-opening}\n\nA kettle.\n")
    (sections / "_marks" / "opening.md").write_text(
        "# The opening {#sec-opening}\n\n<!-- @bind(kettle-signal) -->\n"
    )
    book = {
        "source": "manuscript",
        "chapters": "manuscript/sections",
        "quarto": "manuscript/_quarto.yml",
        "spec": "manuscript/sections/_spec.md",
        "spec_trail": "private/spec-trail.md",
    }
    if leads:
        book["spec_trail_leads"] = leads
    corpus = root / "private" / "corpus" / "cc-sessions"
    _session(corpus, MORNING, "2031-04-02", MORNING_TURNS)
    _session(corpus, EVENING, "2031-04-03", EVENING_TURNS)
    return Project.from_settings(
        root,
        {
            "book": book,
            "provenance": {
                "enabled": True,
                "root": "private/corpus",
                "curation": "presence",
            },
            "providers": {"claude": {"sessions": str(tmp_path / "home")}},
        },
    )


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    project = _book(tmp_path)
    with project_scope(project):
        yield project


def _read(tmp_path, answers, **options):
    """Plan external seats, bank ``answers`` through the engine, import them."""
    out = tmp_path / "packets"
    backfill.plan(
        out, harness="claude", model="sonnet", effort="medium", external=True, **options
    )
    record = tmp_path / "record"
    engine.prepare(out / "plan.json", record, resolve=False)
    run = engine.Run(record)
    for seat, text in zip(sorted(run.seats), answers):
        answer = tmp_path / f"{seat}.md"
        answer.write_text(text, encoding="utf-8")
        engine.import_answer(
            run,
            seat,
            "read",
            answer,
            {"session_id": seat, "model": "claude-sonnet-5", "source": "test"},
        )
    return backfill.import_run(record)


def _lines(*objects):
    return "\n".join(json.dumps(o) for o in objects) + "\n"


# --------------------------------------------------------------------------- #
# what a reader is handed
# --------------------------------------------------------------------------- #
def test_a_packet_shows_each_turn_with_the_reply_it_answered(book):
    corpus = trail.Corpus()
    family, path = corpus.captured()[MORNING]
    text = backfill.packet(MORNING, family, path)
    assert text.startswith(f"# Session {MORNING}\n")
    assert "## Turn 2 · 2031-04-02T10:05:30Z · typed" in text
    assert "> Opening on the whistle. Shall cooling get a section?" in text
    assert "Yes. Cooling is a different route" in text
    assert text.count("## Turn ") == 3


def test_a_long_turn_keeps_its_head_and_tail(book):
    tail = " the author's own closing words."
    body = "pasted review " * 1000 + tail
    clipped = backfill._clip(body)
    assert "characters not shown" in clipped
    assert clipped.startswith("pasted review") and clipped.endswith(tail.strip())


def test_the_menu_names_each_section_by_its_slug(book):
    menu = backfill.menu()
    assert (
        "- `kettle-signal`: A kettle signals without thinking. A whistle "
        "reports a state." in menu
    )
    assert "[chapters: opening]" in menu, "a turn often names its chapter"
    assert "thinking. A kettle reaches" not in menu
    assert "another. [chapters: none]" in menu
    assert "@todo" not in menu, "the spec's own marks are not the reader's business"


# --------------------------------------------------------------------------- #
# a run plan
# --------------------------------------------------------------------------- #
def test_a_plan_hands_out_every_session_oldest_first_with_no_tools(book, tmp_path):
    done = backfill.plan(
        tmp_path / "packets", harness="codex", model="luna", effort="medium"
    )
    plan = json.loads((tmp_path / "packets" / "plan.json").read_text())
    assert (done["seats"], done["sessions"]) == (1, 2)
    seat = plan["seats"][0]
    assert seat["policy"] == {"tools": "none", "filesystem": "enforced"}
    assert seat["tier"] == "cheap"
    assert seat["metadata"]["trail_read"]["sessions"] == [MORNING, EVENING]
    sources = [m["source"] for m in seat["turns"][0]["materials"]]
    assert sources == ["menu.md", f"packets/{MORNING}.md", f"packets/{EVENING}.md"]


def test_an_agy_seat_is_audited_because_agy_cannot_enforce(book, tmp_path):
    backfill.plan(
        tmp_path / "packets", harness="agy", model="gemini-pro", effort="high"
    )
    plan = json.loads((tmp_path / "packets" / "plan.json").read_text())
    assert plan["seats"][0]["policy"] == {"tools": "none", "filesystem": "audited"}


def test_a_small_budget_gives_each_session_its_own_seat(book, tmp_path):
    done = backfill.plan(
        tmp_path / "packets", harness="codex", model="luna", effort="low", budget=1
    )
    assert done["seats"] == 2


def test_packets_never_land_in_the_checkout(book):
    with pytest.raises(ValueError, match="outside the checkout"):
        backfill.plan(
            book.root / "packets", harness="codex", model="luna", effort="low"
        )


# --------------------------------------------------------------------------- #
# importing what a reader flagged
# --------------------------------------------------------------------------- #
ANSWER = _lines(
    {
        "session": "0badc0de-0000-4000-8000-000000000001",
        "turn": 1,
        "sections": [
            {
                "slug": "kettle-signal",
                "kind": "seed",
                "confidence": "high",
                "reason": "Asks to open on the whistle.",
            }
        ],
    },
    {
        "session": MORNING,
        "turn": 3,
        "sections": [
            {
                "slug": "cooling-asymmetry",
                "kind": "correct",
                "confidence": "high",
                "reason": "Refuses speed as the difference.",
            },
            {"slug": "no-such-section", "kind": "seed", "confidence": "low"},
        ],
    },
    {"session": MORNING, "turn": 9, "sections": []},
    {"session": MORNING, "done": True},
)


def test_a_finished_session_is_recorded_as_read_with_its_checked_flags(book, tmp_path):
    report = _read(tmp_path, ["```jsonl\n" + ANSWER + "```\n"])
    assert report[0]["read"] == 1 and report[0]["dropped"] == 2
    assert report[0]["unread"] == [EVENING], "no done line, so not read"
    [path] = (book.path("book.spec_trail_leads") / "reads" / MORNING).glob("*.json")
    read = json.loads(path.read_text())
    assert read["model"] == "claude-sonnet-5" and read["requested"] == "sonnet"
    first, third = read["candidates"]
    assert first["logged"] == "2031-04-02T10:00:05.120000+00:00"
    assert [s["slug"] for s in third["sections"]] == ["cooling-asymmetry"]
    assert path.stat().st_mode & 0o077 == 0, "the ledger is its owner's alone"


def test_a_new_section_does_not_send_the_corpus_out_again(book, tmp_path):
    _read(tmp_path, [ANSWER])
    spec = book.path("book.spec")
    spec.write_text(
        SPEC + "\n## Steam is not smoke {#steam-not-smoke}\n\nIt is water.\n"
    )
    done = backfill.plan(
        tmp_path / "again", harness="codex", model="luna", effort="low"
    )
    assert done["sessions"] == 1, "only the session never read"
    assert "#steam-not-smoke" in "\n".join(backfill.status())


def test_a_current_read_leaves_the_session_out_of_the_next_plan(book, tmp_path):
    _read(tmp_path, [ANSWER])
    done = backfill.plan(
        tmp_path / "again", harness="codex", model="luna", effort="low"
    )
    plan = json.loads((tmp_path / "again" / "plan.json").read_text())
    assert done["sessions"] == 1
    assert plan["seats"][0]["metadata"]["trail_read"]["sessions"] == [EVENING]


# --------------------------------------------------------------------------- #
# ruling on a section
# --------------------------------------------------------------------------- #
def test_a_worklist_moves_a_candidate_once_it_is_ruled_on(book, tmp_path):
    _read(tmp_path, [ANSWER])
    pending, on_trail, rejected, stale = backfill.worklist(
        "cooling-asymmetry", commits=False
    )
    assert [c.turn.number for c in pending] == [3] and not stale
    model, flag = pending[0].flags[0]
    assert (model, flag["kind"]) == ("claude-sonnet-5", "correct")

    trail.add("cooling-asymmetry", "correct", session=MORNING, turn=3)
    pending, on_trail, _, _ = backfill.worklist("cooling-asymmetry", commits=False)
    assert not pending and [c.turn.number for c in on_trail] == [3]

    backfill.reject(
        "kettle-signal", session=MORNING, turn=1, reason="Placement, not the claim."
    )
    pending, _, rejected, _ = backfill.worklist("kettle-signal", commits=False)
    assert not pending and [c.turn.number for c in rejected] == [1]


def test_a_rejection_needs_a_reason_and_is_made_once(book):
    with pytest.raises(ValueError, match="needs its reason"):
        backfill.reject("kettle-signal", session=MORNING, turn=1, reason=" ")
    backfill.reject("kettle-signal", session=MORNING, turn=1, reason="Not the claim.")
    with pytest.raises(LookupError, match="already rejected"):
        backfill.reject("kettle-signal", session=MORNING, turn=1, reason="Again.")


def test_status_counts_the_sessions_read_and_the_sections_flagged(book, tmp_path):
    _read(tmp_path, [ANSWER])
    text = "\n".join(backfill.status())
    assert "claude-code  book       1 of    2 read" in text
    assert "#cooling-asymmetry" in text and "1 flagged" in text


def test_the_command_line_prints_a_sections_worklist(book, tmp_path, capsys):
    _read(tmp_path, [ANSWER])
    assert trail.main(["backfill", "cooling-asymmetry", "--no-commits", "--utc"]) == 0
    out = capsys.readouterr().out
    assert "1 candidate(s) to rule on" in out
    assert "claude-sonnet-5: correct (high) Refuses speed as the difference." in out
    assert "quirework provenance show 0badc0de --turn 3" in out


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
def test_a_ledger_needs_a_trail_and_says_when_it_is_not_configured(tmp_path):
    project = _book(tmp_path, leads=None)
    with project_scope(project):
        with pytest.raises(ConfigurationError, match="keeps no backfill ledger"):
            backfill.ledger()
    book = dict(project.describe()["settings"]["book"])
    del book["spec_trail"]
    book["spec_trail_leads"] = "private/leads"
    broken = Project.from_settings(project.root, {"book": book})
    with pytest.raises(ConfigurationError, match="keeps leads for a spec trail"):
        broken.require("book")
