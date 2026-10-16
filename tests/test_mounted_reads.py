"""A mounted read: the record names the bytes it pinned and proves the reading.

A seat that walks a directory with its own tools is handed nothing the record
froze, so two things have to be checked from outside it. The mounted bytes
must still be the ones the plan pinned, and the native session must show that
each file the seat was to read was read in full and in order.
"""

import hashlib
import json
from pathlib import Path

import pytest

from quirework.providers import harness, images, reads
from quirework.runs import engine
from quirework.runs.storage import tree_digest


@pytest.fixture
def mount(tmp_path):
    root = tmp_path / ".read"
    (root / "chapters").mkdir(parents=True)
    (root / "chapters" / "01-opening.md").write_text(
        "Mira left a green umbrella.\nThe door stayed open.\nNobody came back.\n"
    )
    (root / "chapters" / "02-later.md").write_text(
        "The chair moved.\nThen it rained.\n"
    )
    return root


def chapter(mount, n):
    return str(sorted((mount / "chapters").glob("*.md"))[n])


def plan_file(tmp_path, mount, **policy):
    plan = dict(
        title="Fictitious mounted read",
        seats=[
            dict(
                id="reader",
                harness="codex",
                model="fixture-model",
                effort="high",
                policy=dict(tools="write", read_roots=[str(mount)], **policy),
                turns=[
                    dict(
                        id="wholebook",
                        prompt="Read the chapters in order.",
                        outputs=["drafts/notes.md"],
                        referenced_files=[chapter(mount, 0), chapter(mount, 1)],
                    )
                ],
            )
        ],
    )
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    return path


def claude_session(tmp_path, calls):
    rows = [
        {"type": "assistant", "message": {"content": [dict(type="tool_use", **c)]}}
        for c in calls
    ]
    path = tmp_path / "claude.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def codex_session(tmp_path, payloads):
    rows = [{"type": "response_item", "payload": p} for p in payloads]
    path = tmp_path / "codex.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_claude_reads_in_order_and_in_full_are_proven(tmp_path, mount):
    session = claude_session(
        tmp_path,
        [
            dict(name="Read", input={"file_path": chapter(mount, 0)}),
            dict(name="Read", input={"file_path": chapter(mount, 1)}),
        ],
    )
    got = reads.coverage(
        reads.file_reads(session), [chapter(mount, 0), chapter(mount, 1)]
    )
    assert got == (2, True, [])


def test_a_partial_read_is_not_proven_full(tmp_path, mount):
    """Reading two of three lines is reading most of a chapter, which is not
    the same as reading it, and the difference is what a skimmer looks like."""
    session = claude_session(
        tmp_path,
        [
            dict(name="Read", input={"file_path": chapter(mount, 0), "limit": 2}),
            dict(name="Read", input={"file_path": chapter(mount, 1)}),
        ],
    )
    fully, in_order, unproven = reads.coverage(
        reads.file_reads(session), [chapter(mount, 0), chapter(mount, 1)]
    )
    assert (fully, in_order, unproven) == (1, True, ["01-opening.md"])


def test_codex_line_ranges_add_up_to_a_whole_chapter(tmp_path, mount):
    """A seat may print a chapter in pieces. Only the union of the pieces
    shows whether it printed all of it."""
    first = chapter(mount, 0)
    session = codex_session(
        tmp_path,
        [
            dict(
                type="function_call",
                arguments=json.dumps({"cmd": f"sed -n '1,2p' {first}"}),
            ),
            dict(
                type="custom_tool_call",
                input="const r = await tools.exec_command({cmd: \"sed -n '3,40p' "
                + first
                + '"});',
            ),
            dict(
                type="local_shell_call",
                action={"command": ["cat", chapter(mount, 1)]},
            ),
        ],
    )
    assert reads.coverage(reads.file_reads(session), [first, chapter(mount, 1)]) == (
        2,
        True,
        [],
    )


def test_a_command_it_cannot_parse_is_unproven_not_assumed(tmp_path, mount):
    """A read assembled at run time leaves no path to parse. The check must
    say it is unproven rather than count it or call it skipped."""
    session = codex_session(
        tmp_path,
        [
            dict(
                type="custom_tool_call",
                input="for (const f of files) { await tools.exec_command({cmd: `cat ${f}`}); }",
            )
        ],
    )
    fully, in_order, unproven = reads.coverage(
        reads.file_reads(session), [chapter(mount, 0)]
    )
    assert (fully, in_order, unproven) == (0, None, ["01-opening.md"])


