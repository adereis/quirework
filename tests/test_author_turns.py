"""The author's turns a Claude Code session logs in shapes a reader can miss.

A prompt typed while the model is working reaches the model inside the running
turn and is logged only as queue operations. An answer to a question the model
asked is logged as a tool result. A prompt sent with a pasted image is logged
as a list of blocks, the shape of a tool result. A command that sends the model
a prompt, a skill and its arguments, is logged as nothing but the harness's
wrappers. Every reader that counts the author's turns must find all four, agree
on their numbers, and take from an answer only the words the author typed.
"""

import json

import pytest

from conftest import corpus_settings, keep_session
from quirework.project import Project, project_scope
from quirework.provenance import ccfilter, humaninput, metrics, show

ASK = "toolu_kettle_ask"
QUESTIONS = {
    "questions": [
        {
            "question": "Copper or steel?",
            "options": [
                {"label": "Copper", "description": "warm"},
                {"label": "Steel", "description": "cold"},
            ],
        },
        {
            "question": "How loud?",
            "options": [
                {"label": "Soft", "description": ""},
                {"label": "Shrill", "description": ""},
            ],
        },
    ]
}
ANSWER = (
    'Your questions have been answered: "Copper or steel?"="Copper", '
    '"How loud?"="Loud enough to wake the cat" notes: and keep it short. '
    "You can now continue with these answers in mind."
)
NOTICE = "<task-notification>\n<task-id>b1</task-id>\n</task-notification>"
INTERRUPT = "[Request interrupted by user]"
SKILL = (
    "<command-message>kettle-review</command-message>\n"
    "<command-name>/kettle-review</command-name>\n"
    "<command-args>discuss: only the lid</command-args>"
)
BUILTIN = (
    "<command-name>/model</command-name>\n"
    "<command-message>model</command-message>\n"
    "<command-args></command-args>"
)
LOADED = (
    "<command-message>pot-reference</command-message>\n"
    "<command-name>pot-reference</command-name>\n"
    "<skill-format>true</skill-format>"
)


def _queue(op, at, content=None, **extra):
    record = {"type": "queue-operation", "operation": op, "timestamp": at, **extra}
    if content is not None:
        record["content"] = content
    return record


