"""The spec's trail sidecar: pointers from spec sections into sessions.

Every project here is invented: a two-section spec about a kettle, one
chapter binding it, and Claude Code sessions written for the purpose. The
breadcrumbs point into those sessions by the time a turn was logged, so each
test that writes one also reads it back through the corpus, which is the
only proof a pointer is worth keeping.
"""

import json
import subprocess

import pytest

from quirework.book import notes, trail
from quirework.project import ConfigurationError, Project, project_scope
from quirework.text import mdwrap

SPEC = """# What the notebook holds

## A kettle signals without thinking {#kettle-signal}

A whistle reports a state. Reporting a state is not having a thought.

## Cooling is not the reverse of boiling {#cooling-asymmetry}

A kettle reaches the boil by one route and cools by another.
"""

SESSION = "0badc0de-0000-4000-8000-000000000001"
OTHER = "0badc0de-0000-4000-8000-000000000002"

MORNING = [
    (
        "human",
        "The kettle whistles and nobody thinks. Put that first.",
        "2031-04-02T10:00:05.120Z",
    ),
    ("assistant", "Opening on the whistle.", "2031-04-02T10:01:00Z"),
    (
        "human",
        "Cooling is a different route, not boiling backwards.",
        "2031-04-02T10:05:30.000Z",
    ),
    ("assistant", "Noted as its own section.", "2031-04-02T10:06:00Z"),
    ("human", "No, not slower. A route, not a speed.", "2031-04-02T10:09:12.500Z"),
]


def _session(directory, sid, turns):
    directory.mkdir(parents=True, exist_ok=True)
    lines = []
    for role, text, ts in turns:
        if role == "human":
            record = {"type": "user", "message": {"content": text}}
        else:
            record = {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": text}]},
            }
        record["timestamp"] = ts
        lines.append(json.dumps(record))
    path = directory / f"{sid}.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _book(tmp_path, *, provenance=True, trail_file="private/spec-trail.md", **extra):
    root = tmp_path / "book"
    sections = root / "manuscript" / "sections"
    (sections / "_marks").mkdir(parents=True)
    (root / "manuscript" / "_quarto.yml").write_text(
        "project:\n  type: book\nbook:\n  chapters:\n    - sections/opening.qmd\n"
    )
    (sections / "_spec.md").write_text(SPEC)
    (sections / "opening.qmd").write_text("# The opening {#sec-opening}\n\nA kettle.\n")
    (sections / "_marks" / "opening.md").write_text(
        "# The opening {#sec-opening}\n\n<!-- @bind(kettle-signal) -->\n"
    )
    book = {
        "source": "manuscript",
        "chapters": "manuscript/sections",
        "quarto": "manuscript/_quarto.yml",
        "spec": "manuscript/sections/_spec.md",
    }
    if trail_file:
        book["spec_trail"] = trail_file
    settings = {"book": book, **extra}
    corpus = root / "private" / "corpus"
    _session(corpus / "cc-sessions", SESSION, MORNING)
    if provenance:
        settings["provenance"] = {
            "enabled": True,
            "root": "private/corpus",
            "curation": "presence",
        }
    # Live transcripts are looked for under a harness home; point it at an
    # empty directory of the test's own, never the contributor's.
    settings.setdefault("providers", {})["claude"] = {
        "sessions": str(tmp_path / "home")
    }
    return Project.from_settings(root, settings)


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    project = _book(tmp_path)
    with project_scope(project):
        yield project


def _text(project):
    return project.path("book.spec_trail").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# writing a breadcrumb
# --------------------------------------------------------------------------- #
def test_a_phrase_the_author_typed_becomes_a_pointer_to_the_logged_time(book):
    crumb, turn = trail.add(
        "kettle-signal", "seed", find="nobody thinks", note="The opening image."
    )
    assert (crumb.harness, crumb.session) == ("claude-code", SESSION)
    assert crumb.stamp == "2031-04-02T10:00:05Z"
    assert turn.number == 1
    text = _text(book)
    assert "## A kettle signals without thinking {#kettle-signal}" in text
    assert "nobody thinks" not in text, "a breadcrumb points; it does not copy"