def test_opening_a_later_chapter_first_is_out_of_order(tmp_path, mount):
    session = claude_session(
        tmp_path,
        [
            dict(name="Read", input={"file_path": chapter(mount, 1)}),
            dict(name="Read", input={"file_path": chapter(mount, 0)}),
        ],
    )
    _, in_order, _ = reads.coverage(
        reads.file_reads(session), [chapter(mount, 0), chapter(mount, 1)]
    )
    assert in_order is False


def agy_session(tmp_path, steps):
    """An agy transcript where it keeps one: in a session directory under
    `brain/`, beside the copies of any image the session was shown."""
    logs = tmp_path / "brain" / "fixture-session" / ".system_generated" / "logs"
    logs.mkdir(parents=True)
    rows = [dict(step_index=i, source="MODEL", **s) for i, s in enumerate(steps)]
    path = logs / "transcript_full.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def agy_call(name, **args):
    return dict(
        type="PLANNER_RESPONSE", status="DONE", tool_calls=[dict(name=name, args=args)]
    )


def agy_viewed(path, shown, status="DONE"):
    """The result agy records for a view: the file, and the lines it showed."""
    total = len(Path(path).read_text().splitlines())
    return dict(
        type="GENERIC",
        status=status,
        content=f"Created At: now\nFile Path: `file://{path}`\nTotal Lines: {total}"
        f"\nTotal Bytes: 80\nShowing lines 1 to {shown}\n1: Mira left",
    )


def agy_created(path):
    return dict(
        type="GENERIC",
        status="DONE",
        content=f"Created At: now\nCreated file file://{path} with requested content.",
    )


def test_agy_reads_and_reactions_are_proven_from_its_results(tmp_path, mount):
    first, second = chapter(mount, 0), chapter(mount, 1)
    session = agy_session(
        tmp_path,
        [
            agy_call("view_file", AbsolutePath=first),
            agy_viewed(first, 3),
            agy_call("write_to_file", TargetFile="/tmp/work/" + draft(mount, 0)),
            agy_created("/tmp/work/" + draft(mount, 0)),
            agy_call("view_file", AbsolutePath=second),
            agy_viewed(second, 2),
            agy_call("write_to_file", TargetFile="/tmp/work/" + draft(mount, 1)),
            agy_created("/tmp/work/" + draft(mount, 1)),
        ],
    )
    referenced = [first, second]
    evs = reads.events(session)
    assert reads.coverage(reads.file_reads(session), referenced) == (2, True, [])
    pairs = reads.reaction_pairs(referenced, [draft(mount, 0), draft(mount, 1)])
    assert reads.reactions_before_next(evs, referenced, pairs) is True


def test_agy_counts_the_lines_it_showed_not_the_ones_asked_for(tmp_path, mount):
    """A view can show less than the call asked for, so the result decides."""
    first = chapter(mount, 0)
    session = agy_session(
        tmp_path,
        [
            agy_call("view_file", AbsolutePath=first, StartLine=1, EndLine=3),
            agy_viewed(first, 2),
        ],
    )
    assert reads.coverage(reads.file_reads(session), [first]) == (
        0,
        True,
        ["01-opening.md"],
    )


def test_an_agy_call_without_a_completed_result_counts_for_nothing(tmp_path, mount):
    first = chapter(mount, 0)
    session = agy_session(
        tmp_path,
        [
            agy_call("view_file", AbsolutePath=first),
            agy_viewed(first, 3, status="INVALID"),
            agy_call("write_to_file", TargetFile="/tmp/work/" + draft(mount, 0)),
        ],
    )
    assert reads.events(session) == []


def test_an_agy_shell_command_is_read_like_any_other(tmp_path, mount):
    first = chapter(mount, 0)
    session = agy_session(
        tmp_path, [agy_call("run_command", CommandLine=f"cat {first}", Cwd="/tmp")]
    )
    assert reads.file_reads(session) == [(first, None)]


