"""The roundtable's mechanics: names, order, turns, limits and the supervisor."""

import json
import subprocess
import sys
from contextlib import nullcontext

import pytest

from quirework.providers import models as drivers
from quirework.runs.storage import read, write
from quirework.sops import room, roundtable


@pytest.fixture
def plan(tmp_path):
    """Three fictional seats on three harnesses, and a short fictional brief."""
    source = tmp_path / "plan"
    source.mkdir()
    (source / "brief.md").write_text("Name the lighthouse keeper's cat.\n")
    write(
        source / "table.json",
        {
            "title": "Test table",
            "goal": "Agree on a name for a fictional cat, or say why not.",
            "rounds": 3,
            "effort": "medium",
            "seed": 11,
            "brief": {"path": "brief.md", "local_source": "brief.md"},
            "seats": [
                {"model": "haiku", "harness": "claude"},
                # Codex windows come from its live catalog, which these
                # tests never read, so the plan states this one.
                {"model": "luna", "harness": "codex", "window": 828400},
                {"model": "gemini-flash", "harness": "agy"},
            ],
        },
    )
    return source / "table.json"


@pytest.fixture
def table(plan, tmp_path):
    roundtable.prepare(plan, tmp_path / "table", resolve_models=False)
    return tmp_path / "table"


class Seats:
    """Stands in for the launcher: each turn runs a scripted action instead."""

    def __init__(self, monkeypatch):
        self.script = {}
        self.prompts = []
        self.statuses = {}
        self.context = {}
        monkeypatch.setattr(roundtable.room, "launch_turn", self.launch)

    def launch(self, root, plan, seat, n, prompt, resume, **kw):
        name = seat["id"]
        record = root / "records" / name / f"r{n:03d}"
        record.mkdir(parents=True, exist_ok=True)
        (record / "prompt.md").write_text(prompt)
        self.prompts.append({"seat": name, "round": n, "text": prompt, **kw})
        action = self.script.get((name, n))
        if action:
            action(root / "seats" / name)
        status = self.statuses.get((name, n), "answered")
        value = read(room.state_path(root, name))
        value["session_id"] = value.get("session_id") or f"s-{name}"
        value["rounds"][str(n)] = {"status": status, "error": None}
        write(room.state_path(root, name), value)
        spent = self.context.get((name, n))
        if spent is not None:
            # What each harness would have reported for the turn's last request.
            if seat["config"]["harness"] == "codex":
                rollout = record / "rollout.jsonl"
                count = {"input_tokens": spent, "cached_input_tokens": 0}
                row = {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"last_token_usage": count},
                    },
                }
                rollout.write_text(json.dumps(row) + "\n")
                receipt = {"native_transcripts": [str(rollout)], "usage": {}}
            else:
                usage = {"input_tokens": spent}
                row = {"type": "assistant", "message": {"usage": usage}}
                (record / "events.jsonl").write_text(json.dumps(row) + "\n")
                receipt = {"native_transcripts": [], "usage": {}}
            write(record / "receipt.json", receipt)
        return status

    def prompt(self, seat, n):
        return next(
            p["text"] for p in self.prompts if p["seat"] == seat and p["round"] == n
        )


