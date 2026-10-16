"""A copy on a Kindle: what to send, where it goes, and its notes brought back.

The book, its copies and the reader's devices are fictional. The store is
never reached: what a browser tab would report is written here as the
snapshot the script returns, and the notebook the Kindle app exports is
written by hand, malformed where the app's own export is.
"""

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from quirework.project import Project, project_scope
from quirework.provenance import show
from quirework.publish import kindle

_QUARTO = """\
project:
  type: book
book:
  title: "A Kettle Book"
  chapters:
    - index.qmd
    - canon/boiling.qmd
    - canon/cooling.qmd
"""

_BOILING = """\
# Boiling {#sec-boiling}

<!-- @guidance: keep the whistle plain
     and short. -->
A kettle boils.
The whistle is *a signal*, not a thought.
It reports a state.

Steam escapes through the spout[^1].

[^1]: The spout is the only way out.
"""

_COOLING = """\
# Cooling {#sec-cooling}

A kettle cools by another route.
"""

_ANDROID = {"kind": "Kindle for Android", "family": "KINDLE_APP"}
_DEVICES = [
    {
        "name": "Ada's 2nd Android Device",
        **_ANDROID,
        "registered": "2026-01-04T18:05:17.170Z",
    },
    {
        "name": "Ada's Android Device",
        **_ANDROID,
        "registered": "2024-05-25T02:27:52.004Z",
    },
    {
        "name": "Ada's Paperwhite",
        "kind": "Kindle Paperwhite",
        "family": "KINDLE",
        "registered": "2018-10-17T21:21:46.090Z",
    },
]


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _epub(path, title, creator="Ada Kettle"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        z.writestr(
            "EPUB/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:title>{title}</dc:title><dc:creator>{creator}</dc:creator>"
            "</metadata></package>",
        )
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def book(tmp_path, monkeypatch):
    """A fictional book under git at `book/`, its log of copies outside it."""
    root = tmp_path / "book"
    canon = root / "docs" / "canon"
    canon.mkdir(parents=True)
    empty = tmp_path / "gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Ada Kettle")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "ada@example.org")
    (root / "docs" / "_quarto.yml").write_text(_QUARTO)
    (root / "docs" / "index.qmd").write_text("# Welcome\n")
    (canon / "boiling.qmd").write_text(_BOILING)
    (canon / "cooling.qmd").write_text(_COOLING)
    (root / ".gitignore").write_text("build/\n")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "A fictional first draft")

    home = tmp_path / "xdg"
    (home / "quirework").mkdir(parents=True)
    log = tmp_path / "logs" / "copies.jsonl"
    (home / "quirework" / "config.toml").write_text(
        f'[copies]\nlog = "{log}"\n\n[kindle]\n'
        'targets = [{ kind = "Kindle for Android" }, { kind = "Kindle Paperwhite" }]\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    project = Project.from_settings(
        root,
        {
            "manuscript": {
                "source": "docs",
                "chapters": "docs/canon",
                "quarto": "docs/_quarto.yml",
            },
            "provenance": {"redpen": "private/redpen"},
        },
    )
    with project_scope(project):
        yield root


def _issue(root, number, title, stem=None):
    """Log copy `number` as `publish copy` would, its EPUB in build/copies."""
    stem = stem or f"a-kettle-book_PROOF_copy-{number:02d}"
    path = root / "build" / "copies" / f"{stem}.epub"
    digest = _epub(path, title)
    log = kindle._proof._log_path()
    commit = _git(root, "rev-parse", "HEAD")
    kindle._proof._append(
        log,
        {
            "copy": number,
            "event": "started",
            "recipient": "Ada Kettle",
            "commit": commit,
        },
    )
    kindle._proof._append(
        log,
        {
            "copy": number,
            "event": "issued",
            "files": [
                {"name": path.name, "sha256": digest, "bytes": path.stat().st_size}
            ],
        },
    )
    return path


def _log():
    return [json.loads(l) for l in kindle._proof._log_path().read_text().splitlines()]


