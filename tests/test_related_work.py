"""The related work, the register of other work the work is placed against,
and the marks that cite it.

Every work, quotation and slug here is fiction.
"""

import pytest

from quirework.manuscript import footprint, notes, related_work
from quirework.project import ConfigurationError, Project, project_scope

REGISTER = """# Related work

## The Keeper's Almanac, 1911 {#almanac}

A yearly book of light periods and fog signals for the northern coast.

## A letter from the Gull Point keeper {#gull-letter}

Written in the winter the lamp was electrified.

## A survey of foghorns {#foghorn-survey}

Read for the fog post, and not used yet.
"""

ALMANAC = (
    "The light at Gull Point shows one white flash every ten seconds, and "
    "its horn sounds two blasts every thirty.\n"
)


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
    "related_work": "research/related-work.md",
    "related_work_shape": "entries",
    "related_work_copies": "research/copies/{slug}.txt",
}


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    _write(root / "specs" / "_site.md", "# Site\n")
    _write(root / "research" / "related-work.md", REGISTER)
    _write(root / "research" / "copies" / "almanac.txt", ALMANAC)
    _post(
        root,
        "why-the-lamp-turns",
        '<!-- @cite(almanac) the period: "shows one white flash every ten seconds" -->',
    )
    _post(
        root,
        "what-the-fog-signal-is-for",
        "<!-- @cite(almanac) the horn's pattern -->\n"
        '<!-- @cite(gull-letter) "the lamp never once failed us that winter" -->\n'
        "<!-- @cite the keeper's log, in passing -->",
    )
    project = Project.from_settings(root, {"manuscript": SETTINGS})
    with project_scope(project):
        yield root


def test_each_work_lists_the_posts_that_cite_it(site, capsys):
    assert related_work.main([]) == 0
    out = capsys.readouterr().out
    assert "almanac" in out
    # In reading order: same date, so by slug.
    assert "cited by: what-the-fog-signal-is-for, why-the-lamp-turns" in out
    assert "cited by: no post" in out  # the survey


def test_one_post_s_citations(site, capsys):
    assert related_work.main(["-c", "why-the-lamp-turns"]) == 0
    out = capsys.readouterr().out
    assert "almanac" in out and "gull-letter" not in out


def test_the_register_passes_its_gate_and_names_what_is_unused(site, capsys):
    assert related_work.main(["--check"]) == 0
    err = capsys.readouterr().err
    assert "1 work(s) no post cites" in err
    assert "foghorn-survey" in err
    # The letter's quotation has no captured copy to be checked against.
    assert "1 quotation(s) cite a work with no captured copy" in err


def test_a_quotation_that_drifted_from_the_work_is_named(site, capsys):
    _write(
        site / "specs" / "why-the-lamp-turns" / "marks.md",
        '## The light\n\n<!-- @cite(almanac) "shows one red flash every ten '
        'seconds" -->\n',
    )
    assert related_work.main(["--check"]) == 0
    err = capsys.readouterr().err
    assert "1 quotation(s) not in the captured copy" in err
    assert '@cite(almanac) "shows one red flash every ten seconds"' in err


def test_a_quotation_after_a_short_quoted_word_is_still_checked(site, capsys):
    """A short span used to fail the pattern and leave its closing mark to open
    the next span, so the prose between was checked as a quotation and the
    real one after it was never checked."""
    _write(
        site / "specs" / "why-the-lamp-turns" / "marks.md",
        '## The light\n\n<!-- @cite(almanac) the "lamp" entry in the second '
        'edition: "shows one red flash every ten seconds" -->\n',
    )
    assert related_work.main(["--check"]) == 0
    err = capsys.readouterr().err
    assert '@cite(almanac) "shows one red flash every ten seconds"' in err
    assert "second edition" not in err


def test_a_citation_of_no_work_fails_the_notes_gate(site, capsys):
    _write(
        site / "specs" / "why-the-lamp-turns" / "marks.md",
        "## The light\n\n<!-- @cite(almanack) the period -->\n",
    )
    assert notes.check() == 1
    err = capsys.readouterr().err
    assert "@cite(almanack) names no work in the related-work register" in err
    assert "did you mean almanac" in err
    assert related_work.main(["--check"]) == 1


def test_a_work_slug_is_an_identity(site, capsys):
    _write(
        site / "research" / "related-work.md",
        REGISTER + "\n## Another almanac {#almanac}\n\nx\n\n## Unnamed\n\ny\n",
    )
    assert related_work.main(["--check"]) == 1
    err = capsys.readouterr().err
    assert "work slug 'almanac' is also" in err
    assert "work 'Unnamed' has no {#slug} anchor" in err


def test_a_work_per_file(tmp_path, capsys):
    root = tmp_path / "site"
    _write(root / "specs" / "_site.md", "# Site\n")
    _write(
        root / "research" / "almanac.md", '+++\ntitle = "The Keeper\'s Almanac"\n+++\n'
    )
    _write(root / "research" / "gull-letter" / "notes.md", "# A letter\n\nx\n")
    _write(root / "research" / "README.md", "# Research\n")
    _post(root, "why-the-lamp-turns", "<!-- @cite(almanac) the period -->")
    for pattern, expected in (
        ("research/*.md", ["almanac"]),
        ("research/*/notes.md", ["gull-letter"]),
    ):
        settings = {**SETTINGS, "related_work": pattern}
        with project_scope(Project.from_settings(root, {"manuscript": settings})):
            listed = related_work.register()
        assert [s.slug for s in listed] == expected
    assert listed[0].title == "A letter"


