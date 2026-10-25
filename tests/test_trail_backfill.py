"""Backfilling a spec trail: packets for cheap readers, and a ledger of leads.

The book is invented: a two-section spec about a kettle, one chapter binding
it, and two Claude Code sessions written for the purpose. A reader's answer is
banked through the run engine's own external import, so every test that
imports one goes through a real prepared record, never a hand-built stand-in.
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quirework.manuscript import backfill, trail
from quirework.project import ConfigurationError, Project, project_scope
from quirework.runs import engine
from quirework.runs.storage import hashes, write

from conftest import corpus_settings, keep_session

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


def _session(project, sid, day, turns):
    """A captured session, accepted into the project's corpus."""
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
    keep_session(project, f"cc-sessions/{sid}.jsonl", "\n".join(lines) + "\n")


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
    project = Project.from_settings(
        root,
        {
            "manuscript": book,
            "provenance": corpus_settings(),
            "providers": {"claude": {"sessions": str(tmp_path / "home")}},
        },
    )
    _session(project, MORNING, "2031-04-02", MORNING_TURNS)
    _session(project, EVENING, "2031-04-03", EVENING_TURNS)
    return project


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    project = _book(tmp_path)
    with project_scope(project):
        yield project


def _read(tmp_path, answers, *, flags=(), accept=(), **options):
    """Plan external seats, bank ``answers`` through the engine, import them.

    ``flags`` are red flags the banked receipts carry, as a runner's would.
    """
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
        if flags:
            # Banked as the engine banks a runner's flag: the receipt file,
            # the state's copy of it, and the record's hashes all agree.
            state = run.state(seat)
            done = state["done"]["read"]
            done["receipt"]["red_flags"] = list(flags)
            write(run.record(seat, "read") / "receipt.json", done["receipt"])
            if done.get("record_hashes"):
                done["record_hashes"] = hashes(run.record(seat, "read"))
            run.save(seat, state)
    return backfill.import_run(record, accept=accept)


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


def test_only_an_agy_seat_expects_the_keyring_flag(book, tmp_path):
    backfill.plan(tmp_path / "agy", harness="agy", model="flash", effort="high")
    backfill.plan(tmp_path / "codex", harness="codex", model="luna", effort="low")
    agy = json.loads((tmp_path / "agy" / "plan.json").read_text())["seats"][0]
    codex = json.loads((tmp_path / "codex" / "plan.json").read_text())["seats"][0]
    assert agy["accept_red_flags"] == ["session-bus"]
    assert "accept_red_flags" not in codex


def test_a_plan_without_out_takes_a_new_run_directory_each_time(book):
    first = backfill.plan(None, harness="codex", model="luna", effort="low")
    second = backfill.plan(
        None, harness="codex", model="luna", effort="low", reread=True
    )
    one, two = Path(first["plan"]).parent.parent, Path(second["plan"]).parent.parent
    assert one.parent == two.parent == book.state_root
    assert two.name == one.name + "-2", "a second pass the same day never collides"
    assert first["record"] == str(one / "record")


def test_a_plan_summary_names_harnesses_and_the_whole_cost(book, tmp_path):
    done = backfill.plan(
        tmp_path / "packets", harness="codex", model="luna", effort="low"
    )
    assert done["harnesses"] == {"claude-code": 2}
    assert done["undated"] == []
    assert done["total_tokens"] > done["tokens"], "the menu goes to every seat"
    lines = backfill.plan_lines(done)
    assert lines[0] == "2 session(s) in 1 seat(s): 2 claude-code"
    record = str(tmp_path / "record")
    assert f"  quirework sop runs start {record}" in lines


def test_a_small_budget_gives_each_session_its_own_seat(book, tmp_path):
    done = backfill.plan(
        tmp_path / "packets", harness="codex", model="luna", effort="low", budget=1
    )
    assert done["seats"] == 2


