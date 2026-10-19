"""The board's mechanics: staging, promotion, rotation and integrity."""

import fnmatch
import json
from contextlib import nullcontext
from pathlib import Path

import pytest
from conftest import native_session

from quirework.providers import blind
from quirework.providers import models as drivers
from quirework.providers import quota
from quirework.runs.storage import lock, read, shown, write
from quirework.sops import room


def profile(name, label):
    return {
        "schema_version": 1,
        "id": name,
        "revision": 1,
        "label": label,
        "description": "A fictional seat, invented for these tests.",
        "tags": ["test"],
        "prompt": f"You are {label}. You judge a draft by whether it is honest.",
    }


@pytest.fixture
def plan(tmp_path):
    """A two-seat room whose seats are given deliberately different packets."""
    source = tmp_path / "plan"
    (source / "profiles").mkdir(parents=True)
    for name, text in (("ana-pell", "Ana Pell"), ("bo-larke", "Bo Larke")):
        (source / "profiles" / f"{name}.json").write_text(
            json.dumps(profile(name, text))
        )
    (source / "brief.md").write_text("Argue with each other.\n")
    (source / "proposal.md").write_text("A book about a fictional lighthouse.\n")
    (source / "extra.md").write_text("Only Bo Larke may read this.\n")
    write(
        source / "room.json",
        {
            "title": "Test room",
            "harness": "agy",
            "model": "test-model",
            "effort": "medium",
            "rounds": 3,
            "silent_rounds": [1],
            "post_words": 100,
            "chair": "operator",
            "brief": {"path": "brief.md", "local_source": "brief.md"},
            "shared_files": [{"path": "proposal.md", "local_source": "proposal.md"}],
            "seats": [
                {"id": "ana-pell", "persona": "ana-pell", "files": []},
                {
                    "id": "bo-larke",
                    "persona": "bo-larke",
                    "files": [{"path": "extra.md", "local_source": "extra.md"}],
                },
            ],
        },
    )
    return source / "room.json"


@pytest.fixture
def prepared(plan, tmp_path):
    room.prepare(plan, tmp_path / "room", resolve_models=False)
    return tmp_path / "room"


@pytest.fixture
def prepared_labelled(plan, tmp_path):
    """A room whose private holdings are named for the room, not by path."""
    source = plan.parent
    (source / "reports-gpt-6-astra.md").write_text("An old reader report.\n")
    (source / "plain.md").write_text("Unlabelled.\n")
    spec = read(plan)
    spec["seats"][1]["files"] = [
        {
            "path": "reports/gpt-6-astra.md",
            "local_source": "reports-gpt-6-astra.md",
            "label": "six reader reports on an earlier draft",
        },
        {"path": "notes/plain.md", "local_source": "plain.md"},
    ]
    write(plan, spec)
    room.prepare(plan, tmp_path / "labelled", resolve_models=False)
    return tmp_path / "labelled"


def post(root, seat, text):
    (root / "seats" / seat / "outbox" / "post.md").write_text(text)


def test_a_seat_whose_canary_has_no_model_is_refused_at_prepare(
    plan, tmp_path, monkeypatch
):
    """A seat is proven blind on its runner's cheapest model when it first
    launches, which is a round after the room was staged."""
    monkeypatch.setattr(
        drivers, "resolve", lambda harness, model, effort: dict(harness=harness)
    )
    monkeypatch.setattr(blind, "cheapest_model", lambda runner: None)
    with pytest.raises(RuntimeError, match="^seat ana-pell: .*canary_models"):
        room.prepare(plan, tmp_path / "room")
    assert not (tmp_path / "room").exists()