def test_agy_images_are_hashed_from_the_copies_beside_its_transcript(tmp_path):
    session = agy_session(tmp_path, [])
    stored = session.parents[2] / ".tempmediaStorage"
    stored.mkdir()
    (stored / "media_1.png").write_bytes(b"a green umbrella")
    inside = "/home/reader/.gemini/antigravity-cli/brain/fixture-session"
    rows = [
        dict(type="USER_INPUT", status="DONE", content="Read it."),
        dict(
            type="GENERIC",
            status="DONE",
            media=[
                dict(
                    mime_type="image/png", uri=f"{inside}/.tempmediaStorage/media_1.png"
                )
            ],
        ),
        dict(
            type="GENERIC",
            status="DONE",
            media=[
                dict(mime_type="image/png", uri=f"{inside}/.tempmediaStorage/gone.png")
            ],
        ),
        dict(
            type="GENERIC",
            status="DONE",
            media=[dict(mime_type="image/png", uri=f"{inside}/../../escape.png")],
        ),
    ]
    session.write_text(
        "".join(json.dumps(dict(step_index=i, **r)) + "\n" for i, r in enumerate(rows))
    )
    (tmp_path / "escape.png").write_bytes(b"outside the session")
    # The missing copy and the one outside the session are not counted.
    assert images.history_image_hashes(session, tmp_path) == [
        hashlib.sha256(b"a green umbrella").hexdigest()
    ]


def test_a_digest_for_a_path_outside_the_mount_is_refused(tmp_path, mount):
    other = tmp_path / "elsewhere"
    other.mkdir()
    path = plan_file(tmp_path, mount, read_root_digests={str(other): "0" * 64})
    with pytest.raises(ValueError, match="outside read_roots"):
        engine.prepare(path, tmp_path / "record", resolve=False)


def test_a_referenced_file_outside_the_mount_is_refused(tmp_path, mount):
    stray = tmp_path / "stray.md"
    stray.write_text("Not part of the book.\n")
    path = plan_file(tmp_path, mount)
    plan = json.loads(path.read_text())
    plan["seats"][0]["turns"][0]["referenced_files"] = [str(stray)]
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="referenced_files"):
        engine.prepare(path, tmp_path / "record", resolve=False)


def launch(run, monkeypatch):
    monkeypatch.setattr(harness, "command", lambda *a: (["fixture"], b"", "prompt"))
    monkeypatch.setattr(
        harness, "launch_context", lambda *a: ([], {}, run.private("reader"))
    )

    def spawned(*a, **kw):
        raise OSError("reached the spawn")

    monkeypatch.setattr(engine.subprocess, "Popen", spawned)
    run.turn("reader", 0, 1)


def test_a_mount_that_changed_after_planning_is_never_submitted(
    tmp_path, mount, monkeypatch, canaries_pass
):
    path = plan_file(
        tmp_path, mount, read_root_digests={str(mount): tree_digest(mount)}
    )
    engine.prepare(path, tmp_path / "record", resolve=False)
    run = engine.Run(tmp_path / "record")
    (mount / "chapters" / "02-later.md").write_text("A different ending.\n")
    with pytest.raises(RuntimeError, match="mounted material changed"):
        launch(run, monkeypatch)
    assert run.state("reader")["pending"] is None
    # The free check refuses first: a turn that cannot launch costs no canary.
    assert canaries_pass == []


def test_an_unchanged_mount_goes_on_to_launch(
    tmp_path, mount, monkeypatch, canaries_pass
):
    path = plan_file(
        tmp_path, mount, read_root_digests={str(mount): tree_digest(mount)}
    )
    engine.prepare(path, tmp_path / "record", resolve=False)
    run = engine.Run(tmp_path / "record")
    with pytest.raises(OSError, match="reached the spawn"):
        launch(run, monkeypatch)


def test_the_receipt_says_what_was_read_and_flags_what_was_not(
    tmp_path, mount, monkeypatch
):
    path = plan_file(
        tmp_path, mount, read_root_digests={str(mount): tree_digest(mount)}
    )
    engine.prepare(path, tmp_path / "record", resolve=False)
    run = engine.Run(tmp_path / "record")
    state = run.state("reader")
    state["pending"] = dict(turn="wholebook", mode="start", parent_id="sid")
    run.save("reader", state)
    run.record("reader", "wholebook").mkdir(parents=True)
    (run.workspace("reader") / "drafts").mkdir(parents=True, exist_ok=True)
    (run.workspace("reader") / "drafts" / "notes.md").write_text("A reaction.\n")
    session = claude_session(
        tmp_path, [dict(name="Read", input={"file_path": chapter(mount, 0)})]
    )
    monkeypatch.setattr(
        harness,
        "receipt",
        lambda *a: dict(
            session_id="sid",
            model="fixture-model",
            rollout=str(session),
            answer="Done.",
            forked_from=None,
        ),
    )
    monkeypatch.setattr(harness, "history_image_hashes", lambda *a: [])
    run.collect_turn("reader", 0)
    receipt = run.state("reader")["done"]["wholebook"]["receipt"]
    assert receipt["referenced_files_read"] == "1/2"
    # The second file was never seen opening, which is a gap, not a disorder.
    assert receipt["referenced_files_in_order"] is None
    assert receipt["read_root_digests_held"] is True
    assert any("Not proven read in full" in f for f in receipt["red_flags"])
    assert any("Not proven that referenced files" in f for f in receipt["red_flags"])
    assert not any("not first opened" in f for f in receipt["red_flags"])


