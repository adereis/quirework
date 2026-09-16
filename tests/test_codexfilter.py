"""Codex rollout parsing on synthetic records, never the private corpus."""

import hashlib
import json

from quirework.provenance import codexfilter as codex


SID = "01a01272-de83-7753-a634-fbdc4cc56e90"


def _write(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return path


def _meta(*, source="cli", cwd="/repo"):
    return {
        "timestamp": "2026-08-17T22:18:33Z",
        "type": "session_meta",
        "payload": {
            "id": SID,
            "session_id": SID,
            "cwd": cwd,
            "source": source,
            "timestamp": "2026-08-17T22:18:33Z",
        },
    }


def _message(role, text, stamp="2026-08-17T22:19:00Z", phase=None):
    payload = {
        "type": "message",
        "role": role,
        "content": [
            {
                "type": "input_text" if role == "user" else "output_text",
                "text": text,
            }
        ],
    }
    if phase:
        payload["phase"] = phase
    return {"timestamp": stamp, "type": "response_item", "payload": payload}


def test_main_thread_drops_injected_card_and_keeps_visible_dialogue(tmp_path):
    path = _write(
        tmp_path / "rollout.jsonl",
        [
            _meta(),
            _message(
                "user", "# AGENTS.md instructions for /repo\n\n<environment_context>x"
            ),
            _message("user", "Please sharpen the spout argument."),
            _message(
                "assistant", "The weak point is the transition.", phase="commentary"
            ),
        ],
    )
    assert codex.records(path) == [
        ("human", "Please sharpen the spout argument.", "2026-08-17T22:19:00Z"),
        ("assistant", "The weak point is the transition.", "2026-08-17T22:19:00Z"),
    ]
    assert codex.human_records(path)[0][1] == "Please sharpen the spout argument."


def test_raw_labels_injected_context_instead_of_calling_it_human(tmp_path):
    injected = "# AGENTS.md instructions for /repo\n\n<environment_context>x"
    path = _write(tmp_path / "rollout.jsonl", [_meta(), _message("user", injected)])
    assert codex.records(path, raw=True) == [
        ("harness", injected, "2026-08-17T22:19:00Z")
    ]


def test_subagent_prompt_is_machine_written_not_human(tmp_path):
    source = {"subagent": {"other": "reviewer"}}
    path = _write(
        tmp_path / "rollout.jsonl",
        [
            _meta(source=source),
            _message("user", "Read this chapter and report the weak seam."),
            _message("assistant", "The seam is between scale and kind."),
        ],
    )
    assert codex.records(path)[0][0] == "agent"
    assert codex.human_records(path) == []
    assert codex.scan(path)["source_kind"] == "reviewer"
    assert codex.scan(path)["title"].startswith("[reviewer]")


def test_scan_reads_dates_model_and_first_real_prompt(tmp_path):
    path = _write(
        tmp_path / "rollout.jsonl",
        [
            _meta(),
            _message(
                "user", "# AGENTS.md instructions for /repo\n<environment_context>x"
            ),
            _message("user", "A precise title for this session"),
            {
                "timestamp": "2026-08-18T00:01:00Z",
                "type": "turn_context",
                "payload": {"model": "gpt-5.6-sol"},
            },
        ],
    )
    assert codex.scan(path) == {
        "title": "A precise title for this session",
        "date": "2026-08-17",
        "date_end": "2026-08-18",
        "model": "gpt-5.6-sol",
        "source_kind": "main",
    }


def test_apply_patch_targets_are_recovered_from_escaped_exec_input(tmp_path):
    call = {
        "timestamp": "2026-08-17T22:20:00Z",
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "exec",
            "input": (
                'const patch = "*** Begin Patch\\n'
                "*** Update File: /repo/docs/canon/the-spout.qmd\\n"
                '@@\\n-old\\n+new\\n*** End Patch"; '
                "await tools.apply_patch(patch);"
            ),
        },
    }
    path = _write(tmp_path / "rollout.jsonl", [_meta(), call])
    assert codex.edit_paths(path) == ["/repo/docs/canon/the-spout.qmd"]
    doc, meta, files = codex.edit_targets(
        path, lambda value: "doc" if "/docs/" in value else "meta"
    )
    assert (doc, meta, files) == (1, 0, {"the-spout.qmd": 1})


