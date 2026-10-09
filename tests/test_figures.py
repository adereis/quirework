"""The figure pipeline: embeds, sources, the staged gallery and its PNGs.

Each case builds its own manuscript and gallery; nothing reads a real book.
"""

from pathlib import Path

import pytest

from conftest import patch_setting
from quirework.project import Project, project_scope
from quirework.publish import figures
from quirework.publish import graybuild as tool_gray

WELLFORMED = "<svg xmlns='http://www.w3.org/2000/svg'></svg>"
# Two arbitrary colors for the gray set to neutralize.
_AMBER, _BRICK = "#d98a34", "#b0503f"


@pytest.fixture(autouse=True)
def quarto_book(tmp_path):
    """A book whose chapters and figure gallery are where `_setup` puts them."""
    settings = {
        "manuscript": {"source": "docs", "chapters": "docs/canon"},
        "publish": {"figures": "docs/canon/illustrations", "build": "build"},
    }
    with project_scope(Project.from_settings(tmp_path, settings)):
        yield


def _setup(tmp_path, monkeypatch, name="doubling"):
    canonical = tmp_path / "docs" / "canon"
    illus = canonical / "illustrations"
    illus.mkdir(parents=True)
    build_illus = tmp_path / "build" / "book" / "canon" / "illustrations"
    build_illus.mkdir(parents=True)
    (illus / f"{name}.svg").write_text(WELLFORMED)
    (canonical / "ch.qmd").write_text(
        f"# C\n\n![caption](illustrations/{name}.svg){{#fig-x}}\n"
    )
    patch_setting(monkeypatch, figures, "_settings", "CANONICAL", canonical)
    patch_setting(monkeypatch, figures, "_settings", "ILLUSTRATIONS", illus)
    patch_setting(monkeypatch, figures, "_settings", "BUILD_ILLUSTRATIONS", build_illus)
    return canonical, illus, build_illus


def _no_gallery(tmp_path, chapter):
    """A book that configures no figure directory, as one without plates does."""
    canonical = tmp_path / "plain" / "docs" / "canon"
    canonical.mkdir(parents=True)
    (canonical / "ch.qmd").write_text(chapter)
    settings = {
        "manuscript": {"source": "docs", "chapters": "docs/canon"},
        "publish": {"build": "build"},
    }
    return Project.from_settings(tmp_path / "plain", settings)


def test_a_book_without_figures_builds_with_no_gallery(tmp_path):
    with project_scope(_no_gallery(tmp_path, "# C\n\nNo plates here.\n")):
        assert figures.build(stage=tmp_path / "stage") == 0
        assert figures.sources() == []
        assert figures.inventory() == 0
        from quirework.publish import fonts

        assert fonts._stacks_in_figures() == []


def test_an_embed_without_a_gallery_names_the_figure(tmp_path):
    from quirework.project import ConfigurationError

    chapter = "# C\n\n![caption](illustrations/kettle.svg){#fig-kettle}\n"
    with project_scope(_no_gallery(tmp_path, chapter)):
        with pytest.raises(ConfigurationError, match="'kettle'"):
            figures.build(stage=tmp_path / "stage")


def test_gallery_default_lives_under_build_not_docs():
    """The materialized gallery is a build artifact: its default home is under
    build/ (the render workdir), never the docs/ source tree."""
    assert (
        figures._settings().BUILD_BOOK
        in figures._settings().BUILD_ILLUSTRATIONS.parents
    )
    assert "docs" not in figures._settings().BUILD_ILLUSTRATIONS.parts


def test_embedded_names_scans_embeds(tmp_path, monkeypatch):
    canonical, _, _ = _setup(tmp_path, monkeypatch)
    assert figures.embedded_names(canonical) == {"doubling"}


def test_embedded_names_ignores_image_lines_inside_comments(tmp_path, monkeypatch):
    """An image line quoted inside an @-annotation is not an embed.

    A @figure mark for an unbuilt figure drafts its own caption and alt text,
    and HTML comments do not render, so the consistency gate must not demand a
    source for one -- otherwise specifying a figure before building it blocks
    every commit.
    """
    canonical, _, _ = _setup(tmp_path, monkeypatch)
    (canonical / "ch.qmd").write_text(
        "# C\n\n"
        "<!-- @figure(planned) NOT BUILT. Paste-ready embed: "
        '![cap](illustrations/planned.svg){#fig-planned fig-alt="..."} -->\n\n'
        "![caption](illustrations/doubling.svg){#fig-x}\n"
    )
    assert figures.embedded_names(canonical) == {"doubling"}


def test_resolve_default(tmp_path, monkeypatch):
    _, illus, _ = _setup(tmp_path, monkeypatch)
    assert figures.resolve("doubling") == illus / "doubling.svg"


def test_build_materializes_color_and_prunes_orphans(tmp_path, monkeypatch):
    _, _, build_illus = _setup(tmp_path, monkeypatch)
    (build_illus / "stale.svg").write_text(WELLFORMED)
    assert figures.build() == 0
    assert (build_illus / "doubling.svg").exists()
    assert not (build_illus / "stale.svg").exists()


