"""Shared render staging for the Quarto book.

`quirework book build` stages the book and runs Quarto in the staged tree (never
writing into `docs/`). That staging plus Quarto discovery lives here.

A book renders from **three** stages, because screen and print have different
front matter and two formats mishandle `part:` dividers:

- `build/book/` -- parts intact, color figures -- renders screen formats (HTML).
- `build/book-print/` -- parts intact, color figures, screen-only front matter
  emptied -- renders the Typst PDF.
- `build/book-noparts/` -- parts flattened into unnumbered divider chapters,
  color and gray galleries both present -- renders the **parts-less** formats
  (EPUB, DOCX). Quarto drops every `part:` from those: a file-based part loses
  its whole opening essay, and a title-only part loses the book's division into
  Parts entirely. So flattening promotes a part file to a chapter in reading
  order (`unnumber_h1` keeping the chapter numbers identical to the other
  formats) and materializes a title-only part as a one-line `.unnumbered
  .part-divider` chapter in its slot. The EPUB's per-format Lua filter swaps
  color figures for gray; DOCX keeps color. This is the same fix
  `quirework book redpen build` applies for its review DOCX
  (`flatten_book_parts` / `unnumber_h1`, from bookstage).

All stages also generate the Contents page, which only Typst can build for
itself (`bookstage.render_contents`).

Final outputs from all stages are consolidated into `build/book/_site/`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from quirework.project import current_project
from quirework.book import bookstage as _bookstage
from quirework.book import fignumbers as _fignumbers
from quirework.book import figures as _figures

# the screen stage (git-ignored)
# the parts-flattened stage
# the print stage (see below)
SITE = "_site"  # Quarto output-dir (relative to each stage), from docs/_quarto.yml

# Book formats whose output has no part-divider concept: Quarto drops every
# `part:` from them (taking a file-based part's opening essay with it), so they
# render from the flattened stage. Everything else keeps its parts.
PARTLESS_FORMATS = {"epub", "docx"}

# Formats whose output is a PRINT INTERIOR rather than something read on a
# screen, and which therefore get a stage of their own. It exists for one
# reason and the reason generalizes: print's front matter is not the screen's.
# Quarto pins `index.qmd` to reading-position 1 in every format, so the web
# edition's welcome landing was also printing as a two-sentence leaf between
# the title page and the copyright page, where a book has nothing; and Quarto's
# conditional content cannot remove it, because a chapter's opening heading is
# hoisted out of the conditional div before the condition is evaluated, leaving
# a page with just a title on it. One `quarto render` per stage is what makes
# this possible at all: two formats sharing a stage share its reading order.
PRINT_FORMATS = {"typst"}


def _options():
    """Read policy from the selected project for this operation."""
    from types import SimpleNamespace

    BUILD_BOOK = _figures._settings().BUILD_BOOK
    PRINT_DROPS = set(
        current_project().data("book.render_policy", {}).get("print_drops", set())
    )
    return SimpleNamespace(BUILD_BOOK=BUILD_BOOK, PRINT_DROPS=PRINT_DROPS)


def _settings():
    """Resolve paths from the current operation, without import-time state."""
    from types import SimpleNamespace

    BUILD_BOOK_NOPARTS = current_project().path("book.build", "build") / "book-noparts"
    BUILD_BOOK_PRINT = current_project().path("book.build", "build") / "book-print"
    return SimpleNamespace(
        BUILD_BOOK_NOPARTS=BUILD_BOOK_NOPARTS, BUILD_BOOK_PRINT=BUILD_BOOK_PRINT
    )


def find_quarto() -> str | None:
    """Quarto on PATH, or the one pip-installed next to this venv's python."""
    found = shutil.which("quarto")
    if found:
        return found
    cand = Path(sys.executable).parent / "quarto"
    return str(cand) if cand.exists() else None