def test_torn_or_unknown_records_are_ignored(tmp_path):
    path = tmp_path / "rollout.jsonl"
    path.write_text(
        json.dumps(_meta())
        + "\nnot-json\n"
        + json.dumps({"type": "future_record", "payload": {"anything": True}})
        + "\n"
        + json.dumps(_message("user", "Keep the actual prompt."))
        + "\n"
    )
    assert [text for _role, text, _ts in codex.human_records(path)] == [
        "Keep the actual prompt."
    ]


def test_every_harness_block_seen_in_the_corpus_is_kept_out_of_human_turns(tmp_path):
    """Codex delivers its own framing through the `user` role.

    Each of these was found in the live corpus wearing a user label. Left in,
    they enter `search`, `input` and `metrics` as the author's own words.
    """
    blocks = [
        "# AGENTS.md instructions for /repo\n\nBe careful.",
        "<environment_context>\n  <current_date>2026-08-17</current_date>\n</...>",
        "<recommended_plugins>\nHere is a list of plugins…\n</recommended_plugins>",
        "<skill>\n<name>mw-reader-readthrough</name>\n</skill>",
        "<turn_aborted>\nThe user interrupted the previous turn.\n</turn_aborted>",
        # a tag this filter has never met: the shape is the rule, not the list
        "<some_future_block>\ninjected later\n</some_future_block>",
    ]
    rows = [_meta()]
    for text in blocks:
        rows.append(_message("user", text))
    rows.append(_message("user", "[$mw-reader-readthrough](/repo/skills/x/SKILL.md)"))
    rows.append(_message("user", "approved"))
    path = _write(tmp_path / "rollout.jsonl", rows)

    assert [text for _r, text, _ts in codex.human_records(path)] == [
        "[$mw-reader-readthrough](/repo/skills/x/SKILL.md)",
        "approved",
    ]
    # raw mode labels them rather than pretending they were never there
    kinds = [role for role, _t, _ts in codex.records(path, raw=True)]
    assert kinds == ["harness"] * len(blocks) + ["human", "human"]


def test_a_human_turn_that_merely_mentions_a_tag_is_not_dropped(tmp_path):
    """The rule is a first line that is *nothing but* an opening tag."""
    path = _write(
        tmp_path / "rollout.jsonl",
        [
            _meta(),
            _message("user", "<environment_context> is what Codex injects; drop it."),
            _message("user", "Why is <skill> showing up as one of my turns?"),
        ],
    )
    assert len(codex.human_records(path)) == 2


def _exec(js, stamp="2026-08-17T22:20:00Z"):
    return {
        "timestamp": stamp,
        "type": "response_item",
        "payload": {"type": "custom_tool_call", "name": "exec", "input": js},
    }


def test_an_exec_marker_carries_the_command_not_just_the_tool_name(tmp_path):
    """Codex funnels everything through one `exec` tool, so the name says nothing.

    Unadorned, a session renders as a hundred identical `[exec_command]` lines.
    All three shapes Codex writes have to give up their command: a quoted key,
    a bare JS key, and a command that quotes things itself.
    """
    path = _write(
        tmp_path / "rollout.jsonl",
        [
            _meta(),
            _exec(
                'const r = await tools.exec_command({"cmd":"git status --short",'
                '"workdir":"/repo"});'
            ),
            _exec(
                'const r = await tools.exec_command({cmd:"quirework book build",'
                '"justification":"Allow the render to reach the cache outside '
                'the workspace so the chapter can be verified?"});'
            ),
            _exec(
                r"""const r = await tools.exec_command({"cmd":"rg -n \"the spout\" docs"});"""
            ),
            _exec(
                'const r = await tools.exec_command({"cmd":["bash","-lc","ls -la"]});'
            ),
        ],
    )
    body, _stats = codex.filter_session(path)
    assert "[exec_command: git status --short]" in body
    # the justification is not the command, however quotable it looks
    assert "[exec_command: quirework book build]" in body
    assert "Allow the render" not in body
    assert '[exec_command: rg -n "the spout" docs]' in body
    assert "[exec_command: bash -lc ls -la]" in body


