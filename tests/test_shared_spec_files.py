"""A register kept in more than one shared file.

A project can list several shared spec files, for instance what the work
claims in one and the decisions about how it says so in another. The files
are one register: a binding, the trail, the checks and a rename reach a
section in any of them by its anchor, and an anchor is unique across them, so
a section can move from one file to another and keep its name. Every fixture
here is fiction.
"""

import pytest

from quirework.manuscript import footprint, notes, rename, trail
from quirework.project import ConfigurationError, Project, project_scope

CLAIMS = """# What the notebook holds

## A kettle signals without thinking {#kettle-signal}

A whistle reports a state.

## Cooling is not the reverse of boiling {#cooling-asymmetry}

Unlike [the whistle](#kettle-signal), cooling makes no sound.
"""

HOUSE = """# How the notebook says it

## The whistle is named before it is explained {#whistle-first}

The opening names the whistle and explains it a page later, as
[the claim](#kettle-signal) allows.
"""

TRAIL = """## A kettle signals without thinking {#kettle-signal}

## The whistle is named before it is explained {#whistle-first}
"""

SPEC = ["manuscript/sections/_claims.md", "manuscript/sections/_house.md"]


def _settings(spec=SPEC):
    return {
        "manuscript": {
            "source": "manuscript",
            "quarto": "manuscript/_quarto.yml",
            "chapters": "manuscript/sections",
            "spec": spec,
            "spec_trail": "private/spec-trail.md",
            "companion_dirs": ["companion"],
        }
    }


def _book(root):
    sections = root / "manuscript" / "sections"
    (sections / "_marks").mkdir(parents=True)
    (root / "manuscript" / "_quarto.yml").write_text(
        "project:\n  type: book\nbook:\n  chapters:\n    - sections/opening.qmd\n"
    )
    (sections / "_claims.md").write_text(CLAIMS)
    (sections / "_house.md").write_text(HOUSE)
    (sections / "opening.qmd").write_text("# The opening {#sec-opening}\n\nA kettle.\n")
    (sections / "_marks" / "opening.md").write_text(
        "# The opening {#sec-opening}\n\n"
        "<!-- @bind(kettle-signal) -->\n<!-- @bind(whistle-first) -->\n"
    )
    (root / "companion").mkdir()
    (root / "companion" / "entry.md").write_text(
        "# Entry\n\n**Anchors:** `opening` `@bind(whistle-first)`.\n\n"
        "The position rests on `#whistle-first`.\n"
    )
    (root / "private").mkdir()
    (root / "private" / "spec-trail.md").write_text(TRAIL)


@pytest.fixture
def book(tmp_path):
    root = tmp_path / "book"
    _book(root)
    with project_scope(Project.from_settings(root, _settings())):
        yield root


def test_the_files_are_read_in_the_order_they_are_listed(book):
    assert [f.name for f in notes.spec_files()] == ["_claims.md", "_house.md"]
    assert [(s.slug, s.path.name) for s in notes.spec_sections()] == [
        ("kettle-signal", "_claims.md"),
        ("cooling-asymmetry", "_claims.md"),
        ("whistle-first", "_house.md"),
    ]


def test_a_binding_reaches_a_section_of_the_second_file(book, capsys):
    assert notes.check() == 0, capsys.readouterr().err
    assert notes.chapter_bindings()["opening"] == {"kettle-signal", "whistle-first"}


def test_an_anchor_is_unique_across_the_shared_files(book, capsys):
    (book / "manuscript" / "sections" / "_house.md").write_text(
        HOUSE + "\n## Again {#cooling-asymmetry}\n\nA second claim.\n"
    )
    assert notes.check() == 1
    err = capsys.readouterr().err
    assert (
        "manuscript/sections/_house.md: duplicate section anchor "
        "'#cooling-asymmetry' (also on 'Cooling is not the reverse of boiling' "
        "in manuscript/sections/_claims.md)" in err
    )


def test_the_trail_mirrors_sections_of_every_file(book):
    assert trail.structure_problems() == []
    sidecar = trail.parse(
        TRAIL + "\n## Nowhere {#no-such-section}\n", book / "private" / "x.md"
    )
    problems = trail.structure_problems(None, sidecar)
    assert len(problems) == 1
    assert "'#no-such-section' names no spec section" in problems[0]
    trail._known_slug("whistle-first", trail._spec_titles())


