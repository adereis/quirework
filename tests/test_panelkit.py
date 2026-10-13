"""Panel-kit pure logic: the rule that turns the book's reading order into the
segments a reader consumes. The render-and-slice path needs Quarto + Typst and is
exercised by hand (`quirework publish packet`); the grouping is all here, on synthetic
rows, because it is the part that decides what a reader is handed."""

import json

import pytest

from quirework.publish.bookstage import Row
from quirework.publish import panelkit


def _book():
    """A book shaped like a real one: unlisted front matter, a listed preface, two
    body parts, and a back-matter part whose chapters are all unnumbered."""
    return [
        Row("chapter", "index.qmd", "Welcome", None, False, None),
        Row("chapter", "copyright.qmd", "Copyright", None, False, None),
        Row("chapter", "preface.qmd", "Preface", None, True, None),
        Row("part", None, "Part One", None, True, "Part One"),
        Row("chapter", "canon/one.qmd", "One", 1, True, "Part One"),
        Row("chapter", "canon/two.qmd", "Two", 2, True, "Part One"),
        Row("part", None, "Part Two", None, True, "Part Two"),
        Row("chapter", "canon/three.qmd", "Three", 3, True, "Part Two"),
        Row("part", None, "Back matter", None, True, "Back matter"),
        Row("chapter", "canon/sources.qmd", "Sources", None, True, "Back matter"),
        Row("chapter", "canon/colophon.qmd", "Colophon", None, True, "Back matter"),
    ]


# Preface 3, Part One 5, One 6, Two 10, Part Two 14, Three 15, Back matter 20,
# Sources 21, Colophon 30 -- the pages the rendered outline would report.
STARTS = [3, 5, 6, 10, 14, 15, 20, 21, 30]
TOTAL = 32


def test_segments_cover_the_book_exactly():
    segs = panelkit.plan_segments(_book(), STARTS, TOTAL)
    assert [(s.start, s.end) for s in segs] == [
        (1, 2),
        (3, 4),
        (5, 9),
        (10, 13),
        (14, 19),
        (20, 32),
    ]
    assert sum(s.pages for s in segs) == TOTAL


def test_front_matter_is_one_block_named_by_its_pages():
    front = panelkit.plan_segments(_book(), STARTS, TOTAL)[0]
    assert (front.kind, front.stem, front.start) == ("front-matter", "front-matter", 1)
    assert front.contains == ["Welcome", "Copyright"]


def test_a_part_divider_rides_on_the_chapter_it_opens():
    segs = panelkit.plan_segments(_book(), STARTS, TOTAL)
    opener = next(s for s in segs if s.title == "One")
    assert opener.opens_part == "Part One"
    assert opener.start == 5  # the divider page, not the chapter's own 6
    assert next(s for s in segs if s.title == "Two").opens_part is None


def test_the_preface_is_its_own_segment_with_no_number():
    preface = panelkit.plan_segments(_book(), STARTS, TOTAL)[1]
    assert (preface.kind, preface.number, preface.part) == ("chapter", None, None)


def test_a_part_with_no_numbered_chapter_collapses_to_one_block():
    back = panelkit.plan_segments(_book(), STARTS, TOTAL)[-1]
    assert (back.kind, back.start, back.end) == ("back-matter", 20, TOTAL)
    assert back.contains == ["Sources", "Colophon"]


def test_chapters_keep_the_number_printed_on_the_page():
    segs = panelkit.plan_segments(_book(), STARTS, TOTAL)
    assert [(s.title, s.number) for s in segs if s.kind == "chapter"] == [
        ("Preface", None),
        ("One", 1),
        ("Two", 2),
        ("Three", 3),
    ]


def test_a_book_with_no_front_matter_starts_at_page_one():
    rows = _book()[2:]
    segs = panelkit.plan_segments(rows, [1, 3, 4, 8, 12, 13, 18, 19, 28], 30)
    assert segs[0].title == "Preface" and segs[0].start == 1


def test_pages_the_template_adds_before_the_book_are_front_matter():
    """Quarto 1.9's Typst book template prints a cover, a title page and its
    Contents before the first entry, none of them the book's own pages."""
    rows = _book()[2:]
    starts = [n + 4 for n in (1, 3, 4, 8, 12, 13, 18, 19, 28)]
    front = panelkit.plan_segments(rows, starts, 34)[0]
    assert (front.kind, front.start, front.end, front.contains) == (
        "front-matter",
        1,
        4,
        [],
    )