def mirror_sources(src: Path, stage: Path) -> None:
    """Refresh the book sources staged under `stage` from the `src` tree.

    Replaces the previously staged sources (so a deleted chapter cannot linger)
    while preserving Quarto's own outputs and caches (`_site`, `.quarto`) for
    incremental renders. Both figure galleries (color `illustrations/` and the
    EPUB's grayscale `illustrations-gray/`) are skipped here: they are build
    artifacts that `figures.build()` materializes straight into the staged tree.
    Agent-guidance and repo-scaffolding files (`AGENTS.md`, `CLAUDE.md`,
    `GEMINI.md`, `.gitignore`) are skipped too: they live in `docs/` for editors,
    are not book sources, and only confuse anyone reading the staged tree. The
    per-chapter metadata sidecars (`_marks/`) are skipped as well: they are pure
    working metadata, never rendered, and keeping them out of the stage means the
    published book cannot leak a mark into its HTML source (the marks are gone
    from the prose `.qmd` and the sidecars never arrive). `ignore_patterns`
    matches by basename at every depth, so the one entry covers both `docs/_marks/`
    and `docs/canon/_marks/`.
    """
    stage.mkdir(parents=True, exist_ok=True)
    for child in stage.iterdir():
        if child.name in ("_site", ".quarto"):
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    shutil.copytree(
        src,
        stage,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            "_site",
            ".quarto",
            _figures.GALLERY,
            _figures.GRAY_GALLERY,
            "*.quarto_ipynb",
            "__pycache__",
            "AGENTS.md",
            "CLAUDE.md",
            "GEMINI.md",
            ".gitignore",
            "_marks",
        ),
    )


def stage_book(stage: Path | None = None) -> int:
    """Stage docs/ into a parts-intact render tree and materialize the galleries.

    Default stage is build/book/ (the publish stage); redpen passes its own so a
    review-copy build cannot clobber the publish stage's `_site` outputs or its
    incremental `.quarto` cache. Returns the figure-build return code (non-zero
    if a figure source is missing or malformed); callers should abort the render
    on a non-zero result.
    """
    stage = stage or _options().BUILD_BOOK
    mirror_sources(current_project().path("book.source", "docs"), stage)
    _bookstage.render_contents(stage)
    # Print settings that have to take effect partway through the book (the
    # front matter's roman folios giving way to arabic, the back matter set
    # smaller). Typst-only raw blocks, so every other format ignores them;
    # bookstage.TYPST_PROLOGUES owns what they are and why they cannot live in
    # the template.
    _bookstage.inject_typst_prologues(stage)
    return (
        _figures.build()
        if stage == _options().BUILD_BOOK
        else _figures.build(stage=stage)
    )


# Front matter that belongs to the screen and not to the printed block.


def stage_book_print() -> int:
    """Stage docs/ into build/book-print/: parts intact, screen-only pages gone.

    Same staging as `stage_book`, then `bookstage.blank_chapters` empties the
    pages a printed book does not have (`PRINT_DROPS`) -- emptied rather than
    removed because Quarto refuses a book whose chapter list has no home page.
    Returns the figure-build return code."""
    rc = stage_book(stage=_settings().BUILD_BOOK_PRINT)
    if rc:
        return rc
    blanked = _bookstage.blank_chapters(
        _settings().BUILD_BOOK_PRINT, _options().PRINT_DROPS
    )
    if blanked:
        print(
            f"render: print interior omits {', '.join(blanked)} (screen front matter)"
        )
    return 0


def stage_book_noparts() -> int:
    """Stage docs/ into build/book-noparts/ with the book's parts flattened.

    Same staging as `stage_book`, then the fix for the parts-less formats: promote
    each file-based `part:` to a chapter in its reading-order slot and mark its H1
    `.unnumbered`, so the Part Two opener reaches EPUB/DOCX in position without
    shifting any chapter number (`bookstage.flatten_book_parts` / `unnumber_h1`).
    Returns the figure-build return code.
    """
    mirror_sources(
        current_project().path("book.source", "docs"), _settings().BUILD_BOOK_NOPARTS
    )
    # Before flatten (it wants real parts), and with anchor links: Quarto rewrites
    # a `chapter.qmd` link to the output file for HTML only.
    _bookstage.render_contents(_settings().BUILD_BOOK_NOPARTS, anchors=True)
    rc = _figures.build(
        stage=_settings().BUILD_BOOK_NOPARTS, gray=True
    )  # EPUB embeds the gray set
    if rc:
        return rc
    before, after = _bookstage.strip_style_comments(_settings().BUILD_BOOK_NOPARTS)
    print(
        f"render: EPUB styles stripped to rules, {before} -> {after} bytes "
        "per content document"
    )
    promoted, dividers = _bookstage.flatten_book_parts(_settings().BUILD_BOOK_NOPARTS)
    for rel in promoted:
        _bookstage.unnumber_h1(_settings().BUILD_BOOK_NOPARTS / rel)
    if promoted or dividers:
        print(
            f"render: EPUB/DOCX have no book parts; promoted {len(promoted)} part "
            f"file(s) to chapters ({', '.join(promoted) or '-'}), materialized "
            f"{len(dividers)} divider(s) ({', '.join(dividers) or '-'})"
        )
    return 0


