"""Seats of one record, started by separate operators.

The spend order runs one cheap trial seat first and the rest once it has
proved the assignment, in turn or side by side. A record used to admit
one operator at a time, so adding seats beside a running trial meant
freezing them into a second record, which left them declared and unrun
in the first. These tests start the rest of a record while its trial
seat still runs, and check that a record says which of its declared
seats never started.
"""

import json
import os
import threading

import pytest

from quirework.runs import commands as run_commands
from quirework.runs import engine
from quirework.runs.storage import lock
from quirework.runs.storage import process_identity


@pytest.fixture
def panel(tmp_path):
    """A trial seat and two others, all one turn long."""
    (tmp_path / "scene.md").write_text("Mira left a green umbrella.\n")
    plan = dict(
        title="Fictitious panel",
        seats=[
            dict(
                id=name,
                harness="codex",
                model="fixture-model",
                effort="high",
                tier=tier,
                turns=[
                    dict(
                        id="read",
                        prompt="Read this scene.",
                        materials=[dict(source="scene.md", delivery="text")],
                    )
                ],
            )
            for name, tier in (
                ("trial", "cheap"),
                ("second", "standard"),
                ("third", "standard"),
            )
        ],
    )
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    engine.prepare(path, tmp_path / "record", resolve=False)
    return engine.Run(tmp_path / "record")


def test_the_rest_start_while_the_trial_seat_runs(panel, monkeypatch):
    entered, finish = threading.Event(), threading.Event()

    def turn(self, seat, index, timeout, wait_quota=None):
        if seat == "trial":
            entered.set()
            assert finish.wait(10)

    monkeypatch.setattr(engine.Run, "turn", turn)
    first = {}
    trial = threading.Thread(target=lambda: first.update(panel.run(["trial"])))
    trial.start()
    try:
        assert entered.wait(10)
        # A bare start, issued while the trial seat is still running.
        assert panel.run() == {
            "trial": engine.HELD,
            "second": "completed",
            "third": "completed",
        }
    finally:
        finish.set()
        trial.join(10)
    assert first == {"trial": "completed"}
    assert panel.not_started() == []


def test_a_seat_held_elsewhere_is_not_a_failed_start(panel, monkeypatch):
    monkeypatch.setattr(engine.Run, "turn", lambda *a, **k: None)
    monkeypatch.setattr(engine, "frozen_dispatch", lambda root, args: None)
    with lock(panel.root / "locks" / "trial.lock"):
        assert run_commands.main(["start", str(panel.root)]) == 0
    assert panel.status("second") == "complete"
    assert panel.status("trial") == "not started"


def test_collect_leaves_a_seat_another_operator_runs_to_that_operator(
    panel, monkeypatch, capsys
):
    state = panel.state("trial")
    state["pending"] = dict(turn="read", mode="start", parent_id="sid")
    panel.save("trial", state)
    monkeypatch.setattr(engine, "frozen_dispatch", lambda root, args: None)
    with lock(panel.root / "locks" / "trial.lock"):
        assert run_commands.main(["collect", str(panel.root)]) == 0
    assert json.loads(capsys.readouterr().out) == {"trial": engine.HELD}


def test_each_seat_says_where_it_stands(panel):
    assert panel.status("trial") == "not started"
    state = panel.state("trial")
    state["pending"] = dict(
        turn="read", pid=os.getpid(), process_identity=process_identity(os.getpid())
    )
    panel.save("trial", state)
    assert panel.status("trial") == "running turn read"
    state["pending"]["process_identity"] = "another process"
    panel.save("trial", state)
    assert panel.status("trial") == (
        "turn read pending with no process; 0 of 1 turns banked"
    )
    state = panel.state("second")
    state["complete"] = True
    panel.save("second", state)
    assert panel.status("second") == "complete"
    seats = panel.inspect()
    assert seats["not_started"] == ["third"]
    assert seats["seats"]["third"]["status"] == "not started"


def test_a_partial_export_names_the_seats_that_never_ran(
    panel, tmp_path, monkeypatch, capsys
):
    state = panel.state("trial")
    state["complete"] = True
    panel.save("trial", state)
    monkeypatch.setattr(engine, "frozen_dispatch", lambda root, args: None)
    out = tmp_path / "export"
    assert run_commands.main(["export", str(panel.root), str(out), "--partial"]) == 0
    said = capsys.readouterr().err
    assert "never started" in said and "second, third" in said
    receipt = json.loads((out / "receipt.json").read_text())
    assert receipt["not_started"] == ["second", "third"]
    assert receipt["seats"]["second"]["_completeness"]["status"] == "not started"
