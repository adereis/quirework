"""`publish.figure_fonts`: a staged figure carries the faces it letters in."""

import base64
import io
import re

import pytest

pytest.importorskip("fontTools")
pytest.importorskip("brotli")

from fontTools.fontBuilder import FontBuilder  # noqa: E402
from fontTools.pens.ttGlyphPen import TTGlyphPen  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402

from quirework.publish import figfonts, figures  # noqa: E402
from quirework.project import Project, project_scope  # noqa: E402

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40">'
    "<style>text{{font-family:Tidewater,'Other Face',serif}}</style>"
    "{}</svg>"
)


def _box():
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((0, 500))
    pen.lineTo((500, 500))
    pen.closePath()
    return pen.glyph()


def make_font(path, style, weight, italic, chars="abcdeilnorstw"):
    glyphs = [".notdef", "space"] + [f"uni{ord(c):04X}" for c in chars]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(glyphs)
    fb.setupCharacterMap({32: "space", **{ord(c): f"uni{ord(c):04X}" for c in chars}})
    fb.setupGlyf(
        {
            g: (TTGlyphPen(None).glyph() if g in (".notdef", "space") else _box())
            for g in glyphs
        }
    )
    fb.setupHorizontalMetrics({g: (600, 0) for g in glyphs})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Tidewater", "styleName": style})
    fb.setupOS2(usWeightClass=weight, fsSelection=1 if italic else 0x40)
    fb.setupPost()
    fb.font["head"].macStyle = 2 if italic else 0
    fb.save(path)


@pytest.fixture
def book(tmp_path):
    chapters = tmp_path / "manuscript"
    plates = chapters / "illustrations"
    plates.mkdir(parents=True)
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    make_font(fonts / "Tidewater-Regular.ttf", "Regular", 400, False)
    make_font(fonts / "Tidewater-Bold.ttf", "Bold", 700, False)
    make_font(fonts / "Tidewater-Italic.ttf", "Italic", 400, True)
    (chapters / "shore.qmd").write_text(
        "# Shore\n\n![A tide.](illustrations/tide.svg)\n"
    )
    (plates / "tide.svg").write_text(
        SVG.format('<text x="1" y="20">tide</text><text font-weight="700">sea</text>')
    )
    settings = {
        "manuscript": {"source": "manuscript", "chapters": "manuscript"},
        "publish": {
            "figures": "manuscript/illustrations",
            "figure_fonts": [
                "fonts/Tidewater-Regular.ttf",
                "fonts/Tidewater-Bold.ttf",
                "fonts/Tidewater-Italic.ttf",
            ],
        },
    }
    figfonts._describe.cache_clear()
    with project_scope(Project.from_settings(tmp_path, settings)):
        yield tmp_path


def _faces(svg):
    return re.findall(
        r"font-style:(\w+);font-weight:(\d+);src:url\(data:font/woff2;base64,([^)]+)\)",
        svg,
    )


def test_a_staged_figure_carries_the_faces_it_asks_for(book):
    source = (book / "manuscript/illustrations/tide.svg").read_text()
    assert figures.build(stage=book / "stage") == 0
    staged = next((book / "stage").rglob("tide.svg")).read_text()

    faces = _faces(staged)
    assert [(s, w) for s, w, _ in faces] == [("normal", "400"), ("normal", "700")]
    font = TTFont(io.BytesIO(base64.b64decode(faces[0][2])))
    assert set(font.getBestCmap()) == {ord(c) for c in " tidesa"}
    # The tracked source is never the thing that changes, and the staged copy
    # differs from it by the one inserted style block.
    assert (book / "manuscript/illustrations/tide.svg").read_text() == source
    assert re.sub(r"<style>@font-face.*?</style>", "", staged) == source


def test_an_italic_face_travels_only_with_italic_text(book):
    plate = book / "manuscript/illustrations/tide.svg"
    plate.write_text(SVG.format('<text font-style="italic">wet</text>'))
    assert figures.build(stage=book / "stage") == 0
    staged = next((book / "stage").rglob("tide.svg")).read_text()
    assert [s for s, _, _ in _faces(staged)] == ["normal", "italic"]


def test_the_gray_gallery_carries_them_too(book):
    assert figures.build(stage=book / "stage", gray=True) == 0
    gray = next((book / "stage").rglob("illustrations-gray/tide.svg")).read_text()
    assert len(_faces(gray)) == 2


def test_a_figure_in_another_family_carries_nothing(book):
    plate = book / "manuscript/illustrations/tide.svg"
    other = SVG.replace("Tidewater,", "").format("<text>tide</text>")
    plate.write_text(other)
    assert figures.build(stage=book / "stage") == 0
    assert next((book / "stage").rglob("tide.svg")).read_text() == other


def test_a_character_the_face_cannot_draw_fails_the_check(book, capsys):
    plate = book / "manuscript/illustrations/tide.svg"
    plate.write_text(SVG.format("<text>high tide?</text>"))
    assert figures.check() == 1
    err = capsys.readouterr().err
    assert "tide: Tidewater-Regular.ttf lacks '?gh'" in err
    assert figures.build(stage=book / "stage") == 1


def test_unset_means_a_plain_copy(tmp_path):
    chapters = tmp_path / "manuscript"
    plates = chapters / "illustrations"
    plates.mkdir(parents=True)
    (chapters / "shore.qmd").write_text("![A tide.](illustrations/tide.svg)\n")
    source = SVG.format("<text>tide</text>")
    (plates / "tide.svg").write_text(source)
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
        assert figures.build(stage=tmp_path / "stage") == 0
    assert next((tmp_path / "stage").rglob("tide.svg")).read_text() == source


def test_family_lists_are_read_in_every_spelling():
    assert figfonts._families(
        "<text font-family=\"Tidewater,'Other Face',serif\"/>"
        "<text style=\"font-family: 'Second';fill:red\"/>"
        "<style>.a{font-family:Third , serif}</style>"
    ) == {"tidewater", "other face", "serif", "second", "third"}