def test_prepare_ignores_the_seats_runner_homes(prepared):
    """A room may be prepared into a checkout, and a runner home holds keys."""
    declared = [
        line
        for line in (prepared / ".gitignore").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    # The credential itself has to be covered, not merely the directory name:
    # that file is the reason this ignore exists.
    home = prepared / "seats" / "bo-larke.blind-state" / "codex"
    home.mkdir(parents=True)
    (home / "auth.json").write_text("{}")
    relative = str((home / "auth.json").relative_to(prepared))
    assert any(
        fnmatch.fnmatch(relative, pattern.rstrip("/") + "/*") for pattern in declared
    )
    # What sits there now is a link into the cache, which a pattern naming
    # only directories would miss.
    link = "seats/bo-larke.blind-state"
    assert any(fnmatch.fnmatch(link, p) and not p.endswith("/") for p in declared)


def test_compaction_is_detected_and_raised(prepared, tmp_path):
    """A compacted round must not be indistinguishable from a clean one."""
    stream = tmp_path / "events.jsonl"
    stream.write_text(
        '{"type": "assistant", "timestamp": "t0"}\n'
        '{"type": "system", "subtype": "compact_boundary", "timestamp": "t1"}\n'
    )
    assert room.compaction(stream) == [
        {"timestamp": "t1", "tags": ["compact_boundary"]}
    ]
    assert room.compaction(tmp_path / "absent.jsonl") == []


def test_an_unreadable_stream_never_reads_as_clean(prepared, tmp_path):
    """Silence about compaction is a claim, so a broken stream may not make it."""
    stream = tmp_path / "events.jsonl"
    stream.write_text("this is not json\n")
    assert room.compaction(stream) == [
        {"timestamp": None, "tags": ["unreadable-stream"]}
    ]


def test_status_surfaces_a_compacted_round(prepared):
    room.event(prepared, round=2, seat="ana-pell", event="compacted", markers=1)
    value = room.status(prepared)
    assert value["compacted"] == [{"seat": "ana-pell", "round": 2}]
    assert "COMPACTED" in room._render(value)


def test_seats_get_different_packets(prepared):
    """Asymmetric exposure is the point; a shared packet would be a bug."""
    ana = {
        p.name for p in (prepared / "seats/ana-pell/input").rglob("*") if p.is_file()
    }
    bo = {p.name for p in (prepared / "seats/bo-larke/input").rglob("*") if p.is_file()}
    assert ana == {"brief.md", "proposal.md"}
    assert bo == {"brief.md", "proposal.md", "extra.md"}


def test_standing_assignment_carries_the_perspective(prepared):
    text = (prepared / "seats/ana-pell/START.md").read_text()
    assert "You are Ana Pell" in text
    assert "outbox/post.md" in text


def test_promotion_drains_the_outbox_and_records_absence(prepared):
    post(prepared, "ana-pell", "Ana's first position.")
    result = room.promote(prepared, 1)
    assert result["posted"] == ["ana-pell"]
    assert result["absent"] == ["bo-larke"]
    assert not (prepared / "seats/ana-pell/outbox/post.md").exists()
    assert (
        prepared / "board/posts/ana-pell/r001.md"
    ).read_text() == "Ana's first position."


def test_a_promoted_round_cannot_be_promoted_twice(prepared):
    post(prepared, "ana-pell", "Once.")
    room.promote(prepared, 1)
    post(prepared, "ana-pell", "Twice.")
    with pytest.raises(RuntimeError, match="already promoted"):
        room.promote(prepared, 1)


def test_digest_quotes_posts_whole_and_attributes_them(prepared):
    post(prepared, "ana-pell", "Line one.\n\nLine two, which must survive.")
    post(prepared, "bo-larke", "Bo disagrees.")
    room.promote(prepared, 1)
    text = (prepared / "board/rounds/r001/digest.md").read_text()
    assert "Line two, which must survive." in text
    # Attribution is the frame that makes a post a colleague's opinion.
    assert "Ana Pell" in text and "Bo Larke" in text
    # The digest describes no machinery: no rounds mechanics, no promotion,
    # no ordering rule. A writer occupies the room, not the controller.
    for machinery in ("rotating", "promot", "outbox", "seat"):
        assert machinery not in text.lower()


def test_board_order_rotates_so_no_seat_always_leads(prepared):
    _, plan = room.load(prepared)
    assert room.board_order(plan["seats"], 1)[0] == "ana-pell"
    assert room.board_order(plan["seats"], 2)[0] == "bo-larke"
    assert room.board_order(plan["seats"], 3)[0] == "ana-pell"


def test_the_first_round_shows_no_board(prepared):
    assert room.open_round(prepared, 1) is None


def test_a_round_cannot_open_before_the_one_before_it_closed(prepared):
    with pytest.raises(RuntimeError, match="has not been promoted"):
        room.open_round(prepared, 2)


def test_opening_a_round_puts_the_digest_in_every_workspace(prepared):
    post(prepared, "ana-pell", "A position.")
    room.promote(prepared, 1)
    room.open_round(prepared, 2)
    for seat in ("ana-pell", "bo-larke"):
        assert (prepared / "seats" / seat / "board/round-001/digest.md").exists()


def test_a_changed_packet_stops_the_seat(prepared):
    _, plan = room.load(prepared)
    (prepared / "seats/ana-pell/input/proposal.md").write_text("swapped")
    seat = next(s for s in plan["seats"] if s["id"] == "ana-pell")
    with pytest.raises(RuntimeError, match="seat input changed"):
        room.run_seat(prepared, plan, seat, 1)


def test_a_seat_with_no_session_joins_the_room_in_progress(prepared):
    """A refusal before the model ran leaves nothing to continue, not a seat.

    The alternative the controller used to offer was to lose that seat for
    every remaining round, or to throw away the turns its colleagues had
    already bought and start the room again.
    """
    _, plan = room.load(prepared)
    seat = next(s for s in plan["seats"] if s["id"] == "ana-pell")
    prompt = room.turn_prompt(prepared, plan, seat, 2, cold=True)
    assert "Read START.md" in prompt
    assert "joining a room already in progress" in prompt
    assert "board/round-001/digest.md" in prompt
    # It must not be told the others were answering it.
    assert "nothing it has said is a reply to you" in prompt


def test_a_late_arrival_is_named_as_one_in_status(prepared):
    """A late seat's first post is a reply, and a reading must not take it
    for an opening position."""
    state = room.state_path(prepared, "ana-pell")
    value = read(state)
    value["rounds"] = {"1": {"status": "failed"}, "2": {"status": "answered"}}
    write(state, value)
    assert room.status(prepared)["seats"]["ana-pell"]["joined_late"] == 2


def test_the_chair_reaches_a_round_it_speaks_to(prepared):
    """The packet is hashed, so a fact found after preparation has no other
    way in, and four seats of the first measured room asked for one."""
    _, plan = room.load(prepared)
    seat = next(s for s in plan["seats"] if s["id"] == "ana-pell")
    assert room.chair_note(prepared, 1) is None
    (prepared / "chair" / "r001.md").write_text("The coined phrase is five words.\n")
    assert "five words" in room.turn_prompt(prepared, plan, seat, 1)
    post(prepared, "ana-pell", "A position.")
    room.promote(prepared, 1)
    digest = (prepared / "board" / "rounds" / "r001" / "digest.md").read_text()
    assert "## From the chair" in digest
    assert digest.index("From the chair") < digest.index("A position.")


def test_an_empty_chair_note_is_no_note(prepared):
    """A stray empty file must not announce a chair who said nothing."""
    (prepared / "chair" / "r001.md").write_text("   \n")
    assert room.chair_note(prepared, 1) is None


def test_status_reads_a_locked_room_without_blocking(prepared):
    """Watching must never contend with the controller that is running."""
    post(prepared, "ana-pell", "A position.")
    room.promote(prepared, 1)
    with lock(prepared / "room.lock"):
        value = room.status(prepared)
    assert value["published_rounds"] == ["r001"]
    assert value["seats"]["ana-pell"]["posts"] == ["r001.md"]


def test_the_trail_is_one_whole_json_object_per_line(prepared):
    post(prepared, "ana-pell", "A position.")
    room.promote(prepared, 1)
    rows = [
        json.loads(line) for line in (prepared / "room.log").read_text().splitlines()
    ]
    assert {r["event"] for r in rows} >= {
        "prepared",
        "promoted",
        "absent",
        "round-closed",
    }


@pytest.fixture
def project_rooms(tmp_path):
    """A project whose room store is inside the test's own tree."""
    from quirework.project import Project, project_scope

    project = Project.from_settings(
        tmp_path / "project", {"runs": {"store": str(tmp_path / "state")}}
    )
    with project_scope(project):
        yield project.state_root / room.ROOMS


def test_a_bare_name_is_a_room_the_project_owns(plan, project_rooms):
    """Rooms live where nobody navigates by hand, so a name has to resolve."""
    room.prepare(plan, project_rooms / "named", resolve_models=False)
    assert room.room_root("named") == (project_rooms / "named").resolve()


def test_a_path_is_taken_as_a_path_not_a_name(project_rooms, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError) as caught:
        room.room_root("./named")
    assert shown(tmp_path / "named") in str(caught.value)


def test_an_unknown_name_says_where_it_looked(project_rooms):
    with pytest.raises(ValueError) as caught:
        room.room_root("absent")
    assert shown(project_rooms) in str(caught.value)


def test_watching_a_room_that_is_not_there_fails_instead_of_following_nothing(
    project_rooms,
):
    """A watcher that silently tails a path nobody will ever write is a trap."""
    with pytest.raises(ValueError, match="no room at"):
        room.watch("absent")


def render(row, **kwargs):
    return room._agy_lines(row, **{"thinking": True, "width": 76, **kwargs})


def test_a_tool_result_is_not_part_of_the_conversation():
    """A single file read returns kilobytes of quoted source; it is noise."""
    row = {"type": "GENERIC", "status": "DONE", "content": "File Path: ..." * 200}
    assert render(row) == []


def test_an_action_reads_as_one_line_in_plain_words():
    row = {
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "tool_calls": [{"name": "view_file", "args": {"toolSummary": "Read brief"}}],
    }
    assert render(row) == ["  →  Read brief"]


def test_reasoning_can_be_turned_off():
    row = {"type": "PLANNER_RESPONSE", "status": "DONE", "thinking": "Weighing it up."}
    assert render(row, thinking=True)
    assert render(row, thinking=False) == []


def test_the_prompt_loses_the_harness_bookkeeping():
    row = {
        "type": "USER_INPUT",
        "status": "DONE",
        "content": (
            "<USER_REQUEST>Write your post.</USER_REQUEST>"
            "<ADDITIONAL_METADATA> The current local time is: 2026-01-01."
            " </ADDITIONAL_METADATA>"
        ),
    }
    shown = "\n".join(render(row))
    assert "Write your post." in shown
    assert "local time" not in shown


def test_an_error_shows_the_reason_not_the_timestamps():
    row = {
        "type": "GENERIC",
        "status": "ERROR",
        "content": "Created At: 2026-01-01\nCompleted At: 2026-01-01\nno such file",
    }
    shown = "\n".join(render(row))
    assert "no such file" in shown
    assert "Created At" not in shown


def test_following_an_unknown_seat_names_the_seats(prepared):
    with pytest.raises(ValueError, match="this room seats"):
        room.follow(prepared, "nobody")


def test_following_an_unsupported_harness_says_so(plan, tmp_path):
    """It must raise before the polling loop, or it follows nothing forever.

    Named harnesses all have renderers now, so this uses one that does not
    exist. An earlier version of this test named claude, and the moment claude
    gained a renderer the test stopped asserting anything and hung the suite.
    """
    supplied = json.loads(plan.read_text())
    supplied["harness"] = "invented-harness"
    plan.write_text(json.dumps(supplied))
    room.prepare(plan, tmp_path / "odd-room", resolve_models=False)
    with pytest.raises(ValueError, match="not implemented"):
        room.follow(tmp_path / "odd-room", "ana-pell")


def test_every_supported_harness_can_be_followed():
    """A room may mix harnesses, so a renderer missing for one is a gap."""
    from quirework.runs import observe as reader

    assert set(room._RENDERERS) == set(reader.NATIVE_TRANSCRIPTS)


# What a writer is handed must read as a room, not as a procedure being run.
# These are the words that leaked in the first version: a machine header above
# the persona, and our own scheduling and plumbing vocabulary in the prompts.
MACHINERY = ("PERSPECTIVE", "seat", "silent round", "promot", "digest", "SOP")


def prose(text):
    """The words a writer reads, minus the paths it has to be given.

    A workspace path necessarily contains `seats/` and a board file is named
    `digest.md`, so checking raw text would fail on the one part that has to
    say those things. What matters is that the prose never does.
    """
    return " ".join(word for word in text.split() if "/" not in word)


def test_the_standing_assignment_names_no_machinery(prepared):
    text = (prepared / "seats/ana-pell/START.md").read_text()
    assert "You are Ana Pell" in text
    assert not text.startswith("===")
    for word in MACHINERY:
        assert word.lower() not in prose(text).lower(), word


def test_a_turn_prompt_names_no_machinery(prepared):
    _, plan = room.load(prepared)
    seat = next(s for s in plan["seats"] if s["id"] == "ana-pell")
    for number in (1, 2):
        text = room.turn_prompt(prepared, plan, seat, number)
        for word in MACHINERY:
            assert word.lower() not in prose(text).lower(), (number, word)


def test_a_later_turn_does_not_repeat_the_standing_assignment(prepared):
    """The session already holds it; resending it buys nothing every round."""
    _, plan = room.load(prepared)
    seat = next(s for s in plan["seats"] if s["id"] == "ana-pell")
    later = room.turn_prompt(prepared, plan, seat, 2)
    assert str(plan["post_words"]) not in later
    assert len(later) < 250


def test_a_first_round_never_reports_convergence(prepared):
    """One round cannot show a trend; both signals need two to fire."""
    post(prepared, "ana-pell", "A position.")
    post(prepared, "bo-larke", "Another position.")
    signal = room.promote(prepared, 1)["convergence"]
    assert signal["converged"] is False
    assert signal["novelty"] == 100.0


def test_repetition_stops_the_room(prepared):
    """A room that restates itself twice running has nothing left to say."""
    for number in (1, 2, 3):
        for seat, text in (
            ("ana-pell", "The structure is wrong and the spine is thin."),
            ("bo-larke", "The structure is wrong and the spine is thin."),
        ):
            post(prepared, seat, text)
        signal = room.promote(prepared, number)["convergence"]
    assert signal["converged"] is True
    assert signal["reason"] == "novelty"


def test_one_round_of_agreement_is_not_convergence(prepared):
    """A measured room hit three-quarters closing, then went back to arguing."""
    post(prepared, "ana-pell", "The verdict is unanimous, adjourn the room.")
    post(prepared, "bo-larke", "Agreed entirely; our work here is settled.")
    assert room.promote(prepared, 1)["convergence"]["converged"] is False
    post(prepared, "ana-pell", "Actually no. Reopen it: the second act collapses.")
    post(prepared, "bo-larke", "You are mistaken about that, and here is why.")
    signal = room.promote(prepared, 2)["convergence"]
    assert signal["converged"] is False


def test_a_seat_may_name_its_own_model(plan, tmp_path):
    supplied = json.loads(plan.read_text())
    supplied["seats"][1]["harness"] = "codex"
    supplied["seats"][1]["model"] = "gpt-fictional"
    plan.write_text(json.dumps(supplied))
    room.prepare(plan, tmp_path / "mixed", resolve_models=False)
    _, frozen = room.load(tmp_path / "mixed")
    configs = {s["id"]: room.seat_config(frozen, s) for s in frozen["seats"]}
    assert configs["ana-pell"]["harness"] == "agy"
    assert configs["bo-larke"]["harness"] == "codex"
    assert configs["bo-larke"]["model"] == "gpt-fictional"


def test_claude_rows_render_words_actions_and_the_turn():
    prompt = {"type": "user", "message": {"content": "The board has moved."}}
    action = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "/a/b/brief.md"},
                }
            ]
        },
    }
    words = {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": "Posted. The structure is wrong."}]
        },
    }
    result = {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "content": "x" * 5000}]},
    }
    assert "The board has moved." in " ".join(
        room._claude_lines(prompt, thinking=True, width=76)
    )
    assert room._claude_lines(action, thinking=True, width=76) == ["  →  Read brief.md"]
    assert "Posted." in " ".join(room._claude_lines(words, thinking=True, width=76))
    assert room._claude_lines(result, thinking=True, width=76) == []