def test_a_turn_number_from_search_is_stored_as_its_logged_time(book):
    crumb, _ = trail.add("cooling-asymmetry", "sharpen", session="0badc0de", turn=3)
    assert crumb.stamp == "2031-04-02T10:09:12Z" and crumb.turn is None


def test_an_excerpt_must_be_words_the_turn_holds(book):
    trail.add(
        "cooling-asymmetry",
        "seed",
        session=SESSION,
        turn=2,
        excerpt="a different route",
    )
    with pytest.raises(LookupError, match="excerpt is not in"):
        trail.add(
            "cooling-asymmetry",
            "sharpen",
            session=SESSION,
            turn=3,
            excerpt="a different route",
        )


def test_a_phrase_several_turns_hold_is_refused_with_the_candidates(book):
    with pytest.raises(LookupError, match="2 author turns hold") as caught:
        trail.add("cooling-asymmetry", "seed", find="route")
    assert "Human 2" in str(caught.value) and "Human 3" in str(caught.value)


def test_the_same_turn_is_not_put_on_one_trail_twice(book):
    trail.add("kettle-signal", "seed", session=SESSION, turn=1)
    with pytest.raises(LookupError, match="already on"):
        trail.add("kettle-signal", "sharpen", find="nobody thinks")
    # The same turn may feed another section.
    trail.add("cooling-asymmetry", "probe", session=SESSION, turn=1)


def test_a_section_the_spec_does_not_hold_is_refused(book):
    with pytest.raises(
        LookupError, match="no section '#kettle-signals'.*kettle-signal"
    ):
        trail.add("kettle-signals", "seed", session=SESSION, turn=1)


