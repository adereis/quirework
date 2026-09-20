"""Usage records answer which commands ran, without keeping what they were given.

The store is the one place in Quirework that accumulates across projects, so
these tests hold it to two promises at once: that it counts what really ran,
and that nothing an author typed as an argument ever reaches it.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from quirework.cli import invoke
from quirework.core import usage
from quirework.project import Project, ConfigurationError


@pytest.fixture(autouse=True)
def fresh_module(monkeypatch):
    """Nothing carries between tests: not the warning latch, not the switch."""
    monkeypatch.setattr(usage, "_silenced", False)
    monkeypatch.delenv("QUIREWORK_USAGE", raising=False)


def project_with(tmp_path, procedures=(), **settings):
    """A project whose usage store is inside the test's own tree."""
    root = tmp_path / "book"
    (root / "procedures").mkdir(parents=True)
    for name in procedures:
        (root / "procedures" / f"{name}.md").write_text(
            f"# {name.replace('-', ' ').title()}\n\nGoal: a fictional procedure.\n"
        )
    merged = {
        "project": {"name": "A Small Notebook"},
        "sops": {"root": "procedures"},
        "usage": {"store": str(tmp_path / "records")},
    }
    for section, values in settings.items():
        merged.setdefault(section, {}).update(values)
    return Project.from_settings(root, merged)


def records(project):
    rows, damaged = usage.read(project)
    assert damaged == 0
    return rows


def test_a_dispatched_command_leaves_one_record(tmp_path):
    project = project_with(tmp_path, procedures=["shape-a-chapter"])
    assert invoke(("sop", "show"), ["shape-a-chapter"], project=project) == 0
    (entry,) = records(project)
    assert entry["command"] == "sop show"
    assert entry["target"] == "shape-a-chapter"
    assert entry["project"] == str(project.root)
    assert entry["name"] == "A Small Notebook"
    assert entry["status"] == 0


def test_asking_what_a_command_does_is_not_using_it(tmp_path):
    """Help is side-effect-free by design, and that includes this."""
    project = project_with(tmp_path, procedures=["shape-a-chapter"])
    invoke(("sop", "show"), ["--help"], project=project)
    assert records(project) == []


def test_the_report_does_not_count_itself(tmp_path):
    project = project_with(tmp_path)
    assert invoke(("usage", "show"), [], project=project) == 0
    assert records(project) == []


def test_an_argument_the_author_typed_never_reaches_the_store(tmp_path):
    """`provenance search` takes a phrase out of the author's own notes."""
    project = project_with(tmp_path)
    phrase = "the lantern in the orchard"
    with usage.record(("provenance", "search"), [phrase], project=project) as outcome:
        outcome.status = 0
    (entry,) = records(project)
    assert "target" not in entry
    store = tmp_path / "records"
    assert phrase not in "".join(p.read_text() for p in store.glob("*.jsonl"))


def test_a_name_is_kept_only_once_the_project_resolved_it(tmp_path):
    """A failed lookup is the one case where the word was not a procedure."""
    project = project_with(tmp_path, procedures=["shape-a-chapter"])
    assert invoke(("sop", "show"), ["a-private-working-title"], project=project) == 2
    (entry,) = records(project)
    assert entry["status"] == 2
    assert "target" not in entry


def test_a_command_that_raises_is_recorded_without_a_status(tmp_path):
    project = project_with(tmp_path)
    with pytest.raises(RuntimeError):
        with usage.record(("book", "build"), [], project=project):
            raise RuntimeError("a fictitious render failure")
    (entry,) = records(project)
    assert entry["command"] == "book build"
    assert entry["status"] is None
    assert usage.summarize(records(project))["errors"] == 1


def test_a_project_can_turn_the_whole_thing_off(tmp_path):
    project = project_with(
        tmp_path, procedures=["shape-a-chapter"], usage={"enabled": False}
    )
    assert invoke(("sop", "show"), ["shape-a-chapter"], project=project) == 0
    assert records(project) == []


def test_the_environment_overrides_an_enabled_project(monkeypatch, tmp_path):
    """The off switch a borrowed machine or a test harness can reach."""
    project = project_with(tmp_path, procedures=["shape-a-chapter"])
    monkeypatch.setenv("QUIREWORK_USAGE", "0")
    assert invoke(("sop", "show"), ["shape-a-chapter"], project=project) == 0
    assert records(project) == []
    monkeypatch.setenv("QUIREWORK_USAGE", "1")
    assert invoke(("sop", "show"), ["shape-a-chapter"], project=project) == 0
    assert len(records(project)) == 1


