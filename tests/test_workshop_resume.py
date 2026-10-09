"""A writer a limit stopped is resumed only while its session survives."""

import json
import subprocess

import pytest

from quirework.project import Project, project_scope
from quirework.providers import blind
from quirework.sops import workshop
from conftest import native_session

PERSONA = {
    "schema_version": 1,
    "id": "plain",
    "revision": 1,
    "label": "A test writer",
    "description": "Writes whatever the assignment asks for.",
    "tags": [],
    "prompt": "Write plainly.",
}

# A Claude turn the session limit cut after the model had begun to answer.
CUT = [
    {"type": "system", "subtype": "init", "session_id": "sid-1"},
    {"type": "assistant", "session_id": "sid-1", "message": {"content": []}},
    {
        "type": "result",
        "is_error": True,
        "session_id": "sid-1",
        "result": "You've hit your session limit",
        "modelUsage": {"m": {"outputTokens": 9100}},
    },
]


@pytest.fixture
def stopped(tmp_path):
    """A one-writer workshop whose writer a limit stopped partway."""
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
        writer = frozen["writers"][0]["id"]
        record = tmp_path / "run" / "records" / writer
        (record / "events.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in CUT)
        )
        state = json.loads((record / "state.json").read_text())
        state["status"] = "failed"
        (record / "state.json").write_text(json.dumps(state))
        yield tmp_path / "run", writer, record


def test_resume_files_nothing_away_once_the_session_is_gone(stopped):
    """The stopped attempt stays where it was, so the writer reads the same."""
    run, writer, record = stopped
    state = (record / "state.json").read_text()
    with pytest.raises(blind.SessionGone, match="sid-1: its private home"):
        workshop.resume(run, writer)
    assert (record / "events.jsonl").exists()
    assert not (record / "attempts").exists()
    assert (record / "state.json").read_text() == state


def test_resume_goes_on_while_the_session_survives(stopped, monkeypatch):
    run, writer, record = stopped
    native_session(run / "writers" / writer, "claude", "sid-1")
    launched = []
    monkeypatch.setattr(
        workshop, "run_writer", lambda *a, **kw: launched.append(kw["resume"])
    )
    workshop.resume(run, writer)
    assert launched == ["sid-1"]
    assert (record / "attempts" / "01" / "events.jsonl").exists()


def test_a_home_removed_by_hand_reads_as_gone(stopped):
    run, writer, _ = stopped
    native_session(run / "writers" / writer, "claude", "sid-1")
    blind.discard_state_root(run / "writers" / writer)
    with pytest.raises(blind.SessionGone, match="is gone"):
        workshop.resume(run, writer)
