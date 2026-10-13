"""The bundled example book, run through the gates a real project runs.

Every other test here builds a project out of fragments written for it. This
one reads the example in `examples/side-quest`, which was written the way a
book is written, by an author and a companion over many sessions, and asserts
that the commands a working author runs all day still pass on it: a spec that
binds, sidecars that mirror, a figure that resolves in two inks, procedures a
catalog can find, a trail that leads into the captured sessions, and three
derived editions bound to the same spec as the book.

The example brings its own extension, `sq_checks`: a check suite that refuses
an em dash and a figure check that holds every plate to two inks. It is source
in this repository, put on the import path here rather than installed, so a
fresh clone does not depend on an editable installation left in one
contributor's environment.

Nothing here contacts a model or runs Quarto. The edition builds are
exercised through their dry runs, which is the whole pipeline up to the point
where it would spend money.
"""

import json
from pathlib import Path

import pytest

from quirework.project import Project, project_scope

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "side-quest"
EDITIONS = ("poems", "six-pager", "young-readers")


@pytest.fixture
def book(monkeypatch):
    if not (EXAMPLE / "quirework.toml").is_file():
        pytest.skip("the example book is not in this checkout")
    monkeypatch.syspath_prepend(str(EXAMPLE / "sq_checks" / "src"))
    project = Project.load(EXAMPLE)
    with project_scope(project):
        yield project


def run(project, command, *argv):
    """A command as the author would type it, returning its exit status."""
    return project.invoke(command, list(argv))


def test_the_reading_order_and_the_files_agree(book):
    assert run(book, "spec units", "--check") == 0


def test_the_marks_mirror_their_chapters_and_editions_and_bind_the_spec(book):
    assert run(book, "spec notes", "--check") == 0


def test_the_figure_resolves_converts_to_gray_and_keeps_to_two_inks(book):
    """The plate passes Quirework's own figure gate and the book's: its
    `publish.figure_checks` names `sq_checks.figures`, which admits black,
    gray and one orange and nothing else."""
    assert run(book, "publish figures", "--check") == 0


def test_the_books_own_check_suite_passes(book):
    """`checks.command` replaces the default suite with the book's, which
    runs the default checks and then its own: no em dash anywhere in the
    manuscript or the figures, and two inks in every figure."""
    assert run(book, "dev check") == 0


def test_quoted_material_still_appears_in_the_book(book):
    assert run(book, "spec quotes") == 0


def test_the_procedure_catalog_matches_the_procedures_on_disk(book):
    assert run(book, "sop catalog", "--check") == 0


def test_the_personas_and_the_instruments_validate(book):
    assert run(book, "sop personas", "check") == 0


def test_the_provenance_index_matches_the_captured_corpus(book):
    """The committed index is a golden record of what the scanner sees, over
    fifteen captured sessions: the companion's, and one of Codex painting the
    cover. A change to how sessions are scanned moves the counts beneath it."""
    assert run(book, "provenance catalog", "--check") == 0


def test_every_breadcrumb_leads_to_the_turn_it_names(book):
    """Fifty pointers into the committed sessions, one of them a reversal:
    the name's origin, told one way in the first session and corrected in the
    fourteenth. Following each is the only proof it still names a turn."""
    assert run(book, "spec trail", "--check") == 0


def test_a_reversed_commitment_keeps_its_first_telling_on_the_trail(book):
    from quirework.manuscript import trail

    kinds = [crumb.kind for crumb in trail.load().section("the-name").crumbs]
    assert kinds[0] == "seed" and "reverse" in kinds


def test_the_prose_is_wrapped_the_way_the_project_wraps_it(book):
    sources = sorted(str(p) for p in (EXAMPLE / "manuscript").rglob("*.qmd"))
    assert sources, "the example has chapters"
    assert run(book, "text wrap", "--check", *sources) == 0


def test_the_book_declares_all_four_formats(book):
    """The example is the proof that one source reaches four formats."""
    from quirework.publish import render

    assert set(render.book_formats()) == {"html", "typst", "epub", "docx"}


def test_every_spec_section_is_expressed_by_some_chapter(book):
    """A commitment nothing binds is either unwritten or no longer held."""
    from quirework.manuscript import notes

    declared = notes.spec_slugs()
    assert declared, "the example declares commitments"
    assert set().union(*notes.chapter_bindings().values()) == declared