def test_a_session_longer_than_a_seat_is_read_in_parts(book, tmp_path):
    # The morning session holds three author turns and the evening one.
    done = backfill.plan(
        tmp_path / "packets", harness="codex", model="luna", effort="low", turns=2
    )
    plan = json.loads((tmp_path / "packets" / "plan.json").read_text())
    reads = [s["metadata"]["trail_read"] for s in plan["seats"]]
    assert [r.get("parts") for r in reads] == [{MORNING: [1, 2]}, {MORNING: [3]}]
    assert reads[1]["sessions"] == [MORNING, EVENING], "a part shares a seat"
    assert [len(keys) for keys in reads[1]["seen"].values()] == [1, 1]
    assert done["split"] == [(MORNING, 3, [[1, 2], [3]])]
    lines = "\n".join(backfill.plan_lines(done))
    assert "0badc0de turns 1-2, 3-3 of 3" in lines
    part = (tmp_path / "packets" / "packets" / f"{MORNING}.turns-3-3.md").read_text()
    assert "only turns 3 to 3 of the session's 3" in part
    assert "## Turn 3 " in part and "## Turn 1 " not in part


def test_parts_cut_evenly_and_never_leave_a_stub():
    def runs(numbers, size):
        return [(c[0], c[-1]) for c in backfill._chunks(list(numbers), size)]

    assert runs(range(1, 53), 26) == [(1, 26), (27, 52)]
    assert runs(range(1, 148), 80) == [(1, 74), (75, 147)]
    assert runs(range(5, 8), 80) == [(5, 7)]
    assert backfill._chunks([2, 9, 30], 2) == [[2, 9], [30]]


def test_a_session_read_in_part_hands_out_only_its_unread_turns(book, tmp_path):
    flag = {"slug": "cooling-asymmetry", "kind": "correct", "confidence": "high"}
    first = _lines(
        {"session": MORNING, "turn": 1, "sections": [flag]},
        {"session": MORNING, "turn": 3, "sections": [flag]},
        {"session": MORNING, "done": True, "about": "The first two turns."},
    )
    [row, _] = _read(tmp_path, [first], turns=2)
    assert row["read"] == 1 and row["dropped"][0]["why"] == "no such author turn"
    read = backfill._reads()[0]
    assert read["turns"] == 2 and len(read["seen"]) == 2 and "part" not in read
    assert [c["turn"] for c in read["candidates"]] == [1]
    backfill.plan(tmp_path / "next", harness="codex", model="luna", effort="low")
    plan = json.loads((tmp_path / "next" / "plan.json").read_text())
    [seat] = plan["seats"]
    assert seat["metadata"]["trail_read"]["parts"] == {MORNING: [3]}
    assert "1 of    2 read" not in "\n".join(backfill.status())
    assert "1 read only in part" in "\n".join(backfill.status())


ASK = "toolu_kettle_route"


def _answered(project):
    """The morning session as a reader that counts answers lists it.

    Between the first two turns the model asks a question with options, and
    the author's pick, logged as the question tool's result, becomes Human 2.
    """
    day = "2031-04-02"
    records = [
        {"type": "user", "message": {"content": MORNING_TURNS[0][1]}},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": MORNING_TURNS[1][1]}]},
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": ASK,
                        "name": "AskUserQuestion",
                        "input": {
                            "questions": [
                                {
                                    "question": "Which route?",
                                    "options": [
                                        {"label": "Cooling", "description": ""}
                                    ],
                                }
                            ]
                        },
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": ASK,
                        "content": 'Your questions have been answered: "Which route?"='
                        '"Cooling". You can now continue with these answers in mind.',
                    }
                ]
            },
        },
        {"type": "user", "message": {"content": MORNING_TURNS[2][1]}},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": MORNING_TURNS[3][1]}]},
        },
        {"type": "user", "message": {"content": MORNING_TURNS[4][1]}},
    ]
    clocks = [
        "10:00:05.120",
        "10:01:00",
        "10:01:30",
        "10:02:00",
        "10:05:30",
        "10:06:00",
        "10:09:12.500",
    ]
    for record, clock in zip(records, clocks):
        record["timestamp"] = f"{day}T{clock}Z"
    keep_session(
        project,
        f"cc-sessions/{MORNING}.jsonl",
        "\n".join(json.dumps(r) for r in records) + "\n",
    )


