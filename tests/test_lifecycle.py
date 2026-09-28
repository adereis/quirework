"""A site's units in collections, and where each one stands in its life.

A site is posts and more: an About page is written, specified and revised
like a post, and it is ordered by nothing a post is ordered by. A post has a
life, too: a brief, a spec, a draft, a publication, and revisions after it.
Every fixture here is fiction.
"""

import subprocess

import pytest

from quirework.manuscript import footprint, lifecycle, units
from quirework.project import ConfigurationError, Project, project_scope

BASE = {
    "format": "pages",
    "source": "content",
    "marks": "specs/{slug}/marks.md",
    "spec": "specs/_site.md",
    "unit_spec": "specs/{slug}/spec.md",
    "unit_name": "post",
}
COLLECTIONS = {
    "posts": {"pages": "posts/*/index.md"},
    "pages": {"pages": "*.md", "order": "title"},
}


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _post(root, slug, date, draft=False, extra=""):
    _write(
        root / "content" / "posts" / slug / "index.md",
        f"+++\ntitle = '{slug.replace('-', ' ').title()}'\ndate = '{date}'\n"
        f"draft = {'true' if draft else 'false'}\n{extra}+++\n\nA kettle.\n",
    )


@pytest.fixture
def site(tmp_path, monkeypatch):
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "site"
    _post(root, "the-kettle", "2031-04-02")
    _post(root, "cooling-down", "2031-05-01", draft=True)
    _write(root / "content" / "about.md", "+++\ntitle = 'About'\n+++\n\nA keeper.\n")
    _write(root / "content" / "colophon.md", "+++\ntitle = 'Colophon'\n+++\n\nType.\n")
    _write(root / "content" / "_index.md", "+++\ntitle = 'Home'\n+++\n")
    _write(root / "specs" / "_site.md", "# Site\n")
    _write(
        root / "specs" / "the-kettle" / "spec.md",
        "# The Kettle\n\n## A whistle is a report {#thesis}\n\nIt reports.\n",
    )
    return root


def _project(root, **manuscript):
    return Project.from_settings(root, {"manuscript": {**BASE, **manuscript}})


def test_a_site_s_units_come_collection_by_collection(site):
    with project_scope(_project(site, collections=COLLECTIONS)):
        listed = [(u.collection, u.slug) for u in units.registry()]
    assert listed == [
        ("posts", "the-kettle"),
        ("posts", "cooling-down"),
        ("pages", "about"),
        ("pages", "colophon"),
    ]


def test_the_listing_groups_the_collections(site, capsys):
    with project_scope(_project(site, collections=COLLECTIONS)):
        assert units.main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "posts:"
    assert out[3] == "pages:"
    assert "about" in out[4]


def test_a_page_two_globs_match_is_the_first_one_s(site):
    both = {
        "pages": {"pages": "*.md", "order": "title"},
        "everything": {"pages": "**/*.md"},
    }
    with project_scope(_project(site, collections=both)):
        listed = [(u.collection, u.slug) for u in units.registry()]
    assert ("pages", "about") in listed
    assert ("everything", "about") not in listed
    assert ("everything", "the-kettle") in listed


def test_weight_orders_a_collection_that_asks_for_it(site):
    _write(site / "content" / "about.md", "+++\ntitle = 'About'\nweight = 2\n+++\n")
    _write(
        site / "content" / "colophon.md", "+++\ntitle = 'Colophon'\nweight = 1\n+++\n"
    )
    ordered = {**COLLECTIONS, "pages": {"pages": "*.md", "order": "weight"}}
    with project_scope(_project(site, collections=ordered)):
        pages = [u.slug for u in units.registry() if u.collection == "pages"]
    assert pages == ["colophon", "about"]


def test_the_about_page_is_the_work_once_it_is_a_unit(site):
    fp = footprint.footprint(_project(site, collections=COLLECTIONS))
    assert fp.covers("content/about.md")
    assert fp.covers("content/posts/the-kettle/index.md")


