"""The bundled example book, run through the gates a real project runs.

Every other test here builds a project out of fragments written for it. This
one reads the example in `examples/demo-book`, which was written the way a
book is written rather than the way a fixture is, and asserts that the
commands a working author runs all day still pass on it. It is the check that
the pieces compose: a spec that binds, sidecars that mirror, a figure that
resolves, procedures that a catalog can find.

Nothing here contacts a model. The derived-edition build is exercised through
its dry run, which is the whole pipeline up to the point where it would spend
money.
"""

from pathlib import Path

import pytest

from quirework.project import Project, project_scope

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "demo-book"


@pytest.fixture
def book():
    if not (EXAMPLE / "quirework.toml").is_file():
        pytest.skip("the example book is not in this checkout")
    project = Project.load(EXAMPLE)
    with project_scope(project):
        yield project


def run(project, command, *argv):
    """A command as the author would type it, returning its exit status."""
    return project.invoke(command, list(argv))


def test_the_reading_order_and_the_files_agree(book):
    assert run(book, "book chapters", "--check") == 0


def test_the_marks_mirror_their_chapters_and_bind_the_spec(book):
    assert run(book, "book notes", "--check") == 0


def test_every_embedded_figure_resolves_and_converts_to_gray(book):
    assert run(book, "book figures", "--check") == 0


def test_quoted_material_still_appears_in_the_book(book):
    # `book quotes` is a check and takes no --check flag, unlike its siblings.
    assert run(book, "book quotes") == 0


def test_the_procedure_catalog_matches_the_procedures_on_disk(book):
    assert run(book, "sop catalog", "--check") == 0


def test_the_personas_and_the_instrument_validate(book):
    assert run(book, "sop personas", "check") == 0


def test_the_prose_is_wrapped_the_way_the_project_wraps_it(book):
    sources = sorted(
        str(p)
        for p in (EXAMPLE / "manuscript").rglob("*.qmd")
    )
    assert sources, "the example has chapters"
    assert run(book, "text wrap", "--check", *sources) == 0


def test_the_example_declares_all_four_editions(book):
    """The example is the proof that one source reaches four formats.

    An example that renders only HTML demonstrates the easy format and
    silently skips the three that stage differently, which is where the
    disagreements this tool exists to catch actually happen.
    """
    from quirework.book import render

    assert set(render.book_formats()) == {"html", "typst", "epub", "docx"}


def test_the_derived_edition_assembles_without_buying_an_answer(book):
    assert run(book, "book variant", "build", "manager", "--dry-run") == 0


def test_every_spec_section_is_expressed_by_some_chapter(book):
    """A commitment nothing binds is either unwritten or no longer held."""
    from quirework.book import notes

    declared = notes.spec_slugs()
    bound = {
        annotation.args[0]
        for annotation in notes.scan()
        if annotation.kind == "bind" and annotation.args
    }
    assert declared, "the example declares commitments"
    assert declared == bound


def test_the_book_keeps_its_own_punctuation_treaty(book):
    """The house rule the story is about, asserted on the story itself."""
    prose = sorted((EXAMPLE / "manuscript").rglob("*.qmd"))
    offenders = [str(p) for p in prose if "—" in p.read_text(encoding="utf-8")]
    assert offenders == []