def test_a_section_moved_between_files_keeps_its_name(book, capsys):
    """Moving a section to the other file changes no binding, citation or
    trail heading, because all of them name it by its anchor."""
    sections = book / "manuscript" / "sections"
    claims, moved = CLAIMS.split("## Cooling is not the reverse")
    (sections / "_claims.md").write_text(claims)
    (sections / "_house.md").write_text(
        HOUSE + "\n## Cooling is not the reverse" + moved
    )
    assert notes.check() == 0, capsys.readouterr().err
    assert notes.spec_file_of("cooling-asymmetry").name == "_house.md"
    assert trail.structure_problems() == []


def test_a_rename_in_the_second_file_takes_its_trail_with_it(book):
    assert rename.main(["whistle-first", "whistle-named-first"]) == 0
    sections = book / "manuscript" / "sections"
    assert "{#whistle-named-first}" in (sections / "_house.md").read_text()
    assert "whistle-first" not in (sections / "_claims.md").read_text()
    marks = (sections / "_marks" / "opening.md").read_text()
    assert "@bind(whistle-named-first)" in marks
    entry = (book / "companion" / "entry.md").read_text()
    assert "`#whistle-named-first`" in entry
    sidecar = (book / "private" / "spec-trail.md").read_text()
    assert "{#whistle-named-first}" in sidecar
    assert notes.check() == 0
    assert trail.structure_problems() == []


def test_a_link_into_the_second_file_is_renamed_from_the_first(book):
    assert rename.main(["kettle-signal", "whistle-signal"]) == 0
    house = (book / "manuscript" / "sections" / "_house.md").read_text()
    assert "](#whistle-signal)" in house


def test_the_primer_lists_each_file_under_its_own_heading(book, capsys):
    assert notes.main(["--primer"]) == 0
    out = capsys.readouterr().out
    assert "spec — manuscript/sections/_claims.md (shared; 2 section(s);" in out
    assert "spec — manuscript/sections/_house.md (shared; 1 section(s);" in out


def test_every_file_is_in_the_footprint(book):
    register = footprint.footprint().register
    assert register[:2] == tuple(SPEC)


def test_a_single_path_is_a_register_of_one_file(tmp_path):
    root = tmp_path / "book"
    _book(root)
    project = Project.from_settings(root, _settings(SPEC[0]))
    assert project.paths("manuscript.spec") == [root / SPEC[0]]
    project.require("manuscript")
    with project_scope(project):
        assert [f.name for f in notes.spec_files()] == ["_claims.md"]


@pytest.mark.parametrize(
    "spec, why",
    [
        ([SPEC[0], "manuscript/sections/_missing.md"], "spec file is missing: "),
        ([], "names no file"),
        ([SPEC[0], SPEC[0]], "names manuscript/sections/_claims.md twice"),
        ([SPEC[0], ""], "must list nonempty paths"),
    ],
)
def test_a_list_that_names_no_usable_register_is_refused(tmp_path, spec, why):
    root = tmp_path / "book"
    _book(root)
    with pytest.raises(ConfigurationError, match=why):
        Project.from_settings(root, _settings(spec)).require("manuscript")


def test_review_notes_stay_a_single_file(tmp_path):
    """Only the spec takes a list; a list elsewhere is still refused."""
    root = tmp_path / "book"
    _book(root)
    settings = _settings()
    settings["manuscript"]["review_notes"] = [SPEC[0]]
    with pytest.raises(ConfigurationError, match="must be a nonempty path"):
        Project.from_settings(root, settings).require("manuscript")


# The publication boundary. A project may publish only some of its spec files
# (`manuscript.spec_public`); what is published may not cite the rest, and the
# rest may cite anything. Absent, every spec file is published.


def _published(root, public=SPEC[:1]):
    settings = _settings()
    settings["manuscript"]["spec_public"] = public
    return Project.from_settings(root, settings)