def test_a_command_free_call_keeps_a_bare_marker(tmp_path):
    path = _write(
        tmp_path / "rollout.jsonl",
        [
            _meta(),
            _exec(
                'const r = await tools.write_stdin({"session_id":73949,"chars":""});'
            ),
        ],
    )
    body, _ = codex.filter_session(path)
    assert "[write_stdin]" in body


def test_markers_sit_where_they_happened_and_repeats_collapse(tmp_path):
    """Order is the evidence: who asked, what ran, what was said back.

    A trailing block of markers says a hundred commands ran; interleaved, it
    says the human asked and *then* the agent moved. A retry loop collapses so
    it costs one line rather than forty.
    """
    path = _write(
        tmp_path / "rollout.jsonl",
        [
            _meta(),
            _message("user", "Fix the steamer chapter."),
            _exec('await tools.exec_command({"cmd":"rg -n steamer docs"});'),
            _exec('await tools.write_stdin({"chars":""});'),
            _exec('await tools.write_stdin({"chars":""});'),
            _exec('await tools.write_stdin({"chars":""});'),
            _message("assistant", "Done; the seam was in the second paragraph."),
        ],
    )
    body, stats = codex.filter_session(path)
    order = [
        body.index("### Human"),
        body.index("[exec_command: rg -n steamer docs]"),
        body.index("[write_stdin] × 3"),
        body.index("### Assistant"),
    ]
    assert order == sorted(order)
    assert body.count("write_stdin") == 1  # collapsed, not repeated
    assert stats["human_turns"] == 1 and stats["assistant_turns"] == 1


def test_the_edits_stat_agrees_with_edit_paths(tmp_path):
    """catalog/triage count patches one way and the render another; one truth."""
    patch = (
        'const patch = "*** Begin Patch\\n'
        "*** Update File: /repo/docs/canon/the-spout.qmd\\n"
        "*** Update File: /repo/docs/canon/_marks/the-spout.md\\n"
        '*** End Patch"; await tools.apply_patch(patch);'
    )
    path = _write(tmp_path / "rollout.jsonl", [_meta(), _exec(patch)])
    body, stats = codex.filter_session(path)
    assert stats["edits"] == len(codex.edit_paths(path)) == 2
    assert "[Edit the-spout.qmd]" in body and "[Edit the-spout.md]" in body


