"""A seat's answer is its whole reply, even where the output ceiling cut it.

Claude Code resumes a response that reaches the ceiling in a further one,
after a message of its own, and its result row then holds only the last
part. These tests build such a turn from fictitious rows and check that
the receipt banks every part, says where they were joined, and keeps the
harness's message apart from the seat's words. An existing record is
checked the same way by `observe`.
"""

import json

from quirework.providers import harness
from quirework.providers import models as drivers
from quirework.providers import replies
from quirework.publish import experiment
from quirework.runs import observe
from quirework.runs.storage import digest
from quirework.sops import review

POLICY = dict(
    tools="none", read_roots=[], allow_compaction=False, minimum_context_window=0
)
CONFIG = dict(harness="claude", model="fixture-model", effort="high")
RESUME = "Output limit reached. Resume where you stopped."


def response(ident, text, *, thinking=True):
    """The stream's rows for one response: a thinking block, then its text."""
    rows = []
    if thinking:
        rows.append(
            dict(
                type="assistant",
                parent_tool_use_id=None,
                message=dict(
                    id=ident,
                    model="fixture-model",
                    content=[dict(type="thinking", thinking="")],
                ),
            )
        )
    if text is not None:
        rows.append(
            dict(
                type="assistant",
                parent_tool_use_id=None,
                message=dict(
                    id=ident,
                    model="fixture-model",
                    content=[dict(type="text", text=text)],
                ),
            )
        )
    return rows


def injected(text=RESUME):
    return dict(
        type="user",
        isSynthetic=True,
        message=dict(role="user", content=[dict(type="text", text=text)]),
    )


def turn(tmp_path, parts, *, result_stop="end_turn"):
    """A record whose stream holds ``parts``, ``(id, text, stop)`` triples
    with a harness message between them, and its native session."""
    stream = [
        dict(
            type="system",
            subtype="init",
            session_id="sid",
            model="fixture-model",
            tools=["Read"],
            mcp_servers=[],
            output_style="default",
        )
    ]
    native = []
    for index, (ident, text, stop) in enumerate(parts):
        if index:
            stream.append(injected())
        stream += response(ident, text)
        native.append(
            dict(
                type="assistant",
                effort="high",
                message=dict(
                    id=ident,
                    stop_reason=stop,
                    usage=dict(input_tokens=10, output_tokens=64000),
                ),
            )
        )
    stream.append(
        dict(
            type="result",
            subtype="success",
            is_error=False,
            session_id="sid",
            stop_reason=result_stop,
            result=parts[-1][1] or "",
            modelUsage={
                "fixture-model": dict(
                    outputTokens=64100, costUSD=0.5, maxOutputTokens=64000
                )
            },
        )
    )
    record = tmp_path / "attempt"
    record.mkdir()
    (record / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in stream))
    private = tmp_path / "private"
    path = private / "projects" / "work" / "sid.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in native))
    return record, private


def test_a_reply_resumed_after_the_ceiling_is_banked_whole(tmp_path):
    record, private = turn(
        tmp_path,
        [
            ("m1", "Mira left the umbrella by the do", "max_tokens"),
            ("m2", "or, and it was green.", "end_turn"),
        ],
    )
    receipt = harness.receipt(CONFIG, POLICY, record, private)
    assert receipt["answer"] == "Mira left the umbrella by the door, and it was green."
    assert receipt["output_limit"] == dict(
        responses_at_limit=1,
        answer_joined_at=[32],
        answer_ends_at_limit=False,
        cut_at=[64000],
        ceiling=64000,
    )
    assert receipt["harness_text"] == [
        dict(text=RESUME, cause="output limit", answer_offset=32)
    ]
    assert RESUME not in receipt["answer"]
    (flag,) = receipt["red_flags"]
    assert "output limit once" in flag and "character 32" in flag