def book_formats() -> list[str]:
    """The `format:` keys declared in docs/_quarto.yml (top-level block), in order.

    A tiny stdlib scan, not a YAML dependency (the package core is stdlib-only,
    like redpen's own `_quarto.yml` parsing): find `format:` at column 0, then take
    each immediate child `key:` at the first child indent until the block ends
    (nested keys like `theme:` / `filters:` are skipped).
    """
    lines = (
        (current_project().path("book.quarto", "docs/_quarto.yml"))
        .read_text(encoding="utf-8")
        .splitlines()
    )
    keys: list[str] = []
    i, n = 0, len(lines)
    while i < n and lines[i].rstrip() != "format:":
        i += 1
    i += 1
    child_indent: int | None = None
    while i < n:
        line, i = lines[i], i + 1
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            break  # left the format: block
        if child_indent is None:
            child_indent = indent
        if indent != child_indent:
            continue  # a nested key (theme:, filters:, template-partials:)
        m = re.match(r"([A-Za-z][\w-]*)\s*:", s)
        if m:
            keys.append(m.group(1))
    return keys


def split_to(args: list[str]) -> tuple[list[str] | None, list[str]]:
    """Pull every explicit `--to a,b` / `--to=a,b` out of pass-through render args.

    Returns (requested formats or None if no `--to`, the remaining args). All
    `--to` occurrences are consumed and their formats unioned in order — Quarto
    itself takes only one `--to`, so a second one riding through used to reach a
    stage whose config had already been trimmed to the first, silently rendering
    the second format without its declared options. Everything that is not a
    `--to` rides through untouched, so `quirework book build --cache-refresh` still
    works.
    """
    out: list[str] = []
    requested: list[str] | None = None
    i = 0
    while i < len(args):
        a = args[i]
        got: list[str] | None = None
        if a == "--to" and i + 1 < len(args):
            got = [f.strip() for f in args[i + 1].split(",") if f.strip()]
            i += 2
        elif a.startswith("--to="):
            got = [f.strip() for f in a[len("--to=") :].split(",") if f.strip()]
            i += 1
        else:
            out.append(a)
            i += 1
            continue
        requested = requested or []
        requested += [f for f in got if f not in requested]
    return requested, out


def restrict_formats(stage: Path, keep: list[str]) -> None:
    """Trim the staged _quarto.yml's top-level `format:` block to `keep` only.

    A single `quarto render` (no --to) then emits exactly those formats in one
    pass. That is how a stage renders both its formats: a comma-joined --to is
    rejected by Quarto, and running one `--to` render after another cleans the
    first's output out of _site. Operates on the staged copy; docs/ is untouched.
    Unknown names in `keep` are simply absent from the result.
    """
    cfg = stage / "_quarto.yml"
    lines = cfg.read_text(encoding="utf-8").splitlines()
    n = len(lines)
    f = 0
    while f < n and lines[f].rstrip() != "format:":
        f += 1
    if f >= n:
        return
    out = lines[: f + 1]
    i = f + 1
    child_indent: int | None = None
    while i < n:
        line = lines[i]
        s = line.strip()
        indent = len(line) - len(line.lstrip())
        if s and not s.startswith("#") and indent == 0:
            out.extend(lines[i:])  # left the format block; keep the rest verbatim
            i = n
            break
        if not s or s.startswith("#"):
            out.append(line)
            i += 1
            continue
        if child_indent is None:
            child_indent = indent
        if indent == child_indent:
            m = re.match(r"([A-Za-z][\w-]*)\s*:", s)
            key = m.group(1) if m else None
            j = i + 1  # gather this format's sub-block [i, j)
            while j < n:
                s2, l2 = lines[j].strip(), lines[j]
                if (
                    s2
                    and not s2.startswith("#")
                    and len(l2) - len(l2.lstrip()) <= child_indent
                ):
                    break
                j += 1
            if key in keep:
                out.extend(lines[i:j])
            i = j
        else:
            out.append(line)  # defensive: mis-indented line rides along
            i += 1
    cfg.write_text("\n".join(out) + "\n", encoding="utf-8")


