"""A procedure told as it runs: each harness's stream, in plain lines."""

import io
import json
import time

from quirework.runs import feed
from quirework.runs.storage import write


def append(path, items):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as out:
        for item in items:
            out.write((item if isinstance(item, str) else json.dumps(item)) + "\n")


def told(lines):
    return "\n".join(lines)


def follower(tmp_path, harness, show=feed.FULL, private=None):
    work = tmp_path / "seat"
    work.mkdir(exist_ok=True)
    turn = feed.Turn(
        feed.Page(100, who=8),
        "Ana Pell",
        harness,
        tmp_path / "events.jsonl",
        places=[(work, "", work)],
        private=private,
        show=show,
    )
    return work, turn


def claude_tool(name, **given):
    content = [{"type": "tool_use", "name": name, "input": given}]
    return {"type": "assistant", "message": {"content": content}}


def claude_says(text, shape="text"):
    return {"type": "assistant", "message": {"content": [{"type": shape, shape: text}]}}


# -- Reading a file that is still being written --------------------------------


def test_a_row_the_writer_is_halfway_through_waits_for_the_next_poll(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"a": 1}\n{"b"')
    tail = feed.Tail(path)
    assert tail.rows() == [{"a": 1}]
    with path.open("a") as out:
        out.write(": 2}\n")
    assert tail.rows() == [{"b": 2}]
    assert tail.rows() == []


def test_a_stream_replaced_under_its_name_is_read_from_its_start(tmp_path):
    """A continuation archives the stopped attempt and starts a new stream."""
    path = tmp_path / "events.jsonl"
    path.write_text('{"a": 1}\n{"b": 2}\n')
    tail = feed.Tail(path)
    tail.rows()
    fresh = tmp_path / "fresh.jsonl"
    fresh.write_text('{"c": 3}\n')
    fresh.replace(path)
    assert tail.rows() == [{"c": 3}]


def test_following_from_the_end_tells_only_what_comes_after(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"a": 1}\n')
    tail = feed.Tail(path, at_end=True)
    append(path, [{"b": 2}])
    assert tail.rows() == [{"b": 2}]


def test_a_file_not_yet_written_is_waited_for(tmp_path):
    tail = feed.Tail(tmp_path / "later.jsonl")
    assert tail.rows() == []
    append(tmp_path / "later.jsonl", [{"a": 1}])
    assert tail.rows() == [{"a": 1}]


def test_an_unreadable_line_is_said_not_skipped(tmp_path):
    _, turn = follower(tmp_path, "claude")
    append(tmp_path / "events.jsonl", ["not a row"])
    assert "unreadable line" in told(turn.poll(None))


# -- What each harness's stream shows -------------------------------------------


def test_a_claude_turn_reads_as_what_the_seat_did(tmp_path):
    work, turn = follower(tmp_path, "claude")
    (work / "outbox").mkdir()
    (work / "outbox" / "post.md").write_text("x" * 1500)
    append(
        tmp_path / "events.jsonl",
        [
            {"type": "system", "subtype": "init"},
            {"type": "system", "subtype": "thinking_tokens"},
            claude_tool(
                "Read", file_path=str(work / "board" / "round-001" / "digest.md")
            ),
            claude_tool("Grep", pattern="kettle", path=str(work / "input")),
            claude_tool(
                "Write", file_path=str(work / "outbox" / "post.md"), content="x"
            ),
            claude_says("Posted my answer about the cat."),
            {"type": "result", "subtype": "success", "is_error": False},
        ],
    )
    text = told(turn.poll(None))
    assert "read     board/round-001/digest.md" in text
    assert "searched kettle in input" in text
    assert "wrote    outbox/post.md (1.5 kB)" in text
    assert "said     Posted my answer about the cat." in text
    assert str(work) not in text


def test_a_tool_that_failed_and_a_turn_that_failed_are_both_said(tmp_path):
    _, turn = follower(tmp_path, "claude")
    result = {
        "type": "tool_result",
        "is_error": True,
        "content": "File does not exist.",
    }
    append(
        tmp_path / "events.jsonl",
        [
            {"type": "user", "message": {"content": [result]}},
            {"type": "result", "is_error": True, "result": "You've hit your limit"},
        ],
    )
    text = told(turn.poll(None))
    assert "error    File does not exist." in text
    assert "error    You've hit your limit" in text


def test_reasoning_is_shown_only_when_asked(tmp_path):
    append(
        tmp_path / "events.jsonl", [claude_says("Weighing the two names.", "thinking")]
    )
    _, quiet = follower(tmp_path, "claude")
    assert told(quiet.poll(None)) == ""
    _, loud = follower(tmp_path, "claude", show=feed.Show(thinking=True))
    assert "thought  Weighing the two names." in told(loud.poll(None))


def test_a_brief_feed_keeps_the_changes_and_leaves_the_rest(tmp_path):
    """A running command's own log is read by the assistant that started it."""
    work, turn = follower(tmp_path, "claude", show=feed.BRIEF)
    append(
        tmp_path / "events.jsonl",
        [
            claude_tool("Read", file_path=str(work / "input" / "brief.md")),
            claude_tool("Bash", command="ls input"),
            claude_tool("Edit", file_path=str(work / "outbox" / "post.md")),
            claude_says("Done."),
        ],
    )
    text = told(turn.poll(None))
    assert "edited   outbox/post.md" in text
    assert "brief.md" not in text and "ls input" not in text and "Done." not in text


def test_a_codex_turn_reads_as_what_the_seat_did(tmp_path):
    work, turn = follower(tmp_path, "codex")

    def done(item):
        return {"type": "item.completed", "item": item}

    command = '/bin/bash -lc "rg -n \\"cat\\" input && echo \\$HOME"'
    append(
        tmp_path / "events.jsonl",
        [
            {"type": "thread.started", "thread_id": "t-1"},
            {
                "type": "item.started",
                "item": {"type": "command_execution", "command": "x"},
            },
            done({"type": "command_execution", "command": command, "exit_code": 0}),
            done({"type": "command_execution", "command": "false", "exit_code": 1}),
            done(
                {
                    "type": "file_change",
                    "changes": [
                        {"path": str(work / "outbox" / "post.md"), "kind": "add"},
                        {"path": str(work / "notes.md"), "kind": "update"},
                        {"path": str(work / "old.md"), "kind": "delete"},
                    ],
                }
            ),
            done({"type": "agent_message", "text": "The cat should be called Tern."}),
        ],
    )
    text = told(turn.poll(None))
    assert text.count("ran ") == 2
    assert 'ran      rg -n "cat" input && echo $HOME' in text
    assert "ran      false  (exit 1)" in text
    assert "wrote    outbox/post.md" in text
    assert "edited   notes.md" in text
    assert "deleted  old.md" in text
    assert "said     The cat should be called Tern." in text


def test_codex_says_a_failed_turn_once(tmp_path):
    """It reports the failure as an error row and again as turn.failed."""
    _, turn = follower(tmp_path, "codex")
    append(
        tmp_path / "events.jsonl",
        [
            {
                "type": "item.completed",
                "item": {"type": "error", "message": "a setting"},
            },
            {"type": "error", "message": "usage limit"},
            {"type": "turn.failed", "error": {"message": "usage limit"}},
        ],
    )
    text = told(turn.poll(None))
    assert "notice   a setting" in text
    assert text.count("usage limit") == 1


def agy_step(index, kind, state="DONE", **extra):
    step = {"step_index": index, "step_type": kind, "state": state, **extra}
    return {"event": "step_update", "step_update": step}


def agy_tool(index, name, state="DONE", **parameters):
    info = {"name": name, "parameters": parameters}
    return agy_step(index, "tool", state, tool_name=name, tool_info=info)


def transcript(private, conversation, steps):
    path = (
        private
        / "antigravity-cli"
        / "brain"
        / conversation
        / ".system_generated"
        / "logs"
        / "transcript_full.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in steps))


def planner(index, content):
    return {
        "step_index": index,
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "content": content,
    }


def test_an_agy_turn_has_its_words_from_the_transcript_in_order(tmp_path):
    """agy's stream says a response finished, never what it said."""
    private = tmp_path / "private"
    work, turn = follower(tmp_path, "agy", private=private)
    transcript(private, "c-1", [planner(4, "I will read the brief first.")])
    append(
        tmp_path / "events.jsonl",
        [
            {"event": "init", "conversation_id": "c-1"},
            agy_step(4, "agent_response"),
            agy_tool(5, "view_file", AbsolutePath=str(work / "input" / "brief.md")),
            agy_step(6, "agent_response"),
            agy_tool(7, "write_to_file", TargetFile=str(work / "outbox" / "post.md")),
        ],
    )
    text = told(turn.poll(None))
    # Step 6's words are not in the transcript yet, so what follows them waits.
    assert "said     I will read the brief first." in text
    assert "read     input/brief.md" in text
    assert "outbox/post.md" not in text
    transcript(
        private,
        "c-1",
        [planner(4, "I will read the brief first."), planner(6, "Now the post.")],
    )
    later = told(turn.poll(None))
    assert later.index("Now the post.") < later.index("wrote    outbox/post.md")


def test_an_agy_step_the_transcript_passed_without_words_holds_nothing_up(tmp_path):
    private = tmp_path / "private"
    work, turn = follower(tmp_path, "agy", private=private)
    transcript(private, "c-1", [{"step_index": 9, "type": "GENERIC", "status": "DONE"}])
    append(
        tmp_path / "events.jsonl",
        [
            {"event": "init", "conversation_id": "c-1"},
            agy_step(4, "agent_response"),
            agy_tool(5, "write_to_file", TargetFile=str(work / "outbox" / "post.md")),
        ],
    )
    assert "wrote    outbox/post.md" in told(turn.poll(None))


def test_an_agy_transcript_that_is_not_there_is_said_once(tmp_path):
    _, turn = follower(tmp_path, "agy", private=tmp_path / "nowhere")
    append(
        tmp_path / "events.jsonl",
        [
            {"event": "init", "conversation_id": "c-1"},
            agy_step(4, "agent_response"),
            agy_tool(5, "run_command", CommandLine="ls input"),
            agy_step(6, "agent_response"),
            agy_tool(7, "search_web", "ERROR", query="lighthouse cats"),
        ],
    )
    text = told(turn.poll(None))
    assert text.count("not in its transcript") == 1
    assert "ran      ls input" in text
    assert "error    search_web: failed" in text


def test_agy_newer_file_tools_are_told_by_the_file(tmp_path):
    """agy 1.4.3 records its file tools as `read_file` and `write_file`."""
    work, turn = follower(tmp_path, "agy")
    (work / "outbox").mkdir()
    (work / "outbox" / "post.md").write_text("Mira's umbrella stayed.\n")
    append(
        tmp_path / "events.jsonl",
        [
            agy_tool(5, "read_file", Path=str(work / "input" / "brief.md")),
            agy_tool(7, "write_file", Path=str(work / "outbox" / "post.md")),
            agy_tool(9, "sketch_file", Path="/srv/atlas/map.png"),
            agy_tool(11, "run_command"),
        ],
    )
    text = told(turn.poll(None))
    assert "read     input/brief.md" in text
    assert "wrote    outbox/post.md (24 B)" in text
    # A tool the feed does not know still says which file it named.
    assert "used     sketch_file /srv/atlas/map.png" in text
    assert "used     run_command" in text


def test_a_harness_without_a_reader_is_said_once(tmp_path):
    _, turn = follower(tmp_path, "invented-harness")
    append(tmp_path / "events.jsonl", [{"a": 1}])
    assert "cannot be followed yet" in told(turn.poll(None))
    assert turn.poll(None) == []


# -- A document still being written ----------------------------------------------


def test_a_growing_report_is_told_by_what_each_change_added(tmp_path):
    path = tmp_path / "report.md"
    report = feed.Growing(path)
    assert report.poll() == []
    path.write_text("# Findings\n\nSTATUS: partial\n")
    assert report.poll() == ["# Findings", "", "STATUS: partial"]
    assert report.poll() == []
    path.write_text(
        "# Findings\n\n## F1 The cat\n\nSTATUS: partial\n\n## F2 The kettle\n"
    )
    added = report.poll()
    assert "## F1 The cat" in added and "## F2 The kettle" in added
    assert "# Findings" not in added
    assert "⋯" in added  # two separate places changed


def test_a_report_followed_from_now_tells_only_the_next_change(tmp_path):
    path = tmp_path / "report.md"
    path.write_text("# Findings\n")
    report = feed.Growing(path, at_end=True)
    path.write_text("# Findings\n\n## F1 The cat\n")
    assert report.poll() == ["", "## F1 The cat"]


# -- The page ------------------------------------------------------------------


def test_a_post_is_framed_whole_and_wrapped_to_the_width():
    page = feed.Page(60)
    lines = page.block("Ana Pell, round 2", "Short line.\n\n" + "word " * 40)
    assert lines[0].endswith("Ana Pell, round 2")
    assert lines[-1].strip() == "└─"
    assert all(len(line) <= 60 for line in lines)
    assert sum("word" in line for line in lines) > 1


def test_a_long_command_is_one_line():
    page = feed.Page(60, who=4)
    lines = page.command(None, "ana", "ran", "cat <<'EOF'\n" + "x\n" * 300 + "EOF")
    assert len(lines) == 1 and lines[0].endswith("…")


def test_a_long_seat_label_gives_way_to_the_ids():
    assert feed.short_names({"ana": "Ana Pell"}) == {"ana": "Ana Pell"}
    labels = {"ana": "Ana Pell", "bo": "Keeper of the lighthouse tide tables"}
    assert feed.short_names(labels) == {"ana": "ana", "bo": "bo"}


# -- Narration on a running command ---------------------------------------------


class Script:
    def __init__(self, *batches):
        self.batches = list(batches)

    def poll(self):
        return self.batches.pop(0) if self.batches else []


def test_narration_tells_what_arrived_after_its_last_poll():
    """The block's own last steps land after the thread's last look."""
    out = io.StringIO()
    with feed.narrating(Script(["round 1 closed"]), poll=3600, out=out):
        pass
    assert out.getvalue() == "round 1 closed\n"


def test_a_feed_that_fails_stops_the_narration_and_not_the_run():
    class Broken:
        def poll(self):
            raise OSError("record moved")

    out = io.StringIO()
    with feed.narrating(Broken(), poll=0.01, out=out):
        time.sleep(0.1)
    assert out.getvalue().count("stopped following (OSError: record moved)") == 1


# -- The run engine's record ------------------------------------------------------


def engine_record(tmp_path):
    root = tmp_path / "run"
    write(
        root / "plan.json",
        {
            "seats": [
                {"id": "reader", "config": {"harness": "claude", "model": "haiku"}}
            ]
        },
    )
    return root


def submit(root, turn, created_at):
    write(
        root / "state" / "reader.json",
        {"done": {}, "pending": {"turn": turn, "created_at": created_at}},
    )
    record = root / "records" / "reader" / turn / "attempt-000"
    write(record / "launch.json", {})
    return record


def bank(root, turn, answer, *, complete=False):
    record = root / "records" / "reader" / turn / "attempt-000"
    (record / "answers").mkdir(parents=True, exist_ok=True)
    (record / "answers" / "answer.md").write_text(answer)
    write(
        root / "state" / "reader.json",
        {"done": {turn: {"attempt": 0}}, "pending": None, "complete": complete},
    )


def test_a_run_is_told_turn_by_turn_with_each_answer(tmp_path):
    root = engine_record(tmp_path)
    following = feed.RunFeed(root, width=100)
    assert following.poll() == []
    record = submit(root, "opening", time.time())
    work = root / "work" / "reader" / "seat"
    append(
        record / "events.jsonl", [claude_tool("Read", file_path=str(work / "note.md"))]
    )
    text = told(following.poll())
    assert "reader [claude/haiku] starts turn opening" in text
    assert "read     note.md" in text
    bank(root, "opening", "The note is clear about the cat.\n", complete=True)
    text = told(following.poll())
    assert "reader answered opening in" in text
    assert "The note is clear about the cat." in text
    assert "reader has taken all its turns" in text


def test_a_run_names_a_file_where_it_is_not_where_the_seat_saw_it(tmp_path):
    """A seat works in a jail that mounts its workspace at /tmp/work."""
    root = engine_record(tmp_path)
    following = feed.RunFeed(root, width=100)
    record = submit(root, "opening", time.time())
    drafts = root / "work" / "reader" / "seat" / "drafts"
    drafts.mkdir(parents=True)
    (drafts / "notes.md").write_text("The cat stayed in.\n")
    append(
        record / "events.jsonl",
        [claude_tool("Write", file_path="/tmp/work/drafts/notes.md")],
    )
    assert "wrote    drafts/notes.md (19 B)" in told(following.poll())


def test_a_run_read_back_tells_each_banked_turn_in_order(tmp_path):
    root = engine_record(tmp_path)
    record = submit(root, "opening", 1.0)
    append(record / "events.jsonl", [claude_says("Reading it now.")])
    bank(root, "opening", "It is clear.\n")
    text = told(feed.RunFeed(root, width=100).poll())
    assert text.index("starts turn opening") < text.index("Reading it now.")
    assert text.index("Reading it now.") < text.index("answered opening")


def test_a_run_followed_from_now_tells_nothing_from_before(tmp_path):
    root = engine_record(tmp_path)
    submit(root, "opening", 1.0)
    bank(root, "opening", "It is clear.\n")
    assert feed.RunFeed(root, history="none").poll() == []


def test_a_turn_that_leaves_without_an_answer_is_said(tmp_path):
    root = engine_record(tmp_path)
    following = feed.RunFeed(root, width=100)
    record = submit(root, "opening", time.time())
    following.poll()
    write(record / "retirement.json", {"reason": "proved unsubmitted"})
    write(root / "state" / "reader.json", {"done": {}, "pending": None})
    assert "stopped without an answer: proved unsubmitted" in told(following.poll())
