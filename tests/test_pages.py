"""The `pages` manuscript format: a site's posts as the units of the work.

A static site keeps each post as a page bundle (`posts/<slug>/index.md`), opens
it with TOML or YAML frontmatter, and renders everything under its source. So
the slug comes from the directory, the order and the draft flag from the
frontmatter, and the marks live outside the rendered tree, at a path pattern
the project names. Every fixture here is fiction.
"""

import subprocess

import pytest

from quirework.manuscript import frontmatter, notes, trail, units
from quirework.project import ConfigurationError, Project, project_scope

SPEC = """# What the kettle blog holds

## A kettle signals without thinking {#kettle-signal}

A whistle reports a state.

## Cooling is not the reverse of boiling {#cooling-asymmetry}

A kettle cools by another route.
"""

KETTLE = """+++
date = '2031-04-02T09:00:00-03:00'
title = 'Notes on a Kettle'
draft = false
# a TOML comment is not a heading
+++

The kettle whistles.

## The whistle

A whistle reports a state.
"""

COOLING = """---
title: "Cooling Down"
date: 2031-05-01
draft: true
tags:
  - kitchens
---

## The long route

It cools by another route.
"""

KETTLE_MARKS = """<!-- @guidance open on the whistle -->

## The whistle

<!-- @bind(kettle-signal) -->
"""


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _site(root):
    posts = root / "content" / "posts"
    for slug, text in (("notes-on-a-kettle", KETTLE), ("cooling-down", COOLING)):
        (posts / slug).mkdir(parents=True)
        (posts / slug / "index.md").write_text(text)
    # The section's list page is a page of the site, not a post.
    (posts / "_index.md").write_text("+++\ntitle = 'Posts'\n+++\n")
    specs = root / "specs"
    (specs / "notes-on-a-kettle").mkdir(parents=True)
    (specs / "notes-on-a-kettle" / "marks.md").write_text(KETTLE_MARKS)
    (specs / "_site.md").write_text(SPEC)
    return root


SETTINGS = {
    "manuscript": {
        "format": "pages",
        "source": "content",
        "pages": "posts/*/index.md",
        "marks": "specs/{slug}/marks.md",
        "spec": "specs/_site.md",
        "unit_name": "post",
    }
}


@pytest.fixture
def site(tmp_path, monkeypatch):
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    root = _site(tmp_path / "site")
    with project_scope(Project.from_settings(root, SETTINGS)):
        yield root


def test_a_post_is_named_by_its_bundle_and_ordered_by_date(site):
    found = units.registry()
    assert [u.slug for u in found] == ["notes-on-a-kettle", "cooling-down"]
    assert [u.title for u in found] == ["Notes on a Kettle", "Cooling Down"]
    assert [u.draft for u in found] == [False, True]
    assert found[0].rel == "content/posts/notes-on-a-kettle/index.md"
    assert found[1].date == "2031-05-01"
    assert all(u.number is None for u in found)


def test_both_frontmatter_conventions_are_read():
    assert frontmatter.fields(KETTLE)["title"] == "Notes on a Kettle"
    yaml = frontmatter.fields(COOLING)
    assert yaml["draft"] is True
    assert "tags" not in yaml  # a list is skipped, never guessed at
    with pytest.raises(ValueError, match="not valid TOML"):
        frontmatter.fields("+++\ntitle = \n+++\n")


def test_a_sidecar_outside_the_source_is_attributed_to_its_post(site):
    marks = notes.scan()
    bind = next(a for a in marks if a.kind == "bind")
    assert bind.file == "content/posts/notes-on-a-kettle/index.md"
    assert bind.source == "specs/notes-on-a-kettle/marks.md"
    assert bind.section == "The whistle"
    assert notes.chapter_bindings() == {"notes-on-a-kettle": {"kettle-signal"}}


def test_a_clean_site_passes_the_gate(site, capsys):
    assert notes.check() == 0
    assert "sidecar layout clean" in capsys.readouterr().out


def test_a_toml_comment_is_not_a_heading(site, capsys):
    """The frontmatter's `# a TOML comment` must not join the heading mirror."""
    labels = [label for _, label in notes._headings(KETTLE)]
    assert labels == ["The whistle"]