def partition_formats(
    requested: list[str] | None,
) -> tuple[list[str], list[str], list[str]]:
    """Split formats into (screen, print, parts-less), preserving order.

    Three stages, three reading orders. `screen` keeps the book's parts and the
    welcome landing; `print` keeps the parts and drops the landing; `parts-less`
    flattens the parts for the formats Quarto gives none.

    `requested` is `split_to`'s format list, or None for "everything declared in
    _quarto.yml". Names are not validated here — `main()` checks them against
    the declared formats before staging, because `restrict_formats` silently
    drops any name the config does not declare.
    """
    universe = book_formats() if requested is None else requested
    screen = [
        f for f in universe if f not in PARTLESS_FORMATS and f not in PRINT_FORMATS
    ]
    printed = [f for f in universe if f in PRINT_FORMATS]
    less = [f for f in universe if f in PARTLESS_FORMATS]
    return screen, printed, less


HELP = """\
usage: quirework book build [--to FMT[,FMT...]] [--any-font] [quarto render args...]

Render the book into build/ (the render workdir), never into the docs/
sources. Renders from three staged trees: HTML with the book's parts intact,
Typst PDF with screen-only front matter emptied, and EPUB/DOCX with the parts
flattened so Quarto cannot drop them. Chapter numbers stay identical across
all formats.
Every final output lands in build/book/_site/.

options:
  -h, --help        show this help message and exit
  --to FMT[,FMT..]  render only these declared formats (repeatable);
                    default is everything declared in docs/_quarto.yml
  --any-font        render even if a required typeface is missing. Off by
                    default because a missing font does not fail: the
                    renderer substitutes and the book comes out whole and
                    in the wrong face (`quirework book fonts`)

Anything else passes straight through to `quarto render`
(e.g. --cache-refresh). Uses the quarto from the venv or PATH.
"""


