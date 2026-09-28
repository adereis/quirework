"""A register of what the work stands on, and the marks that cite it.

Every source, quotation and slug here is fiction.
"""

import pytest

from quirework.manuscript import footprint, notes, sources
from quirework.project import ConfigurationError, Project, project_scope

REGISTER = """# Sources

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
    "sources": "research/sources.md",
    "source_copies": "research/copies/{slug}.txt",
}


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    _write(root / "specs" / "_site.md", "# Site\n")
    _write(root / "research" / "sources.md", REGISTER)
    _write(root / "research" / "copies" / "almanac.txt", ALMANAC)
    _post(
        root,
        "why-the-lamp-turns",
        '<!-- @source(almanac) the period: "shows one white flash every ten '
        'seconds" -->',
    )
    _post(
        root,
        "what-the-fog-signal-is-for",
        "<!-- @source(almanac) the horn's pattern -->\n"
        '<!-- @source(gull-letter) "the lamp never once failed us that winter" -->\n'
        "<!-- @source the keeper's log, in passing -->",
    )
    project = Project.from_settings(root, {"manuscript": SETTINGS})
    with project_scope(project):
        yield root


def test_each_source_lists_the_posts_that_cite_it(site, capsys):
    assert sources.main([]) == 0
    out = capsys.readouterr().out
    assert "almanac" in out
    # In reading order: same date, so by slug.
    assert "cited by: what-the-fog-signal-is-for, why-the-lamp-turns" in out
    assert "cited by: no post" in out  # the survey


def test_one_post_s_sources(site, capsys):
    assert sources.main(["-c", "why-the-lamp-turns"]) == 0
    out = capsys.readouterr().out
    assert "almanac" in out and "gull-letter" not in out


def test_the_register_passes_its_gate_and_names_what_is_unused(site, capsys):
    assert sources.main(["--check"]) == 0
    err = capsys.readouterr().err
    assert "1 source(s) no post cites" in err
    assert "foghorn-survey" in err
    # The letter's quotation has no captured copy to be checked against.
    assert "1 quotation(s) cite a source with no captured copy" in err


def test_a_quotation_that_drifted_from_the_source_is_named(site, capsys):
    _write(
        site / "specs" / "why-the-lamp-turns" / "marks.md",
        '## The light\n\n<!-- @source(almanac) "shows one red flash every ten '
        'seconds" -->\n',
    )
    assert sources.main(["--check"]) == 0
    err = capsys.readouterr().err
    assert "1 quotation(s) not in the captured copy" in err
    assert '@source(almanac) "shows one red flash every ten seconds"' in err


def test_a_citation_of_no_source_fails_the_notes_gate(site, capsys):
    _write(
        site / "specs" / "why-the-lamp-turns" / "marks.md",
        "## The light\n\n<!-- @source(almanack) the period -->\n",
    )
    assert notes.check() == 1
    err = capsys.readouterr().err
    assert "@source(almanack) names no source in the register" in err
    assert "did you mean almanac" in err
    assert sources.main(["--check"]) == 1


def test_a_source_slug_is_an_identity(site, capsys):
    _write(
        site / "research" / "sources.md",
        REGISTER + "\n## Another almanac {#almanac}\n\nx\n\n## Unnamed\n\ny\n",
    )
    assert sources.main(["--check"]) == 1
    err = capsys.readouterr().err
    assert "source slug 'almanac' is also" in err
    assert "source 'Unnamed' has no {#slug} anchor" in err


def test_a_source_per_file(tmp_path, capsys):
    root = tmp_path / "site"
    _write(root / "specs" / "_site.md", "# Site\n")
    _write(
        root / "research" / "almanac.md", '+++\ntitle = "The Keeper\'s Almanac"\n+++\n'
    )
    _write(root / "research" / "gull-letter" / "notes.md", "# A letter\n\nx\n")
    _write(root / "research" / "README.md", "# Research\n")
    _post(root, "why-the-lamp-turns", "<!-- @source(almanac) the period -->")
    for pattern, expected in (
        ("research/*.md", ["almanac"]),
        ("research/*/notes.md", ["gull-letter"]),
    ):
        settings = {**SETTINGS, "sources": pattern}
        with project_scope(Project.from_settings(root, {"manuscript": settings})):
            listed = sources.register()
        assert [s.slug for s in listed] == expected
    assert listed[0].title == "A letter"


def test_the_register_is_part_of_the_work(site):
    fp = footprint.footprint(Project.from_settings(site, {"manuscript": SETTINGS}))
    assert fp.covers("research/sources.md")


@pytest.mark.parametrize(
    "change, message",
    [
        ({"sources": "research/missing.md"}, "sources register is missing"),
        ({"source_copies": "research/copies.txt"}, "must contain {slug}"),
    ],
)
def test_the_register_is_declared_so_it_can_be_read(site, change, message):
    settings = {**SETTINGS, **change}
    with pytest.raises(ConfigurationError, match=message):
        Project.from_settings(site, {"manuscript": settings}).require("manuscript")
