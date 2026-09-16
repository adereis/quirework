import json
import os

import pytest

from quirework.provenance import ccfilter


@pytest.fixture
def redpen_archive(tmp_path, monkeypatch):
    """A stand-in red-pen archive, so the tests never read the real corpus."""
    d = tmp_path / "redpen"
    d.mkdir()
    (d / "v9-2026-07-30.md").write_text(
        "# Red-pen round v9\n\n"
        "```\n"
        "source-docx:    V9 - The-Book.docx\n"
        "canon-sha:      abc1234\n"
        "```\n\n"
        "## MARKS  (`quirework book redpen read`)\n\n"
        "### COMMENTS (1)\n\n"
        "[Example Writer] 2026-07-30  (on text)\n"
        "  note: the dial metaphor is doing too much work here\n",
        encoding="utf-8",
    )
    (d / "README.md").write_text("not a round\n", encoding="utf-8")
    monkeypatch.setattr(ccfilter, "redpen_directory", lambda: d)
    yield d


def _session(tmp_path, records):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return str(p)


def test_redact_removes_identity_fingerprint():
    out = ccfilter.redact("I am a Director of Software Engineering at Example Labs.")
    assert "Example Labs" not in out
    assert "Director of Software Engineering" not in out
    assert ccfilter.REDACT_PLACEHOLDER in out


def test_redact_removes_authorship_verdict():
    out = ccfilter.redact("the model is human-directed, AI-articulated")
    assert "human-directed" not in out
    assert ccfilter.REDACT_PLACEHOLDER in out


def test_redact_keeps_subject_matter():
    text = "consciousness is a state of states"
    assert ccfilter.redact(text) == text


def test_strip_wrappers_removes_injected_spans():
    out = ccfilter.strip_wrappers("keep <system-reminder>SECRET</system-reminder> this")
    assert "system-reminder" not in out
    assert "SECRET" not in out
    assert "keep" in out and "this" in out


def test_has_fingerprint():
    assert ccfilter._has_fingerprint("... at Example Labs ...")
    assert not ccfilter._has_fingerprint("a neutral sentence about minds")


def test_sidechain_meta_and_harness_turns_never_reach_the_analyst(tmp_path):
    """v2: subagent side-threads and harness-authored pseudo-human turns must
    not read as main-thread dialogue in a blind render."""
    import json

    lines = [
        {"type": "user", "message": {"content": "A real human turn."}},
        {
            "type": "user",
            "isSidechain": True,
            "message": {"content": "subagent prompt, not the human"},
        },
        {
            "type": "assistant",
            "isSidechain": True,
            "message": {"content": [{"type": "text", "text": "subagent prose"}]},
        },
        {"type": "user", "isMeta": True, "message": {"content": "meta bookkeeping"}},
        {
            "type": "user",
            "message": {
                "content": "Caveat: The messages below were generated while running local commands."
            },
        },
        {
            "type": "user",
            "message": {
                "content": [{"type": "text", "text": "[Request interrupted by user]"}]
            },
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Main-thread reply."}]},
        },
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    body, stats = ccfilter.filter_session(str(p))
    assert "A real human turn." in body and "Main-thread reply." in body
    for leaked in ("subagent", "meta bookkeeping", "Caveat:", "[Request interrupted"):
        assert leaked not in body
    assert stats["human_turns"] == 1 and stats["assistant_turns"] == 1


# --------------------------------------------------------------------------- #
# v3: human authorship the filter used to delete
# --------------------------------------------------------------------------- #


def test_docx_names_handles_quoted_paths_with_spaces():
    cmd = 'quirework book redpen read "/home/x/Downloads/V9 - The-Book.docx" --out /tmp/o.md'
    assert ccfilter.docx_names(cmd) == ["V9 - The-Book.docx"]


def test_redpen_marks_splices_the_archive_stripped_and_blind(redpen_archive):
    hit = ccfilter.redpen_marks(
        'quirework book redpen read "~/Downloads/V9 - The-Book.docx"'
    )
    assert hit is not None
    _, text = hit
    assert "the dial metaphor is doing too much work" in text  # the author's words
    assert "Example Writer" not in text  # byline stripped
    assert "canon-sha" not in text  # header stripped
    assert "2026-07-30" in text  # date kept


def test_redpen_marks_returns_none_for_an_unarchived_docx(redpen_archive):
    assert (
        ccfilter.redpen_marks("quirework book redpen read /tmp/01-reviewed.docx")
        is None
    )


def test_redpen_marks_reach_the_render_even_when_output_was_redirected(
    tmp_path, redpen_archive
):
    """The rounds that redirected the dump to a scratch file left only a few
    bytes of shell confirmation in the log; the marks must still be recovered."""
    body, stats = ccfilter.filter_session(
        _session(
            tmp_path,
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "Bash",
                                "input": {
                                    "command": 'quirework book redpen read "~/Downloads/V9 - The-Book.docx"'
                                    " > ~/tmp/marks.txt"
                                },
                            }
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {"type": "tool_result", "tool_use_id": "t1", "content": ""}
                        ]
                    },
                },
            ],
        )
    )
    assert "### Human (red-pen marks" in body
    assert "the dial metaphor is doing too much work" in body
    assert stats["human_turns"] == 1