def test_codex_rows_drop_the_cli_preamble_and_tool_output():
    """The CLI injects skills and plugin blocks as developer *and* user turns."""

    def row(payload):
        return {"type": "response_item", "payload": payload}

    preamble_dev = row(
        {
            "type": "message",
            "role": "developer",
            "content": [{"text": "<skills_instructions>...</skills_instructions>"}],
        }
    )
    preamble_user = row(
        {
            "type": "message",
            "role": "user",
            "content": [{"text": "<recommended_plugins>...</recommended_plugins>"}],
        }
    )
    turn = row(
        {
            "type": "message",
            "role": "user",
            "content": [{"text": "The board has moved."}],
        }
    )
    said = row(
        {
            "type": "message",
            "role": "assistant",
            "content": [{"text": "I disagree with Mara."}],
        }
    )
    output = row({"type": "custom_tool_call_output", "output": [{"text": "y" * 4000}]})
    assert room._codex_lines(preamble_dev, thinking=True, width=76) == []
    assert room._codex_lines(preamble_user, thinking=True, width=76) == []
    assert room._codex_lines(output, thinking=True, width=76) == []
    assert "The board has moved." in " ".join(
        room._codex_lines(turn, thinking=True, width=76)
    )
    assert "I disagree with Mara." in " ".join(
        room._codex_lines(said, thinking=True, width=76)
    )


def test_a_codex_action_reads_as_the_command_it_ran():
    call = {
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "exec",
            "input": "const r = await tools.exec_command({cmd:\"sed -n '1,9p' START.md\"});",
        },
    }
    assert room._codex_lines(call, thinking=True, width=76) == [
        "  →  sed -n '1,9p' START.md"
    ]


@pytest.fixture
def staged(plan, tmp_path):
    """A room with a drafting phase: one seat writes long, the other listens."""
    supplied = json.loads(plan.read_text())
    supplied["rounds"] = 4
    supplied["phases"] = [
        {"name": "talk", "rounds": 2},
        {
            "name": "draft",
            "rounds": 1,
            "seats": ["bo-larke"],
            "words": 900,
            "instruction": "Write the passage itself, as prose.",
        },
        {"name": "review", "rounds": 1, "seats": ["ana-pell"]},
    ]
    plan.write_text(json.dumps(supplied))
    room.prepare(plan, tmp_path / "phased", resolve_models=False)
    return tmp_path / "phased"


def test_a_phase_may_seat_only_part_of_the_room(staged):
    _, frozen = room.load(staged)
    assert room.acting(frozen, 2) == ["ana-pell", "bo-larke"]
    assert room.acting(frozen, 3) == ["bo-larke"]
    assert room.acting(frozen, 4) == ["ana-pell"]


def test_a_drafting_phase_carries_its_own_length_and_instruction(staged):
    _, frozen = room.load(staged)
    seat = next(s for s in frozen["seats"] if s["id"] == "bo-larke")
    text = room.turn_prompt(staged, frozen, seat, 3)
    assert "900 words" in text
    assert "Write the passage itself, as prose." in text


def test_a_seat_that_sat_out_is_told_what_it_missed(staged):
    for n in (1, 2):
        post(staged, "ana-pell", f"Ana in round {n}.")
        post(staged, "bo-larke", f"Bo in round {n}.")
        room.promote(staged, n)
    post(staged, "bo-larke", "The passage itself, drafted alone.")
    room.promote(staged, 3)
    _, frozen = room.load(staged)
    seat = next(s for s in frozen["seats"] if s["id"] == "ana-pell")
    assert "did not post in round 3" in room.turn_prompt(staged, frozen, seat, 4)


def test_a_resting_seat_is_not_absent_and_does_not_count_as_repetition(staged):
    post(staged, "ana-pell", "A first position on the shape of it.")
    post(staged, "bo-larke", "A different first position entirely.")
    room.promote(staged, 1)
    post(staged, "ana-pell", "Second thoughts about the second act.")
    post(staged, "bo-larke", "Answering that with a structural objection.")
    room.promote(staged, 2)
    post(staged, "bo-larke", "Here is the passage, freshly drafted prose.")
    result = room.promote(staged, 3)
    # Ana rested. She is neither absent nor a silent seat dragging novelty down.
    assert result["absent"] == []
    assert result["posted"] == ["bo-larke"]
    assert result["convergence"]["novelty"] is not None


def test_phase_rounds_must_add_up(plan, tmp_path):
    supplied = json.loads(plan.read_text())
    supplied["rounds"] = 5
    supplied["phases"] = [{"name": "talk", "rounds": 2}]
    plan.write_text(json.dumps(supplied))
    with pytest.raises(ValueError, match="add up"):
        room.prepare(plan, tmp_path / "bad", resolve_models=False)


def test_the_room_is_told_who_brought_what(prepared):
    """Asymmetric packets make a colleague's citation impossible to check.

    Bo holds a file Ana does not. Without a roster, Ana cannot tell a private
    source from an invention, and a room instructed to cite its sources will
    eventually accuse someone of fabricating one. This happened in a measured
    room: a seat correctly observed that a cited comparable title was not in
    the brief, and escalated toward calling it fiction, when the colleague did
    hold the file in a packet that seat had never been shown.
    """
    text = (prepared / "seats/ana-pell/START.md").read_text()
    assert "In the room, and what each person brought" in text
    assert "Bo Larke: extra.md" in text
    assert "Ana Pell: nothing the rest of you lack" in text


def test_the_roster_never_leaks_a_packet_into_a_seat_that_lacks_it(prepared):
    """Naming a file is not handing it over; only the title is shared."""
    text = (prepared / "seats/ana-pell/START.md").read_text()
    assert "Only Bo Larke may read this" not in text
    assert not (prepared / "seats/ana-pell/input/extra.md").exists()


def test_a_seat_can_be_named_by_its_position(prepared):
    _, plan = room.load(prepared)
    assert room.seat_named(plan, 1) == "ana-pell"
    assert room.seat_named(plan, "2") == "bo-larke"
    assert room.seat_named(plan, "bo-larke") == "bo-larke"


def test_an_id_that_looks_like_a_number_beats_the_position(plan, tmp_path):
    """A seat's own name is never a guess, so it wins over an index."""
    supplied = json.loads(plan.read_text())
    supplied["seats"][1]["id"] = "1"
    plan.write_text(json.dumps(supplied))
    room.prepare(plan, tmp_path / "numbered", resolve_models=False)
    _, frozen = room.load(tmp_path / "numbered")
    assert room.seat_named(frozen, "1") == "1"
    assert room.seat_named(frozen, 2) == "1"


def test_an_unknown_seat_lists_the_numbers_to_use(prepared):
    _, plan = room.load(prepared)
    with pytest.raises(ValueError, match=r"1\. ana-pell, 2\. bo-larke"):
        room.seat_named(plan, "nobody")


def test_window_assurance_separates_pinned_from_merely_watched():
    """An empty flag list must not read the same for both reasons."""
    assert drivers.window_assurance("claude") == "enforced"
    assert drivers.window_assurance("codex") == "enforced"
    # Antigravity's CLI has no context or compaction setting to pass, so a
    # seat there can only be watched after the fact.
    assert drivers.window_flags({"harness": "agy"}) == []
    assert drivers.window_assurance("agy") == "detected"