def _old_read(project, sid, turns, flags=(), part=None):
    """A read as the ledger kept one before reads recorded their turns' keys."""
    read = {
        "session": sid,
        "harness": "Claude Code",
        "turns": turns,
        "about": None,
        "candidates": [
            {
                "turn": number,
                "logged": logged,
                "sections": [
                    {
                        "slug": "cooling-asymmetry",
                        "kind": "correct",
                        "confidence": "high",
                        "reason": "A route.",
                    }
                ],
            }
            for number, logged in flags
        ],
        **({"part": {"first": part[0], "last": part[1]}} if part else {}),
        "packet_version": backfill.PACKET_VERSION,
        "slugs": ["kettle-signal", "cooling-asymmetry"],
    }
    target = project.path("manuscript.spec_trail_leads") / "reads" / sid
    target.mkdir(parents=True, exist_ok=True)
    (target / f"old-{len(list(target.iterdir())) + 1:03d}.json").write_text(
        json.dumps(read)
    )


def _handed(out):
    """``{sid: parts}`` of the plan in ``out``, or ``None`` for a whole session."""
    plan = json.loads((out / "plan.json").read_text())
    handed = {}
    for seat in plan["seats"]:
        meta = seat["metadata"]["trail_read"]
        for sid in meta["sessions"]:
            handed[sid] = meta.get("parts", {}).get(sid)
    return handed


def test_a_turn_is_known_by_its_instant_or_else_by_its_words():
    at = datetime(2031, 4, 2, 10, 0, tzinfo=timezone.utc)
    later = at.replace(minute=1)
    keys = backfill._keys(
        [
            (at, "Copper."),
            (at, "Steel."),
            (later, "Yes."),
            (None, "Yes."),
            (None, "Yes."),
        ]
    )
    assert keys[2] == "2031-04-02T10:01:00.000000+00:00"
    assert keys[0] != keys[1] and keys[0].startswith("2031-04-02T10:00:00")
    assert keys[3].startswith("untimed/") and keys[4] == keys[3] + "#2"
    assert len(set(keys)) == 5


def test_a_turn_a_session_gains_after_its_read_is_handed_out_alone(book, tmp_path):
    done = _lines(
        {"session": MORNING, "done": True}, {"session": EVENING, "done": True}
    )
    _read(tmp_path, [done])
    _answered(book)
    out = tmp_path / "gained"
    summary = backfill.plan(out, harness="codex", model="luna", effort="low")
    assert _handed(out) == {MORNING: [2]}, "only the answer, at its new number"
    assert summary["partial"] == [(MORNING, 1, 4)] and summary["stale"] == []
    lines = "\n".join(backfill.plan_lines(summary))
    assert "1 session(s) read before hand out only the 1 turn(s) no reader saw" in lines
    packet = (out / "packets" / f"{MORNING}.turns-2-2.md").read_text()
    assert "only turns 2 to 2 of the session's 4" in packet
    assert "## Turn 2 " in packet and "## Turn 3 " not in packet
    assert '"Which route?"="Cooling"' in packet


def test_a_flag_is_kept_at_the_turn_its_packet_held(book, tmp_path):
    _read(
        tmp_path / "first",
        [
            _lines(
                {"session": MORNING, "done": True}, {"session": EVENING, "done": True}
            )
        ],
    )
    _answered(book)
    flag = {"slug": "cooling-asymmetry", "kind": "accept", "confidence": "high"}
    [row] = _read(
        tmp_path,
        [
            _lines(
                {"session": MORNING, "turn": 2, "sections": [flag]},
                {"session": MORNING, "turn": 3, "sections": [flag]},
                {"session": MORNING, "done": True},
            )
        ],
    )
    assert [d["why"] for d in row["dropped"]] == ["no such author turn"]
    [read] = [
        r for r in backfill._reads() if r["session"] == MORNING and r["turns"] == 1
    ]
    [candidate] = read["candidates"]
    assert candidate["turn"] == 2
    assert candidate["logged"] == "2031-04-02T10:02:00.000000+00:00"
    with pytest.raises(LookupError, match="no session is left"):
        backfill.plan(tmp_path / "none", harness="codex", model="luna", effort="low")


def test_an_older_read_is_matched_to_the_reader_it_ran_on(book, tmp_path):
    _answered(book)
    # Read when answers were not turns: three of them, the last at 10:09:12.
    _old_read(book, MORNING, 3, flags=[(3, "2031-04-02T10:09:12.500000+00:00")])
    # Two turns, but the session holds one under every reader: it fits none.
    _old_read(book, EVENING, 2)
    out = tmp_path / "old"
    summary = backfill.plan(out, harness="codex", model="luna", effort="low")
    assert _handed(out) == {MORNING: [2], EVENING: None}
    assert summary["stale"] == [EVENING]
    lines = "\n".join(backfill.plan_lines(summary))
    assert "1 session(s) are read whole again" in lines and "0badc0de" in lines
    assert "will be read whole again" in "\n".join(backfill.status())