def test_an_outline_that_does_not_match_the_book_is_an_error():
    with pytest.raises(ValueError, match="PDF outline"):
        panelkit.plan_segments(_book(), STARTS[:-1], TOTAL)


def test_an_unlisted_page_inside_the_body_is_an_error():
    rows = _book()
    rows.insert(
        5, Row("chapter", "canon/hidden.qmd", "Hidden", None, False, "Part One")
    )
    with pytest.raises(ValueError, match="no segment to sit in: Hidden"):
        panelkit.plan_segments(rows, STARTS, TOTAL)


def test_an_unlisted_page_in_a_matter_part_sits_in_its_block():
    rows = _book()
    rows.insert(
        10, Row("chapter", "canon/thanks.qmd", "Thanks", None, False, "Back matter")
    )
    back = panelkit.plan_segments(rows, STARTS, TOTAL)[-1]
    assert back.contains == ["Sources", "Thanks", "Colophon"]


def _book_with_a_back_cover():
    """The authoring guide's back cover: unnumbered, unlisted, after the last
    chapter and outside every Part, in a book with no back-matter Part."""
    return [r for r in _book() if r.part != "Back matter"] + [
        Row("chapter", "back-cover.qmd", "Back Cover", None, False, None)
    ]


def test_the_body_ends_at_the_last_listed_entry():
    rows = _book_with_a_back_cover()
    head, tail = panelkit.body_span(rows)
    assert (rows[head].title, rows[tail - 1].title) == ("Preface", "Three")
    assert [r.title for r in rows[tail:]] == ["Back Cover"]


def test_unlisted_pages_after_the_body_are_one_back_matter_block():
    segs = panelkit.plan_segments(_book_with_a_back_cover(), STARTS[:6], 20, 19)
    three, back = segs[-2:]
    assert (three.title, three.start, three.end) == ("Three", 14, 18)
    assert (back.kind, back.stem, back.start, back.end) == (
        "back-matter",
        "back-matter",
        19,
        20,
    )
    assert back.contains == ["Back Cover"]


def test_unlisted_pages_after_the_body_need_their_first_page():
    with pytest.raises(ValueError, match="does not record: Back Cover"):
        panelkit.plan_segments(_book_with_a_back_cover(), STARTS[:6], 20)


def test_unlisted_pages_after_a_matter_part_join_its_block():
    rows = _book() + [Row("chapter", "back-cover.qmd", "Back Cover", None, False, None)]
    back = panelkit.plan_segments(rows, STARTS, TOTAL)[-1]
    assert (back.title, back.start, back.end) == ("Back matter", 20, TOTAL)
    assert back.contains == ["Sources", "Colophon", "Back Cover"]


# The book above as `bookstage.book_outline` lists it: every listed entry.
OUTLINE = [
    ("chapter", "Preface"),
    ("part", "Part One"),
    ("chapter", "1 One"),
    ("chapter", "2 Two"),
    ("part", "Part Two"),
    ("chapter", "3 Three"),
    ("part", "Back matter"),
    ("chapter", "Sources"),
    ("chapter", "Colophon"),
]


def test_a_renderer_that_bookmarks_every_part_gives_the_pages_alone():
    marks = [(label, page) for (_, label), page in zip(OUTLINE, STARTS)]
    assert panelkit.entry_pages(OUTLINE, marks, []) == STARTS


def test_parts_without_bookmarks_take_their_pages_from_the_contents():
    """Quarto's Typst book template writes `1. One` and no bookmark for a
    Part, and the kit refused every numbered book. Its Contents page still
    links every entry, Parts included."""
    marks = [
        (label.replace(" ", ". ", 1) if label[0].isdigit() else label, page)
        for (kind, label), page in zip(OUTLINE, STARTS)
        if kind == "chapter"
    ]
    assert panelkit.entry_pages(OUTLINE, marks, STARTS) == STARTS
    with pytest.raises(ValueError, match="cannot place its Parts"):
        panelkit.entry_pages(OUTLINE, marks, [])
    moved = [*STARTS[:2], 7, *STARTS[3:]]  # One's link and bookmark disagree
    with pytest.raises(ValueError, match="'1 One'.*page 7.*page 6"):
        panelkit.entry_pages(OUTLINE, marks, moved)


