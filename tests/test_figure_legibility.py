"""`book.figure_type_floor_pt`: the printed size under which figure type is reported."""

import pytest

from quirework.book import figures
from quirework.project import Project, project_scope

# A 6in page with 1in inside and 0.85in outside margins leaves a 4.15in text
# block, so one unit of a 960-wide viewBox prints at 0.311pt and 20 units at
# 6.2pt: above the default floor, below a book that letters at 24.
TEMPLATE = (
    "#set page(\n"
    "  width: 6in,\n"
    "  height: 9in,\n"
    "  margin: (inside: 1in, outside: 0.85in, top: 1in, bottom: 1in),\n"
    ")\n"
)
PLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 100">'
    "<style>.t{font-size:20px}</style>"
    '<text class="t" x="10" y="50">tide</text></svg>'
)


def _book(tmp_path, **book):
    plates = tmp_path / "manuscript" / "illustrations"
    plates.mkdir(parents=True)
    (tmp_path / "manuscript" / "shore.qmd").write_text(
        "# Shore\n\n![A tide.](illustrations/tide.svg)\n"
    )
    (plates / "tide.svg").write_text(PLATE)
    (tmp_path / "page.typ").write_text(TEMPLATE)
    settings = {
        "book": {
            "source": "manuscript",
            "chapters": "manuscript",
            "figures": "manuscript/illustrations",
            "typst_template": "page.typ",
            **book,
        }
    }
    return Project.from_settings(tmp_path, settings)


def test_the_default_floor_passes_type_printing_above_six_points(tmp_path):
    with project_scope(_book(tmp_path)):
        assert figures.print_floor_pt() == 6.0
        assert figures.print_legibility({"tide"}) == []


def test_a_configured_floor_reports_type_under_the_size_the_book_chose(tmp_path):
    with project_scope(_book(tmp_path, figure_type_floor_pt=7.4)):
        (line,) = figures.print_legibility({"tide"})
    assert line == "tide: smallest 6.2pt (.t 6.2pt)"


@pytest.mark.parametrize("bad", [0, -1, "7pt", True])
def test_the_floor_must_be_a_positive_number_of_points(tmp_path, bad):
    with project_scope(_book(tmp_path, figure_type_floor_pt=bad)):
        with pytest.raises(SystemExit, match="figure_type_floor_pt"):
            figures.print_legibility({"tide"})
