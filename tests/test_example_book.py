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
def book(monkeypatch):
    if not (EXAMPLE / "quirework.toml").is_file():
        pytest.skip("the example book is not in this checkout")
    # The example's extension is source in this repository. Put that source on
    # the test import path so a fresh clone does not depend on an editable
    # installation left behind in one contributor's virtual environment.
    monkeypatch.syspath_prepend(str(EXAMPLE / "cvc_checks" / "src"))
    project = Project.load(EXAMPLE)
    with project_scope(project):
        yield project


def run(project, command, *argv):
    """A command as the author would type it, returning its exit status."""
    return project.invoke(command, list(argv))


def test_the_reading_order_and_the_files_agree(book):
    assert run(book, "spec units", "--check") == 0


def test_the_marks_mirror_their_chapters_and_bind_the_spec(book):
    assert run(book, "spec notes", "--check") == 0


def test_every_embedded_figure_resolves_and_converts_to_gray(book):
    assert run(book, "publish figures", "--check") == 0


def test_quoted_material_still_appears_in_the_book(book):
    # `spec quotes` is a check and takes no --check flag, unlike its siblings.
    assert run(book, "spec quotes") == 0


def test_the_procedure_catalog_matches_the_procedures_on_disk(book):
    assert run(book, "sop catalog", "--check") == 0


def test_the_provenance_index_matches_the_captured_corpus(book):
    """The committed index is a golden record of what the scanner sees.

    `--check` rebuilds the index from the corpus and compares it byte for
    byte, so this gate fails two ways. A capture edited without
    regenerating leaves a recorded SHA-256 that no longer describes the
    file it names. A change to how sessions are scanned moves the turn
    counts and edit tallies underneath an index nobody thought to
    rebuild, and this is the only captured session in the repository that
    would notice.
    """
    assert run(book, "provenance catalog", "--check") == 0


def test_every_breadcrumb_leads_to_the_turn_it_names(book):
    """The spec's trail points into the committed session by logged time.

    A breadcrumb is a pointer, never a copy, so following each one is the
    only proof it still names one of the author's turns. A change to how a
    session is read, or to which messages count as the author's, would
    move the turns out from under the pointers, and this is the one real
    trail in the repository that would notice.
    """
    assert run(book, "spec trail", "--check") == 0


def test_the_personas_and_the_instrument_validate(book):
    assert run(book, "sop personas", "check") == 0


def test_the_prose_is_wrapped_the_way_the_project_wraps_it(book):
    sources = sorted(str(p) for p in (EXAMPLE / "manuscript").rglob("*.qmd"))
    assert sources, "the example has chapters"
    assert run(book, "text wrap", "--check", *sources) == 0


def test_the_example_declares_all_four_editions(book):
    """The example is the proof that one source reaches four formats.

    An example that renders only HTML demonstrates the easy format and
    silently skips the three that stage differently, which is where the
    disagreements this tool exists to catch actually happen.
    """
    from quirework.publish import render

    assert set(render.book_formats()) == {"html", "typst", "epub", "docx"}


def test_the_derived_edition_assembles_without_buying_an_answer(book):
    assert run(book, "publish variant", "build", "manager", "--dry-run") == 0


def test_every_spec_section_is_expressed_by_some_chapter(book):
    """A commitment nothing binds is either unwritten or no longer held."""
    from quirework.manuscript import notes

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


def test_a_self_driven_reader_gets_clean_text_and_real_figure_names(book, tmp_path):
    """The directory a seat walks on its own, built from the same reading order
    as the printed book. Both halves matter: the source would leak marks and
    anchors, and a figure renamed `material-024.png` would stop a chapter that
    asks for `sidecar.png` by name. The embed is resolved against the chapter
    that carries it, so this works for a project that keeps its illustrations
    somewhere other than beside the front matter."""
    from quirework.publish import panelkit

    out = tmp_path / "read"
    assert panelkit.prose_dir(out) == 0
    chapters = sorted(p.name for p in (out / "chapters").glob("*.md"))
    assert chapters == [
        f"{i:02d}-{stem}.md"
        for i, stem in enumerate(
            ("index", "preface", "the-book-that-disagreed-with-itself", "the-sidecar"),
            start=1,
        )
    ]
    assert sorted(p.name for p in (out / "figures").glob("*")) == ["sidecar.png"]
    text = (out / "chapters" / "04-the-sidecar.md").read_text(encoding="utf-8")
    assert "figures/sidecar.png" in text
    assert "@bind" not in text and "{#sec-" not in text
    assert "Figure description" not in text


def test_a_prose_directory_names_its_book_beside_itself(book, tmp_path):
    """A mounted read carries nothing the record froze, so the directory has
    to say which book state it is. It cannot say so inside itself: a seat
    mounts it and must find chapters and figures and nothing else. So the
    provenance sits beside it, with a digest a plan can pin."""
    import json

    from quirework.publish import panelkit
    from quirework.runs.storage import tree_digest

    out = tmp_path / "read"
    assert panelkit.prose_dir(out) == 0
    assert sorted(p.name for p in out.iterdir()) == ["chapters", "figures"]
    record = json.loads(panelkit.manifest_path(out).read_text(encoding="utf-8"))
    assert panelkit.manifest_path(out).parent == out.parent
    assert record["digest"] == tree_digest(out)
    assert record["source_sha"]
    assert "chapters/04-the-sidecar.md" in record["files"]
    # The example has no Parts, so no chapter opens with one.
    assert set(record["parts"].values()) == {None}
    first = (out / "chapters" / "01-index.md").read_text(encoding="utf-8")
    assert first.startswith("# ")


def test_a_prose_directory_shows_the_parts_a_print_reader_would_see():
    """A divider before each Part's first chapter, a running head after.

    Without them a seat could not say which Part a chapter sat in, and its
    remarks about the book's structure described the delivery instead. A
    chapter outside every Part, like a preface, carries neither, and a Part
    that resumes after an unparted page announces itself again, as a divider
    would.
    """
    from quirework.publish.bookstage import Row
    from quirework.publish.panelkit import part_heads

    def chapter(title, part):
        return Row("chapter", f"{title}.qmd", title, None, True, part)

    one, two = "Part One: Rooms", "Part Two: Doors"
    heads = part_heads(
        [
            chapter("preface", None),
            chapter("hall", one),
            chapter("stair", one),
            chapter("porch", two),
            chapter("gate", two),
        ]
    )
    assert heads == [
        "",
        f"# {one}\n\n---\n\n",
        f"*{one}*\n\n",
        f"# {two}\n\n---\n\n",
        f"*{two}*\n\n",
    ]
    assert part_heads([chapter("a", one), chapter("b", None), chapter("c", one)]) == [
        f"# {one}\n\n---\n\n",
        "",
        f"# {one}\n\n---\n\n",
    ]


def test_the_machine_habit_policy_reads_the_chapters_in_order(book):
    """The example adopts `config template tells` as written; the advisory
    must read its chapters in reading order and never fail the run."""
    from quirework.text import tells

    assert run(book, "text tells") == 0
    names = [Path(rel).stem for rel, _, _ in tells.measure()]
    assert names[0] == "preface" and len(names) == 3