def test_a_bookmark_out_of_order_is_still_refused():
    marks = [(label, page) for (_, label), page in zip(OUTLINE, STARTS)]
    marks[2], marks[3] = marks[3], marks[2]
    with pytest.raises(ValueError, match="at entry 3: '1 One' vs '2 Two'"):
        panelkit.entry_pages(OUTLINE, marks, [])


def test_manifest_row_carries_the_page_range_and_the_divider():
    segs = panelkit.plan_segments(_book(), STARTS, TOTAL)
    row = panelkit._entry(3, segs[2])
    assert row["file"] == "chapters/03-one.pdf"
    assert row["book_pages"] == [5, 9] and row["pages"] == 5
    assert row["opens_part"] == "Part One" and row["number"] == 1


@pytest.mark.parametrize(
    "title,want",
    [
        ("Back matter", "back-matter"),
        ("Part One: What kettles are", "part-one"),
        ("Nearly there, and not yet", "nearly-there"),
    ],
)
def test_slug_truncates_at_the_first_comma_or_colon(title, want):
    assert panelkit.slug(title) == want


def _fake_kit(tmp_path):
    """A kit directory as `build` leaves one: a manifest and the files it names."""
    kit = tmp_path / "kit"
    (kit / "chapters").mkdir(parents=True)
    rows = [
        {
            "seq": 1,
            "file": "chapters/01-front-matter.pdf",
            "kind": "front-matter",
            "stem": "front-matter",
            "title": "Front matter",
            "number": None,
            "part": None,
            "opens_part": None,
            "pages": 6,
            "book_pages": [1, 6],
        },
        {
            "seq": 2,
            "file": "chapters/02-preface.pdf",
            "kind": "chapter",
            "stem": "preface",
            "title": "Preface",
            "number": None,
            "part": None,
            "opens_part": None,
            "pages": 3,
            "book_pages": [7, 9],
        },
        {
            "seq": 3,
            "file": "chapters/03-one.pdf",
            "kind": "chapter",
            "stem": "one",
            "title": "One",
            "number": 1,
            "part": "Part One",
            "opens_part": "Part One",
            "pages": 5,
            "book_pages": [10, 14],
        },
        {
            "seq": 4,
            "file": "chapters/04-back-matter.pdf",
            "kind": "back-matter",
            "stem": "back-matter",
            "title": "Back matter",
            "number": None,
            "part": None,
            "opens_part": None,
            "pages": 4,
            "book_pages": [15, 18],
        },
    ]
    for row in rows:
        (kit / row["file"]).write_bytes(b"%PDF-1.7\n")
    (kit / "manifest.json").write_text(
        json.dumps(
            {
                "source_sha": "abc1234",
                "dirty": False,
                "book": {"file": "book.pdf", "pages": 18},
                "chapters": rows,
            }
        )
    )
    (kit / "book.pdf").write_bytes(b"%PDF-1.7\n")
    return kit


def test_a_run_dir_is_the_body_renumbered_from_one(tmp_path):
    """A run dir opening at 02 would tell the seat a file was withheld from it,
    and it would break the verbatim turn-1 prompt the blind reads reuse."""
    run = tmp_path / "run"
    assert panelkit.assemble(_fake_kit(tmp_path), run) == 0
    assert sorted(p.name for p in (run / "chapters").iterdir()) == [
        "01-preface.pdf",
        "02-one.pdf",
    ]
    rows = json.loads((run / "manifest.json").read_text())["chapters"]
    assert [(r["seq"], r["file"]) for r in rows] == [
        (1, "chapters/01-preface.pdf"),
        (2, "chapters/02-one.pdf"),
    ]
    assert rows[1]["number"] == 1 and rows[1]["book_pages"] == [10, 14]


def test_a_run_dir_never_carries_the_whole_book(tmp_path):
    run = tmp_path / "run"
    panelkit.assemble(_fake_kit(tmp_path), run)
    assert not (run / "book.pdf").exists()
    assert "book" not in json.loads((run / "manifest.json").read_text())


def test_with_matter_ships_both_blocks(tmp_path):
    run = tmp_path / "run"
    panelkit.assemble(_fake_kit(tmp_path), run, matter=True)
    rows = json.loads((run / "manifest.json").read_text())["chapters"]
    assert [r["kind"] for r in rows] == [
        "front-matter",
        "chapter",
        "chapter",
        "back-matter",
    ]


def test_a_run_dir_is_never_assembled_over_a_run(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "reaction-01.md").write_text("a read in progress")
    assert panelkit.assemble(_fake_kit(tmp_path), run) == 1


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
