"""Where the work lives, asked once and answered the same for every tool.

A book keeps its spec and marks inside its source; a site keeps them outside
the tree its generator renders. The tools that ask "is this the work?" used
to answer from the source directory alone, which was right for the first and
wrong for the second: a site's spec edits counted as tooling and escaped
review. Every fixture here is fiction.
"""

import subprocess

import pytest

from quirework.manuscript.footprint import footprint
from quirework.project import Project, project_scope
from quirework.provenance import coldread, metrics
from quirework.sops import review
from quirework.text import jargon

SITE = {
    "manuscript": {
        "format": "pages",
        "source": "content",
        "pages": "posts/*/index.md",
        "marks": "specs/{slug}/marks.md",
        "spec": "specs/_site.md",
        "unit_spec": "specs/{slug}/spec.md",
        "noncanonical_roots": ["ideas/"],
    }
}

BOOK = {
    "manuscript": {
        "source": "manuscript",
        "chapters": "manuscript/sections",
        "quarto": "manuscript/_quarto.yml",
        "spec": "manuscript/sections/_spec.md",
        "companion_dirs": ["companion"],
        "noncanonical_roots": ["scratchpad"],
    }
}


def _site(root):
    post = root / "content" / "posts" / "the-kettle"
    post.mkdir(parents=True)
    (post / "index.md").write_text(
        "+++\ntitle = 'The Kettle'\ndate = 2031-04-02\n+++\n\n"
        "The kettle whistles when the water boils.\n"
    )
    (root / "specs" / "the-kettle").mkdir(parents=True)
    (root / "specs" / "_site.md").write_text("# Site\n")
    return root


def test_a_site_s_spec_and_marks_are_the_work_though_outside_the_source(tmp_path):
    fp = footprint(Project.from_settings(tmp_path, SITE))
    for path in (
        "content/posts/the-kettle/index.md",
        "content/posts/the-kettle/whistle.svg",  # a bundle's figure is the post's
        "specs/the-kettle/marks.md",
        "specs/the-kettle/spec.md",
        "specs/_site.md",
    ):
        assert fp.covers(path), path
    for path in ("layouts/_default/single.html", "hugo.toml", "content/about.md"):
        assert not fp.covers(path), path


def test_working_material_is_writing_but_not_authority(tmp_path):
    fp = footprint(Project.from_settings(tmp_path, SITE))
    assert not fp.covers("ideas/the-kettle/brief.md")
    assert fp.covers("ideas/the-kettle/brief.md", working=True)


def test_a_book_s_footprint_is_its_source_and_companions(tmp_path):
    fp = footprint(Project.from_settings(tmp_path, BOOK))
    assert fp.covers("manuscript/sections/opening.qmd")
    assert fp.covers("manuscript/sections/_marks/opening.md")
    assert fp.covers("companion/a-kettle-exercise.md")
    assert not fp.covers("tools/build.sh")
    assert fp.covers("scratchpad/draft.md", working=True)


def test_a_project_without_a_manuscript_has_no_footprint(tmp_path):
    fp = footprint(Project.from_settings(tmp_path, {}))
    assert fp.patterns(working=True) == ()
    assert not fp.covers("anything.md")


def test_authorship_counts_a_site_s_spec_edit_as_writing(tmp_path, monkeypatch):
    """The catalog and triage call a session's edits "doc" or "meta". A post's
    spec is the work, wherever the site keeps it."""
    monkeypatch.setattr(coldread, "_repo_relative", lambda fp: fp)
    with project_scope(Project.from_settings(tmp_path, SITE)):
        assert coldread._classify_edit("specs/the-kettle/spec.md") == "doc"
        assert coldread._classify_edit("ideas/the-kettle/brief.md") == "doc"
        assert coldread._classify_edit("scripts/publish.sh") == "meta"


def test_a_declared_document_policy_replaces_the_footprint(tmp_path, monkeypatch):
    (tmp_path / "coldread.toml").write_text('doc_dirs = ["drafts/"]\n')
    settings = {
        **SITE,
        "provenance": {"coldread_policy": "coldread.toml"},
    }
    monkeypatch.setattr(coldread, "_repo_relative", lambda fp: fp)
    with project_scope(Project.from_settings(tmp_path, settings)):
        assert coldread._classify_edit("drafts/kettle.md") == "doc"
        assert coldread._classify_edit("specs/the-kettle/spec.md") == "meta"


def test_a_reviewer_must_see_a_site_s_spec_edit(tmp_path):
    with project_scope(Project.from_settings(tmp_path, SITE)):
        reviewable = review._options().REVIEWABLE
        assert reviewable("specs/the-kettle/spec.md")
        assert reviewable("content/posts/the-kettle/index.md")
        # Working material has no authority, so no reviewer is owed it.
        assert not reviewable("ideas/the-kettle/brief.md")


def test_declared_reviewable_prefixes_still_win(tmp_path):
    (tmp_path / "review.toml").write_text('reviewable = ["essays/"]\n')
    settings = {**SITE, "sops": {"review_policy": "review.toml"}}
    with project_scope(Project.from_settings(tmp_path, settings)):
        reviewable = review._options().REVIEWABLE
        assert reviewable("essays/kettle.md")
        assert not reviewable("specs/the-kettle/spec.md")


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_metrics_measure_a_site_without_a_chapters_directory(tmp_path):
    """`manuscript.chapters` is the Quarto layout's key. A site's history and
    word count come from its pages, through the same footprint."""
    root = _site(tmp_path / "site")
    git(root, "init", "-q")
    git(root, "-c", "user.name=Keeper", "-c", "user.email=k@example.org", "add", ".")
    git(
        root,
        "-c",
        "user.name=Keeper",
        "-c",
        "user.email=k@example.org",
        "commit",
        "-qm",
        "The kettle",
    )
    (root / "hugo.toml").write_text("title = 'Kettles'\n")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Keeper",
        "-c",
        "user.email=k@example.org",
        "commit",
        "-qm",
        "Configure the site",
    )
    with project_scope(Project.from_settings(root, SITE)):
        assert metrics.git_metrics()["commits_sot"] == 1
        assert metrics.output_metrics()["essay_words"] == 7


def test_the_terminology_check_reads_the_prose_by_default(tmp_path):
    root = _site(tmp_path / "site")
    (root / "terms.toml").write_text('[retired]\n"whistles" = "sings"\n')
    settings = {**SITE, "text": {"terminology": "terms.toml"}}
    with project_scope(Project.from_settings(root, settings)):
        hits = jargon.retired()
    assert [h.term for h in hits] == ["whistles"]


@pytest.mark.parametrize(
    "path, pattern, expected",
    [
        ("content/posts/a/index.md", "content/**/*.md", True),
        ("content/index.md", "content/**/*.md", True),
        ("content/posts/a/index.md", "content/*.md", False),
        ("specs/a/b/marks.md", "specs/**", True),
    ],
)
def test_one_glob_dialect(path, pattern, expected):
    from quirework.core import globs

    assert globs.matches(path, pattern) is expected
