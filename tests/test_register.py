"""A register split across files: one shared spec, and a spec per unit.

A book argues one thing and keeps one spec. A site argues one thing per post,
so each post states its own commitments in its own file, and a shared file
holds what several posts lean on. The slugs stay one namespace, because a
binding names a slug and never a file. Every fixture here is fiction.
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

## The reader has never owned a kettle {#kettle-reader}

Someone who boils water in a pan.

## A whistle is a report, not a thought {#kettle-thesis}

The post leaves the reader with the whistle as a signal.
"""

PLANNED_SPEC = """# A planned post

## Lids matter {#lid-thesis}

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
        "## The whistle\n\n<!-- @bind(kettle-signal) -->\n<!-- @bind(kettle-thesis) -->\n"
    )
    with project_scope(Project.from_settings(root, SETTINGS)):
        yield root


def test_the_shared_spec_is_read_first_then_each_post_s_own(site):
    order = [(s.slug, s.path.parent.name) for s in notes.spec_sections()]
    assert order == [
        ("kettle-signal", "specs"),
        ("cooling-asymmetry", "specs"),
        ("kettle-reader", "notes-on-a-kettle"),
        ("kettle-thesis", "notes-on-a-kettle"),
        ("lid-thesis", "a-planned-post"),
    ]
    assert notes.spec_owner(site / "specs/notes-on-a-kettle/spec.md") == (
        "notes-on-a-kettle"
    )
    assert notes.spec_owner(site / "specs/_site.md") is None


def test_a_post_binds_its_own_commitments_without_saying_so(site):
    bound = notes.chapter_bindings()
    assert bound["notes-on-a-kettle"] == {
        "kettle-signal",
        "kettle-reader",
        "kettle-thesis",
    }
    # A spec for a post that is not written yet binds nothing: no post has it.
    assert "a-planned-post" not in bound


def test_a_slug_is_unique_across_every_file(site, capsys):
    (site / "specs" / "cooling-down").mkdir()
    (site / "specs" / "cooling-down" / "spec.md").write_text(
        "# Cooling Down\n\n## Again {#kettle-signal}\n\nA second claim.\n"
    )
    assert notes.check() == 1
    err = capsys.readouterr().err
    assert (
        "specs/cooling-down/spec.md: duplicate section anchor '#kettle-signal'" in err
    )
    assert "in specs/_site.md" in err


def test_a_spec_ahead_of_its_post_is_an_advisory(site, capsys):
    assert notes.check() == 0
    err = capsys.readouterr().err
    assert "belong to no post yet" in err
    assert "specs/a-planned-post/spec.md" in err


def test_the_primer_scoped_to_a_post_reads_only_what_it_touches(site, capsys):
    assert notes.main(["--primer", "-c", "notes-on-a-kettle"]) == 0
    out = capsys.readouterr().out
    assert "specs/notes-on-a-kettle/spec.md (post notes-on-a-kettle;" in out
    assert "#kettle-thesis" in out
    assert "#kettle-signal" in out  # the shared section it binds
    assert "#cooling-asymmetry" not in out  # shared, but not its concern
    assert "#lid-thesis" not in out  # another post's own


def test_a_post_with_a_spec_and_no_marks_has_a_primer(site, capsys):
    (site / "specs" / "cooling-down").mkdir()
    (site / "specs" / "cooling-down" / "spec.md").write_text(
        "# Cooling Down\n\n## The long route {#cooling-thesis}\n\nIt takes a while.\n"
    )
    assert notes.main(["--primer", "-c", "cooling-down"]) == 0
    out = capsys.readouterr().out
    assert "#cooling-thesis" in out
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
    commits, reason = trail._commits("kettle-thesis")
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
