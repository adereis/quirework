"""Hermetic tests for the three-stage render wiring (no real repo tree, no Quarto).

The book renders from separate screen, print and parts-less staged trees.
Print omits screen-only front matter; EPUB/DOCX flatten the parts -- otherwise
Quarto silently drops the file-based Part Two opener. These cover the format
routing and the `_quarto.yml` transforms; the flatten/unnumber themselves are
covered in test_bookstage, and the end-to-end render by running it for real.
"""

from conftest import patch_setting
from quirework.runs import commands as run_commands
import json
import shutil
import subprocess
import zipfile

import pytest

from quirework.project import Project, project_scope
from quirework.publish import render


def project_at(root):
    """A Quarto book laid out as the configuration guide teaches, at `root`.

    Its render policy keeps the landing page out of print and strips the one
    EPUB style include, which is what the staged trees below are checked for.
    """
    policy = root / "render.toml"
    if not policy.exists():
        root.mkdir(parents=True, exist_ok=True)
        policy.write_text(
            'print_drops = ["index.qmd"]\nepub_style_headers = ["epub-styles.html"]\n'
        )
    return Project.from_settings(
        root,
        {
            "manuscript": {
                "source": "docs",
                "chapters": "docs/canon",
                "quarto": "docs/_quarto.yml",
            },
            "publish": {
                "render_policy": "render.toml",
                "figures": "docs/canon/illustrations",
            },
        },
    )


@pytest.fixture(autouse=True)
def quarto_book(tmp_path):
    """Every module the render reaches sees the same book, not only `render`."""
    with project_scope(project_at(tmp_path)):
        yield


_QUARTO = """\
project:
  type: book
book:
  title: "T"
  chapters:
    - index.qmd
    - part: "Part One"
      chapters:
        - canon/01.qmd
    - part: canon/part-two.qmd
      chapters:
        - canon/08.qmd

format:
  html:
    theme: cosmo
  # a comment inside the format block rides along
  typst:
    toc: false
    template-partials:
      - typst-show.typ
  epub:
    toc: true
    filters:
      - _gray-illustrations.lua
  docx: default
"""


def _quarto_dir(tmp_path):
    (tmp_path / "_quarto.yml").write_text(_QUARTO, encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------- #
# split_to: pulling `--to` out of the pass-through render args
# --------------------------------------------------------------------------- #
def test_split_to_none_when_absent():
    assert render.split_to([]) == (None, [])
    assert render.split_to(["--cache-refresh"]) == (None, ["--cache-refresh"])


def test_split_to_space_and_equals_forms():
    assert render.split_to(["--to", "epub"]) == (["epub"], [])
    assert render.split_to(["--to=html,epub", "--cache-refresh"]) == (
        ["html", "epub"],
        ["--cache-refresh"],
    )


def test_split_to_consumes_every_to_and_unions():
    """A second --to used to ride through to Quarto, which rendered it inside a
    stage whose config had been trimmed to the first format only — the second
    format silently lost its declared options (the EPUB its gray-figure
    filter). All --to flags are consumed and their formats unioned."""
    req, rest = render.split_to(["-v", "--to", "docx", "--to", "epub"])
    assert req == ["docx", "epub"]
    assert rest == ["-v"]
    req, rest = render.split_to(["--to=docx", "--to", "docx,epub"])
    assert req == ["docx", "epub"]  # de-duped, in order
    assert rest == []


# --------------------------------------------------------------------------- #
# partition_formats: screen vs print vs parts-less
# --------------------------------------------------------------------------- #
def test_partition_explicit_request():
    assert render.partition_formats(["html", "epub", "docx"]) == (
        ["html"],
        [],
        ["epub", "docx"],
    )
    assert render.partition_formats(["typst"]) == ([], ["typst"], [])
    # print is its own stage because its front matter differs from the screen's
    assert render.partition_formats(["html", "typst"]) == (["html"], ["typst"], [])


def test_partition_unknown_routes_parts_safe():
    # partition is pure routing; an undeclared name is main()'s to reject
    # (test_main_rejects_an_undeclared_format below)
    assert render.partition_formats(["pdff"]) == (["pdff"], [], [])


def test_partition_none_reads_quarto(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "current_project", lambda: project_at(tmp_path))
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "_quarto.yml").write_text(_QUARTO, encoding="utf-8")
    assert render.book_formats() == ["html", "typst", "epub", "docx"]
    assert render.partition_formats(None) == (["html"], ["typst"], ["epub", "docx"])


