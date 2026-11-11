"""The spec index.

The index is generated from the published spec alone: a working register
beside it never shows, and neither do the units that express a section. A
stale index file fails `spec notes --check`, since people and tools read it
instead of the sections.
"""

from __future__ import annotations

import json

import pytest

from quirework.manuscript import notes, specindex
from quirework.project import ConfigurationError, Project, project_scope
from quirework.text import mdwrap

MANIFEST = """opening = "opening.md"

[[parts]]
title = "The light"
sections = ["flash", "fog"]

[[parts]]
title = "The keeper"
sections = ["keeper"]
"""

SECTIONS = {
    "opening.md": "# The lamp\n\nWhat the lamp holds.\n",
    "flash.md": (
        "## The flash {#flash}\n\nThe flash marks the rock. It needs #fog to"
        " matter, and #keeper to run.\n"
    ),
    "fog.md": "## The fog {#fog}\n\nFog hides the flash (#flash).\n",
    "keeper.md": (
        "## The keeper {#keeper}\n\nThe keeper winds the clock.\n"
        "<!-- a note #fog that is not a citation -->\n"
    ),
}


def _project(root, **extra):
    return Project.from_settings(root, _settings(root, **extra))


def _settings(root, **extra):
    """A one-chapter book whose spec is split by a manifest, with a working
    register beside it, two supplements and a prose related-work register."""
    canon = root / "manuscript" / "sections"
    (canon / "_marks").mkdir(parents=True)
    (root / "manuscript" / "_quarto.yml").write_text(
        "book:\n  chapters:\n    - sections/beacon-chapter.qmd\n"
    )
    (canon / "beacon-chapter.qmd").write_text(
        "# Beacon chapter {#sec-beacon-chapter}\n\nprose.\n"
    )
    (canon / "_marks" / "beacon-chapter.md").write_text(
        "# Beacon chapter {#sec-beacon-chapter}\n\n<!-- @bind(flash) -->\n"
    )
    (canon / "_working.md").write_text(
        "# Working\n\n## The lamp rule {#lamp-rule}\n\nSee #flash.\n"
    )
    spec = root / "spec"
    spec.mkdir()
    (spec / "manifest.toml").write_text(MANIFEST)
    for name, text in SECTIONS.items():
        (spec / name).write_text(text)
    (root / "drills").mkdir()
    (root / "drills" / "cold-start.md").write_text(
        "# Cold start\n\n**Anchors:** `beacon-chapter` `@bind(flash)`.\n\nbody.\n"
    )
    (root / "drills" / "README.md").write_text("# Drills\n\n`#fog`\n")
    (root / "explainers").mkdir()
    (root / "explainers" / "why-fog.md").write_text(
        "# Why fog matters\n\nIt is `#fog` that hides it.\n"
    )
    (root / "research").mkdir()
    (root / "research" / "related.md").write_text(
        "# Related work\n\n## Lamps\n\nOlder lamps (`#flash`), and `#lamp-rule`.\n"
        "\n## Clocks\n\nClockwork keepers (`#keeper`).\n"
    )
    settings = {
        "manuscript": {
            "source": "manuscript",
            "chapters": "manuscript/sections",
            "quarto": "manuscript/_quarto.yml",
            "spec": ["spec/manifest.toml", "manuscript/sections/_working.md"],
            "spec_public": ["spec/manifest.toml"],
            "supplements": [
                {"path": "drills", "role": "tests"},
                {"path": "explainers", "role": "expressions"},
            ],
            "related_work": "research/related.md",
            "related_work_shape": "prose",
            **extra,
        }
    }
    return settings


def _sections(index):
    return {s["slug"]: s for s in index["sections"]}


def test_the_index_holds_the_published_sections_by_part(tmp_path):
    with project_scope(_project(tmp_path)):
        index = specindex.build()
    assert index["parts"] == [
        {"title": "The light", "sections": ["flash", "fog"]},
        {"title": "The keeper", "sections": ["keeper"]},
    ]
    found = _sections(index)
    assert list(found) == ["flash", "fog", "keeper"]
    assert "lamp-rule" not in found
    assert found["flash"]["file"] == "spec/flash.md"
    assert found["flash"]["part"] == "The light"
    assert found["flash"]["words"] == 14


def test_citations_run_both_ways_among_published_sections(tmp_path):
    with project_scope(_project(tmp_path)):
        found = _sections(specindex.build())
    assert found["flash"]["cites"] == ["fog", "keeper"]
    assert found["fog"]["cited_by"] == ["flash"]
    # A comment is a note, not a citation, and the working register's
    # citation of the flash is not the published spec's.
    assert found["keeper"]["cites"] == []
    assert found["flash"]["cited_by"] == ["fog"]


def test_entries_are_grouped_by_role_and_readmes_skipped(tmp_path):
    with project_scope(_project(tmp_path)):
        index = specindex.build()
    found = _sections(index)
    assert found["flash"]["tested_by"] == ["drills/cold-start.md"]
    assert found["fog"]["told_by"] == ["explainers/why-fog.md"]
    assert found["fog"]["tested_by"] == []
    assert [e["path"] for e in index["supplements"]] == [
        "drills/cold-start.md",
        "explainers/why-fog.md",
    ]
    assert index["supplements"][1] == {
        "path": "explainers/why-fog.md",
        "role": "expressions",
        "title": "Why fog matters",
        "cites": ["fog"],
    }