def test_check_ok(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    assert figures.check() == 0
    assert "OK" in capsys.readouterr().out


def test_check_flags_malformed_source(tmp_path, monkeypatch):
    _, illus, _ = _setup(tmp_path, monkeypatch)
    (illus / "doubling.svg").write_text("<svg>unclosed")
    assert figures.check() == 1


def test_check_flags_dangling_embed(tmp_path, monkeypatch):
    canonical, _, _ = _setup(tmp_path, monkeypatch)
    (canonical / "ch2.qmd").write_text("# C2\n\n![c](illustrations/missing.svg)\n")
    assert figures.check() == 1


def test_non_gray_hexes_flags_only_color():
    """Only a chromatic color is reported; neutral grays and white are not."""
    text = f"<svg><rect fill='{_AMBER}'/><rect fill='#808080'/><rect fill='#FFFFFF'/></svg>"
    assert tool_gray.non_gray_hexes(text) == {_AMBER}


def test_generate_gray_writes_all_gray_svgs(tmp_path, monkeypatch):
    _, illus, _ = _setup(tmp_path, monkeypatch)
    (illus / "doubling.svg").write_text(
        f"<svg xmlns='http://www.w3.org/2000/svg'><rect fill='{_AMBER}'/></svg>"
    )
    out = tmp_path / "gen"
    assert figures.generate_gray({"doubling"}, out) == 1
    assert tool_gray.non_gray_hexes((out / "doubling.svg").read_text()) == set()


def test_build_gray_true_generates_into_stage(tmp_path, monkeypatch):
    _, illus, _ = _setup(tmp_path, monkeypatch)
    (illus / "doubling.svg").write_text(
        f"<svg xmlns='http://www.w3.org/2000/svg'><rect fill='{_BRICK}'/></svg>"
    )
    stage = tmp_path / "build" / "book-noparts"
    assert figures.build(stage=stage, gray=True) == 0
    assert (stage / "canon" / "illustrations" / "doubling.svg").exists()  # color
    assert (
        stage / "canon" / "illustrations-gray" / "doubling.svg"
    ).exists()  # generated gray


def test_build_gray_false_skips_gray(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    stage = tmp_path / "build" / "book"
    assert figures.build(stage=stage, gray=False) == 0
    assert not (stage / "canon" / "illustrations-gray").exists()


def test_print_legibility_flags_a_screen_scale_label(tmp_path, monkeypatch):
    """Only the class under the floor is named, not every class in the plate."""
    template = tmp_path / "page.typ"
    # A 4.15in text block: 13 units of a 960-wide viewBox print at 4pt, under
    # the default floor, and 40 units at 12.5pt, well over it.
    template.write_text(
        "#set page(\n  width: 6in,\n  margin: (inside: 1in, outside: 0.85in),\n)\n"
    )
    patch_setting(monkeypatch, figures, "_settings", "_TEMPLATE", template)
    svg = tmp_path / "tiny.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 400">'
        "<style>.t-tiny{font-size:13px;}.t-big{font-size:40px;}</style>"
        "</svg>"
    )
    monkeypatch.setattr(figures, "resolve", lambda name: svg)
    hits = figures.print_legibility({"tiny"})
    assert len(hits) == 1
    assert ".t-tiny" in hits[0] and ".t-big" not in hits[0]


def test_png_rebuilds_the_color_set_before_rasterizing(tmp_path, monkeypatch):
    """An edited SVG must reach the PNG, even when the staged gallery is populated.

    `png` used to rebuild only when build/book/canon/illustrations was EMPTY, so
    once a render (or an earlier png) had populated it, editing a figure and
    re-rendering gave back the previous plate. `figures --check` cannot catch
    that: it validates the source and the gray transform, never a left-over
    artifact. The bug cost a figure review that passed on a stale label.
    """
    _, illus, build_illus = _setup(tmp_path, monkeypatch)
    (build_illus / "doubling.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'>STALE</svg>"
    )
    (illus / "doubling.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'>FRESH</svg>"
    )

    rasterized: list[str] = []
    monkeypatch.setattr(figures.shutil, "which", lambda _: "/usr/bin/rsvg-convert")
    monkeypatch.setattr(
        figures.subprocess,
        "run",
        lambda argv, **kw: rasterized.append(Path(argv[3]).read_text()),
    )

    assert figures.render_png(None, tmp_path / "out") == 0
    assert rasterized == ["<svg xmlns='http://www.w3.org/2000/svg'>FRESH</svg>"]


def test_png_aborts_when_a_figure_source_is_missing(tmp_path, monkeypatch):
    """A short set is worse than no set: rasterizing 44 of 45 looks like success.

    The old empty-dir call discarded build()'s return code.
    """
    canonical, illus, _ = _setup(tmp_path, monkeypatch)
    (illus / "doubling.svg").unlink()
    monkeypatch.setattr(figures.shutil, "which", lambda _: "/usr/bin/rsvg-convert")
    monkeypatch.setattr(
        figures.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("rasterized anyway")),
    )
    assert figures.render_png(None, tmp_path / "out") == 1


def test_png_set_path_rasterizes_that_directory_as_is(tmp_path, monkeypatch):
    """--set PATH is the escape hatch and stays literal: no rebuild, no pruning."""
    _setup(tmp_path, monkeypatch)
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "hand-made.svg").write_text(WELLFORMED)
    seen: list[str] = []
    monkeypatch.setattr(figures.shutil, "which", lambda _: "/usr/bin/rsvg-convert")
    monkeypatch.setattr(
        figures.subprocess, "run", lambda argv, **kw: seen.append(Path(argv[3]).name)
    )
    assert figures.render_png(str(loose), tmp_path / "out") == 0
    assert seen == ["hand-made.svg"]