def _snapshot(tmp_path, library, devices=_DEVICES):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"version": 1, "devices": devices, "library": library}))
    return path


_SIX = "A Kettle Book (uncorrected proof, copy 6)"
_FIVE = "A Kettle Book (uncorrected proof, copy 5)"


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #
def test_the_plan_sends_the_newest_copy_under_its_own_title(book):
    _issue(book, 5, _FIVE)
    path = _issue(book, 6, _SIX)
    made = kindle.plan()
    assert made["copy"] == 6
    assert made["file"] == str(path)
    assert made["title"] == _SIX and made["author"] == "Ada Kettle"
    assert made["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert made["at_head"] is True
    assert made["targets"][0] == {"kind": "Kindle for Android"}


def test_a_copy_whose_bytes_changed_is_not_sent(book):
    path = _issue(book, 6, _SIX)
    path.write_bytes(path.read_bytes() + b"\0")
    with pytest.raises(ValueError, match="never changed after it is made"):
        kindle.plan()


def test_a_copy_made_in_another_checkout_is_sent_from_there(book):
    _issue(book, 6, _SIX).unlink()
    with pytest.raises(ValueError, match="another checkout"):
        kindle.plan(6)


def test_the_newest_registration_of_a_kind_is_the_one_chosen(book, tmp_path):
    _issue(book, 6, _SIX)
    made = kindle.plan(snapshot=_snapshot(tmp_path, []))
    assert made["deliver_to"] == [
        {"name": "Ada's 2nd Android Device", "kind": "Kindle for Android"},
        {"name": "Ada's Paperwhite", "kind": "Kindle Paperwhite"},
    ]
    assert made["send"] is True
    assert made["deliver"] == made["deliver_to"]


def test_a_rule_can_take_every_match_or_one_device_by_name():
    every = kindle.resolve([{"kind": "Kindle for Android", "pick": "all"}], _DEVICES)
    assert [d["name"] for d in every] == [
        "Ada's 2nd Android Device",
        "Ada's Android Device",
    ]
    one = kindle.resolve(
        [{"name": "Ada's Paperwhite"}, {"kind": "Kindle Paperwhite"}], _DEVICES
    )
    assert one == [{"name": "Ada's Paperwhite", "kind": "Kindle Paperwhite"}]


def test_a_rule_that_matches_no_device_stops_the_plan():
    with pytest.raises(
        ValueError,
        match="no reading device in the library matches kind 'Kindle Scribe'",
    ):
        kindle.resolve([{"kind": "Kindle Scribe"}], _DEVICES)


@pytest.mark.parametrize(
    ("rules", "says"),
    [
        ("[]", "must be a list of rules"),
        ('[{ pick = "all" }]', "names a kind or a name"),
        ('[{ kind = "Kindle Paperwhite", colour = "white" }]', "no field 'colour'"),
        (
            '[{ kind = "Kindle Paperwhite", pick = "oldest" }]',
            'pick is "latest" or "all"',
        ),
    ],
)
def test_device_rules_are_checked_before_any_is_used(book, tmp_path, rules, says):
    config = tmp_path / "xdg" / "quirework" / "config.toml"
    text = config.read_text().split("[kindle]")[0]
    config.write_text(text + f"[kindle]\ntargets = {rules}\n")
    with pytest.raises(ValueError, match=says):
        kindle.targets()


def test_without_device_rules_the_plan_says_where_to_set_them(book, tmp_path):
    config = tmp_path / "xdg" / "quirework" / "config.toml"
    config.write_text(config.read_text().split("[kindle]")[0])
    _issue(book, 6, _SIX)
    with pytest.raises(ValueError, match="set kindle.targets in"):
        kindle.plan()


def test_an_earlier_copy_keeps_its_devices_until_its_notes_are_dealt_with(
    book, tmp_path
):
    _issue(book, 5, _FIVE, stem="a-kettle-book_PROOF_copy-05")
    _issue(book, 6, _SIX)
    on = [{"name": "Ada's Paperwhite", "kind": "Kindle Paperwhite"}]
    snapshot = _snapshot(
        tmp_path,
        [
            # Sent by hand before this loop existed, so titled by its file name.
            {
                "title": "a-kettle-book_PROOF_copy-05",
                "added": "October 13, 2026",
                "devices": on,
            },
            # The store's file-name look-alikes for a title's parentheses.
            {"title": "A Kettle Book ﹙uncorrected proof, copy 6﹚", "devices": on},
            {"title": "A Recipe Card", "devices": []},
        ],
    )
    made = kindle.plan(snapshot=snapshot)
    assert made["send"] is False and made["on"] == on
    assert made["deliver"] == [
        {"name": "Ada's 2nd Android Device", "kind": "Kindle for Android"}
    ]
    assert made["earlier"] == [
        {
            "copy": 5,
            "title": "a-kettle-book_PROOF_copy-05",
            "on": on,
            "notes": "missing",
            "may_leave_devices": False,
        }
    ]
    assert made["unrecognized"] == ["A Recipe Card"]
    kindle.record("notes-waived", 5, reason="test highlights only")
    assert kindle.plan(snapshot=snapshot)["earlier"][0]["may_leave_devices"] is True


def test_a_delivery_already_logged_is_not_asked_for_again(book, tmp_path):
    _issue(book, 6, _SIX)
    kindle.record("delivered", 6, ["Ada's Paperwhite"])
    made = kindle.plan(snapshot=_snapshot(tmp_path, [{"title": _SIX, "devices": []}]))
    assert made["deliver"] == [
        {"name": "Ada's 2nd Android Device", "kind": "Kindle for Android"}
    ]


# --------------------------------------------------------------------------- #
# record
# --------------------------------------------------------------------------- #
def test_each_step_is_logged_beside_the_copy(book):
    _issue(book, 6, _SIX)
    kindle.record("sent", 6)
    kindle.record("delivered", 6, ["Ada's Paperwhite"])
    sent, delivered = _log()[-2:]
    assert sent["copy"] == 6 and sent["event"] == "kindle-sent"
    assert sent["title"] == _SIX and len(sent["sha256"]) == 64
    assert delivered["devices"] == ["Ada's Paperwhite"]


def test_a_title_logged_as_sent_still_names_the_copy_once_its_file_is_gone(
    book, tmp_path
):
    path = _issue(book, 5, _FIVE)
    _issue(book, 6, _SIX)
    kindle.record("sent", 5)
    path.unlink()
    made = kindle.plan(
        6, snapshot=_snapshot(tmp_path, [{"title": _FIVE, "devices": []}])
    )
    assert [e["copy"] for e in made["earlier"]] == [5]


@pytest.mark.parametrize(
    ("args", "says"),
    [
        (("deleted", 6), "record one of"),
        (("sent", 9), "holds no copy 9"),
        (("removed", 6), "name the devices"),
    ],
)
def test_a_step_that_did_not_happen_cannot_be_logged(book, args, says):
    _issue(book, 6, _SIX)
    with pytest.raises(ValueError, match=says):
        kindle.record(*args)


# --------------------------------------------------------------------------- #
# reading the notebook the app exports
# --------------------------------------------------------------------------- #
# Shaped like the app's own export: every entry on lines of its own, a
# page and no location for a personal document, a note listed before a
# short highlight and after a long one, and markup that does not close
# what it opens.
_EXPORT = """\
<?xml version="1.0" encoding="UTF-8" ?>
<html xmlns="http://www.w3.org/TR/1999/REC-html-in-xml" xml:lang="en" lang="en">
<head><meta charset="UTF-8"/><title></title></head>
<body><div class="bodyContainer">
<div class="notebookFor">
Notebook for
</div>
<div class="bookTitle">
{title}
</div>
<div class="authors">
Unknown
</div>
<div class="citation">
{citation}
</div>
<hr />
<div class="sectionHeading">
Front Matter
</div>
<div class="noteHeading">
Note -  Page 2
</div>
<div class="noteText">
a note on the title
</div>
<div class="noteHeading">
Highlight (<span class="annotation_yellow">yellow</span>) -  Page 2
</div>
<div class="noteText">
Kettle
</div>
<div class="sectionHeading">
1 Boiling
</div>
<div class="noteHeading">
Highlight (<span class="annotation_green">green</span>) -  Page 5
</div>
<div class="noteText">
The whistle is a signal, not a thought. It reports a state.</h3>
<div class="noteHeading">
Note -  Page 5
</div>
<div class="noteText">
Say what it reports.
</div>
<div class="noteHeading">
Highlight (<span class="annotation_aqua">aqua</span>) -  Page 6
</div>
<div class="noteText">
spout
</div>
<div class="noteHeading">
Note -  Page 6
</div>
<div class="noteText">
which of the two?
</div>
<div class="noteHeading">
Highlight (<span class="annotation_aqua">aqua</span>) -  Page 6
</div>
<div class="noteText">
the only way out
</div>
<div class="noteHeading">
Note -  Page 7
</div>
<div class="noteText">
A margin thought,<br/>on two lines.
</div>
<div class="noteHeading">
Bookmark -  Page 7
</div>
<div class="noteText">
</div>
<div class="sectionHeading">
2 Cooling
</div>
<div class="noteHeading">
Highlight (<span class="annotation_pink">pink</span>) - Location 30
</div>
<div class="noteText">
A kettle cools by a route nobody wrote.
</div>
</div></body></html>
"""


def _export(tmp_path, title=_SIX, citation="", name="Notebook.html"):
    path = tmp_path / name
    path.write_text(
        _EXPORT.replace("{title}", title).replace("{citation}", citation),
        encoding="utf-8",
    )
    return path


def test_a_notebook_is_read_entry_by_entry_in_the_book_s_order(tmp_path):
    book = kindle.parse(_export(tmp_path))
    assert book.title == _SIX and book.authors == "Unknown"
    kinds = [m.kind for m in book.marks]
    assert kinds == [
        "note",
        "highlight",
        "highlight",
        "note",
        "highlight",
        "note",
        "highlight",
        "note",
        "bookmark",
        "highlight",
    ]
    first = book.marks[2]
    assert (first.color, first.page, first.location, first.section) == (
        "green",
        "5",
        None,
        "1 Boiling",
    )
    assert first.text == "The whistle is a signal, not a thought. It reports a state."
    assert book.marks[7].text == "A margin thought,\non two lines."
    last = book.marks[-1]
    assert (last.section, last.color, last.location, last.page) == (
        "2 Cooling",
        "pink",
        "30",
        None,
    )


def test_a_note_is_shown_beside_the_highlight_at_its_place_before_or_after(tmp_path):
    marks = kindle.parse(_export(tmp_path)).marks
    title_note, kettle, whistle, reports, spout, which, way_out, margin = marks[:8]
    # Listed before a short highlight, as the app lists it.
    assert title_note.beside == [kettle]
    # Listed after a long one.
    assert reports.beside == [whistle]
    # Between two highlights at its place, the export cannot say which.
    assert which.beside == [spout, way_out]
    # Alone on its page.
    assert margin.beside == []


def test_the_citation_the_app_adds_changes_no_mark(tmp_path):
    plain = kindle.parse(_export(tmp_path))
    cited = kindle.parse(
        _export(
            tmp_path,
            citation="Citation (APA): Unknown. (2026). <i>A book</i>.",
            name="Cited.html",
        )
    )
    assert kindle.marks_digest(plain) == kindle.marks_digest(cited)


def test_a_file_that_is_no_notebook_is_refused(tmp_path):
    path = tmp_path / "letter.html"
    path.write_text("<html><body><p>Dear Ada</p></body></html>")
    with pytest.raises(ValueError, match="not a notebook exported by a Kindle app"):
        kindle.parse(path)


def test_an_entry_the_reader_cannot_read_stops_it(tmp_path):
    path = _export(tmp_path)
    path.write_text(
        path.read_text().replace("Bookmark -  Page 7", "Lesezeichen - Seite 7")
    )
    with pytest.raises(ValueError, match="an entry this cannot read"):
        kindle.parse(path)


# --------------------------------------------------------------------------- #
# finding a highlight in the manuscript
# --------------------------------------------------------------------------- #
def _files(*pairs):
    return kindle._index(list(pairs))


def test_a_highlight_across_two_lines_names_both_through_the_markup():
    index = _files(("docs/canon/boiling.qmd", _BOILING))
    assert kindle.locate(
        "The whistle is a signal, not a thought. It reports a state.", index
    ) == ("docs/canon/boiling.qmd:6-7")
    assert (
        kindle.locate("Steam escapes through the spout.", index)
        == "docs/canon/boiling.qmd:9"
    )


def test_the_book_s_own_marks_are_not_its_words():
    index = _files(("docs/canon/boiling.qmd", _BOILING))
    assert kindle.locate("keep the whistle plain and short", index) is None


def test_a_footnote_number_swept_into_a_highlight_is_not_part_of_it():
    index = _files(("docs/canon/boiling.qmd", _BOILING))
    assert (
        kindle.locate("not a thought. It reports a state.1", index)
        == "docs/canon/boiling.qmd:6-7"
    )
    assert (
        kindle.locate("Steam escapes through the spout.1", index)
        == "docs/canon/boiling.qmd:9"
    )


def test_a_long_highlight_whose_middle_differs_is_found_near():
    lines = ["A kettle boils."] * 3 + [
        "The whistle is a signal, not a thought.",
        "It reports a state.",
    ]
    index = _files(("docs/canon/boiling.qmd", "\n".join(lines)))
    text = (
        "A kettle boils. " * 3
        + "The whistle (see figure 2) is a signal, not a thought. It reports a state."
    )
    assert kindle.locate(text, index) == "near docs/canon/boiling.qmd:1-5"


def test_a_sentence_two_chapters_share_is_found_in_the_section_s_own():
    index = _files(
        ("docs/canon/boiling.qmd", _BOILING),
        (
            "docs/canon/cooling.qmd",
            _COOLING + "The whistle is a signal, not a thought.\n",
        ),
    )
    text = "The whistle is a signal, not a thought."
    assert kindle.locate(text, index) == "docs/canon/boiling.qmd:6"
    assert kindle.locate(text, index, "2 Cooling") == "docs/canon/cooling.qmd:4"


def test_a_word_or_two_is_looked_for_in_its_chapter_alone():
    index = _files(
        ("docs/canon/boiling.qmd", _BOILING), ("docs/canon/cooling.qmd", _COOLING)
    )
    assert kindle.locate("whistle", index, "1 Boiling") == "docs/canon/boiling.qmd:6"
    assert kindle.locate("spout", index, "1 Boiling") == (
        "one of 2 places in docs/canon/boiling.qmd: lines 9, 11"
    )
    assert kindle.locate("kettle", index, "Front Matter") is None
    assert kindle.locate("spout", index, "2 Cooling") is None


def test_a_figure_s_caption_is_found_and_its_label_is_explained(book):
    canon = book / "docs" / "canon"
    (canon / "cooling.qmd").write_text(
        _COOLING + "\n![The kettle at rest](illustrations/rest.svg){#fig-rest}\n"
    )
    marks = [
        kindle.Mark("highlight", "", "2 Cooling", text="The kettle at rest"),
        kindle.Mark("highlight", "", "2 Cooling", text="Figure"),
        kindle.Mark("highlight", "", "Front Matter", text="Kettle"),
    ]
    kindle.place(marks, canon, None)
    assert marks[0].source == "docs/canon/cooling.qmd:5"
    assert (
        marks[1].source
        == "unlocated (a word or two its chapter does not contain, such as a figure's label)"
    )
    assert (
        marks[2].source
        == "unlocated (a word or two, in a section that names no chapter)"
    )


def test_text_revised_since_the_copy_is_found_in_the_copy_s_commit(book):
    commit = _git(book, "rev-parse", "HEAD")
    canon = book / "docs" / "canon"
    (canon / "cooling.qmd").write_text(
        "# Cooling {#sec-cooling}\n\nA kettle cools slowly.\n"
    )
    _git(book, "commit", "-qam", "A fictional revision")
    marks = [
        kindle.Mark("highlight", "", "", text="A kettle cools by another route."),
        kindle.Mark("highlight", "", "", text="A kettle cools slowly."),
        kindle.Mark("highlight", "", "", text="Nobody ever wrote this sentence."),
    ]
    kindle.place(marks, canon, commit)
    assert (
        marks[0].source
        == f"docs/canon/cooling.qmd:3 in copy's commit {commit[:8]}, changed since"
    )
    assert marks[1].source == "docs/canon/cooling.qmd:3"
    assert marks[2].source == "unlocated"


# --------------------------------------------------------------------------- #
# the archive
# --------------------------------------------------------------------------- #
def test_the_notes_are_archived_as_the_reader_s_marks(book, tmp_path, capsys):
    _issue(book, 6, _SIX)
    export = _export(tmp_path)
    assert kindle.notes(export, 6, dated="2026-10-20") == 0
    archive = book / "private" / "redpen" / "kindle-copy-06-2026-10-20.md"
    text = archive.read_text()
    assert "copy-commit:    " + _git(book, "rev-parse", "HEAD") in text
    assert "notes:          4 (3 beside a highlight)" in text
    assert (
        "anchors:        exact 2 / near 0 / several places 1 / changed since 0 / unlocated 2"
        in text
    )
    assert "### 1 Boiling" in text and "### Front Matter" in text
    assert (
        archive.with_suffix(".raw") / "Notebook.html"
    ).read_bytes() == export.read_bytes()
    # A reader of red-pen rounds takes each mark for one of the reader's turns.
    assert show.is_redpen_round(archive)
    turns = show.redpen_marks(archive)
    assert len(turns) == 10
    note, day = turns[3]
    assert str(day) == "2026-10-20"
    assert "note: Say what it reports." in note
    assert "-> beside the highlight “The whistle is a signal, not a thought." in note
    assert "(docs/canon/boiling.qmd:6-7)" in note
    assert "-> between “spout”" in turns[5][0]
    assert "-> beside no highlight; find it at page 7" in turns[7][0]
    assert "A margin thought,\n    on two lines." in turns[7][0]
    event = _log()[-1]
    assert event["event"] == "kindle-notes" and event["marks"] == 10
    assert event["archive"] == str(archive) and len(event["marks_sha256"]) == 64
    assert "archived copy 6's notes" in capsys.readouterr().out


def test_notes_on_another_copy_are_never_archived_under_this_one(book, tmp_path):
    _issue(book, 5, _FIVE)
    _issue(book, 6, _SIX)
    with pytest.raises(ValueError, match="which is not copy 5"):
        kindle.notes(_export(tmp_path), 5, dated="2026-10-20")


def test_an_export_whose_marks_are_archived_is_not_archived_again(book, tmp_path):
    _issue(book, 6, _SIX)
    kindle.notes(_export(tmp_path), 6, dated="2026-10-20")
    with pytest.raises(ValueError, match="is already archived"):
        kindle.notes(_export(tmp_path), 6, dated="2026-10-21")
    cited = _export(tmp_path, citation="Citation (APA): A book.", name="Cited.html")
    with pytest.raises(ValueError, match="has the same marks as"):
        kindle.notes(cited, 6, dated="2026-10-21")


def test_an_archive_is_never_overwritten(book, tmp_path):
    _issue(book, 6, _SIX)
    kindle.notes(_export(tmp_path), 6, dated="2026-10-20")
    later = _export(tmp_path, name="Later.html")
    later.write_text(
        later.read_text().replace("Say what it reports.", "Say it plainly.")
    )
    with pytest.raises(ValueError, match="never overwritten"):
        kindle.notes(later, 6, dated="2026-10-20")


def test_archived_notes_let_the_copy_leave_the_devices(book, tmp_path):
    _issue(book, 5, _FIVE)
    _issue(book, 6, _SIX)
    kindle.notes(_export(tmp_path, _FIVE), 5, dated="2026-10-20")
    made = kindle.plan(
        6, snapshot=_snapshot(tmp_path, [{"title": _FIVE, "devices": []}])
    )
    assert made["earlier"][0]["notes"] == "kept"
    assert made["earlier"][0]["may_leave_devices"] is True


# --------------------------------------------------------------------------- #
# the inbox
# --------------------------------------------------------------------------- #
@pytest.fixture
def drive(book, tmp_path, monkeypatch):
    """A shared folder that, like Google Drive, holds two files of one name."""
    config = tmp_path / "xdg" / "quirework" / "config.toml"
    config.write_text(
        config.read_text().replace(
            "[kindle]\n", '[kindle]\ninbox = "drive:Notebooks"\n'
        )
    )
    files = {}

    def rclone(args):
        if args[0] == "lsjson":
            assert args[1:] == ["--files-only", "drive:Notebooks"]
            return json.dumps(
                [{"ID": i, "Name": n, "ModTime": t} for i, (n, t, _) in files.items()]
            )
        if args[0] == "backend":
            assert args[1:3] == ["copyid", "drive:"]
            name, _, body = files[args[3]]
            target = Path(args[4]) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(kindle, "_rclone", rclone)
    return files


def test_the_inbox_fetches_each_export_by_its_id_and_names_its_copy(
    book, tmp_path, drive
):
    _issue(book, 6, _SIX)
    plain = _export(tmp_path).read_bytes()
    cited = _export(
        tmp_path, citation="Citation (APA): A book.", name="c.html"
    ).read_bytes()
    drive["b"] = ("A Book - Notebook.html", "2026-10-16T16:29:35Z", cited)
    drive["a"] = ("A Book - Notebook.html", "2026-10-16T16:27:12Z", plain)
    drive["c"] = ("Screenshot.png", "2026-10-16T16:30:30Z", b"\x89PNG")
    seen = kindle.inbox(tmp_path / "fetched")
    first, second = seen["exports"]
    assert Path(first["file"]).read_bytes() == plain
    assert Path(second["file"]).read_bytes() == cited
    assert first["copy"] == second["copy"] == 6 and first["archived"] is None
    assert second["same_marks_as"] == first["file"]
    assert seen["ignored"] == ["Screenshot.png"]
    kindle.notes(Path(first["file"]), 6, dated="2026-10-16")
    again = kindle.inbox(tmp_path / "again")
    assert all(row["archived"] for row in again["exports"])


def test_the_inbox_fetches_into_a_new_directory_only(book, tmp_path, drive):
    (tmp_path / "used").mkdir()
    (tmp_path / "used" / "old.html").write_text("")
    with pytest.raises(ValueError, match="is not empty"):
        kindle.inbox(tmp_path / "used")


def test_without_an_inbox_the_command_says_where_to_name_one(book, tmp_path):
    with pytest.raises(ValueError, match="set kindle.inbox in"):
        kindle.inbox(tmp_path / "fetched")


# --------------------------------------------------------------------------- #
# the command line
# --------------------------------------------------------------------------- #
def test_help_names_the_rules_and_that_nothing_deletes(capsys):
    assert kindle.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "kindle.targets" in out and "No command deletes a document" in out


def test_the_driver_ships_beside_the_command(capsys):
    assert kindle.main(["driver"]) == 0
    path = Path(capsys.readouterr().out.strip())
    assert path.name == "kindle.js" and "window.qwKindle" in path.read_text()


@pytest.mark.parametrize(
    ("argv", "says"),
    [
        (["record", "sent"], "record names the copy"),
        (["notes", "missing.html", "--copy", "6"], "no such file"),
        (["plan", "--copy", "six"], "a copy's number"),
        (["plan", "--fast"], "unknown option --fast"),
        (["inbox"], "fetches into a new directory"),
        (["forget"], "unknown command"),
    ],
)
def test_a_command_asked_for_wrongly_is_refused(book, capsys, argv, says):
    assert kindle.main(argv) == 2
    assert says in capsys.readouterr().err