@pytest.fixture
def phased(tmp_path, plan):
    """A room whose middle round seats one writer, as a drafting phase does."""
    supplied = json.loads(plan.read_text())
    supplied["phases"] = [
        {"name": "discuss", "rounds": 1},
        {"name": "draft", "rounds": 1, "seats": ["ana-pell"]},
        {"name": "read", "rounds": 1},
    ]
    write(plan, supplied)
    room.prepare(plan, tmp_path / "phased", resolve_models=False)
    return tmp_path / "phased"


def test_a_resting_seat_is_not_a_seat_with_nothing_to_say(phased):
    """A drafting phase must not read as a room three-quarters finished."""
    post(phased, "ana-pell", "Opening position about lighthouses and keepers.")
    post(phased, "bo-larke", "Rival position about harbours and charts.")
    room.promote(phased, 1)
    # Round two seats only the drafter. The other seat is listening.
    post(phased, "ana-pell", "Wholly fresh vocabulary: gulls, fog, rope, brass.")
    room.promote(phased, 2)
    _, value = room.load(phased)
    signal = room.convergence(phased, value, 2)
    assert room.spoke(phased, value, 2) == ["ana-pell"]
    # Measured on the seat that spoke, this is a high-novelty round. Averaged
    # over the whole room it used to land near a quarter of that.
    assert signal["novelty"] > 50
    assert not signal["converged"]


def test_a_declared_schedule_is_not_cut_short_by_the_stop_rule(phased, monkeypatch):
    """A phased room's schedule says how long it runs; the board does not."""
    ran = []

    def fake_round(root, n, **kw):
        ran.append(n)
        # Publishing the round is what advances the loop's own counter.
        (root / "board" / "rounds" / f"r{n:03d}").mkdir(parents=True)
        return {"_board": {"convergence": {"converged": True, "reason": "novelty"}}}

    monkeypatch.setattr(room, "run_round", fake_round)
    # Every round reports convergence. A room without phases stops at the
    # first one; this room owes three rounds and a revision.
    _, why = room.run_until(phased)
    assert ran == [1, 2, 3]
    assert why == "budget"


def test_a_room_without_phases_still_stops_when_it_converges(prepared, monkeypatch):
    """The stop rule keeps the case it was built for: one open discussion."""
    ran = []

    def fake_round(root, n, **kw):
        ran.append(n)
        (root / "board" / "rounds" / f"r{n:03d}").mkdir(parents=True)
        return {"_board": {"convergence": {"converged": True, "reason": "novelty"}}}

    monkeypatch.setattr(room, "run_round", fake_round)
    _, why = room.run_until(prepared)
    assert ran == [1]
    assert why == "converged on novelty"


def test_rounds_hands_back_when_a_provider_wall_holds_a_round(prepared, monkeypatch):
    """A held round has no digest and no signal. The loop used to reach for
    the signal anyway and die on a KeyError, in the one situation the module
    is built to survive; it must instead stop and name the seats to continue."""
    ran = []

    def fake_round(root, n, **kw):
        ran.append(n)
        if n == 2:
            # No board directory: the round is open, waiting on a seat.
            return {"_board": {"held": ["ana-pell"]}}
        (root / "board" / "rounds" / f"r{n:03d}").mkdir(parents=True)
        return {
            "_board": {
                "posted": ["ana-pell", "bo-larke"],
                "convergence": {"converged": False, "reason": ""},
            }
        }

    monkeypatch.setattr(room, "run_round", fake_round)
    played, why = room.run_until(prepared)
    assert ran == [1, 2]
    assert why == "held on ana-pell"
    assert [n for n, _ in played] == [1, 2]


def test_the_rounds_command_reports_a_held_round(prepared, monkeypatch, capsys):
    """The command's own report must survive the outcome shape a hold returns,
    and tell the operator what to run next."""
    monkeypatch.setattr(
        room,
        "run_until",
        lambda root, **kw: (
            [(3, {"_board": {"held": ["bo-larke"]}})],
            "held on bo-larke",
        ),
    )
    assert room.main(["rounds", str(prepared)]) == 0
    out = capsys.readouterr().out
    assert "round 3: held, waiting on bo-larke" in out
    assert "continue" in out and "promote 3" in out
    assert "stopped: held on bo-larke" in out


def _research_plan(plan, harness, tools="research"):
    supplied = read(plan)
    supplied["harness"] = "codex"
    supplied["seats"][1].update({"tools": tools, "harness": harness})
    write(plan, supplied)
    return plan


def test_a_research_seat_is_launched_with_the_web_and_marked_on_the_board(
    plan, tmp_path
):
    """The digest's "[can search the web]" mark is a claim to the other seats.
    It used to be read off the plan and never reach the launcher, so a room
    could be told a colleague checked a source through a seat that had no
    way to."""
    root = tmp_path / "room"
    room.prepare(_research_plan(plan, "codex"), root, resolve_models=False)
    _, frozen = room.load(root)
    bo = next(s for s in frozen["seats"] if s["id"] == "bo-larke")
    assert bo["config"]["tools"] == "research"
    # An unresolved config carries no catalog ceiling; supply the one the
    # driver insists on so the command can be built at all.
    ceiling = {"max_context_window": 400_000}
    args, _ = room.drivers.command({**bo["config"], **ceiling}, tmp_path, "Check it.")
    assert 'web_search="live"' in args
    ana = next(s for s in frozen["seats"] if s["id"] == "ana-pell")
    assert ana["config"]["tools"] == "write"
    args, _ = room.drivers.command({**ana["config"], **ceiling}, tmp_path, "Write it.")
    assert 'web_search="disabled"' in args
    post(root, "ana-pell", "Ana opens.")
    post(root, "bo-larke", "Bo cites a source.")
    room.promote(root, 1)
    digest = (root / "board" / "rounds" / "r001" / "digest.md").read_text()
    assert "(bo-larke)  [can search the web]" in digest
    assert "(ana-pell)  [can search the web]" not in digest


def test_a_room_turn_records_its_seat_before_the_process_starts(
    prepared, monkeypatch, canaries_pass
):
    _, plan = room.load(prepared)
    seat = next(s for s in plan["seats"] if s["id"] == "ana-pell")
    monkeypatch.setattr(room.drivers, "command", lambda *a, **k: (["fixture"], b""))
    monkeypatch.setattr(
        room.drivers, "launch_context", lambda *a: ([], {}, prepared / "private")
    )
    # The jail is stubbed, so its bus is too: the fixture's seats run on agy,
    # and a unit test must never start a proxy on the operator's own bus.
    monkeypatch.setattr(room.blind, "session_bus", lambda runners: nullcontext())
    launched = []
    monkeypatch.setattr(
        room.usage_records,
        "seat",
        lambda workflow, config, **kw: launched.append(workflow),
    )

    def fail_spawn(*a, **kw):
        raise OSError("synthetic pre-spawn failure")

    monkeypatch.setattr(room.subprocess, "Popen", fail_spawn)
    with pytest.raises(OSError, match="pre-spawn"):
        room.launch_turn(prepared, plan, seat, 1, "Open.", None)
    assert launched == ["room"]
    # Proven before it launched, and the proof is in the seat's own record.
    assert [a["label"] for a in canaries_pass] == ["room seat ana-pell"]
    record = prepared / "records" / "ana-pell" / "r001" / "launch.json"
    assert json.loads(record.read_text())["canary"]["label"] == "room seat ana-pell"


def test_a_release_between_rounds_does_not_switch_a_seat_model(
    prepared, monkeypatch, canaries_pass
):
    """`opus` is resolved by the CLI; round two must keep round one's Opus."""
    from quirework.project import current_project

    # The launch stands in the project root, which the fixture never makes.
    current_project().root.mkdir(exist_ok=True)
    _, plan = room.load(prepared)
    seat = next(s for s in plan["seats"] if s["id"] == "ana-pell")
    seat = {**seat, "config": {**seat["config"], "harness": "claude", "model": "opus"}}
    sent = []
    # Round one is served the Opus of the day; then a newer one ships, and an
    # unpinned resume would land on it.
    newest = iter(["claude-opus-5-20260601", "claude-opus-6-20270101"])

    def command(config, work, prompt, *, resume=None):
        sent.append(config)
        return ["true"], b""

    def receipt(config, record, private, *, resumed=None):
        model = config.get("model_pinned") or next(newest)
        return {
            "session_id": "s-1",
            "model": model,
            **(
                {"model_pinned": config["model_pinned"]}
                if config.get("model_pinned")
                else {}
            ),
        }

    monkeypatch.setattr(room.drivers, "command", command)
    monkeypatch.setattr(room.drivers, "receipt", receipt)
    monkeypatch.setattr(
        room.drivers, "launch_context", lambda *a: ([], {}, prepared / "private")
    )
    monkeypatch.setattr(room.blind, "session_bus", lambda runners: nullcontext())
    # Nothing is jailed here: the argv is the stubbed one plus the board bind,
    # and the process only has to exit cleanly.
    monkeypatch.setattr(
        room.blind,
        "popen",
        lambda argv, bus, **kw: room.subprocess.Popen(["true"], **kw),
    )
    assert room.launch_turn(prepared, plan, seat, 1, "Open.", None) == "answered"
    assert "model_pinned" not in sent[0], "a first turn has nothing to keep"
    state = read(room.state_path(prepared, "ana-pell"))
    assert state["model_served"] == "claude-opus-5-20260601"
    assert room.launch_turn(prepared, plan, seat, 2, "Again.", "s-1") == "answered"
    assert sent[1]["model_pinned"] == "claude-opus-5-20260601"
    got = read(prepared / "records/ana-pell/r002/receipt.json")
    assert got["model"] == got["model_pinned"] == "claude-opus-5-20260601"
    # A seat whose plan already names the served version needs no pin.
    exact = {**seat, "config": {**seat["config"], "model": "claude-opus-5-20260601"}}
    room.launch_turn(prepared, plan, exact, 3, "Once more.", "s-1")
    assert "model_pinned" not in sent[2]