@pytest.mark.parametrize(
    "settings, message",
    [
        ({"pages": "*.md", "collections": COLLECTIONS}, "keep one"),
        ({"collections": {"posts": {"pages": "*.md", "order": "mood"}}}, "order"),
        ({"collections": {"posts": {"pages": "*.md", "noun": "x"}}}, "unknown keys"),
        ({"collections": {"posts": {"order": "date"}}}, "needs pages"),
    ],
)
def test_a_collection_is_declared_completely(site, settings, message):
    with pytest.raises(ConfigurationError, match=message):
        _project(site, **settings).require("manuscript")


# --------------------------------------------------------------------------- #
# the stages before a page, and what a published page owes a reader
# --------------------------------------------------------------------------- #
def test_planned_units_and_open_ideas_are_listed(site, capsys):
    _write(site / "specs" / "a-lid" / "spec.md", "# A lid\n\n## Lids {#thesis}\n\nx\n")
    _write(site / "ideas" / "the-tide-clock" / "brief.md", "# A tide clock\n")
    _write(site / "ideas" / "kettle-notes" / "brief.md", "# Kettle notes\n")
    _write(site / "ideas" / "cooling-down" / "brief.md", "# Cooling\n")
    _write(site / "ideas" / "README.md", "# Ideas\n")
    # The kettle post grew from a brief of another name, and says so.
    _write(
        site / "specs" / "the-kettle" / "marks.md",
        "<!-- @genesis ideas/kettle-notes/brief.md -->\n",
    )
    project = _project(site, pages="posts/*/index.md", ideas="ideas/*/brief.md")
    with project_scope(project):
        assert units.main([]) == 0
    out = capsys.readouterr().out
    assert "planned (a spec, no post yet):\n  a-lid" in out
    assert "the-tide-clock" in out
    assert "kettle-notes" not in out  # taken up by a post's @genesis
    assert "ideas/cooling-down" not in out  # taken up by a post of its name


def test_an_idea_named_by_its_directory_is_taken_up_by_the_directory(site):
    for name in ("steam", "steam-two", "whistle", "lid"):
        _write(site / "ideas" / name / "brief.md", f"# {name}\n")
    # A @genesis names the idea as the directory it is, in running prose.
    _write(
        site / "specs" / "the-kettle" / "marks.md",
        "<!-- @genesis the brief in ideas/steam, as its angle A -->\n"
        "<!-- @source the notes in ideas/whistle. -->\n"
        "<!-- @guidance compare ideas/lid.md, a file of another name -->\n",
    )
    project = _project(site, pages="posts/*/index.md", ideas="ideas/*/brief.md")
    with project_scope(project):
        assert [i.slug for i in lifecycle.open_ideas()] == ["lid", "steam-two"]


def test_an_idea_that_is_one_file_is_taken_up_by_that_file_alone(site):
    for name in ("steam", "whistle"):
        _write(site / "ideas" / f"{name}.md", f"# {name}\n")
    # Naming one idea in the shared directory does not take up the other.
    _write(
        site / "specs" / "the-kettle" / "marks.md",
        "<!-- @genesis ideas/steam.md -->\n",
    )
    project = _project(site, pages="posts/*/index.md", ideas="ideas/*.md")
    with project_scope(project):
        assert [i.slug for i in lifecycle.open_ideas()] == ["whistle"]


def test_an_open_todo_on_a_published_post_is_an_advisory(site, capsys):
    _write(
        site / "specs" / "the-kettle" / "marks.md",
        "<!-- @todo check the whistle's pitch -->\n",
    )
    _write(
        site / "specs" / "cooling-down" / "marks.md",
        "<!-- @todo write the ending -->\n",
    )
    with project_scope(_project(site, pages="posts/*/index.md")):
        assert units.main(["--check"]) == 0
    err = capsys.readouterr().err
    assert "1 open @todo on published posts" in err
    assert "check the whistle's pitch" in err
    assert "write the ending" not in err  # a draft is allowed its to-dos


