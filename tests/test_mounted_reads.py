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


def codex_rollout(tmp_path, cwd, payloads):
    """A rollout as Codex writes it: the session's and the turn's working
    directory first, then the calls."""
    rows = [
        {"type": "session_meta", "payload": {"id": "fixture", "cwd": str(cwd)}},
        {"type": "turn_context", "payload": {"cwd": str(cwd)}},
    ] + [{"type": "response_item", "payload": p} for p in payloads]
    path = tmp_path / "rollout.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_a_codex_call_naming_no_directory_runs_in_the_turns(tmp_path, mount):
    """Codex runs a call with no `workdir` in the turn's working directory,
    so a relative path is the file there, in each of its call forms."""
    session = codex_rollout(
        tmp_path,
        mount,
        [
            dict(
                type="custom_tool_call",
                input="await tools.exec_command({cmd: \"sed -n '1,2p' "
                'chapters/01-opening.md"});',
            ),
            dict(
                type="function_call",
                arguments=json.dumps({"cmd": "sed -n '3,3p' chapters/01-opening.md"}),
            ),
            dict(
                type="local_shell_call",
                action={
                    "command": ["cat", "02-later.md"],
                    "working_directory": "chapters",
                },
            ),
        ],
    )
    assert reads.coverage(
        reads.file_reads(session), [chapter(mount, 0), chapter(mount, 1)]
    ) == (2, True, [])


def test_a_sed_read_to_the_last_line_covers_the_rest_of_the_file(tmp_path, mount):
    """`sed -n 'N,$p'` prints from line N to the end, so a file read in two
    pieces, the second open-ended, was read in full. The open piece alone
    leaves the lines before it unread."""
    first = chapter(mount, 0)

    def sed(*ranges):
        return [
            dict(
                type="custom_tool_call",
                input=f"await tools.exec_command({{cmd: \"sed -n '{r}' {first}\"}});",
            )
            for r in ranges
        ]

    (tmp_path / "pieces").mkdir()
    pieces = codex_rollout(tmp_path / "pieces", mount, sed("1,1p", "2,$p"))
    assert reads.coverage(reads.file_reads(pieces), [first]) == (1, True, [])
    (tmp_path / "tail").mkdir()
    tail = codex_rollout(tmp_path / "tail", mount, sed("3,$p"))
    assert reads.coverage(reads.file_reads(tail), [first]) == (
        0,
        True,
        ["01-opening.md"],
    )


def test_a_codex_call_naming_its_own_directory_is_read_there(tmp_path, mount):
    """A `workdir` the call names wins over the turn's: the same relative
    name in another directory is another file, and proves nothing here."""
    elsewhere = tmp_path / "elsewhere"
    session = codex_rollout(
        tmp_path,
        mount,
        [
            dict(
                type="custom_tool_call",
                input='await tools.exec_command({workdir: "'
                + str(elsewhere)
                + '", cmd: "cat chapters/01-opening.md"});\n'
                'await tools.exec_command({cmd: "cat 02-later.md", '
                'workdir: "chapters"});',
            )
        ],
    )
    assert reads.file_reads(session) == [
        (str(elsewhere / "chapters" / "01-opening.md"), None),
        (chapter(mount, 1), None),
    ]


def test_a_codex_directory_that_cannot_be_read_is_not_guessed(tmp_path, mount):
    """A `workdir` built at run time, or a rollout that records no directory,
    leaves a relative path unproven rather than counted at the turn's."""
    unreadable = codex_rollout(
        tmp_path,
        mount,
        [
            dict(
                type="custom_tool_call",
                input='await tools.exec_command({cmd: "cat chapters/01-opening.md", '
                "workdir: base + '/x'});",
            )
        ],
    )
    assert reads.file_reads(unreadable) == []
    unrecorded = codex_session(
        tmp_path,
        [
            dict(
                type="custom_tool_call",
                input='await tools.exec_command({cmd: "cat chapters/01-opening.md"});',
            )
        ],
    )
    assert reads.file_reads(unrecorded) == []


