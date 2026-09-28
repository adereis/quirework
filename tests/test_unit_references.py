"""A pointer to a unit reads the same in a book and on a site.

The metadata layer names a chapter or a post with `@unit(slug)`, and a
companion entry's **Anchors:** run may also name one by its file, written as
the format writes it. Neither form may assume a Quarto book. Every fixture
here is fiction.
"""

import json

import pytest

from quirework.manuscript import notes, units
from quirework.project import Project, project_scope
from quirework.provenance import coldread
from quirework.provenance import scope as SCOPE

SPEC = """# What the kettle blog holds

## A kettle signals without thinking {#kettle-signal}

A whistle reports a state.
"""

POST = """+++
title = 'Notes on a Kettle'
date = 2031-04-02
+++

The kettle whistles when the water boils.

## The whistle

A whistle reports a state.
"""

MARKS = """## The whistle

<!-- @bind(kettle-signal) -->
"""


def _site(root, entry):
    post = root / "content" / "posts" / "notes-on-a-kettle"
    post.mkdir(parents=True)
    (post / "index.md").write_text(POST)
    (root / "specs" / "notes-on-a-kettle").mkdir(parents=True)
    (root / "specs" / "notes-on-a-kettle" / "marks.md").write_text(MARKS)
    (root / "specs" / "_site.md").write_text(SPEC)
    (root / "notes").mkdir()
    (root / "notes" / "a-kettle-at-sea.md").write_text(entry)
    # An index and a template are part of a collection, not entries of it.
    (root / "notes" / "README.md").write_text("# Notes\n")
    (root / "notes" / "_template.md").write_text("**Anchors:** `<unit>`\n")
    return Project.from_settings(
        root,
        {
            "manuscript": {
                "format": "pages",
                "source": "content",
                "pages": "posts/*/index.md",
                "marks": "specs/{slug}/marks.md",
                "unit_name": "post",
                "spec": "specs/_site.md",
                "companion_dirs": ["notes"],
            }
        },
    )


def _check(project, capsys):
    with project_scope(project):
        rc = notes.check()
    return rc, capsys.readouterr().err


def test_a_site_s_companion_names_a_post_by_its_page(tmp_path, capsys):
    entry = (
        "# A kettle at sea\n\n**Anchors:** `posts/notes-on-a-kettle/index.md` "
        "`@bind(kettle-signal)`\n\nA ship's kettle still whistles.\n"
    )
    rc, err = _check(_site(tmp_path, entry), capsys)
    assert rc == 0, err


def test_a_companion_naming_a_missing_post_says_post(tmp_path, capsys):
    entry = (
        "# A kettle at sea\n\n**Anchors:** `posts/a-kettle-overboard/index.md` "
        "`@bind(kettle-signal)`\n"
    )
    rc, err = _check(_site(tmp_path, entry), capsys)
    assert rc == 1
    assert "which is no post" in err
    assert "chapter" not in err


def test_an_anchor_on_a_post_that_does_not_bind_the_claim_is_named(tmp_path, capsys):
    entry = "**Anchors:** `@unit(notes-on-a-kettle)` `@bind(kettle-signal)`\n"
    project = _site(tmp_path, entry)
    (tmp_path / "specs" / "notes-on-a-kettle" / "marks.md").write_text(
        "## The whistle\n"
    )
    rc, err = _check(project, capsys)
    assert rc == 1
    assert "notes-on-a-kettle does not bind" in err
    assert "Bound by: no post" in err


def test_a_unit_reference_is_never_a_mark_kind(tmp_path, capsys):
    project = _site(tmp_path, "**Anchors:** `@bind(kettle-signal)`\n")
    (tmp_path / "specs" / "notes-on-a-kettle" / "marks.md").write_text(
        MARKS + "\n<!-- @unit(notes-on-a-kettle) the same kettle -->\n"
    )
    rc, err = _check(project, capsys)
    assert rc == 1
    assert "@unit is a *reference* to a post, not a mark kind" in err


def test_an_unresolved_unit_reference_is_reported(tmp_path, capsys):
    project = _site(tmp_path, "**Anchors:** `@bind(kettle-signal)`\n")
    (tmp_path / "specs" / "notes-on-a-kettle" / "marks.md").write_text(
        MARKS + "\n<!-- @guidance read after @unit(a-kettle-overboard) -->\n"
    )
    with project_scope(project):
        hits = units.scan_layers()
    assert [h.token for h in hits] == ["@unit(a-kettle-overboard)"]
    assert hits[0].note.startswith("no such post")


def test_the_kinds_are_described_for_any_work():
    assert "chapter" not in json.dumps(notes.KIND_DESCRIPTIONS)
    assert "book" not in json.dumps(notes.KIND_DESCRIPTIONS)


def test_a_report_cached_under_the_old_scope_label_still_hits(tmp_path):
    """The work's corpus scope was once recorded as "book". A cache that
    missed on the renamed label would buy every report again."""
    root = tmp_path / "work"
    reports = root / "private" / "coldread" / "reports"
    reports.mkdir(parents=True)
    (reports / "abc-book.md").write_text("A report.\n")
    project = Project.from_settings(
        root,
        {"provenance": {"enabled": True, "root": "private"}},
    )
    relpath = "cc-sessions/0b8e2a6c-5a4b-4c7e-9d11-2f3a4b5c6d7e.jsonl"
    with project_scope(project):
        entry = {
            "sha256": "abc",
            "scope": "book",
            "prompt_version": coldread.PROMPT_VERSIONS[SCOPE.WORK],
            "report": "reports/abc-book.md",
            "filter_version": coldread.filter_version_for(relpath),
        }
        manifest = {"sources": {relpath: entry}}
        assert coldread.is_hit(manifest, relpath, "abc")
    assert SCOPE.normalize("book") == SCOPE.WORK == "work"


@pytest.mark.parametrize("value", ["work", "making"])
def test_current_scope_labels_are_their_own(value):
    assert SCOPE.normalize(value) == value


def test_a_workshop_orders_a_site_s_posts_by_where_they_came_from(tmp_path):
    """Every post of a site is an `index.md`, so the staged order is matched by
    the path a file was staged from, never by its name."""
    from quirework.sops import workshop

    project = _site(tmp_path, "**Anchors:** `@bind(kettle-signal)`\n")
    posts = tmp_path / "content" / "posts"
    for day, slug in (("2031-05-01", "cooling-down"), ("2031-06-01", "descaling")):
        (posts / slug).mkdir()
        (posts / slug / "index.md").write_text(
            f"+++\ntitle = '{slug.replace('-', ' ').title()}'\ndate = {day}\n+++\n"
        )
    sources = [
        "content/posts/descaling/index.md",
        "content/posts/notes-on-a-kettle/index.md",
        "content/posts/cooling-down/index.md",
    ]
    with project_scope(project):
        listing = workshop._reading_order(tmp_path, sources)
    assert listing.splitlines()[2:5] == [
        "     Notes on a Kettle",
        "     Cooling Down",
        "     Descaling",
    ]
    # Pages carry no printed number, so nothing is said about numbering.
    assert "number" not in listing
