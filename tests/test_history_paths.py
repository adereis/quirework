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
    (root / "specs" / "_site.md").write_text("# Site\n\n## The lamp {#flash}\n\nIt.\n")
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