@pytest.fixture
def launches(prepared, monkeypatch, canaries_pass):
    """`launch_turn` with every harness call stubbed, running `true` unjailed.

    The private home is made as a real launch makes it, so what a launch
    leaves there and records about it can be read back.
    """
    from quirework.project import current_project

    # The launch stands in the project root, which the fixture never makes.
    current_project().root.mkdir(exist_ok=True)
    monkeypatch.setattr(room.drivers, "command", lambda *a, **kw: (["true"], b""))
    monkeypatch.setattr(room.drivers, "receipt", lambda *a, **kw: {"session_id": "s"})
    monkeypatch.setattr(
        room.drivers,
        "launch_context",
        lambda config, work, *a: (
            [],
            {},
            blind.make_state_root(work) / config["harness"],
        ),
    )
    monkeypatch.setattr(room.blind, "session_bus", lambda runners: nullcontext())
    monkeypatch.setattr(
        room.blind,
        "popen",
        lambda argv, bus, **kw: room.subprocess.Popen(["true"], **kw),
    )
    _, plan = room.load(prepared)
    return plan


def test_a_resumed_launch_marks_where_its_session_stood(prepared, launches):
    """The mark is what lets a refused turn be cut back out of the session."""
    seat = next(s for s in launches["seats"] if s["id"] == "bo-larke")
    session = native_session(prepared / "seats" / "bo-larke", "agy", "s-1")
    session.write_text("round one\n")
    room.launch_turn(prepared, launches, seat, 2, "Round two.", "s-1")
    launch = read(prepared / "records" / "bo-larke" / "r002" / "launch.json")
    assert launch["resumed"] == "s-1"
    assert launch["resume_point"] == quota.session_marks([session])
    room.launch_turn(prepared, launches, seat, 1, "Open.", None)
    first = read(prepared / "records" / "bo-larke" / "r001" / "launch.json")
    assert first["resume_point"] is None


def test_a_seats_home_is_held_from_its_launch_until_the_room_finishes(
    prepared, launches
):
    """Between rounds a seat looks idle, and the next round resumes the
    session its home holds, so only the room can say when it may go."""
    plan = launches
    seat = next(s for s in plan["seats"] if s["id"] == "ana-pell")
    room.launch_turn(prepared, plan, seat, 1, "Open.", None)
    home = blind.state_root(prepared / "seats" / "ana-pell")
    assert f"room {prepared.name} resumes seat ana-pell's session" in blind.held(home)
    words = iter("lantern harbour cellar orchard meadow quarry".split())
    for n in (1, 2, 3):
        assert blind.held(home), f"released with round {n} still to publish"
        post(prepared, "ana-pell", f"{next(words)} and more.")
        post(prepared, "bo-larke", f"{next(words)} besides.")
        room.promote(prepared, n)
    assert blind.held(home) is None


def test_an_unknown_persona_is_refused_with_the_ones_that_exist(plan, tmp_path):
    spec = read(plan)
    spec["seats"][0]["persona"] = "ana-pel"
    write(plan, spec)
    with pytest.raises(
        ValueError, match="no persona 'ana-pel'.*has ana-pell, bo-larke"
    ):
        room.prepare(plan, tmp_path / "room", resolve_models=False)


def test_a_research_seat_is_refused_where_it_cannot_be_enforced(plan, tmp_path):
    with pytest.raises(ValueError, match="agy cannot confine"):
        room.prepare(
            _research_plan(plan, "agy"), tmp_path / "room", resolve_models=False
        )
    with pytest.raises(ValueError, match="tools must be one of"):
        room.prepare(
            _research_plan(plan, "codex", "browse"),
            tmp_path / "room",
            resolve_models=False,
        )


def test_a_seat_is_told_paths_it_can_use_where_it_stands(staged):
    """The jail chdirs into the workspace, so the operator's home is noise."""
    post(staged, "ana-pell", "Ana opens.")
    post(staged, "bo-larke", "Bo opens.")
    room.promote(staged, 1)
    _, frozen = room.load(staged)
    seat = next(s for s in frozen["seats"] if s["id"] == "bo-larke")
    standing = (staged / "seats" / "bo-larke" / "START.md").read_text()
    moving = room.turn_prompt(staged, frozen, seat, 2)
    for text in (standing, moving):
        assert str(staged) not in text
        assert str(Path.home()) not in text
    assert "input/brief.md" in standing and "outbox/post.md" in standing
    assert "board/round-001/digest.md" in moving