# --------------------------------------------------------------------------- #
# restrict_formats: trim the staged format block to one stage's formats
# --------------------------------------------------------------------------- #
def _format_keys(text):
    import re

    keys, i, lines, ci = [], 0, text.splitlines(), None
    while i < len(lines) and lines[i].rstrip() != "format:":
        i += 1
    i += 1
    while i < len(lines):
        s = lines[i].strip()
        ind = len(lines[i]) - len(lines[i].lstrip())
        i += 1
        if not s or s.startswith("#"):
            continue
        if ind == 0:
            break
        if ci is None:
            ci = ind
        if ind != ci:
            continue
        m = re.match(r"([A-Za-z][\w-]*)\s*:", s)
        if m:
            keys.append(m.group(1))
    return keys


def test_restrict_to_parts_safe_drops_epub_docx_and_filter(tmp_path):
    d = _quarto_dir(tmp_path)
    render.restrict_formats(d, ["html", "typst"])
    text = (d / "_quarto.yml").read_text()
    assert _format_keys(text) == ["html", "typst"]
    assert "- _gray-illustrations.lua" not in text  # epub's filter directive gone
    assert "- typst-show.typ" in text  # typst's nested block kept
    assert (
        "book:" in text and "part: canon/part-two.qmd" in text
    )  # other blocks untouched


def test_restrict_to_parts_less_keeps_epub_filter(tmp_path):
    d = _quarto_dir(tmp_path)
    render.restrict_formats(d, ["epub", "docx"])
    text = (d / "_quarto.yml").read_text()
    assert _format_keys(text) == ["epub", "docx"]
    assert "- _gray-illustrations.lua" in text  # the EPUB gray-swap filter survives


def test_restrict_to_single_format(tmp_path):
    d = _quarto_dir(tmp_path)
    render.restrict_formats(d, ["epub"])
    assert _format_keys((d / "_quarto.yml").read_text()) == ["epub"]


# --------------------------------------------------------------------------- #
# stage_book_noparts: composes mirror + figures + flatten + unnumber
# --------------------------------------------------------------------------- #
def test_stage_book_noparts_flattens_and_unnumbers(tmp_path, monkeypatch):
    # a minimal docs/ tree with a file-based part
    docs = tmp_path / "docs"
    (docs / "canon").mkdir(parents=True)
    (docs / "_quarto.yml").write_text(_QUARTO, encoding="utf-8")
    (docs / "index.qmd").write_text("# Welcome\n")
    (docs / "canon" / "01.qmd").write_text("# One {#sec-one}\n")
    (docs / "canon" / "08.qmd").write_text("# Weight {#sec-weight}\n")
    (docs / "canon" / "part-two.qmd").write_text(
        "# Part Two: What we pay them {#sec-part-two}\n\nOpener prose.\n"
    )
    # The epub include-in-header, which this stage reduces to its rules. It is
    # required, not optional: a rename must break the render rather than
    # silently ship the commentary again, so the fixture carries a real one.
    (docs / "epub-styles.html").write_text(
        "<!-- banner -->\n<style>/* why */\np { margin: 0; }\n</style>\n",
        encoding="utf-8",
    )

    stage = tmp_path / "build" / "book-noparts"
    monkeypatch.setattr(render, "current_project", lambda: project_at(tmp_path))
    patch_setting(monkeypatch, render, "_settings", "BUILD_BOOK_NOPARTS", stage)
    monkeypatch.setattr(
        render._figures, "build", lambda stage=None, gray=False: 0
    )  # skip figure I/O

    assert render.stage_book_noparts() == 0
    cfg = (stage / "_quarto.yml").read_text()
    assert "part:" not in cfg  # every part flattened
    assert "- canon/part-two.qmd" in cfg  # opener promoted, in place
    h1 = (stage / "canon" / "part-two.qmd").read_text().splitlines()[0]
    assert ".unnumbered" in h1  # numbering preserved
    styles = (stage / "epub-styles.html").read_text()
    assert "banner" not in styles and "why" not in styles  # comments never ship
    assert "p { margin: 0; }" in styles  # the rules do