def test_a_mark_in_a_post_names_where_it_belongs(site, capsys):
    page = site / "content" / "posts" / "cooling-down" / "index.md"
    page.write_text(COOLING + "\n<!-- @todo say what cooling costs -->\n")
    assert notes.check() == 1
    err = capsys.readouterr().err
    assert "mark left in prose — move it to specs/cooling-down/marks.md" in err


def test_a_sidecar_for_a_post_that_is_gone_is_an_orphan(site, capsys):
    (site / "specs" / "retired-post").mkdir()
    (site / "specs" / "retired-post" / "marks.md").write_text("<!-- @todo x -->\n")
    assert notes.check() == 1
    assert "orphan sidecar — no post 'retired-post'" in capsys.readouterr().err


def test_a_drifted_mirror_names_the_post(site, capsys):
    marks = site / "specs" / "notes-on-a-kettle" / "marks.md"
    marks.write_text(KETTLE_MARKS.replace("## The whistle", "## The steam"))
    assert notes.check() == 1
    err = capsys.readouterr().err
    assert "heading 'The steam' has no matching heading" in err
    assert "content/posts/notes-on-a-kettle/index.md" in err


def test_two_posts_sharing_a_slug_are_refused(site, capsys):
    other = site / "content" / "notes" / "cooling-down"
    other.mkdir(parents=True)
    (other / "index.md").write_text("+++\ntitle = 'Again'\n+++\n")
    settings = {"manuscript": {**SETTINGS["manuscript"], "pages": "*/*/index.md"}}
    with project_scope(Project.from_settings(site, settings)):
        problems = units.order_problems()
    assert any("slug 'cooling-down' is also" in p for p in problems)


def test_a_reference_to_a_post_resolves_by_slug(site):
    marks = site / "specs" / "notes-on-a-kettle" / "marks.md"
    marks.write_text(
        KETTLE_MARKS + "<!-- @guidance read after @ch(cooling-down) -->\n"
        "<!-- @guidance and after @ch(no-such-post) -->\n"
    )
    unresolved = units.scan_layers()
    assert [h.token for h in unresolved] == ["@ch(no-such-post)"]
    assert unresolved[0].note.startswith("no such post")


def test_the_units_listing_marks_drafts(site, capsys):
    assert units.main([]) == 0
    out = capsys.readouterr().out
    assert "draft  cooling-down" in out
    assert "notes-on-a-kettle" in out


def test_the_primer_reads_a_post_by_its_slug(site, capsys):
    assert notes.main(["--primer", "-c", "notes-on-a-kettle"]) == 0
    out = capsys.readouterr().out
    assert "Notes on a Kettle" in out
    assert "@bind(kettle-signal)" in out
    assert "open on the whistle" in out


def test_a_trail_view_takes_the_post_s_slug(site):
    settings = {
        "manuscript": {**SETTINGS["manuscript"], "spec_trail": "private/trail.md"}
    }
    with project_scope(Project.from_settings(site, settings)):
        lines = trail.unit_view("notes-on-a-kettle")
        assert lines[0] == "notes-on-a-kettle binds 1 spec section(s)"
        with pytest.raises(LookupError, match="no post matching 'gone'"):
            trail.unit_view("gone")


def test_a_site_needs_no_quarto_file(site):
    Project.from_settings(site, SETTINGS).require("manuscript")


def test_a_site_must_say_where_its_pages_are(site):
    settings = {"manuscript": {"format": "pages", "source": "content"}}
    with pytest.raises(ConfigurationError, match="needs manuscript.pages"):
        Project.from_settings(site, settings).require("manuscript")


def test_a_marks_pattern_must_name_the_slug(site):
    settings = {"manuscript": {**SETTINGS["manuscript"], "marks": "specs/marks.md"}}
    with pytest.raises(ConfigurationError, match="must contain {slug}"):
        Project.from_settings(site, settings).require("manuscript")


def test_an_unknown_format_is_refused(site):
    settings = {"manuscript": {"format": "wiki", "source": "content"}}
    with pytest.raises(ConfigurationError, match="one of quarto, pages"):
        Project.from_settings(site, settings).require("manuscript")


