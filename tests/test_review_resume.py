"""A jailed review continues only while its private home holds the session."""

import json

import pytest

from quirework.project import Project, project_scope
from quirework.providers import blind
from quirework.sops import review
from conftest import native_session

RUN = "2026-01-01-review-a-fictional-edit"
CONFIG = {"harness": "codex", "model": "demo-model", "effort": "high"}


@pytest.fixture
def record(tmp_path):
    """A review whose first turn completed in session th-1."""
    with project_scope(Project.from_settings(tmp_path, {})):
        record = tmp_path / "reviews" / RUN
        record.mkdir(parents=True)
        (record / "run.json").write_text(
            json.dumps(
                {
                    "run": RUN,
                    "config": CONFIG,
                    "delta": {},
                    "turns": [{"turn": "01", "session_id": "th-1", "mode": "run"}],
                }
            )
        )
        yield record


@pytest.mark.parametrize("mode", ["resume", "fork", "continue"])
def test_a_turn_is_refused_before_it_is_numbered_once_the_home_is_gone(record, mode):
    """A turn number is a directory, so allocating one and then failing
    would leave an empty turn in the review's own evidence."""
    with pytest.raises(blind.SessionGone, match="th-1: its private home .* is gone"):
        review._turn(RUN, mode, "Look again.", store=record.parent)
    assert not (record / "turns").exists()


def test_a_home_that_lost_the_session_is_named_as_such(record):
    native_session(record, "codex", "another-session")
    with pytest.raises(blind.SessionGone, match="no longer holds it"):
        review._turn(RUN, "resume", "Look again.", store=record.parent)
    assert not (record / "turns").exists()


def test_an_unconfined_reviewer_keeps_its_session_in_the_operators_home(
    record, monkeypatch
):
    """Its session is not in a private home, so none is asked for."""

    class Launched(Exception):
        pass

    def launch(*args, **kwargs):
        raise Launched

    monkeypatch.setattr(review, "canary", lambda *a, **kw: None)
    monkeypatch.setattr(review, "launch_context", launch)
    with pytest.raises(Launched):
        review._turn(RUN, "resume", "Look again.", store=record.parent, jailed=False)
