"""The derived-edition builder, on a synthetic book.

Nothing here launches a model. The parts worth testing are the ones that
decide *what a blind drafter is told*: the bundle it reads, the brief it is
given, and the guard that refuses to hand it the project's own marginalia.
"""

import pytest

from quirework.book import variant
from quirework.project import Project, project_scope


def _book(tmp_path):
    """A two-chapter book with one sidecar, in the layout the guide teaches."""
    root = tmp_path / "book"
    sections = root / "manuscript" / "sections"
    (sections / "_marks").mkdir(parents=True)
    (root / "manuscript" / "_quarto.yml").write_text(
        "lang: en-US\n"
        "project:\n  type: book\n"
        "book:\n  title: A Small Notebook\n  chapters:\n"
        "    - index.qmd\n"
        "    - sections/opening.qmd\n"
        "    - sections/closing.qmd\n"
    )
    (root / "manuscript" / "index.qmd").write_text("# A Small Notebook {#sec-index}\n")
    (sections / "opening.qmd").write_text(
        "# The opening {#sec-opening}\n\nA kettle boils.\n"
    )
    (sections / "closing.qmd").write_text(
        "# The closing {#sec-closing}\n\nThe kettle cools.\n"
    )
    (sections / "_marks" / "opening.md").write_text(
        "# The opening {#sec-opening}\n\n"
        "<!-- @bind(kettle-signal) -->\n"
        "<!-- @figure(kettle)  A kettle with steam leaving the spout. "
        "Built: illustrations/kettle.svg -->\n"
    )
    procedures = root / "procedures"
    procedures.mkdir()
    (procedures / "variant-common.md").write_text(
        "# Deriving an edition\n\n## Blind-draft brief\n\nShort sentences. No jokes.\n"
    )
    (procedures / "variant-postcard.md").write_text(
        "# The postcard edition\n\n## Blind-draft brief\n\nA reader in a hurry.\n"
    )
    return root


def _project(root, **overrides):
    settings = {
        "book": {"source": "manuscript", "chapters": "manuscript/sections"},
        "sops": {"root": "procedures"},
    }
    settings.update(overrides)
    return Project.from_settings(root, settings)


def test_the_bundle_is_the_book_in_reading_order(tmp_path):
    root = _book(tmp_path)
    with project_scope(_project(root)):
        chapters = variant.chapter_order()
        assert [c["slug"] for c in chapters] == ["index", "opening", "closing"]
        assert [c["title"] for c in chapters] == [
            "A Small Notebook",
            "The opening",
            "The closing",
        ]
        bundle = variant.build_bundle(chapters)
    assert "A kettle boils." in bundle
    assert "The kettle cools." in bundle


def test_the_reading_order_comes_from_the_configured_file(tmp_path):
    """A book that keeps its Quarto config under another name is still read."""
    root = _book(tmp_path)
    (root / "manuscript" / "_quarto.yml").rename(root / "manuscript" / "book.yml")
    project = _project(root)
    with project_scope(project):
        with pytest.raises(FileNotFoundError):
            variant.chapter_order()
    settings = {
        "book": {
            "source": "manuscript",
            "chapters": "manuscript/sections",
            "quarto": "manuscript/book.yml",
        },
        "sops": {"root": "procedures"},
    }
    with project_scope(Project.from_settings(root, settings)):
        assert [c["slug"] for c in variant.chapter_order()] == [
            "index",
            "opening",
            "closing",
        ]


def test_marks_never_reach_the_drafter(tmp_path):
    root = _book(tmp_path)
    chapter = root / "manuscript" / "sections" / "opening.qmd"
    chapter.write_text(
        "# The opening {#sec-opening}\n\n"
        "<!-- @todo  say what a signal costs -->\n\n"
        "A kettle boils.\n"
    )
    with project_scope(_project(root)):
        bundle = variant.build_bundle(variant.chapter_order())
        variant.assert_blind(bundle)
    assert "@todo" not in bundle
    assert "A kettle boils." in bundle


def test_a_leaked_comment_stops_the_build(tmp_path):
    with pytest.raises(AssertionError, match="HTML comments"):
        variant.assert_blind("A kettle boils.\n<!-- @bind(kettle-signal) -->\n")


def test_a_project_adds_its_own_blindness_tells(tmp_path):
    """The mark syntax is the tool's; a book's private vocabulary is the book's."""
    variant.assert_blind("The kettle is load-bearing.\n")
    with pytest.raises(AssertionError, match="load-bearing"):
        variant.assert_blind("The kettle is load-bearing.\n", ("load-bearing",))


def test_the_brief_is_read_out_of_the_two_procedures(tmp_path):
    root = _book(tmp_path)
    with project_scope(_project(root)):
        voice, reader = variant.read_brief("postcard")
    assert voice == "Short sentences. No jokes."
    assert reader == "A reader in a hurry."


def test_a_procedure_is_found_in_any_layer_the_project_uses(tmp_path):
    """Shared fragments belong under protocols/, and the catalog files them there.

    Reading only the procedure root meant a project that followed its own
    documented layout got "no SOP" from a command standing next to the file.
    """
    root = _book(tmp_path)
    protocols = root / "procedures" / "protocols"
    protocols.mkdir()
    (root / "procedures" / "variant-common.md").rename(protocols / "variant-common.md")
    workflows = root / "procedures" / "workflows"
    workflows.mkdir()
    (root / "procedures" / "variant-postcard.md").rename(
        workflows / "variant-postcard.md"
    )
    with project_scope(_project(root)):
        assert variant.kinds() == ["postcard"]
        assert variant.procedure("variant-common") == protocols / "variant-common.md"
        voice, reader = variant.read_brief("postcard")
    assert voice == "Short sentences. No jokes."
    assert reader == "A reader in a hurry."


def test_a_kind_without_a_procedure_says_which_file_is_missing(tmp_path):
    root = _book(tmp_path)
    with project_scope(_project(root)):
        with pytest.raises(ValueError, match="variant-telegram.md"):
            variant.read_brief("telegram")


def test_figures_come_from_the_sidecar_without_operator_metadata(tmp_path):
    root = _book(tmp_path)
    with project_scope(_project(root)):
        figures = variant.figures_for(root / "manuscript" / "sections" / "opening.qmd")
    assert figures == [
        {"name": "kettle", "desc": "A kettle with steam leaving the spout"}
    ]


def test_the_prompt_carries_the_book_the_task_the_reader_and_the_voice(tmp_path):
    root = _book(tmp_path)
    with project_scope(_project(root)):
        chapters = variant.chapter_order()
        bundle = variant.build_bundle(chapters)
        voice, reader = variant.read_brief("postcard")
        prompt = variant.assemble_prompt(
            "postcard", bundle, chapters[1], reader, voice, []
        )
    assert "POSTCARD edition" in prompt
    assert "A kettle boils." in prompt
    assert 'Rewrite the chapter titled "The opening"' in prompt
    assert "A reader in a hurry." in prompt
    assert "Short sentences. No jokes." in prompt
    assert "No illustration is assigned" in prompt