def test_a_round_is_spliced_once_however_often_it_is_read(tmp_path, redpen_archive):
    cmd = 'quirework book redpen read "~/Downloads/V9 - The-Book.docx"'
    body, stats = ccfilter.filter_session(
        _session(
            tmp_path,
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "Bash",
                                "input": {"command": cmd},
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
                                "tool_use_id": "t1",
                                "content": "ok",
                            }
                        ]
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "t2",
                                "name": "Bash",
                                "input": {"command": cmd},
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
                                "tool_use_id": "t2",
                                "content": "ok",
                            }
                        ]
                    },
                },
            ],
        )
    )
    assert body.count("### Human (red-pen marks") == 1
    assert stats["human_turns"] == 1


def test_a_failed_redpen_command_splices_nothing(tmp_path, redpen_archive):
    body, stats = ccfilter.filter_session(
        _session(
            tmp_path,
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "Bash",
                                "input": {
                                    "command": 'quirework book redpen read "~/Downloads/V9 - The-Book.docx"'
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
                                "tool_use_id": "t1",
                                "is_error": True,
                                "content": "no such file",
                            }
                        ]
                    },
                },
            ],
        )
    )
    assert "red-pen marks" not in body and stats["human_turns"] == 0


def test_ordinary_tool_results_are_still_dropped(tmp_path, redpen_archive):
    body, stats = ccfilter.filter_session(
        _session(
            tmp_path,
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "Read",
                                "input": {"file_path": "/x/the-spout.qmd"},
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
                                "tool_use_id": "t1",
                                "content": "WHOLE CHAPTER DUMPED HERE",
                            }
                        ]
                    },
                },
            ],
        )
    )
    assert "WHOLE CHAPTER DUMPED HERE" not in body
    assert stats["human_turns"] == 0


def test_redpen_splice_is_redacted_for_blindness(tmp_path, redpen_archive):
    """The review-notes blocks state the book's own authorship disclosures; a
    blind analyst must not be handed the conclusion in the author's voice."""
    (redpen_archive / "v9-2026-07-30.md").write_text(
        "# r\n\n```\nsource-docx:    V9 - The-Book.docx\n```\n\n"
        "concepts:\n  - the book is AI co-authored - most of the sentences are"
        " the machine's.\n",
        encoding="utf-8",
    )
    _, text = ccfilter.redpen_marks('redpen read "V9 - The-Book.docx"')
    assert "AI co-authored" not in text
    assert "sentences are the machine" not in text
    assert ccfilter.REDACT_PLACEHOLDER in text


def test_askuserquestion_renders_question_options_and_answer(tmp_path):
    """v2 rendered every one of these as the bare string "[AskUserQuestion]":
    no question, no options, and no answer -- the human's decision invisible."""
    body, stats = ccfilter.filter_session(
        _session(
            tmp_path,
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "AskUserQuestion",
                                "input": {
                                    "questions": [
                                        {
                                            "question": "Should the spout chapter keep the whistling image?",
                                            "options": [
                                                {
                                                    "label": "Keep it",
                                                    "description": "it carries the argument",
                                                },
                                                {
                                                    "label": "Cut it",
                                                    "description": "it repeats the dial",
                                                },
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
                                "tool_use_id": "t1",
                                "content": 'Your questions have been answered: "Should the spout '
                                'chapter keep the whistling image?"="Cut it"',
                            }
                        ]
                    },
                },
            ],
        )
    )
    assert "Should the spout chapter keep the whistling image?" in body
    assert "Keep it" in body and "it repeats the dial" in body
    assert "### Human (answer to AskUserQuestion)" in body
    assert '"Cut it"' in body
    assert stats["human_turns"] == 1


