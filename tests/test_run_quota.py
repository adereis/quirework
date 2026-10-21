"""Quota refusals must cost a wait, never a session or a banked answer.

A provider that refuses a turn on an account limit has not run the model, but
it has already appended the reveal -- and its own refusal notice -- to the
session. Resuming without undoing that would show the reader the same chapter
twice and a quota message in its own voice. These tests pin the three
properties that make the recovery safe: the refusal is told apart from an
ambiguous transport fault, the session is returned exactly to its prior state,
and a seat stalled this way never withholds the seats that finished.
"""

from quirework.runs import commands as run_commands
import json

import pytest
from conftest import native_session

from quirework.runs import engine
from quirework.providers import blind
from quirework.providers import harness
from quirework.providers import quota
from quirework.runs.storage import hashes

RESET = 4_000_000_000


def claude_events(path, *, refused=True, answered=False, interrupted=False):
    rows = [dict(type="system", subtype="init", session_id="sid")]
    if interrupted:
        # The model ran, read, reasoned and wrote before the wall arrived.
        # Everything below is then the limit landing on top of real work.
        rows.append(
            dict(
                type="assistant",
                session_id="sid",
                message=dict(
                    model="fixture-model",
                    content=[dict(type="text", text="Half an answer.")],
                ),
            )
        )
    if refused:
        rows.append(
            dict(
                type="rate_limit_event",
                session_id="sid",
                rate_limit_info=dict(
                    status="rejected", rateLimitType="five_hour", resetsAt=RESET
                ),
            )
        )
        rows.append(
            dict(
                type="assistant",
                message=dict(
                    model="fixture-model",
                    content=[dict(type="text", text="You've hit your session limit")],
                ),
            )
        )
        rows.append(
            dict(
                type="result",
                subtype="success",
                is_error=True,
                session_id="sid",
                result="You've hit your session limit",
                modelUsage=(
                    {"fixture-model": dict(outputTokens=7400)} if interrupted else {}
                ),
            )
        )
    if answered:
        rows.append(
            dict(
                type="assistant",
                message=dict(
                    model="fixture-model", content=[dict(type="text", text="A reply.")]
                ),
            )
        )
        rows.append(
            dict(
                type="result",
                subtype="success",
                is_error=False,
                session_id="sid",
                result="A reply.",
                modelUsage={"fixture-model": dict(outputTokens=12)},
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_quota_refusal_is_distinguished_from_a_transport_fault(tmp_path):
    """The notice now arrives as an assistant row; that is not an answer."""
    record = tmp_path / "refused"
    claude_events(record / "events.jsonl")
    detail = harness.quota_rejection("claude", record)
    assert detail and detail["scope"] == "five_hour" and detail["resets_at"] == RESET

    served = tmp_path / "served"
    claude_events(served / "events.jsonl", refused=False, answered=True)
    assert harness.quota_rejection("claude", served) is None
    assert not harness.rejected_without_answer("claude", served)


def test_a_served_turn_is_never_treated_as_refused(tmp_path):
    """An error result alongside real output tokens stays unresolved."""
    record = tmp_path / "ambiguous"
    record.mkdir()
    (record / "events.jsonl").write_text(
        json.dumps(
            dict(
                type="result",
                subtype="success",
                is_error=True,
                result="transport closed",
                modelUsage={"fixture-model": dict(outputTokens=340)},
            )
        )
        + "\n"
    )
    assert harness.quota_rejection("claude", record) is None


def test_rewind_removes_only_the_refused_turn(tmp_path):
    session = tmp_path / "session.jsonl"
    session.write_text('{"turn": 1}\n{"turn": 2}\n')
    marks = engine.session_marks([session])
    session.write_text('{"turn": 1}\n{"turn": 2}\n{"turn": 3}\n{"refusal": true}\n')
    engine.rewind_sessions(marks)
    assert session.read_text() == '{"turn": 1}\n{"turn": 2}\n'


def test_rewind_refuses_a_session_it_did_not_only_append_to(tmp_path):
    """A rewritten prefix is no longer a clean resume; the operator decides."""
    session = tmp_path / "session.jsonl"
    session.write_text('{"turn": 1}\n')
    marks = engine.session_marks([session])
    session.write_text('{"turn": 9}\n{"turn": 2}\n')
    with pytest.raises(RuntimeError, match="prefix changed"):
        engine.rewind_sessions(marks)
    assert session.read_text() == '{"turn": 9}\n{"turn": 2}\n'

    session.write_text("")
    with pytest.raises(RuntimeError, match="shrank"):
        engine.rewind_sessions(marks)


def test_rewind_is_a_no_op_when_nothing_was_appended(tmp_path):
    session = tmp_path / "session.jsonl"
    session.write_text('{"turn": 1}\n')
    marks = engine.session_marks([session])
    engine.rewind_sessions(marks)
    assert session.read_text() == '{"turn": 1}\n'


def test_delay_follows_the_provider_reset(monkeypatch):
    now = RESET - 120
    assert engine.quota_delay(dict(resets_at=RESET), now=now) == 240
    # A reset already past, or absent, still waits rather than hammering.
    assert engine.quota_delay(dict(resets_at=RESET), now=RESET + 5) == 300
    assert engine.quota_delay({}, now=now) == 300
    # Never sleeps beyond a bounded window, whatever the provider claims.
    assert engine.quota_delay(dict(resets_at=RESET), now=0) == 6 * 3600


@pytest.fixture
def two_seats(tmp_path):
    (tmp_path / "one.md").write_text("A kite caught the fence.\n")
    plan = dict(
        title="Fictitious scene",
        seats=[
            dict(
                id=name,
                harness="claude",
                model="fixture-model",
                effort="high",
                turns=[
                    dict(
                        id="one",
                        prompt="Read this.",
                        materials=[dict(source="one.md", delivery="text")],
                    )
                ],
            )
            for name in ("finished", "stalled")
        ],
    )
    path = tmp_path / "input.json"
    path.write_text(json.dumps(plan))
    engine.prepare(path, tmp_path / "record", resolve=False)
    return engine.Run(tmp_path / "record")


def test_a_stalled_seat_rewinds_and_becomes_resumable(two_seats):
    """The stuck state clears itself instead of blocking every later reveal."""
    run = two_seats
    session = run.private("stalled") / "projects" / "p" / "sid.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text('{"turn": "banked"}\n')
    marks = engine.session_marks([session])
    session.write_text('{"turn": "banked"}\n{"reveal": "refused"}\n{"notice": 1}\n')

    record = run.record("stalled", "one")
    claude_events(record / "events.jsonl")
    state = run.state("stalled")
    state["pending"] = dict(
        turn="one", mode="resume", phase="receipt-pending", resume_point=marks
    )
    run.save("stalled", state)

    with pytest.raises(engine.QuotaPaused):
        run.turn("stalled", 0, 1, wait_quota=None)

    after = run.state("stalled")
    assert after["pending"] is None, "a refused turn must not stay pending"
    assert after["attempts"]["one"] == 1, "the refused attempt is retired, not reused"
    assert "quota" in after["retired_attempts"]["one"]["0"]["reason"]
    assert session.read_text() == '{"turn": "banked"}\n', "session was not rewound"
    assert (record / "events.jsonl").exists(), "the refused attempt is preserved"


def test_one_stalled_seat_does_not_withhold_a_finished_seat(two_seats, tmp_path):
    run = two_seats
    record = run.record("finished", "one")
    (record / "answers").mkdir(parents=True)
    (record / "answers" / "answer.md").write_text("The kite came down.\n")
    receipt = dict(session_id="sid", model="fixture-model", rollout="x")
    (record / "receipt.json").write_text(json.dumps(receipt))
    state = run.state("finished")
    state["done"]["one"] = dict(
        attempt=0,
        receipt=receipt,
        answer_hashes=hashes(record / "answers"),
        record_hashes=hashes(record),
    )
    state["complete"] = True
    run.save("finished", state)

    with pytest.raises(RuntimeError, match="seats that did finish"):
        run.export(tmp_path / "strict")

    out = run.export(tmp_path / "partial", partial=True)
    receipt = json.loads((tmp_path / "partial" / "receipt.json").read_text())
    assert (tmp_path / "partial" / "finished" / "one" / "answer.md").exists()
    assert receipt["seats"]["finished"]["_completeness"]["complete"] is True
    assert receipt["seats"]["stalled"]["_completeness"] == dict(
        status="not started",
        declared_turns=1,
        answered_turns=0,
        complete=False,
        pending=None,
    )
    assert receipt["not_started"] == ["stalled"]
    assert out


def test_a_new_flag_is_not_forced_on_an_older_frozen_executor(two_seats, monkeypatch):
    """The bootstrap must not hand a frozen executor an argument it lacks.

    A record runs the code it froze. Passing this run's newer flags into an
    older snapshot makes it exit on an argparse error before it does anything
    -- which is how a live resume was lost once already.
    """
    from quirework.runs import engine as mod

    seen = []
    monkeypatch.setattr(
        mod, "frozen_dispatch", lambda root, args: seen.append(args) or 0
    )
    record = str(two_seats.root)
    run_commands.main(["start", record, "finished", "--jobs", "1"])
    assert "--wait-quota" not in seen[-1], "default must stay implicit"
    run_commands.main(
        ["start", record, "finished", "--jobs", "1", "--wait-quota", "60"]
    )
    assert seen[-1][-2:] == ["--wait-quota", "60.0"], "an explicit choice is forwarded"


def test_the_window_asked_for_is_the_model_s_own_ceiling():
    """A hardcoded window goes stale silently; the catalog does not."""
    catalog = dict(
        harness="codex",
        model="fixture-model",
        context_window=272_000,
        max_context_window=872_000,
        effective_context_window_percent=95,
    )
    p = engine.resolve_window(engine.policy({}), catalog)
    assert p["context_window"] == 872_000, "must ask for the ceiling, not the default"
    # 872000 * 0.95 = 828400 served; the guard sits just under it so a
    # rounding change does not stop a run, but the 272k default would fail.
    assert 820_000 <= p["minimum_context_window"] < 828_400
    assert p["minimum_context_window"] > catalog["context_window"]


def test_an_unprepared_policy_never_leaks_a_sentinel():
    """policy() alone must yield numbers; only preparation fills the ceiling."""
    p = engine.policy({})
    assert p["context_window"] is None and p["minimum_context_window"] == 0


def test_a_harness_without_a_window_receipt_asserts_no_minimum(monkeypatch):
    """Claude evidences no effective window, so a minimum could never pass."""
    p = engine.resolve_window(engine.policy({}), dict(harness="claude"))
    assert p["context_window"] == engine.CLAUDE_MAX_WINDOW
    assert p["minimum_context_window"] == 0


def test_claude_is_told_not_to_compact(monkeypatch):
    """Compaction was previously detected there but never prevented."""
    config = dict(harness="claude", model="fixture-model", effort="high")
    base = dict(tools="none", read_roots=[], context_window=1_000_000)
    args, _, _ = harness.command(
        config, dict(base, allow_compaction=False), "start", "sid", "hi", [], []
    )
    assert args[args.index("--autocompact") + 1] == "1000000"
    allowed, _, _ = harness.command(
        config, dict(base, allow_compaction=True), "start", "sid", "hi", [], []
    )
    assert "--autocompact" not in allowed


def test_an_explicit_window_still_overrides_the_ceiling():
    p = engine.resolve_window(
        engine.policy(dict(context_window=300_000, minimum_context_window=250_000)),
        dict(harness="codex", max_context_window=872_000),
    )
    assert (p["context_window"], p["minimum_context_window"]) == (300_000, 250_000)


def test_a_limit_that_lands_mid_turn_continues_instead_of_rewinding(two_seats):
    """The opposite recovery to the one above, for the opposite provider act.

    Here the model DID run: it read the material, reasoned about it and may
    have written files, and all of that is inside the session. Rewinding would
    delete it and re-delivering the material would show the reader the same
    pages twice, so the turn is continued in place -- and the next submission
    carries the continuation notice, no material at all, and the turn's
    ORIGINAL output baseline, because the turn did change its files, just
    across two submissions.
    """
    run = two_seats
    session = run.private("stalled") / "projects" / "p" / "sid.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text('{"turn": "banked"}\n')
    marks = engine.session_marks([session])
    session.write_text('{"turn": "banked"}\n{"reveal": 1}\n{"partial": 1}\n')
    intact = session.read_text()

    record = run.record("stalled", "one")
    claude_events(record / "events.jsonl", interrupted=True)
    state = run.state("stalled")
    state["pending"] = dict(
        turn="one",
        mode="start",
        phase="receipt-pending",
        resume_point=marks,
        outputs_before={"drafts/x.md": "abc"},
    )
    run.save("stalled", state)

    with pytest.raises(engine.QuotaPaused):
        run.turn("stalled", 0, 1, wait_quota=None)

    after = run.state("stalled")
    assert session.read_text() == intact, "an interrupted turn is never rewound"
    assert after["pending"] is None
    assert after["session_id"] == "sid", "the session to continue is recovered"
    assert after["continuation"] == dict(
        turn="one",
        session_id="sid",
        scope="five_hour",
        outputs_before={"drafts/x.md": "abc"},
    )
    assert "continued, not restarted" in after["retired_attempts"]["one"]["0"]["reason"]


def test_an_interrupted_turn_without_a_session_refuses_to_guess(two_seats):
    """No session id means nothing to continue, and a silent restart would be
    the one recovery that destroys evidence."""
    run = two_seats
    record = run.record("stalled", "one")
    rows = [
        dict(
            type="assistant",
            message=dict(model="fixture-model", content=[dict(type="text", text="x")]),
        ),
        dict(
            type="rate_limit_event",
            rate_limit_info=dict(status="rejected", rateLimitType="five_hour"),
        ),
        dict(
            type="result",
            subtype="success",
            is_error=True,
            result="You've hit your session limit",
            modelUsage={"fixture-model": dict(outputTokens=10)},
        ),
    ]
    record.mkdir(parents=True, exist_ok=True)
    (record / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    state = run.state("stalled")
    state["pending"] = dict(turn="one", mode="start", phase="receipt-pending")
    run.save("stalled", state)
    with pytest.raises(RuntimeError, match="names no single session"):
        run.turn("stalled", 0, 1, wait_quota=None)


def test_seats_run_cheapest_first_then_premium(tmp_path):
    """One shakedown seat, then the seat a wall must not be allowed to take."""
    (tmp_path / "one.md").write_text("A kite caught the fence.\n")
    plan = dict(
        title="Fictitious scene",
        seats=[
            dict(
                id=name,
                harness="claude",
                model="fixture-model",
                effort="high",
                tier=tier,
                turns=[
                    dict(
                        id="one",
                        prompt="Read this.",
                        materials=[dict(source="one.md", delivery="text")],
                    )
                ],
            )
            for name, tier in (
                ("costly", "premium"),
                ("middle", "standard"),
                ("thrifty", "cheap"),
            )
        ],
    )
    path = tmp_path / "input.json"
    path.write_text(json.dumps(plan))
    engine.prepare(path, tmp_path / "record", resolve=False)
    run = engine.Run(tmp_path / "record")
    assert run.plan["order"] == ["thrifty", "costly", "middle"]


def test_retry_sends_an_interrupted_turn_to_the_continuation_instead(two_seats):
    """`retry` is for a rejection that produced nothing. Pointed at a turn the
    wall took mid-work it would discard the session, so it says so."""
    run = two_seats
    record = run.record("stalled", "one")
    claude_events(record / "events.jsonl", interrupted=True)
    state = run.state("stalled")
    state["pending"] = dict(turn="one", mode="start", phase="receipt-pending")
    run.save("stalled", state)
    with pytest.raises(RuntimeError, match="continued in its own session"):
        engine.retry(run, "stalled", "one")


def claude_events_cut_by_a_fault():
    """The model ran and the stream then ended with no result at all."""
    return [
        dict(type="system", subtype="init", session_id="sid"),
        dict(
            type="assistant",
            session_id="sid",
            message=dict(model="fixture-model", content=[dict(type="text", text="x")]),
        ),
    ]


def stopped_without_a_cause(run, rows):
    record = run.record("stalled", "one")
    record.mkdir(parents=True, exist_ok=True)
    (record / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    # The stopped turn's harness had already opened the session it names.
    harness_name = run.seats["stalled"]["config"]["harness"]
    native_session(run.workspace("stalled"), harness_name, "sid")
    state = run.state("stalled")
    state["pending"] = dict(
        turn="one",
        mode="start",
        phase="receipt-pending",
        outputs_before={"drafts/x.md": "abc"},
    )
    run.save("stalled", state)


def test_the_operator_continues_a_stop_nothing_classified(two_seats):
    """A dropped stream is no quota and no rejection, and its session is kept.

    The runner cannot tell what was served, so it decides nothing; the
    operator reads the record and continues the session, and that decision
    and its reason are what the record keeps.
    """
    run = two_seats
    stopped_without_a_cause(run, claude_events_cut_by_a_fault())
    result = engine.continue_turn(run, "stalled", "one", "  stream dropped  ")
    assert result["session_id"] == "sid" and result["continued"]

    after = run.state("stalled")
    assert after["pending"] is None
    assert after["session_id"] == "sid"
    assert after["continuation"] == dict(
        turn="one",
        session_id="sid",
        scope=engine.OPERATOR_CONTINUATION,
        reason="stream dropped",
        outputs_before={"drafts/x.md": "abc"},
    )
    reason = after["retired_attempts"]["one"]["0"]["reason"]
    assert "operator continued it in its own session: stream dropped" in reason


def test_the_next_submission_resumes_the_session_with_the_error_prompt(
    two_seats, monkeypatch, canaries_pass
):
    """The decision is carried out by `start`: same session, no material."""
    run = two_seats
    stopped_without_a_cause(run, claude_events_cut_by_a_fault())
    engine.continue_turn(run, "stalled", "one", "stream dropped")

    sent = []

    def command(config, policy, mode, sid, prompt, images, files):
        sent.append(dict(mode=mode, sid=sid, prompt=prompt, material=images + files))
        return ["fixture"], b"", prompt

    monkeypatch.setattr(harness, "command", command)
    monkeypatch.setattr(
        harness, "launch_context", lambda *a: ([], {}, run.private("stalled"))
    )

    def fail_spawn(*a, **kw):
        raise OSError("synthetic pre-spawn failure")

    monkeypatch.setattr(engine.subprocess, "Popen", fail_spawn)
    with pytest.raises(OSError, match="pre-spawn"):
        run.turn("stalled", 0, 1)
    assert sent == [
        dict(
            mode="resume",
            sid="sid",
            prompt=quota.continuation(cause="error"),
            material=[],
        )
    ]


def test_a_resume_is_refused_before_anything_is_staged_once_its_home_is_gone(
    two_seats, monkeypatch, canaries_pass
):
    """A resume into an empty home fails for a reason the harness never names."""
    run = two_seats
    stopped_without_a_cause(run, claude_events_cut_by_a_fault())
    engine.continue_turn(run, "stalled", "one", "stream dropped")
    blind.discard_state_root(run.workspace("stalled"))
    before = run.state("stalled")
    monkeypatch.setattr(harness, "command", lambda *a: pytest.fail("launched"))
    with pytest.raises(blind.SessionGone, match="session sid: its private home"):
        run.turn("stalled", 0, 1)
    assert run.state("stalled") == before


def test_a_launch_holds_the_seats_home(two_seats, monkeypatch, canaries_pass):
    """The next turn resumes this session, so prune must keep the home."""
    run = two_seats
    stopped_without_a_cause(run, claude_events_cut_by_a_fault())
    engine.continue_turn(run, "stalled", "one", "stream dropped")
    monkeypatch.setattr(harness, "command", lambda *a: (["fixture"], b"", a[4]))
    monkeypatch.setattr(
        harness, "launch_context", lambda *a: ([], {}, run.private("stalled"))
    )

    def fail_spawn(*a, **kw):
        raise OSError("synthetic pre-spawn failure")

    monkeypatch.setattr(engine.subprocess, "Popen", fail_spawn)
    with pytest.raises(OSError, match="pre-spawn"):
        run.turn("stalled", 0, 1)
    home = blind.state_root(run.workspace("stalled"))
    assert "seat stalled continues this session until it completes" in blind.held(home)


def test_continue_refuses_what_it_cannot_honestly_continue(two_seats):
    run = two_seats
    with pytest.raises(ValueError, match="--reason"):
        engine.continue_turn(run, "stalled", "one", " ")
    with pytest.raises(ValueError, match="needs the pending turn"):
        engine.continue_turn(run, "stalled", "one", "nothing is pending")

    # No session in the stream: nothing to resume, and no guess is made.
    stopped_without_a_cause(
        run,
        [dict(type="assistant", message=dict(model="fixture-model", content=[]))],
    )
    with pytest.raises(RuntimeError, match="no single session"):
        engine.continue_turn(run, "stalled", "one", "no session")


def test_a_recognised_quota_stop_is_left_to_start(two_seats):
    run = two_seats
    record = run.record("stalled", "one")
    claude_events(record / "events.jsonl", interrupted=True)
    state = run.state("stalled")
    state["pending"] = dict(turn="one", mode="start", phase="receipt-pending")
    run.save("stalled", state)
    with pytest.raises(RuntimeError, match="`start` recovers it"):
        engine.continue_turn(run, "stalled", "one", "limit")


def test_the_resumed_agent_is_told_of_an_error_not_a_limit():
    """It needs only to know it was cut off; naming a cause would guess."""
    prompt = quota.continuation(cause="error")
    assert prompt.startswith("We hit an error")
    assert "quota" not in prompt and "resume your task" in prompt
    assert quota.continuation() == quota.CONTINUATION
    with pytest.raises(ValueError):
        quota.continuation(cause="weather")


def test_continue_is_forwarded_with_its_reason(two_seats, monkeypatch):
    seen = []
    monkeypatch.setattr(
        engine, "frozen_dispatch", lambda root, args: seen.append(args) or 0
    )
    code = run_commands.main(
        ["continue", str(two_seats.root), "stalled", "one", "--reason", "net"]
    )
    assert code == 0
    assert seen[0][2:] == ["stalled", "one", "--reason", "net"]