def tool(work, *args, stdin=None):
    return subprocess.run(
        [sys.executable, str(work / "bin" / "room"), *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=work,
        check=False,
    )


def posts(text):
    return lambda work: tool(work, "post", "-", stdin=text)


def order(table, n):
    return read(table / "schedule.json")["rounds"][str(n)]


def test_seats_are_named_after_their_models_and_meet_as_themselves(table):
    _, plan = roundtable.load(table)
    assert [s["name"] for s in plan["seats"]] == ["Haiku", "Luna", "Gemini-Flash"]
    assert [s["maker"] for s in plan["seats"]] == ["Anthropic", "OpenAI", "Google"]
    start = (table / "seats" / "luna" / "START.md").read_text()
    assert "You are Luna, an AI model made by OpenAI." in start
    assert "- Haiku: Anthropic model haiku, running in Claude Code\n" in start
    assert "- Luna: OpenAI model luna, running in Codex CLI (you)\n" in start
    assert "running in Antigravity CLI\n" in start
    assert start.count("(you)") == 1
    assert "direct messages included" in start
    assert "No model here outranks another." in start
    flat = " ".join(start.split())
    assert "Check each other's work rather than taking it on trust" in flat
    assert "what you checked" in flat
    assert "skeptic" not in flat.lower(), "a norm for every seat, not a role"


def test_an_exact_model_id_is_still_named_as_people_name_it():
    assert roundtable.seat_name("claude-haiku-4-5") == "Haiku"
    assert roundtable.seat_name("gpt-6-luna") == "Luna"
    assert roundtable.seat_name("gemini-3.8-flash-medium") == "Gemini-Flash"


def test_a_model_is_seated_once(plan, tmp_path):
    spec = read(plan)
    spec["seats"].append({"model": "haiku", "harness": "claude", "name": "Other"})
    write(plan, spec)
    with pytest.raises(ValueError, match="each model once.*haiku"):
        roundtable.prepare(plan, tmp_path / "twice", resolve_models=False)


def test_every_seat_gets_a_shell_and_the_web(table):
    _, plan = roundtable.load(table)
    assert {s["config"]["tools"] for s in plan["seats"]} == {"workbench"}


@pytest.mark.parametrize("count", [3, 4, 5])
def test_orders_balance_positions_and_never_give_two_turns_in_a_row(count):
    ids = [f"s{i}" for i in range(count)]
    rounds = roundtable.extend_schedule({}, ids, 7, 2 * count)["rounds"]
    orders = [rounds[str(n)] for n in range(1, 2 * count + 1)]
    for block in (orders[:count], orders[count:]):
        for p in range(count):
            assert sorted(o[p] for o in block) == sorted(ids)
    for before, after in zip(orders, orders[1:]):
        assert before[-1] != after[0]
    again = roundtable.extend_schedule({}, ids, 7, 2 * count)["rounds"]
    assert again == rounds


def test_neighbours_vary_rather_than_rotate():
    """Rotation keeps the same seat answering the same seat forever."""
    ids = ["a", "b", "c", "d"]
    rounds = roundtable.extend_schedule({}, ids, 3, 4)["rounds"]
    pairs = {
        pair for n in range(1, 5) for pair in zip(rounds[str(n)], rounds[str(n)][1:])
    }
    assert len(pairs) > 3


def test_the_tool_checks_the_limits_inside_the_turn(table):
    work = table / "seats" / "luna"
    long = " ".join(["word"] * 201)
    got = tool(work, "post", "-", stdin=long)
    assert got.returncode == 2 and "201 words" in got.stderr
    assert not (work / "outbox" / "post.md").exists()
    got = tool(work, "post", "-", stdin="A short post.")
    assert got.returncode == 0
    assert (work / "outbox" / "post.md").read_text() == "A short post.\n"
    assert tool(work, "message", "Nobody", "hi").returncode == 2
    assert "That is you" in tool(work, "message", "luna", "hi").stderr
    assert tool(work, "message", "x" * 5, "y" * 301).returncode == 2
    for who in ("Haiku", "supervisor", "@gemini-flash"):
        assert tool(work, "message", who, "Hello.").returncode == 0
    got = tool(work, "message", "haiku", "One more.")
    assert got.returncode == 2 and "already queued" in got.stderr
    assert "Message to the supervisor" in tool(work, "outbox").stdout


def test_a_missing_file_is_not_posted_as_its_own_name(table):
    work = table / "seats" / "haiku"
    for name in ("draft.md", "notes", "/no/such/place"):
        got = tool(work, "post", name)
        assert got.returncode == 2 and f"named {name}" in got.stderr
    assert not (work / "outbox" / "post.md").exists()


def test_a_post_read_from_dev_stdin_is_the_text_not_the_path(table):
    work = table / "seats" / "haiku"
    got = tool(work, "post", "/dev/stdin", stdin="Barnacle, for the tides.")
    assert got.returncode == 0
    assert (work / "outbox" / "post.md").read_text() == "Barnacle, for the tides.\n"


def test_a_turn_is_handed_what_happened_since_it_last_spoke(table, monkeypatch):
    seats = Seats(monkeypatch)
    first, second, third = order(table, 1)

    def speak(work):
        tool(work, "post", "-", stdin="I propose Barnacle.")
        tool(work, "message", second, "Do you like Barnacle?")

    seats.script[(first, 1)] = speak
    roundtable.run_turn(table)
    roundtable.run_turn(table)
    _, plan = roundtable.load(table)
    labels = roundtable.names(plan)
    text = seats.prompt(second, 1)
    assert "I propose Barnacle." in text
    assert f"From {labels[first]} (round 1, turn 1):\n\nDo you like Barnacle?" in text
    assert f"2nd of 3 this round. Before you: {labels[first]}." in text
    assert f"After you: {labels[third]}." in text
    assert "Read START.md first" in text
    # Its own words are not news to the seat that wrote them.
    roundtable.run(table, to=2)
    again = seats.prompt(first, 2)
    assert "I propose Barnacle." not in again
    assert "Read START.md" not in again


def test_the_controller_refuses_what_the_tool_would_have(table, monkeypatch):
    seats = Seats(monkeypatch)
    first = order(table, 1)[0]

    def bypass(work):
        (work / "outbox").mkdir(exist_ok=True)
        (work / "outbox" / "post.md").write_text(" ".join(["word"] * 250))
        folder = work / "outbox" / "messages"
        folder.mkdir()
        for i in range(4):
            (folder / f"{i}.json").write_text(
                json.dumps({"to": "supervisor", "text": "Hi"})
            )

    seats.script[(first, 1)] = bypass
    result = roundtable.run_turn(table)
    assert result["outcome"]["post"] is False
    assert len(result["outcome"]["messages"]) == 3
    assert len(roundtable.mailbox(table, "supervisor")) == 3
    board = (table / "board" / "board.md").read_text()
    assert "post was not accepted: 250 words, over the 200-word limit" in board
    # Mail to the supervisor stops the run at the end of the round.
    assert roundtable.run(table)[1]["kind"] == "attention"
    roundtable.run(table, to=2)
    text = seats.prompt(first, 2)
    assert "Not delivered from your last turn: your post (250 words" in text
    assert "over the 3-message limit" in text
    # The outbox moved into the record whole, so the attempt stays readable.
    assert (table / "records" / first / "r001" / "outbox" / "post.md").exists()


def test_a_delivery_taken_twice_posts_once(table, monkeypatch):
    seats = Seats(monkeypatch)
    first = order(table, 1)[0]
    seats.script[(first, 1)] = posts("Once.")
    roundtable.run_turn(table)
    _, plan = roundtable.load(table)
    seat = roundtable.seat_by(plan, first)
    (table / "records" / first / "r001" / "delivered.json").unlink()
    roundtable.deliver(table, plan, seat, 1, 1)
    assert [e["kind"] for e in roundtable.entries(table)].count("post") == 1


def test_the_room_waits_for_the_supervisor_before_its_last_round(table, monkeypatch):
    Seats(monkeypatch)
    taken, where = roundtable.run(table)
    assert len(taken) == 6
    assert where == {"kind": "decision", "after": 2, "end": 3}
    with pytest.raises(ValueError, match="extend to a round after 3"):
        roundtable.decide(table, "extend", "More.", to=3)
    fact = roundtable.decide(table, "close", "We have enough.")
    assert fact == "Round 3 is the final round."
    board = (table / "board" / "board.md").read_text()
    assert "Round 3 is the final round.\n\nWe have enough." in board
    taken, where = roundtable.run(table)
    assert where == {"kind": "closed", "rounds": 3}
    assert "This is the final round." in (table / "board" / "board.md").read_text()


def test_the_final_round_says_so_to_every_seat(table, monkeypatch):
    seats = Seats(monkeypatch)
    roundtable.run(table)
    roundtable.decide(table, "close", "")
    roundtable.run(table)
    for seat in order(table, 3):
        assert "Round 3, the final round." in seats.prompt(seat, 3)
    assert "Round 2 of at least 3." in seats.prompt(order(table, 2)[0], 2)


def test_a_decision_before_its_time_is_refused(table, monkeypatch):
    Seats(monkeypatch)
    roundtable.run_turn(table)
    with pytest.raises(ValueError, match="no decision is due"):
        roundtable.decide(table, "close", "Too early.")


def test_an_extension_draws_new_orders_and_asks_again(table, monkeypatch):
    Seats(monkeypatch)
    roundtable.run(table)
    fact = roundtable.decide(table, "extend", "Keep going.", to=5)
    assert "at least to round 5" in fact
    assert set(read(table / "schedule.json")["rounds"]) == {"1", "2", "3", "4", "5"}
    taken, where = roundtable.run(table)
    assert where == {"kind": "decision", "after": 4, "end": 5}
    assert order(table, 3)[0] != order(table, 2)[-1]


def test_a_reordered_round_reads_its_reason_when_it_opens(table, monkeypatch):
    Seats(monkeypatch)
    ids = list(reversed(order(table, 2)))
    roundtable.reorder(table, 2, ids, "Luna asked to answer Haiku directly.")
    board = (table / "board" / "board.md").read_text()
    assert "asked to answer" not in board, "the reason waits for the round"
    roundtable.run(table, to=1)
    assert "asked to answer" not in (table / "board" / "board.md").read_text()
    roundtable.run_turn(table)
    board = (table / "board" / "board.md").read_text()
    assert (
        "The supervisor set this order: Luna asked to answer Haiku directly." in board
    )
    with pytest.raises(ValueError, match="has opened"):
        roundtable.reorder(table, 2, ids, "Again.")


def test_shared_files_travel_between_seats_with_their_author(table, monkeypatch):
    seats = Seats(monkeypatch)
    first, second, _ = order(table, 1)

    def draft(work):
        (work / "shared" / "names.md").write_text("Barnacle\n")
        (work / "shared" / "sneaky").symlink_to("/etc/hostname")
        (work / "shared" / "__pycache__").mkdir()
        (work / "shared" / "__pycache__" / "names.cpython-314.pyc").write_bytes(b"x")

    seen = []
    seats.script[(first, 1)] = draft
    seats.script[(second, 1)] = lambda work: seen.append(
        (work / "shared" / "names.md").read_text()
    )
    first_turn = roundtable.run_turn(table)
    assert first_turn["outcome"]["shared"]["added"] == ["names.md"]
    assert "links in shared/" in first_turn["outcome"]["refused"][0]
    roundtable.run_turn(table)
    assert seen == ["Barnacle\n"]
    assert not (table / "shared" / "sneaky").exists()
    assert not (table / "shared" / "__pycache__").exists()
    kept = table / "records" / first / "r001" / "shared" / "names.md"
    assert kept.read_text() == "Barnacle\n"
    board = (table / "board" / "board.md").read_text()
    _, plan = roundtable.load(table)
    assert f"*{roundtable.names(plan)[first]} added names.md in shared/.*" in board


def test_writing_to_the_supervisor_stops_the_run_at_the_round_end(table, monkeypatch):
    seats = Seats(monkeypatch)
    middle = order(table, 1)[1]
    seats.script[(middle, 1)] = lambda work: tool(
        work, "message", "supervisor", "Is the cat a tabby?"
    )
    taken, where = roundtable.run(table)
    assert len(taken) == 3
    assert where["kind"] == "attention" and where["mail"] == 1
    assert "Is the cat a tabby?" in roundtable.inbox(table)
    roundtable.reply(table, middle, "Yes, a grey tabby.")
    roundtable.run(table)
    assert "From the supervisor (before round 2):\n\nYes, a grey tabby." in (
        seats.prompt(middle, 2)
    )


def test_an_announcement_reaches_every_seat_that_acts_next(table, monkeypatch):
    seats = Seats(monkeypatch)
    roundtable.run(table, to=1)
    roundtable.announce(table, "Consider names from the sea.")
    roundtable.run(table, to=2)
    for seat in order(table, 2):
        assert "Consider names from the sea." in seats.prompt(seat, 2)


def test_a_provider_limit_holds_the_room(table, monkeypatch):
    seats = Seats(monkeypatch)
    first = order(table, 1)[0]
    seats.statuses[(first, 1)] = "quota"
    taken, where = roundtable.run(table)
    assert where["kind"] == "held" and where["seat"] == first
    assert roundtable.position(table, roundtable.load(table)[1])["kind"] == "held"


def test_a_failed_turn_is_named_and_stops_the_run_after_its_round(table, monkeypatch):
    seats = Seats(monkeypatch)
    first = order(table, 1)[0]
    seats.statuses[(first, 1)] = "failed"
    taken, where = roundtable.run(table)
    assert where == {"kind": "attention", "round": 1, "mail": 0, "failed": [first]}
    assert "turn did not complete" in (table / "board" / "board.md").read_text()


def test_the_seats_are_never_shown_the_signals(table, monkeypatch):
    seats = Seats(monkeypatch)
    for seat in order(table, 1):
        seats.script[(seat, 1)] = posts("We are unanimous: the verdict is Barnacle.")
    roundtable.run(table, to=1)
    value = roundtable.status(table)
    assert value["signals"] == [{"round": 1, "novelty": 100.0, "closing": 1.0}]
    assert "novelty" not in (table / "board" / "board.md").read_text()


def test_a_roundtable_turn_binds_its_folders_read_only(table, monkeypatch):
    """The shared launcher binds what the procedure names and records it."""
    from quirework.project import current_project

    current_project().root.mkdir(exist_ok=True)
    _, plan = roundtable.load(table)
    seat = plan["seats"][0]
    monkeypatch.setattr(room.drivers, "command", lambda *a, **k: (["true"], b""))
    monkeypatch.setattr(
        room.drivers, "launch_context", lambda *a: ([], {}, table / "private")
    )
    monkeypatch.setattr(
        room.drivers,
        "receipt",
        lambda *a, **k: {"session_id": "s-1", "model": "haiku"},
    )
    monkeypatch.setattr(room.blind, "session_bus", lambda runners: nullcontext())
    monkeypatch.setattr(
        room.blind,
        "popen",
        lambda argv, bus, **kw: room.subprocess.Popen(["true"], **kw),
    )
    launched = []
    monkeypatch.setattr(
        room.usage_records,
        "seat",
        lambda workflow, config, **kw: launched.append(workflow),
    )
    status = room.launch_turn(
        table,
        plan,
        seat,
        1,
        "Go.",
        None,
        workflow="roundtable",
        readonly=roundtable.READONLY,
    )
    assert status == "answered" and launched == ["roundtable"]
    jail = read(table / "records" / seat["id"] / "r001" / "launch.json")["jail"]
    work = table / "seats" / seat["id"]
    for folder in ("board", "mail", "bin"):
        assert ["--ro-bind", str(work / folder), str(work / folder)] == jail[
            jail.index(str(work / folder)) - 1 : jail.index(str(work / folder)) + 2
        ]


def test_workbench_gives_each_harness_a_shell_and_the_web(tmp_path):
    base = {"model": "m", "effort": "medium", "tools": "workbench"}
    claude, _ = drivers.command({**base, "harness": "claude"}, tmp_path, "x")
    tools = claude[claude.index("--tools") + 1]
    assert {"Bash", "WebSearch", "WebFetch"} <= set(tools.split(","))
    codex, _ = drivers.command(
        {**base, "harness": "codex", "max_context_window": 1000}, tmp_path, "x"
    )
    assert "sandbox_workspace_write.network_access=true" in codex
    assert 'web_search="live"' in codex
    drivers.command({**base, "harness": "agy"}, tmp_path, "x")
    with pytest.raises(ValueError, match="cannot confine"):
        drivers.command({**base, "harness": "agy", "tools": "research"}, tmp_path, "x")
    with pytest.raises(ValueError, match="tools must be one of"):
        drivers.command({**base, "harness": "claude", "tools": "all"}, tmp_path, "x")


def agy_record(tmp_path, tool_name, tools):
    """A finished agy turn whose transcript used one tool."""
    record, private = tmp_path / "record", tmp_path / "private"
    record.mkdir(parents=True)
    rows = [
        {"event": "init", "init": {"model": "gemini-flash"}, "conversation_id": "c1"},
        {"event": "result", "result": {"status": "SUCCESS", "response": "ok"}},
    ]
    (record / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    logs = private / "antigravity-cli/brain/c1/.system_generated/logs"
    logs.mkdir(parents=True)
    (logs / "transcript_full.jsonl").write_text(
        json.dumps({"tool_calls": [{"name": tool_name}]}) + "\n"
    )
    config = {"harness": "agy", "model": "gemini-flash", "effort": "medium"}
    return {**config, "tools": tools}, record, private


def test_agy_may_search_at_a_workbench_and_nowhere_else(tmp_path):
    config, record, private = agy_record(tmp_path / "a", "search_web", "workbench")
    assert drivers.receipt(config, record, private)["session_id"] == "c1"
    config, record, private = agy_record(tmp_path / "b", "search_web", "write")
    with pytest.raises(RuntimeError, match="outside the text assignment"):
        drivers.receipt(config, record, private)
    config, record, private = agy_record(tmp_path / "c", "browser_open", "workbench")
    with pytest.raises(RuntimeError, match="outside the text assignment"):
        drivers.receipt(config, record, private)


def test_context_is_read_from_the_latest_request(tmp_path):
    record = tmp_path / "record"
    record.mkdir()
    usage = lambda n: {  # noqa: E731
        "type": "assistant",
        "message": {"usage": {"input_tokens": n, "cache_read_input_tokens": 1000}},
    }
    rows = [
        usage(10),
        usage(50),
        {"type": "result", "modelUsage": {"m": {"contextWindow": 200000}}},
    ]
    (record / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    got = drivers.context_now("claude", record)
    assert (got["tokens"], got["window"]) == (1050, 200000)
    rollout = tmp_path / "rollout.jsonl"
    count = lambda n: {  # noqa: E731
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "last_token_usage": {"input_tokens": n, "cached_input_tokens": 0},
                "model_context_window": 800000,
            },
        },
    }
    rollout.write_text("".join(json.dumps(count(n)) + "\n" for n in (900, 300)))
    got = drivers.context_now("codex", record, [rollout])
    assert (got["tokens"], got["window"]) == (300, 800000)
    assert drivers.context_now("agy", record)["tokens"] is None


def test_the_context_line_warns_when_the_window_is_nearly_spent():
    line = roundtable.context_line({"tokens": 170000, "window": 200000})
    assert "170,000 of 200,000 tokens (85%)" in line
    assert "Leave room" in line
    assert "does not report" in roundtable.context_line(
        {"tokens": None, "window": None}
    )


def test_status_reads_the_table_and_names_what_it_waits_for(table, monkeypatch):
    Seats(monkeypatch)
    roundtable.run(table)
    text = roundtable.render_status(roundtable.status(table))
    assert "decision due after round 2: is round 3 the last?" in text
    assert "Gemini-Flash" in text


@pytest.mark.parametrize(
    ("window", "allowance"),
    [
        (200_000, 160_000),
        (500_000, 400_000),
        (600_000, 400_000),
        (828_400, 497_040),
        (1_000_000, 600_000),
    ],
)
def test_a_budget_is_most_of_a_small_window_and_less_of_a_large_one(window, allowance):
    assert roundtable.allowance_for(window) == allowance


def test_a_larger_window_never_gets_a_smaller_budget():
    sizes = range(100_000, 2_000_001, 10_000)
    budgets = [roundtable.allowance_for(w) for w in sizes]
    assert budgets == sorted(budgets)


def test_every_seat_is_told_its_budget_and_the_time_limit(table):
    _, plan = roundtable.load(table)
    assert [s["allowance"] for s in plan["seats"]] == [160_000, 497_040, 629_145]
    haiku = " ".join((table / "seats/haiku/START.md").read_text().split())
    assert "Your budget is 160,000 tokens, 80% of your 200,000-token window." in haiku
    assert "the turn that crosses it is that model's last" in haiku
    assert "a turn still running after 4 hours, which is treated as hung" in haiku
    assert "estimates" not in haiku
    gemini = " ".join((table / "seats/gemini-flash/START.md").read_text().split())
    assert "those figures are estimates" in gemini


def test_a_model_with_no_known_window_is_refused(plan, tmp_path):
    spec = read(plan)
    spec["seats"].append({"model": "gpt-oss-120b", "harness": "agy"})
    write(plan, spec)
    with pytest.raises(ValueError, match="no context window is known.*give the seat"):
        roundtable.prepare(plan, tmp_path / "unknown", resolve_models=False)


def test_each_turn_states_what_was_spent_and_what_remains(table, monkeypatch):
    seats = Seats(monkeypatch)
    seats.context[("haiku", 1)] = 30_000
    seats.context[("haiku", 2)] = 72_000
    roundtable.run(table)
    roundtable.decide(table, "close", "")
    roundtable.run(table)
    assert "budget is 160,000 tokens. This is your first turn." in seats.prompt(
        "haiku", 1
    )
    assert (
        "You have used 30,000; your last turn added 30,000. 130,000 remain."
        in seats.prompt("haiku", 2)
    )
    assert (
        "You have used 72,000; your last turn added 42,000. 88,000 remain."
        in seats.prompt("haiku", 3)
    )


def test_a_seat_past_its_budget_takes_no_more_turns(table, monkeypatch):
    seats = Seats(monkeypatch)
    seats.context[("haiku", 1)] = 170_000
    roundtable.run(table)
    board = (table / "board" / "board.md").read_text()
    assert "Haiku has used its context budget (170,000 of 160,000 tokens)" in board
    assert not [p for p in seats.prompts if p["seat"] == "haiku" and p["round"] > 1]
    second = [e for e in roundtable.entries(table) if e["kind"] == "round"][1]
    assert "haiku" not in second["order"]
    for later in (p["text"] for p in seats.prompts if p["round"] == 2):
        head = next(line for line in later.split("\n\n") if line.startswith("Round"))
        assert "of 2 this round" in head
        assert "Haiku" not in head, "nobody is told Haiku is still to come"
    text = roundtable.render_status(roundtable.status(table))
    assert "retired after round 1: 170,000 of 160,000 tokens" in text


def test_a_room_whose_every_seat_is_spent_cannot_go_on(table, monkeypatch):
    seats = Seats(monkeypatch)
    for seat, spent in (("haiku", 170_000), ("luna", 500_000), ("gemini-flash", 0)):
        seats.context[(seat, 1)] = spent
    # agy's estimate is not what the stand-in writes, so retire it by hand.
    value = read(room.state_path(table, "gemini-flash"))
    value["retired"] = {"round": 1, "tokens": 700_000, "allowance": 629_145}
    write(room.state_path(table, "gemini-flash"), value)
    taken, where = roundtable.run(table)
    assert where == {"kind": "spent", "round": 2}


def test_agy_occupancy_is_estimated_from_billing_and_transcript_growth(tmp_path):
    transcript = tmp_path / "transcript_full.jsonl"
    rows = [{"type": "PLANNER_RESPONSE"}] * 4 + [{"type": "GENERIC"}]
    transcript.write_text("".join(json.dumps(r) + "\n" for r in rows))
    size = transcript.stat().st_size
    usage = {"input_tokens": 30_000, "cache_read_tokens": 50_000}
    tokens, marker = drivers.agy_estimate(usage, [transcript])
    assert tokens == int(80_000 / 4 + size / drivers.CHARS_PER_TOKEN / 2)
    assert marker == {"bytes": size, "responses": 4}
    # The next turn counts only its own requests and its own growth.
    with transcript.open("a") as f:
        f.write(json.dumps({"type": "PLANNER_RESPONSE"}) + "\n")
    grown = transcript.stat().st_size - size
    tokens, _ = drivers.agy_estimate({"input_tokens": 90_000}, [transcript], marker)
    assert tokens == int(90_000 + grown / drivers.CHARS_PER_TOKEN / 2)
    assert drivers.agy_estimate({}, [transcript], marker)[0] is None


def test_windows_are_known_before_a_model_has_run():
    known = drivers.stated_window
    assert known({"harness": "claude", "model": "haiku"}) == (
        200_000,
        "documented window",
    )
    assert known({"harness": "claude", "model": "claude-opus-5-5"})[0] == 1_000_000
    assert known({"harness": "agy", "model": "gemini-3.1-pro-high"})[0] == 1_048_576
    codex = {
        "harness": "codex",
        "model": "gpt-6-luna",
        "max_context_window": 872_000,
        "effective_context_window_percent": 95,
    }
    assert known(codex) == (828_400, "Codex catalog")
    assert known({"harness": "agy", "model": "gpt-oss-120b-medium"}) == (None, None)


def test_a_table_prepared_before_budgets_keeps_its_old_context_line(table):
    _, plan = roundtable.load(table)
    seat = {k: v for k, v in plan["seats"][0].items() if k != "allowance"}
    value = {"rounds": {"1": {}}, "context": {"tokens": 6000, "window": 200000}}
    assert roundtable.budget_line(seat, value) == (
        "Your context holds about 6,000 of 200,000 tokens (3%)."
    )