# --------------------------------------------------------------------------- #
# normalize_capture: what the repository copy of a session keeps
# --------------------------------------------------------------------------- #


def _capture(tmp_path, records):
    """Normalize a session and return its header plus the records it kept."""
    rows = [
        json.loads(line)
        for line in ccfilter.normalize_capture(_session(tmp_path, records))
        .decode("utf-8")
        .splitlines()
    ]
    return rows[0], rows[1:]


def test_marker_prefix_is_invisible_to_snippet():
    """The prefix a capture stores must print the same marker as the whole.

    A fixed character cut is not enough: `snippet` collapses whitespace before
    it truncates, so heavily indented content collapses far below the cut and a
    naive prefix would print a shorter marker than the source did.
    """
    for text in (
        "x" * 900,
        ("word" + " " * 60) * 80,
        "\n\n".join("    indented line " + str(i) for i in range(200)),
    ):
        kept = ccfilter.marker_prefix(text)
        assert len(kept) <= len(text)
        for width in (60, 80, 90, 140, 200):
            assert ccfilter.snippet(kept, width) == ccfilter.snippet(text, width)


def test_capture_keeps_the_dialogue_and_drops_the_freight(tmp_path):
    header, kept = _capture(
        tmp_path,
        [
            {"type": "user", "message": {"content": "Prune the session files."}},
            {
                "type": "assistant",
                "message": {
                    "model": "claude-opus-5",
                    "usage": {"input_tokens": 91234},
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "weigh it",
                            "signature": "Ct8BCkYIBxg" * 200,
                        },
                        {"type": "text", "text": "Here is the plan."},
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Read",
                            "input": {"file_path": "/repo/docs/canon/the-spout.qmd"},
                        },
                    ],
                },
            },
            {
                "type": "user",
                "toolUseResult": {"file": {"content": "X" * 5000}},
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": "X" * 5000,
                        }
                    ]
                },
            },
            {"type": "attachment", "attachment": {"content": "Y" * 5000}},
        ],
    )
    user, assistant, result = kept
    assert len(kept) == 3  # the attachment is gone
    assert user["message"]["content"] == "Prune the session files."
    blocks = assistant["message"]["content"]
    assert "usage" not in assistant["message"]
    assert assistant["message"]["model"] == "claude-opus-5"
    assert blocks[0] == {"type": "thinking", "thinking": "weigh it"}
    assert blocks[1]["text"] == "Here is the plan."
    assert blocks[2]["input"]["file_path"] == "/repo/docs/canon/the-spout.qmd"
    assert "toolUseResult" not in result
    assert "content" not in result["message"]["content"][0]
    assert result["message"]["content"][0]["tool_use_id"] == "t1"
    assert header["capture_schema"] == ccfilter.CAPTURE_SCHEMA
    assert header["dropped"]["attachment"] == 1
    assert header["dropped"]["toolUseResult"] == 1
    assert header["dropped"]["thinking.signature"] == 1


