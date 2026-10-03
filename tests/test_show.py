"""show unit tests — pure logic, synthetic sessions, no private corpus.

Exercises the cc/web turn extraction (tool calls and tool-result echoes dropped,
thinking gated, harness wrappers and meta/sidechain handled), id resolution
(prefix match, ambiguity, miss, direct path), and rendering (role filter,
header), all on tmp_path fixtures with CC/CONV monkeypatched off the real corpus.
"""

from conftest import keep_session
import json

from quirework.provenance import show


def _jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _assistant(*blocks, **extra):
    return {"type": "assistant", "message": {"content": list(blocks)}, **extra}


def _user(content, **extra):
    return {"type": "user", "message": {"content": content}, **extra}


def _roles(records):
    """Drop the timestamp column so tests can assert on (role, text) pairs."""
    return [(r, t) for r, t, _ in records]


def _text(s):
    return {"type": "text", "text": s}


# --------------------------------------------------------------------------- #
# cc_records
# --------------------------------------------------------------------------- #
def test_cc_records_keeps_prose_drops_tools(tmp_path):
    p = _jsonl(
        tmp_path / "s.jsonl",
        [
            _user("what books should I read?"),
            _assistant(
                {"type": "thinking", "thinking": "secret reasoning", "signature": "x"},
                _text("Read Seth first."),
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            ),
            # a tool-result echo arrives as a user turn with *list* content
            _user([{"type": "tool_result", "content": "file listing"}]),
            _assistant(_text("Then Dehaene.")),
        ],
    )
    recs = show.cc_records(p)
    assert _roles(recs) == [
        ("human", "what books should I read?"),
        ("assistant", "Read Seth first."),  # thinking + tool_use stripped
        ("assistant", "Then Dehaene."),  # tool_result echo skipped
    ]


def test_cc_records_thinking_gated(tmp_path):
    p = _jsonl(
        tmp_path / "s.jsonl",
        [
            _assistant(
                {
                    "type": "thinking",
                    "thinking": "I should sequence the spine.",
                    "signature": "x",
                },
                _text("Here is the list."),
            ),
        ],
    )
    assert _roles(show.cc_records(p)) == [("assistant", "Here is the list.")]
    with_thinking = show.cc_records(p, thinking=True)
    assert _roles(with_thinking) == [
        ("assistant", "[thinking]\nI should sequence the spine.\n\nHere is the list."),
    ]


def test_cc_records_skips_meta_and_sidechain_unless_raw(tmp_path):
    p = _jsonl(
        tmp_path / "s.jsonl",
        [
            _user("/clear", isMeta=True),
            _user("real question"),
            _assistant(_text("subagent chatter"), isSidechain=True),
            _assistant(_text("real answer")),
        ],
    )
    assert _roles(show.cc_records(p)) == [
        ("human", "real question"),
        ("assistant", "real answer"),
    ]
    raw = _roles(show.cc_records(p, raw=True))
    assert ("harness", "/clear") in raw  # raw keeps the meta turn, as harness
    assert ("assistant", "subagent chatter") in raw


