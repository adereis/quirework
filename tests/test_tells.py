"""The machine-habit advisory: contrast forms, policy phrases, added-only.

The check warns, so its value is that it stays credible. Each contrast form
must fire on its shape and stay quiet on an ordinary negation, and the
added-only rule must report a new habit while ignoring a sentence edited
around one it already had. Every fixture is fiction.
"""

import re
import subprocess

import pytest

from quirework.project import Project, project_scope
from quirework.text import tells

PHRASES = {r"\bit is worth \w+ing\b": "announces", r"^none of this\b": "pivot"}

POLICY = """\
scope_glob = [["manuscript", "*.qmd"]]
exclude_names = ["notes.qmd"]

[phrases]
"\\\\bit is worth \\\\w+ing\\\\b" = "announces attention instead of giving it"
"""


def _rules(text, phrases=PHRASES, contrast=True):
    return [h.rule for h in tells.scan(text, phrases, contrast)[0]]


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def book(tmp_path):
    (tmp_path / "manuscript").mkdir()
    (tmp_path / "tells.toml").write_text(POLICY)
    settings = {"text": {"tells_policy": "tells.toml"}}
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "writer@example.invalid")
    _git(tmp_path, "config", "user.name", "A Writer")
    with project_scope(Project.from_settings(tmp_path, settings)):
        yield tmp_path


def _commit(root, message="draft"):
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


# --------------------------------------------------------------------
# sentences
# --------------------------------------------------------------------


def test_paragraphs_skip_apparatus_and_keep_line_numbers():
    text = (
        "---\ntitle: probe\n---\n"
        "# A heading {#sec-probe}\n"
        "The kettle boils.\n"
        "![A caption.](figures/probe.svg){#fig-probe}\n"
        "<!-- a comment. With sentences. -->\n"
        "The lamp is warm;\n"
        "the room is not. Then it rains.\n"
    )
    flat = [(s.line, s.text) for p in tells.paragraphs(text) for s in p]
    assert flat == [
        (5, "The kettle boils."),
        (8, "The lamp is warm; the room is not."),
        (9, "Then it rains."),
    ]


def test_a_footnote_reference_does_not_hide_a_sentence_end():
    """A reference after a period used to join two sentences, so the first
    one's closing contrast was never at a sentence end to be found."""
    text = (
        "The lamp is lit, not warm.[^oil]\n"
        "It burns oil.[^1] The wick is new.\n"
        "\n"
        "[^oil]: Whale oil, not kerosene.\n"
    )
    flat = [s.text for p in tells.paragraphs(text) for s in p]
    assert flat == [
        "The lamp is lit, not warm.",
        "It burns oil.",
        "The wick is new.",
        "[^oil]: Whale oil, not kerosene.",
    ]
    assert _rules(text) == ["..., not Y.", "..., not Y."]


def test_toml_frontmatter_is_skipped_too():
    flat = [
        s.text for p in tells.paragraphs("+++\ntitle = 'x'\n+++\nA lamp.\n") for s in p
    ]
    assert flat == ["A lamp."]


# --------------------------------------------------------------------
# the contrast forms fire on their shapes, and only there
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,form",
    [
        ("The lamp answers to a timer, not to the room.", "..., not Y."),
        ("It is not the heat, but the hour.", "not X, but Y"),
        ("Not the lamp: the switch behind it.", "Not X: Y"),
        ("A timer is not a clock: it is a countdown.", "Not X: Y"),
        ("The lamp is not warm. It is merely lit.", tells.PAIR),
        ("The lamps weren't broken. They were unplugged.", tells.PAIR),
    ],
)
def test_contrast_forms_are_reported(text, form):
    assert form in _rules(text)


@pytest.mark.parametrize(
    "text",
    [
        "The lamp did not come on. Then the fuse went.",
        "It is not raining.",
        "The lamp is not warm. It is not lit either.",
        "Nothing happened, and the kettle cooled.",
        "The lamp is not warm.\n\nIt is merely lit.\n",  # across a paragraph
    ],
)
def test_ordinary_negation_is_not_a_contrast(text):
    assert not any(h.contrast for h in tells.scan(text, {})[0])


