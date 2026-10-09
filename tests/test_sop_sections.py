"""Reading part of a procedure: its outline, named sections, a declared mode."""

import json

import pytest

from quirework.cli import invoke
from quirework.core import usage
from quirework.project import Project, project_scope
from quirework.sops import catalog, commands, sections

PROCEDURE = """\
# Tidy the notebook

<!-- @skill: Tidy a small notebook before filing it. -->
<!-- @mode one-page: Choosing a pass; One page -->

Tidy only what the human named. Every pass ends with the gates.

## Choosing a pass

One page, or the whole notebook.

## One page

Read the page twice.

### Checks

Run `quirework text wrap --check` on it.

```markdown
## Not a heading, only an example
```

<!--
## Not a heading either
-->

## The whole notebook

Every page, in order.

### Checks

Run every gate.
"""


@pytest.fixture
def notebook(tmp_path, monkeypatch):
    monkeypatch.delenv("QUIREWORK_USAGE", raising=False)
    (tmp_path / "procedures").mkdir()
    (tmp_path / "procedures" / "tidy.md").write_text(PROCEDURE)
    (tmp_path / "procedures" / "index.json").write_text(
        json.dumps(
            {
                "version": 1,
                "families": [{"id": "tidy", "label": "Tidy", "question": "What?"}],
                "entries": {
                    "tidy": {
                        "family": "tidy",
                        "kind": "procedure",
                        "skill": True,
                        "requires": [],
                        "mechanics": [],
                        "followups": [],
                        "note": "",
                    }
                },
            }
        )
    )
    project = Project.from_settings(
        tmp_path,
        {
            "sops": {"root": "procedures", "catalog": "procedures/index.json"},
            "usage": {"store": str(tmp_path / "records")},
        },
    )
    with project_scope(project):
        yield project


def test_the_outline_skips_headings_quoted_in_code_and_comments():
    assert sections.outline(PROCEDURE) == (
        "Tidy the notebook\n"
        "  Choosing a pass\n"
        "  One page\n"
        "    Checks\n"
        "  The whole notebook\n"
        "    Checks\n"
        "\n"
        "Modes (sop show NAME --mode MODE):\n"
        "  one-page: Choosing a pass; One page\n"
    )


def test_a_section_is_verbatim_and_carries_its_subsections():
    printed = sections.select(PROCEDURE, ["one page"])
    assert printed.startswith("## One page\n\nRead the page twice.\n")
    assert "### Checks\n\nRun `quirework text wrap --check`" in printed
    assert "## Not a heading, only an example" in printed
    assert "The whole notebook" not in printed


def test_sections_print_in_document_order_and_never_twice():
    printed = sections.select(
        PROCEDURE, ["One page", "Choosing a pass", "One page > Checks"]
    )
    assert printed.index("## Choosing a pass") < printed.index("## One page")
    assert printed.count("wrap --check") == 1


def test_a_repeated_title_is_refused_until_its_path_names_one():
    with pytest.raises(ValueError, match="ambiguous.*One page > Checks"):
        sections.select(PROCEDURE, ["Checks"])
    printed = sections.select(PROCEDURE, ["The whole notebook > Checks"])
    assert printed == "### Checks\n\nRun every gate.\n"


def test_an_unknown_section_names_the_headings_there_are():
    with pytest.raises(ValueError, match="no section 'Epilogue'.*'One page'"):
        sections.select(PROCEDURE, ["Epilogue"])


def test_a_mode_is_the_opening_and_its_declared_sections():
    printed = sections.mode(PROCEDURE, "one-page")
    assert printed.startswith("# Tidy the notebook\n")
    assert "Every pass ends with the gates." in printed
    assert "## Choosing a pass" in printed
    assert "Read the page twice." in printed
    assert "The whole notebook" not in printed
    with pytest.raises(ValueError, match="declares one-page"):
        sections.mode(PROCEDURE, "whole")


def test_a_malformed_or_repeated_mode_marker_is_refused():
    with pytest.raises(ValueError, match="NAME: Section"):
        sections.modes("<!-- @mode one-page -->")
    with pytest.raises(ValueError, match="declared twice"):
        sections.modes("<!-- @mode a: X -->\n<!-- @mode a: Y -->")


def test_a_renamed_heading_breaks_its_mode_at_the_gate(notebook):
    catalog.load()
    path = notebook.root / "procedures" / "tidy.md"
    path.write_text(PROCEDURE.replace("## One page", "## A single page"))
    with pytest.raises(ValueError, match="tidy: @mode one-page: no section 'One page'"):
        catalog.load()


def test_the_route_names_the_modes_only_when_there_are_some(notebook):
    body = commands._skill_md("tidy", "Tidy it.")
    assert "`quirework sop show tidy --mode MODE`" in body
    assert "- `one-page`: Choosing a pass; One page\n" in body
    path = notebook.root / "procedures" / "tidy.md"
    path.write_text(PROCEDURE.replace("<!-- @mode one-page", "<!-- note"))
    assert "--mode" not in commands._skill_md("tidy", "Tidy it.")


def test_a_generated_skill_is_at_the_wrapper_s_fixed_point(notebook):
    """A skill the project's own check names is a file nobody can keep.

    Wrapping it by hand makes `sop skills --check` call it stale, and
    regenerating it brings the drift back, so the generator wraps it.
    """
    from quirework.text import mdwrap

    path = notebook.root / ".claude" / "skills" / "tidy" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(commands._skill_md("tidy", "Tidy a small notebook."))
    assert mdwrap.at_fixed_point(str(path))


def test_a_partial_read_is_still_recorded_as_a_read_of_the_procedure(notebook, capsys):
    assert (
        invoke(("sop", "show"), ["tidy", "--section", "One page"], project=notebook)
        == 0
    )
    assert capsys.readouterr().out.startswith("## One page\n")
    (entry,) = usage.read(notebook)[0]
    assert entry["target"] == "tidy"


def test_the_name_comes_before_any_option(notebook, capsys):
    """Usage credits the first bare word; a title there must not be counted."""
    assert (
        invoke(("sop", "show"), ["--section", "One page", "tidy"], project=notebook)
        == 2
    )
    assert "usage: quirework sop show NAME" in capsys.readouterr().err
    (entry,) = usage.read(notebook)[0]
    assert "target" not in entry
