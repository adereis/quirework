"""A self-driven read: the seat fetches its own chapters, and says it looked."""

import json

import pytest

from quirework.publish.plans import selfdriven
from quirework.project import Project, project_scope

INSTRUMENT = {
    "id": "reader-selfdriven",
    "revision": "1",
    "default_scope": "work",
    "sequence": {"first": "first", "next": "next", "wrap": "wrap"},
    "prompts": {
        "first": {"text": "Open the chapter yourself and read all of it."},
        "next": {"text": "Open the next chapter yourself and read all of it."},
        "wrap": {"text": "Now that the book is finished, describe its shape."},
    },
}


@pytest.fixture
def book(tmp_path):
    """The shape `publish packet --prose-dir` writes: named figures, numbered chapters."""
    root = tmp_path / "project"
    (root / "questions").mkdir(parents=True)
    (root / "questions" / "reader-selfdriven.json").write_text(json.dumps(INSTRUMENT))
    (root / "policy.toml").write_text('mount_markers = ["notebook"]\n')
    source = tmp_path / ".read" / "book"
    (source / "chapters").mkdir(parents=True)
    (source / "figures").mkdir()
    (source / "chapters" / "01-opening.md").write_text(
        "# Opening\nMira left a green umbrella.\n\n![A door](../figures/door.png)\n"
    )
    (source / "chapters" / "02-later.md").write_text(
        "# Later\nThe chair moved.\n\n![A chair](../figures/chair.png)\n"
    )
    for name in ("door.png", "chair.png"):
        (source / "figures" / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    settings = {
        "sops": {
            "instruments": "questions",
            "default_reader": "plain",
            "reader_presets": {"plain": {"instrument": "reader-selfdriven"}},
        },
        "providers": {"isolation_policy": "policy.toml"},
    }
    with project_scope(Project.from_settings(root, settings)):
        yield source


def plan_for(book, output, **kwargs):
    selfdriven(book, output, harness="codex", model="fixture-model", **kwargs)
    return json.loads(output.read_text())["seats"][0]


def test_the_seat_gets_its_chapters_and_a_count_of_the_figures(book, tmp_path):
    """The reading order goes into the prompt because the seat, not the operator,
    turns the pages; the figure count goes into the turn because the figures are
    where this mode fails silently."""
    seat = plan_for(book, tmp_path / "plan.json", mount=tmp_path / ".read" / "book")
    turn = seat["turns"][0]
    assert turn["referenced_images"] == 2
    assert turn["outputs"] == [
        "drafts/01-opening.md",
        "drafts/02-later.md",
        "drafts/wrap.md",
    ]
    assert seat["id"] == "plain"
    assert seat["policy"]["tools"] == "write"
    assert seat["policy"]["allow_compaction"] is False
    assert "chapters/01-opening.md" in turn["prompt"]
    assert "Open the chapter yourself" in turn["prompt"]
    assert "describe its shape" in turn["prompt"]


def test_an_instrument_without_a_sequence_asks_the_operator_to_name_the_ask(
    book, tmp_path
):
    """The repeated block has to stand on its own, and only the instrument knows
    which of its asks does. Guessing an ask name would be assuming this
    project's vocabulary."""
    questions = tmp_path / "project" / "questions" / "reader-selfdriven.json"
    value = json.loads(questions.read_text())
    del value["sequence"]
    questions.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="--ask"):
        selfdriven(
            book,
            tmp_path / "plan.json",
            harness="codex",
            model="fixture-model",
            mount=tmp_path / ".read" / "book",
        )
    seat = plan_for(
        book,
        tmp_path / "plan.json",
        mount=tmp_path / ".read" / "book",
        ask="first",
        wrap_ask="wrap",
    )
    assert "Open the chapter yourself" in seat["turns"][0]["prompt"]


def test_a_withheld_wrap_is_a_second_turn_the_reading_never_mentions(book, tmp_path):
    """The closing question is itself the measurement in some reads, so a seat
    that saw it coming would have been reading for it all along."""
    seat = plan_for(
        book,
        tmp_path / "plan.json",
        mount=tmp_path / ".read" / "book",
        withhold_wrap=True,
    )
    reading, wrap = seat["turns"]
    assert "describe its shape" not in reading["prompt"]
    assert "wrap.md" not in reading["prompt"]
    assert reading["outputs"] == ["drafts/01-opening.md", "drafts/02-later.md"]
    assert wrap["prompt"].strip().startswith("Now that the book is finished")


