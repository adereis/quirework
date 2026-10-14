"""What an Antigravity capture keeps, on synthetic records, never the corpus."""

import json

from quirework.provenance import agyfilter as agy


def _session(tmp_path, rows, name="t.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return str(p)


def _capture(tmp_path, rows):
    out = [
        json.loads(line)
        for line in agy.normalize_capture(_session(tmp_path, rows))
        .decode()
        .splitlines()
    ]
    return out[0], out[1:]


def _row(kind, content=None, **extra):
    row = {
        "step_index": extra.pop("step_index", 0),
        "source": extra.pop("source", "MODEL"),
        "type": kind,
        "status": "DONE",
        "created_at": extra.pop("created_at", "2026-08-17T22:00:00Z"),
    }
    if content is not None:
        row["content"] = content
    row.update(extra)
    return row


def test_capture_keeps_the_dialogue_and_hollows_out_the_freight(tmp_path):
    header, kept = _capture(
        tmp_path,
        [
            _row(
                "USER_INPUT",
                "<USER_REQUEST>Prune the sessions.</USER_REQUEST>",
                source="USER",
            ),
            _row(
                "PLANNER_RESPONSE",
                "Here is the plan.",
                thinking="weighing the options",
                tool_calls=[
                    {
                        "name": "view_file",
                        "args": {"AbsolutePath": "/repo/manuscript/sections/x.qmd"},
                    }
                ],
            ),
            _row("VIEW_FILE", "X" * 5000),
            _row("EPHEMERAL_MESSAGE", "harness reminder " * 200, source="SYSTEM"),
            _row(
                "DIRECTORY_RULES", "the project's own AGENTS.md " * 300, source="SYSTEM"
            ),
            _row(
                "CHECKPOINT",
                "{{ CHECKPOINT 0 }} # USER Objective: ...",
                source="SYSTEM",
            ),
        ],
    )
    assert len(kept) == 6  # every record still there
    assert kept[0]["content"].startswith("<USER_REQUEST>")
    assert kept[1]["content"] == "Here is the plan."
    assert kept[1]["thinking"] == "weighing the options"
    assert (
        kept[1]["tool_calls"][0]["args"]["AbsolutePath"]
        == "/repo/manuscript/sections/x.qmd"
    )
    for record in kept[2:]:
        assert "content" not in record  # ...but hollowed out
        assert record["created_at"] == "2026-08-17T22:00:00Z"
        assert record["type"] and record["status"] == "DONE"
    assert header["capture_schema"] == agy.CAPTURE_SCHEMA
    assert header["dropped"]["VIEW_FILE.content"] == 1
    assert header["dropped"]["DIRECTORY_RULES.content"] == 1


def test_every_record_keeps_its_timestamp(tmp_path):
    """Why the envelope stays instead of the record going.

    `catalog._agy_scan` dates a session from the first and last record carrying
    `created_at`, and `metrics` feeds every one into the session span -- neither
    filters by type. The transcript is not written in timestamp order either, so
    dropping a record outright can move a session's span at both ends. Two
    sessions in the corpus do exactly that.
    """
    _, kept = _capture(
        tmp_path,
        [
            _row("VIEW_FILE", "X" * 900, created_at="2026-08-17T09:00:00Z"),
            _row("USER_INPUT", "go", created_at="2026-08-17T10:00:00Z"),
            _row("CHECKPOINT", "replay", created_at="2026-08-17T11:00:00Z"),
        ],
    )
    assert [r["created_at"] for r in kept] == [
        "2026-08-17T09:00:00Z",
        "2026-08-17T10:00:00Z",
        "2026-08-17T11:00:00Z",
    ]


def test_the_header_carries_no_timestamp_of_its_own(tmp_path):
    """A `created_at` on the header would silently become the session's start."""
    header, _ = _capture(tmp_path, [_row("USER_INPUT", "go")])
    assert "created_at" not in header
    assert "tool_calls" not in header and "content" not in header


def test_a_subagents_relayed_words_are_kept(tmp_path):
    """Six SYSTEM_MESSAGE records in the corpus quote a subagent's own prose.

    Sixteen are truncated shell output and would go with the rest, but slicing
    the type by its `sender=` field is a content sniff on a beta runtime's
    message format -- so the type keeps its body, and no reader renders it.
    """
    _, kept = _capture(
        tmp_path,
        [
            _row(
                "SYSTEM_MESSAGE",
                source="SYSTEM",
                content="sender=system content=Subagent 8a8a8a8a has gone idle. "
                "This was the subagent's most recent response: I have "
                "completed the blind rewrite of Chapter 3.",
            ),
            _row("ERROR_MESSAGE", "quarto: command not found", error="ENOENT"),
        ],
    )
    assert "blind rewrite of Chapter 3" in kept[0]["content"]
    assert kept[1]["content"] == "quarto: command not found"


def test_a_file_body_is_cut_back_but_a_parameter_is_not(tmp_path):
    body = "\n".join(f"    line {i} of a rewritten chapter" for i in range(400))
    _, kept = _capture(
        tmp_path,
        [
            _row(
                "PLANNER_RESPONSE",
                "writing",
                tool_calls=[
                    {
                        "name": "write_to_file",
                        "args": {
                            "TargetFile": "/repo/manuscript/sections/the-sidecar.qmd",
                            "CodeContent": body,
                        },
                    },
                    {
                        "name": "run_command",
                        "args": {"CommandLine": "quarto render", "Cwd": "/repo"},
                    },
                ],
            )
        ],
    )
    args = kept[0]["tool_calls"][0]["args"]
    assert args["TargetFile"] == "/repo/manuscript/sections/the-sidecar.qmd"
    assert body.startswith(args["CodeContent"])
    assert len(args["CodeContent"]) < len(body) // 4
    # a parameter is not a payload
    assert kept[0]["tool_calls"][1]["args"] == {
        "CommandLine": "quarto render",
        "Cwd": "/repo",
    }


def test_a_capture_is_told_from_a_transcript_and_is_deterministic(tmp_path):
    raw = _session(tmp_path, [_row("USER_INPUT", "go")])
    assert agy.capture_header(raw) is None
    capture = str(tmp_path / "capture.jsonl")
    agy.write_capture(raw, capture)
    header = agy.capture_header(capture)
    assert (header["source_bytes"], header["source_sha256"]) == agy.source_fingerprint(
        raw
    )
    assert agy.is_current(raw, capture)
    assert agy.normalize_capture(raw) == open(capture, "rb").read()
    assert not list(tmp_path.glob(".capture.jsonl.*"))  # no temp left behind


def test_a_torn_line_costs_only_itself(tmp_path):
    """Five lines in the corpus are torn halves of a body with a raw newline."""
    p = tmp_path / "torn.jsonl"
    p.write_text(
        json.dumps(_row("USER_INPUT", "kept"))
        + "\n"
        + 'ontent":"the tail half of a VIEW_FILE"}\n',
        encoding="utf-8",
    )
    rows = agy.normalize_capture(str(p)).decode().splitlines()
    assert json.loads(rows[1])["content"] == "kept"
    assert json.loads(rows[0])["dropped"]["<unparseable>"] == 1


def _pair(tmp_path, full, cut):
    """A conversation's two copies, each given as lines of text or records."""
    logs = tmp_path / "conversation" / ".system_generated" / "logs"
    logs.mkdir(parents=True)
    for name, lines in ((agy.FULL, full), (agy.CUT, cut)):
        if lines is not None:
            text = "\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines)
            (logs / name).write_text(text + "\n", encoding="utf-8")
    return str(logs / agy.FULL)


def _opening(step=0):
    return _row(
        "USER_INPUT",
        "<USER_REQUEST>Draft the opening scene.</USER_REQUEST>",
        source="USER_EXPLICIT",
        step_index=step,
    )


def test_a_record_written_over_is_taken_from_the_cut_copy(tmp_path):
    """The shape that destroyed opening requests: a late step written over the
    first record, whose tail survives on the next line and does not parse."""
    late = _row("PLANNER_RESPONSE", "done", step_index=52)
    full = _pair(
        tmp_path,
        [
            late,
            'ols in the list are available."}',
            _row("VIEW_FILE", "x", step_index=1),
        ],
        [_opening(), _row("VIEW_FILE", "x", step_index=1), late],
    )
    rows, dropped, recovered = agy.records(full)
    assert recovered == [0]
    assert dropped["<unparseable>"] == 1
    assert [r["step_index"] for r in rows] == [0, 52, 1]

    header, *kept = [
        json.loads(line) for line in agy.normalize_capture(full).decode().splitlines()
    ]
    assert kept[0]["content"] == "<USER_REQUEST>Draft the opening scene.</USER_REQUEST>"
    took = header["recovered"]
    assert took["source"] == agy.CUT and took["steps"] == [0]
    assert (took["source_bytes"], took["source_sha256"]) == agy.source_fingerprint(
        agy.companion(full)
    )
    # The header still names the full copy as the source it fingerprints.
    assert (header["source_bytes"], header["source_sha256"]) == agy.source_fingerprint(
        full
    )


def test_a_recovered_step_goes_after_its_nearest_earlier_step(tmp_path):
    """Not before the first later step: the late step left at the top of the
    file would pull every recovered record up to the start."""
    rows, _, recovered = agy.records(
        _pair(
            tmp_path,
            [_row("PLANNER_RESPONSE", "late", step_index=400)]
            + [_row("VIEW_FILE", step_index=s) for s in (1, 2, 4)],
            [_row("VIEW_FILE", step_index=s) for s in (1, 2, 3, 4)],
        )
    )
    assert recovered == [3]
    assert [r["step_index"] for r in rows] == [400, 1, 2, 3, 4]


def test_a_full_copy_that_lacks_nothing_is_captured_as_before(tmp_path):
    """Step indexes skip numbers in undamaged transcripts, so a gap that
    neither copy fills is not a repair and leaves no trace in the header."""
    steps = [_opening(), _row("PLANNER_RESPONSE", "ok", step_index=2)]
    full = _pair(tmp_path, steps, steps[:1])
    header = agy.capture_header(_written(tmp_path, full))
    assert "recovered" not in header
    alone = _session(tmp_path, steps, name="alone.jsonl")
    assert (
        agy.normalize_capture(full).split(b"\n", 1)[1]
        == (agy.normalize_capture(alone).split(b"\n", 1)[1])
    )


def _written(tmp_path, source, name="capture.jsonl"):
    capture = str(tmp_path / name)
    agy.write_capture(source, capture)
    return capture


def test_a_capture_missing_a_step_its_cut_copy_holds_is_not_current(tmp_path):
    """A capture made from the full copy alone stays current by its own
    fingerprint, so the copy beside it has to be asked as well."""
    full = _pair(tmp_path, [_row("PLANNER_RESPONSE", "ok", step_index=1)], None)
    capture = _written(tmp_path, full)
    assert agy.is_current(full, capture)

    cut = tmp_path / "conversation" / ".system_generated" / "logs" / agy.CUT
    cut.write_text(
        "\n".join(json.dumps(r) for r in [_opening(), _row("VIEW_FILE", step_index=1)])
        + "\n"
    )
    assert not agy.is_current(full, capture)
    assert "holds steps the capture lacks" in agy.why_not_current(full, capture)

    repaired = _written(tmp_path, full, name="repaired.jsonl")
    assert agy.is_current(full, repaired)
    cut.write_text(cut.read_text() + json.dumps(_row("VIEW_FILE", step_index=9)) + "\n")
    assert not agy.is_current(full, repaired)
    assert "has changed" in agy.why_not_current(full, repaired)


def test_only_a_full_transcript_has_a_companion(tmp_path):
    full = _pair(tmp_path, [_opening()], [_opening()])
    assert agy.companion(full).endswith(agy.CUT)
    assert agy.companion(agy.companion(full)) is None


def test_a_renamed_format_is_not_reported_as_a_changed_session(tmp_path):
    raw = _session(tmp_path, [_row("USER_INPUT", "go")])
    capture = tmp_path / "capture.jsonl"
    agy.write_capture(raw, str(capture))
    lines = capture.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    header["capture_schema"] = "an-earlier-name-agy-v1"
    capture.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n")
    assert not agy.is_current(raw, str(capture))
    why = agy.why_not_current(raw, str(capture))
    assert "source is unchanged" in why and "an-earlier-name-agy-v1" in why
    with open(raw, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_row("USER_INPUT", "and more")) + "\n")
    assert agy.why_not_current(raw, str(capture)) == (
        "its source has changed since it was captured"
    )
