"""Odd folios on right-hand pages, read from a PDF's page labels."""

import pytest

from quirework.publish import folios


def test_an_arabic_label_must_match_its_side():
    # Page 1 is a right-hand page, so an odd folio must sit on an odd page.
    assert folios.wrong_sides(["", "ii", "1", "2", "3"]) == []
    assert folios.wrong_sides(["", "", "", "1", "2"]) == [(4, "1"), (5, "2")]


def test_roman_and_empty_labels_are_not_folios():
    assert folios.wrong_sides(["i", "", "iv", "vii"]) == []


def _pdf(path, start_page, pages=6):
    """A blank PDF whose arabic labels begin, at 1, on 1-based `start_page`."""
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=396, height=612)
    writer.set_page_label(0, start_page - 2, style="/r")
    writer.set_page_label(start_page - 1, pages - 1, style="/D", start=1)
    with open(path, "wb") as fh:
        writer.write(fh)


def test_verify_passes_a_body_that_starts_on_a_right_hand_page(tmp_path, capsys):
    _pdf(tmp_path / "book.pdf", start_page=3)
    assert folios.verify(tmp_path) == 0
    assert "all 4 arabic folios on their side" in capsys.readouterr().out


def test_verify_fails_a_body_that_starts_on_a_left_hand_page(tmp_path, capsys):
    _pdf(tmp_path / "book.pdf", start_page=4)
    assert folios.verify(tmp_path) == 1
    err = capsys.readouterr().err
    assert "3 of 3 arabic folios fall on the wrong side" in err
    assert "page 4 is folio 1" in err
