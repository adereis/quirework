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
    text = "the kettle whistles before the train arrives"
    assert ccfilter.redact(text) == text


def test_strip_wrappers_removes_injected_spans():
    out = ccfilter.strip_wrappers("keep <system-reminder>SECRET</system-reminder> this")
    assert "system-reminder" not in out
    assert "SECRET" not in out
    assert "keep" in out and "this" in out


def test_has_fingerprint():
    assert ccfilter._has_fingerprint("... at Example Labs ...")
    assert not ccfilter._has_fingerprint("a neutral sentence about kettles")


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


def test_a_subagent_report_and_a_compaction_summary_are_not_the_human(tmp_path):
    """v4: both are model-written text the harness logs in the human's slot.

    A background subagent's findings arrive as a task notification, and a
    compacted session resumes from a summary. Rendered as `### Human`, either
    would credit the author with a model's analysis.
    """
    body, stats = ccfilter.filter_session(
        _session(
            tmp_path,
            [
                {"type": "user", "message": {"content": "Check the keeper's name."}},
                {
                    "type": "user",
                    "origin": {"kind": "task-notification"},
                    "message": {
                        "content": "<task-notification>\n<status>completed</status>\n"
                        "<result>The keeper is named twice in chapter 3.</result>\n"
                        "</task-notification>"
                    },
                },
                {
                    "type": "user",
                    "isCompactSummary": True,
                    "message": {
                        "content": "This session is being continued from a "
                        "previous conversation. The keeper was renamed."
                    },
                },
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "Fixed."}]},
                },
            ],
        )
    )
    assert "Check the keeper's name." in body and "Fixed." in body
    assert "named twice" not in body and "being continued" not in body
    assert stats["human_turns"] == 1


def test_a_failed_request_is_not_rendered_as_the_model(tmp_path):
    """The harness writes a failed request's notice in the model's slot."""
    body, _ = ccfilter.filter_session(
        _session(
            tmp_path,
            [
                {"type": "user", "message": {"content": "Check the keeper's name."}},
                {
                    "type": "assistant",
                    "isApiErrorMessage": True,
                    "message": {
                        "model": "<synthetic>",
                        "content": [{"type": "text", "text": "API Error: 529"}],
                    },
                },
            ],
        )
    )
    assert "Check the keeper's name." in body and "API Error" not in body


# --------------------------------------------------------------------------- #
# v3: human authorship the filter used to delete
# --------------------------------------------------------------------------- #


def test_source_names_handles_quoted_paths_with_spaces():
    cmd = 'quirework book redpen read "/home/x/Downloads/V9 - The-Book.docx" --out /tmp/o.md'
    assert ccfilter.source_names(cmd) == ["V9 - The-Book.docx"]
    cmd = "quirework publish kindle notes ~/tmp/Notebook.html --copy 6"
    assert ccfilter.source_names(cmd) == ["Notebook.html"]


def test_a_kindle_round_reaches_the_render_as_the_reader_s_marks(
    tmp_path, redpen_archive
):
    (redpen_archive / "kindle-copy-06-2026-10-20.md").write_text(
        "# Kindle notes, copy 6\n\n"
        "```\n"
        "source-export:  Notebook.html\n"
        "copy-commit:    abc1234\n"
        "```\n\n"
        "## MARKS  (`quirework publish kindle notes`)\n\n"
        "### docs/canon/boiling.qmd\n\n"
        "[Kindle] 2026-10-20  (highlight, yellow, location 12)\n"
        "  highlight: “The whistle is a signal.”\n"
        "  note: say what it signals\n",
        encoding="utf-8",
    )
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
                                    "command": "quirework publish kindle notes "
                                    "~/tmp/Notebook.html --copy 6"
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
                                "content": "kindle: archived",
                            }
                        ]
                    },
                },
            ],
        )
    )
    assert "### Human (Kindle highlights and notes" in body
    assert "say what it signals" in body
    assert "copy-commit" not in body and "[Kindle]" not in body
    assert stats["human_turns"] == 1


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
                                "input": {"file_path": "/x/the-sidecar.qmd"},
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
                                            "question": "Should the sidecar chapter keep the kettle image?",
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
                                "content": 'Your questions have been answered: "Should the sidecar '
                                'chapter keep the kettle image?"="Cut it"',
                            }
                        ]
                    },
                },
            ],
        )
    )
    assert "Should the sidecar chapter keep the kettle image?" in body
    assert "Keep it" in body and "it repeats the dial" in body
    assert "### Human (answer to AskUserQuestion)" in body
    assert '"Cut it"' in body
    assert stats["human_turns"] == 1


