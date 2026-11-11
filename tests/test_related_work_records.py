"""Works as records: their facts, and their links to the spec, each with a
stance.

Every work, stance, flag and slug here is fiction.
"""

import pytest

from quirework.manuscript import notes, related_work
from quirework.project import ConfigurationError, Project, project_scope

SPEC = (
    "# Site\n\n"
    "## The lamp turns {#lamp-period}\n\nOnce every ten seconds.\n\n"
    "## The horn sounds in fog {#fog-signal}\n\nTwo blasts.\n"
)

ALMANAC = """+++
title = "The Keeper's Almanac, 1911"
authors = ["Ada Lantern", "Ben Wick"]
year = 1911
venue = "Harbour Press, Port Quill"

[verification]
status = "checked"
date = 2031-03-01
against = "the library's bound copy"
+++

A yearly book of light periods and fog signals for the northern coast.

## `#lamp-period`

*Stance: support.*

It gives the period the lamp post relies on.

## `#fog-signal`

*Stance: counter, contested.*

It times the horn differently, and the post sides with the log.
"""

LETTER = """+++
title = "A letter from the Gull Point keeper"
+++

Written in the winter the lamp was electrified.

## `#lamp-period`

*Stance: support.*
"""


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _post(root, slug, marks):
    _write(
        root / "content" / "posts" / slug / "index.md",
        f"+++\ntitle = '{slug}'\ndate = 2031-04-02\n+++\n\n## The light\n\nIt turns.\n",
    )
    _write(root / "specs" / slug / "marks.md", f"## The light\n\n{marks}\n")


SETTINGS = {
    "format": "pages",
    "source": "content",
    "pages": "posts/*/index.md",
    "marks": "specs/{slug}/marks.md",
    "unit_name": "post",
    "spec": "specs/_site.md",
    "related_work": "research/works/*.md",
    "related_work_stances": ["support", "counter", "method"],
    "related_work_flags": ["contested"],
}


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    _write(root / "specs" / "_site.md", SPEC)
    _write(root / "research" / "works" / "almanac.md", ALMANAC)
    _write(root / "research" / "works" / "gull-letter.md", LETTER)
    _post(root, "why-the-lamp-turns", "<!-- @cite(almanac) the period -->")
    _post(root, "what-the-fog-signal-is-for", "<!-- @cite(gull-letter) the winter -->")
    yield root


def _scope(root, **change):
    settings = {k: v for k, v in {**SETTINGS, **change}.items() if v is not None}
    return project_scope(Project.from_settings(root, {"manuscript": settings}))


def test_a_work_carries_its_facts_and_its_links(site):
    with _scope(site):
        works = {w.slug: w for w in related_work.register()}
    almanac = works["almanac"]
    assert almanac.facts["authors"] == ["Ada Lantern", "Ben Wick"]
    assert almanac.facts["year"] == 1911
    assert [(k.section, k.stance, k.flags) for k in almanac.links] == [
        ("lamp-period", "support", ()),
        ("fog-signal", "counter", ("contested",)),
    ]
    assert almanac.links[0].note == "It gives the period the lamp post relies on."
    assert almanac.links[1].line == 21
    assert related_work.verification(almanac) == {
        "status": "checked",
        "date": "2031-03-01",
        "against": "the library's bound copy",
    }
    # A link may carry no note, and a work that states no check is unchecked.
    assert works["gull-letter"].links[0].note == ""
    assert related_work.verification(works["gull-letter"])["status"] == "unchecked"


def test_well_formed_records_pass_the_gate(site, capsys):
    with _scope(site):
        assert notes.check() == 0, capsys.readouterr().err


@pytest.mark.parametrize(
    "body, message",
    [
        ("## `#lamp-period`\n\nIt gives the period.\n", "has no stance line"),
        ("## `#lamp-period`\n\n*Stance: .*\n", "write it as `*Stance: NAME.*`"),
        ("## `#lamp-period`\n\n*Stance: praise.*\n", "stance 'praise' is not one"),
        ("## `#lamp-period`\n\n*Stance: support, loud.*\n", "flag 'loud' is not one"),
        (
            (
                "## `#lamp-period`\n\n*Stance: support.*\n\n"
                "## `#lamp-period`\n\n*Stance: method.*\n"
            ),
            "links this section twice",
        ),
        ("## `#tide-table`\n\n*Stance: support.*\n", "cites '#tide-table'"),
    ],
)
def test_a_link_that_does_not_read_fails_the_gate(site, capsys, body, message):
    _write(site / "research" / "works" / "gull-letter.md", f"# A letter\n\n{body}")
    with _scope(site):
        assert notes.check() == 1
    assert message in capsys.readouterr().err


def test_without_a_declared_vocabulary_any_stance_is_read(site, capsys):
    _write(
        site / "research" / "works" / "gull-letter.md",
        "# A letter\n\n## `#lamp-period`\n\n*Stance: praise, loud.*\n",
    )
    with _scope(site, related_work_stances=None, related_work_flags=None):
        assert notes.check() == 0, capsys.readouterr().err
        letter = {w.slug: w for w in related_work.register()}["gull-letter"]
    assert (letter.links[0].stance, letter.links[0].flags) == ("praise", ("loud",))