def test_a_codex_patch_with_a_relative_name_is_written_in_the_turns(tmp_path, mount):
    """A relative patch target is the file in the turn's directory, and one
    with no directory recorded keeps its name, which still ends like the
    answer file it is matched against."""
    patch = (
        'await tools.apply_patch("*** Begin Patch\\n*** Add File: '
        'drafts/notes.md\\n+A reaction.\\n*** End Patch");'
    )
    placed = codex_rollout(
        tmp_path, mount, [dict(type="custom_tool_call", input=patch)]
    )
    assert reads.events(placed) == [("write", str(mount / "drafts" / "notes.md"), None)]
    (tmp_path / "bare").mkdir()
    bare = codex_session(
        tmp_path / "bare", [dict(type="custom_tool_call", input=patch)]
    )
    assert reads.events(bare) == [("write", "drafts/notes.md", None)]


def test_a_brace_inside_a_string_does_not_move_the_calls_directory(tmp_path, mount):
    """The `workdir` belongs to the object around its `cmd`; a brace or a
    `workdir` inside a string or a comment is text."""
    session = codex_rollout(
        tmp_path,
        mount,
        [
            dict(
                type="custom_tool_call",
                input="// pass {workdir: '/nowhere'} only when needed\n"
                'const note = "} {";\n'
                'await tools.exec_command({cmd: "cat chapters/02-later.md"});',
            )
        ],
    )
    assert reads.file_reads(session) == [(chapter(mount, 1), None)]


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


def agy_calls(*calls):
    """One response making several calls, each a (name, args) pair."""
    return dict(
        type="PLANNER_RESPONSE",
        status="DONE",
        tool_calls=[dict(name=name, args=args) for name, args in calls],
    )


def agy_answer(head, *body, status="DONE"):
    """A result as agy's newer file tools record it: no file, only what
    happened, under the two timestamps agy puts on every result."""
    return dict(
        type="GENERIC",
        status=status,
        content="\n".join(["Created At: now", "Completed At: now", head, *body]),
    )


def agy_lines(path, first, last):
    text = Path(path).read_text().splitlines()
    return [f"{n}: {text[n - 1]}" for n in range(first, last + 1)]


def test_agy_newer_tools_are_proven_with_the_call_each_result_answers(tmp_path, mount):
    """From 1.4.3, `read_file` and `write_file` name the file only in the call."""
    first, second = chapter(mount, 0), chapter(mount, 1)
    session = agy_session(
        tmp_path,
        [
            agy_call("read_file", Path=first),
            agy_answer("Showing lines 1-3 of 3", *agy_lines(first, 1, 3)),
            agy_call("write_file", Path="/tmp/work/" + draft(mount, 0), Content="…"),
            agy_answer("Created file."),
            agy_call("read_file", Path=second),
            agy_answer("Showing lines 1-2 of 2", *agy_lines(second, 1, 2)),
            agy_call("write_file", Path="/tmp/work/" + draft(mount, 1), Content="…"),
            agy_answer("Created file."),
        ],
    )
    referenced = [first, second]
    evs = reads.events(session)
    assert reads.coverage(reads.file_reads(session), referenced) == (2, True, [])
    pairs = reads.reaction_pairs(referenced, [draft(mount, 0), draft(mount, 1)])
    assert reads.reactions_before_next(evs, referenced, pairs) is True


