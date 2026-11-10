"""A configured path, a file and the settings, as an earlier revision kept
them, for every tool that reads the past.

The project here is fiction: a lighthouse book whose source directory moves
from drafts/ to book/ in its second commit, and whose chapters are a short
log of the light.
"""

import subprocess

import pytest

from quirework.manuscript import backfill, history
from quirework.project import Project, project_scope
from quirework.publish import kindle
from quirework.text import tells

LAMP = "# The lamp {#sec-lamp}\n\nIt shows one white flash every ten seconds.\n"


def git(root, *args):
    return subprocess.run(
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
        text=True,
    ).stdout.strip()


def _toml(source):
    return (
        "schema = 2\n\n[manuscript]\n"
        f'source = "{source}"\nchapters = "{source}/chapters"\n'
    )


def _settings(source):
    return {"manuscript": {"source": source, "chapters": f"{source}/chapters"}}


@pytest.fixture
def book(tmp_path):
    """drafts/ in the first commit, book/ in the second."""
    root = tmp_path / "book"
    (root / "drafts" / "chapters").mkdir(parents=True)
    (root / "drafts" / "chapters" / "lamp.qmd").write_text(LAMP)
    (root / "quirework.toml").write_text(_toml("drafts"))
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Write the lamp")
    first = git(root, "rev-parse", "HEAD")
    git(root, "mv", "drafts", "book")
    (root / "quirework.toml").write_text(_toml("book"))
    (root / "book" / "chapters" / "horn.qmd").write_text("# The horn\n\nTwo blasts.\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Move the book")
    with project_scope(Project.from_settings(root, _settings("book"))):
        yield root, first


def test_a_path_setting_is_read_from_the_revision_s_configuration(book):
    _, first = book
    assert history.path_at("manuscript.chapters", first) == "drafts/chapters"
    assert history.path_at("manuscript.chapters", "HEAD") == "book/chapters"
    with pytest.raises(history.Unplaced, match="sets no manuscript.quarto"):
        history.path_at("manuscript.quarto", first)


def test_a_file_is_found_where_the_revision_kept_it(book):
    _, first = book
    assert (
        history.file_at("book/chapters/lamp.qmd", first) == "drafts/chapters/lamp.qmd"
    )
    # A file made after the revision keeps the path the configuration gives
    # it, where the revision has nothing.
    assert (
        history.file_at("book/chapters/horn.qmd", first) == "drafts/chapters/horn.qmd"
    )
    assert history.file_at("README.md", first) == "README.md"


def test_settings_are_the_revision_s_own(book):
    _, first = book
    values, how = history.settings_for(first)
    assert (values["manuscript"]["source"], how) == ("drafts", "configuration")


def test_without_a_configuration_the_map_places_the_paths(tmp_path):
    """A revision without a configuration is read through today's settings,
    each path placed by the map; a path it cannot place stops the read."""
    root = tmp_path / "book"
    (root / "drafts" / "chapters").mkdir(parents=True)
    (root / "drafts" / "chapters" / "lamp.qmd").write_text(LAMP)
    (root / "policy.toml").write_text("")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Write the lamp")
    first = git(root, "rev-parse", "HEAD")
    git(root, "mv", "drafts", "book")
    git(root, "commit", "-qm", "Move the book")
    settings = _settings("book")
    settings["manuscript"]["chapter_policy"] = "policy.toml"
    with (
        project_scope(Project.from_settings(root, settings)),
        pytest.raises(history.Unplaced, match="the path book is not at"),
    ):
        history.settings_for(first)
    (root / "policy.toml").write_text('[moved_paths]\n"drafts/" = "book/"\n')
    with project_scope(Project.from_settings(root, settings)):
        values, how = history.settings_for(first)
        assert (values["manuscript"]["chapters"], how) == (
            "drafts/chapters",
            "moved paths",
        )
        assert history.path_at("manuscript.source", first) == "drafts"


def test_kindle_reads_the_copy_s_chapters_where_its_commit_kept_them(book):
    root, first = book
    files = kindle._files_at(first, root / "book" / "chapters")
    assert [name for name, _ in files] == ["drafts/chapters/lamp.qmd"]


def test_the_backfill_reads_the_book_of_a_round_s_revision(book):
    root, first = book
    held = backfill._book_at(str(root), first)
    assert held is not None and "one white flash" in held