def test_an_older_part_keeps_only_the_turns_every_fitting_reader_agrees_on(
    book, tmp_path
):
    _answered(book)
    # Turns 1 and 2 of a read in parts: with no flag to tell them apart, today's
    # reader (the answer is turn 2) and the older one (turn 2 is at 10:05:30)
    # agree only on turn 1, so turns 2 to 4 are handed out again.
    _old_read(book, MORNING, 2, part=(1, 2))
    out = tmp_path / "unsure"
    backfill.plan(out, harness="codex", model="luna", effort="low")
    assert _handed(out)[MORNING] == [2, 3, 4]
    # A flag on turn 2 at 10:05:30 fits only the older reader.
    _old_read(
        book, MORNING, 2, flags=[(2, "2031-04-02T10:05:30.000000+00:00")], part=(1, 2)
    )
    out = tmp_path / "sure"
    backfill.plan(out, harness="codex", model="luna", effort="low")
    assert _handed(out)[MORNING] == [2, 4]


def test_a_named_session_hands_out_every_turn_only_when_reread(book, tmp_path):
    _read(tmp_path, [_lines({"session": MORNING, "done": True})])
    with pytest.raises(LookupError, match="no session is left"):
        backfill.plan(
            tmp_path / "named",
            harness="codex",
            model="luna",
            effort="low",
            sessions=[MORNING],
        )
    out = tmp_path / "again"
    backfill.plan(
        out,
        harness="codex",
        model="luna",
        effort="low",
        sessions=[MORNING],
        reread=True,
    )
    assert _handed(out) == {MORNING: None}


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
    assert report[0]["read"] == 1 and len(report[0]["dropped"]) == 2
    assert report[0]["unread"] == [EVENING], "no done line, so not read"
    [path] = (book.path("manuscript.spec_trail_leads") / "reads" / MORNING).glob(
        "*.json"
    )
    read = json.loads(path.read_text())
    assert read["model"] == "claude-sonnet-5" and read["requested"] == "sonnet"
    first, third = read["candidates"]
    assert first["logged"] == "2031-04-02T10:00:05.120000+00:00"
    assert [s["slug"] for s in third["sections"]] == ["cooling-asymmetry"]
    assert path.stat().st_mode & 0o077 == 0, "the ledger is its owner's alone"


KEYRING = (
    "Session bus proxied into the jail for org.freedesktop.secrets: the "
    "operator's login keyring is readable by the seat."
)


def test_a_seat_with_an_unexpected_red_flag_is_held_and_records_nothing(book, tmp_path):
    report = _read(tmp_path, [ANSWER], flags=["Compaction occurred: review it."])
    assert report[0]["held"] == ["Compaction occurred: review it."]
    assert report[0]["read"] == 0
    assert not (
        book.path("manuscript.spec_trail_leads") / "reads"
    ).exists() or not list(
        (book.path("manuscript.spec_trail_leads") / "reads").rglob("*.json")
    )
    assert "HELD" in "\n".join(backfill.import_lines(report))


def test_an_older_record_imports_past_the_keyring_flag_only_when_told(book, tmp_path):
    held = _read(tmp_path, [ANSWER], flags=[KEYRING])
    assert held[0]["held"] == [KEYRING], "its plan could not declare the flag"
    report = backfill.import_run(tmp_path / "record", accept=["session-bus"])
    assert report[0]["read"] == 1 and report[0]["accepted"] == [KEYRING]


def test_import_refuses_a_flag_code_it_does_not_know(book, tmp_path):
    with pytest.raises(ValueError, match="no such flag code"):
        _read(tmp_path, [ANSWER], accept=["compaction"])


