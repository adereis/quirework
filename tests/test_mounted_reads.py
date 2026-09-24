"""A mounted read: the record names the bytes it pinned and proves the reading.

A seat that walks a directory with its own tools is handed nothing the record
froze, so two things have to be checked from outside it. The mounted bytes
must still be the ones the plan pinned, and the native session must show that
each file the seat was to read was read in full and in order.
"""

import json

import pytest

from quirework.providers import harness, reads
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
    assert (fully, in_order, unproven) == (0, False, ["01-opening.md"])


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
    tmp_path, mount, monkeypatch
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


def test_an_unchanged_mount_goes_on_to_launch(tmp_path, mount, monkeypatch):
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
    assert receipt["referenced_files_in_order"] is False
    assert receipt["read_root_digests_held"] is True
    assert any("Not proven read in full" in f for f in receipt["red_flags"])