def test_capture_keeps_every_channel_the_human_speaks_through(tmp_path):
    """Three of the author's channels arrive as tool *output*, not as a turn.

    Dropping tool results wholesale would delete his answer to a question, the
    plan as he edited it, and the instructions he gave when rejecting a call --
    the last of which no reader surfaces yet, which is exactly why the capture
    must not be the thing that decides it never can.
    """
    _, kept = _capture(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "a1",
                            "name": "AskUserQuestion",
                            "input": {"questions": [{"question": "Which?"}]},
                        },
                        {
                            "type": "tool_use",
                            "id": "p1",
                            "name": "ExitPlanMode",
                            "input": {"plan": "the plan"},
                        },
                        {
                            "type": "tool_use",
                            "id": "e1",
                            "name": "Edit",
                            "input": {"file_path": "/repo/x.qmd", "new_string": "n"},
                        },
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "a1",
                            "content": "Option B",
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
                            "tool_use_id": "p1",
                            "content": "Approved Plan (edited by user): do it in a worktree",
                        }
                    ]
                },
            },
            {
                "type": "user",
                "toolDenialKind": "user-rejected",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "e1",
                            "content": "the user said:\nname it by the scene, not the number",
                        }
                    ]
                },
            },
        ],
    )
    bodies = [b["content"] for r in kept[1:] for b in r["message"]["content"]]
    assert bodies == [
        "Option B",
        "Approved Plan (edited by user): do it in a worktree",
        "the user said:\nname it by the scene, not the number",
    ]


def test_capture_keeps_the_paste_and_drops_the_pixels(tmp_path):
    _, kept = _capture(
        tmp_path,
        [
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "text", "text": "look at this"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "iVBORw0KGgo" * 900,
                            },
                        },
                    ]
                },
            },
        ],
    )
    image = kept[0]["message"]["content"][1]
    assert image["source"] == {"type": "base64", "media_type": "image/png"}


def test_capture_prints_the_same_action_markers_as_its_source(tmp_path):
    """A file body is cut back, but never below what the marker prints."""
    body = "\n".join(f"    line {i} of a long rewritten section" for i in range(400))
    records = [
        {"type": "user", "message": {"content": "Rewrite the spout."}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "w1",
                        "name": "Write",
                        "input": {
                            "file_path": "/repo/docs/canon/the-spout.qmd",
                            "content": body,
                        },
                    },
                    {
                        "type": "tool_use",
                        "id": "e1",
                        "name": "Edit",
                        "input": {
                            "file_path": "/repo/docs/canon/the-spout.qmd",
                            "old_string": body,
                            "new_string": body,
                        },
                    },
                ]
            },
        },
    ]
    raw = _session(tmp_path, records)
    capture = tmp_path / "capture.jsonl"
    capture.write_bytes(ccfilter.normalize_capture(raw))
    assert ccfilter.filter_session(raw) == ccfilter.filter_session(str(capture))
    assert capture.stat().st_size < os.path.getsize(raw) // 4


def test_capture_header_tells_a_capture_from_a_raw_transcript(tmp_path):
    raw = _session(tmp_path, [{"type": "user", "message": {"content": "hi"}}])
    assert ccfilter.capture_header(raw) is None
    capture = tmp_path / "capture.jsonl"
    data = ccfilter.normalize_capture(raw)
    capture.write_bytes(data)
    header = ccfilter.capture_header(str(capture))
    assert (
        header["source_bytes"],
        header["source_sha256"],
    ) == ccfilter.source_fingerprint(raw)
    # Deterministic: the sweep decides by fingerprint, so the same source must
    # normalize to the same bytes or every sweep rewrites the whole corpus.
    assert ccfilter.normalize_capture(raw) == data


def test_a_torn_line_costs_only_itself(tmp_path):
    """The mirror can read a session mid-write; one bad line must not cost the file."""
    p = tmp_path / "torn.jsonl"
    p.write_text(
        json.dumps({"type": "user", "message": {"content": "kept"}})
        + "\n"
        + '{"type": "assistant", "mess\n',
        encoding="utf-8",
    )
    rows = ccfilter.normalize_capture(str(p)).decode().splitlines()
    assert json.loads(rows[1])["message"]["content"] == "kept"
    assert json.loads(rows[0])["dropped"]["<unparseable>"] == 1


@pytest.fixture(autouse=True)
def fictitious_redaction_policy(tmp_path, isolated_project):
    from quirework.project import Project, project_scope

    markers = [
        "Director of Software Engineering at Example Labs",
        "Director of Software Engineering",
        "Example Labs",
        "human-directed, AI-articulated",
        "human-directed",
        "AI-articulated",
        "How we work together",
        "AI co-authored",
        "most of the sentences are the machine's",
    ]
    with project_scope(
        Project.from_settings(
            tmp_path / "book", {"provenance": {"redaction_markers": markers}}
        )
    ):
        yield
