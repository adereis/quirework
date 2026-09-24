import json
import re
import time

import pytest

from quirework.provenance import show


def _session(tmp_path, turns):
    """A cc-session JSONL from ``(role, text, timestamp)`` turns."""
    lines = []
    for role, text, ts in turns:
        if role == "human":
            rec = {"type": "user", "message": {"content": text}}
        else:
            content = [{"type": "text", "text": text}]
            rec = {"type": "assistant", "message": {"content": content}}
        if ts is not None:
            rec["timestamp"] = ts
        lines.append(rec)
    p = tmp_path / "0badc0de-0000-4000-8000-000000000000.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in lines) + "\n", encoding="utf-8")
    return p


def _render(path, **kw):
    return show.render("cc", path, utc=True, **kw)


def _heads(text):
    return re.findall(r"^## .*$", text, re.MULTILINE)


ONE_MORNING = [
    ("human", "The kettle scene drags.", "2031-04-02T10:00:05.000Z"),
    ("assistant", "Cut the second whistle.", "2031-04-02T10:02:00.900Z"),
    ("human", "Done. Now the lighthouse.", "2031-04-02T10:05:30.000Z"),
    ("assistant", "Open on the lamp.", "2031-04-02T10:07:00.000Z"),
]

ACROSS_MIDNIGHT = [
    ("human", "Late pass on chapter two.", "2031-04-02T23:50:00Z"),
    ("assistant", "The ending lands.", "2031-04-02T23:55:00Z"),
    ("human", "And the epigraph?", "2031-04-03T00:10:00Z"),
    ("assistant", "Keep it.", "2031-04-03T00:15:00Z"),
]


OVER_TWO_DAYS = [
    ("human", "Begin the long revision.", "2031-04-02T03:28:13Z"),
    ("assistant", "Starting with the prologue.", "2031-04-02T03:30:00Z"),
    ("human", "Back after sleep.", "2031-04-03T01:00:00Z"),
    ("assistant", "The middle holds.", "2031-04-03T02:00:00Z"),
    ("human", "Noon check.", "2031-04-03T12:00:00Z"),
    ("assistant", "Revision closed.", "2031-04-04T00:11:28Z"),
]


def test_a_bound_needs_no_turn_at_it(tmp_path):
    """--from 00:00:00 starts the day, though no turn was logged at midnight."""
    p = _session(tmp_path, ONE_MORNING)
    out = _render(p, role="human", frm="00:00:00", to="10:05:30")
    assert _heads(out) == ["## Human 1 · 2031-04-02 10:00:05", "## Human 2 · 10:05:30"]
    out = _render(p, frm="10:01", to="10:06")
    assert "Cut the second whistle." in out and "Done." in out
    assert "The kettle" not in out and "Open on the lamp." not in out


def test_the_first_turn_shown_is_reached_by_its_bare_time(tmp_path):
    """Its stamp prints with the date; the bare time still reaches it."""
    p = _session(tmp_path, ONE_MORNING)
    out = _render(p, role="assistant", frm="10:02:00", to="10:02:00")
    assert _heads(out) == ["## Assistant · 2031-04-02 10:02:00"]


def test_a_bound_covers_its_whole_last_unit(tmp_path):
    """A turn logged at 10:02:00.9 is inside --to 10:02:00 and --to 10:02."""
    p = _session(tmp_path, ONE_MORNING)
    assert "Cut the second whistle." in _render(p, to="10:02:00")
    assert "Cut the second whistle." in _render(p, frm="10:02", to="10:02")
    assert "Cut the second whistle." not in _render(p, to="10:01")


def test_a_bare_time_takes_the_day_the_session_was_running(tmp_path):
    p = _session(tmp_path, ACROSS_MIDNIGHT)
    after = _render(p, frm="00:00")
    assert "And the epigraph?" in after and "Late pass" not in after
    before = _render(p, to="23:59")
    assert "The ending lands." in before and "Keep it." not in before


def test_role_does_not_change_the_day_a_time_names(tmp_path):
    """Human turns alone sit on one day; the session still crosses midnight."""
    p = _session(tmp_path, ACROSS_MIDNIGHT[:2] + ACROSS_MIDNIGHT[3:])
    out = _render(p, role="human", to="00:05")
    assert "Late pass on chapter two." in out