def test_the_register_is_part_of_the_work(site):
    fp = footprint.footprint(Project.from_settings(site, {"manuscript": SETTINGS}))
    assert fp.covers("research/related-work.md")


@pytest.mark.parametrize(
    "change, message",
    [
        ({"related_work": "research/missing.md"}, "related-work register is missing"),
        ({"related_work_copies": "research/copies.txt"}, "must contain {slug}"),
    ],
)
def test_the_register_is_declared_so_it_can_be_read(site, change, message):
    settings = {**SETTINGS, **change}
    with pytest.raises(ConfigurationError, match=message):
        Project.from_settings(site, {"manuscript": settings}).require("manuscript")


@pytest.mark.parametrize(
    "change, message",
    [
        ({"related_work_shape": None}, "set manuscript.related_work_shape"),
        ({"related_work_shape": "essay"}, "must be 'prose' or 'entries'"),
        (
            {"related_work": "research/*.md", "related_work_shape": "prose"},
            "is a glob, one file per work",
        ),
        (
            {"related_work_shape": "prose"},
            "a prose register holds none",
        ),
        ({"related_work_public": "yes"}, "must be true or false"),
    ],
)
def test_a_register_says_its_shape(site, change, message):
    settings = {k: v for k, v in {**SETTINGS, **change}.items() if v is not None}
    with pytest.raises(ConfigurationError, match=message):
        Project.from_settings(site, {"manuscript": settings}).require("manuscript")


@pytest.mark.parametrize(
    "key", ["related_work_shape", "related_work_copies", "related_work_public"]
)
def test_a_register_setting_needs_a_register(site, key):
    settings = {k: v for k, v in SETTINGS.items() if k != "related_work"}
    settings[key] = settings.get(key, True)
    with pytest.raises(ConfigurationError, match="names none"):
        Project.from_settings(site, {"manuscript": settings}).require("manuscript")


@pytest.mark.parametrize(
    "old, new, implied",
    [
        ("sources", "related_work", 'related_work_shape = "entries"'),
        ("source_copies", "related_work_copies", None),
        ("references", "related_work", 'related_work_shape = "prose"'),
        ("references_public", "related_work_public", None),
    ],
)
def test_a_renamed_setting_is_refused_with_its_new_name(site, old, new, implied):
    settings = {**SETTINGS, old: "research/related-work.md"}
    with pytest.raises(ConfigurationError) as raised:
        Project.from_settings(site, {"manuscript": settings})
    message = str(raised.value)
    assert f"manuscript.{old} is renamed manuscript.{new}" in message
    assert (implied in message) if implied else "beside it" not in message


def test_a_renamed_mark_kind_fails_the_notes_gate(site, capsys):
    _write(
        site / "specs" / "why-the-lamp-turns" / "marks.md",
        "## The light\n\n<!-- @source(almanac) the period -->\n",
    )
    assert notes.check() == 1
    assert "@source is renamed @cite" in capsys.readouterr().err


PROSE = """# Related work

## The light-keeping literature {#keeping}

Every almanac of the period agrees on the flash, which `#flash` holds, and the
keepers' letters disagree about fog, which [the fog](#keeping) leaves open.
"""

SPEC = "# Site\n\n## The lamp flashes {#flash}\n\nIt flashes.\n"


@pytest.fixture
def essay(tmp_path):
    root = tmp_path / "site"
    _write(root / "specs" / "_site.md", SPEC)
    _write(root / "research" / "related-work.md", PROSE)
    _post(root, "why-the-lamp-turns", "<!-- @cite the keeper's log, in passing -->")
    settings = {
        **{k: v for k, v in SETTINGS.items() if k != "related_work_copies"},
        "related_work_shape": "prose",
    }
    with project_scope(Project.from_settings(root, {"manuscript": settings})):
        yield root


def test_a_prose_register_holds_no_works_and_says_so(essay, capsys):
    assert related_work.register() == []
    assert related_work.main([]) == 0
    assert "is prose: it holds no works to list" in capsys.readouterr().out
    assert related_work.main(["--check"]) == 0
    assert "a prose register, every spec citation resolves" in (capsys.readouterr().out)
    assert notes.check() == 0, capsys.readouterr().err


def test_a_prose_register_s_spec_citation_must_resolve(essay, capsys):
    _write(essay / "research" / "related-work.md", PROSE + "\nSee `#fog-horn`.\n")
    assert related_work.main(["--check"]) == 1
    assert "cites '#fog-horn', which names no spec section" in (capsys.readouterr().err)
    assert notes.check() == 1


def test_a_prose_register_has_no_work_to_cite_by_slug(essay, capsys):
    _write(
        essay / "specs" / "why-the-lamp-turns" / "marks.md",
        "## The light\n\n<!-- @cite(keeping) the period -->\n",
    )
    assert notes.check() == 1
    assert "the related-work register is prose" in capsys.readouterr().err


def test_an_entries_register_s_spec_citation_must_resolve_too(tmp_path, capsys):
    root = tmp_path / "site"
    _write(root / "specs" / "_site.md", SPEC)
    _write(root / "research" / "almanac.md", "# The almanac\n\nIt holds `#flash`.\n")
    _write(root / "research" / "letter.md", "# A letter\n\nIt holds `#fog`.\n")
    _post(root, "why-the-lamp-turns", "<!-- @cite(almanac) the period -->")
    settings = {**SETTINGS, "related_work": "research/*.md"}
    del settings["related_work_shape"]
    with project_scope(Project.from_settings(root, {"manuscript": settings})):
        assert [w.slug for w in related_work.register()] == ["almanac", "letter"]
        assert notes.check() == 1
    assert "research/letter.md:3: cites '#fog'" in capsys.readouterr().err