def test_every_procedure_that_reads_a_claude_answer_banks_it_whole(tmp_path):
    """Rooms and workshops, reviews and chapter comparisons each read a
    Claude answer through their own receipt, and each had read the result
    row alone."""
    record, private = turn(
        tmp_path,
        [
            ("m1", "Mira left the umbrella by the do", "max_tokens"),
            ("m2", "or, and it was green.", "end_turn"),
        ],
    )
    whole = "Mira left the umbrella by the door, and it was green."
    stream = drivers.json_rows(record / "events.jsonl")
    reaction, compared = experiment.parse_claude(
        stream, private, "fixture-model", "high", None
    )
    read = [
        drivers.receipt(CONFIG, record, private),
        review._answer("claude", CONFIG, stream, private),
        compared | {"answer": reaction},
    ]
    for receipt in read:
        assert receipt["answer"] == whole
        assert receipt["output_limit"]["answer_joined_at"] == [32]
        assert receipt["harness_text"][0]["cause"] == "output limit"
        assert "output limit once" in receipt["red_flags"][0]


def test_two_cuts_are_joined_in_order(tmp_path):
    record, private = turn(
        tmp_path,
        [
            ("m1", "One, ", "max_tokens"),
            ("m2", "two, ", "max_tokens"),
            ("m3", "three.", "end_turn"),
        ],
    )
    receipt = harness.receipt(CONFIG, POLICY, record, private)
    assert receipt["answer"] == "One, two, three."
    assert receipt["output_limit"]["responses_at_limit"] == 2
    assert receipt["output_limit"]["answer_joined_at"] == [5, 10]
    assert [h["answer_offset"] for h in receipt["harness_text"]] == [5, 10]
    assert "2 times" in receipt["red_flags"][0]


def test_a_reply_that_ends_at_the_ceiling_says_it_is_cut_off(tmp_path):
    record, private = turn(
        tmp_path,
        [("m1", "The chair moved and then", "max_tokens")],
        result_stop="max_tokens",
    )
    receipt = harness.receipt(CONFIG, POLICY, record, private)
    assert receipt["answer"] == "The chair moved and then"
    assert receipt["output_limit"]["answer_ends_at_limit"] is True
    assert "cut off" in receipt["red_flags"][0]


def test_a_cut_while_thinking_is_a_cut_but_not_a_join(tmp_path):
    record, private = turn(
        tmp_path, [("m1", None, "max_tokens"), ("m2", "Green.", "end_turn")]
    )
    receipt = harness.receipt(CONFIG, POLICY, record, private)
    assert receipt["answer"] == "Green."
    assert receipt["output_limit"]["responses_at_limit"] == 1
    assert receipt["output_limit"]["answer_joined_at"] == []
    assert "character" not in receipt["red_flags"][0]


def test_a_reply_within_the_ceiling_is_the_result_itself(tmp_path):
    record, private = turn(tmp_path, [("m1", "Green.", "end_turn")])
    receipt = harness.receipt(CONFIG, POLICY, record, private)
    assert receipt["answer"] == "Green."
    assert receipt["output_limit"]["responses_at_limit"] == 0
    assert receipt["harness_text"] == []
    assert receipt["red_flags"] == []


def test_a_declared_ceiling_is_stated_as_declared_not_as_the_cli_default(tmp_path):
    # The usage table says 64000 whatever the launch asked for, so with
    # no cut to show otherwise the declaration is all there is to state.
    record, private = turn(tmp_path, [("m1", "Green.", "end_turn")])
    policy = dict(POLICY, output_ceiling=128000)
    receipt = harness.receipt(CONFIG, policy, record, private)
    assert receipt["output_limit"]["ceiling"] == 128000
    assert receipt["output_limit"]["cut_at"] == []
    assert receipt["red_flags"] == []


def test_a_cut_at_the_declared_ceiling_raises_no_ceiling_flag(tmp_path):
    record, private = turn(
        tmp_path,
        [
            ("m1", "Mira left the umbrella by the do", "max_tokens"),
            ("m2", "or.", "end_turn"),
        ],
    )
    policy = dict(POLICY, output_ceiling=64000)
    receipt = harness.receipt(CONFIG, policy, record, private)
    assert not any(f.startswith("Output ceiling") for f in receipt["red_flags"])