def test_a_second_shared_by_two_turns_is_written_to_the_microsecond(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    project = _book(tmp_path)
    crowded = [
        ("human", "First thought.", "2031-04-02T10:00:05.100Z"),
        ("human", "Second thought, same second.", "2031-04-02T10:00:05.900Z"),
    ]
    _session(project.path("provenance.root") / "cc-sessions", OTHER, crowded)
    with project_scope(project):
        crumb, _ = trail.add("kettle-signal", "seed", find="Second thought")
        assert crumb.stamp == "2031-04-02T10:00:05.900000Z"
        assert trail.check() == 0


# --------------------------------------------------------------------------- #
# the file
# --------------------------------------------------------------------------- #
def test_the_sidecar_is_written_at_the_formatters_fixed_point(book):
    trail.add(
        "cooling-asymmetry",
        "correct",
        session=SESSION,
        turn=3,
        note="Refuses speed as the difference and keeps the route, which is the "
        "distinction the section has to state without leaning on the scene.",
        excerpt="A route, not a speed.",
    )
    text = _text(book)
    assert mdwrap.reflow_columns(text) == text
    assert all(len(line) <= 72 for line in text.splitlines())


def test_a_reflowed_breadcrumb_reads_back_the_same(book):
    """A formatter may wrap the header or run the note onto it; nothing changes."""
    squeezed = (
        "## Cooling is not the reverse of boiling {#cooling-asymmetry}\n\n"
        f"- 2031-04-02T10:09:12Z · correct · claude-code {SESSION} Refuses\n"
        "  speed as the difference.\n\n"
        "  > A route,\n  not a speed.\n"
    )
    parsed = trail.parse(squeezed, book.path("book.spec_trail"))
    assert parsed.problems == []
    (crumb,) = parsed.sections[0].crumbs
    assert crumb.note == "Refuses speed as the difference."
    assert crumb.excerpt == "A route, not a speed."
    again = trail.parse(trail.dump(parsed), book.path("book.spec_trail"))
    assert again.sections[0].crumbs[0].note == crumb.note


@pytest.mark.parametrize(
    "item, trouble",
    [
        (f"- 2031-04-02T10:00:05Z · musing · claude-code {SESSION}", "unknown kind"),
        (f"- 2031-04-02T10:00:05Z · seed · slack {SESSION}", "unknown harness"),
        (f"- turn 3 · seed · claude-code {SESSION}", "names the logged time"),
        ("- 2031-04-02T10:00:05Z · seed · gemini gemini-00ff", "logs no times"),
        (f"- 10:00:05 · seed · claude-code {SESSION}", "neither a logged UTC time"),
        ("- 2031-04-02T10:00:05Z · seed", "names its time, kind, harness and session"),
    ],
)
def test_a_breadcrumb_that_cannot_be_read_is_reported(book, item, trouble):
    text = f"## A kettle signals without thinking {{#kettle-signal}}\n\n{item}\n"
    parsed = trail.parse(text, book.path("book.spec_trail"))
    assert len(parsed.problems) == 1 and trouble in parsed.problems[0]


def test_prose_the_writer_would_drop_is_refused_rather_than_lost(book):
    text = (
        "## A kettle signals without thinking {#kettle-signal}\n\n"
        "A paragraph somebody wrote by hand.\n\n"
        "<!-- a comment in a section -->\n"
    )
    parsed = trail.parse(text, book.path("book.spec_trail"))
    assert len(parsed.problems) == 2
    _write(book, text)
    with pytest.raises(LookupError, match="problems to fix before it is rewritten"):
        trail.add("kettle-signal", "seed", session=SESSION, turn=1)


# --------------------------------------------------------------------------- #
# the gates
# --------------------------------------------------------------------------- #
def _write(project, body):
    path = project.path("book.spec_trail")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def test_a_renamed_section_breaks_the_trail_in_notes_check(book, capsys):
    trail.add("kettle-signal", "seed", session=SESSION, turn=1)
    spec = book.path("book.spec")
    spec.write_text(SPEC.replace("{#kettle-signal}", "{#whistle-signal}"))
    notes_rc = notes.check()
    err = capsys.readouterr().err
    assert notes_rc == 1
    assert "'#kettle-signal' names no spec section" in err
    assert trail.check() == 1


def test_the_check_follows_every_breadcrumb_to_its_turn(book, capsys):
    trail.add("kettle-signal", "seed", session=SESSION, turn=1, excerpt="nobody thinks")
    assert trail.check() == 0
    assert "1 breadcrumb(s)" in capsys.readouterr().out


@pytest.mark.parametrize(
    "item, trouble",
    [
        (f"- 2031-04-02T10:01:00Z · seed · claude-code {SESSION}", "no author turn at"),
        (
            f"- 2031-04-02T10:00:05Z · seed · codex {SESSION}",
            "holds this session as a claude-code",
        ),
    ],
)
def test_a_pointer_that_leads_nowhere_fails_the_check(book, capsys, item, trouble):
    # 10:01:00 is the assistant's reply: a breadcrumb names the author's words.
    _write(book, f"## A kettle signals without thinking {{#kettle-signal}}\n\n{item}\n")
    assert trail.check() == 1
    assert trouble in capsys.readouterr().err


def test_a_session_curated_out_under_a_breadcrumb_fails_the_check(book, capsys):
    trail.add("kettle-signal", "seed", session=SESSION, turn=1)
    removed = book.path("provenance.root") / "cc-sessions" / "removed-sessions.txt"
    removed.write_text(f"{SESSION}.jsonl  # not about the book\n")
    assert trail.check() == 1
    assert "curated out" in capsys.readouterr().err


def test_a_session_not_captured_yet_is_an_advisory(book, capsys):
    _write(
        book,
        "## A kettle signals without thinking {#kettle-signal}\n\n"
        f"- 2031-04-03T09:00:00Z · seed · claude-code {OTHER}\n",
    )
    assert trail.check() == 0
    err = capsys.readouterr().err
    assert "not in the corpus yet" in err and "no live transcript" in err


# --------------------------------------------------------------------------- #
# the live conversation
# --------------------------------------------------------------------------- #
def test_a_turn_in_the_live_conversation_is_found_before_capture(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    project = _book(tmp_path)
    encoded = str(project.root.resolve()).replace("/", "-").replace(".", "-")
    live = [("human", "Say the kettle never argues back.", "2031-04-05T08:00:00Z")]
    _session(tmp_path / "home" / encoded, OTHER, live)
    with project_scope(project):
        crumb, turn = trail.add("kettle-signal", "sharpen", find="never argues back")
        assert turn.live and crumb.session == OTHER
        assert trail.check() == 0
        assert "live transcript is still on this machine" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# without provenance
# --------------------------------------------------------------------------- #
def test_a_trail_is_kept_by_hand_and_read_as_pointers_without_provenance(tmp_path):
    project = _book(tmp_path, provenance=False)
    with project_scope(project):
        with pytest.raises(ConfigurationError, match="needs provenance"):
            trail.add("kettle-signal", "seed", find="nobody thinks")
        crumb, turn = trail.add(
            "kettle-signal",
            "seed",
            harness="claude.ai",
            session="5e55e55e-0000-4000-8000-00000000000a",
            at="2031-04-02T07:00:05-03:00",
        )
        assert crumb.stamp == "2031-04-02T10:00:05Z" and turn is None
        story = "\n".join(trail.story("kettle-signal"))
        assert (
            "find it: claude.ai session 5e55e55e-0000-4000-8000-00000000000a" in story
        )
        assert trail.check() == 0


def test_a_trail_without_a_spec_is_a_configuration_error(tmp_path):
    project = _book(tmp_path)
    book = {k: v for k, v in project.describe()["settings"]["book"].items()}
    del book["spec"]
    broken = Project.from_settings(project.root, {"book": book})
    with pytest.raises(ConfigurationError, match="follows the spec's sections"):
        broken.require("book")


def test_leaving_the_setting_out_turns_the_feature_off(tmp_path, capsys):
    project = _book(tmp_path, trail_file=None)
    with project_scope(project):
        assert not trail.configured() and trail.counts() == {}
        with pytest.raises(ConfigurationError, match="keeps no spec trail"):
            trail.sidecar_path()
        notes._print_spec_toc()
        assert "trail" not in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def test_a_story_reads_the_turns_oldest_first_with_where_to_open_them(book):
    trail.add(
        "cooling-asymmetry",
        "correct",
        session=SESSION,
        turn=3,
        note="Route, not speed.",
    )
    trail.add("cooling-asymmetry", "seed", session=SESSION, turn=2)
    lines = trail.story("cooling-asymmetry", utc=True)
    text = "\n".join(lines)
    assert text.index("seed") < text.index("correct")
    assert "quirework provenance show 0badc0de --turn 2" in text
    assert "opens    Cooling is a different route" in text
    assert "2 breadcrumbs from 1 session, 2031-04 · seed 1 · correct 1" in text


def test_the_primer_counts_each_sections_breadcrumbs(book, capsys):
    trail.add("kettle-signal", "seed", session=SESSION, turn=1)
    notes._print_spec_toc()
    out = capsys.readouterr().out
    assert "#kettle-signal" in out and "[trail ×1]" in out
    assert "1 of 2 section(s) have a trail" in out


def test_a_binding_says_when_its_section_has_a_trail(book, capsys):
    """Following a binding is how a reader reaches a commitment."""
    notes.main(["bind"])
    assert "(kettle-signal)" in capsys.readouterr().out
    notes.main(["bind"])
    assert "[trail" not in capsys.readouterr().out, "no breadcrumbs, no marker"

    trail.add("kettle-signal", "seed", session=SESSION, turn=1)
    notes.main(["bind"])
    assert "(kettle-signal)  [trail ×1]" in capsys.readouterr().out
    notes.main(["bind", "--by", "chapter"])
    assert "@bind(kettle-signal)  [trail ×1]" in capsys.readouterr().out


def test_a_chapter_reads_the_trails_of_the_sections_it_binds(book):
    trail.add(
        "kettle-signal", "seed", session=SESSION, turn=1, note="The opening image."
    )
    trail.add("cooling-asymmetry", "seed", session=SESSION, turn=2)
    text = "\n".join(trail.chapter_view("opening"))
    assert "#kettle-signal" in text and "The opening image." in text
    assert "#cooling-asymmetry" not in text, "opening binds only kettle-signal"


def test_a_session_says_which_sections_it_fed(book):
    trail.add("kettle-signal", "seed", session=SESSION, turn=1)
    trail.add("cooling-asymmetry", "reverse", session=SESSION, turn=3)
    text = "\n".join(trail.session_view("0badc0de"))
    assert "#kettle-signal" in text and "#cooling-asymmetry" in text
    assert "Human 3" in text


# --------------------------------------------------------------------------- #
# backfill
# --------------------------------------------------------------------------- #
def _git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "A. Fictional Author",
            "GIT_AUTHOR_EMAIL": "author@example.invalid",
            "GIT_COMMITTER_NAME": "A. Fictional Author",
            "GIT_COMMITTER_EMAIL": "author@example.invalid",
            "GIT_AUTHOR_DATE": "2031-04-02T10:10:00Z",
            "GIT_COMMITTER_DATE": "2031-04-02T10:10:00Z",
            "PATH": "/usr/bin:/bin",
            "HOME": str(root),
        },
    )