@pytest.mark.parametrize(
    "facts, message",
    [
        ('authors = "Ada Lantern"', "authors must be a list of names"),
        ('year = "the winter"', "year must be a number"),
        ("venue = 1911", "venue must be text"),
        ('verification = "yes"', "verification is a table"),
        ('[verification]\nstatus = "glanced"', "it is one of unchecked"),
        ('[verification]\nstatus = "verified"', "says when it was checked"),
        (
            '[verification]\nstatus = "unchecked"\nagainst = "a copy"',
            "an unchecked work records no check",
        ),
        (
            '[verification]\nstatus = "checked"\ndate = "spring"\nagainst = "a copy"',
            "verification date must be a date",
        ),
        (
            (
                '[verification]\nstatus = "checked"\ndate = 2031-03-01\n'
                'against = "a copy"\nby = "me"'
            ),
            "verification sets by",
        ),
    ],
)
def test_facts_that_do_not_read_fail_the_gate(site, capsys, facts, message):
    _write(
        site / "research" / "works" / "gull-letter.md",
        f"+++\n{facts}\n+++\n\nA letter.\n",
    )
    with _scope(site):
        assert notes.check() == 1
    assert message in capsys.readouterr().err


def test_facts_in_yaml_are_refused_rather_than_dropped(site, capsys):
    _write(
        site / "research" / "works" / "gull-letter.md",
        "---\ntitle: A letter\nauthors:\n  - Ada Lantern\n---\n\nA letter.\n",
    )
    with _scope(site):
        assert notes.check() == 1
    assert "states authors in YAML frontmatter" in capsys.readouterr().err


def test_a_one_file_register_links_its_works_too(tmp_path, capsys):
    root = tmp_path / "site"
    _write(root / "specs" / "_site.md", SPEC)
    _write(
        root / "research" / "related-work.md",
        "# Related work\n\n"
        "## The almanac {#almanac}\n\nA yearly book.\n\n"
        "### `#lamp-period`\n\n*Stance: support.*\n\nThe period.\n\n"
        "## A letter {#gull-letter}\n\nA winter letter.\n\n"
        "### `#fog-signal`\n\n*Stance: method.*\n",
    )
    _post(root, "why-the-lamp-turns", "<!-- @cite(almanac) the period -->")
    with _scope(
        root, related_work="research/related-work.md", related_work_shape="entries"
    ):
        works = related_work.register()
        assert notes.check() == 0, capsys.readouterr().err
    assert [[(k.section, k.stance) for k in w.links] for w in works] == [
        [("lamp-period", "support")],
        [("fog-signal", "method")],
    ]
    assert works[0].links[0].note == "The period."


@pytest.mark.parametrize(
    "change, message",
    [
        ({"related_work_stances": "support"}, "is a list of words"),
        ({"related_work_stances": ["support", "support"]}, "names a word twice"),
        ({"related_work_flags": ["contested, loud"]}, "without a comma"),
        ({"related_work_stances": []}, "declares no stance"),
        (
            {
                "related_work": "research/related-work.md",
                "related_work_shape": "prose",
            },
            "a prose register holds none",
        ),
    ],
)
def test_the_vocabularies_are_declared_so_they_can_be_read(site, change, message):
    _write(site / "research" / "related-work.md", "# Related work\n")
    settings = {k: v for k, v in {**SETTINGS, **change}.items() if v is not None}
    with pytest.raises(ConfigurationError, match=message):
        Project.from_settings(site, {"manuscript": settings}).require("manuscript")


NOTE = """+++
title = "The almanac and the letter on the winter light"
works = ["almanac", "gull-letter"]
+++

The almanac's period and the keeper's letter disagree about the winter
the lamp was electrified.

## `#lamp-period`

The letter dates the change the almanac never records.
"""


def _noted(root, text=NOTE):
    _write(root / "research" / "notes" / "winter-light.md", text)
    return _scope(root, related_work_notes="research/notes/*.md")


def test_a_note_relates_works_to_a_section(site, capsys):
    with _noted(site):
        assert notes.check() == 0, capsys.readouterr().err
        (note,) = related_work.register_notes()
        files = related_work.files()
    assert note.works == ("almanac", "gull-letter")
    assert [(k.section, k.stance, k.note) for k in note.links] == [
        ("lamp-period", None, "The letter dates the change the almanac never records.")
    ]
    # A note is part of the register, so its citations are resolved with it.
    assert site / "research" / "notes" / "winter-light.md" in files