def test_shown_abbreviates_only_the_home_prefix(tmp_path, monkeypatch):
    """Output a person reads; a record keeps the literal path."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert shown(tmp_path / "projects" / "book") == "~/projects/book"
    assert shown(tmp_path) == "~"
    # Not a prefix match on the string: a sibling directory keeps its name.
    assert shown(Path(f"{tmp_path}-other") / "x") == f"{tmp_path}-other/x"
    assert shown(Path("/etc/hosts")) == "/etc/hosts"


def test_usage_normalizes_three_native_shapes():
    """Each harness names the same quantities differently; one place knows."""
    codex = drivers.usage_totals(
        "codex",
        {
            "input_tokens": 1000,
            "cached_input_tokens": 900,
            "output_tokens": 50,
            "reasoning_output_tokens": 20,
        },
    )
    agy = drivers.usage_totals(
        "agy",
        {
            "input_tokens": 1000,
            "output_tokens": 50,
            "thinking_tokens": 20,
            "cache_read_tokens": 900,
        },
    )
    claude = drivers.usage_totals(
        "claude",
        {
            "claude-opus-5": {
                "inputTokens": 100,
                "outputTokens": 50,
                "thinkingTokens": 20,
                "cacheReadInputTokens": 900,
                "costUSD": 1.5,
            }
        },
    )
    for counted in (codex, agy, claude):
        assert (counted["output"], counted["reasoning"]) == (50, 20)
        assert counted["cache_read"] == 900
    # Only Claude quotes a price, and the others must say so rather than zero:
    # a room whose cost is partly unknown is not a cheaper room.
    assert claude["cost_usd"] == 1.5
    assert codex["cost_usd"] is None and agy["cost_usd"] is None
    # Codex counts cached tokens inside its input figure; Claude does not.
    assert codex["cache_inside_input"] and not claude["cache_inside_input"]
    assert drivers.usage_totals("claude", {}) is None


def test_a_rooms_usage_names_the_seats_it_cannot_price(prepared):
    """The failure this exists to prevent is a total that omits seats."""
    for seat, payload in (
        ("ana-pell", {"input_tokens": 10, "output_tokens": 5}),
        ("bo-larke", {"input_tokens": 10, "output_tokens": 7}),
    ):
        record = prepared / "records" / seat / "r001"
        record.mkdir(parents=True)
        write(record / "receipt.json", {"usage": payload})
    value = room.usage(prepared)
    assert [r["output"] for r in value["seats"]] == [5, 7]
    # Both seats are agy in this fixture, so nothing is priced at all.
    assert all(r["cost_usd"] is None for r in value["seats"])
    text = room._render_usage(value)
    assert "no price reported by agy" in text
    assert "the room cost more than this" in text


@pytest.fixture
def making(tmp_path, plan):
    """A room whose second round asks its drafter for a file, not just a post."""
    supplied = json.loads(plan.read_text())
    supplied["phases"] = [
        {"name": "discuss", "rounds": 1},
        {
            "name": "draft",
            "rounds": 1,
            "seats": ["ana-pell"],
            "words": 900,
            "deliverable": "chapter-one.md",
        },
        {"name": "read", "rounds": 1},
    ]
    write(plan, supplied)
    room.prepare(plan, tmp_path / "making", resolve_models=False)
    return tmp_path / "making"


def test_a_drafting_seat_is_asked_for_the_work_as_a_file(making):
    _, frozen = room.load(making)
    seat = next(s for s in frozen["seats"] if s["id"] == "ana-pell")
    text = room.turn_prompt(making, frozen, seat, 2)
    assert "outbox/chapter-one.md" in text
    # The length belongs to the work; the post is the note about it.
    assert "about 900 words" in text and "about 100 words" in text


@pytest.mark.parametrize("unstated", [None, False])
def test_a_phase_can_ask_for_the_work_without_a_length(tmp_path, plan, unstated):
    supplied = json.loads(plan.read_text())
    supplied["phases"] = [
        {"name": "discuss", "rounds": 1},
        {
            "name": "draft",
            "rounds": 1,
            "seats": ["ana-pell"],
            "words": unstated,
            "deliverable": "chapter-one.md",
        },
        {"name": "read", "rounds": 1, "words": unstated},
    ]
    write(plan, supplied)
    room.prepare(plan, tmp_path / "unsized", resolve_models=False)
    _, frozen = room.load(tmp_path / "unsized")
    seat = next(s for s in frozen["seats"] if s["id"] == "ana-pell")
    text = room.turn_prompt(tmp_path / "unsized", frozen, seat, 2)
    assert "outbox/chapter-one.md, as finished text and nothing else." in text
    assert "about 100 words" in text, "the note keeps its length"
    assert text.count(" words") == 1
    later = room.turn_prompt(tmp_path / "unsized", frozen, seat, 3)
    assert "this time" not in later


def test_a_phase_length_is_a_positive_number_or_none(tmp_path, plan):
    supplied = json.loads(plan.read_text())
    supplied["phases"] = [
        {"name": "discuss", "rounds": 2, "words": 0},
        {"name": "read", "rounds": 1},
    ]
    write(plan, supplied)
    with pytest.raises(ValueError, match="positive integer, or null"):
        room.prepare(plan, tmp_path / "zero", resolve_models=False)


def test_the_work_is_kept_as_a_file_and_still_reaches_the_room(making):
    post(making, "ana-pell", "Opening position.")
    post(making, "bo-larke", "A rival position.")
    room.promote(making, 1)
    post(making, "ana-pell", "Note to the room: I cut the second scene.")
    work = making / "seats" / "ana-pell" / "outbox" / "chapter-one.md"
    work.write_text("# Chapter One\n\nThe lighthouse keeper counted the ships.\n")
    room.promote(making, 2)
    kept = making / "board" / "made" / "ana-pell" / "r002" / "chapter-one.md"
    # Readable as a file, with no room talk to cut out of it.
    assert kept.read_text().startswith("# Chapter One")
    assert "Note to the room" not in kept.read_text()
    assert not work.exists()
    # And the room still sees it, under its author, beside the note.
    digest_text = (making / "board" / "rounds" / "r002" / "digest.md").read_text()
    assert "Note to the room: I cut the second scene." in digest_text
    assert "The lighthouse keeper counted the ships." in digest_text


def test_a_phase_that_asks_for_no_file_is_unchanged(making):
    post(making, "ana-pell", "Just a post, no file this round.")
    post(making, "bo-larke", "Likewise.")
    room.promote(making, 1)
    assert not (making / "board" / "made").exists()


def test_a_deliverable_name_may_not_leave_the_outbox(plan, tmp_path):
    supplied = json.loads(plan.read_text())
    supplied["phases"] = [
        {"name": "draft", "rounds": 3, "deliverable": "../../escaped.md"}
    ]
    write(plan, supplied)
    with pytest.raises(ValueError, match="plain file name"):
        room.prepare(plan, tmp_path / "escaping", resolve_models=False)


def test_a_solo_phase_does_not_claim_the_board_moved(making):
    """The drafter alone on a board must not read silence as an answer."""
    post(making, "ana-pell", "Opening position.")
    post(making, "bo-larke", "A rival position, and a question for you.")
    room.promote(making, 1)
    # Round two seats the drafter alone, so round three's board is her own.
    post(making, "ana-pell", "My note about the draft.")
    room.promote(making, 2)
    _, frozen = room.load(making)
    seat = next(s for s in frozen["seats"] if s["id"] == "ana-pell")
    text = room.turn_prompt(making, frozen, seat, 3)
    assert "The board has moved" not in text
    assert "Nobody else has posted since round 1" in text
    # It points at the board where the room last actually spoke, which is not
    # the last board to change.
    assert "board/round-001/digest.md" in text
    assert "still outstanding, not declined" in text


def test_a_board_with_colleagues_on_it_still_reads_as_moved(making):
    post(making, "ana-pell", "Opening position.")
    post(making, "bo-larke", "A rival position.")
    room.promote(making, 1)
    _, frozen = room.load(making)
    seat = next(s for s in frozen["seats"] if s["id"] == "ana-pell")
    text = room.turn_prompt(making, frozen, seat, 2)
    assert "The board has moved: board/round-001/digest.md" in text
    assert "Only your own last post" not in text


def test_a_correction_made_after_its_phase_is_still_collected(making):
    """A file the plan asks for anywhere is taken whenever the seat writes it."""
    post(making, "ana-pell", "Opening position.")
    post(making, "bo-larke", "A rival position.")
    room.promote(making, 1)
    post(making, "ana-pell", "Note about the draft.")
    work = making / "seats" / "ana-pell" / "outbox" / "chapter-one.md"
    work.write_text("# Chapter One\n\nFirst version.\n")
    room.promote(making, 2)
    # Round three asks for no file. The seat corrects one anyway, which is
    # exactly when a drafter notices a section it can no longer reach.
    post(making, "ana-pell", "I reissued chapter one with the date fixed.")
    post(making, "bo-larke", "Noted.")
    work.write_text("# Chapter One\n\nSecond version, date fixed.\n")
    room.promote(making, 3)
    kept = making / "board" / "made" / "ana-pell" / "r003" / "chapter-one.md"
    assert "Second version" in kept.read_text()
    assert not work.exists()
    trail = [
        json.loads(line) for line in (making / "room.log").read_text().splitlines()
    ]
    events = {(r.get("round"), r.get("event")) for r in trail if r.get("file")}
    assert (2, "made") in events
    # Named apart from the phase's own deliverable, because the round did not
    # ask for this one.
    assert (3, "revised") in events


def test_an_unasked_file_is_not_forced_onto_the_board(making):
    """Collecting a correction must not put it into a conversation uninvited."""
    post(making, "ana-pell", "Opening position.")
    post(making, "bo-larke", "A rival position.")
    room.promote(making, 1)
    post(making, "ana-pell", "Note.")
    (making / "seats" / "ana-pell" / "outbox" / "chapter-one.md").write_text("Draft.\n")
    room.promote(making, 2)
    post(making, "ana-pell", "Reissued.")
    post(making, "bo-larke", "Fine.")
    (making / "seats" / "ana-pell" / "outbox" / "chapter-one.md").write_text("Fixed.\n")
    room.promote(making, 3)
    digest_text = (making / "board" / "rounds" / "r003" / "digest.md").read_text()
    assert "Reissued." in digest_text
    assert "Fixed." not in digest_text


def _stream(record, rows):
    record.mkdir(parents=True, exist_ok=True)
    (record / "events.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


CAPACITY = [
    {"type": "thread.started", "thread_id": "t-1"},
    {"type": "turn.started"},
    {"type": "turn.failed", "error": {"message": "Selected model is at capacity."}},
]


def test_a_quota_stop_holds_the_round_instead_of_publishing_an_absence(prepared):
    """A published board is never rewritten, so a digest filing a stopped seat
    as silent would put a false fact permanently out of reach."""
    _, plan = room.load(prepared)
    seat = next(s for s in plan["seats"] if s["id"] == "ana-pell")
    record = prepared / "records" / "ana-pell" / "r001"
    _stream(record, CAPACITY)
    state = read(room.state_path(prepared, "ana-pell"))
    state["rounds"] = {"1": {"status": "quota", "quota_stop": "refused"}}
    write(room.state_path(prepared, "ana-pell"), state)
    assert not (prepared / "board" / "rounds" / "r001").exists()


def test_continue_refuses_a_failure_that_is_not_a_quota_stop(prepared):
    """A crash and a wall look identical at the command line, and re-running
    a crash can duplicate work that actually happened."""
    record = prepared / "records" / "ana-pell" / "r001"
    _stream(record, [{"type": "turn.failed", "error": {"message": "segfault"}}])
    state = read(room.state_path(prepared, "ana-pell"))
    state["rounds"] = {"1": {"status": "failed"}}
    write(room.state_path(prepared, "ana-pell"), state)
    with pytest.raises(RuntimeError, match="does not carry a provider quota stop"):
        room.continue_seat(prepared, 1, "ana-pell")


def test_continue_refuses_a_round_the_seat_already_answered(prepared):
    state = read(room.state_path(prepared, "ana-pell"))
    state["rounds"] = {"1": {"status": "answered"}}
    write(room.state_path(prepared, "ana-pell"), state)
    with pytest.raises(ValueError, match="already answered"):
        room.continue_seat(prepared, 1, "ana-pell")


def test_continue_refuses_once_the_round_is_published(prepared):
    """After promotion the board is fixed; a late post would rewrite it."""
    post(prepared, "ana-pell", "A position.")
    room.promote(prepared, 1)
    state = read(room.state_path(prepared, "ana-pell"))
    state["rounds"] = {"1": {"status": "quota"}}
    write(room.state_path(prepared, "ana-pell"), state)
    with pytest.raises(RuntimeError, match="already published"):
        room.continue_seat(prepared, 1, "ana-pell")


def test_a_busy_model_is_a_refusal_and_not_an_allowance_to_wait_out(prepared):
    """The stop that cost a real room a seat: no limit code, no limit text,
    so it classified as an ordinary failure and no recovery would take it."""
    record = prepared / "records" / "ana-pell" / "r001"
    _stream(record, CAPACITY)
    stop = quota.classify("codex", record)
    assert stop["phase"] == "refused"
    assert stop["scope"] == "capacity"
    # Nothing to wait for, and nothing banked in the session to continue.
    assert stop["resets_at"] is None
    assert quota.resumable_session(stop) is None


def test_an_interrupted_turn_is_continued_in_its_own_session(prepared):
    """The wall arrived partway, so the session holds the reasoning and a
    fresh run would buy all of it again."""
    record = prepared / "records" / "ana-pell" / "r001"
    _stream(
        record,
        [
            {"type": "thread.started", "thread_id": "t-9"},
            {"type": "item.completed", "item": {"type": "agent_message"}},
            {
                "type": "turn.failed",
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": "usage limit reached",
                },
            },
        ],
    )
    stop = quota.classify("codex", record)
    assert stop["phase"] == "interrupted"
    assert quota.resumable_session(stop) == "t-9"


AGY_CUT = [
    {"event": "init", "conversation_id": "cv-2"},
    {
        "event": "step_update",
        "conversation_id": "cv-2",
        "step_update": {"step_type": "tool"},
    },
    {
        "event": "result",
        "conversation_id": "cv-2",
        "result": {
            "status": "ERROR",
            "error": "429 rate limit reached",
            "usage": {"output_tokens": 40},
        },
    },
]


def test_a_round_is_refused_before_it_opens_once_a_seats_session_is_gone(prepared):
    """Every seat resumes its session each round. A home pruned between
    rounds would fail mid-round, after the board went out to the others."""
    post(prepared, "ana-pell", "A position.")
    post(prepared, "bo-larke", "Another position.")
    room.promote(prepared, 1)
    for seat in ("ana-pell", "bo-larke"):
        state = read(room.state_path(prepared, seat))
        state["session_id"] = f"s-{seat}"
        write(room.state_path(prepared, seat), state)
    native_session(prepared / "seats" / "ana-pell", "agy", "s-ana-pell")
    trail = (prepared / "room.log").read_text()
    with pytest.raises(blind.SessionGone, match="s-bo-larke.*private home"):
        room.run_round(prepared, 2)
    assert (prepared / "room.log").read_text() == trail
    assert not (prepared / "records" / "ana-pell" / "r002").exists()


def test_continue_files_nothing_away_once_the_session_is_gone(prepared):
    """The stopped attempt stays where it was, so the round reads the same."""
    record = prepared / "records" / "ana-pell" / "r001"
    _stream(record, AGY_CUT)
    state = read(room.state_path(prepared, "ana-pell"))
    state["rounds"] = {"1": {"status": "quota", "quota_stop": "interrupted"}}
    write(room.state_path(prepared, "ana-pell"), state)
    with pytest.raises(blind.SessionGone, match="cv-2"):
        room.continue_seat(prepared, 1, "ana-pell")
    assert (record / "events.jsonl").exists()
    assert not (record / "attempts").exists()
    assert read(room.state_path(prepared, "ana-pell"))["rounds"] == state["rounds"]


AGY_REFUSED = [
    {"event": "init", "conversation_id": "s-1"},
    {
        "event": "result",
        "conversation_id": "s-1",
        "result": {"status": "ERROR", "error": "429 rate limit reached"},
    },
]


def refused_in_round_two(prepared, *, marked=True):
    """Bo Larke's round-two turn, a resume of round one's session, refused.

    The harness appended the refused prompt to the session before the
    provider declined, as a real one does.
    """
    session = native_session(prepared / "seats" / "bo-larke", "agy", "s-1")
    session.write_text("round one\n")
    marks = quota.session_marks([session])
    with session.open("a") as out:
        out.write("round two, refused\n")
    record = prepared / "records" / "bo-larke" / "r002"
    _stream(record, AGY_REFUSED)
    (record / "prompt.md").write_text("Round two: read the board and reply.")
    launch = {"resumed": "s-1", **({"resume_point": marks} if marked else {})}
    write(record / "launch.json", launch)
    state = read(room.state_path(prepared, "bo-larke"))
    state["session_id"] = "s-1"
    state["rounds"] = {
        "1": {"status": "answered"},
        "2": {"status": "quota", "quota_stop": "refused"},
    }
    write(room.state_path(prepared, "bo-larke"), state)
    return session, record


@pytest.fixture
def relaunched(monkeypatch):
    """What `continue` launches, instead of launching it."""
    sent = []

    def launch(root, plan, seat, n, prompt, resume, **kwargs):
        sent.append({"prompt": prompt, "resume": resume})
        return "answered"

    monkeypatch.setattr(room, "launch_turn", launch)
    return sent


def test_a_refused_later_round_goes_back_into_the_seats_own_session(
    prepared, relaunched
):
    """A fresh session lost every earlier round, and the round's prompt,
    written for a seat that remembers them, reached one with no assignment."""
    session, _ = refused_in_round_two(prepared)
    room.continue_seat(prepared, 2, "bo-larke")
    assert relaunched == [
        {"prompt": "Round two: read the board and reply.", "resume": "s-1"}
    ]
    assert session.read_text() == "round one\n", "the refused rows are cut away"
    attempt = read(room.state_path(prepared, "bo-larke"))["attempts"][-1]
    assert attempt["resumed"] == "s-1" and attempt["rewound"]


def test_a_turn_launched_before_marks_is_resent_into_its_session_as_it_is(
    prepared, relaunched
):
    session, _ = refused_in_round_two(prepared, marked=False)
    room.continue_seat(prepared, 2, "bo-larke")
    assert relaunched[0]["resume"] == "s-1"
    assert session.read_text() == "round one\nround two, refused\n"
    assert not read(room.state_path(prepared, "bo-larke"))["attempts"][-1]["rewound"]


def test_a_refused_first_turn_still_opens_a_fresh_session(prepared, relaunched):
    """Nothing of the seat's exists yet, so there is nothing to go back into."""
    record = prepared / "records" / "ana-pell" / "r001"
    _stream(record, AGY_REFUSED)
    (record / "prompt.md").write_text("Round one.")
    write(record / "launch.json", {"resumed": None, "resume_point": None})
    state = read(room.state_path(prepared, "ana-pell"))
    state["rounds"] = {"1": {"status": "quota", "quota_stop": "refused"}}
    write(room.state_path(prepared, "ana-pell"), state)
    room.continue_seat(prepared, 1, "ana-pell")
    assert relaunched == [{"prompt": "Round one.", "resume": None}]