def test_suggest_follows_a_sections_commits_to_their_sessions(book):
    root = book.root
    _git(root, "init", "-q")
    _git(root, "add", "manuscript")
    _git(root, "commit", "-q", "-m", "Draft the notebook's spec")
    spec = book.path("book.spec")
    spec.write_text(SPEC.replace("cools by another.", "cools by another route."))
    message = (
        "Say cooling is a route of its own\n\n"
        "The author ruled out speed:\n\n> No, not slower. A route, not a speed.\n"
    )
    _git(root, "commit", "-q", "-am", message)
    # The session that made the commit wrote its subject into its own log.
    committing = MORNING + [
        (
            "assistant",
            'git commit -m "Say cooling is a route of its own"',
            "2031-04-02T10:09:40Z",
        )
    ]
    _session(book.path("provenance.root") / "cc-sessions", SESSION, committing)
    trail.add("cooling-asymmetry", "seed", session=SESSION, turn=2)

    text = "\n".join(trail.suggest("cooling-asymmetry", utc=True))
    assert "2 commit(s) changed this section" in text
    assert "made in claude-code 0badc0de (by the commit's subject)" in text
    assert "Human 2 · 2031-04-02 10:05  (on the trail)" in text
    assert "quoted “No, not slower. A route, not a speed.”" in text
    assert "Human 3 · 2031-04-02 10:09" in text