@pytest.fixture
def boundary(tmp_path):
    root = tmp_path / "book"
    _book(root)
    with project_scope(_published(root)):
        yield root


def test_an_entry_may_not_cite_an_unpublished_section(boundary, capsys):
    """The entry cites the second file's section twice: as its anchor and in
    its prose. The chapter binds the same section, and that is allowed."""
    assert notes.check() == 1
    err = capsys.readouterr().err
    for line in (3, 5):
        assert (
            f"companion/entry.md:{line}: cites '#whistle-first', a section of "
            "manuscript/sections/_house.md, which is not published" in err
        )
    assert "_marks/opening.md" not in err


def test_an_entry_citing_a_published_section_passes(boundary, capsys):
    (boundary / "companion" / "entry.md").write_text(
        "# Entry\n\n**Anchors:** `opening` `@bind(kettle-signal)`.\n\n"
        "The position rests on `#kettle-signal`.\n"
    )
    assert notes.check() == 0, capsys.readouterr().err


def test_without_a_boundary_every_spec_file_is_published(book, capsys):
    """The default: an entry citing the second file is fine (the `book`
    fixture is the same project with no `manuscript.spec_public`)."""
    assert notes.check() == 0, capsys.readouterr().err


def test_a_published_spec_file_may_not_point_into_an_unpublished_one(boundary, capsys):
    (boundary / "companion" / "entry.md").unlink()
    sections = boundary / "manuscript" / "sections"
    (sections / "_claims.md").write_text(
        CLAIMS + "\nHow it is said is [another matter](#whistle-first).\n"
    )
    assert notes.check() == 1
    err = capsys.readouterr().err
    assert (
        "manuscript/sections/_claims.md:11: '#whistle-first' names a section of "
        "manuscript/sections/_house.md, which is not published" in err
    )


def test_an_unpublished_spec_file_may_cite_a_published_one(boundary, capsys):
    """The second file links to `#kettle-signal` in the first."""
    (boundary / "companion" / "entry.md").unlink()
    assert notes.check() == 0, capsys.readouterr().err


def test_the_same_pointer_passes_when_both_files_are_published(tmp_path, capsys):
    root = tmp_path / "book"
    _book(root)
    (root / "manuscript" / "sections" / "_claims.md").write_text(
        CLAIMS + "\nHow it is said is [another matter](#whistle-first).\n"
    )
    with project_scope(_published(root, SPEC)):
        assert notes.check() == 0, capsys.readouterr().err


@pytest.mark.parametrize("public", [None, SPEC[:1]])
def test_a_reference_inside_any_spec_file_must_resolve(tmp_path, capsys, public):
    root = tmp_path / "book"
    _book(root)
    (root / "manuscript" / "sections" / "_house.md").write_text(
        HOUSE + "\nAs #nowhere-at-all says.\n"
    )
    (root / "companion" / "entry.md").unlink()
    project = (
        Project.from_settings(root, _settings())
        if public is None
        else _published(root, public)
    )
    with project_scope(project):
        assert notes.check() == 1
    assert (
        "manuscript/sections/_house.md:8: '#nowhere-at-all' names no spec section"
        in capsys.readouterr().err
    )


def test_what_is_not_a_reference_is_not_read(book, capsys):
    """A heading's own anchor, code, a comment, frontmatter and a URL
    fragment all carry a `#` without citing a section."""
    (book / "manuscript" / "sections" / "_house.md").write_text(
        HOUSE
        + "\nThe form is `#some-slug`, and a link is https://example.invalid/a#b.\n"
        + "\n<!-- a note about #nothing-here -->\n"
        + "\n```\n#also-nothing\n```\n"
    )
    assert notes.spec_text_refs(
        (book / "manuscript" / "sections" / "_house.md").read_text()
    ) == [("kettle-signal", 6)]
    assert notes.check() == 0, capsys.readouterr().err


def test_a_published_file_must_be_a_spec_file(tmp_path):
    root = tmp_path / "book"
    _book(root)
    (root / "manuscript" / "sections" / "_other.md").write_text("# Other\n")
    with pytest.raises(ConfigurationError, match="manuscript.spec does not list"):
        _published(root, ["manuscript/sections/_other.md"]).require("manuscript")