@pytest.mark.parametrize("where", ["inside", "named"])
def test_a_mount_that_reveals_the_project_is_refused(book, tmp_path, where):
    """The seat has a shell and looks around before it reads, so the mount path
    is part of the material whether or not the operator meant it to be."""
    root = tmp_path / "project"
    mount = root / "build" / "read" if where == "inside" else tmp_path / "notebook-read"
    with pytest.raises(ValueError, match="a seat with a shell reads its own paths"):
        selfdriven(
            book,
            tmp_path / "plan.json",
            harness="codex",
            model="fixture-model",
            mount=mount,
        )


def test_an_empty_directory_names_the_command_that_fills_it(book, tmp_path):
    empty = tmp_path / ".read" / "empty"
    (empty / "chapters").mkdir(parents=True)
    with pytest.raises(ValueError, match="publish packet --prose-dir"):
        selfdriven(
            empty,
            tmp_path / "plan.json",
            harness="codex",
            model="fixture-model",
            mount=tmp_path / ".read" / "empty",
        )


def scoped(root, **preset):
    """The same project as the fixture, with one preset field changed."""
    from quirework.project import Project, project_scope

    return project_scope(
        Project.from_settings(
            root,
            {
                "sops": {
                    "instruments": "questions",
                    "default_reader": "plain",
                    "reader_presets": {
                        "plain": {"instrument": "reader-selfdriven", **preset}
                    },
                },
                "providers": {"isolation_policy": "policy.toml"},
            },
        )
    )


def test_a_preset_written_for_pages_is_refused_for_a_self_driven_read(book, tmp_path):
    """The opening sentence of a reader instrument says what the seat was
    handed. Handed to the wrong delivery it is a lie in the first line, and a
    seat that answers it is answering the courier rather than the book."""
    with scoped(tmp_path / "project", delivery=["pages"]):
        with pytest.raises(ValueError, match="declares its questions for pages"):
            selfdriven(
                book,
                tmp_path / "plan.json",
                harness="codex",
                model="fixture-model",
                mount=tmp_path / ".read" / "book",
            )
        # Overriding the preset is how a deliberate mismatch is run.
        selfdriven(
            book,
            tmp_path / "plan.json",
            harness="codex",
            model="fixture-model",
            instrument="reader-selfdriven",
            mount=tmp_path / ".read" / "book",
        )
    assert (tmp_path / "plan.json").is_file()


def test_a_preset_declaring_self_driven_still_plans_one(book, tmp_path):
    with scoped(tmp_path / "project", delivery=["self-driven"]):
        selfdriven(
            book,
            tmp_path / "plan.json",
            harness="codex",
            model="fixture-model",
            mount=tmp_path / ".read" / "book",
        )
    assert json.loads((tmp_path / "plan.json").read_text())["seats"][0]["turns"]


def test_a_preset_can_name_the_asks_when_the_instrument_declares_no_sequence(
    book, tmp_path
):
    """An instrument written for this mode has no continuation ask, so it has no
    sequence to declare. The preset that selects it names its asks instead,
    rather than the operator retyping them at every launch."""
    questions = tmp_path / "project" / "questions" / "reader-selfdriven.json"
    value = json.loads(questions.read_text())
    del value["sequence"], value["prompts"]["next"]
    questions.write_text(json.dumps(value))
    with scoped(
        tmp_path / "project", delivery=["self-driven"], ask="first", wrap_ask="wrap"
    ):
        selfdriven(
            book,
            tmp_path / "plan.json",
            harness="codex",
            model="fixture-model",
            mount=tmp_path / ".read" / "book",
        )
    turns = json.loads((tmp_path / "plan.json").read_text())["seats"][0]["turns"]
    assert "Open the chapter yourself" in turns[0]["prompt"]
    assert turns[0]["metadata"]["ask"] == "first"


def with_manifest(source, **fields):
    from quirework.publish.panelkit import manifest_path
    from quirework.runs.storage import tree_digest

    record = dict(source_sha="abc1234", dirty=False, digest=tree_digest(source))
    record.update(fields)
    manifest_path(source).write_text(json.dumps(record))


def test_the_plan_names_its_book_and_pins_the_mounted_bytes(book, tmp_path):
    """The record freezes nothing a self-driven seat reads, so the plan has to
    say which book state it is and name the exact bytes, which launch checks
    again before anything is submitted."""
    from quirework.runs.storage import tree_digest

    with_manifest(book)
    selfdriven(
        book,
        tmp_path / "plan.json",
        harness="codex",
        model="fixture-model",
        effort="high",
        tier="cheap",
    )
    plan = json.loads((tmp_path / "plan.json").read_text())
    seat = plan["seats"][0]
    assert plan["source"] == {"source_sha": "abc1234", "dirty": False}
    assert seat["policy"]["read_root_digests"] == {str(book): tree_digest(book)}
    assert seat["turns"][0]["referenced_files"] == [
        str(book / "chapters" / "01-opening.md"),
        str(book / "chapters" / "02-later.md"),
    ]
    assert "fixture-model" in plan["title"] and "high" in plan["title"]
    assert seat["tier"] == "cheap"