def test_a_sentence_counts_once_toward_the_density():
    hits, n = tells.scan("Not the lamp: the switch, not the fuse.", {})
    assert n == 1 and sum(h.contrast for h in hits) == 1
    assert tells.density(hits, n) == 1000


def test_the_contrast_detectors_can_be_turned_off():
    assert _rules("The lamp answers to a timer, not to the room.", {}, False) == []


def test_phrases_match_case_insensitively_and_anchor_at_the_sentence():
    assert _rules("It is worth asking why.") == [r"\bit is worth \w+ing\b"]
    assert _rules("None of this was planned.") == [r"^none of this\b"]
    assert _rules("They said none of this was planned.") == []


def test_a_phrase_sharing_the_contrast_reason_is_still_a_phrase():
    hits, _ = tells.scan("It is worth asking.", {r"\bworth\b": tells.CONTRAST_WHY})
    assert [h.contrast for h in hits] == [False]


# --------------------------------------------------------------------
# only what is added is reported
# --------------------------------------------------------------------

BEFORE = "The lamp answers to a timer, not to the room.\nThe kettle boils.\n"


def test_a_new_habit_is_reported():
    [hit] = tells.added(BEFORE, BEFORE + "It is worth asking why.\n", PHRASES)
    assert hit.line == 3 and hit.rule == r"\bit is worth \w+ing\b"


def test_an_edit_around_an_existing_habit_is_quiet():
    after = "The lamp answers to its timer, not to the room.\nThe kettle boils.\n"
    assert tells.added(BEFORE, after, PHRASES) == []


def test_a_moved_sentence_is_quiet():
    after = "The kettle boils.\nThe lamp answers to a timer, not to the room.\n"
    assert tells.added(BEFORE, after, PHRASES) == []


def test_a_new_file_reports_everything():
    assert len(tells.added("", BEFORE, PHRASES)) == 1


# --------------------------------------------------------------------
# the project's scope, and a revision range
# --------------------------------------------------------------------


def test_scope_follows_the_policy(book):
    for name in ("b.qmd", "a.qmd", "notes.qmd", "_draft.qmd"):
        (book / "manuscript" / name).write_text("A lamp.\n")
    assert tells.targets() == ["manuscript/a.qmd", "manuscript/b.qmd"]
    assert tells.triggers() == ("manuscript/*.qmd",)


def test_added_since_compares_against_the_revision(book):
    page = book / "manuscript" / "a.qmd"
    page.write_text("The lamp answers to a timer, not to the room.\n")
    _commit(book)
    page.write_text(page.read_text() + "It is worth asking why.\n")
    found = tells.added_since("HEAD")
    assert [(rel, h.line) for rel, h in found] == [("manuscript/a.qmd", 2)]


def test_a_file_new_since_the_revision_adds_all_its_hits(book):
    (book / "manuscript" / "a.qmd").write_text("A lamp.\n")
    _commit(book)
    (book / "manuscript" / "b.qmd").write_text("It is not warm. It is lit.\n")
    assert [rel for rel, _ in tells.added_since("HEAD")] == ["manuscript/b.qmd"]


def test_an_unknown_revision_is_refused_not_read_as_empty(book):
    (book / "manuscript" / "a.qmd").write_text("It is worth asking.\n")
    _commit(book)
    with pytest.raises(ValueError, match="unknown revision"):
        tells.added_since("no-such-rev")


def test_the_command_prints_what_a_range_adds(book, capsys):
    page = book / "manuscript" / "a.qmd"
    page.write_text("A lamp.\n")
    _commit(book)
    page.write_text("A lamp.\nIt is worth asking why.\n")
    assert tells.main(["--since", "HEAD"]) == 0
    out = capsys.readouterr().out
    assert "1 hit(s) added since HEAD" in out and "manuscript/a.qmd:2" in out
    assert tells.main(["--since", "HEAD", "elsewhere.qmd"]) == 2