def test_a_cut_agy_read_counts_only_the_lines_it_showed_whole(tmp_path, mount):
    """A read cut to agy's size limit can begin and end inside a line."""
    first = chapter(mount, 0)
    session = agy_session(
        tmp_path,
        [
            agy_call("read_file", Path=first, EndLine=1),
            agy_answer("Showing lines 1-1 of 3", *agy_lines(first, 1, 1)),
            # From byte 13, inside line 1, to the end: lines 2 and 3 whole.
            agy_call("read_file", Path=first, ByteOffset=13),
            agy_answer(
                "Partial read (lines 1-3 of 3). Showing bytes 13-68 of 68 in "
                "this line range.",
                "green umbrella.",
                *agy_lines(first, 2, 3),
            ),
            # Stopping at byte 40, inside line 2: only line 1 whole.
            agy_call("read_file", Path=first),
            agy_answer(
                "Partial read (lines 1-3 of 3). Showing bytes 1-40 of 68 in "
                "this line range.",
                *agy_lines(first, 1, 1),
                "2: The door sta",
            ),
        ],
    )
    assert reads.file_reads(session) == [
        (first, [(1, 1)]),
        (first, [(2, 3)]),
        (first, [(1, 1)]),
    ]


def test_a_read_agy_stopped_at_its_size_limit_counts_its_whole_lines(tmp_path, mount):
    """agy words a read it stopped at the limit with a comma, not a period.

    The first piece of a long file read in pieces looks like this, and the
    rest of the range is fetched by a second call from the byte it stopped
    at; both pieces together are the whole range.
    """
    first = chapter(mount, 0)
    session = agy_session(
        tmp_path,
        [
            agy_call("read_file", Path=first),
            agy_answer(
                "Partial read (lines 1-3 of 3). Showing bytes 0-40 of 68 in "
                "this line range, truncated at the 40-byte limit. To read the "
                "rest of this line range, call this tool again with the same "
                "range and ByteOffset 40.",
                *agy_lines(first, 1, 1),
                "2: The door sta",
            ),
            agy_call("read_file", Path=first, ByteOffset=40),
            agy_answer(
                "Partial read (lines 1-3 of 3). Showing bytes 40-68 of 68 in "
                "this line range.",
                "yed open.",
                *agy_lines(first, 3, 3),
            ),
        ],
    )
    # Each piece shows its whole lines; the line the cut fell in is whole in
    # neither, and together the pieces cover every byte of the range.
    assert reads.file_reads(session) == [
        (first, [(1, 1)]),
        (first, [(3, 3)]),
        (first, [(1, 3)]),
    ]
    assert reads.coverage(reads.file_reads(session), [first]) == (1, True, [])


def test_pieces_with_a_gap_between_them_do_not_make_a_whole_range(tmp_path, mount):
    first = chapter(mount, 0)
    session = agy_session(
        tmp_path,
        [
            agy_call("read_file", Path=first),
            agy_answer(
                "Partial read (lines 1-3 of 3). Showing bytes 0-40 of 68 in "
                "this line range, truncated at the 40-byte limit. To read the "
                "rest, call again with ByteOffset 40.",
                *agy_lines(first, 1, 1),
                "2: The door sta",
            ),
            agy_call("read_file", Path=first, ByteOffset=50),
            agy_answer(
                "Partial read (lines 1-3 of 3). Showing bytes 50-68 of 68 in "
                "this line range.",
                "open.",
                *agy_lines(first, 3, 3),
            ),
        ],
    )
    assert reads.file_reads(session) == [(first, [(1, 1)]), (first, [(3, 3)])]
    assert reads.coverage(reads.file_reads(session), [first])[0] == 0


def test_a_cut_line_that_looks_numbered_is_not_taken_for_a_whole_one(tmp_path, mount):
    score = mount / "chapters" / "03-score.md"
    score.write_text("The score at noon was 1: nil.\nThe rain held off.\nThey left.\n")
    session = agy_session(
        tmp_path,
        [
            # From byte 23, the tail of line 1 reads like line 1 itself.
            agy_call("read_file", Path=str(score), ByteOffset=23),
            agy_answer(
                "Partial read (lines 1-3 of 3). Showing bytes 23-60 of 60 in "
                "this line range.",
                "1: nil.",
                *agy_lines(score, 2, 3),
            ),
        ],
    )
    assert reads.file_reads(session) == [(str(score), [(2, 3)])]