WIKI = """
from pathlib import Path

from quirework.manuscript.formats import Format, Unit


class Wiki(Format):
    \"\"\"Every Markdown file directly under the source is a unit.\"\"\"

    name = "wiki"
    noun = "entry"

    def registry(self, docs):
        return [
            Unit(p.stem, str(p.relative_to(docs.parent)), p.stem)
            for p in self.prose_files(docs)
        ]

    def order_problems(self, docs):
        return []

    def prose_files(self, docs):
        return sorted(docs.glob("*.md"))

    def slug_of(self, path):
        return Path(path).stem

    def marks_path(self, slug, docs):
        return None

    def sidecars(self, docs):
        return []

    def sidecar_slug(self, path):
        return None

    def prose_for_sidecar(self, path, docs):
        return None

    def harvest(self, docs):
        return self.prose_files(docs)

    def default_layers(self, project):
        return ()


def wiki():
    return Wiki()


def not_a_format():
    return object()
"""


def test_a_format_can_be_an_installed_extension(tmp_path, monkeypatch, capsys):
    """A layout Quirework does not ship is one class in the project's own
    package, and the spec layer reads it through the same interface."""
    (tmp_path / "ext").mkdir()
    (tmp_path / "ext" / "kettle_wiki.py").write_text(WIKI)
    monkeypatch.syspath_prepend(str(tmp_path / "ext"))
    root = tmp_path / "wiki"
    (root / "entries").mkdir(parents=True)
    (root / "entries" / "whistle.md").write_text("# Whistle\n\nIt reports.\n")
    (root / "entries" / "steam.md").write_text("# Steam\n\nIt rises.\n")
    project = Project.from_settings(
        root, {"manuscript": {"format": "kettle_wiki:wiki", "source": "entries"}}
    )
    project.require("manuscript")
    with project_scope(project):
        assert units.manuscript_format() == "wiki"
        assert units.noun() == "entry"
        assert units.slugs() == {"whistle", "steam"}
        assert units.main([]) == 0
    assert "whistle" in capsys.readouterr().out


def test_an_extension_that_returns_no_format_is_refused(tmp_path, monkeypatch):
    (tmp_path / "ext").mkdir()
    (tmp_path / "ext" / "kettle_wiki.py").write_text(WIKI)
    monkeypatch.syspath_prepend(str(tmp_path / "ext"))
    (tmp_path / "entries").mkdir()
    settings = {
        "manuscript": {"format": "kettle_wiki:not_a_format", "source": "entries"}
    }
    with pytest.raises(ConfigurationError, match="must return a quirework Format"):
        Project.from_settings(tmp_path, settings).require("manuscript")


def test_since_reads_marks_outside_the_source_from_the_revision(site, capsys):
    """`--since` checks the tree as it was. The marks sit outside the source,
    so the project has to be rebuilt on that tree, not merely relabeled: a
    relabeled project would read today's marks against the revision's pages
    and call a standing advisory new."""
    marks = site / "specs" / "notes-on-a-kettle" / "marks.md"
    marks.write_text(KETTLE_MARKS + "<!-- @guidance as settled 2031-04-02 -->\n")
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
        "A kettle site",
    )
    marks.write_text(marks.read_text() + "<!-- @guidance per the author's call -->\n")
    assert notes.check(since="HEAD") == 0
    err = capsys.readouterr().err
    new = err[err.index("new since HEAD:") : err.index("standing")]
    assert "author's call" in new
    assert "2031-04-02" not in new


def test_an_origin_in_working_material_is_history_not_a_commitment(site, capsys):
    """@genesis names the brief a post grew from; @guidance resting on the
    same directory has put a commitment somewhere the site does not carry."""
    settings = {
        "manuscript": {**SETTINGS["manuscript"], "noncanonical_roots": ["ideas/"]}
    }
    marks = site / "specs" / "notes-on-a-kettle" / "marks.md"
    marks.write_text(
        "<!-- @genesis the brief in ideas/kettle -->\n"
        "<!-- @guidance as ideas/kettle says -->\n" + KETTLE_MARKS
    )
    with project_scope(Project.from_settings(site, settings)):
        assert notes.check() == 0
    err = capsys.readouterr().err
    assert "@guidance" in err[err.index("reference ideas/") :]
    assert "@genesis" not in err
