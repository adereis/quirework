"""Supplements: the reader-facing collections beside a work, each with a role.

Tests apply the spec to cases and are counted by `spec notes --coverage`;
expressions tell it to a reader and are held to the same citation checks
without being counted. The setting replaced `manuscript.companion_dirs`, a
list of directories with no role, which is refused today and translated when
a tool reads a revision committed before the change.
"""

from __future__ import annotations

import pytest

from quirework.manuscript import footprint, notes
from quirework.project import (
    ConfigurationError,
    Project,
    project_scope,
    translate_renamed,
)

SPEC = "# Spec\n\n## The flash {#flash}\n\nbody.\n\n## The fog {#fog}\n\nbody.\n"


def _book(root, supplements):
    """A Quarto book whose one chapter binds both sections, with a drill
    entry anchored to the flash and an explainer anchored to the fog."""
    canon = root / "manuscript" / "sections"
    (canon / "_marks").mkdir(parents=True)
    (root / "manuscript" / "_quarto.yml").write_text(
        "book:\n  chapters:\n    - sections/lamp.qmd\n"
    )
    (canon / "lamp.qmd").write_text("# The lamp {#sec-lamp}\n\nprose.\n")
    (canon / "_marks" / "lamp.md").write_text(
        "# The lamp {#sec-lamp}\n\n<!-- @bind(flash) -->\n<!-- @bind(fog) -->\n"
    )
    (canon / "_spec.md").write_text(SPEC)
    for name, slug in (("drills", "flash"), ("explainers", "fog")):
        (root / name).mkdir()
        (root / name / "entry.md").write_text(
            f"# Entry\n\n**Anchors:** `lamp` `@bind({slug})`.\n\nbody.\n"
        )
    return Project.from_settings(
        root,
        {
            "manuscript": {
                "source": "manuscript",
                "chapters": "manuscript/sections",
                "quarto": "manuscript/_quarto.yml",
                "spec": "manuscript/sections/_spec.md",
                "supplements": supplements,
            }
        },
    )


BOTH = [
    {"path": "drills", "role": "tests"},
    {"path": "explainers", "role": "expressions"},
]


def test_coverage_counts_only_the_tests(tmp_path):
    with project_scope(_book(tmp_path, BOTH)):
        by_slug, unexercised = notes.coverage()
    assert by_slug == {"flash": ["entry"]}
    assert [slug for slug, _ in unexercised] == ["fog"]


def test_every_role_is_held_to_the_citation_checks(tmp_path):
    project = _book(tmp_path, BOTH)
    (tmp_path / "explainers" / "entry.md").write_text(
        "# Entry\n\n**Anchors:** `lamp` `@bind(fog)`.\n\nSee `#gone`.\n"
    )
    with project_scope(project):
        docs = project.path("manuscript.source")
        problems, _, _ = notes.supplement_check(
            docs, notes.spec_slugs(docs), root=tmp_path
        )
    assert any("explainers/entry.md" in p and "gone" in p for p in problems)


def test_a_valid_declaration_passes(tmp_path):
    _book(tmp_path, BOTH).require("manuscript")


@pytest.mark.parametrize(
    "supplements, message",
    [
        ([{"path": "drills"}], "needs a role: 'tests' or 'expressions'"),
        ([{"path": "drills", "role": "notes"}], "needs a role"),
        (
            [
                {"path": "drills", "role": "tests"},
                {"path": "drills/", "role": "expressions"},
            ],
            "names the directory drills twice, as drills and drills/",
        ),
        (
            [
                {"path": "drills", "role": "tests"},
                {"path": "./drills", "role": "expressions"},
            ],
            "names the directory drills twice, as drills and ./drills",
        ),
        ([{"path": "drills", "role": "tests", "gate": True}], "sets gate"),
        (["drills"], "is not a table"),
        ({"path": "drills", "role": "tests"}, "is a list of tables"),
        ([{"role": "tests"}], "needs a nonempty path"),
    ],
)
def test_a_malformed_declaration_is_refused(tmp_path, supplements, message):
    project = _book(tmp_path, BOTH)
    values = project.describe()["settings"]
    values["manuscript"]["supplements"] = supplements
    with pytest.raises(ConfigurationError, match=message):
        Project.from_settings(tmp_path, values).require("manuscript")


@pytest.mark.parametrize(
    "old, value, new, shape",
    [
        ("companion_dirs", ["drills"], "supplements", "its path and its role"),
        ("companion_anchors", "Rests on", "supplement_anchors", None),
    ],
)
def test_the_old_settings_are_refused_with_the_new_names(
    tmp_path, old, value, new, shape
):
    with pytest.raises(ConfigurationError) as raised:
        Project.from_settings(tmp_path, {"manuscript": {old: value}})
    message = str(raised.value)
    assert f"manuscript.{old} is renamed manuscript.{new}" in message
    assert (shape in message) if shape else "role" not in message


def test_an_old_revision_reads_its_directories_with_no_role():
    old = {"manuscript": {"companion_dirs": ["drills", "explainers"]}}
    values = translate_renamed(old)
    assert values["manuscript"] == {
        "supplements": [{"path": "drills"}, {"path": "explainers"}]
    }
    assert old["manuscript"] == {"companion_dirs": ["drills", "explainers"]}


def test_a_translated_revision_is_read_without_a_role(tmp_path):
    """History builds its project from a translated configuration and never
    validates it, so the missing role is no error there; every entry is
    still checked, and none is counted."""
    project = _book(tmp_path, BOTH)
    values = project.describe()["settings"]
    values["manuscript"].pop("supplements")
    values["manuscript"]["companion_dirs"] = ["drills", "explainers"]
    then = Project.from_settings(tmp_path, translate_renamed(values))
    with project_scope(then):
        assert [s.role for s in notes.supplements()] == [None, None]
        assert len(notes.supplement_files()) == 2
        assert notes.coverage()[0] == {}


def test_the_footprint_holds_every_role(tmp_path):
    fp = footprint.footprint(_book(tmp_path, BOTH))
    assert fp.covers("drills/entry.md") and fp.covers("explainers/entry.md")
