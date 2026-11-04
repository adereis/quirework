"""The review command line: its help, and the names it gives runs."""

import datetime

import pytest

from quirework.runs import feed
from quirework.sops import review

ACTIONS = (
    "run",
    "prompt",
    "resume",
    "continue",
    "fork",
    "status",
    "watch",
    "quotes",
    "audit",
)


@pytest.mark.parametrize("action", ACTIONS)
def test_subcommand_help_opens_and_launches_nothing(action, capsys, monkeypatch):
    """Help is read before a brief, a range or a run exists, so it must
    need none and prepare nothing."""

    def no_work(*args, **kwargs):
        pytest.fail("help must not open, read or launch a review")

    for name in ("start", "_turn", "status", "audit", "requote", "resolve_run"):
        monkeypatch.setattr(review, name, no_work)
    monkeypatch.setattr(feed, "watch", no_work)
    with pytest.raises(SystemExit) as stopped:
        review.main([action, "--help"])
    assert stopped.value.code == 0
    assert f"quirework sop review {action}" in capsys.readouterr().out


def test_a_run_is_named_by_its_date_and_scope():
    """The name `start` gives a run is one `resolve_run` takes as a name."""
    name = review.run_name("kettle-whistle")
    date, _, scope = name.partition("-review-")
    assert datetime.date.fromisoformat(date) and scope == "kettle-whistle"
    assert review.RUN_NAME.match(name)
    assert review.RUN_NAME.match("2026-01-01-review-tide-2")
    for other in (
        "2026-01-01-workshop-kettle",
        "2026-01-01-review-",
        "2026-01-01-review-Kettle",
        "kettle-whistle",
    ):
        assert not review.RUN_NAME.match(other), other