def test_normalize_keeps_provenance_signal_and_drops_runtime_transport(tmp_path):
    meta = _meta()
    meta["payload"]["base_instructions"] = "runtime instructions to omit"
    reasoning = {
        "timestamp": "2026-08-17T22:20:01Z",
        "type": "response_item",
        "payload": {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "Check the seam."}],
            "content": [{"type": "reasoning_text", "text": "Inspect both sides."}],
            "encrypted_content": "opaque-ciphertext",
        },
    }
    patch = (
        'const patch = "*** Begin Patch\\n'
        "*** Update File: /repo/docs/canon/the-spout.qmd\\n"
        '*** End Patch"; await tools.apply_patch(patch);'
    )
    call = _exec(patch)
    call["payload"]["call_id"] = "call-1"
    output = {
        "timestamp": "2026-08-17T22:20:02Z",
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output",
            "call_id": "call-1",
            "output": [{"type": "input_image", "image_url": "file-bytes-to-omit"}],
        },
    }
    rows = [
        meta,
        _message("user", "# AGENTS.md instructions for /repo\n\nInjected."),
        _message("user", "Please sharpen the spout argument."),
        reasoning,
        call,
        output,
        _message("assistant", "The seam is now explicit."),
        {
            "timestamp": "2026-08-17T22:20:03Z",
            "type": "event_msg",
            "payload": {"type": "item_completed", "item": "duplicate transport"},
        },
        {
            "timestamp": "2026-08-17T22:20:04Z",
            "type": "compacted",
            "payload": {"replacement_history": ["duplicate history"]},
        },
        {
            "timestamp": "2026-08-17T22:20:05Z",
            "type": "world_state",
            "payload": {"state": "runtime state"},
        },
    ]
    raw = _write(tmp_path / "raw.jsonl", rows)
    before_body, before_stats = codex.filter_session(raw)
    before_records = codex.records(raw)
    before_edits = codex.edit_paths(raw)
    source = raw.read_bytes()

    normalized = tmp_path / "normalized.jsonl"
    normalized.write_bytes(codex.normalize_capture(raw))

    assert codex.filter_session(normalized) == (before_body, before_stats)
    assert codex.records(normalized) == before_records
    assert codex.edit_paths(normalized) == before_edits
    assert codex.records(normalized, thinking=True)[-2][1] == (
        "[reasoning summary]\nCheck the seam.\n\n[reasoning]\nInspect both sides."
    )
    captured = normalized.read_text()
    for omitted in (
        "runtime instructions to omit",
        "opaque-ciphertext",
        "file-bytes-to-omit",
        "duplicate transport",
        "duplicate history",
        "runtime state",
    ):
        assert omitted not in captured
    meta_out = codex.session_meta(normalized)
    assert meta_out["capture_schema"] == codex.CAPTURE_SCHEMA
    assert meta_out["source_bytes"] == len(source)
    assert meta_out["source_sha256"] == hashlib.sha256(source).hexdigest()


def test_normalize_keeps_interactive_human_answer_not_other_tool_output(tmp_path):
    question = _exec(
        'await tools.request_user_input({"questions":[{"question":"Keep it?"}]});'
    )
    question["payload"]["call_id"] = "interactive"
    answer = {
        "timestamp": "2026-08-17T22:20:01Z",
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output",
            "call_id": "interactive",
            "output": [{"type": "input_text", "text": "Keep it."}],
        },
    }
    ordinary = _exec('await tools.exec_command({"cmd":"git status"});')
    ordinary["payload"]["call_id"] = "ordinary"
    ordinary_output = {
        "timestamp": "2026-08-17T22:20:02Z",
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output",
            "call_id": "ordinary",
            "output": "large command output",
        },
    }
    raw = _write(
        tmp_path / "raw.jsonl",
        [
            _meta(),
            question,
            answer,
            ordinary,
            ordinary_output,
        ],
    )
    raw_records = codex.records(raw)
    raw_render = codex.filter_session(raw)
    normalized = tmp_path / "normalized.jsonl"
    normalized.write_bytes(codex.normalize_capture(raw))

    assert codex.records(normalized) == raw_records
    assert codex.filter_session(normalized) == raw_render
    assert [
        text for role, text, _stamp in codex.records(normalized) if role == "human"
    ] == ["Keep it."]
    body, stats = codex.filter_session(normalized)
    assert "[request_user_input]" in body
    assert "### Human\n\nKeep it." in body
    assert "large command output" not in normalized.read_text()
    assert stats["human_turns"] == 1