def _asked_and_answered(tmp_path, answer):
    ask = {
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
                                "question": "Keep the kettle image?",
                                "options": [
                                    {
                                        "label": "Keep it",
                                        "preview": "The kettle stays.",
                                    },
                                    {"label": "Cut it", "preview": "The dial alone."},
                                ],
                            }
                        ]
                    },
                }
            ]
        },
    }
    result = {
        "type": "user",
        "message": {
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": answer}]
        },
    }
    body, _ = ccfilter.filter_session(_session(tmp_path, [ask, result]))
    return body.split("### Human (answer to AskUserQuestion)\n\n", 1)[1]


def test_a_blind_render_cuts_the_models_preview_from_an_answer(tmp_path):
    """The harness copies the picked option's preview, the model's sketch, into
    the answer, which the analyst would read as the author's words."""
    answer = _asked_and_answered(
        tmp_path,
        'Your questions have been answered: "Keep the kettle image?"="Cut it" '
        "selected preview:\nThe dial alone. notes: keep the whistle. You can now "
        "continue with these answers in mind.",
    )
    assert "The dial alone" not in answer
    assert answer.startswith(
        'Your questions have been answered: "Keep the kettle image?"="Cut it" '
        "notes: keep the whistle. You can now continue"
    )


def test_an_answer_without_a_preview_renders_as_it_always_did(tmp_path):
    """The framing stays in the blind render, so a report on a session whose
    answers carried no preview revalidates instead of being bought again."""
    logged = (
        'Your questions have been answered: "Keep the kettle image?"="Keep it". '
        "You can now continue with these answers in mind."
    )
    assert _asked_and_answered(tmp_path, logged) == logged


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
                            "input": {
                                "file_path": "/repo/manuscript/sections/the-sidecar.qmd"
                            },
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
    assert (
        blocks[2]["input"]["file_path"] == "/repo/manuscript/sections/the-sidecar.qmd"
    )
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
        {"type": "user", "message": {"content": "Rewrite the sidecar."}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "w1",
                        "name": "Write",
                        "input": {
                            "file_path": "/repo/manuscript/sections/the-sidecar.qmd",
                            "content": body,
                        },
                    },
                    {
                        "type": "tool_use",
                        "id": "e1",
                        "name": "Edit",
                        "input": {
                            "file_path": "/repo/manuscript/sections/the-sidecar.qmd",
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


# --------------------------------------------------------------------------- #
# sub-agents: part of the session, captured as side-threads
# --------------------------------------------------------------------------- #
ASK = "Read chapter 1 and report where the kettle first whistles."

MAIN = [
    {
        "type": "user",
        "timestamp": "2031-04-02T10:00:00Z",
        "message": {"content": "Where does the kettle first whistle?"},
    },
    {
        "type": "assistant",
        "timestamp": "2031-04-02T10:00:05Z",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "m1",
                    "name": "Agent",
                    "input": {"description": "Find the whistle", "prompt": ASK},
                }
            ]
        },
    },
    {
        "type": "assistant",
        "timestamp": "2031-04-02T10:02:00Z",
        "message": {"content": [{"type": "text", "text": "Page 3, it says."}]},
    },
]


def _side(**fields):
    return {"isSidechain": True, "agentId": "a1b2c3", **fields}


SIDE = [
    _side(type="user", timestamp="2031-04-02T10:00:06Z", message={"content": ASK}),
    # The guides and memory the harness injects into every sub-agent.
    _side(
        type="attachment", attachment={"type": "instructions", "content": "Y" * 5000}
    ),
    _side(
        type="assistant",
        timestamp="2031-04-02T10:00:10Z",
        message={
            "model": "claude-haiku-5-5",
            "usage": {"input_tokens": 50000},
            "content": [
                {
                    "type": "tool_use",
                    "id": "s1",
                    "name": "Read",
                    "input": {"file_path": "/book/chapter-1.md"},
                }
            ],
        },
    ),
    _side(
        type="user",
        timestamp="2031-04-02T10:00:11Z",
        message={
            "content": [
                {"type": "tool_result", "tool_use_id": "s1", "content": "X" * 5000}
            ]
        },
    ),
    _side(
        type="assistant",
        timestamp="2031-04-02T10:01:00Z",
        message={
            "content": [
                {"type": "text", "text": "Page 3, second paragraph."},
                {
                    "type": "tool_use",
                    "id": "s2",
                    "name": "SubagentHandback",
                    "input": {"message": "The kettle first whistles on page 3."},
                },
            ]
        },
    ),
]