def test_an_agy_receipt_counts_the_figures_its_seat_opened(
    tmp_path, mount, monkeypatch
):
    path = plan_file(tmp_path, mount, filesystem="audited")
    plan = json.loads(path.read_text())
    plan["seats"][0]["harness"] = "agy"
    plan["seats"][0]["turns"][0]["referenced_images"] = 2
    path.write_text(json.dumps(plan))
    engine.prepare(path, tmp_path / "record", resolve=False)
    run = engine.Run(tmp_path / "record")
    state = run.state("reader")
    state["pending"] = dict(turn="wholebook", mode="start", parent_id="sid")
    run.save("reader", state)
    run.record("reader", "wholebook").mkdir(parents=True)
    (run.workspace("reader") / "drafts").mkdir(parents=True, exist_ok=True)
    (run.workspace("reader") / "drafts" / "notes.md").write_text("A reaction.\n")
    session = agy_session(tmp_path, [])
    monkeypatch.setattr(
        harness,
        "receipt",
        lambda *a: dict(
            session_id="sid",
            model="fixture-model",
            rollout=str(session),
            answer="Done.",
            forked_from=None,
        ),
    )
    monkeypatch.setattr(harness, "history_image_hashes", lambda *a: ["a" * 64])
    run.collect_turn("reader", 0)
    receipt = run.state("reader")["done"]["wholebook"]["receipt"]
    assert receipt["referenced_images_opened"] == "1/2"
    assert any("Opened fewer images" in f for f in receipt["red_flags"])


def draft(mount, n):
    return "drafts/" + chapter(mount, n).rsplit("/", 1)[-1]


def test_reacting_before_opening_the_next_chapter_is_proven(tmp_path, mount):
    """Reading in order is not reading blind. What keeps a reaction from
    drawing on later material is that it exists before the next file opens."""
    referenced = [chapter(mount, 0), chapter(mount, 1)]
    pairs = reads.reaction_pairs(referenced, [draft(mount, 0), draft(mount, 1)])
    assert pairs == [draft(mount, 0), draft(mount, 1)]
    blind = claude_session(
        tmp_path,
        [
            dict(name="Read", input={"file_path": chapter(mount, 0)}),
            dict(name="Write", input={"file_path": "/tmp/work/" + draft(mount, 0)}),
            dict(name="Read", input={"file_path": chapter(mount, 1)}),
            dict(name="Write", input={"file_path": "/tmp/work/" + draft(mount, 1)}),
        ],
    )
    assert reads.reactions_before_next(reads.events(blind), referenced, pairs)
    (tmp_path / "ahead").mkdir()
    ahead = claude_session(
        tmp_path / "ahead",
        [
            dict(name="Read", input={"file_path": chapter(mount, 0)}),
            dict(name="Read", input={"file_path": chapter(mount, 1)}),
            dict(name="Write", input={"file_path": "/tmp/work/" + draft(mount, 0)}),
            dict(name="Write", input={"file_path": "/tmp/work/" + draft(mount, 1)}),
        ],
    )
    events = reads.events(ahead)
    assert reads.coverage([(p, r) for k, p, r in events if k == "read"], referenced)[1]
    assert reads.reactions_before_next(events, referenced, pairs) is False