def test_quoted_interactive_tool_name_does_not_make_output_human(tmp_path):
    """A patch can mention a tool without the outer exec having called it."""
    patch = (
        'const patch = "*** Begin Patch\\n'
        "*** Update File: tests/test_example.py\\n"
        '+await tools.request_user_input({question: \\"Keep it?\\"});\\n'
        '*** End Patch"; await tools.apply_patch(patch);'
    )
    call = _exec(patch)
    call["payload"]["call_id"] = "patch-call"
    output = {
        "timestamp": "2026-08-17T22:20:02Z",
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output",
            "call_id": "patch-call",
            "output": "Success. Updated tests/test_example.py",
        },
    }
    raw = _write(tmp_path / "raw.jsonl", [_meta(), call, output])
    normalized = tmp_path / "normalized.jsonl"
    normalized.write_bytes(codex.normalize_capture(raw))

    assert codex._tool_names(call["payload"]) == ["apply_patch"]
    assert codex.records(normalized) == []
    assert "Success. Updated" not in normalized.read_text()


def test_nested_tool_lexer_honors_comments_and_template_expressions():
    source = (
        "// tools.request_user_input({})\n"
        'const sample = "tools.request_user_input({})"; '
        "const shown = `tools.request_user_input({}) ${tools.exec_command({})}`;"
    )
    assert codex._nested_tool_names(source) == ["exec_command"]


def test_normalize_keeps_inter_agent_messages_that_no_reader_renders(tmp_path):
    """The one payload kept for the archive rather than for a render.

    Dropping a subagent's report at *import* is a curation call made where it
    cannot be revisited: the rollout leaves `~/.codex`, and the capture is then
    the only copy. Every reader drops it anyway -- they dispatch on an allowlist
    of payload kinds -- so keeping it changes nothing downstream and leaves the
    call with the reader, which is where Claude Code's corpus already makes it.
    """
    path = _write(
        tmp_path / "rollout.jsonl",
        [
            _meta(),
            _message("user", "Audit the brief."),
            {
                "timestamp": "2026-08-17T22:20:00Z",
                "type": "response_item",
                "payload": {
                    "type": "agent_message",
                    "id": "amsg_0000",
                    "author": "/root",
                    "recipient": "/root/brief_audit",
                    "internal_chat_message_metadata_passthrough": {"opaque": "state"},
                    "content": [
                        {"type": "input_text", "text": "Message Type: NEW_TASK"},
                        {"type": "encrypted_content", "encrypted_content": "gAAAA…"},
                    ],
                },
            },
            {
                "timestamp": "2026-08-17T22:21:00Z",
                "type": "response_item",
                "payload": {
                    "type": "agent_message",
                    "author": "/root/brief_audit",
                    "recipient": "/root",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "FINAL_ANSWER\nThe brief is strong.",
                        }
                    ],
                },
            },
            _message("assistant", "Relaying the verdict."),
        ],
    )
    rows = [
        json.loads(line) for line in codex.normalize_capture(path).decode().splitlines()
    ]
    agent = [r["payload"] for r in rows if r["payload"].get("type") == "agent_message"]
    assert len(agent) == 2
    assert agent[0]["author"] == "/root"
    assert agent[0]["recipient"] == "/root/brief_audit"
    # the opaque halves go, like every other blob in this module
    assert agent[0]["content"] == [
        {"type": "input_text", "text": "Message Type: NEW_TASK"}
    ]
    assert "id" not in agent[0]
    assert "internal_chat_message_metadata_passthrough" not in agent[0]
    assert "The brief is strong." in agent[1]["content"][0]["text"]

    # and nothing a reader produces moves because of them
    capture = _write(tmp_path / "capture.jsonl", [])
    capture.write_bytes(codex.normalize_capture(path))
    assert [r[0] for r in codex.records(str(capture))] == ["human", "assistant"]
    assert codex.human_records(str(capture)) == [
        ("human", "Audit the brief.", "2026-08-17T22:19:00Z")
    ]
    body, stats = codex.filter_session(str(capture))
    assert "brief is strong" not in body
    assert (stats["human_turns"], stats["assistant_turns"]) == (1, 1)
