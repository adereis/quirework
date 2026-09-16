"""Hermetic tests for the cross-format figure-numbering guard (no Quarto).

The guard reads built artifacts, so an end-to-end render can only exercise the
paths a real book happens to take. These build the artifacts by hand, which is
the only way to reach the failure the guard exists for: two editions that each
look fine on their own and disagree with each other. The real render is the
other half of the coverage -- it is what proves the extractors match what the
four writers actually emit.
"""

import zipfile

import pytest

from quirework.book import fignumbers as fn


def _figcaption(number: str, caption: str) -> str:
    return (
        f'<figure class="quarto-float"><figcaption class="quarto-float-fig">'
        f"Figure&nbsp;{number}: {caption}</figcaption></figure>"
    )


def _write_html(site, pages):
    (site / "canon").mkdir(parents=True, exist_ok=True)
    for name, figs in pages.items():
        body = "".join(_figcaption(n, c) for n, c in figs)
        (site / "canon" / f"{name}.html").write_text(
            f"<html><body>{body}</body></html>"
        )


def _write_epub(path, figs):
    with zipfile.ZipFile(path, "w") as z:
        for i, (n, c) in enumerate(figs):
            z.writestr(
                f"EPUB/text/ch{i:03d}.xhtml", f"<body>{_figcaption(n, c)}</body>"
            )


def _write_docx(path, figs):
    paras = "".join(
        f"<w:p><w:r><w:t>Figure {n}: {c}</w:t></w:r></w:p>" for n, c in figs
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "word/document.xml", f"<w:document><w:body>{paras}</w:body></w:document>"
        )


def _stage(tmp_path, embeds_per_chapter):
    """A staged tree whose _quarto.yml and chapters carry `embeds` figure embeds."""
    stage = tmp_path / "stage"
    (stage / "canon").mkdir(parents=True)
    names = [f"c{i}" for i in range(len(embeds_per_chapter))]
    chapters = "\n".join(f"    - canon/{n}.qmd" for n in names)
    (stage / "_quarto.yml").write_text(
        f'project:\n  type: book\nbook:\n  title: "T"\n  chapters:\n{chapters}\n'
    )
    for name, count in zip(names, embeds_per_chapter):
        body = "\n\n".join(
            f"![cap {name} {i}](illustrations/{name}-{i}.svg){{#fig-{name}-{i}}}"
            for i in range(count)
        )
        (stage / "canon" / f"{name}.qmd").write_text(f"# {name}\n\n{body}\n")
    return stage


def test_expected_count_ignores_commented_out_embeds(tmp_path):
    stage = _stage(tmp_path, [2, 1])
    qmd = stage / "canon" / "c0.qmd"
    qmd.write_text(
        qmd.read_text()
        + "\n<!-- ![drafted](illustrations/not-yet.svg){#fig-not-yet} -->\n"
    )
    assert fn.expected_count(stage) == (3, set())


def test_embed_without_a_fig_id_is_counted_as_unnumbered(tmp_path):
    """A plate in an unnumbered chapter: no `#fig-` id, so no writer labels it."""
    stage = _stage(tmp_path, [2, 1])
    qmd = stage / "canon" / "c1.qmd"
    qmd.write_text(
        qmd.read_text() + "\n![a back matter plate](illustrations/plate.svg)\n"
    )
    numbered, unnumbered = fn.expected_count(stage)
    assert numbered == 3
    assert unnumbered == {fn.caption_key("a back matter plate")}


def test_an_edition_that_numbers_an_unnumbered_plate_fails(tmp_path, capsys):
    """The half of property 1 the exemption would otherwise hide."""
    stage = _stage(tmp_path, [1])
    qmd = stage / "canon" / "c0.qmd"
    qmd.write_text(qmd.read_text() + "\n![a back matter plate](illustrations/p.svg)\n")
    site = tmp_path / "_site"
    # HTML leaves it alone; DOCX reaches for the position and labels it.
    _write_html(site, {"c0": [("1.1", "cap c0 0")]})
    _write_docx(site / "b.docx", [("1.1", "cap c0 0"), ("22.1", "a back matter plate")])
    assert fn.verify(site, stage, ["html", "docx"]) == 1
    assert "leaves unnumbered" in capsys.readouterr().out


