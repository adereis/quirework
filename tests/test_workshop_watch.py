"""Writers followed as they work, without what would unseal their drafts."""

import json
import subprocess
import time

import pytest

from quirework.project import Project, project_scope
from quirework.runs import feed
from quirework.sops import workshop

PERSONA = {
    "schema_version": 1,
    "id": "plain",
    "revision": 1,
    "label": "A test writer",
    "description": "Writes whatever the assignment asks for.",
    "tags": [],
    "prompt": "Write plainly.",
}


@pytest.fixture
def prepared(tmp_path):
    """A one-writer workshop, prepared and not yet run."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "draft.md").write_text("The kettle boils at noon.\n")
    for args in (
        ["init", "-q"],
        ["add", "draft.md"],
        ["-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-qm", "x"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "assignment": "Rewrite the draft.",
                "brief": "One short paragraph.",
                "files": [{"path": "draft.md", "source": "draft.md"}],
                "outputs": ["drafts/draft.md"],
                "writers": [
                    {
                        "harness": "claude",
                        "requested_model": "sonnet",
                        "effort": "medium",
                        "persona": {"inline": PERSONA},
                    }
                ],
            }
        )
    )
    with project_scope(Project.from_settings(root, {})):
        frozen = workshop.prepare(plan, tmp_path / "run", resolve_models=False)
        yield tmp_path / "run", frozen["writers"][0]["id"]


def write_a_draft(run, writer):
    """What `run_writer` records around a writer's launch, without one."""
    record, work = run / "records" / writer, run / "writers" / writer
    state = json.loads((record / "state.json").read_text())
    state.update(status="running", started_at=time.time() - 90)
    (record / "state.json").write_text(json.dumps(state))
    (work / "drafts" / "draft.md").write_text(
        "The kettle sang at noon, and nobody came.\n"
    )
    rows = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": str(work / "drafts" / "draft.md")},
                    }
                ]
            },
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "The kettle sang."}]},
        },
    ]
    (record / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return record, state


def finish(record, state):
    state.update(status="completed", ended_at=state["started_at"] + 90)
    (record / "state.json").write_text(json.dumps(state))


def test_a_writer_is_followed_without_its_words_or_its_draft_size(prepared):
    """Either one tells an editor which sealed draft is whose."""
    run, writer = prepared
    following = workshop.Feed(run, width=100)
    record, state = write_a_draft(run, writer)
    text = "\n".join(following.poll())
    assert f"{writer} [claude/sonnet] starts writing" in text
    assert "wrote    drafts/draft.md" in text
    assert " B)" not in text and "kB" not in text
    assert "kettle" not in text
    finish(record, state)
    assert f"{writer} finished in 1m30s" in "\n".join(following.poll())


def test_a_writers_words_are_shown_only_when_asked_for(prepared):
    run, writer = prepared
    write_a_draft(run, writer)
    text = "\n".join(workshop.Feed(run, show=feed.Show(), width=100).poll())
    assert "said     The kettle sang." in text
    assert "drafts/draft.md (42 B)" in text


def test_the_run_commands_own_log_withholds_them_too(prepared):
    run, writer = prepared
    following = workshop.Feed(run, history="none", show=workshop.NARRATION, width=100)
    write_a_draft(run, writer)
    text = "\n".join(following.poll())
    assert "wrote    drafts/draft.md" in text
    assert " B)" not in text and "kettle" not in text
