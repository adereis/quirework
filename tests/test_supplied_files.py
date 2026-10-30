"""Long material goes as a file and the answer comes back as one.

A seat handed a path decides whether to open it, so a file that stands in for
text in the prompt is held to being read in full, from the seat's own tool
calls, the way a mounted reading list already is. These tests use fictitious
material and fictitious sessions.
"""

import json

import pytest

from quirework.project import Project, project_scope
from quirework.providers import harness, reads
from quirework.publish import plans
from quirework.runs import engine
from quirework.sops import composition, room


def claude_rows(calls, cwd=None):
    return [
        {
            "type": "assistant",
            **({"cwd": cwd} if cwd else {}),
            "message": {"content": [dict(type="tool_use", **c)]},
        }
        for c in calls
    ]


def session(tmp_path, rows, name="claude.jsonl"):
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_a_read_at_the_seats_path_is_counted_against_the_hosts_copy(tmp_path):
    host = tmp_path / "copy.md"
    host.write_text("one\ntwo\nthree\n")
    seen = "/tmp/work/materials/turn-000/material-000.md"
    rollout = session(
        tmp_path, claude_rows([dict(name="Read", input={"file_path": seen})])
    )
    got = reads.coverage(reads.file_reads(rollout), [seen], local={seen: str(host)})
    assert got == (1, True, [])


def test_a_claude_shell_read_resolves_against_the_directory_it_ran_in(tmp_path):
    rows = claude_rows([dict(name="Bash", input={"command": "cat input/note.md"})])
    rows[0]["cwd"] = "/seat"
    rollout = session(tmp_path, rows)
    assert reads.file_reads(rollout) == [("/seat/input/note.md", None)]


@pytest.fixture
def run(tmp_path):
    (tmp_path / "note.md").write_text("Mira left a green umbrella.\nIt rained.\n")
    plan = dict(
        title="Fictitious note",
        seats=[
            dict(
                id="reader",
                harness="claude",
                model="fixture-model",
                effort="high",
                policy=dict(tools="write"),
                turns=[
                    dict(
                        id="review",
                        prompt="Read the note.",
                        materials=[dict(source="note.md", delivery="file")],
                        outputs=["drafts/review.md"],
                    )
                ],
            )
        ],
    )
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    engine.prepare(path, tmp_path / "record", resolve=False)
    run = engine.Run(tmp_path / "record")
    prompt, _, files = run.stage("reader", 0)
    assert str(files[0][1]) in prompt
    return run, str(files[0][1]), str(files[0][0])


def test_a_supplied_file_read_in_full_is_proven(tmp_path, run):
    run, seen, _ = run
    rollout = session(
        tmp_path, claude_rows([dict(name="Read", input={"file_path": seen})])
    )
    receipt = {"rollout": str(rollout)}
    run.check_supplied_reads("reader", 0, receipt)
    assert receipt["supplied_files_read"] == "1/1"
    assert "red_flags" not in receipt


def test_a_supplied_file_read_by_its_host_path_counts_too(tmp_path, run):
    run, _, host = run
    rollout = session(
        tmp_path, claude_rows([dict(name="Read", input={"file_path": host})])
    )
    receipt = {"rollout": str(rollout)}
    run.check_supplied_reads("reader", 0, receipt)
    assert receipt["supplied_files_read"] == "1/1"


def test_a_supplied_file_never_opened_is_flagged(tmp_path, run):
    run, _, _ = run
    receipt = {"rollout": str(session(tmp_path, []))}
    run.check_supplied_reads("reader", 0, receipt)
    assert receipt["supplied_files_read"] == "0/1"
    assert "Not proven read in full: 0 of 1 supplied files" in receipt["red_flags"][0]


def test_a_turn_with_an_answer_file_asks_for_it_in_parts(tmp_path):
    (tmp_path / "note.md").write_text("Mira left a green umbrella.\n")
    plan = dict(
        title="Fictitious note",
        seats=[
            dict(
                id="reader",
                harness="claude",
                model="fixture-model",
                effort="high",
                policy=dict(tools="write"),
                turns=[
                    dict(
                        id="review",
                        prompt="Read the note.",
                        materials=[dict(source="note.md", delivery="file")],
                        outputs=["drafts/review.md"],
                    )
                ],
            )
        ],
    )
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    engine.prepare(path, tmp_path / "record", resolve=False)
    prompt, _, _ = engine.Run(tmp_path / "record").stage("reader", 0)
    assert prompt.endswith(engine.WRITE_IN_PARTS)
    assert "/tmp/work/drafts/review.md" in prompt


def test_the_harness_names_the_exact_path_of_a_supplied_file(tmp_path, run):
    _, seen, _ = run
    assert seen.startswith(str(harness.VIRTUAL / "materials"))