@pytest.mark.parametrize(
    "change, message",
    [
        (
            ("## `#lamp-period`\n\n", "## `#lamp-period`\n\n*Stance: support.*\n\n"),
            "a note's link takes no stance",
        ),
        (
            ('works = ["almanac", "gull-letter"]', 'works = ["almanac", "tide-book"]'),
            "names 'tide-book', which is no work",
        ),
        (('works = ["almanac", "gull-letter"]\n', ""), "names no works"),
        (
            ('works = ["almanac", "gull-letter"]', 'works = ["almanac"]\nyear = 1911'),
            "states year: a note states no citation",
        ),
        (("## `#lamp-period`", "## `#tide-table`"), "cites '#tide-table'"),
    ],
)
def test_a_note_that_does_not_read_fails_the_gate(site, capsys, change, message):
    old, new = change
    assert old in NOTE
    with _noted(site, NOTE.replace(old, new)):
        assert notes.check() == 1
    assert message in capsys.readouterr().err


def test_a_note_takes_no_work_s_slug(site, capsys):
    _write(site / "research" / "notes" / "almanac.md", NOTE)
    with _scope(site, related_work_notes="research/notes/*.md"):
        assert notes.check() == 1
    assert "has the slug of the work" in capsys.readouterr().err


@pytest.mark.parametrize(
    "change, message",
    [
        ({"related_work_notes": "research/notes.md"}, "is a glob with one file"),
        (
            {
                "related_work": "research/related-work.md",
                "related_work_shape": "prose",
                "related_work_stances": None,
                "related_work_flags": None,
                "related_work_notes": "research/notes/*.md",
            },
            "a prose register holds none",
        ),
    ],
)
def test_the_notes_are_declared_so_they_can_be_read(site, change, message):
    _write(site / "research" / "related-work.md", "# Related work\n")
    settings = {k: v for k, v in {**SETTINGS, **change}.items() if v is not None}
    with pytest.raises(ConfigurationError, match=message):
        Project.from_settings(site, {"manuscript": settings}).require("manuscript")


def test_the_listings_by_name_stance_section_and_verification(site):
    with _noted(site):
        found = related_work.listing()
    assert [w["work"] for w in found["by_name"]] == ["gull-letter", "almanac"]
    assert found["by_name"][1]["links"][1] == {
        "section": "fog-signal",
        "stance": "counter",
        "flags": ["contested"],
    }
    # In the order the project declares its stances.
    assert [g["stance"] for g in found["by_stance"]] == ["support", "counter"]
    assert found["by_section"][0] == {
        "section": "lamp-period",
        "title": "The lamp turns",
        "works": [
            {"work": "gull-letter", "stance": "support", "flags": []},
            {"work": "almanac", "stance": "support", "flags": []},
        ],
        "notes": ["winter-light"],
    }
    assert found["unverified"] == ["gull-letter"]


@pytest.mark.parametrize(
    "flag, expected",
    [
        ("--by-name", "almanac                            The Keeper's Almanac, 1911"),
        ("--by-stance", "counter:\n  almanac"),
        ("--by-section", "#fog-signal  The horn sounds in fog\n  almanac"),
        ("--unverified", "  gull-letter"),
    ],
)
def test_each_listing_prints(site, capsys, flag, expected):
    with _scope(site):
        assert related_work.main([flag]) == 0
    assert expected in capsys.readouterr().out


def test_the_listing_file_is_written_and_gated(site, capsys):
    _write(site / "research" / "notes" / "winter-light.md", NOTE)
    with _scope(
        site,
        related_work_notes="research/notes/*.md",
        related_work_index="research/listings.md",
    ):
        assert notes.check() == 1
        assert "listings are stale" in capsys.readouterr().err
        assert related_work.main(["--write"]) == 0
        text = (site / "research" / "listings.md").read_text()
        assert notes.check() == 0, capsys.readouterr().err
        assert related_work.main(["--check"]) == 0
        _write(site / "research" / "works" / "gull-letter.md", LETTER + "\nMore.\n")
        assert related_work.main(["--write"]) == 0  # the letter's prose is not listed
        _write(
            site / "research" / "works" / "gull-letter.md",
            LETTER.replace("support", "method"),
        )
        assert related_work.main(["--check"]) == 1
    assert "### `#fog-signal` The horn sounds in fog" in text
    assert "- support: `gull-letter`, `almanac`" in text
    assert "- notes: `winter-light`" in text
    assert "## Not yet verified\n\n- `gull-letter`" in text
    assert "Generated by `quirework spec related-work --write`" in text


def test_the_listing_file_is_kept_outside_the_records(site):
    settings = {**SETTINGS, "related_work_index": "research/works/listings.md"}
    with pytest.raises(ConfigurationError, match="outside the records' glob"):
        Project.from_settings(site, {"manuscript": settings}).require("manuscript")


def test_the_spec_index_names_each_section_s_works_by_stance(site):
    from quirework.manuscript import specindex, specsection

    with _noted(site):
        index = specindex.build()
        found = specsection.render(specsection.context("fog-signal"))
    by = {s["slug"]: s for s in index["sections"]}
    assert by["fog-signal"]["related_work"] == [
        {
            "work": "almanac",
            "title": "The Keeper's Almanac, 1911",
            "stance": "counter",
            "flags": ["contested"],
        }
    ]
    assert {"note": "winter-light", "title": NOTE.split('"')[1]} in by["lamp-period"][
        "related_work"
    ]
    assert "almanac  The Keeper's Almanac, 1911  [counter, contested]" in found
