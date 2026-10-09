"""Renaming a spec section everywhere its slug is written.

A slug sits in the spec's heading and links, in every sidecar that binds the
section, in companion citations, in the trail's heading and in the backfill
ledger. Each case renames one and reads every one of those places back, then
asks the gates whether the project still holds together. Every fixture here
is fiction.
"""

import json

import pytest

from quirework.manuscript import notes, rename, trail
from quirework.project import Project, project_scope

SPEC = """# What the notebook holds

## A kettle signals without thinking {#kettle-signal}

A whistle reports a state.

## Cooling is not the reverse of boiling {#cooling-asymmetry}

Unlike [the whistle](#kettle-signal), cooling makes no sound.
"""

TRAIL = """## A kettle signals without thinking {#kettle-signal}

## Cooling is not the reverse of boiling {#cooling-asymmetry}
"""

SESSION = "0badc0de-0000-4000-8000-000000000001"


def _book(tmp_path):
    root = tmp_path / "book"
    sections = root / "manuscript" / "sections"
    (sections / "_marks").mkdir(parents=True)
    (root / "manuscript" / "_quarto.yml").write_text(
        "project:\n  type: book\nbook:\n  chapters:\n    - sections/opening.qmd\n"
    )
    (sections / "_spec.md").write_text(SPEC)
    (sections / "opening.qmd").write_text("# The opening {#sec-opening}\n\nA kettle.\n")
    (sections / "_marks" / "opening.md").write_text(
        "# The opening {#sec-opening}\n\n"
        "<!-- @bind(kettle-signal) -->\n<!-- @bind(cooling-asymmetry) -->\n"
    )
    (root / "companion").mkdir()
    (root / "companion" / "entry.md").write_text(
        "# Entry\n\n**Anchors:** `opening` `@bind(kettle-signal)`.\n\n"
        "The position rests on `#kettle-signal`, and on `#cooling-asymmetry`.\n"
    )
    private = root / "private"
    (private / "leads" / "reads" / "cc").mkdir(parents=True)
    (private / "leads" / "rulings").mkdir()
    (private / "spec-trail.md").write_text(TRAIL)
    flagged = {
        "session": SESSION,
        "candidates": [
            {"turn": 1, "sections": [{"slug": "kettle-signal", "kind": "seed"}]},
            {"turn": 2, "sections": [{"slug": "cooling-asymmetry", "kind": "seed"}]},
        ],
    }
    (private / "leads" / "reads" / "cc" / f"{SESSION}.json").write_text(
        json.dumps(flagged)
    )
    (private / "leads" / "rulings" / "kettle-signal.json").write_text(
        json.dumps({"slug": "kettle-signal", "rejected": []})
    )
    return Project.from_settings(
        root,
        {
            "manuscript": {
                "source": "manuscript",
                "quarto": "manuscript/_quarto.yml",
                "chapters": "manuscript/sections",
                "spec": "manuscript/sections/_spec.md",
                "spec_trail": "private/spec-trail.md",
                "spec_trail_leads": "private/leads",
                "companion_dirs": ["companion"],
            }
        },
    )


@pytest.fixture
def book(tmp_path):
    project = _book(tmp_path)
    with project_scope(project):
        yield project.root