def test_a_session_changed_since_its_mark_is_not_rewound(prepared, relaunched):
    """Cutting a file someone rewrote would cut the wrong rows."""
    session, record = refused_in_round_two(prepared)
    session.write_text("ROUND ONE, rewritten\nround two, refused\n")
    with pytest.raises(RuntimeError, match="not a clean rewind"):
        room.continue_seat(prepared, 2, "bo-larke")
    assert relaunched == []
    assert (record / "events.jsonl").exists() and not (record / "attempts").exists()


def test_the_cli_can_still_read_a_room_after_every_branch(prepared, capsys):
    """A dispatch branch that binds the name `status` makes it local to
    `main` for the whole body, so every other command's call to the
    module-level function raises UnboundLocalError. Shipped once."""
    room.main(["status", str(prepared)])
    assert "rounds: 0 of 3 published" in capsys.readouterr().out


def test_status_names_the_command_to_type_next_at_every_stage(prepared):
    where = shown(prepared)
    text = room._render(room.status(prepared))
    assert "not started, 3 rounds to speak in" in text
    assert text.endswith(f"next: quirework sop room rounds {where}")
    # Every post is mostly new words, so the room never converges.
    fresh = iter(
        "lantern harbour cellar orchard meadow quarry beacon pantry thistle "
        "granary spindle ferryman lattice bramble cistern tinder gable "
        "wicket furrow sextant ledger hollow cobble tiller".split()
    )
    for n in (1, 2, 3):
        for seat in ("ana-pell", "bo-larke"):
            post(prepared, seat, " ".join(next(fresh) for _ in range(4)) + ".")
        room.promote(prepared, n)
    text = room._render(room.status(prepared))
    assert "rounds: 3 of 3 published (1-3)" in text
    assert "novelty: r001 100%, r002" in text
    assert text.endswith(f"next: quirework sop room board {where} 3")
    room.extend(prepared, 5)
    text = room._render(room.status(prepared))
    assert "rounds: 3 of 5 published (1-3); budget raised from the 3 declared" in text
    assert text.endswith(f"next: quirework sop room rounds {where}")
    # Two rounds of restating stop the room, and an extended budget does not
    # change that; the operator is told how to run past it.
    for n in (4, 5):
        post(prepared, "ana-pell", "lantern harbour cellar orchard.")
        post(prepared, "bo-larke", "meadow quarry beacon pantry.")
        room.promote(prepared, n)
    room.extend(prepared, 6)
    value = room.status(prepared)
    assert value["converged"]
    assert "(converged at round 5 on novelty)" in room._render(value)
    assert value["next"] == (
        f"quirework sop room board {where} 5; the room converged, and "
        f"quirework sop room rounds {where} runs past it"
    )