def test_a_prose_register_is_counted_not_located(tmp_path):
    """Its headings are the register's own organization, which may follow
    the units, so the published index keeps to a count per file."""
    project = _project(tmp_path)
    (tmp_path / "research" / "related.md").write_text(
        "# Related work\n\n## Lamps\n\nOlder lamps (`#flash`), and `#lamp-rule`.\n"
        "\n## Beacon chapter\n\nAgain `#flash`; clockwork keepers (`#keeper`).\n"
    )
    with project_scope(project):
        index = specindex.build()
    assert _sections(index)["flash"]["related_work"] == [
        {"file": "research/related.md", "citations": 2}
    ]
    assert "Beacon chapter" not in specindex.render_json(index)


def test_the_index_never_names_a_unit(tmp_path):
    with project_scope(_project(tmp_path, spec_index={"json": "spec/index.json"})):
        text = specindex.render_json(specindex.build())
        markdown = specindex.render_markdown(
            specindex.build(), tmp_path / "spec" / "index.md", tmp_path
        )
    assert "beacon-chapter" not in text
    assert "beacon-chapter" not in markdown


def test_a_single_file_spec_takes_its_parts_from_level_one_headings(tmp_path):
    settings = _settings(tmp_path)
    (tmp_path / "manuscript" / "sections" / "_spec.md").write_text(
        "# Light\n\n## The flash {#flash}\n\nbody.\n\n"
        "# Keeping\n\n## The keeper {#keeper}\n\nbody #flash.\n"
    )
    settings["manuscript"]["spec"] = "manuscript/sections/_spec.md"
    settings["manuscript"].pop("spec_public")
    with project_scope(Project.from_settings(tmp_path, settings)):
        index = specindex.build()
    assert index["parts"] == [
        {"title": "Light", "sections": ["flash"]},
        {"title": "Keeping", "sections": ["keeper"]},
    ]
    assert _sections(index)["flash"]["cited_by"] == ["keeper"]


def test_markdown_links_each_section_and_sits_at_the_wrap_fixed_point(tmp_path):
    with project_scope(_project(tmp_path)):
        text = specindex.render_markdown(
            specindex.build(), tmp_path / "docs" / "index.md", tmp_path
        )
    assert "| [The flash](../spec/flash.md) | `flash` | 14 | 2 | 1 | 1 | 0 | 1 |" in (
        text
    )
    assert "- `flash` cites `fog`, `keeper`; cited by `fog`; tested by" in text
    assert "## The keeper" in text
    assert mdwrap.reflow_columns(text) == text


def test_write_then_check_and_a_stale_index_fails_notes_check(tmp_path, capsys):
    targets = {"markdown": "spec/index.md", "json": "spec/index.json"}
    project = _project(tmp_path, spec_index=targets)
    with project_scope(project):
        assert len(specindex.stale()) == 2
        assert specindex.main(["--write"]) == 0
        assert specindex.stale() == []
        assert specindex.main(["--check"]) == 0
        written = json.loads((tmp_path / "spec" / "index.json").read_text())
        assert written["generated_by"] == specindex.COMMAND
        docs = project.path("manuscript.source")
        problems, _ = notes._findings(docs)
        assert not any("spec index" in p for p in problems)
        (tmp_path / "spec" / "fog.md").write_text(
            "## The fog {#fog}\n\nFog hides the keeper (#keeper).\n"
        )
        assert specindex.main(["--check"]) == 1
        problems, _ = notes._findings(docs)
    stale = [p for p in problems if "spec index is stale" in p]
    assert len(stale) == 2
    assert "spec/index.md" in " ".join(stale)


def test_a_tree_that_is_not_the_project_is_not_held_to_it(tmp_path):
    targets = {"markdown": "spec/index.md"}
    project = _project(tmp_path, spec_index=targets)
    elsewhere = tmp_path / "elsewhere" / "manuscript"
    elsewhere.mkdir(parents=True)
    with project_scope(project):
        assert specindex.held_in(tmp_path)
        assert not specindex.held_in(elsewhere.parent)
        problems, _ = notes._findings(elsewhere)
        assert not any("spec index" in p for p in problems)
        (elsewhere.parent / "quirework.toml").write_text("schema = 2\n")
        assert specindex.held_in(elsewhere.parent)


def test_write_and_check_need_the_setting(tmp_path, capsys):
    with project_scope(_project(tmp_path)):
        assert specindex.main(["--check"]) == 2
        assert specindex.main([]) == 0
    assert "configure manuscript.spec_index" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("value", "says"),
    [
        ("spec/index.md", "is a table"),
        ({}, "is a table"),
        ({"html": "x.html"}, "sets html"),
        ({"json": ""}, "must name a file"),
        ({"json": "../outside.json"}, "outside the project"),
        ({"markdown": "spec/flash.md"}, "a spec file the index would overwrite"),
        ({"markdown": "spec/i", "json": "spec/i"}, "one file for both"),
    ],
)
def test_the_setting_is_validated(tmp_path, value, says):
    project = _project(tmp_path, spec_index=value)
    with pytest.raises(ConfigurationError, match=says):
        project.require("manuscript")