def test_matching_ignores_which_quotation_marks_were_typed():
    turn = "I said the “mind” was the OS, and it's the OS’s own loop."
    assert trail.contains(turn, "The 'mind' was the OS")
    assert trail.contains(turn, 'the "mind" was the OS')
    assert trail.contains(turn, "it’s the OS's own loop")


def test_a_commits_quote_loses_the_escapes_its_shell_left():
    body = 'The author asked:\n\n> "What if you simply drop \\"ordinary\\"?"\n'
    assert trail._quotes(body) == ['What if you simply drop "ordinary"?']


def _history(book, message, committing):
    """A spec edit committed with ``message``, and ``committing`` as a session."""
    root = book.root
    _git(root, "init", "-q")
    _git(root, "add", "manuscript")
    _git(root, "commit", "-q", "-m", "Draft the notebook's spec")
    spec = book.path("book.spec")
    spec.write_text(SPEC.replace("cools by another.", "cools by another route."))
    _git(root, "commit", "-q", "-am", message)
    _session(book.path("provenance.root") / "cc-sessions", SESSION, committing)


def _tool_call(command, ts):
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": command}}
            ]
        },
    }


def _write_records(book, sid, records):
    path = book.path("provenance.root") / "cc-sessions" / f"{sid}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_a_session_that_only_read_about_a_commit_did_not_make_it(book):
    _history(book, "Say cooling is a route of its own", MORNING)
    # Another session saw the subject only in a tool's output, a git log.
    reader = [
        {
            "type": "user",
            "timestamp": "2031-04-02T11:00:00Z",
            "message": {"content": "What landed today?"},
        },
        _tool_call("git log --oneline", "2031-04-02T11:00:05Z"),
        {
            "type": "user",
            "timestamp": "2031-04-02T11:00:06Z",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "content": "abc1234 Say cooling is a route of its own",
                    }
                ]
            },
        },
    ]
    _write_records(book, OTHER, reader)
    text = "\n".join(trail.suggest("cooling-asymmetry", utc=True))
    assert "made in claude-code 0badc0de" not in text, "SESSION never names it"
    assert f"made in claude-code {OTHER[:8]}" not in text


def test_the_session_that_ran_the_commit_made_it(book):
    records = [
        {
            "type": "user",
            "timestamp": "2031-04-02T10:05:30Z",
            "message": {
                "content": "Cooling is a different route, not boiling backwards."
            },
        },
        _tool_call(
            'git commit -m "Say cooling is a route of its own"', "2031-04-02T10:09:40Z"
        ),
    ]
    _history(book, "Say cooling is a route of its own", MORNING)
    _write_records(book, OTHER, records)
    text = "\n".join(trail.suggest("cooling-asymmetry", utc=True))
    assert f"made in claude-code {OTHER[:8]} (by the commit's subject)" in text