def test_a_cut_below_the_declared_ceiling_is_flagged_and_the_answer_kept(tmp_path):
    record, private = turn(
        tmp_path,
        [
            ("m1", "Mira left the umbrella by the do", "max_tokens"),
            ("m2", "or, and it was green.", "end_turn"),
        ],
    )
    policy = dict(POLICY, output_ceiling=128000)
    receipt = harness.receipt(CONFIG, policy, record, private)
    assert receipt["answer"] == "Mira left the umbrella by the door, and it was green."
    assert any(
        "declared 128000, a response was cut at 64000" in flag
        for flag in receipt["red_flags"]
    )


def test_every_procedure_holds_a_cut_to_the_declared_ceiling():
    """Rooms, reviews and experiments read flags here, not in the run receipt."""
    evidence = dict(output_limit=dict(cut_at=[64000]), harness_text=[])
    assert any("declared 128000" in f for f in replies.flags(evidence, 128000))
    assert not any(f.startswith("Output ceiling") for f in replies.flags(evidence))
    assert not any(
        f.startswith("Output ceiling") for f in replies.flags(evidence, 64000)
    )


def test_harness_text_with_no_known_cause_is_flagged_and_kept_out(tmp_path):
    stream = response("m1", "Before. ") + [injected("Something else.")]
    stream += response("m2", "After.")
    stream.append(dict(type="result", result="After."))
    native = [
        dict(type="assistant", message=dict(id="m1", stop_reason="end_turn")),
        dict(type="assistant", message=dict(id="m2", stop_reason="end_turn")),
    ]
    answer, evidence = replies.claude(stream, native)
    assert answer == "After."
    assert evidence["harness_text"] == [
        dict(text="Something else.", cause=None, answer_offset=None)
    ]
    assert "cannot be classified" in replies.flags(evidence)[0]


def test_a_session_that_states_no_stop_reason_evidences_no_limit():
    stream = response("m1", "Green.") + [dict(type="result", result="Green.")]
    answer, evidence = replies.claude(stream, [])
    assert answer == "Green."
    assert evidence["output_limit"] is None
    assert replies.flags(evidence) == []


def banked_record(tmp_path, kept):
    """A one-seat record banked by an executor that kept only the last part."""
    root = tmp_path / "record"
    root.mkdir()
    attempt = turn(
        root,
        [
            ("m1", "Mira left the umbrella by the do", "max_tokens"),
            ("m2", "or, and it was green.", "end_turn"),
        ],
    )[0]
    banked = root / "records/reader/first/attempt-000"
    banked.parent.mkdir(parents=True)
    attempt.rename(banked)
    (banked / "native.jsonl").write_text(
        (root / "private/projects/work/sid.jsonl").read_text()
    )
    (banked / "answers").mkdir()
    (banked / "answers/answer.md").write_text(kept + "\n")
    (root / "state").mkdir()
    (root / "state/reader.json").write_text(
        json.dumps(dict(done={"first": dict(attempt=0)}, pending=None))
    )
    plan = dict(
        schema=1,
        seats=[
            dict(
                id="reader",
                config=dict(harness="claude", model="fixture-model", effort="high"),
                policy=dict(allow_compaction=False),
            )
        ],
    )
    (root / "plan.json").write_text(json.dumps(plan))
    (root / "plan-digest.json").write_text(
        json.dumps(dict(sha256=digest(root / "plan.json")))
    )
    return root


def test_observe_finds_an_answer_banked_without_its_first_part(tmp_path):
    root = banked_record(tmp_path, "or, and it was green.")
    seat = observe.observe(root)["seats"]["reader"]
    finding = seat["answers"]["first"]
    assert finding["banked_answer"] == "part of the reply"
    assert finding["banked_characters"] == 21
    assert finding["reply_characters"] == 53
    assert any("holds 21 of the reply's 53" in f for f in seat["red_flags"])


def test_observe_passes_an_answer_banked_whole(tmp_path):
    root = banked_record(
        tmp_path, "Mira left the umbrella by the door, and it was green."
    )
    seat = observe.observe(root)["seats"]["reader"]
    assert seat["answers"]["first"]["banked_answer"] == "whole reply"
    # The ceiling was still reached, and a whole answer still says so.
    assert any("output limit once" in f for f in seat["red_flags"])