def test_the_report_names_each_file_and_rule(book, capsys):
    (book / "manuscript" / "a.qmd").write_text("It is not warm. It is lit.\n")
    assert tells.main(["--hits"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^a\s+2\s+1\s+500\s+0$", out, re.M)
    assert tells.PAIR in out and "manuscript/a.qmd:1" in out


def test_an_unconfigured_project_reports_nothing(capsys):
    assert not tells.configured()
    assert tells.main([]) == 0
    assert "no text.tells_policy" in capsys.readouterr().err


def test_the_bundled_template_is_a_working_policy(tmp_path):
    """`config template tells` must be adoptable as written: copied into a
    project, it selects the manuscript and reports a seeded phrase."""
    from importlib.resources import files

    text = files("quirework").joinpath("templates", "tells.toml").read_text()
    (tmp_path / "tells.toml").write_text(text)
    (tmp_path / "manuscript").mkdir()
    (tmp_path / "manuscript" / "one.qmd").write_text(
        "The lamp turns on a timer rather than a switch.\n"
    )
    settings = {"text": {"tells_policy": "tells.toml"}}
    with project_scope(Project.from_settings(tmp_path, settings)):
        [(rel, hits, n)] = tells.measure()
    assert rel == "manuscript/one.qmd" and n == 1
    assert [h.shown for h in hits] == ['"rather than"']


def test_a_long_name_keeps_its_numbers_under_their_headings(book, capsys):
    long = "a-chapter-whose-slug-runs-well-past-thirty-two-columns"
    (book / "manuscript" / f"{long}.qmd").write_text("It is not warm. It is lit.\n")
    (book / "manuscript" / "a.qmd").write_text("A lamp.\n")
    tells.main([])
    rows = capsys.readouterr().out.splitlines()
    ends = {len(row) for row in rows[:3]}
    assert len(ends) == 1, rows[:3]


def _bundles(root):
    """A Hugo site's posts, each a page bundle whose file is index.md."""
    for slug in ("why-the-lamp-turns", "the-fog-signal"):
        page = root / "content" / "posts" / slug / "index.md"
        page.parent.mkdir(parents=True)
        page.write_text("+++\ntitle = 'A post'\n+++\nIt is not warm. It is lit.\n")
    (root / "tells.toml").write_text("contrast = true\n")


def test_a_page_bundle_is_named_by_its_post_s_slug(tmp_path, capsys):
    """Every post's file is index.md, so a row named by the file's stem
    could not say which post it counts."""
    _bundles(tmp_path)
    settings = {
        "manuscript": {
            "format": "pages",
            "source": "content",
            "pages": "posts/*/index.md",
            "unit_name": "post",
        },
        "text": {"tells_policy": "tells.toml"},
    }
    with project_scope(Project.from_settings(tmp_path, settings)):
        tells.main([])
        rows = capsys.readouterr().out.splitlines()
        summary = tells.summary()
    assert [row.split()[0] for row in rows[1:3]] == [
        "the-fog-signal",
        "why-the-lamp-turns",
    ]
    assert "index" not in summary and "why-the-lamp-turns 500" in summary


def test_files_sharing_a_name_are_told_apart_by_path(tmp_path, capsys):
    """Without a manuscript there is no slug to ask for, so two index.md
    files selected by the policy's own scope go by their paths."""
    _bundles(tmp_path)
    (tmp_path / "tells.toml").write_text(
        'scope_glob = [["content/posts", "*/index.md"]]\n'
    )
    settings = {"text": {"tells_policy": "tells.toml"}}
    with project_scope(Project.from_settings(tmp_path, settings)):
        tells.main([])
    rows = capsys.readouterr().out.splitlines()
    assert [row.split()[0] for row in rows[1:3]] == [
        "content/posts/the-fog-signal/index.md",
        "content/posts/why-the-lamp-turns/index.md",
    ]