def test_agy_results_that_do_not_line_up_with_their_calls_prove_nothing(
    tmp_path, mount
):
    """With a result missing or one too many, which answers which is a guess."""
    first, second = chapter(mount, 0), chapter(mount, 1)
    shown = agy_answer("Showing lines 1-2 of 2", *agy_lines(second, 1, 2))
    session = agy_session(
        tmp_path,
        [
            agy_calls(
                ("read_file", dict(Path=first)), ("read_file", dict(Path=second))
            ),
            shown,
            agy_call("read_file", Path=first),
            shown,
            shown,
            agy_call("write_file", Path="/tmp/work/" + draft(mount, 0)),
            agy_answer("Created file.", status="ERROR"),
            agy_call("write_file", Path="/tmp/work/" + draft(mount, 1)),
            agy_answer("File already exists."),
        ],
    )
    assert reads.events(session) == []


def test_an_agy_edit_after_reading_on_is_a_revision(tmp_path, mount):
    """agy 1.4.3's `edit_file` result names the file it changed."""
    first, second = chapter(mount, 0), chapter(mount, 1)
    reaction, later = ("/tmp/work/" + draft(mount, n) for n in (0, 1))
    made = f"Made 1 replacement in {reaction} starting at line 1 (+1/-1 lines)."
    session = agy_session(
        tmp_path,
        [
            agy_call("read_file", Path=first),
            agy_answer("Showing lines 1-3 of 3", *agy_lines(first, 1, 3)),
            agy_call("write_file", Path=reaction, Content="…"),
            agy_answer("Created file."),
            agy_call("read_file", Path=second),
            agy_answer("Showing lines 1-2 of 2", *agy_lines(second, 1, 2)),
            agy_call("edit_file", Path=reaction, Target="…", Replacement="…"),
            agy_answer(made),
            # A result that names another file is not this call's edit.
            agy_call("edit_file", Path=later, Target="…", Replacement="…"),
            agy_answer(made),
            agy_call("write_file", Path=later, Content="…", Overwrite=True),
            agy_answer("Created file."),
        ],
    )
    evs = reads.events(session)
    assert [(kind, path) for kind, path, _ in evs] == [
        ("read", first),
        ("write", reaction),
        ("read", second),
        ("write", reaction),
        ("write", later),
    ]
    referenced = [first, second]
    pairs = reads.reaction_pairs(referenced, [draft(mount, 0), draft(mount, 1)])
    assert reads.reactions_before_next(evs, referenced, pairs) is True
    assert reads.reactions_unrevised(evs, referenced, pairs) is False


def test_a_failed_agy_call_leaves_the_rest_of_its_response_proven(tmp_path, mount):
    """A call that failed is answered in its place by an error."""
    first, second = chapter(mount, 0), chapter(mount, 1)
    session = agy_session(
        tmp_path,
        [
            agy_calls(
                ("read_file", dict(Path=first)), ("read_file", dict(Path=second))
            ),
            dict(type="ERROR_MESSAGE", status="DONE", content="read_file: busy"),
            agy_answer("Showing lines 1-2 of 2", *agy_lines(second, 1, 2)),
        ],
    )
    assert reads.file_reads(session) == [(second, [(1, 2)])]


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


def collected_with_images(tmp_path, mount, monkeypatch, referenced, hashes):
    """The receipt of a turn whose seat opened images with these hashes."""
    path = plan_file(tmp_path, mount)
    plan = json.loads(path.read_text())
    plan["seats"][0]["turns"][0]["referenced_images"] = referenced
    path.write_text(json.dumps(plan))
    engine.prepare(path, tmp_path / "record", resolve=False)
    run = engine.Run(tmp_path / "record")
    state = run.state("reader")
    state["pending"] = dict(turn="wholebook", mode="start", parent_id="sid")
    run.save("reader", state)
    run.record("reader", "wholebook").mkdir(parents=True)
    (run.workspace("reader") / "drafts").mkdir(parents=True, exist_ok=True)
    (run.workspace("reader") / "drafts" / "notes.md").write_text("A reaction.\n")
    session = claude_session(tmp_path, [])
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
    monkeypatch.setattr(harness, "history_image_hashes", lambda *a: hashes)
    run.collect_turn("reader", 0)
    return run.state("reader")["done"]["wholebook"]["receipt"]