# --------------------------------------------------------------------------- #
# main(): the orchestration owns validation the stages cannot do
# --------------------------------------------------------------------------- #
def test_main_help_exits_zero(capsys):
    assert render.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "usage: quirework publish build" in out and "quarto render" in out


def test_main_rejects_an_undeclared_format(monkeypatch, capsys):
    """restrict_formats silently drops names the config does not declare, so a
    typo must error up front instead of rendering nothing it promised."""
    monkeypatch.setattr(render, "find_quarto", lambda: "quarto")
    monkeypatch.setattr(
        render, "book_formats", lambda: ["html", "typst", "epub", "docx"]
    )
    rc = render.main(["--to", "pdfx"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown format" in err and "pdfx" in err and "typst" in err


@pytest.fixture
def render_run(tmp_path, monkeypatch):
    """Real staging, consolidation and numbering checks; synthetic writer output."""
    from quirework.publish import epubcheck
    from quirework.publish import fonts

    docs = tmp_path / "docs"
    (docs / "canon").mkdir(parents=True)
    (docs / "_quarto.yml").write_text(_QUARTO)
    (docs / "index.qmd").write_text("# Welcome\n")
    (docs / "epub-styles.html").write_text("<style>p { margin: 0; }</style>\n")
    (docs / "canon" / "part-two.qmd").write_text("# Part Two\n\nAn opener.\n")
    for name, caption in (("01", "first plate"), ("08", "second plate")):
        (docs / "canon" / f"{name}.qmd").write_text(
            f"# Chapter {name}\n\n"
            f"![{caption}](illustrations/{name}.svg){{#fig-{name}}}\n"
        )

    monkeypatch.setattr(render, "current_project", lambda: project_at(tmp_path))
    for attr, folder in (
        ("BUILD_BOOK", "book"),
        ("BUILD_BOOK_PRINT", "book-print"),
        ("BUILD_BOOK_NOPARTS", "book-noparts"),
    ):
        patch_setting(
            monkeypatch,
            render,
            "_options" if attr == "BUILD_BOOK" else "_settings",
            attr,
            tmp_path / "build" / folder,
        )
    monkeypatch.setattr(render, "find_quarto", lambda: "quarto")
    # `missing` takes the formats about to be rendered: only the formats
    # typeset or rasterized here depend on this machine's fonts.
    monkeypatch.setattr(fonts, "missing", lambda formats=None: [])
    monkeypatch.setattr(render._figures, "build", lambda **kwargs: 0)
    monkeypatch.setattr(epubcheck, "verify", lambda site: 0)
    # PDF extraction is external I/O. Other artifact readers and the actual
    # expected-count/numbering guard remain real, including its failure paths.
    monkeypatch.setitem(
        render._fignumbers._READERS,
        "typst",
        (".pdf", lambda path: json.loads(path.read_text())),
    )

    def run(formats, *, stale_html=False, incomplete=False, disagree=False):
        if stale_html:
            old = render._options().BUILD_BOOK
            old.mkdir(parents=True)
            (old / "_quarto.yml").write_text("book:\n  chapters:\n    - old.qmd\n")
            (old / "old.qmd").write_text(
                "# Old\n\n![old plate](illustrations/old.svg){#fig-old}\n"
            )
            (old / "_site").mkdir()
            (old / "_site" / "old.html").write_text(
                "<figcaption>Figure 9.1: stale plate</figcaption>"
            )
        calls = []

        def quarto(args, *, cwd):
            calls.append(cwd)
            site = cwd / "_site"
            site.mkdir(exist_ok=True)
            # Quarto refreshes a stage's output directory when it renders it.
            for path in site.iterdir():
                path.unlink()
            for fmt in _format_keys((cwd / "_quarto.yml").read_text()):
                pairs = [("1.1", "first plate"), ("2.1", "second plate")]
                if incomplete:
                    pairs.pop()
                if disagree and fmt == "epub":
                    pairs[0] = ("3.1", "first plate")
                captions = "".join(
                    f"<figcaption>Figure {n}: {cap}</figcaption>" for n, cap in pairs
                )
                if fmt == "html":
                    (site / "book.html").write_text(captions)
                elif fmt == "typst":
                    (site / "book.pdf").write_text(
                        json.dumps(
                            [
                                (n, render._fignumbers.caption_key(cap))
                                for n, cap in pairs
                            ]
                        )
                    )
                elif fmt == "epub":
                    with zipfile.ZipFile(site / "book.epub", "w") as archive:
                        archive.writestr("EPUB/chapter.xhtml", captions)
                elif fmt == "docx":
                    with zipfile.ZipFile(site / "book.docx", "w") as archive:
                        archive.writestr(
                            "word/document.xml",
                            "".join(
                                f"<w:p><w:r><w:t>Figure {n}: {cap}</w:t></w:r></w:p>"
                                for n, cap in pairs
                            ),
                        )
                else:
                    raise AssertionError(fmt)
            return 0

        monkeypatch.setattr(render.subprocess, "call", quarto)
        return render.main(["--to", formats]), calls

    return run


@pytest.mark.parametrize(
    "formats",
    [
        "typst",
        "epub",
        "docx",
        "html",
        "typst,epub",
        "epub,docx",
        "html,typst,epub,docx",
    ],
)
@pytest.mark.parametrize("stale_html", [False, True], ids=["fresh", "stale-html"])
def test_render_validates_only_requested_stages(render_run, formats, stale_html):
    rc, calls = render_run(formats, stale_html=stale_html)
    assert rc == 0
    selected = set(formats.split(","))
    expected = []
    if "html" in selected:
        expected.append(render._options().BUILD_BOOK)
    if "typst" in selected:
        expected.append(render._settings().BUILD_BOOK_PRINT)
    if selected & {"epub", "docx"}:
        expected.append(render._settings().BUILD_BOOK_NOPARTS)
    assert calls == expected
    if "html" not in selected:
        config = render._options().BUILD_BOOK / "_quarto.yml"
        assert config.exists() == stale_html
        if stale_html:
            assert "old.qmd" in config.read_text()
            assert (render._options().BUILD_BOOK / "_site" / "old.html").exists()


@pytest.mark.parametrize("formats", ["typst", "epub", "docx"])
def test_stale_html_cannot_hide_a_missing_figure(render_run, formats, capsys):
    rc, _ = render_run(formats, stale_html=True, incomplete=True)
    assert rc == 1
    assert "1 numbered figure(s), but the book numbers 2" in capsys.readouterr().out


def test_non_html_editions_still_have_to_agree(render_run, capsys):
    rc, _ = render_run("typst,epub", disagree=True)
    assert rc == 1
    assert "figure numbered 3.1 in epub but 1.1 in typst" in capsys.readouterr().out


def test_mirror_sources_stages_book_without_touching_source(tmp_path):
    """`render.mirror_sources` mirrors docs/ into the build workdir: it drops the
    figure gallery (a build artifact), preserves Quarto's caches, prunes stale
    staged sources, and never writes back into the source tree."""
    src = tmp_path / "docs"
    (src / "canon" / "illustrations").mkdir(parents=True)
    (src / "_quarto.yml").write_text("project:\n  type: book\n")
    (src / "canon" / "ch.qmd").write_text("# C\n")
    (src / "canon" / "illustrations" / "fig.svg").write_text("<svg/>")
    # Agent-guidance / repo-scaffolding files live in docs/ but are not book sources.
    (src / "AGENTS.md").write_text("# agent guidance\n")
    (src / "CLAUDE.md").write_text("@AGENTS.md\n")
    (src / "GEMINI.md").write_text("@AGENTS.md\n")
    (src / ".gitignore").write_text("/_site/\n")

    stage = tmp_path / "build" / "book"
    # Pre-existing staged state: a Quarto cache to preserve and a stale chapter to prune.
    (stage / "_site").mkdir(parents=True)
    (stage / "_site" / "old.html").write_text("stale output")
    (stage / "canon").mkdir(parents=True)
    (stage / "canon" / "gone.qmd").write_text("# removed chapter\n")

    render.mirror_sources(src, stage)

    assert (stage / "_quarto.yml").exists()
    assert (stage / "canon" / "ch.qmd").exists()
    assert not (stage / "canon" / "gone.qmd").exists()  # stale source pruned
    assert not (stage / "canon" / "illustrations").exists()  # gallery not mirrored
    assert not (stage / "AGENTS.md").exists()  # agent guidance not staged
    assert not (stage / "CLAUDE.md").exists()  # agent guidance not staged
    assert not (stage / "GEMINI.md").exists()  # agent guidance not staged
    assert not (stage / ".gitignore").exists()  # repo scaffolding not staged
    assert (stage / "_site" / "old.html").exists()  # Quarto cache preserved
    # The source tree is untouched: no build artifacts leak back into docs/.
    assert not (src / "_site").exists()
    assert sorted(p.name for p in (src / "canon").iterdir()) == [
        "ch.qmd",
        "illustrations",
    ]


# --------------------------------------------------------------------------- #
# stage_book_reader: a reader instrument's copy is the book as published
# --------------------------------------------------------------------------- #
_DRAFT = """\
project:
  type: book
draft-copy: true
book:
  title: "T"
  chapters:
    - index.qmd
"""


def _draft_book(tmp_path, monkeypatch):
    """A book that reserves its rights until publication, and whose render
    policy names what its published state sets."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "_quarto.yml").write_text(_DRAFT, encoding="utf-8")
    (docs / "index.qmd").write_text("# Welcome\n")
    (tmp_path / "render.toml").write_text(
        '[reader_metadata]\ndraft-copy = false\nedition = "First"\n'
    )
    monkeypatch.setattr(render, "current_project", lambda: project_at(tmp_path))
    monkeypatch.setattr(render._figures, "build", lambda stage=None, gray=False: 0)
    return docs


def test_a_reader_stage_renders_the_book_as_published(tmp_path, monkeypatch, capsys):
    _draft_book(tmp_path, monkeypatch)
    stage = tmp_path / "build" / "panel-stage"
    assert render.stage_book_reader(stage) == 0
    lines = (stage / "_quarto.yml").read_text().splitlines()
    assert "draft-copy: false" in lines and "draft-copy: true" not in lines
    assert lines[-1] == 'edition: "First"'
    assert "sets draft-copy, edition as published" in capsys.readouterr().out


def test_a_copy_for_a_person_keeps_the_book_s_own_metadata(tmp_path, monkeypatch):
    """The other half: `publish build` stages without the reader's metadata,
    and nothing a reader stage does reaches the sources or the publish stage."""
    docs = _draft_book(tmp_path, monkeypatch)
    assert render.stage_book_reader(tmp_path / "build" / "panel-stage") == 0
    assert render.stage_book() == 0
    published = (tmp_path / "build" / "book" / "_quarto.yml").read_text()
    assert published == _DRAFT
    assert (docs / "_quarto.yml").read_text() == _DRAFT


def test_a_reader_policy_the_stage_cannot_apply_stops_it(tmp_path, monkeypatch, capsys):
    _draft_book(tmp_path, monkeypatch)
    (tmp_path / "render.toml").write_text("[reader_metadata.notice]\ntext = 'x'\n")
    assert render.stage_book_reader(tmp_path / "build" / "panel-stage") == 1
    assert "reader_metadata in the render policy" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# revision_metadata: a copy says which source it was copied from
# --------------------------------------------------------------------------- #
_STAMPED_POLICY = (
    'epub_style_headers = ["epub-styles.html"]\nrevision_metadata = true\n'
)


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def stamped_book(tmp_path, monkeypatch):
    """A book under git whose render policy asks every stage for its revision.

    The git has no global or system configuration and cannot see a repository
    above the test's own directory."""
    empty = tmp_path / "gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Ada Kettle")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "ada@example.org")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "_quarto.yml").write_text(_DRAFT, encoding="utf-8")
    (docs / "index.qmd").write_text("# Welcome\n\nA kettle boils.\n")
    (docs / "epub-styles.html").write_text("<style>p { margin: 0; }</style>\n")
    (tmp_path / "render.toml").write_text(_STAMPED_POLICY)
    (tmp_path / ".gitignore").write_text("build/\ngitconfig\ndocs/.local/\n")
    monkeypatch.setattr(render, "current_project", lambda: project_at(tmp_path))
    monkeypatch.setattr(render._figures, "build", lambda stage=None, gray=False: 0)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "A fictional first draft")
    return tmp_path


