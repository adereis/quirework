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
    assert _heads(out) == ["## Human · 2031-04-02 10:00:05", "## Human · 10:05:30"]
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
        "## Human · 2031-04-02 03:28:13",
        "## Assistant · 2031-04-02 03:30:00",
        "## Human · 2031-04-03 01:00:00",
        "## Assistant · 02:00:00",
        "## Human · 2031-04-03 12:00:00",
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
    assert _heads(local) == ["## Human · 2031-04-02 07:00:05"]
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
    assert _heads(human) == ["## Human · 2031-04-02 10:00:00"]
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