def test_a_standing_tell_in_a_moved_file_is_not_new(book):
    _, first = book
    assert "one white flash" in tells._at(first, "book/chapters/lamp.qmd")
    assert tells._at(first, "book/chapters/horn.qmd") == ""


def test_notes_since_checks_a_revision_from_before_a_move(tmp_path, capsys):
    """The tree at the revision is checked with the settings it was committed
    with. Here the marks moved from specs/ to notes/: read through today's
    pattern, the revision would hold no marks, and its standing advisory
    would be reported as new."""
    from quirework.manuscript import notes

    def site(meta):
        return {
            "format": "pages",
            "source": "content",
            "pages": "posts/*/index.md",
            "marks": f"{meta}/{{slug}}/marks.md",
            "unit_name": "post",
            "spec": f"{meta}/_site.md",
        }

    def toml(meta):
        lines = "".join(f'{k} = "{v}"\n' for k, v in site(meta).items())
        return "schema = 2\n\n[manuscript]\n" + lines

    root = tmp_path / "site"
    post = root / "content" / "posts" / "lamp" / "index.md"
    post.parent.mkdir(parents=True)
    post.write_text(
        "+++\ntitle = 'lamp'\ndate = 2031-04-02\n+++\n\n## The light\n\nIt.\n"
    )
    (root / "specs" / "lamp").mkdir(parents=True)
    (root / "specs" / "lamp" / "marks.md").write_text(
        "## The light\n\n<!-- @guidance as settled 2031-04-02 -->\n"
    )
    (root / "specs" / "_site.md").write_text(
        "# Site\n\n## The lamp {#flash}\n\nIt must NEVER go dark.\n"
    )
    (root / "quirework.toml").write_text(toml("specs"))
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Write the lamp")
    first = git(root, "rev-parse", "HEAD")
    git(root, "mv", "specs", "notes")
    (root / "quirework.toml").write_text(toml("notes"))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Move the marks")
    marks = root / "notes" / "lamp" / "marks.md"
    marks.write_text(marks.read_text() + "<!-- @guidance per the author's call -->\n")
    with project_scope(Project.from_settings(root, {"manuscript": site("notes")})):
        assert notes.check(since=first) == 0
    err = capsys.readouterr().err
    new = err[err.index("new since") : err.index("standing")]
    assert "author's call" in new
    assert "2031-04-02" not in new
    # A section's advisory names it `file#slug`, and is standing too.
    assert "NEVER" not in new


def test_a_file_nothing_places_is_read_as_new_and_said(tmp_path, capsys):
    """Without a configuration at the revision and without a map entry, one
    file is read as one the revision did not have yet, and a note says so,
    since an unrecorded move would read the same way."""
    root = tmp_path / "book"
    (root / "drafts").mkdir(parents=True)
    (root / "drafts" / "lamp.qmd").write_text(LAMP)
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Write the lamp")
    first = git(root, "rev-parse", "HEAD")
    with project_scope(Project.from_settings(root, _settings("drafts"))):
        assert history.file_at("drafts/horn.qmd", first) == "drafts/horn.qmd"
        history.file_at("drafts/horn.qmd", first)
    err = capsys.readouterr().err
    assert err.count("note: drafts/horn.qmd is read as a file") == 1
    assert "holds no quirework.toml" in err


def test_a_parent_that_cannot_be_placed_is_an_error_not_an_empty_spec(tmp_path, capsys):
    """Only a root commit has no parent. A parent whose spec cannot be placed,
    or a ref that names nothing, stops the comparison."""
    from quirework.manuscript import specdiff

    root = tmp_path / "site"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "claims.md").write_text(
        "# C\n\n" + "## The lamp {#flash}\n\nIt.\n"
    )
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Write the claims")
    git(root, "mv", "notes", "spec")
    (root / "quirework.toml").write_text(
        'schema = 2\n\n[manuscript]\nsource = "spec"\nspec = "spec/claims.md"\n'
    )
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Configure")
    second = git(root, "rev-parse", "HEAD")
    settings = {"manuscript": {"source": "spec", "spec": "spec/claims.md"}}
    with project_scope(Project.from_settings(root, settings)):
        assert specdiff.main([f"{second}^..HEAD"]) == 2
        assert specdiff.main(["no-such-ref^..HEAD"]) == 2
    err = capsys.readouterr().err
    assert "no moved path places it" in err and "names no commit" in err