def test_a_mount_that_is_not_an_exact_copy_is_refused(book, tmp_path):
    import shutil

    with_manifest(book)
    copy = tmp_path / ".elsewhere" / "book"
    shutil.copytree(book, copy)
    (copy / "chapters" / "02-later.md").write_text("A different ending.\n")
    with pytest.raises(ValueError, match="not an exact copy"):
        selfdriven(book, tmp_path / "plan.json", harness="codex", model="m", mount=copy)


def test_a_packet_edited_after_it_was_built_is_refused(book, tmp_path):
    with_manifest(book)
    (book / "chapters" / "01-opening.md").write_text("Rewritten after the build.\n")
    with pytest.raises(ValueError, match="changed since the packet built it"):
        selfdriven(book, tmp_path / "plan.json", harness="codex", model="m")


def test_without_provenance_the_bytes_are_still_pinned_and_it_says_so(
    book, tmp_path, capsys
):
    from quirework.runs.storage import tree_digest

    seat = plan_for(book, tmp_path / "plan.json")
    assert seat["policy"]["read_root_digests"] == {str(book): tree_digest(book)}
    assert "cannot say which book state" in capsys.readouterr().err


PROFILE = {
    "schema_version": 1,
    "id": "slow-reader",
    "revision": 1,
    "label": "A slow reader",
    "description": "A fictional reader, invented for these tests.",
    "tags": [],
    "prompt": "You read slowly and say what you notice.",
}


@pytest.mark.parametrize("slot", [False, True])
def test_the_reader_meets_its_persona_once(book, tmp_path, slot):
    """The persona is the seat's, and preparing a run composes it into the
    first turn. The planner composed it into the reaction headings as well,
    so a reader met it twice: at the top and again mid-protocol."""
    from quirework.runs import engine

    if slot:
        first = INSTRUMENT["prompts"]["first"]
        instrument = dict(
            INSTRUMENT, prompts=dict(INSTRUMENT["prompts"], first=dict(first))
        )
        instrument["prompts"]["first"]["text"] = "{{persona}} " + first["text"]
        questions = book.parents[1] / "project" / "questions"
        (questions / "reader-selfdriven.json").write_text(json.dumps(instrument))
    with_manifest(book)
    plan = tmp_path / "plan.json"
    persona = {"inline": PROFILE}
    selfdriven(book, plan, harness="codex", model="fixture-model", persona=persona)
    assert json.loads(plan.read_text())["seats"][0]["persona"] == persona
    engine.prepare(plan, tmp_path / "record", resolve=False)
    frozen = json.loads((tmp_path / "record" / "plan.json").read_text())
    prompt = frozen["seats"][0]["turns"][0]["prompt"]
    assert prompt.count(PROFILE["prompt"]) == 1
    if slot:  # where the instrument put it
        assert PROFILE["prompt"] + " Open the chapter yourself" in prompt
    else:  # ahead of everything the reader is told
        assert prompt.startswith("=== PERSPECTIVE ===\n" + PROFILE["prompt"])


def test_the_engine_accepts_what_the_planner_declares(book, tmp_path):
    """The pinned digest and the reading list are only worth declaring if the
    run engine takes them as they are written."""
    from quirework.runs import engine

    with_manifest(book)
    selfdriven(book, tmp_path / "plan.json", harness="codex", model="fixture-model")
    engine.prepare(tmp_path / "plan.json", tmp_path / "record", resolve=False)
    frozen = json.loads((tmp_path / "record" / "plan.json").read_text())
    seat = frozen["seats"][0]
    assert seat["policy"]["read_root_digests"]
    assert len(seat["turns"][0]["referenced_files"]) == 2


def test_the_seat_knows_the_back_matter_is_there_and_skips_it(book, tmp_path):
    """Most readers know the pages after the last chapter exist and do not read
    them. The directory carries the body alone, so the prompt names what the
    seat skips, as the printed book would show it."""
    with_manifest(book, back_matter=["Notes", "Colophon"])
    prompt = plan_for(book, tmp_path / "plan.json")["turns"][0]["prompt"]
    assert "the printed book has back matter: Notes and Colophon." in prompt
    assert "you know it is there,\nand you skip it." in prompt
    assert prompt.index("02-later.md") < prompt.index("back matter")


def test_a_book_without_back_matter_says_nothing_of_it(book, tmp_path):
    with_manifest(book, back_matter=[])
    prompt = plan_for(book, tmp_path / "plan.json")["turns"][0]["prompt"]
    assert "back matter" not in prompt
    assert "reading order:\n\n  1. chapters/01-opening.md" in prompt
    assert "chapters/02-later.md\n\nIllustrations are in" in prompt