def test_a_bare_time_on_two_days_asks_for_the_date(tmp_path):
    p = _session(
        tmp_path,
        [
            ("human", "Start the revision.", "2031-04-02T10:00:00Z"),
            ("assistant", "Revision finished.", "2031-04-03T12:00:00Z"),
        ],
    )
    with pytest.raises(LookupError, match="'2031-04-02 11:00' or '2031-04-03 11:00'"):
        _render(p, frm="11:00")
    out = _render(p, frm="2031-04-02 11:00")
    assert "Revision finished." in out and "Start the revision." not in out


def test_the_date_suggested_is_one_the_session_was_running(tmp_path):
    """00:11:28 falls inside this session on its last two days, not its first."""
    p = _session(tmp_path, OVER_TWO_DAYS)
    with pytest.raises(LookupError) as refused:
        _render(p, to="00:11:28")
    assert "'2031-04-03 00:11:28' or '2031-04-04 00:11:28'" in str(refused.value)
    assert "2031-04-02 00:11" not in str(refused.value)


def test_a_time_that_could_name_two_days_prints_its_date(tmp_path):
    """Only 02:00:00 names one day of this session, so only it prints bare."""
    p = _session(tmp_path, OVER_TWO_DAYS)
    assert _heads(_render(p)) == [
        "## Human 1 · 2031-04-02 03:28:13",
        "## Assistant · 2031-04-02 03:30:00",
        "## Human 2 · 2031-04-03 01:00:00",
        "## Assistant · 02:00:00",
        "## Human 3 · 2031-04-03 12:00:00",
        "## Assistant · 2031-04-04 00:11:28",
    ]


@pytest.mark.parametrize("turns", [ONE_MORNING, ACROSS_MIDNIGHT, OVER_TWO_DAYS])
@pytest.mark.parametrize("role", ["all", "human"])
def test_every_printed_stamp_works_as_a_bound(tmp_path, turns, role):
    p = _session(tmp_path, turns)
    for head in _heads(_render(p, role=role)):
        stamp = head.split(" · ", 1)[1]
        assert _heads(_render(p, role=role, frm=stamp, to=stamp)) == [head]


@pytest.fixture
def three_hours_behind_utc(monkeypatch):
    monkeypatch.setenv("TZ", "Etc/GMT+3")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_bounds_are_read_in_the_shown_zone(tmp_path, three_hours_behind_utc):
    p = _session(tmp_path, ONE_MORNING)
    local = show.render("cc", p, frm="07:00", to="07:00")
    assert _heads(local) == ["## Human 1 · 2031-04-02 07:00:05"]
    with pytest.raises(LookupError, match="no shown turn falls"):
        _render(p, frm="07:00", to="07:00")


@pytest.mark.parametrize(
    "bounds, message",
    [
        (["--from", "10:06", "--to", "10:03"], "falls after --to"),
        (["--from", "11:00"], "no shown turn falls in the window from '11:00'"),
        (["--from", "10am"], "'10am' is not a time"),
    ],
)
def test_an_unusable_window_is_refused(tmp_path, capsys, bounds, message):
    p = _session(tmp_path, ONE_MORNING)
    assert show.main([str(p), "--utc", *bounds]) == 2
    assert message in capsys.readouterr().err


def test_a_turn_with_no_time_is_counted_not_hidden(tmp_path):
    p = _session(tmp_path, ONE_MORNING[:2] + [("human", "Undated aside.", None)])
    out = _render(p, frm="10:00")
    assert "Undated aside." not in out
    assert "(1 turn with no time left out)" in out


def test_a_log_with_no_times_refuses_a_window_but_still_renders(tmp_path):
    p = _session(tmp_path, [("human", "Undated aside.", None)])
    assert "Undated aside." in _render(p)
    with pytest.raises(LookupError, match="need per-turn timestamps"):
        _render(p, frm="10:00")


def _jsonl(tmp_path, name, records):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return p