def _texts(root):
    return {
        p.relative_to(root).as_posix(): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_a_rename_reaches_every_place_the_slug_is_written(book):
    assert rename.main(["kettle-signal", "whistle-signal"]) == 0
    sections = book / "manuscript" / "sections"
    spec = (sections / "_spec.md").read_text()
    assert "{#whistle-signal}" in spec and "](#whistle-signal)" in spec
    assert "kettle-signal" not in spec
    marks = (sections / "_marks" / "opening.md").read_text()
    assert "@bind(whistle-signal)" in marks and "@bind(cooling-asymmetry)" in marks
    entry = (book / "companion" / "entry.md").read_text()
    assert "`@bind(whistle-signal)`" in entry and "`#whistle-signal`" in entry
    assert "`#cooling-asymmetry`" in entry
    assert "{#whistle-signal}" in (book / "private" / "spec-trail.md").read_text()
    leads = book / "private" / "leads"
    read = json.loads((leads / "reads" / "cc" / f"{SESSION}.json").read_text())
    assert [c["sections"][0]["slug"] for c in read["candidates"]] == [
        "whistle-signal",
        "cooling-asymmetry",
    ]
    assert not (leads / "rulings" / "kettle-signal.json").exists()
    ruling = json.loads((leads / "rulings" / "whistle-signal.json").read_text())
    assert ruling["slug"] == "whistle-signal"
    assert notes.check() == 0
    assert trail.structure_problems() == []


def test_a_dry_run_writes_nothing(book, capsys):
    before = _texts(book)
    assert rename.main(["kettle-signal", "whistle-signal", "--dry-run"]) == 0
    assert _texts(book) == before
    out = capsys.readouterr().out
    assert "manuscript/sections/_spec.md: heading, link" in out
    assert "private/leads/rulings/kettle-signal.json -> " in out
    assert "would become #whistle-signal in 6 file(s)" in out


@pytest.mark.parametrize(
    "old, new, why",
    [
        ("kettle-whistle", "whistle-signal", "no section '#kettle-whistle'"),
        ("kettle-signal", "cooling-asymmetry", "already has a section"),
        ("kettle-signal", "2-whistles", "is not an anchor"),
        ("kettle-signal", "opening/whistle", "a bare anchor"),
        ("kettle-signal", "kettle-signal", "already has that name"),
    ],
)
def test_a_refused_rename_changes_nothing(book, capsys, old, new, why):
    before = _texts(book)
    assert rename.main([old, new]) == 2
    assert why in capsys.readouterr().err
    assert _texts(book) == before


def test_rulings_already_filed_under_the_new_name_stop_the_rename(book, capsys):
    rulings = book / "private" / "leads" / "rulings"
    (rulings / "whistle-signal.json").write_text('{"slug": "whistle-signal"}\n')
    before = _texts(book)
    assert rename.main(["kettle-signal", "whistle-signal"]) == 2
    assert "merge them by hand" in capsys.readouterr().err
    assert _texts(book) == before


SITE = {
    "manuscript": {
        "format": "pages",
        "source": "content",
        "pages": "posts/*/index.md",
        "marks": "specs/{slug}/marks.md",
        "spec": "specs/_site.md",
        "unit_spec": "specs/{slug}/spec.md",
    }
}


def _site(root):
    for slug, title in (("the-kettle", "The Kettle"), ("the-lamp", "The Lamp")):
        post = root / "content" / "posts" / slug
        post.mkdir(parents=True)
        (post / "index.md").write_text(
            f"+++\ntitle = '{title}'\ndate = 2031-04-02\n+++\n\nA paragraph.\n"
        )
        (root / "specs" / slug).mkdir(parents=True)
        (root / "specs" / slug / "spec.md").write_text(
            f"# {title}\n\n## What it argues {{#thesis}}\n\nIt argues one thing.\n"
        )
        (root / "specs" / slug / "marks.md").write_text("<!-- @bind(thesis) -->\n")
    (root / "specs" / "_site.md").write_text("# Site\n")
    return Project.from_settings(root, SITE)


def test_a_units_own_section_keeps_its_bare_binding_bare(tmp_path):
    """A post binds its own section by its bare anchor, and another post's
    section of the same anchor is a different section."""
    root = tmp_path / "site"
    with project_scope(_site(root)):
        assert rename.main(["the-kettle/thesis", "claim"]) == 0
        assert notes.check() == 0
    kettle = root / "specs" / "the-kettle"
    assert "{#claim}" in (kettle / "spec.md").read_text()
    assert (kettle / "marks.md").read_text() == "<!-- @bind(claim) -->\n"
    lamp = root / "specs" / "the-lamp"
    assert "{#thesis}" in (lamp / "spec.md").read_text()
    assert (lamp / "marks.md").read_text() == "<!-- @bind(thesis) -->\n"


def test_a_units_own_section_cannot_move_to_another_unit(tmp_path, capsys):
    with project_scope(_site(tmp_path / "site")):
        assert rename.main(["the-kettle/thesis", "the-lamp/claim"]) == 2
    assert "lives in the-kettle's own spec" in capsys.readouterr().err