def test_the_pass_summary_carries_the_numbers_a_ledger_commit_reports():
    def row(seat, turns, flagged, empty=()):
        return {
            "seat": seat,
            "answered": True,
            "held": [],
            "accepted": ["keyring"],
            "model": "fixture-flash",
            "read": 2,
            "unread": [],
            "dropped": [{"line": "{}", "why": "not a spec section"}],
            "turns": turns,
            "flagged": flagged,
            "empty": list(empty),
            "sections": [("kettle-signal", "high")] * flagged,
            "sessions": [
                {
                    "session": seat + "-a",
                    "turns": turns,
                    "flagged": flagged,
                    "about": None if flagged < 2 else "Tuning the kettle.",
                    "title": f"{seat} title",
                }
            ],
        }

    lines = backfill.import_lines(
        [row("read-001", 30, 9), row("read-002", 40, 1, [(EVENING, 12)])]
    )
    text = "\n".join(lines)
    assert "dropped (not a spec section)" in text
    assert "4 of 4 session(s) read in 2 seat(s) on fixture-flash" in text
    assert "10 section flag(s) on 10 of 70 author turn(s)" in text
    assert "1 session(s) with no flag" in text
    [warned] = [i for i, line in enumerate(lines) if "flagged under" in line]
    assert "read-002" in lines[warned - 2], "only the seat that read almost nothing"
    assert "read-002 title" in lines[warned + 1], "each session beside its title"
    assert lines[warned + 2].strip() == "about: (no about line)"
    assert "read-001 title" not in text, "a seat that flagged is not listed"
    assert "1 done line(s) without an about sentence" in text


def test_a_done_line_keeps_the_readers_account_of_the_session(book, tmp_path):
    about = {"session": MORNING, "done": True, "about": "  Shaping the kettle  "}
    _read(tmp_path, [_lines(about, {"session": EVENING, "done": True})])
    leads = book.path("manuscript.spec_trail_leads") / "reads"
    [morning] = (leads / MORNING).glob("*.json")
    [evening] = (leads / EVENING).glob("*.json")
    assert json.loads(morning.read_text())["about"] == "Shaping the kettle"
    assert json.loads(evening.read_text())["about"] is None, "read, but unproven"


def test_the_instructions_ask_for_an_account_of_each_session():
    assert '"done": true, "about": "<one sentence>"' in backfill.INSTRUCTIONS


def test_a_new_section_does_not_send_the_corpus_out_again(book, tmp_path):
    _read(tmp_path, [ANSWER])
    spec = book.path("manuscript.spec")
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


def test_import_says_when_an_answer_was_cut_off(book, tmp_path):
    flag = {"slug": "kettle-signal", "kind": "seed", "confidence": "high"}
    whole = json.dumps({"session": MORNING, "turn": 1, "sections": [flag]})
    cut = whole + "\n" + whole[:40] + "\n"
    [row] = _read(tmp_path, [cut])
    assert row["read"] == 0 and row["unread"] == [MORNING, EVENING]
    assert row["cut"] == {"output": None, "thinking": None}
    lines = "\n".join(backfill.import_lines([row]))
    assert "! the answer stops mid-line: the reader ran out of room" in lines
    assert "(--turns)" in lines
    alone = dict(row, handed=1)
    lines = "\n".join(backfill.import_lines([alone]))
    assert "It held one session" in lines and "it is read in parts" in lines


def test_an_answer_that_ends_is_not_cut_off():
    done = json.dumps({"session": MORNING, "done": True})
    assert not backfill._cut_off(done + "\n")
    assert not backfill._cut_off(done + "\n```\n")
    assert backfill._cut_off(done + '\n{"session": "0badc0de", "tu')


def test_status_counts_the_sessions_read_and_the_sections_flagged(book, tmp_path):
    _read(tmp_path, [ANSWER])
    text = "\n".join(backfill.status())
    assert "claude-code  work       1 of    2 read" in text
    assert "#cooling-asymmetry" in text and "1 flagged" in text


def test_the_command_line_prints_a_sections_worklist(book, tmp_path, capsys):
    _read(tmp_path, [ANSWER])
    assert trail.main(["backfill", "cooling-asymmetry", "--no-commits", "--utc"]) == 0
    out = capsys.readouterr().out
    assert "1 candidate(s) to rule on" in out
    assert "claude-sonnet-5: correct (high) Refuses speed as the difference." in out
    assert "quirework provenance show 0badc0de --turn 3" in out