CC_WITH_HARNESS = [
    {
        "type": "user",
        "timestamp": "2031-04-02T10:00:00Z",
        "message": {
            "content": "<system-reminder>Be terse.</system-reminder>\n"
            "Tighten the kettle scene."
        },
    },
    {
        "type": "user",
        "isMeta": True,
        "timestamp": "2031-04-02T10:00:01Z",
        "message": {"content": "Caveat: output of local commands follows."},
    },
    {
        "type": "user",
        "timestamp": "2031-04-02T10:00:02Z",
        "message": {"content": "<command-name>/clear</command-name>"},
    },
    {
        "type": "user",
        "isSidechain": True,
        "timestamp": "2031-04-02T10:00:03Z",
        "message": {"content": "Check chapter 3 for continuity."},
    },
    {
        "type": "assistant",
        "timestamp": "2031-04-02T10:00:04Z",
        "message": {"content": [{"type": "text", "text": "Tightened."}]},
    },
]


def test_raw_labels_each_turn_by_who_wrote_it(tmp_path):
    p = _jsonl(tmp_path, "0badc0de-0000-4000-8000-000000000001.jsonl", CC_WITH_HARNESS)
    raw = [(role, text) for role, text, _ in show.cc_records(p, raw=True)]
    assert raw == [
        ("human", CC_WITH_HARNESS[0]["message"]["content"]),
        ("harness", "Caveat: output of local commands follows."),
        ("harness", "<command-name>/clear</command-name>"),
        ("agent", "Check chapter 3 for continuity."),
        ("assistant", "Tightened."),
    ]
    clean = [(role, text) for role, text, _ in show.cc_records(p)]
    assert clean == [
        ("human", "Tighten the kettle scene."),
        ("assistant", "Tightened."),
    ]


def test_raw_human_turns_hold_only_the_authors_words(tmp_path):
    p = _jsonl(tmp_path, "0badc0de-0000-4000-8000-000000000001.jsonl", CC_WITH_HARNESS)
    human = _render(p, raw=True, role="human")
    assert _heads(human) == ["## Human 1 · 2031-04-02 10:00:00"]
    harness = _render(p, raw=True, role="harness")
    assert "Caveat:" in harness and "/clear" in harness
    assert "Tighten" not in harness


