"""A register split across files: one shared spec, and a spec per unit.

A book argues one thing and keeps one spec. A site argues one thing per post,
so each post states its own commitments in its own file, and a shared file
holds what several posts lean on. A post's own section is named `post/anchor`,
so two posts may each have a `#thesis`, and a binding still names exactly one
section. Every fixture here is fiction.
"""

import subprocess

import pytest

from quirework.manuscript import notes, trail
from quirework.project import ConfigurationError, Project, project_scope

SHARED = """# What the kettle blog holds

## A kettle signals without thinking {#kettle-signal}

A whistle reports a state.

## Cooling is not the reverse of boiling {#cooling-asymmetry}

A kettle cools by another route.
"""

KETTLE_SPEC = """# Notes on a Kettle

## The reader has never owned a kettle {#reader}

Someone who boils water in a pan.

## A whistle is a report, not a thought {#thesis}

The post leaves the reader with the whistle as a signal.
"""

PLANNED_SPEC = """# A planned post

## Lids matter {#thesis}

A lid changes how long the boil takes.
"""


def _page(root, slug, title, date, body):
    page = root / "content" / "posts" / slug / "index.md"
    page.parent.mkdir(parents=True)
    page.write_text(f"+++\ntitle = '{title}'\ndate = '{date}'\n+++\n\n{body}")


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


SETTINGS = {
    "manuscript": {
        "format": "pages",
        "source": "content",
        "pages": "posts/*/index.md",
        "marks": "specs/{slug}/marks.md",
        "spec": "specs/_site.md",
        "unit_spec": "specs/{slug}/spec.md",
        "unit_name": "post",
    }
}


@pytest.fixture
def site(tmp_path, monkeypatch):
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "site"
    _page(
        root, "notes-on-a-kettle", "Notes on a Kettle", "2031-04-02", "## The whistle\n"
    )
    _page(root, "cooling-down", "Cooling Down", "2031-05-01", "## The long route\n")
    specs = root / "specs"
    for slug, text in (
        ("notes-on-a-kettle", KETTLE_SPEC),
        ("a-planned-post", PLANNED_SPEC),
    ):
        (specs / slug).mkdir(parents=True)
        (specs / slug / "spec.md").write_text(text)
    (specs / "_site.md").write_text(SHARED)
    (specs / "notes-on-a-kettle" / "marks.md").write_text(
        "## The whistle\n\n<!-- @bind(kettle-signal) -->\n<!-- @bind(thesis) -->\n"
    )
    with project_scope(Project.from_settings(root, SETTINGS)):
        yield root


def test_the_shared_spec_is_read_first_then_each_post_s_own(site):
    order = [(s.slug, s.path.parent.name) for s in notes.spec_sections()]
    assert order == [
        ("kettle-signal", "specs"),
        ("cooling-asymmetry", "specs"),
        ("notes-on-a-kettle/reader", "notes-on-a-kettle"),
        ("notes-on-a-kettle/thesis", "notes-on-a-kettle"),
        ("a-planned-post/thesis", "a-planned-post"),
    ]
    assert notes.spec_owner(site / "specs/notes-on-a-kettle/spec.md") == (
        "notes-on-a-kettle"
    )
    assert notes.spec_owner(site / "specs/_site.md") is None


def test_a_post_binds_its_own_commitments_without_saying_so(site):
    bound = notes.chapter_bindings()
    # The bare `thesis` in its own marks is its own section.
    assert bound["notes-on-a-kettle"] == {
        "kettle-signal",
        "notes-on-a-kettle/reader",
        "notes-on-a-kettle/thesis",
    }
    # A spec for a post that is not written yet binds nothing: no post has it.
    assert "a-planned-post" not in bound


def test_a_shared_slug_is_unique_across_the_shared_spec(site, capsys):
    (site / "specs" / "_site.md").write_text(
        SHARED + "\n## Again {#kettle-signal}\n\nA second claim.\n"
    )
    assert notes.check() == 1
    err = capsys.readouterr().err
    assert "specs/_site.md: duplicate section anchor '#kettle-signal'" in err


def test_two_posts_may_each_have_a_thesis(site, capsys):
    assert notes.check() == 0, capsys.readouterr().err


def test_a_post_s_anchor_may_not_shadow_a_shared_one(site, capsys):
    """In a post's own marks a bare name finds its own section first, so a
    shared section of the same name would be out of that post's reach."""
    (site / "specs" / "cooling-down").mkdir()
    (site / "specs" / "cooling-down" / "spec.md").write_text(
        "# Cooling Down\n\n## Again {#kettle-signal}\n\nA second claim.\n"
    )
    assert notes.check() == 1
    err = capsys.readouterr().err
    assert (
        "specs/cooling-down/spec.md: anchor '#kettle-signal' is also a shared "
        "section" in err
    )


def test_an_anchor_cannot_carry_the_separator(site, capsys):
    (site / "specs" / "_site.md").write_text(SHARED + "\n## Odd {#odd/one}\n\nx\n")
    assert notes.check() == 1
    assert "anchor '#odd/one' contains '/'" in capsys.readouterr().err