def test_cc_records_tolerates_bad_lines(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(
        "not json\n" + json.dumps(_user("hi there friend")) + "\n", encoding="utf-8"
    )
    assert _roles(show.cc_records(p)) == [("human", "hi there friend")]


# --------------------------------------------------------------------------- #
# web_records
# --------------------------------------------------------------------------- #
def test_web_records_both_speakers(tmp_path):
    p = tmp_path / "w.raw.json"
    p.write_text(
        json.dumps(
            {
                "chat_messages": [
                    {
                        "sender": "human",
                        "content": [{"type": "text", "text": "is it raining?"}],
                    },
                    {
                        "sender": "assistant",
                        "content": [{"type": "text", "text": "depends on the loop."}],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    assert _roles(show.web_records(p)) == [
        ("human", "is it raining?"),
        ("assistant", "depends on the loop."),
    ]


# --------------------------------------------------------------------------- #
# resolve
# --------------------------------------------------------------------------- #
def _keep(project, name, rows, *, pending=False):
    text = "\n".join(json.dumps(r) for r in rows) + "\n"
    return keep_session(project, f"cc-sessions/{name}", text, pending=pending)


def test_resolve_prefix_match(corpus_project):
    f = _keep(corpus_project, "3c3c3c3c-5c02-dead-beef.jsonl", [_user("hi")])
    assert show.resolve("3c3c3c3c") == ("cc", f)


def test_resolve_ambiguous_raises(corpus_project):
    _keep(corpus_project, "ab12-one.jsonl", [_user("a")])
    _keep(corpus_project, "ab12-two.jsonl", [_user("b")])
    try:
        show.resolve("ab12")
        assert False, "expected ambiguity"
    except LookupError as e:
        assert "ambiguous" in str(e)


def test_resolve_miss_raises(corpus_project):
    try:
        show.resolve("nope")
        assert False, "expected miss"
    except LookupError as e:
        assert "no session" in str(e)


def test_a_pending_capture_is_read_before_it_is_accepted(corpus_project):
    f = _keep(corpus_project, "5e5e5e5e-pending.jsonl", [_user("hi")], pending=True)
    assert show.resolve("5e5e5e5e") == ("cc", f)
    assert "· pending, not accepted>" in show.render("cc", f)
    assert ("cc", f) not in show.sessions()


def test_a_grown_session_is_read_at_its_pending_capture(corpus_project):
    accepted = _keep(corpus_project, "6f6f6f6f-grown.jsonl", [_user("hi")])
    grown = _keep(
        corpus_project,
        "6f6f6f6f-grown.jsonl",
        [_user("hi"), _user("more")],
        pending=True,
    )
    assert show.resolve("6f6f6f6f") == ("cc", grown)
    assert ("cc", accepted) in show.sessions()


def test_resolve_direct_path(tmp_path):
    f = _jsonl(tmp_path / "x.jsonl", [_user("hi")])
    assert show.resolve(str(f)) == ("cc", f)


def test_resolve_direct_codex_path_by_session_meta(tmp_path):
    f = _jsonl(
        tmp_path / "rollout.jsonl",
        [
            {
                "type": "session_meta",
                "payload": {"id": "0c0dec0d-0000-7000-8000-000000000000"},
            }
        ],
    )
    assert show.resolve(str(f)) == ("codex", f)


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
def test_render_header_and_role_filter(tmp_path):
    p = _jsonl(
        tmp_path / "s.jsonl",
        [
            _user("ask"),
            _assistant(_text("answer")),
            _user("again"),
        ],
    )
    full = show.render("cc", p)
    assert full.splitlines()[0].startswith("# ")  # header line
    assert "## Human" in full and "## Assistant" in full
    only_human = show.render("cc", p, role="human")
    assert "## Assistant" not in only_human
    assert only_human.count("## Human") == 2


# --------------------------------------------------------------------------- #
# timestamps: extraction, stamped render + span header, --from/--to window
# --------------------------------------------------------------------------- #
def test_cc_records_carries_timestamp(tmp_path):
    p = _jsonl(
        tmp_path / "s.jsonl",
        [
            _user("hello", timestamp="2026-06-14T15:47:04Z"),
            _assistant(_text("hi"), timestamp="2026-06-14T15:47:06Z"),
        ],
    )
    assert show.cc_records(p) == [
        ("human", "hello", "2026-06-14T15:47:04Z"),
        ("assistant", "hi", "2026-06-14T15:47:06Z"),
    ]


def test_web_records_carries_timestamp(tmp_path):
    p = tmp_path / "w.raw.json"
    p.write_text(
        json.dumps(
            {
                "chat_messages": [
                    {
                        "sender": "human",
                        "created_at": "2026-02-15T15:36:46Z",
                        "content": [{"type": "text", "text": "hi"}],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    assert show.web_records(p) == [("human", "hi", "2026-02-15T15:36:46Z")]


def _ts_session(tmp_path):
    """Four stamped cc turns, same UTC day, ~6 minutes apart end to end."""
    return _jsonl(
        tmp_path / "s.jsonl",
        [
            _user("first", timestamp="2026-06-14T15:47:04Z"),
            _assistant(_text("reply one"), timestamp="2026-06-14T15:48:10Z"),
            _user("second", timestamp="2026-06-14T15:52:10Z"),
            _assistant(_text("reply two"), timestamp="2026-06-14T15:53:00Z"),
        ],
    )


def test_render_stamps_turns_and_span(tmp_path):
    out = show.render("cc", _ts_session(tmp_path), utc=True)
    # first turn carries the full date; same-day turns are time-only, and
    # only the author's turns are numbered
    assert "## Human 1 · 2026-06-14 15:47:04" in out
    assert "## Assistant · 15:48:10" in out
    assert "## Human 2 · 15:52:10" in out
    # header states span + duration + turn count (UTC, so machine-independent)
    assert "2026-06-14 15:47 → 15:53 UTC" in out
    assert "5m56s" in out
    assert "4 turns" in out


def test_render_from_to_slice(tmp_path):
    out = show.render(
        "cc", _ts_session(tmp_path), utc=True, frm="15:48:10", to="15:52:10"
    )
    assert "reply one" in out and "second" in out  # the sliced range
    assert "first" not in out and "reply two" not in out  # trimmed off both ends
    assert "· slice" in out and "2 turns" in out


def test_render_from_accepts_the_first_turns_dated_stamp(tmp_path):
    # the first turn's stamp carries the date, and still reads back as a bound
    out = show.render("cc", _ts_session(tmp_path), utc=True, frm="2026-06-14 15:47:04")
    assert out.count("## ") == 4


def test_render_window_needs_no_turn_at_its_bounds(tmp_path):
    # bounds are times, not labels: no turn was logged at midnight or 15:50
    out = show.render("cc", _ts_session(tmp_path), utc=True, frm="00:00", to="15:50")
    assert "first" in out and "reply one" in out
    assert "second" not in out


def test_render_window_past_the_session_raises(tmp_path):
    try:
        show.render("cc", _ts_session(tmp_path), utc=True, frm="16:00:00")
        assert False, "expected an empty window"
    except LookupError as e:
        assert "no shown turn falls in the window" in str(e)


def test_render_malformed_bound_raises(tmp_path):
    try:
        show.render("cc", _ts_session(tmp_path), utc=True, frm="99:99:99")
        assert False, "expected a refusal"
    except LookupError as e:
        assert "--from" in str(e) and "is not a time" in str(e)


def test_render_empty_range_raises(tmp_path):
    # --from after --to -> empty
    try:
        show.render(
            "cc", _ts_session(tmp_path), utc=True, frm="15:52:10", to="15:48:10"
        )
        assert False, "expected empty range"
    except LookupError as e:
        assert "empty range" in str(e)
