"""A writer a limit stopped is resumed only while its session survives."""

import json
import subprocess

import pytest

from quirework.project import Project, project_scope
from quirework.providers import blind, quota
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

# The same limit met before the model had produced anything.
REFUSED = [
    {"type": "system", "subtype": "init", "session_id": "sid-1"},
    {
        "type": "result",
        "is_error": True,
        "session_id": "sid-1",
        "result": "You've hit your session limit",
        "modelUsage": {},
    },
]


def write_stream(record, rows):
    (record / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


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


def test_a_completed_writer_lets_its_home_go(stopped, monkeypatch):
    """A completed writer is never resumed, so prune may take its home."""
    run, writer, record = stopped
    work = run / "writers" / writer
    native_session(work, "claude", "sid-1")
    blind.hold(work, "held by its launch")
    plan = json.loads((run / "plan.json").read_text())
    for item in plan["outputs"]:
        (work / item["path"]).parent.mkdir(parents=True, exist_ok=True)
        (work / item["path"]).write_text("A new paragraph. " * item["min_bytes"])
    monkeypatch.setattr(
        workshop.drivers,
        "receipt",
        lambda *a, **kw: {
            "session_id": "sid-1",
            "model": "m",
            "native_transcripts": [],
        },
    )
    assert workshop.collect(run, writer)["status"] == "completed"
    assert blind.held(blind.state_root(work)) is None


def test_a_writer_that_reached_the_keyring_carries_the_red_flag(stopped, monkeypatch):
    """The launch records the bus it opened, and the receipt says what that
    means, as every other procedure's does. The workshop recorded the proxy
    and raised nothing, so its receipt claimed a confinement it lacked."""
    run, writer, record = stopped
    work = run / "writers" / writer
    bus = {"names": ["agy"], "proxy": "xdg-dbus-proxy", "filtered": True}
    (record / "launch.json").write_text(json.dumps({"session_bus": bus}))
    plan = json.loads((run / "plan.json").read_text())
    for item in plan["outputs"]:
        (work / item["path"]).parent.mkdir(parents=True, exist_ok=True)
        (work / item["path"]).write_text("A new paragraph. " * item["min_bytes"])
    monkeypatch.setattr(
        workshop.drivers,
        "receipt",
        lambda *a, **kw: {
            "session_id": "sid-1",
            "model": "m",
            "native_transcripts": [],
        },
    )
    workshop.collect(run, writer)
    flags = json.loads((record / "receipt.json").read_text())["red_flags"]
    assert flags == [blind.Bus.flag(bus)]
    assert workshop.inspect(run)["writers"][writer]["red_flags"] == flags


def test_a_failed_writer_keeps_its_home_until_it_is_retried(stopped):
    """Its quota stop may still be resumed; a retry starts a session of its own."""
    run, writer, _ = stopped
    work = run / "writers" / writer
    native_session(work, "claude", "sid-1")
    blind.hold(work, "held by its launch")
    assert blind.held(blind.state_root(work))
    workshop.retry(run, writer, "second-try")
    assert blind.held(blind.state_root(work)) is None


def test_a_quota_stop_continues_its_own_session_with_a_notice(
    stopped, monkeypatch, canaries_pass
):
    """The session already holds the assignment and the reasoning, so the
    writer is told only that it was cut off, in that same session, and the
    stopped attempt is kept whole beside the new one."""
    run, writer, record = stopped
    work = run / "writers" / writer
    native_session(work, "claude", "sid-1")
    stream = (record / "events.jsonl").read_text()
    (record / "stderr.log").write_text("cut off\n")
    (record / "prompt.md").write_text("The first prompt.\n")
    # `true` stands in for the jail, so the real argv is built and recorded
    # and no runner is started.
    monkeypatch.setattr(
        workshop.drivers,
        "launch_context",
        lambda *a, **k: (["true"], {}, blind.state_root(work) / "claude"),
    )
    monkeypatch.setattr(blind, "runner_version", lambda name: None)
    with pytest.raises(RuntimeError, match="no successful completed result"):
        workshop.resume(run, writer)
    argv = json.loads((record / "launch.json").read_text())["argv"]
    assert argv[argv.index("--resume") + 1] == "sid-1"
    prompt = (record / "prompt.md").read_text()
    assert prompt.startswith(quota.CONTINUATION) and "START.md" not in prompt
    kept = record / "attempts" / "01"
    assert (kept / "events.jsonl").read_text() == stream
    assert (kept / "stderr.log").read_text() == "cut off\n"
    assert (kept / "prompt.md").read_text() == "The first prompt.\n"
    state = json.loads((record / "state.json").read_text())
    assert state["resumed_from"] == "sid-1"
    assert state["attempts"][0]["reason"].startswith("quota interrupted")


def test_a_refusal_before_the_model_ran_is_relaunched_not_continued(
    stopped, monkeypatch
):
    """Nothing was generated, so there is nothing to continue, and no
    session has to survive for the writer to go again."""
    run, writer, record = stopped
    write_stream(record, REFUSED)
    launched = []
    monkeypatch.setattr(
        workshop, "run_writer", lambda *a, **kw: launched.append(kw["resume"])
    )
    workshop.resume(run, writer)
    assert launched == [None]
    state = json.loads((record / "state.json").read_text())
    assert state["status"] == "prepared" and state["resumed_from"] is None
    assert state["attempts"][0]["reason"].startswith("quota refused")


@pytest.mark.parametrize(
    "rows, phase, resumable", [(CUT, "interrupted", True), (REFUSED, "refused", False)]
)
def test_inspect_calls_only_an_interrupted_writer_resumable(
    stopped, rows, phase, resumable
):
    """A refused turn records a session id too, so the id alone cannot be
    what makes a writer resumable."""
    run, writer, record = stopped
    write_stream(record, rows)
    item = workshop.inspect(run)["writers"][writer]
    assert item["quota_stop"] == phase
    assert item["resumable"] is resumable


def test_a_stop_that_is_not_a_limit_is_never_resumed(stopped):
    """A crash leaves unknown what ran, and continuing from an unknown state
    is worse than starting over."""
    run, writer, record = stopped
    write_stream(
        record,
        [
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "session_id": "sid-1",
                "result": "connection reset",
                "modelUsage": {},
            }
        ],
    )
    native_session(run / "writers" / writer, "claude", "sid-1")
    state = (record / "state.json").read_text()
    with pytest.raises(RuntimeError, match="does not carry a provider quota stop"):
        workshop.resume(run, writer)
    assert (record / "state.json").read_text() == state
    assert not (record / "attempts").exists()


def test_a_resume_that_lands_in_another_session_is_refused(stopped):
    """A harness that opened a new conversation would return a cold draft
    wearing a continuation's receipt."""
    run, writer, record = stopped
    state = json.loads((record / "state.json").read_text())
    state.update(status="running", ended_at=1.0, resumed_from="sid-1")
    (record / "state.json").write_text(json.dumps(state))
    write_stream(
        record,
        [
            {"type": "system", "subtype": "init", "session_id": "sid-9"},
            {
                "type": "assistant",
                "session_id": "sid-9",
                "message": {"model": "claude-sonnet-5", "content": []},
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "session_id": "sid-9",
                "result": "Done.",
                "modelUsage": {"claude-sonnet-5": {"outputTokens": 5}},
            },
        ],
    )
    with pytest.raises(RuntimeError, match="did not continue its own session"):
        workshop.collect(run, writer)
    assert json.loads((record / "state.json").read_text())["status"] == "failed"
