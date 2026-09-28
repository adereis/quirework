"""`publish figures inventory`: who uses a shared glyph, counted rather than kept."""

import pytest

from quirework.publish import figures
from quirework.project import Project, project_scope

SVG = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">{}</svg>'


@pytest.fixture
def gallery(tmp_path):
    chapters = tmp_path / "manuscript"
    plates = chapters / "illustrations"
    plates.mkdir(parents=True)
    (chapters / "kettle.qmd").write_text(
        "# Kettle\n\n![A kettle.](illustrations/kettle.svg)\n\n"
        "![A lid.](illustrations/lid.svg)\n"
    )
    (plates / "kettle.svg").write_text(
        SVG.format(
            '<use href="#steam"/><use xlink:href="#steam"/>'
            '<path marker-end="url(#arrow)"/>'
        )
    )
    (plates / "lid.svg").write_text(
        SVG.format(
            "<style>.hot { fill: url(#glow) }</style>"
            '<path style="stroke: black; marker-start: url(#arrow)"/>'
        )
    )
    (plates / "spare.svg").write_text(SVG.format('<use href="#steam"/>'))
    settings = {
        "manuscript": {
            "source": "manuscript",
            "chapters": "manuscript",
        },
        "publish": {
            "figures": "manuscript/illustrations",
        },
    }
    with project_scope(Project.from_settings(tmp_path, settings)):
        yield plates


def test_every_reference_form_is_found(gallery):
    assert figures.references(gallery / "kettle.svg") == [
        ("steam", "use"),
        ("steam", "use"),
        ("arrow", "marker"),
    ]
    assert sorted(figures.references(gallery / "lid.svg")) == [
        ("arrow", "marker"),
        ("glow", "url"),
    ]


def test_one_definition_names_its_figures_and_counts(gallery, capsys):
    assert figures.main(["inventory", "--uses", "#steam"]) == 0
    assert capsys.readouterr().out == (
        "#steam (use): 2 figure(s), 3 reference(s)\n"
        "    2  kettle\n"
        "    1  spare (not embedded)\n"
    )


def test_the_whole_inventory_lists_each_definition(gallery, capsys):
    assert figures.main(["inventory"]) == 0
    out = capsys.readouterr().out
    assert "#arrow (marker): 2 figure(s), 2 reference(s)\n  kettle, lid\n" in out
    assert "#glow (url): 1 figure(s), 1 reference(s)" in out


def test_a_misspelled_id_suggests_the_near_ones(gallery, capsys):
    assert figures.main(["inventory", "--uses", "stem"]) == 0
    assert "no figure references #stem. Close: #steam." in capsys.readouterr().out


def test_a_figure_that_does_not_parse_fails_the_count(gallery, capsys):
    (gallery / "broken.svg").write_text("<svg><use href='#steam'>")
    assert figures.main(["inventory", "--uses", "steam"]) == 1
    captured = capsys.readouterr()
    assert "broken.svg: malformed SVG" in captured.err
    assert "#steam (use): 2 figure(s)" in captured.out


def test_uses_belongs_to_the_inventory(gallery, capsys):
    assert figures.main(["list", "--uses", "steam"]) == 2
