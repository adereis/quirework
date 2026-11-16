"""The spec index, and one section printed with its context.

The index is generated from the published spec alone: a working register
beside it never shows, and neither do the units that express a section. A
stale index file fails `spec notes --check`, since people and tools read it
instead of the sections.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from quirework.manuscript import notes, specindex, specsection, specsummary
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
        found = specsection.context("flash")
    assert _sections(index)["flash"]["related_work"] == [
        {"file": "research/related.md", "citations": 2}
    ]
    assert "Beacon chapter" not in specindex.render_json(index)
    assert found["related_work"] == [
        {"file": "research/related.md", "heading": "Lamps"},
        {"file": "research/related.md", "heading": "Beacon chapter"},
    ]


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


def test_a_section_prints_as_its_file_holds_it(tmp_path, capsys):
    with project_scope(_project(tmp_path)):
        assert specsection.main(["keeper"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("spec/keeper.md\n\n## The keeper {#keeper}\n")
    assert "<!-- a note #fog that is not a citation -->" in out


def test_context_gathers_what_touches_a_section(tmp_path):
    project = _project(tmp_path, spec_trail="provenance/spec-trail.md")
    with project_scope(project):
        found = specsection.context("flash")
    assert [c["slug"] for c in found["cites"]] == ["fog", "keeper"]
    assert found["cites"][0]["first_sentence"] == "Fog hides the flash (#flash)."
    # Locally every spec file counts, the working register included.
    assert [c["slug"] for c in found["cited_by"]] == ["fog", "lamp-rule"]
    assert found["tested_by"] == [
        {"path": "drills/cold-start.md", "title": "Cold start"}
    ]
    assert found["units"] == [{"slug": "beacon-chapter", "title": "Beacon chapter"}]
    assert found["trail"] == 0
    rendered = specsection.render(found)
    assert "Bound by (1):\n  beacon-chapter  Beacon chapter" in rendered


def test_an_unknown_slug_is_refused_with_close_matches(tmp_path, capsys):
    with project_scope(_project(tmp_path)):
        assert specsection.main(["flsh", "--context"]) == 2
    err = capsys.readouterr().err
    assert "no spec section is anchored 'flsh'" in err
    assert "flash" in err


# A section's summary is its first paragraph when that paragraph carries the
# project's label in bold. The fog's labeled paragraph comes second, so the
# fog opens with no summary.
SUMMARIZED = {
    "flash.md": (
        "## The flash {#flash}\n\n**Summary.** The flash marks the rock\non a"
        " clear  night.\n\nThe flash marks the rock. It needs #fog to matter,"
        " and #keeper to run.\n"
    ),
    "fog.md": (
        "## The fog {#fog}\n\nFog hides the flash (#flash).\n\n"
        "**Summary.** A labeled paragraph that does not open the section.\n"
    ),
}


def _summarized(root, **extra):
    project = _project(root, spec_summary_label="Summary.", **extra)
    for name, text in SUMMARIZED.items():
        (root / "spec" / name).write_text(text)
    return project


def _advised(groups, start):
    return [lines for header, lines in groups if header.startswith(start)]


def test_summaries_are_off_until_the_label_is_set(tmp_path):
    project = _project(tmp_path)
    for name, text in SUMMARIZED.items():
        (tmp_path / "spec" / name).write_text(text)
    with project_scope(project):
        index = specindex.build()
        markdown = specindex.render_markdown(index, tmp_path / "i.md", tmp_path)
        _, groups = notes._findings(project.path("manuscript.source"))
    assert all("summary" not in s for s in index["sections"])
    assert "Under each table" not in markdown
    assert not any("summar" in header for header, _ in groups)


def test_a_summary_is_the_labeled_paragraph_that_opens_a_section(tmp_path):
    project = _summarized(tmp_path)
    (tmp_path / "spec" / "keeper.md").write_text(
        "## The keeper {#keeper}\n\n<!-- a draft -->\n\n**Summary.** Too late.\n"
    )
    with project_scope(project):
        found = _sections(specindex.build())
        assert specsummary.word_limit() == 70
    assert found["flash"]["summary"] == "The flash marks the rock on a clear night."
    assert "summary" in found["fog"] and found["fog"]["summary"] is None
    assert found["keeper"]["summary"] is None


def test_the_label_stands_alone_in_bold():
    def read(paragraph):
        return specsummary.summary(paragraph, "Summary.")

    assert read("**Summary.** Kept <!-- not this --> short.") == "Kept short."
    assert read("**Summary.**") == ""
    assert read("**Summary.**s and more") is None
    assert read("**Summary. All of it**") is None
    assert read("Summary. Not in bold.") is None


def test_the_markdown_index_puts_each_summary_under_its_table(tmp_path):
    with project_scope(_summarized(tmp_path)):
        text = specindex.render_markdown(
            specindex.build(), tmp_path / "docs" / "index.md", tmp_path
        )
    table = text.index("| [The fog](../spec/fog.md)")
    summary = text.index(
        "**The flash** (`flash`). The flash marks the rock on a clear night."
    )
    assert table < summary < text.index("- `flash` cites")
    assert "Under each table come the summaries" in text
    assert "(`fog`)" not in text
    assert mdwrap.reflow_columns(text) == text


def test_the_digest_lists_the_summaries_by_part_and_counts_them(tmp_path, capsys):
    project = _summarized(tmp_path, spec_digest="docs/digest.md")
    keeper = tmp_path / "spec" / "keeper.md"
    keeper.write_text(
        "## The keeper {#keeper}\n\n**Summary.** The keeper winds the clock.\n"
    )
    project.require("manuscript")
    with project_scope(project):
        assert [p.name for p in specindex.stale()] == ["digest.md"]
        assert specindex.main(["--write"]) == 0
        text = (tmp_path / "docs" / "digest.md").read_text()
        docs = project.path("manuscript.source")
        assert not any("stale" in p for p in notes._findings(docs)[0])
        assert specindex.main([]) == 0
        keeper.write_text(
            "## The keeper {#keeper}\n\n**Summary.** The keeper trims the wick.\n"
        )
        problems, _ = notes._findings(docs)
    assert "2 of 3 sections have a summary" in text
    order = [
        "## The light",
        "**[The flash](../spec/flash.md)** (`flash`). The flash marks",
        "## The keeper",
        "**[The keeper](../spec/keeper.md)** (`keeper`). The keeper winds",
    ]
    assert [text.index(s) for s in order] == sorted(text.index(s) for s in order)
    assert "fog" not in text
    assert mdwrap.reflow_columns(text) == text
    printed = capsys.readouterr().out
    assert "; 2 with a summary" in printed
    assert "digest: docs/digest.md" in printed
    assert [p for p in problems if "stale" in p] == [
        f"docs/digest.md: the spec digest is stale; run `{specindex.COMMAND}`"
    ]


def test_notes_check_refuses_a_summary_that_cites_a_section(tmp_path):
    project = _summarized(tmp_path)
    (tmp_path / "spec" / "fog.md").write_text(
        "## The fog {#fog}\n\n**Summary.** Fog hides the flash (#flash) and\n"
        "the `#keeper` too, but not `#include`.\n"
    )
    (tmp_path / "spec" / "keeper.md").write_text(
        "## The keeper {#keeper}\n\n**Summary.**\n\nThe keeper winds the clock.\n"
    )
    with project_scope(project):
        problems, _ = notes._findings(project.path("manuscript.source"))
    cited = [p for p in problems if "the summary cites" in p]
    assert [p.split(". ")[0] for p in cited] == [
        "spec/fog.md#fog: the summary cites '#flash'",
        "spec/fog.md#fog: the summary cites '#keeper'",
    ]
    assert any(
        p.startswith("spec/keeper.md#keeper: the summary label **Summary.** has")
        for p in problems
    )


def test_notes_check_advises_long_summaries_and_counts_the_missing(tmp_path):
    """Every summary is held to the limit, the working register's included;
    the sections still without one are counted over the published spec."""
    project = _summarized(tmp_path, spec_summary_words=5)
    (tmp_path / "manuscript" / "sections" / "_working.md").write_text(
        "# Working\n\n## The lamp rule {#lamp-rule}\n\n"
        "**Summary.** A rule for lamps kept off the page entirely.\n\nSee #flash.\n"
    )
    with project_scope(project):
        problems, groups = notes._findings(project.path("manuscript.source"))
    assert not any("summary" in p for p in problems)
    assert _advised(groups, "{n} spec summary/summaries run past 5 words") == [
        [
            "  spec/flash.md#flash — 9 words",
            "  manuscript/sections/_working.md#lamp-rule — 9 words",
        ]
    ]
    assert _advised(groups, "spec sections with no summary yet") == [
        ["  2 of 3 published section(s) open with no **Summary.** paragraph"]
    ]
    # With no lower bound set, no summary is too short.
    assert not any("run under" in header for header, _ in groups)


def test_notes_check_advises_short_summaries_under_a_lower_bound(tmp_path):
    """A target length with a tolerance either way: the flash's nine words
    sit inside it, the keeper's three fall short and the fog's fifteen run
    past it, each named once and in its own advisory."""
    project = _summarized(tmp_path, spec_summary_words=10, spec_summary_min_words=5)
    (tmp_path / "spec" / "keeper.md").write_text(
        "## The keeper {#keeper}\n\n**Summary.** The keeper winds.\n"
    )
    (tmp_path / "spec" / "fog.md").write_text(
        "## The fog {#fog}\n\n**Summary.** Fog rolls in off the cold sea and"
        " hides the flash from every passing ship.\n"
    )
    with project_scope(project):
        assert specsummary.word_floor() == 5
        problems, groups = notes._findings(project.path("manuscript.source"))
    assert not any("summary" in p for p in problems)
    assert _advised(groups, "{n} spec summary/summaries run under 5 words") == [
        ["  spec/keeper.md#keeper — 3 words"]
    ]
    assert _advised(groups, "{n} spec summary/summaries run past 10 words") == [
        ["  spec/fog.md#fog — 15 words"]
    ]


def test_a_lower_bound_equal_to_the_limit_asks_for_one_length(tmp_path):
    project = _summarized(tmp_path, spec_summary_words=9, spec_summary_min_words=9)
    project.require("manuscript")
    with project_scope(project):
        _, groups = notes._findings(project.path("manuscript.source"))
    assert not any(
        "run under" in header or "run past" in header for header, _ in groups
    )


@pytest.mark.parametrize(
    ("extra", "says"),
    [
        ({"spec_summary_label": ""}, "the text a summary opens with"),
        ({"spec_summary_label": " Summary."}, "the text a summary opens with"),
        ({"spec_summary_label": 5}, "the text a summary opens with"),
        ({"spec_summary_words": 50}, "set the label too"),
        ({"spec_digest": "docs/digest.md"}, "set the label too"),
        ({"spec_summary_label": "S.", "spec_summary_words": 0}, "a whole number"),
        ({"spec_summary_label": "S.", "spec_summary_words": True}, "a whole number"),
        ({"spec_summary_min_words": 20}, "set the label too"),
        ({"spec_summary_label": "S.", "spec_summary_min_words": 0}, "a whole number"),
        (
            {"spec_summary_label": "S.", "spec_summary_min_words": "20"},
            "a whole number",
        ),
        (
            {"spec_summary_label": "S.", "spec_summary_min_words": 80},
            "above the 70-word limit",
        ),
        (
            {
                "spec_summary_label": "S.",
                "spec_summary_words": 30,
                "spec_summary_min_words": 31,
            },
            "above the 30-word limit",
        ),
        ({"spec_summary_label": "S.", "spec_digest": "../d.md"}, "outside the project"),
        (
            {"spec_summary_label": "S.", "spec_digest": "spec/fog.md"},
            "a spec file the digest would overwrite",
        ),
        (
            {
                "spec_summary_label": "S.",
                "spec_digest": "docs/index.md",
                "spec_index": {"markdown": "docs/index.md"},
            },
            "the index's markdown file",
        ),
    ],
)
def test_the_summary_settings_are_validated(tmp_path, extra, says):
    with pytest.raises(ConfigurationError, match=says):
        _project(tmp_path, **extra).require("manuscript")


# A summary is easiest to leave behind in the edit that moved its section, so
# a change that rewrites a section around a summary it leaves alone is named,
# for whoever made it to confirm the summary still says what the section holds.
FLASH = SUMMARIZED["flash.md"]
REWORDED = FLASH.replace("and #keeper to run.", "and #keeper to wind it.")


def _git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "A. Fictional Author",
            "GIT_AUTHOR_EMAIL": "author@example.invalid",
            "GIT_COMMITTER_NAME": "A. Fictional Author",
            "GIT_COMMITTER_EMAIL": "author@example.invalid",
            "PATH": "/usr/bin:/bin",
            "HOME": str(root),
        },
    )


def test_a_section_rewritten_around_its_summary_is_named(tmp_path):
    spec = tmp_path / "spec" / "flash.md"
    with project_scope(_summarized(tmp_path)):
        assert specsummary.unrevised({spec: FLASH}, {spec: REWORDED}) == ["flash"]
        revised = REWORDED.replace("a clear  night", "a clear or a misty night")
        assert specsummary.unrevised({spec: FLASH}, {spec: revised}) == [], (
            "a summary changed with its section"
        )
        rewrapped = REWORDED.replace("the rock\non a", "the rock on\na")
        assert specsummary.unrevised({spec: FLASH}, {spec: rewrapped}) == ["flash"], (
            "a summary rewrapped is the summary it was"
        )


def test_a_rewrap_a_mark_or_a_blank_line_outside_the_summary_is_no_rewrite(
    tmp_path,
):
    spec = tmp_path / "spec" / "flash.md"
    rewrapped = FLASH.replace("It needs #fog", "It needs\n#fog")
    spaced = FLASH.replace("the rock. It", "the rock.  It")
    split = FLASH.replace("the rock. It", "the rock.\n\nIt")
    marked = FLASH.replace(
        "The flash marks the rock. It",
        "<!-- @todo how bright -->\nThe flash marks the rock. It",
    )
    with project_scope(_summarized(tmp_path)):
        for after in (rewrapped, spaced, split, marked):
            assert specsummary.unrevised({spec: FLASH}, {spec: after}) == []


def test_a_first_summary_a_lost_one_a_new_or_a_deleted_section_is_not_named(
    tmp_path,
):
    spec, fog = tmp_path / "spec" / "flash.md", tmp_path / "spec" / "fog.md"
    plain = SECTIONS["fog.md"]
    summarized = (
        "## The fog {#fog}\n\n**Summary.** Fog hides the light from ships.\n\n"
        "Fog hides the flash from every ship at sea.\n"
    )
    lost = REWORDED.replace(
        "**Summary.** The flash marks the rock\non a clear  night.\n\n", ""
    )
    with project_scope(_summarized(tmp_path)):
        assert specsummary.unrevised({fog: plain}, {fog: summarized}) == []
        assert specsummary.unrevised({spec: FLASH}, {spec: lost}) == []
        assert (
            specsummary.unrevised(
                {spec: FLASH, fog: ""}, {spec: FLASH, fog: summarized}
            )
            == []
        ), "a new section with a summary"
        assert specsummary.unrevised({spec: FLASH}, {spec: ""}) == []


def test_a_moved_section_is_compared_by_its_slug(tmp_path):
    spec, keeper = tmp_path / "spec" / "flash.md", tmp_path / "spec" / "keeper.md"
    before = {spec: FLASH, keeper: SECTIONS["keeper.md"]}
    with project_scope(_summarized(tmp_path)):
        moved = {spec: "", keeper: SECTIONS["keeper.md"] + "\n" + FLASH}
        assert specsummary.unrevised(before, moved) == []
        moved[keeper] = SECTIONS["keeper.md"] + "\n" + REWORDED
        assert specsummary.unrevised(before, moved) == ["flash"]


def test_without_the_label_no_section_is_named_and_nothing_runs(tmp_path):
    spec = tmp_path / "spec" / "flash.md"
    with project_scope(_project(tmp_path)):
        assert specsummary.unrevised({spec: FLASH}, {spec: REWORDED}) == []
        # No repository here: off, it never asks git.
        assert specsummary.staged_unrevised() == []


def test_only_what_is_staged_is_compared_with_head_for_summaries(tmp_path):
    spec = tmp_path / "spec" / "flash.md"
    with project_scope(_summarized(tmp_path)):
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-q", "-m", "Light the lamp")
        assert specsummary.staged_unrevised() == []
        spec.write_text(REWORDED)
        assert specsummary.staged_unrevised() == [], "an unstaged edit"
        _git(tmp_path, "add", "spec/flash.md")
        assert specsummary.staged_unrevised() == ["flash"]
        spec.write_text(REWORDED.replace("a clear  night", "a misty night"))
        assert specsummary.staged_unrevised() == ["flash"]
        _git(tmp_path, "add", "spec/flash.md")
        assert specsummary.staged_unrevised() == []


def test_a_project_directory_can_be_named_for_the_staged_summaries(tmp_path):
    _summarized(tmp_path)
    (tmp_path / "quirework.toml").write_text(
        "schema = 2\n\n[manuscript]\n"
        'source = "manuscript"\nchapters = "manuscript/sections"\n'
        'quarto = "manuscript/_quarto.yml"\nspec = "spec/manifest.toml"\n'
        'spec_summary_label = "Summary."\n'
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "Light the lamp")
    (tmp_path / "spec" / "flash.md").write_text(REWORDED)
    _git(tmp_path, "add", "spec/flash.md")
    assert specsummary.staged_unrevised(tmp_path) == ["flash"]


# The summaries are read through one function, and printed wherever a section
# is printed for an agent or a reader: by `spec summary`, in the primer's
# skeleton and in `spec section`.
GIST = "The flash marks the rock on a clear night."
NONE = "    no summary: the section opens with no **Summary.** paragraph"


def _unlabeled(root):
    """The summarized spec in a project that labels no summaries."""
    project = _project(root)
    for name, text in SUMMARIZED.items():
        (root / "spec" / name).write_text(text)
    return project


def test_one_reader_serves_every_summary(tmp_path):
    project = _summarized(tmp_path)
    (tmp_path / "spec" / "keeper.md").write_text(
        "## The keeper {#keeper}\n\n**Summary.**\n\nThe keeper winds the clock.\n"
    )
    with project_scope(project):
        found = specsummary.summaries()
        assert specsummary.summaries(files=[tmp_path / "spec" / "flash.md"]) == {
            "flash": GIST
        }
    assert found == {"flash": GIST, "fog": None, "keeper": None, "lamp-rule": None}
    assert list(found) == ["flash", "fog", "keeper", "lamp-rule"], "register order"
    with project_scope(_unlabeled(tmp_path / "off")):
        assert specsummary.summaries() == dict.fromkeys(found), "off, none has one"


def test_spec_summary_prints_each_named_section_and_says_which_has_none(
    tmp_path, capsys
):
    with project_scope(_summarized(tmp_path)):
        assert specsummary.main(["flash", "#fog"]) == 1
        assert capsys.readouterr().out == (
            f"#flash  The flash\n    {GIST}\n#fog  The fog\n{NONE}\n"
        )
        assert specsummary.main(["flash"]) == 0
        capsys.readouterr()
        # A named section is found in any spec file, the working register's too.
        assert specsummary.main(["lamp-rule"]) == 1
        assert capsys.readouterr().out.startswith("#lamp-rule  The lamp rule\n")


def test_a_long_summary_is_filled_under_its_title(tmp_path, capsys):
    project = _summarized(tmp_path)
    (tmp_path / "spec" / "fog.md").write_text(
        "## The fog {#fog}\n\n**Summary.** Fog rolls in off the cold sea at dusk"
        " and hides the flash from every passing ship, so the keeper sounds the"
        " horn instead of trusting the lamp.\n"
    )
    with project_scope(project):
        assert specsummary.main(["fog"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "#fog  The fog"
    assert len(out) > 2 and all(
        line.startswith("    ") and len(line) <= 72 for line in out[1:]
    )


def test_spec_summary_all_lists_the_published_sections_by_part(tmp_path, capsys):
    project = _summarized(tmp_path)
    (tmp_path / "spec" / "keeper.md").write_text(
        "## The keeper {#keeper}\n\n**Summary.** The keeper winds the clock.\n"
    )
    (tmp_path / "manuscript" / "sections" / "_working.md").write_text(
        "# Working\n\n## The lamp rule {#lamp-rule}\n\n**Summary.** Kept off it.\n"
    )
    with project_scope(project):
        assert specsummary.main(["--all"]) == 1, "the fog has none"
    assert capsys.readouterr().out == (
        f"The light\n  #flash  The flash\n      {GIST}\n  #fog  The fog\n"
        f"  {NONE}\n\nThe keeper\n  #keeper  The keeper\n"
        "      The keeper winds the clock.\n\n"
        "2 of 3 published section(s) open with a summary\n"
    )


def test_spec_summary_missing_names_the_published_sections_without_one(
    tmp_path, capsys
):
    project = _summarized(tmp_path)
    fog = tmp_path / "spec" / "fog.md"
    with project_scope(project):
        assert specsummary.main(["--missing"]) == 1
        assert capsys.readouterr().out == (
            "The light\n  #fog  The fog\n\nThe keeper\n  #keeper  The keeper\n\n"
            "2 of 3 published section(s) open with no **Summary.** paragraph\n"
        )
        for path, title in ((fog, "fog"), (tmp_path / "spec" / "keeper.md", "keeper")):
            path.write_text(
                f"## The {title} {{#{title}}}\n\n**Summary.** The {title} holds.\n"
            )
        assert specsummary.main(["--missing"]) == 0
        assert capsys.readouterr().out == (
            "every published section opens with a **Summary.** paragraph\n"
        )
        assert specsummary.main(["--missing", "--json"]) == 0
        assert json.loads(capsys.readouterr().out) == []


def test_spec_summary_prints_json_for_tools(tmp_path, capsys):
    with project_scope(_summarized(tmp_path)):
        assert specsummary.main(["flash", "fog", "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == [
        {
            "slug": "flash",
            "title": "The flash",
            "part": "The light",
            "file": "spec/flash.md",
            "summary": GIST,
        },
        {
            "slug": "fog",
            "title": "The fog",
            "part": "The light",
            "file": "spec/fog.md",
            "summary": None,
        },
    ]


@pytest.mark.parametrize(
    ("argv", "says"),
    [
        ([], "name the sections, or list them"),
        (["flash", "--all"], "name the sections, or list them"),
        (["flsh"], "did you mean flash"),
    ],
)
def test_spec_summary_refuses_what_it_cannot_answer(tmp_path, capsys, argv, says):
    with project_scope(_summarized(tmp_path)):
        assert specsummary.main(argv) == 2
    assert says in capsys.readouterr().err


def test_spec_summary_needs_the_label(tmp_path, capsys):
    with project_scope(_project(tmp_path)):
        assert specsummary.main(["flash"]) == 2
    assert "summaries are off; set manuscript.spec_summary_label" in (
        capsys.readouterr().err
    )


def test_the_primer_prints_each_summary_under_its_title(tmp_path, capsys):
    with project_scope(_summarized(tmp_path)):
        assert notes.main(["--primer"]) == 0
    out = capsys.readouterr().out
    assert "@bind(flash)" in out, "the marks follow the skeleton"
    flash = next(line for line in out.splitlines() if "#flash" in line)
    assert f"{flash}\n      {GIST}\n  #fog" in out
    assert (
        "  1 of 4 section(s) open with a summary, printed under the title: "
        "quirework spec summary SLUG"
    ) in out
    with project_scope(_unlabeled(tmp_path / "off")):
        assert notes.main(["--primer"]) == 0
    out = capsys.readouterr().out
    assert GIST not in out and "open with a summary" not in out


def test_spec_section_leads_with_the_summary_where_summaries_are_labeled(
    tmp_path, capsys
):
    with project_scope(_summarized(tmp_path)):
        assert specsection.main(["flash", "--json"]) == 0
        found = json.loads(capsys.readouterr().out)
        context = specsection.context("fog")
        rendered = specsection.render(context)
        around = specsection.context("flash")
    assert list(found) == ["slug", "title", "file", "summary", "text"]
    assert found["summary"] == GIST
    assert list(context)[3:5] == ["summary", "text"] and context["summary"] is None
    assert context["cites"][0]["summary"] == GIST
    # A neighbour with a summary is shown by it; one without, by its first
    # sentence, as before.
    assert f"Cites (1):\n  #flash  The flash\n      {GIST}\n" in rendered
    assert around["cites"][0]["summary"] is None
    assert "  #fog  The fog\n      Fog hides the flash (#flash).\n" in (
        specsection.render(around)
    )


def test_spec_section_is_what_it_was_without_the_label(tmp_path, capsys):
    with project_scope(_unlabeled(tmp_path)):
        assert specsection.main(["flash", "--json"]) == 0
        found = json.loads(capsys.readouterr().out)
        context = specsection.context("fog")
    assert list(found) == ["slug", "title", "file", "text"]
    assert "summary" not in context and "summary" not in context["cites"][0]
    assert context["cites"][0]["first_sentence"].startswith("**Summary.**")