def test_three_editions_are_composed_from_the_book(book):
    from quirework.manuscript import editions

    assert editions.composed() == list(EDITIONS)


def test_one_commitment_is_followed_into_every_edition(book, capsys):
    """The example's claim in one command: the name's commitment is carried
    by the chapter and by every edition that retells it, and each says
    where. When the commitment reversed, this is how the companion found
    every artifact that had to change."""
    from quirework.manuscript import notes

    assert notes.main(["bind", "--slug", "the-name"]) == 0
    sources = {
        line.split(":")[0].strip()
        for line in capsys.readouterr().out.splitlines()
        if "_marks/" in line
    }
    assert sources == {
        "manuscript/sections/_marks/the-name.md",
        "variants/poems/_marks/the-name.md",
        "variants/six-pager/_marks/memo.md",
        "variants/young-readers/_marks/the-name.md",
    }
    carried = notes.edition_bindings()
    assert set(carried) == set(EDITIONS)
    assert all("the-name" in carried[kind]["bind"] for kind in EDITIONS)


def test_every_edition_draft_names_the_model_that_served_it(book):
    """The drafts came from the run engine, and each kept its receipt: the
    six-pager from one Claude seat as one document, the young readers'
    from Claude by chapter, the poems from Codex by chapter."""
    harnesses = {}
    for kind in EDITIONS:
        ledger = EXAMPLE / "variants" / kind / "_raw" / "receipts.json"
        drafts = json.loads(ledger.read_text(encoding="utf-8"))["drafts"]
        assert drafts, kind
        for unit, receipt in drafts.items():
            assert receipt["model"] and receipt["seat"], (kind, unit)
            assert (EXAMPLE / "variants" / kind / "_raw" / f"{unit}.md").is_file()
        harnesses[kind] = {receipt["harness"] for receipt in drafts.values()}
    assert harnesses == {
        "six-pager": {"claude"},
        "young-readers": {"claude"},
        "poems": {"codex"},
    }
    assert list(
        json.loads((EXAMPLE / "variants/six-pager/_raw/receipts.json").read_text())[
            "drafts"
        ]
    ) == ["document"]


@pytest.mark.parametrize(
    "kind, shape", [("six-pager", "one document from"), ("poems", "chapter(s)")]
)
def test_an_edition_assembles_without_buying_an_answer(book, capsys, kind, shape):
    assert run(book, "publish variant", "build", kind, "--dry-run") == 0
    assert shape in capsys.readouterr().out


def test_the_book_keeps_its_own_punctuation_treaty(book):
    """The house rule the story is about, asserted on the story and on every
    composed edition. The blind drafts in `_raw/` are evidence of what a
    model wrote and are not held to it."""
    composed = [
        p
        for root in ("manuscript", "variants")
        for p in (EXAMPLE / root).rglob("*.qmd")
        if "_raw" not in p.parts
    ]
    assert composed
    offenders = [str(p) for p in composed if "—" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_a_self_driven_reader_gets_clean_text_and_the_figure_by_name(book, tmp_path):
    """The directory a blind reader walks on its own, built from the same
    reading order as the printed book, Parts announced as a print reader
    would see them, and the plate under the name the chapter asks for. The
    note on the book's unlisted first page is front matter, so the reader
    starts at the preface."""
    from quirework.publish import panelkit

    out = tmp_path / "read"
    assert panelkit.prose_dir(out) == 0
    names = sorted(p.name for p in (out / "chapters").glob("*.md"))
    # NN-slug.md, numbered from 01 in reading order, and found below by slug.
    assert [n[:3] for n in names] == [f"{i:02d}-" for i in range(1, len(names) + 1)]
    file = {n[3:-3]: n for n in names}
    body = ["preface", "hows-the-book", "two-shops", "index-cards", "otto"]
    body += ["the-name", "nine-hundred-lines", "em", "the-demo", "full-stop"]
    assert list(file)[: len(body)] == body
    assert sorted(p.name for p in (out / "figures").glob("*")) == ["hedda-screen.png"]
    demo = (out / "chapters" / file["the-demo"]).read_text(encoding="utf-8")
    assert "figures/hedda-screen.png" in demo
    assert "@bind" not in demo and "{#sec-" not in demo
    record = json.loads(panelkit.manifest_path(out).read_text(encoding="utf-8"))
    assert record["parts"][file["hows-the-book"]] == "The Book That Disagreed"
    assert record["parts"][file["preface"]] is None