def _recorded(stage):
    """The source metadata a stage's `_quarto.yml` carries, as written."""
    return {
        key: value
        for key, _, value in (
            line.partition(": ")
            for line in (stage / "_quarto.yml").read_text().splitlines()
        )
        if key.startswith("source-")
    }


def test_a_stage_records_the_commit_it_was_copied_from(stamped_book, capsys):
    stage = stamped_book / "build" / "book"
    assert render.stage_book(stage) == 0
    short = _git(stamped_book, "log", "-1", "--format=%h")
    date = _git(stamped_book, "log", "-1", "--format=%cs")
    assert _recorded(stage) == {
        "source-commit": f'"{_git(stamped_book, "rev-parse", "HEAD")}"',
        "source-commit-short": f'"{short}"',
        "source-date": f'"{date}"',
        "source-modified": "false",
    }
    out = capsys.readouterr().out
    assert f"build/book is commit {short} of {date}\n" in out


@pytest.mark.parametrize(
    "change",
    [
        lambda docs: (docs / "index.qmd").write_text("# Welcome\n\nIt whistles.\n"),
        lambda docs: (docs / "notes.qmd").write_text("# Not yet added\n"),
    ],
    ids=["edited", "untracked"],
)
def test_sources_not_committed_mark_the_copy_modified(stamped_book, change, capsys):
    change(stamped_book / "docs")
    stage = stamped_book / "build" / "book"
    assert render.stage_book(stage) == 0
    assert _recorded(stage)["source-modified"] == "true"
    assert "with uncommitted changes" in capsys.readouterr().out