def git(root, *args):
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=A. Keeper",
            "-c",
            "user.email=keeper@example.invalid",
            *args,
        ],
        check=True,
        capture_output=True,
    )


def _stale(root, **manuscript):
    with project_scope(_project(root, pages="posts/*/index.md", **manuscript)):
        return lifecycle.unrevised()


def test_a_spec_changed_after_its_post_was_dated_is_named(site):
    git(site, "init", "-q")
    git(site, "add", ".")
    git(site, "commit", "-qm", "Publish the kettle")
    assert _stale(site) == []
    spec = site / "specs" / "the-kettle" / "spec.md"
    spec.write_text(spec.read_text() + "\n## A lid matters {#lid}\n\nIt does.\n")
    # Uncommitted, and the post not redated: named already.
    [line] = _stale(site)
    assert "content/posts/the-kettle/index.md: specs/the-kettle/spec.md changed" in line
    assert "set lastmod" in line
    git(site, "commit", "-qam", "Add a claim about lids")
    assert len(_stale(site)) == 1
    _post(site, "the-kettle", "2031-04-02", extra="lastmod = '2031-06-01'\n")
    assert _stale(site) == []  # redated, not yet committed
    git(site, "commit", "-qam", "Revise the kettle post")
    assert _stale(site) == []


def test_the_revision_field_is_the_project_s_to_name(site):
    git(site, "init", "-q")
    git(site, "add", ".")
    git(site, "commit", "-qm", "Publish the kettle")
    spec = site / "specs" / "the-kettle" / "spec.md"
    spec.write_text(spec.read_text() + "\n## A lid {#lid}\n\nx\n")
    git(site, "commit", "-qam", "Add a claim")
    _post(site, "the-kettle", "2031-04-02", extra="last_modified_at = '2031-06-01'\n")
    git(site, "commit", "-qam", "Revise")
    assert len(_stale(site)) == 1
    assert _stale(site, revision_field="last_modified_at") == []
    with project_scope(
        _project(site, pages="posts/*/index.md", revision_field="last_modified_at")
    ):
        [kettle] = [u for u in units.registry() if u.slug == "the-kettle"]
    assert kettle.lastmod == "2031-06-01"


def test_without_history_the_revision_check_says_it_did_not_run(site, capsys):
    assert _stale(site) is None
    with project_scope(_project(site, pages="posts/*/index.md")):
        assert units.main(["--check"]) == 0
    assert "not a git repository" in capsys.readouterr().err


def test_a_book_s_chapters_have_no_publication_state(tmp_path):
    from quirework.manuscript.formats import Unit

    assert Unit("opening", "opening.qmd", "Opening").draft is None


def test_a_spec_written_for_a_published_post_describes_it(site):
    """Adopting a spec for a post already out is not revising the post. The
    first change after the spec exists is."""
    spec = site / "specs" / "the-kettle" / "spec.md"
    text = spec.read_text()
    spec.unlink()
    git(site, "init", "-q")
    git(site, "add", ".")
    git(site, "commit", "-qm", "Publish the kettle")
    spec.write_text(text)
    assert _stale(site) == []  # being written
    git(site, "add", ".")
    git(site, "commit", "-qm", "Specify the published kettle post")
    assert _stale(site) == []  # created, not revised
    spec.write_text(text + "\n## A lid {#lid}\n\nx\n")
    git(site, "commit", "-qam", "Add a claim")
    assert len(_stale(site)) == 1


def test_renaming_an_anchor_or_rewrapping_revises_nothing(site):
    """A reader was told the claims, not their anchors or their line breaks."""
    git(site, "init", "-q")
    git(site, "add", ".")
    git(site, "commit", "-qm", "Publish the kettle")
    spec = site / "specs" / "the-kettle" / "spec.md"
    spec.write_text(
        "<!-- a banner the keeper added -->\n# The Kettle\n\n"
        "## A whistle is a report {#the-whistle}\n\nIt\nreports.\n"
    )
    git(site, "commit", "-qam", "Rename an anchor and rewrap")
    assert _stale(site) == []