def test_agreeing_editions_pass(tmp_path, capsys):
    stage = _stage(tmp_path, [2, 1])
    site = tmp_path / "_site"
    figs = [("1.1", "first plate"), ("1.2", "second plate"), ("2.1", "third plate")]
    _write_html(site, {"c0": figs[:2], "c1": figs[2:]})
    _write_epub(site / "b.epub", figs)
    _write_docx(site / "b.docx", figs)

    assert fn.verify(site, stage, ["html", "epub", "docx"]) == 0
    assert (
        "38" not in capsys.readouterr().out
    )  # the count is the book's, not a constant


def test_per_chapter_restart_is_caught_as_duplicate_numbers(tmp_path, capsys):
    """The original defect: each chapter restarts, so "1" names several figures."""
    stage = _stage(tmp_path, [2, 1])
    site = tmp_path / "_site"
    _write_html(
        site,
        {
            "c0": [("1", "first plate"), ("2", "second plate")],
            "c1": [("1", "third plate")],
        },
    )

    assert fn.verify(site, stage, ["html"]) == 1
    out = capsys.readouterr().out
    assert "more than one figure: 1" in out


def test_editions_disagreeing_on_one_figure_is_caught(tmp_path, capsys):
    """Each edition is internally fine; they number the same plate differently."""
    stage = _stage(tmp_path, [2, 1])
    site = tmp_path / "_site"
    good = [("1.1", "first plate"), ("1.2", "second plate"), ("2.1", "third plate")]
    skewed = [("1.1", "first plate"), ("1.2", "second plate"), ("3.1", "third plate")]
    _write_html(site, {"c0": good[:2], "c1": good[2:]})
    _write_epub(site / "b.epub", skewed)

    assert fn.verify(site, stage, ["html", "epub"]) == 1
    out = capsys.readouterr().out
    assert "2.1 in epub" in out or "2.1 in html" in out


def test_missing_figure_is_caught_by_the_count(tmp_path, capsys):
    stage = _stage(tmp_path, [2, 1])
    site = tmp_path / "_site"
    _write_html(site, {"c0": [("1.1", "first plate"), ("1.2", "second plate")]})

    assert fn.verify(site, stage, ["html"]) == 1
    assert "the book numbers 3" in capsys.readouterr().out


def test_caption_key_survives_markup_and_typography():
    """The join key is what lets four writers' captions be compared at all."""
    html = fn.caption_key("The <em>same</em> bar, turned&nbsp;on three minds.")
    pdf = fn.caption_key("The same bar, turned on three\nminds.")
    docx = fn.caption_key("The same bar, turned on three minds.")
    assert html == pdf == docx


def test_unlabelled_caption_is_not_counted_as_a_figure(tmp_path):
    """A `redpen --gray` twin carries a caption and no number; it is not a figure."""
    site = tmp_path / "_site"
    (site / "canon").mkdir(parents=True)
    (site / "canon" / "c0.html").write_text(
        "<figcaption>Figure&nbsp;1.1: real plate</figcaption>"
        "<figcaption>a twin with no number</figcaption>"
    )
    assert fn.from_html(site) == [("1.1", fn.caption_key("real plate"))]


def test_pdf_reader_reports_a_missing_pdftotext(tmp_path, monkeypatch):
    """The check fails loudly rather than skipping the format that broke."""
    monkeypatch.setattr(fn.shutil, "which", lambda _: None)
    with pytest.raises(FileNotFoundError, match="poppler"):
        fn.from_pdf(tmp_path / "b.pdf")


def test_a_caption_wrapped_across_lines_is_still_one_embed():
    """The gallery check and the numbering check must see the same embeds.

    They did not: the numbering matcher could not cross a line break, so a
    wrapped caption passed `figures --check` and then failed the cross-check
    with "the book numbers 0 figures", pointing at the render rather than at
    the caption.
    """
    from quirework.book import figures, fignumbers

    wrapped = (
        "![The sidecar carries the marks, and the bike\n"
        "still runs without it.](illustrations/sidecar.svg)"
        "{#fig-sidecar fig-alt=\"A motorcycle with a sidecar.\"}\n"
    )
    found = list(figures.EMBED.finditer(wrapped))
    assert [m.group("name") for m in found] == ["sidecar"]
    assert fignumbers._FIGID.search(found[0].group("attrs"))
    assert "still runs without it." in found[0].group("cap")