def test_a_held_round_names_its_seats_and_their_reset(prepared):
    record = prepared / "records" / "ana-pell" / "r001"
    record.mkdir(parents=True)
    write(
        record / "receipt.json",
        {"quota_stop": "refused", "quota_resets_at": "14:20Z", "red_flags": []},
    )
    room.event(
        prepared, round=1, event="round-held", waiting_on=["ana-pell", "bo-larke"]
    )
    text = room._render(room.status(prepared))
    assert (
        "HELD: round 1 waiting on ana-pell, bo-larke (provider quota; resets 14:20Z)"
        in text
    )
    where = shown(prepared)
    assert text.endswith(
        f"next: quirework sop room continue {where} 1 ana-pell (and 1 more) once "
        f"the window resets, then quirework sop room promote {where} 1"
    )


def test_red_flags_are_counted_and_shown_under_their_seat(prepared):
    record = prepared / "records" / "bo-larke" / "r001"
    record.mkdir(parents=True)
    write(
        record / "receipt.json", {"red_flags": ["Session bus proxied into the jail."]}
    )
    text = room._render(room.status(prepared))
    assert "red flags: 1 (marked ! below)" in text
    assert "     ! r001: Session bus proxied into the jail." in text


def test_round_numbers_collapse_instead_of_printing_a_python_list():
    assert room._spans([1]) == "1"
    assert room._spans([1, 2, 3, 5]) == "1-3, 5"
    assert room._spans([4, 2, 1]) == "1-2, 4"


def test_a_seat_reports_every_round_not_only_its_last_turn():
    seat = {
        "acting_rounds": [1, 2, 3],
        "rounds": {
            "1": {"status": "answered"},
            "2": {"status": "answered"},
            "3": {"status": "failed"},
        },
    }
    assert room._seat_summary(seat) == "answered 2 of 3 rounds, r003 failed"
    assert room._seat_summary({"acting_rounds": [2], "rounds": {}}) == (
        "not started, 1 round to speak in"
    )


def test_the_operator_sees_models_and_the_room_never_does(prepared):
    """Naming the model on the stored digest would be a different room: a
    seat would weigh a colleague by its make instead of by what it said."""
    post(prepared, "ana-pell", "A position.")
    room.promote(prepared, 1)
    stored = (prepared / "board" / "rounds" / "r001" / "digest.md").read_text()
    _, plan = room.load(prepared)
    model = plan["seats"][0]["config"]["model"]
    assert model not in stored
    assert model in room.attributed(stored, plan)
    assert "(ana-pell," in room.attributed(stored, plan)


def test_a_labelled_private_file_is_named_without_its_path(prepared_labelled):
    """Whatever is in a private holding's filename reaches every seat, so a
    name carrying a model id puts that in the room."""
    _, plan = room.load(prepared_labelled)
    roster = room.holdings(plan)
    assert "six reader reports on an earlier draft" in roster
    assert "gpt-6-astra" not in roster


def test_an_unlabelled_private_file_still_shows_its_path(prepared_labelled):
    """The path stays the default, so naming is opt-in rather than required."""
    _, plan = room.load(prepared_labelled)
    assert "notes/plain.md" in room.holdings(plan)


def test_status_names_a_queued_chair_note_before_it_is_spent(prepared):
    (prepared / "chair" / "r002.md").write_text("A correction.\n")
    value = room.status(prepared)
    assert value["chair"]["queued"] == ["r002"]
    assert value["chair"]["delivered"] == []


def test_a_round_runs_one_seat_at_a_time_unless_asked(prepared):
    """Seats sharing one account drain a rate window faster together, and the
    wall then lands mid-turn on all of them at once."""
    import inspect

    assert inspect.signature(room.run_round).parameters["jobs"].default == 1
    assert inspect.signature(room.run_until).parameters["jobs"].default == 1


# -- Following a room as it runs ------------------------------------------------


def stream(root, seat, n, rows):
    path = root / "records" / seat / f"r{n:03d}" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as out:
        out.writelines(json.dumps(row) + "\n" for row in rows)


def wrote(root, seat, name):
    info = {
        "name": "write_to_file",
        "parameters": {"TargetFile": str(root / "seats" / seat / name)},
    }
    step = {
        "step_type": "tool",
        "state": "DONE",
        "tool_name": "write_to_file",
        "tool_info": info,
    }
    return {"event": "step_update", "step_update": step}


def take_turn(root, seat, n, text):
    """What `launch_turn` records about a turn, without launching anything."""
    room.event(root, round=n, seat=seat, event="launching", resumed=None)
    stream(root, seat, n, [wrote(root, seat, "outbox/post.md")])
    post(root, seat, text)
    room.event(root, round=n, seat=seat, event="exited", code=0, seconds=123)
    room.event(
        root, round=n, seat=seat, event="collected", status="answered", error=None
    )


def told(following):
    return "\n".join(following.poll())


def test_a_room_followed_live_tells_each_turn_its_files_and_its_post(prepared):
    following = room.Feed(prepared, history="none", width=100)
    assert told(following) == ""
    room.event(prepared, round=1, event="round-opened", jobs=1)
    room.event(prepared, round=1, seat="ana-pell", event="launching", resumed=None)
    text = told(following)
    assert "round 1 opened · speaking: Ana Pell, Bo Larke · one at a time" in text
    assert "Ana Pell [agy/test-model] starts round 1, in a new session" in text
    stream(prepared, "ana-pell", 1, [wrote(prepared, "ana-pell", "outbox/post.md")])
    post(prepared, "ana-pell", "Call the keeper's cat Tern.")
    assert "Ana Pell  wrote    outbox/post.md (27 B)" in told(following)
    room.event(prepared, round=1, seat="ana-pell", event="exited", code=0, seconds=123)
    room.event(
        prepared,
        round=1,
        seat="ana-pell",
        event="collected",
        status="answered",
        error=None,
    )
    text = told(following)
    assert "Ana Pell answered in 2m03s" in text
    assert "Call the keeper's cat Tern." in text
    room.promote(prepared, 1)
    text = told(following)
    assert "Bo Larke left no post this round" in text
    assert "round 1 closed: 1 post, 1 absent" in text


def test_a_room_read_back_shows_the_posts_from_the_board(prepared):
    """Promotion empties the outbox; the board keeps the same words."""
    room.event(prepared, round=1, event="round-opened", jobs=1)
    take_turn(prepared, "ana-pell", 1, "Call the keeper's cat Tern.")
    room.promote(prepared, 1)
    text = told(room.Feed(prepared, width=100))
    assert "wrote    outbox/post.md" in text
    assert "Call the keeper's cat Tern." in text


def test_a_room_is_told_from_its_latest_round_unless_asked_for_all(prepared):
    for n, words in ((1, "First, Tern."), (2, "Second, still Tern.")):
        room.event(prepared, round=n, event="round-opened", jobs=1)
        take_turn(prepared, "ana-pell", n, words)
        room.promote(prepared, n)
    latest = told(room.Feed(prepared, width=100))
    assert "Second, still Tern." in latest and "First, Tern." not in latest
    assert "First, Tern." in told(room.Feed(prepared, history="all", width=100))


def test_a_rooms_own_log_keeps_the_files_and_leaves_the_words(prepared):
    following = room.Feed(prepared, history="none", show=room.feed.BRIEF, width=100)
    room.event(prepared, round=1, event="round-opened", jobs=1)
    take_turn(prepared, "ana-pell", 1, "Call the keeper's cat Tern.")
    text = told(following)
    assert "wrote    outbox/post.md" in text
    assert "Ana Pell answered in 2m03s" in text
    assert "Tern" not in text


def test_a_step_the_feed_does_not_know_is_shown_as_recorded(prepared):
    following = room.Feed(prepared, history="none", width=100)
    room.event(prepared, round=1, event="something-new", detail="kept")
    assert 'something-new {"round": 1, "detail": "kept"}' in told(following)


def test_a_round_tells_its_progress_on_stderr_as_it_runs(prepared, monkeypatch, capsys):
    """The round's own log was empty until the round ended."""

    def run_round(root, n, **kw):
        room.event(root, round=n, event="round-opened", jobs=1)
        take_turn(root, "ana-pell", n, "Call the keeper's cat Tern.")
        return {"ana-pell": "answered", "_board": {"posted": ["ana-pell"]}}

    monkeypatch.setattr(room, "run_round", run_round)
    assert room.main(["round", str(prepared), "1"]) == 0
    captured = capsys.readouterr()
    assert "Ana Pell [agy/test-model] starts round 1" in captured.err
    assert "wrote    outbox/post.md" in captured.err
    assert "Tern" not in captured.err
    assert "ana-pell: answered" in captured.out