SESSION = [
    {
        "type": "user",
        "uuid": "u1",
        "timestamp": "2031-04-03T10:00:00Z",
        "message": {"content": "Write the kettle chapter."},
    },
    # Sent between turns: queued, dequeued, then logged as a user record.
    _queue("enqueue", "2031-04-03T10:00:05Z", "Start with the whistle."),
    _queue("dequeue", "2031-04-03T10:00:05Z"),
    {
        "type": "user",
        "uuid": "u2",
        "timestamp": "2031-04-03T10:00:06Z",
        "message": {"content": "Start with the whistle."},
    },
    {
        "type": "assistant",
        "uuid": "a1",
        "timestamp": "2031-04-03T10:01:00Z",
        "message": {
            "content": [
                {"type": "text", "text": "Which pot?"},
                {
                    "type": "tool_use",
                    "id": ASK,
                    "name": "AskUserQuestion",
                    "input": QUESTIONS,
                },
            ]
        },
    },
    {
        "type": "user",
        "uuid": "u3",
        "timestamp": "2031-04-03T10:02:00Z",
        "message": {
            "content": [{"type": "tool_result", "tool_use_id": ASK, "content": ANSWER}]
        },
    },
    # Typed mid-turn, and a task notice queued beside it.
    _queue("enqueue", "2031-04-03T10:03:00Z", "Actually, make the kettle blue."),
    _queue("enqueue", "2031-04-03T10:03:10Z", NOTICE),
    _queue("remove", "2031-04-03T10:03:30Z", NOTICE, reason="absorbed_mid_turn"),
    _queue(
        "remove",
        "2031-04-03T10:03:40Z",
        "Actually, make the kettle blue.",
        reason="absorbed_mid_turn",
    ),
    # Pulled back into the input box, never delivered.
    _queue("enqueue", "2031-04-03T10:04:00Z", "A thought taken back."),
    _queue("popAll", "2031-04-03T10:04:05Z", "A thought taken back."),
    # Delivered mid-turn by an older release, which logged no reason.
    _queue("enqueue", "2031-04-03T10:05:00Z", "And a lid."),
    _queue("remove", "2031-04-03T10:05:20Z", "And a lid."),
    {
        "type": "assistant",
        "uuid": "a2",
        "timestamp": "2031-04-03T10:06:00Z",
        "message": {"content": [{"type": "text", "text": "A blue copper kettle."}]},
    },
    # Sent with a pasted image, whose bytes the capture drops, and followed by
    # the harness's own note of where it saved the picture.
    {
        "type": "user",
        "uuid": "u4",
        "timestamp": "2031-04-03T10:07:00Z",
        "message": {
            "content": [
                {"type": "text", "text": "[Image #1] The spout looks bent here."},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png"},
                },
            ]
        },
    },
    {
        "type": "user",
        "uuid": "u5",
        "isMeta": True,
        "timestamp": "2031-04-03T10:07:00Z",
        "message": {
            "content": [{"type": "text", "text": "[Image: source: kettle.png]"}]
        },
    },
    # The harness's note of an interrupt, logged as a list too.
    {
        "type": "user",
        "uuid": "u6",
        "timestamp": "2031-04-03T10:07:30Z",
        "message": {"content": [{"type": "text", "text": INTERRUPT}]},
    },
    # A skill typed with its arguments: the harness logs the command as its
    # wrappers alone, then sends the model the skill as a meta record.
    {
        "type": "user",
        "uuid": "u7",
        "timestamp": "2031-04-03T10:08:00Z",
        "message": {"content": SKILL},
    },
    {
        "type": "user",
        "uuid": "u8",
        "parentUuid": "u7",
        "isMeta": True,
        "timestamp": "2031-04-03T10:08:00Z",
        "message": {"content": [{"type": "text", "text": "Review the kettle."}]},
    },
    # A command the harness answers alone; the model never sees it.
    {
        "type": "user",
        "uuid": "u9",
        "timestamp": "2031-04-03T10:09:00Z",
        "message": {"content": BUILTIN},
    },
    {
        "type": "user",
        "uuid": "u10",
        "parentUuid": "u9",
        "timestamp": "2031-04-03T10:09:00Z",
        "message": {
            "content": "<local-command-stdout>Set model to Kettle</local-command-stdout>"
        },
    },
    # A skill the model loaded for itself, logged as the harness's own.
    {
        "type": "user",
        "uuid": "u11",
        "isMeta": True,
        "timestamp": "2031-04-03T10:10:00Z",
        "message": {"content": LOADED},
    },
    {
        "type": "user",
        "uuid": "u12",
        "parentUuid": "u11",
        "isMeta": True,
        "timestamp": "2031-04-03T10:10:00Z",
        "message": {"content": [{"type": "text", "text": "How pots are made."}]},
    },
]
NAME = "0badc0de-0000-4000-8000-0000000000aa.jsonl"
TURNS = [
    "Write the kettle chapter.",
    "Start with the whistle.",
    '"Copper or steel?"="Copper", "How loud?"="Loud enough to wake the cat"'
    " notes: and keep it short",
    "Actually, make the kettle blue.",
    "And a lid.",
    "[Image #1] The spout looks bent here.",
    "/kettle-review discuss: only the lid",
]


@pytest.fixture
def session(tmp_path):
    project = Project.from_settings(tmp_path, {"provenance": corpus_settings()})
    path = keep_session(
        project,
        f"cc-sessions/{NAME}",
        "\n".join(json.dumps(r) for r in SESSION) + "\n",
    )
    with project_scope(project):
        yield path


def test_show_numbers_every_author_turn(session):
    turns = show.author_turns("cc", session)
    assert [text for _, _, text in turns] == TURNS
    stamps = [logged.strftime("%H:%M:%S") for _, logged, _ in turns]
    assert stamps[3:5] == ["10:03:00", "10:05:00"], "when he typed, not delivery"