@pytest.fixture
def figures(mount):
    (mount / "figures").mkdir()
    door, chair = mount / "figures" / "door.png", mount / "figures" / "chair.png"
    door.write_bytes(b"a door left open")
    chair.write_bytes(b"a chair that moved")
    return door, chair


def test_a_figure_opened_twice_does_not_stand_in_for_one_never_opened(
    tmp_path, mount, figures, monkeypatch
):
    door, chair = figures
    seen = hashlib.sha256(door.read_bytes()).hexdigest()
    receipt = collected_with_images(
        tmp_path, mount, monkeypatch, [str(door), str(chair)], [seen, seen]
    )
    assert receipt["referenced_images_opened"] == "1/2"
    assert any("never opened: chair.png" in f for f in receipt["red_flags"])


def test_every_listed_figure_seen_once_is_proven(tmp_path, mount, figures, monkeypatch):
    hashes = [hashlib.sha256(f.read_bytes()).hexdigest() for f in figures]
    receipt = collected_with_images(
        tmp_path, mount, monkeypatch, [str(f) for f in figures], hashes
    )
    assert receipt["referenced_images_opened"] == "2/2"
    assert not any("Opened fewer images" in f for f in receipt["red_flags"])


def test_a_bare_count_is_compared_with_distinct_images(tmp_path, mount, monkeypatch):
    receipt = collected_with_images(tmp_path, mount, monkeypatch, 2, ["a" * 64] * 2)
    assert receipt["referenced_images_opened"] == "1/2"


def test_a_listed_figure_outside_the_mount_is_refused(tmp_path, mount):
    stray = tmp_path / "stray.png"
    stray.write_bytes(b"not part of the book")
    path = plan_file(tmp_path, mount)
    plan = json.loads(path.read_text())
    plan["seats"][0]["turns"][0]["referenced_images"] = [str(stray)]
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="referenced_images"):
        engine.prepare(path, tmp_path / "record", resolve=False)


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
    # Read ahead, but each reaction written once: early, never revised.
    assert receipt["reactions_unrevised"] is True
    assert any(
        "before the reaction to the file before it" in f for f in receipt["red_flags"]
    )


def test_a_reaction_rewritten_after_reading_on_is_a_revision(tmp_path, mount):
    """The order check dates a reaction by its first write, so a seat that goes
    back and rewrites one after reading the next chapter passes it. Looking
    at an earlier chapter or reaction again is not a revision, and neither is
    finishing a reaction in two writes before moving on."""
    referenced = [chapter(mount, 0), chapter(mount, 1)]
    pairs = reads.reaction_pairs(referenced, [draft(mount, 0), draft(mount, 1)])

    def session(name, calls):
        (tmp_path / name).mkdir()
        return reads.events(claude_session(tmp_path / name, calls))

    def read(n):
        return dict(name="Read", input={"file_path": chapter(mount, n)})

    def write(n, tool="Write"):
        return dict(name=tool, input={"file_path": "/tmp/work/" + draft(mount, n)})

    looked_back = session(
        "looked-back",
        [read(0), write(0), write(0, "Edit"), read(1), read(0), write(1)],
    )
    assert reads.reactions_unrevised(looked_back, referenced, pairs) is True
    rewrote = session(
        "rewrote", [read(0), write(0), read(1), write(1), write(0, "Edit")]
    )
    assert reads.reactions_unrevised(rewrote, referenced, pairs) is False
    unwritten = session("unwritten", [read(0), write(0), read(1)])
    assert reads.reactions_unrevised(unwritten, referenced, pairs) is None