def test_an_unwritable_store_costs_a_warning_not_the_command(tmp_path, capsys):
    """Work the author asked for never fails because a metrics file did."""
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("")
    project = project_with(
        tmp_path, procedures=["shape-a-chapter"], usage={"store": str(blocked / "in")}
    )
    assert invoke(("sop", "show"), ["shape-a-chapter"], project=project) == 0
    assert invoke(("sop", "show"), ["shape-a-chapter"], project=project) == 0
    assert capsys.readouterr().err.count("not recording usage") == 1


def test_a_misconfigured_store_is_reported_the_same_way(tmp_path, capsys):
    project = project_with(tmp_path, usage={"store": "   "})
    with usage.record(("book", "build"), [], project=project) as outcome:
        outcome.status = 0
    assert "not recording usage" in capsys.readouterr().err
    with pytest.raises(ConfigurationError):
        usage.store(project)


def test_concurrent_commands_all_land(tmp_path):
    """Several seats of one room run at once; none of their records is lost."""
    project = project_with(tmp_path)

    def write(n):
        usage.append(
            {"at": "2026-05-04T00:00:00Z", "command": f"sop room {n}"}, project=project
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(64)))
    rows = records(project)
    assert len(rows) == 64
    assert len({r["command"] for r in rows}) == 64


def test_two_projects_share_one_store_and_stay_apart(tmp_path):
    """The comparison across a person's books is the reason for one store."""
    shared = {"usage": {"store": str(tmp_path / "records")}}
    one = Project.from_settings(
        tmp_path / "one", {"project": {"name": "One"}, **shared}
    )
    two = Project.from_settings(
        tmp_path / "two", {"project": {"name": "Two"}, **shared}
    )
    for project in (one, two, two):
        with usage.record(("book", "build"), [], project=project) as outcome:
            outcome.status = 0
    report = usage.summarize(records(one))
    assert report["projects"] == {str(two.root): 2, str(one.root): 1}
    assert report["project_names"][str(one.root)] == "One"


def test_a_torn_line_is_counted_rather_than_ignored(tmp_path):
    project = project_with(tmp_path)
    usage.append(
        {"at": "2026-05-04T00:00:00Z", "command": "book build"}, project=project
    )
    store = tmp_path / "records"
    written = next(iter(store.glob("*.jsonl")))
    with written.open("a") as f:
        f.write('{"at": "2026-05-04T00:00:0\n')
    rows, damaged = usage.read(project)
    assert len(rows) == 1 and damaged == 1


def test_pruning_drops_the_old_and_keeps_the_rest(tmp_path):
    project = project_with(tmp_path)
    for at in ("2024-01-02T00:00:00Z", "2026-05-04T00:00:00Z"):
        usage.append({"at": at, "command": "book build"}, project=project)
    dropped, files = usage.prune(project, "2025-01-01", dry_run=True)
    assert (dropped, files, len(records(project))) == (1, 0, 2)
    dropped, files = usage.prune(project, "2025-01-01")
    assert dropped == 1
    assert [r["at"] for r in records(project)] == ["2026-05-04T00:00:00Z"]


def test_pruning_everything_removes_the_file_itself(tmp_path):
    project = project_with(tmp_path)
    usage.append(
        {"at": "2024-01-02T00:00:00Z", "command": "book build"}, project=project
    )
    assert usage.prune(project, "2025-01-01") == (1, 1)
    assert list((tmp_path / "records").glob("*.jsonl")) == []


def test_a_window_is_a_date_or_an_age(tmp_path):
    assert usage.when("2026-01-02") == "2026-01-02"
    assert usage.when("30d") < usage.when("7d")
    assert usage.when(" 2w ") == usage.when("14d")
    with pytest.raises(ValueError, match="not a date"):
        usage.when("last tuesday")


def test_the_default_store_is_shared_by_every_project_of_one_user(
    monkeypatch, tmp_path
):
    """Per-user, not per-project: the project root is a field, not a directory."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    one = Project.from_settings(tmp_path / "one", {})
    two = Project.from_settings(tmp_path / "two", {})
    assert usage.store(one) == usage.store(two)
    assert usage.store(one) != one.state_root


def test_records_are_json_lines_a_later_reader_can_version(tmp_path):
    project = project_with(tmp_path)
    with usage.record(("book", "build"), [], project=project) as outcome:
        outcome.status = 0
    written = next(iter((tmp_path / "records").glob("*.jsonl")))
    lines = written.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["schema"] == usage.SCHEMA
    assert os.stat(tmp_path / "records").st_mode & 0o077 == 0