def test_raw_shows_a_listed_notice_as_the_harness(session):
    records = [(role, text) for role, text, _ in show.cc_records(session, raw=True)]
    assert ("harness", INTERRUPT) in records
    assert ("human", TURNS[5]) in records


def test_typed_text_reads_each_block_on_its_own():
    content = [
        {"type": "text", "text": INTERRUPT},
        {"type": "text", "text": "Make it whistle."},
        {"type": "tool_result", "tool_use_id": ASK, "content": "boiled"},
    ]
    assert ccfilter.typed_text(content) == "Make it whistle."
    assert ccfilter.typed_text(content, raw=True) == f"{INTERRUPT}\n\nMake it whistle."
    assert ccfilter.typed_text("A kettle boils.") == "A kettle boils."
    assert ccfilter.typed_text(None) == ""


def test_an_answer_gives_only_the_words_the_author_typed():
    block = {"type": "tool_result", "tool_use_id": ASK, "content": ANSWER}
    assert ccfilter.answer_words(block, QUESTIONS) == (
        "Loud enough to wake the cat\n\nand keep it short"
    )
    picked = dict(
        block,
        content=ANSWER.replace("Loud enough to wake the cat", "Soft").replace(
            " notes: and keep it short", ""
        ),
    )
    assert ccfilter.answer_words(picked, QUESTIONS) == ""


def test_the_human_input_stream_numbers_turns_as_show_does(session):
    turns = [t for t in humaninput.all_turns() if t["source"] == "cc"]
    assert [(t["ordinal"], t["text"]) for t in turns] == [
        (1, TURNS[0]),
        (2, TURNS[1]),
        (3, "Loud enough to wake the cat\n\nand keep it short"),
        (4, TURNS[3]),
        (5, TURNS[4]),
        (6, TURNS[5]),
        (7, TURNS[6]),
    ]


def test_the_cold_read_render_shows_mid_turn_prompts(session):
    text, stats = ccfilter.filter_session(session)
    assert "### Human\n\nActually, make the kettle blue." in text
    assert "### Human\n\nAnd a lid." in text
    assert f"### Human\n\n{TURNS[5]}" in text
    assert "task-id" not in text and "taken back" not in text
    assert INTERRUPT not in text and "kettle.png" not in text
    # The blind render keeps dropping command lines, so its version and the
    # reports cached from it stand.
    assert "kettle-review" not in text and "only the lid" not in text
    assert stats["human_turns"] == 6


def test_metrics_count_every_author_turn_once(session):
    acc = metrics.Acc("cc")
    metrics.claude_code(acc)
    assert acc.human_turns == 7
    typed = [
        TURNS[0],
        TURNS[1],
        "Loud enough to wake the cat\n\nand keep it short",
        TURNS[3],
        TURNS[4],
        TURNS[5],
        TURNS[6],
    ]
    assert acc.human_words == sum(metrics.words(t) for t in typed)
    audit = [text for tag, text in metrics.human_turns() if tag.startswith("cc/")]
    assert audit == typed


def test_each_turn_says_how_it_reached_the_log(session):
    assert show.turn_origins("cc", session) == [
        "typed",
        "typed",
        "answer",
        "queued",
        "queued",
        "listed",
        "command",
    ]


def test_a_command_is_a_turn_only_when_it_prompted_the_model():
    assert ccfilter.command_line(SKILL) == "/kettle-review discuss: only the lid"
    assert ccfilter.command_line(BUILTIN) == "/model"
    assert ccfilter.command_line(LOADED) == ""
    assert ccfilter.command_line("Run /kettle-review on it.") == ""
    assert ccfilter.prompted_commands(SESSION) == {"u7"}


def test_raw_keeps_a_prompted_command_as_logged(session):
    records = [(role, text) for role, text, _ in show.cc_records(session, raw=True)]
    assert ("human", SKILL) in records
    assert not any(text == BUILTIN for role, text in records if role == "human")
