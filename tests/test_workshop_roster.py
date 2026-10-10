"""A workshop needs the default roster only when a plan names no writers."""

import json
import subprocess

import pytest

from quirework.project import ConfigurationError, Project, project_scope
from quirework.sops import workshop


def profile(identifier):
    return {
        "schema_version": 1,
        "id": identifier,
        "revision": 1,
        "label": "A test writer",
        "description": "Writes whatever the assignment asks for.",
        "tags": [],
        "prompt": "Write plainly.",
    }


WRITER = {"harness": "claude", "requested_model": "sonnet", "effort": "medium"}


@pytest.fixture
def project(tmp_path):
    """A one-file repository, so a plan has a source and a HEAD to freeze."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "draft.md").write_text("The kettle boils at noon.\n")
    for args in (
        ["init", "-q"],
        ["add", "draft.md"],
        ["-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-qm", "x"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True)
    return root


def plan_at(tmp_path, **extra):
    plan = {
        "assignment": "Rewrite the draft.",
        "brief": "One short paragraph.",
        "files": [{"path": "draft.md", "source": "draft.md"}],
        "outputs": ["drafts/draft.md"],
        **extra,
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    return path


def test_a_plan_naming_its_writers_needs_no_roster(project, tmp_path):
    """The roster is the fallback for a plan without writers; a project
    whose plans always name theirs never has to declare one."""
    plan = plan_at(
        tmp_path, writers=[dict(WRITER, persona={"inline": profile("plain")})]
    )
    with project_scope(Project.from_settings(project, {})):
        frozen = workshop.prepare(plan, tmp_path / "run", resolve_models=False)
    assert not frozen["writers_from_roster"]
    assert [w["persona"]["profile"]["id"] for w in frozen["writers"]] == ["plain"]


def test_a_plan_without_writers_names_the_missing_roster(project, tmp_path):
    plan = plan_at(tmp_path)
    with project_scope(Project.from_settings(project, {})):
        with pytest.raises(ConfigurationError, match="sops.roster"):
            workshop.prepare(plan, tmp_path / "run", resolve_models=False)
    assert not (tmp_path / "run").exists()


def test_the_roster_persona_still_reaches_a_plans_own_writers(project, tmp_path):
    """Declared, the roster's persona applies to a plan writer naming none."""
    roster = project / "roster.json"
    roster.write_text(
        json.dumps({"persona": {"inline": profile("house")}, "writers": [dict(WRITER)]})
    )
    plan = plan_at(tmp_path, writers=[dict(WRITER)])
    settings = {"sops": {"roster": "roster.json"}}
    with project_scope(Project.from_settings(project, settings)):
        frozen = workshop.prepare(plan, tmp_path / "run", resolve_models=False)
    assert [w["persona"]["profile"]["id"] for w in frozen["writers"]] == ["house"]


def test_a_writer_whose_canary_has_no_model_is_refused_at_prepare(
    project, tmp_path, monkeypatch
):
    """A writer is proven blind on its runner's cheapest model when it
    launches. A workshop learned at that launch that no model was named,
    after every writer's workspace had been staged."""
    from quirework.providers import blind, models

    monkeypatch.setattr(
        models, "resolve", lambda harness, model, effort, *_: dict(model=model)
    )
    monkeypatch.setattr(blind, "cheapest_model", lambda runner: None)
    persona = {"inline": profile("plain")}
    plan = plan_at(tmp_path, writers=[dict(WRITER, id="first", persona=persona)])
    with project_scope(Project.from_settings(project, {})):
        with pytest.raises(RuntimeError, match="^writer first: .*canary_models"):
            workshop.prepare(plan, tmp_path / "run")
    assert not (tmp_path / "run").exists()


def test_the_roster_command_names_the_missing_setting(project):
    with project_scope(Project.from_settings(project, {})):
        with pytest.raises(ConfigurationError, match="sops.roster"):
            workshop.roster()