def test_today_s_name_follows_a_rename_and_then_a_directory_move(tmp_path):
    root = tmp_path / "book"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "claims.md").write_text(LAMP)
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Write the claims")
    first = git(root, "rev-parse", "HEAD")
    git(root, "mv", "notes/claims.md", "notes/lamp.md")
    git(root, "commit", "-qm", "Rename the claims")
    git(root, "mv", "notes", "spec")
    (root / "policy.toml").write_text(
        '[moved_paths]\n"notes/claims.md" = "notes/lamp.md"\n"notes/" = "spec/"\n'
    )
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Move the notes")
    settings = {"manuscript": {"source": "spec", "chapter_policy": "policy.toml"}}
    with project_scope(Project.from_settings(root, settings)):
        assert history.file_at("spec/lamp.md", first) == "notes/claims.md"
        assert history.path_today("notes/claims.md", first) == "spec/lamp.md"


def test_by_commit_finds_an_edit_made_at_a_home_between_the_ends(tmp_path):
    """The spec lived at a third path between the two ends, and no map row
    names it: the configuration of the commit that moved it there does."""
    from quirework.manuscript import specdiff

    def toml(spec):
        return f'schema = 2\n\n[manuscript]\nsource = "."\nspec = "{spec}"\n'

    root = tmp_path / "site"
    root.mkdir()
    git(root, "init", "-q")
    lamp = "# C\n\n## The lamp {#flash}\n\nIt flashes.\n"
    for name, text, spec, message in (
        ("a.md", lamp, "a.md", "Write the claims"),
        ("b.md", lamp, "b.md", "Move them to b"),
        ("b.md", lamp.replace("flashes", "flashes white"), "b.md", "Sharpen the lamp"),
        ("c.md", lamp.replace("flashes", "flashes white"), "c.md", "Move them to c"),
    ):
        for old in root.glob("*.md"):
            if old.name != name:
                old.unlink()
        (root / name).write_text(text)
        (root / "quirework.toml").write_text(toml(spec))
        git(root, "add", "-A")
        git(root, "commit", "-qm", message)
        if message == "Write the claims":
            first = git(root, "rev-parse", "HEAD")
    settings = {"manuscript": {"source": ".", "spec": "c.md"}}
    with project_scope(Project.from_settings(root, settings)):
        found = specdiff.by_commit(f"{first}..HEAD")
        # A root commit has no parent, so all of it is new.
        root_diff = specdiff.diff(f"{first}^..{first}")
    assert [(c.state, c.slug) for c in root_diff] == [("new", "flash")]
    assert [(c.subject, [x.slug for x in c.changes]) for c in found] == [
        ("Sharpen the lamp", ["flash"])
    ]


def test_a_file_split_by_its_configuration_names_the_section_directory(tmp_path):
    """Where a revision's own setting named one spec file and today's names
    a manifest, the old file's name today is the manifest's directory, so a
    section's advisory finds its section file."""
    root = tmp_path / "site"
    (root / "spec").mkdir(parents=True)
    (root / "spec" / "claims.md").write_text("# C\n\n## The lamp {#flash}\n\nIt.\n")
    (root / "quirework.toml").write_text(
        'schema = 2\n\n[manuscript]\nsource = "spec"\nspec = "spec/claims.md"\n'
    )
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Write the claims")
    first = git(root, "rev-parse", "HEAD")
    git(root, "rm", "-q", "spec/claims.md")
    (root / "spec").mkdir(exist_ok=True)
    (root / "spec" / "flash.md").write_text("## The lamp {#flash}\n\nIt.\n")
    (root / "spec" / "manifest.toml").write_text('sections = ["flash"]\n')
    (root / "quirework.toml").write_text(
        'schema = 2\n\n[manuscript]\nsource = "spec"\nspec = "spec/manifest.toml"\n'
    )
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Split the claims")
    settings = {"manuscript": {"source": "spec", "spec": "spec/manifest.toml"}}
    with project_scope(Project.from_settings(root, settings)):
        assert history.path_today("spec/claims.md", first) == "spec/"