def test_a_neighbours_flags_join_the_worklist_when_asked(book, tmp_path):
    _read(tmp_path, [ANSWER])
    pending, *_ = backfill.worklist("kettle-signal", commits=False)
    assert [c.turn.number for c in pending] == [1]
    pending, *_ = backfill.worklist(
        "kettle-signal", commits=False, also=["cooling-asymmetry"]
    )
    assert [c.turn.number for c in pending] == [1, 3]
    lines = backfill._worklist_lines(
        "kettle-signal",
        commits=False,
        everything=False,
        utc=True,
        also=["cooling-asymmetry"],
    )
    assert "claude-sonnet-5 [#cooling-asymmetry]: correct (high)" in "\n".join(lines)
    with pytest.raises(LookupError, match="no section"):
        backfill.worklist("kettle-signal", commits=False, also=["no-such"])


def test_a_worklist_can_show_each_turn_with_the_reply_it_answered(
    book, tmp_path, capsys
):
    _read(tmp_path, [ANSWER])
    assert trail.main(["backfill", "cooling-asymmetry", "--no-commits", "--read"]) == 0
    out = capsys.readouterr().out
    assert "    > Noted as its own section." in out
    assert "    No, not slower. A route, not a speed." in out
    assert "  opens  " not in out
    assert "quirework provenance show 0badc0de --turn 3" in out


RESENT = "a0000000-0000-4000-8000-00000000000a"
FORK = "a0000000-0000-4000-8000-00000000000b"


def test_a_resent_turn_and_a_twice_captured_turn_are_noted(book, tmp_path):
    same = "Cooling is a route, not a speed."
    _session(
        book,
        RESENT,
        "2031-04-04",
        [
            ("human", same, "09:00:00"),
            ("human", same, "09:02:00"),
            ("assistant", "A route it is.", "09:03:00"),
            ("human", "Thanks.", "09:04:00"),
        ],
    )
    _session(book, FORK, "2031-04-04", [("human", same, "09:02:00")])
    flag = {"slug": "cooling-asymmetry", "kind": "seed", "confidence": "high"}
    answer = _lines(
        {"session": RESENT, "turn": 1, "sections": [flag]},
        {"session": RESENT, "turn": 2, "sections": [flag]},
        {"session": RESENT, "done": True},
        {"session": FORK, "turn": 1, "sections": [flag]},
        {"session": FORK, "done": True},
    )
    _read(tmp_path, [answer], sessions=[RESENT, FORK])
    pending, *_ = backfill.worklist("cooling-asymmetry", commits=False)
    notes = {(c.turn.session, c.turn.number): c.notes for c in pending}
    assert notes[(RESENT, 1)] == ["same words as Human 2; this copy drew none"]
    assert "same words as Human 1; this copy drew a reply" in notes[(RESENT, 2)]
    assert "the same turn is captured again as a0000000 Human 1" in notes[(RESENT, 2)]
    assert notes[(FORK, 1)] == ["the same turn is captured again as a0000000 Human 2"]


def test_a_sections_rulings_apply_from_a_file_all_or_none(book, tmp_path):
    _read(tmp_path, [ANSWER])
    good = [
        json.dumps({"keep": "seed", "session": MORNING, "turn": 1, "note": "Opens."}),
        json.dumps({"reject": "Not the claim.", "session": MORNING, "turn": 2}),
    ]
    bad = good + [json.dumps({"keep": "seed", "session": MORNING, "turn": 99})]
    with pytest.raises(LookupError, match="nothing was written") as caught:
        backfill.rule("kettle-signal", bad)
    assert "line 3" in str(caught.value)
    assert not book.path("manuscript.spec_trail").exists()
    assert backfill.rejections("kettle-signal") == []
    kept, dropped = backfill.rule("kettle-signal", ["# a comment", *good])
    assert [t.number for t in kept] == [1] and [t.number for t in dropped] == [2]
    with pytest.raises(LookupError, match="already rejected"):
        backfill.rule("kettle-signal", good[1:])


def test_a_turn_cannot_be_kept_and_rejected_in_one_file(book):
    both = [
        json.dumps({"keep": "seed", "session": MORNING, "turn": 1}),
        json.dumps({"reject": "No.", "session": MORNING, "turn": 1}),
    ]
    with pytest.raises(LookupError, match="both kept and rejected"):
        backfill.rule("kettle-signal", both)