def test_agy_and_web_label_harness_text_in_the_human_slot(tmp_path):
    notice = "[Request interrupted by user]"
    agy = _jsonl(
        tmp_path,
        "agy.jsonl",
        [
            {
                "type": "USER_INPUT",
                "step_index": 0,
                "content": f"<USER_REQUEST>{notice}</USER_REQUEST>",
                "created_at": "2031-04-02T10:00:00Z",
            }
        ],
    )
    web = tmp_path / "web.raw.json"
    web.write_text(
        json.dumps(
            {
                "chat_messages": [
                    {
                        "sender": "human",
                        "content": [{"type": "text", "text": notice}],
                        "created_at": "2031-04-02T10:00:00Z",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    for reader, path in ((show.agy_records, agy), (show.web_records, web)):
        assert reader(path) == []
        assert [r[:2] for r in reader(path, raw=True)] == [("harness", notice)]


def test_a_turn_opens_with_the_replies_up_to_the_next(tmp_path):
    p = _session(tmp_path, ONE_MORNING)
    one = _render(p, turn="1")
    assert _heads(one) == [
        "## Human 1 · 2031-04-02 10:00:05",
        "## Assistant · 10:02:00",
    ]
    assert "· turn 1" in one.splitlines()[1]
    assert len(_heads(_render(p, turn="1-2"))) == 4
    assert _heads(_render(p, turn="2", role="assistant")) == ["## Assistant · 10:07:00"]


def test_harness_and_agent_text_never_takes_a_turn_number(tmp_path):
    p = _jsonl(tmp_path, "0badc0de-0000-4000-8000-000000000001.jsonl", CC_WITH_HARNESS)
    raw = _render(p, raw=True, turn="1")
    assert [h.split(" · ")[0] for h in _heads(raw)] == [
        "## Human 1",
        "## Harness context",
        "## Harness context",
        "## Agent prompt (machine-written)",
        "## Assistant",
    ]


@pytest.mark.parametrize(
    "turn, message",
    [
        ("3", "the author took 2 turns in this session"),
        ("0", "counted from 1"),
        ("2-1", "counted from 1"),
        ("two", "not a turn number"),
    ],
)
def test_a_turn_outside_the_session_is_refused(tmp_path, turn, message):
    p = _session(tmp_path, ONE_MORNING)
    with pytest.raises(LookupError, match=message):
        _render(p, turn=turn)


def test_a_turn_is_not_combined_with_a_time_window(tmp_path, capsys):
    p = _session(tmp_path, ONE_MORNING)
    assert show.main([str(p), "--turn", "1", "--from", "10:00"]) == 2
    assert "drop --from/--to" in capsys.readouterr().err


GEMINI_REVIEW = """# Google Gemini

> Provenance note for a fictitious review.

## You · #1

Is the lighthouse chapter too long?

## Gemini · #1

## Pacing

It runs long in the middle.

## You · #2

## Gemini · #2

Nothing to answer.

## You · #3

Cut the storm, then?

## Gemini · #3

Yes.
"""


def test_a_gemini_review_opens_by_turn_as_search_counts_them(tmp_path):
    """Search counts every `## You` section, the empty second one included."""
    p = tmp_path / "gemini-fictitious-review.md"
    p.write_text(GEMINI_REVIEW, encoding="utf-8")
    one = show.render("gemini", p, turn="1")
    assert "## Pacing" in one and "## You · #2" not in one
    three = show.render("gemini", p, turn="3")
    assert three.rstrip().endswith(
        "## You · #3\n\nCut the storm, then?\n\n## Gemini · #3\n\nYes."
    )
    with pytest.raises(LookupError, match="took 3 turns"):
        show.render("gemini", p, turn="4")


def test_search_and_show_number_the_same_turns(tmp_path):
    """A search hit's `turn N` is `## Human N` in show, for every source."""
    from quirework.project import current_project
    from quirework.provenance import humaninput

    project = current_project()

    def place(setting, default, name):
        folder = project.path(setting, default)
        folder.mkdir(parents=True, exist_ok=True)
        return folder / name

    cc = place(
        "provenance.claude_sessions",
        ".quirework/provenance/cc-sessions",
        "0badc0de-0000-4000-8000-000000000001.jsonl",
    )
    cc.write_text(
        "\n".join(
            json.dumps(r)
            for r in CC_WITH_HARNESS
            + [
                {
                    "type": "user",
                    "timestamp": "2031-04-02T10:05:00Z",
                    "message": {"content": "Now the storm chapter."},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    agy = place(
        "provenance.agy_sessions",
        ".quirework/provenance/agy-sessions",
        "0badc0de-0000-4000-8000-000000000002.jsonl",
    )
    agy.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {
                    "type": "USER_INPUT",
                    "step_index": 0,
                    "created_at": "2031-04-02T11:00:00Z",
                    "content": "<USER_REQUEST>[Request interrupted by user]</USER_REQUEST>",
                },
                {
                    "type": "USER_INPUT",
                    "step_index": 1,
                    "created_at": "2031-04-02T11:01:00Z",
                    "content": "<USER_REQUEST>Shorten the epigraph.</USER_REQUEST>",
                },
                {
                    "type": "PLANNER_RESPONSE",
                    "step_index": 2,
                    "created_at": "2031-04-02T11:02:00Z",
                    "content": "Shortened.",
                },
                {
                    "type": "USER_INPUT",
                    "step_index": 3,
                    "created_at": "2031-04-02T11:03:00Z",
                    "content": "<USER_REQUEST>And the title?</USER_REQUEST>",
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    codex = place(
        "provenance.codex_sessions",
        ".quirework/provenance/codex-sessions",
        "0badc0de-0000-4000-8000-000000000003.jsonl",
    )
    codex.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {
                    "timestamp": "2031-04-02T12:00:00Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "fictitious",
                        "cwd": "/book",
                        "source": "cli",
                        "timestamp": "2031-04-02T12:00:00Z",
                    },
                },
                *(
                    {
                        "timestamp": stamp,
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": role,
                            "content": [
                                {
                                    "type": "input_text"
                                    if role == "user"
                                    else "output_text",
                                    "text": text,
                                }
                            ],
                        },
                    }
                    for role, text, stamp in [
                        ("user", "Draft the storm.", "2031-04-02T12:01:00Z"),
                        ("assistant", "Drafted.", "2031-04-02T12:02:00Z"),
                        ("user", "Darker.", "2031-04-02T12:03:00Z"),
                    ]
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    web = place(
        "provenance.conversations",
        ".quirework/provenance/conversations",
        "0badc0de-web.raw.json",
    )
    web.write_text(
        json.dumps(
            {
                "chat_messages": [
                    {
                        "sender": "human",
                        "created_at": "2031-04-02T13:00:00Z",
                        "content": [
                            {
                                "type": "text",
                                "text": "<system-reminder>x</system-reminder>",
                            }
                        ],
                    },
                    {
                        "sender": "human",
                        "created_at": "2031-04-02T13:01:00Z",
                        "content": [{"type": "text", "text": "Is the ending earned?"}],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    gemini = place(
        "provenance.conversations",
        ".quirework/provenance/conversations",
        "gemini-fictitious-review.md",
    )
    gemini.write_text(GEMINI_REVIEW, encoding="utf-8")

    paths = {"cc": cc, "agy": agy, "codex": codex, "web": web, "gemini": gemini}
    hits = humaninput.all_turns()
    assert {t["source"] for t in hits} == set(paths)
    for t in hits:
        path = paths[t["source"]]
        for raw in (False, True):
            if t["source"] == "gemini":
                out = show.render("gemini", path, turn=str(t["ordinal"]), raw=raw)
                assert f"## You · #{t['ordinal']}" in out and t["text"] in out
                continue
            out = show.render(
                t["source"],
                path,
                turn=str(t["ordinal"]),
                role="human",
                raw=raw,
                utc=True,
            )
            (head,) = _heads(out)
            assert head.startswith(f"## Human {t['ordinal']} · ")
            assert t["text"] in out


def test_a_bound_that_names_its_zone_means_one_moment(tmp_path, three_hours_behind_utc):
    p = _session(tmp_path, ONE_MORNING)
    local = ["## Human 1 · 2031-04-02 07:00:05"]
    assert _heads(show.render("cc", p, frm="10:00Z", to="10:00Z")) == local
    offset = show.render("cc", p, frm="2031-04-02 12:00+02:00", to="12:00+0200")
    assert _heads(offset) == local
    in_utc = ["## Human 1 · 2031-04-02 10:00:05"]
    assert _heads(_render(p, frm="07:00-03:00", to="07:00-03:00")) == in_utc


def test_a_logged_stamp_is_read_as_its_millisecond(tmp_path):
    p = _session(tmp_path, ONE_MORNING)
    stamp = ONE_MORNING[1][2]
    assert _heads(_render(p, frm=stamp, to=stamp)) == ["## Assistant · 10:02:00"]
    with pytest.raises(LookupError, match="no shown turn falls"):
        _render(p, frm="10:02:00.8Z", to="10:02:00.8Z")


def test_a_bare_time_with_a_zone_counts_days_in_that_zone(
    tmp_path, three_hours_behind_utc
):
    """Locally the session sits on one evening; in UTC it crosses midnight."""
    p = _session(tmp_path, ACROSS_MIDNIGHT)
    out = show.render("cc", p, frm="00:00Z")
    assert "And the epigraph?" in out and "Late pass" not in out


@pytest.mark.parametrize(
    "mark", ["10:00+25:00", "25:00Z", "10:00 Z", "Z", "2031-04-02Z", "2031-04-02T"]
)
def test_a_malformed_bound_is_refused(tmp_path, mark):
    with pytest.raises(LookupError, match="is not a time"):
        _render(_session(tmp_path, ONE_MORNING), frm=mark)


def test_utc_shows_a_logged_offset_in_utc(tmp_path):
    p = _session(tmp_path, [("human", "Offset stamp.", "2031-04-02T12:00:05+02:00")])
    out = _render(p)
    assert _heads(out) == ["## Human 1 · 2031-04-02 10:00:05"]
    assert " UTC · " in out.splitlines()[1]