def test_another_post_names_a_post_s_section_in_full(site, capsys):
    (site / "specs" / "cooling-down").mkdir()
    (site / "specs" / "cooling-down" / "marks.md").write_text(
        "## The long route\n\n<!-- @bind(notes-on-a-kettle/thesis) -->\n"
    )
    assert notes.check() == 0, capsys.readouterr().err
    assert "notes-on-a-kettle/thesis" in notes.chapter_bindings()["cooling-down"]
    assert notes.main(["bind", "--slug", "notes-on-a-kettle/thesis"]) == 0
    out = capsys.readouterr().out
    # Its own post binds it by the bare name; the other post, in full.
    assert "specs/notes-on-a-kettle/marks.md" in out
    assert "specs/cooling-down/marks.md" in out


def test_a_bare_name_only_reaches_a_post_s_own_sections(site, capsys):
    (site / "specs" / "cooling-down").mkdir()
    (site / "specs" / "cooling-down" / "marks.md").write_text(
        "## The long route\n\n<!-- @bind(thesis) -->\n"
    )
    assert notes.check() == 1
    assert "@bind('thesis') binds no section" in capsys.readouterr().err


def test_a_spec_ahead_of_its_post_is_an_advisory(site, capsys):
    assert notes.check() == 0
    err = capsys.readouterr().err
    assert "belong to no post yet" in err
    assert "specs/a-planned-post/spec.md" in err


def test_the_primer_scoped_to_a_post_reads_only_what_it_touches(site, capsys):
    assert notes.main(["--primer", "-c", "notes-on-a-kettle"]) == 0
    out = capsys.readouterr().out
    assert "specs/notes-on-a-kettle/spec.md (post notes-on-a-kettle;" in out
    assert "#notes-on-a-kettle/thesis" in out
    assert "#kettle-signal" in out  # the shared section it binds
    assert "#cooling-asymmetry" not in out  # shared, but not its concern
    assert "#a-planned-post/thesis" not in out  # another post's own


def test_a_post_with_a_spec_and_no_marks_has_a_primer(site, capsys):
    (site / "specs" / "cooling-down").mkdir()
    (site / "specs" / "cooling-down" / "spec.md").write_text(
        "# Cooling Down\n\n## The long route {#route}\n\nIt takes a while.\n"
    )
    assert notes.main(["--primer", "-c", "cooling-down"]) == 0
    out = capsys.readouterr().out
    assert "#cooling-down/route" in out
    assert "no marks match" in out


def test_suggest_follows_the_file_that_holds_the_section(site):
    git(site, "init", "-q")
    git(site, "add", ".")
    git(
        site,
        "-c",
        "user.name=A. Writer",
        "-c",
        "user.email=writer@example.invalid",
        "commit",
        "-qm",
        "Specify the kettle post",
    )
    commits, reason = trail._commits("notes-on-a-kettle/thesis")
    assert reason is None or not reason
    assert [c[2] for c in commits] == ["Specify the kettle post"]
    assert trail._commits("no-such-section") == (
        [],
        "'#no-such-section' is in no spec file",
    )


def test_a_trail_may_follow_post_specs_alone(tmp_path):
    settings = {
        "manuscript": {
            **SETTINGS["manuscript"],
            "spec_trail": "private/trail.md",
        }
    }
    del settings["manuscript"]["spec"]
    (tmp_path / "content").mkdir()
    Project.from_settings(tmp_path, settings).require("manuscript")


def test_a_unit_spec_pattern_must_name_the_slug(tmp_path):
    (tmp_path / "content").mkdir()
    settings = {"manuscript": {**SETTINGS["manuscript"], "unit_spec": "specs/spec.md"}}
    del settings["manuscript"]["spec"]
    with pytest.raises(ConfigurationError, match="must contain {slug}"):
        Project.from_settings(tmp_path, settings).require("manuscript")


def test_a_project_with_no_register_says_how_to_declare_one(tmp_path):
    settings = {"manuscript": {"source": "content"}}
    with project_scope(Project.from_settings(tmp_path, settings)):
        with pytest.raises(ConfigurationError, match="manuscript.unit_spec"):
            notes.spec_sections()


def test_a_bare_anchor_outside_its_marks_is_answered_with_the_full_name(site):
    """Outside a post's marks a bare anchor names nothing, and `thesis` is too
    far from `notes-on-a-kettle/thesis` for a fuzzy match to offer it."""
    with pytest.raises(LookupError, match="named in full: a-planned-post/thesis, "):
        trail._known_slug("thesis", trail._spec_titles())
    with pytest.raises(LookupError, match="close: kettle-signal"):
        trail._known_slug("kettle-signl", trail._spec_titles())
    trail._known_slug("notes-on-a-kettle/thesis", trail._spec_titles())


def test_a_trail_heading_carries_a_post_s_section_in_full(site):
    """Breadcrumbs are kept by a section's full name, so the trail's heading
    for a post's own section reads `{#post/anchor}` and parses back to it."""
    text = "## A whistle is a report {#notes-on-a-kettle/thesis}\n"
    sidecar = trail.parse(text, site / "trail.md")
    assert [s.slug for s in sidecar.sections] == ["notes-on-a-kettle/thesis"]
    assert trail.structure_problems(None, sidecar) == []
