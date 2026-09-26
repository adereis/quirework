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
                content="sender=system content=Subagent 8da2f7cc has gone idle. "
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