def test_a_file_git_ignores_is_not_a_source(stamped_book):
    (stamped_book / "docs" / ".local").mkdir()
    (stamped_book / "docs" / ".local" / "settings.json").write_text("{}\n")
    stage = stamped_book / "build" / "book"
    assert render.stage_book(stage) == 0
    assert _recorded(stage)["source-modified"] == "false"


def test_a_change_outside_the_sources_is_not_theirs(stamped_book):
    (stamped_book / "render.toml").write_text(_STAMPED_POLICY + "# a note\n")
    stage = stamped_book / "build" / "book"
    assert render.stage_book(stage) == 0
    assert _recorded(stage)["source-modified"] == "false"


@pytest.mark.parametrize("moved", ["edited", "committed"])
def test_sources_that_move_while_copied_mark_the_copy_modified(
    stamped_book, monkeypatch, moved
):
    """Another hand at work during the copy: the copy is neither commit, so it
    names the one it started from and says it is not that commit."""
    first = _git(stamped_book, "rev-parse", "HEAD")
    copy = render.mirror_sources

    def copy_then_move(src, stage):
        copy(src, stage)
        (stamped_book / "docs" / "index.qmd").write_text("# Welcome\n\nSteam.\n")
        if moved == "committed":
            _git(stamped_book, "commit", "-qam", "A fictional second draft")

    monkeypatch.setattr(render, "mirror_sources", copy_then_move)
    stage = stamped_book / "build" / "book"
    assert render.stage_book(stage) == 0
    recorded = _recorded(stage)
    assert recorded["source-commit"] == f'"{first}"'
    assert recorded["source-modified"] == "true"