def test_a_short_quote_is_looked_for_only_where_its_commit_was_made(book):
    message = "Say cooling is a route of its own\n\nThe author:\n\n> Yes.\n"
    _history(book, message, MORNING)
    text = "\n".join(trail.suggest("cooling-asymmetry", utc=True))
    assert "quoted “Yes.”: too short to look for, and no session made" in text


def test_suggest_can_show_what_each_commit_changed_in_the_section(book):
    _history(book, "Say cooling is a route of its own", MORNING)
    text = "\n".join(trail.suggest("cooling-asymmetry", utc=True, diff=True))
    assert "changed in the section:" in text
    assert "+A kettle reaches the boil by one route and cools by another route." in text
    assert "-A kettle reaches the boil by one route and cools by another." in text


def test_breadcrumbs_checked_together_are_written_together_or_not_at_all(book):
    first, _ = trail.prepare("cooling-asymmetry", "seed", session=SESSION, turn=2)
    again, _ = trail.prepare("cooling-asymmetry", "sharpen", session=SESSION, turn=2)
    with pytest.raises(LookupError, match="already on"):
        trail.append("cooling-asymmetry", [first, again])
    assert not book.path("book.spec_trail").exists(), "nothing half-written"
    third, _ = trail.prepare("cooling-asymmetry", "correct", session=SESSION, turn=3)
    trail.append("cooling-asymmetry", [first, third])
    assert trail.counts() == {"cooling-asymmetry": 2}


REDPEN_ROUND = """# Red-pen round v1 (2031-04-05)

```
source-docx:    V1 - The Kettle.docx
marks-dated:    2031-04-05
```

## MARKS  (`redpen read`)

### COMMENTS (1)

[A. Fictional Author] 2031-04-05  (on text)
  edit/anchor: “It cools.”
  note: I called it a speed before. It is a route.
"""


def _thawed(value):
    """A project's frozen settings as plain dicts, to build a variant from."""
    if hasattr(value, "items"):
        return {k: _thawed(v) for k, v in value.items()}
    return value


def test_a_red_pen_breadcrumb_takes_its_place_by_the_rounds_day(tmp_path, monkeypatch):
    """A round logs no times, so its day orders it among the dated turns."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    root = tmp_path / "book"
    settings = _thawed(_book(tmp_path).settings)
    settings["provenance"]["redpen"] = "private/redpen"
    archive = root / "private" / "redpen"
    archive.mkdir(parents=True)
    earlier = REDPEN_ROUND.replace("2031-04-05", "2031-04-01")
    (archive / "v0-2031-04-01.md").write_text(earlier, encoding="utf-8")
    with project_scope(Project.from_settings(root, settings)):
        trail.add("cooling-asymmetry", "correct", session=SESSION, turn=3)
        trail.add("cooling-asymmetry", "seed", session="redpen-v0", turn=1)
        text = "\n".join(trail.story("cooling-asymmetry", utc=True))
        assert text.index("redpen-v0-2031-04-01") < text.index("0badc0de")
        assert "2031-04-01  redpen-v0-2031-04-01" in text
        assert "undated" not in text
        written = trail.sidecar_path().read_text()
        assert written.index("redpen-v0") < written.index("0badc0de")


def test_a_breadcrumb_can_point_at_a_red_pen_mark(tmp_path, monkeypatch):
    """The author's marks on a rendered copy are his words too, named by turn."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    root = tmp_path / "book"
    settings = _thawed(_book(tmp_path).settings)
    settings["provenance"]["redpen"] = "private/redpen"
    archive = root / "private" / "redpen"
    archive.mkdir(parents=True)
    (archive / "v1-2031-04-05.md").write_text(REDPEN_ROUND, encoding="utf-8")
    with project_scope(Project.from_settings(root, settings)):
        crumb, _ = trail.add(
            "cooling-asymmetry",
            "reverse",
            session="redpen-v1",
            turn=1,
            excerpt="It is a route.",
        )
        assert crumb.header() == "- turn 1 · reverse · redpen redpen-v1-2031-04-05"
        assert trail.check() == 0
