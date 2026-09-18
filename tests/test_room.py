"""The board's mechanics: staging, promotion, rotation and integrity."""

import fnmatch
import json

import pytest

from quirework.runs.storage import lock, write
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


def post(root, seat, text):
    (root / "seats" / seat / "outbox" / "post.md").write_text(text)


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


def test_a_later_round_needs_a_session_to_resume(prepared):
    _, plan = room.load(prepared)
    seat = next(s for s in plan["seats"] if s["id"] == "ana-pell")
    with pytest.raises(RuntimeError, match="no session to resume"):
        room.run_seat(prepared, plan, seat, 2)


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
    assert str(tmp_path / "named") in str(caught.value)


def test_an_unknown_name_says_where_it_looked(project_rooms):
    with pytest.raises(ValueError) as caught:
        room.room_root("absent")
    assert str(project_rooms) in str(caught.value)


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