META = {
    "agentType": "general-purpose",
    "description": "Find the whistle",
    "model": "haiku",
}


def _with_subagent(folder, main, side, *, agent="agent-a1b2c3", meta=META):
    """A session and one sub-agent beside it, laid out as Claude Code keeps them."""
    folder.mkdir(parents=True, exist_ok=True)
    session = folder / "s.jsonl"
    session.write_text("".join(json.dumps(r) + "\n" for r in main), encoding="utf-8")
    agents = folder / "s" / ccfilter.SUBAGENT_DIR
    agents.mkdir(parents=True)
    sub = agents / f"{agent}.jsonl"
    sub.write_text("".join(json.dumps(r) + "\n" for r in side), encoding="utf-8")
    if meta is not None:
        (agents / f"{agent}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return session, sub


def _rows(data):
    rows = [json.loads(line) for line in data.decode("utf-8").splitlines()]
    return rows[0], rows[1:]


def test_a_capture_folds_in_its_subagents_as_side_threads(tmp_path):
    session, sub = _with_subagent(tmp_path, MAIN, SIDE)
    data = ccfilter.normalize_capture(session)
    header, kept = _rows(data)

    [agent] = header["subagents"]
    size, sha = ccfilter.source_fingerprint(sub)
    assert (agent["agent"], agent["source_bytes"], agent["source_sha256"]) == (
        "agent-a1b2c3",
        size,
        sha,
    )
    assert agent["meta"] == META
    assert agent["records"] == 4
    # What it was given and what it read are gone; what it did stays.
    assert agent["dropped"] == {
        "attachment": 1,
        "message.usage": 1,
        "tool_result.content": 1,
    }
    assert b"Y" * 100 not in data and b"X" * 100 not in data
    # The session's own census is its own: nothing of the sub-agent's in it.
    assert header["dropped"] == {}
    assert header["records"] == len(kept) == 7

    # Interleaved by time, the session's own thread in its own order.
    stamps = [r["timestamp"] for r in kept]
    assert stamps == sorted(stamps)
    assert [r.get("isSidechain", False) for r in kept] == [
        False,
        False,
        True,
        True,
        True,
        True,
        False,
    ]
    read, _result, report = kept[3], kept[4], kept[5]
    assert read["message"]["content"][0]["input"] == {"file_path": "/book/chapter-1.md"}
    handback = report["message"]["content"][1]
    assert handback["input"]["message"] == "The kettle first whistles on page 3."
    # Deterministic, as every capture is.
    assert ccfilter.normalize_capture(session) == data


def test_a_session_without_subagents_captures_as_it_always_did(tmp_path):
    header, _ = _capture(tmp_path, MAIN)
    assert "subagents" not in header


def test_a_subagent_record_not_stamped_as_a_side_thread_is_dropped(tmp_path):
    stray = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "stray"}]},
    }
    session, _ = _with_subagent(tmp_path, MAIN, [*SIDE, stray])
    header, kept = _rows(ccfilter.normalize_capture(session))
    assert header["subagents"][0]["dropped"]["<not-a-side-thread>"] == 1
    assert all("stray" not in json.dumps(r) for r in kept)


def test_subagents_change_nothing_a_blind_analyst_reads(tmp_path):
    """The cold-read render drops side-threads, so a capture that gained its
    sub-agents renders exactly as the capture without them did."""
    with_agents, _ = _with_subagent(tmp_path / "a", MAIN, SIDE)
    without = tmp_path / "b" / "s.jsonl"
    without.parent.mkdir()
    without.write_text("".join(json.dumps(r) + "\n" for r in MAIN), encoding="utf-8")
    renders = []
    for source in (with_agents, without):
        capture = source.with_name("capture.jsonl")
        capture.write_bytes(ccfilter.normalize_capture(source))
        renders.append(ccfilter.filter_session(str(capture))[0])
    assert renders[0] == renders[1]
    assert "Page 3, it says." in renders[0]
    assert "second paragraph" not in renders[0]


def test_interleaving_never_reorders_a_stream():
    main = [
        {"n": "m0", "timestamp": "2031-04-02T10:00:00Z"},
        {"n": "m1", "timestamp": "2031-04-02T09:59:00Z"},  # logged out of order
        {"n": "m2"},  # no timestamp: travels with the record before it
        {"n": "m3", "timestamp": "2031-04-02T10:05:00Z"},
    ]
    side = [{"n": "s0", "timestamp": "2031-04-02T10:01:00Z"}]
    order = [r["n"] for r in ccfilter._interleave([main, side])]
    assert order == ["m0", "m1", "m2", "s0", "m3"]