def test_one_codex_snippet_is_ordered_by_what_it_awaits_first(tmp_path, mount):
    """A snippet that writes a reaction and then opens the next chapter is
    reading blind; one that opens two chapters before writing is not."""
    referenced = [chapter(mount, 0), chapter(mount, 1)]
    pairs = reads.reaction_pairs(referenced, [draft(mount, 0), draft(mount, 1)])

    def patch(n):
        return (
            'await tools.apply_patch("*** Begin Patch\\n*** Add File: /tmp/work/'
            + draft(mount, n)
            + '\\n+A reaction.\\n*** End Patch");'
        )

    def cat(n):
        return f'await tools.exec_command({{cmd: "cat {chapter(mount, n)}"}});'

    blind = codex_session(
        tmp_path,
        [
            dict(type="custom_tool_call", input=cat(0)),
            dict(type="custom_tool_call", input=patch(0) + "\n" + cat(1)),
            dict(type="custom_tool_call", input=patch(1)),
        ],
    )
    assert reads.reactions_before_next(reads.events(blind), referenced, pairs)
    (tmp_path / "ahead").mkdir()
    ahead = codex_session(
        tmp_path / "ahead",
        [
            dict(type="custom_tool_call", input=cat(0) + "\n" + cat(1)),
            dict(type="custom_tool_call", input=patch(0) + "\n" + patch(1)),
        ],
    )
    assert reads.reactions_before_next(reads.events(ahead), referenced, pairs) is False


def test_a_heredoc_writes_its_target_and_its_body_is_not_read(tmp_path, mount):
    """`cat > file <<EOF` names a file it writes, not one it prints, and the
    body is the reaction's text: a line in it that looks like a command
    must not be counted as opening a chapter."""
    later = chapter(mount, 1)
    command = (
        f"cat > /tmp/work/{draft(mount, 0)} <<'EOF'\ncat {later}\nEOF\ncat {later}"
    )
    session = codex_session(
        tmp_path,
        [
            dict(
                type="local_shell_call", action={"command": ["cat", chapter(mount, 0)]}
            ),
            dict(type="function_call", arguments=json.dumps({"cmd": command})),
        ],
    )
    events = reads.events(session)
    assert events == [
        ("read", chapter(mount, 0), None),
        ("write", f"/tmp/work/{draft(mount, 0)}", None),
        ("read", later, None),
    ]


def test_a_reaction_the_record_cannot_parse_is_unproven(tmp_path, mount):
    referenced = [chapter(mount, 0), chapter(mount, 1)]
    pairs = reads.reaction_pairs(referenced, [draft(mount, 0), draft(mount, 1)])
    session = codex_session(
        tmp_path,
        [
            dict(
                type="local_shell_call", action={"command": ["cat", chapter(mount, 0)]}
            ),
            dict(type="custom_tool_call", input="await tools.write(name, text);"),
            dict(
                type="local_shell_call", action={"command": ["cat", chapter(mount, 1)]}
            ),
        ],
    )
    assert reads.reactions_before_next(reads.events(session), referenced, pairs) is None
    # A turn whose single output is not a per-file reaction has nothing to pair.
    assert reads.reaction_pairs(referenced, ["drafts/notes.md"]) is None


def test_the_receipt_flags_a_seat_that_read_ahead(tmp_path, mount, monkeypatch):
    path = plan_file(
        tmp_path, mount, read_root_digests={str(mount): tree_digest(mount)}
    )
    plan = json.loads(path.read_text())
    plan["seats"][0]["turns"][0]["outputs"] = [draft(mount, 0), draft(mount, 1)]
    path.write_text(json.dumps(plan))
    engine.prepare(path, tmp_path / "record", resolve=False)
    run = engine.Run(tmp_path / "record")
    state = run.state("reader")
    state["pending"] = dict(turn="wholebook", mode="start", parent_id="sid")
    run.save("reader", state)
    run.record("reader", "wholebook").mkdir(parents=True)
    (run.workspace("reader") / "drafts").mkdir(parents=True, exist_ok=True)
    for n in (0, 1):
        (run.workspace("reader") / draft(mount, n)).write_text("A reaction.\n")
    session = claude_session(
        tmp_path,
        [
            dict(name="Read", input={"file_path": chapter(mount, 0)}),
            dict(name="Read", input={"file_path": chapter(mount, 1)}),
            dict(name="Write", input={"file_path": "/tmp/work/" + draft(mount, 0)}),
            dict(name="Write", input={"file_path": "/tmp/work/" + draft(mount, 1)}),
        ],
    )
    monkeypatch.setattr(
        harness,
        "receipt",
        lambda *a: dict(
            session_id="sid",
            model="fixture-model",
            rollout=str(session),
            answer="Done.",
            forked_from=None,
        ),
    )
    monkeypatch.setattr(harness, "history_image_hashes", lambda *a: [])
    run.collect_turn("reader", 0)
    receipt = run.state("reader")["done"]["wholebook"]["receipt"]
    assert receipt["referenced_files_in_order"] is True
    assert receipt["reactions_before_next"] is False
    assert any(
        "before the reaction to the file before it" in f for f in receipt["red_flags"]
    )