def panel_files(tmp_path):
    def write(directory, name, value):
        path = tmp_path / directory / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    write(
        "profiles",
        "reader",
        {
            "schema_version": 1,
            "id": "reader",
            "revision": 1,
            "label": "reader",
            "description": "A fictitious perspective.",
            "tags": ["reader"],
            "prompt": "Say whether the note is clear.",
        },
    )
    write(
        "questions",
        "review",
        {"id": "review", "revision": "1", "prompts": {"read": {"text": "Read it."}}},
    )
    write(
        "panels",
        "fixture",
        {
            "schema_version": 1,
            "id": "fixture",
            "revision": 1,
            "description": "One fictitious perspective.",
            "required_tags": [],
            "seats": [
                {
                    "persona": "reader",
                    "instrument": "review",
                    "ask": "read",
                    "addenda": [],
                }
            ],
        },
    )
    return Project.from_settings(
        tmp_path,
        {
            "sops": {
                "personas": "profiles",
                "instruments": "questions",
                "panels": "panels",
            }
        },
    )


SPEC = {
    "panel": "fixture",
    "models": [{"id": "m", "harness": "codex", "model": "fixture"}],
    "materials": [{"source": "note.md"}],
}


def test_a_panel_hands_its_material_over_as_a_file_and_takes_a_file_back(tmp_path):
    with project_scope(panel_files(tmp_path)):
        plan = composition.compile_plan(dict(SPEC), directory=tmp_path)
    (seat,) = plan["seats"]
    (turn,) = seat["turns"]
    assert turn["materials"][0]["delivery"] == "file"
    assert turn["outputs"] == ["drafts/review.md"]
    assert seat["policy"]["tools"] == "write"


def test_a_panel_that_keeps_answers_in_the_reply_says_so(tmp_path):
    spec = dict(SPEC, answers="reply", policy={"tools": "none"})
    spec["materials"] = [{"source": "note.md", "delivery": "text"}]
    with project_scope(panel_files(tmp_path)):
        plan = composition.compile_plan(spec, directory=tmp_path)
    (turn,) = plan["seats"][0]["turns"]
    assert "outputs" not in turn
    assert turn["materials"][0]["delivery"] == "text"


def test_answer_files_without_the_write_tools_are_refused(tmp_path):
    spec = dict(SPEC, policy={"tools": "none"})
    with project_scope(panel_files(tmp_path)):
        with pytest.raises(ValueError, match="answer files need the write tools"):
            composition.compile_plan(spec, directory=tmp_path)


def test_a_required_file_is_held_to_being_read_across_the_session(tmp_path):
    work = tmp_path / "seat"
    (work / "input").mkdir(parents=True)
    (work / "input" / "brief.md").write_text("Goal.\nScope.\n")
    (work / "input" / "spec.md").write_text("One.\nTwo.\nThree.\n")
    seat = {"required_reads": ["brief.md", "spec.md"]}
    first = session(
        tmp_path,
        claude_rows(
            [dict(name="Bash", input={"command": "cat input/brief.md"})], str(work)
        ),
        "first.jsonl",
    )
    got = room.required_reading(work, seat, {"native_transcripts": [str(first)]})
    assert got["required_files_read"] == "1/2"
    assert "spec.md" in got["red_flags"][0]
    later = session(
        tmp_path,
        claude_rows(
            [dict(name="Read", input={"file_path": str(work / "input" / "spec.md")})]
        ),
        "later.jsonl",
    )
    got = room.required_reading(
        work, seat, {"native_transcripts": [str(first), str(later)]}
    )
    assert got == {"required_files_read": "2/2"}
    assert room.required_reading(work, {}, {}) == {}


def test_a_whole_book_bundle_hands_chapters_over_as_files(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    chapter = artifact / "01.md"
    chapter.write_text("Mira left.\n")
    from quirework.runs.storage import digest
    import hashlib

    manifest = {
        "kind": "codex-multidisc-markdown-png",
        "source_sha": "fixture",
        "reading_order": [{"file": "01.md", "sha256": digest(chapter), "figures": []}],
        "figures": [],
        "artifact_sha256": hashlib.sha256(chapter.read_bytes()).hexdigest(),
    }
    (artifact / "manifest.json").write_text(json.dumps(manifest))
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Read the book.")
    out = tmp_path / "plan.json"
    plans.bundle(artifact, out, harness="claude", model="fixture", prompt_file=prompt)
    (seat,) = json.loads(out.read_text())["seats"]
    assert seat["policy"]["tools"] == "write"
    assert seat["turns"][0]["materials"][0]["delivery"] == "file"
    assert seat["turns"][0]["outputs"] == ["drafts/review.md"]