def main(argv: list[str] | None = None) -> int:
    """The `quirework book build` orchestration: stage, trim, render, consolidate.

    Hand-parsed rather than argparse because unknown flags are the contract
    here — they pass through to `quarto render` — so only `-h/--help` and
    `--to` are interpreted.
    """
    argv = list(argv if argv is not None else sys.argv[1:])
    if any(a in ("-h", "--help") for a in argv):
        print(HELP, end="")
        return 0

    quarto = find_quarto()
    if not quarto:
        print(
            "render: quarto not found. Install it (`pip install -e '.[docs]'`), "
            "then re-run.",
            file=sys.stderr,
        )
        return 127

    # Fonts are refused rather than warned about, and the asymmetry with every
    # other advisory in this project is deliberate. A missing font does not
    # fail: Typst takes the next family in the stack and rsvg substitutes, so
    # the render SUCCEEDS and hands back a whole, plausible book set in the
    # wrong face, with every gate green. There is no later step that catches
    # it, and a warning scrolls past in a build log. `--any-font` is the way
    # through for anyone who means it.
    if "--any-font" in argv:
        argv.remove("--any-font")  # consumed here; never reaches quarto
    else:
        from quirework.book import fonts as _fonts

        if _fonts.missing():
            _fonts.report(as_check=False)
            print(
                "  Refusing to render in a substituted face. Install them, or "
                "pass --any-font\n  to render anyway.",
                file=sys.stderr,
            )
            return 3

    requested, passthru = split_to(argv)
    declared = book_formats()
    if requested:
        unknown = [f for f in requested if f not in declared]
        if unknown:
            # restrict_formats keeps only declared blocks, so an unknown name
            # would silently render nothing it promised.
            print(
                f"render: unknown format(s): {', '.join(unknown)} "
                f"(declared in docs/_quarto.yml: {', '.join(declared)})",
                file=sys.stderr,
            )
            return 2
    screen, printed, less = partition_formats(requested)
    if not screen and not printed and not less:
        print(
            f"render: no known formats to render (--to {requested}).", file=sys.stderr
        )
        return 2

    # Each stage renders its formats in ONE bare `quarto render`: a comma-joined
    # --to is rejected by Quarto, and a second `--to` render cleans the first's
    # output from _site, so instead the staged _quarto.yml is trimmed to the
    # stage's formats. Parts-safe formats render from the parts-intact stage;
    # parts-less ones from the flattened stage.
    if screen:
        rc = stage_book()
        if rc:
            return rc
        restrict_formats(_options().BUILD_BOOK, screen)
        rc = subprocess.call(
            [quarto, "render", *passthru, "--output-dir", SITE],
            cwd=_options().BUILD_BOOK,
        )
        if rc:
            return rc
        # `search: false` in _quarto.yml removes the box and its JavaScript,
        # but Quarto writes the full-text index anyway -- a 275 KB copy of the
        # whole book that nothing on the site loads. Delete it here rather than
        # ship a dead file. Same class of post-render touch-up as the DOCX and
        # EPUB fixes below: a property of the artifact the config cannot reach.
        (_options().BUILD_BOOK / SITE / "search.json").unlink(missing_ok=True)

    if printed:
        rc = stage_book_print()
        if rc:
            return rc
        restrict_formats(_settings().BUILD_BOOK_PRINT, printed)
        rc = subprocess.call(
            [quarto, "render", *passthru, "--output-dir", SITE],
            cwd=_settings().BUILD_BOOK_PRINT,
        )
        if rc:
            return rc
        psite = _settings().BUILD_BOOK_PRINT / SITE
        site = _options().BUILD_BOOK / SITE
        site.mkdir(parents=True, exist_ok=True)
        for produced in sorted(psite.glob("*.pdf")):
            shutil.copyfile(produced, site / produced.name)

    if less:
        rc = stage_book_noparts()
        if rc:
            return rc
        restrict_formats(_settings().BUILD_BOOK_NOPARTS, less)
        rc = subprocess.call(
            [quarto, "render", *passthru, "--output-dir", SITE],
            cwd=_settings().BUILD_BOOK_NOPARTS,
        )
        if rc:
            return rc
        # Cap DOCX image widths in the PUBLISHED edition too, deliberately
        # (author call): the DOCX's realistic consumption path is Google Docs /
        # Word review, where an image near the column width is cropped rather
        # than scaled — whole figures at 4.5in beat larger cropped ones. Then
        # consolidate EPUB/DOCX into build/book/_site/ so every final output
        # lands in one place.
        nsite = _settings().BUILD_BOOK_NOPARTS / SITE
        for docx_path in nsite.glob("*.docx"):
            _bookstage.cap_docx_image_widths(docx_path)
        # Two conformance defects the writers emit that no reader reveals: an
        # `alt` copied onto a wrapping div, and a font `src` url that resolves
        # nowhere. Applied here, before the consolidating copy, so the checked
        # artifact and the shipped one are the same file. `bookstage` owns why.
        for epub_path in nsite.glob("*.epub"):
            _bookstage.conform_epub(epub_path)
        site = _options().BUILD_BOOK / SITE
        site.mkdir(parents=True, exist_ok=True)
        for produced in (*sorted(nsite.glob("*.epub")), *sorted(nsite.glob("*.docx"))):
            shutil.copyfile(produced, site / produced.name)

    # Figure numbering, proved against the artifacts rather than trusted from
    # the config. The four writers reach their numbers four different ways and
    # three of the four have broken on their own, so this is the only place the
    # invariant can actually be checked -- and it is checked on every render,
    # over exactly the formats this run produced. `fignumbers` owns the why.
    # Consolidating artifacts into book/_site does not refresh book's sources.
    # A non-HTML render must not read a missing or stale screen-stage config.
    # Use a source stage actually refreshed by this invocation.
    if screen:
        validation_stage = _options().BUILD_BOOK
    elif printed:
        validation_stage = _settings().BUILD_BOOK_PRINT
    else:
        validation_stage = _settings().BUILD_BOOK_NOPARTS
    rc = _fignumbers.verify(
        _options().BUILD_BOOK / SITE, validation_stage, screen + printed + less
    )

    # EPUB spec conformance, for the same reason and in the same place: it is a
    # property of the artifact, not of the config, and it is the one edition
    # whose defects are invisible from the inside -- every desktop reader is
    # forgiving exactly where EPUB is strict. Unlike the checks above it needs
    # a JRE and a pinned 32 MB download, so its absence is a loud skip rather
    # than a render failure; `epubcheck` owns that call and its reasoning.
    rc_epub = 0
    if "epub" in less:
        from quirework.book import epubcheck as _epubcheck

        rc_epub = _epubcheck.verify(_options().BUILD_BOOK / SITE)
    return rc or rc_epub


if __name__ == "__main__":
    raise SystemExit(main())