def test_every_stage_records_its_own_revision(stamped_book, monkeypatch):
    noparts = stamped_book / "build" / "book-noparts"
    patch_setting(monkeypatch, render, "_settings", "BUILD_BOOK_NOPARTS", noparts)
    reader = stamped_book / "build" / "panel-stage"
    assert render.stage_book_noparts() == 0
    assert render.stage_book_reader(reader) == 0
    for stage in (noparts, reader):
        assert _recorded(stage)["source-modified"] == "false"


def test_without_the_policy_no_stage_records_a_revision(stamped_book):
    (stamped_book / "render.toml").write_text("")
    stage = stamped_book / "build" / "book"
    assert render.stage_book(stage) == 0
    assert _recorded(stage) == {}


def test_a_policy_that_is_not_a_boolean_stops_the_stage(stamped_book, capsys):
    (stamped_book / "render.toml").write_text('revision_metadata = "true"\n')
    assert render.stage_book(stamped_book / "build" / "book") == 1
    assert "must be true or false, not 'true'" in capsys.readouterr().err


def test_outside_a_checkout_no_revision_is_recorded(stamped_book, capsys):
    shutil.rmtree(stamped_book / ".git")
    stage = stamped_book / "build" / "book"
    assert render.stage_book(stage) == 0
    assert _recorded(stage) == {}
    assert "not in a git checkout" in capsys.readouterr().err
