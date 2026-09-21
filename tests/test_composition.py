"""Panel allocations preserve the model's spending tier in every seat."""

import json

import pytest

from quirework.project import Project, project_scope
from quirework.runs import engine
from quirework.sops import composition


@pytest.fixture
def panel_spec(tmp_path):
    def write(kind, name, value):
        directory = {
            "personas": "profiles",
            "instruments": "questions",
            "panels": "panels",
        }[kind]
        path = tmp_path / directory / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    for name in ("reader", "editor"):
        write(
            "personas",
            name,
            {
                "schema_version": 1,
                "id": name,
                "revision": 1,
                "label": name,
                "description": "A fictitious test perspective.",
                "tags": ["reader"],
                "prompt": "Say whether the note is clear.",
            },
        )
    write(
        "instruments",
        "review",
        {
            "id": "review",
            "revision": "1",
            "prompts": {"read": {"text": "Read the supplied note."}},
        },
    )
    write(
        "panels",
        "fixture",
        {
            "schema_version": 1,
            "id": "fixture",
            "revision": 1,
            "description": "Two fictitious perspectives.",
            "required_tags": [],
            "seats": [
                {"persona": name, "instrument": "review", "ask": "read", "addenda": []}
                for name in ("reader", "editor")
            ],
        },
    )
    spec = {
        "panel": "fixture",
        "models": [
            {"id": name, "harness": "codex", "model": "fixture", "tier": tier}
            for name, tier in (
                ("dear", "premium"),
                ("cheap", "cheap"),
                ("plain", "standard"),
            )
        ],
        "materials": [{"source": "note.md", "delivery": "text"}],
    }
    settings = {
        "sops": {
            "personas": "profiles",
            "instruments": "questions",
            "panels": "panels",
        }
    }
    with project_scope(Project.from_settings(tmp_path, settings)):
        yield spec


def test_model_tiers_reach_every_perspective(panel_spec, tmp_path):
    seats = composition.compile_plan(panel_spec, directory=tmp_path)["seats"]
    assert [seat["tier"] for seat in seats] == ["premium", "cheap", "standard"] * 2
    assert engine.order(seats) == [
        "reader--cheap",
        "reader--dear",
        "editor--dear",
        "reader--plain",
        "editor--cheap",
        "editor--plain",
    ]


def test_omitted_tiers_preserve_declared_order(panel_spec, tmp_path):
    for model in panel_spec["models"]:
        del model["tier"]
    seats = composition.compile_plan(panel_spec, directory=tmp_path)["seats"]
    assert engine.order(seats) == [seat["id"] for seat in seats]


@pytest.mark.parametrize("tier", ["discount", "", None, 1])
def test_invalid_tier_is_rejected_before_a_run(panel_spec, tmp_path, tier):
    panel_spec["models"][0]["tier"] = tier
    with pytest.raises(ValueError, match="model tier"):
        composition.compile_plan(panel_spec, directory=tmp_path)