REDPEN_ROUND = """# Red-pen round v2 (2031-04-01)

```
source-docx:    V2 - The Kettle.docx
marks-dated:    2031-04-01
```

## MARKS  (`redpen read`)

### COMMENTS (1)

[A. Fictional Author] 2031-04-01  (on text)
  edit/anchor: “It whistles.”
  note: A report, not a thought.
"""


def _thawed(value):
    if hasattr(value, "items"):
        return {k: _thawed(v) for k, v in value.items()}
    return value


def test_a_red_pen_round_is_read_in_date_order_with_its_parts_explained(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    settings = _thawed(_book(tmp_path).settings)
    settings["provenance"]["redpen"] = "private/redpen"
    archive = tmp_path / "book" / "private" / "redpen"
    archive.mkdir(parents=True)
    (archive / "v2-2031-04-01.md").write_text(REDPEN_ROUND, encoding="utf-8")
    with project_scope(Project.from_settings(tmp_path / "book", settings)):
        done = backfill.plan(
            tmp_path / "packets", harness="claude", model="sonnet", effort="medium"
        )
        plan = json.loads((tmp_path / "packets" / "plan.json").read_text())
        order = plan["seats"][0]["metadata"]["trail_read"]["sessions"]
        assert order == ["redpen-v2-2031-04-01", MORNING, EVENING]
        assert not done["undated"], "a round carries the day of its marks"
        text = (
            tmp_path / "packets" / "packets" / "redpen-v2-2031-04-01.md"
        ).read_text()
        assert "redpen · started 2031-04-01 · 1 author turn(s)" in text
        assert "This is not a conversation. It is a red-pen round" in text
        flat = " ".join(text.split())
        assert 'a "+ now" line can be something the copy already held' in flat
        assert "note: A report, not a thought." in text
        assert "could not be read" in text, "a round with no revision says so"
        labels = [m["label"] for m in plan["seats"][0]["turns"][0]["materials"]]
        assert "redpen-v2-2031-04-01" in labels, "a round is named whole"


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
def test_a_ledger_needs_a_trail_and_says_when_it_is_not_configured(tmp_path):
    project = _book(tmp_path, leads=None)
    with project_scope(project):
        with pytest.raises(ConfigurationError, match="keeps no backfill ledger"):
            backfill.ledger()
    book = dict(project.describe()["settings"]["manuscript"])
    del book["spec_trail"]
    book["spec_trail_leads"] = "private/leads"
    broken = Project.from_settings(project.root, {"manuscript": book})
    with pytest.raises(ConfigurationError, match="keeps leads for a spec trail"):
        broken.require("manuscript")


def test_a_red_pen_packet_leaves_out_lines_the_book_already_held(tmp_path, monkeypatch):
    """A build prints agents' notes into a block; the round then shows them as
    "+ now" lines. The book's text at the round's revision tells them apart."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    root = tmp_path / "book"
    settings = _thawed(_book(tmp_path).settings)
    settings["provenance"]["redpen"] = "private/redpen"
    note = "(pacing) a note an agent wrote - before the round was built"
    marks = root / "manuscript" / "sections" / "_marks" / "opening.md"
    spelled = note.replace(" - ", " -- ")  # the source spells the dash twice
    marks.write_text(marks.read_text() + f"\n<!-- @todo {spelled} -->\n")

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(root), "-c", "user.name=A. Fictional Author"]
            + ["-c", "user.email=author@example.invalid", *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "The kettle book")
    revision = git("rev-parse", "--short=9", "HEAD").strip()
    archive = root / "private" / "redpen"
    archive.mkdir(parents=True)
    (archive / "v3-2031-04-06.md").write_text(
        "# Red-pen round v3 (2031-04-06)\n\n```\n"
        f"canon-sha:      {revision}\nmarks-dated:    2031-04-06\n```\n\n"
        "## MARKS  (`redpen read`)\n\n### REVIEW-NOTES FREE EDITS (1)\n\n"
        "[1. The opening]\n"
        f"  + now: {note}\n"
        "  + now: The kettle whistles for itself, and nobody asked it to.\n",
        encoding="utf-8",
    )
    with project_scope(Project.from_settings(root, settings)):
        sid, family, path, _ = trail.Corpus().session("redpen-v3")
        text = backfill.packet(sid, family, path)
    assert "The kettle whistles for itself, and nobody asked it to." in text
    assert note not in text
    assert f"1 '+ now' line(s) are left out" in text and revision in text
