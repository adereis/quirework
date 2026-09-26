#!/usr/bin/env python3
"""Curate and materialize the book's figures.

Canon prose embeds figures as ``![caption](illustrations/NAME.svg){…}``.
The configured figure directory holds the tracked SVG sources. At build time,
this module copies embedded figures into the corresponding staged directory so
relative embeds resolve there.

Commands (``quirework book figures …``):

    build            materialize the embedded figures into the build stage;
                     regenerates a matplotlib chart export from its sibling .py
                     if missing/stale; prunes orphans.
    check / --check  read-only consistency gate (used by the pre-commit hook):
                     every embed resolves to a live, well-formed source AND
                     generates into clean, fully-neutral grayscale.
                     Stdlib-only, install-free.
    list             print the resolved figure table.
    png              rasterize a set of SVGs to PNG in a git-ignored build dir
                     (on demand, for a PDF build or a review contact sheet).
                     Both built-in sets are re-materialized from source first,
                     so a PNG can never show a stale plate: the default color
                     set rebuilds the staged gallery and ``--set gray``
                     regenerates the grayscale one. ``--set PATH`` rasterizes
                     an arbitrary SVG directory as-is, and is the one form that
                     rasterizes whatever it finds.
    gray             generate the grayscale SVG set for inspection
                     (build/figures-gray/, or ``--out DIR``).
    inventory        which figures reference each shared definition (a
                     ``<use href="#id">`` glyph, a marker, any ``url(#id)``),
                     or with ``--uses ID`` one of them: the list a style
                     guide would otherwise keep by hand, and let go stale.

Build/check are stdlib-only (copy + parse); only ``png`` and the chart
regeneration shell out (rsvg-convert / running the chart's .py), and only then.
A project that sets ``book.figure_fonts`` also needs fontTools for both, because
each staged figure then carries subsets of those faces (see ``figfonts``).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from quirework.book import figfonts as _figfonts
from quirework.project import current_project


# The gallery directory name, as it appears in a chapter's embed
# (`![…](illustrations/NAME.svg)`), beside the chapters in the source tree, and in
# every tree a chapter is rendered from. It lives here once: an embed's relative
# path only resolves if the render tree spells the directory the same way, and a
# renamed gallery that misses one of those spellings degrades silently into
# "Could not fetch resource" placeholders. Import it; never spell it again.
GALLERY = "illustrations"

# The grayscale gallery for the e-ink EPUB edition (same filenames). It is NOT
# tracked: `figures.build` generates it from the color set into the render stage
# via graybuild, so it can never drift from the originals. A per-EPUB Quarto Lua
# filter rewrites `illustrations/X.svg` -> `illustrations-gray/X.svg` at render
# time. See graybuild.py and "Grayscale edition" in illustrations/STYLE.md.
GRAY_GALLERY = f"{GALLERY}-gray"


# generated gray, for inspection
RASTER_W = 1920  # 2x the 960px viewBox

# One definition of what a figure embed looks like, with its caption, its name
# and its optional attribute block. It lives here because this module owns the
# gallery, and `fignumbers` reads the same pattern rather than writing a second
# one: when the two disagreed, a caption wrapped across two lines matched here
# and not there, so the figure passed this check and then failed the numbering
# cross-check with "the book numbers 0". DOTALL is what admits the wrapped
# caption; the length bound keeps a stray "![" from swallowing a page. The
# bound is generous because a caption over it does not fail loudly here: it
# simply stops being an embed, the figure is never staged, and `check` counts
# one figure fewer. A 518-character caption did exactly that at 500.
# `unparsed_embeds` is the guard: every gallery reference must also parse.
CAPTION_MAX = 2000
EMBED = re.compile(
    rf"!\[(?P<cap>.{{0,{CAPTION_MAX}}}?)\]\({GALLERY}/(?P<name>[A-Za-z0-9][A-Za-z0-9_-]*)\.svg\)"
    r"(?P<attrs>\{[^}]*\})?",
    re.DOTALL,
)
# Any gallery reference at all, however its caption is shaped. What this
# finds and EMBED does not is a figure the book means to embed and the
# scanner would silently drop.
_REFERENCE = re.compile(rf"\]\({GALLERY}/(?P<name>[A-Za-z0-9][A-Za-z0-9_-]*)\.svg\)")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _settings():
    """Resolve paths from the current operation, without import-time state."""
    from types import SimpleNamespace

    CANONICAL = current_project().path("book.chapters")
    ILLUSTRATIONS = current_project().path("book.figures")
    BUILD_BOOK = current_project().path("book.build", "build") / "book"
    BUILD_ILLUSTRATIONS = BUILD_BOOK / current_project().chapter_subdir / GALLERY
    BUILD_DIR = current_project().path("book.build", "build") / "figures"
    BUILD_GRAY_DIR = current_project().path("book.build", "build") / "figures-gray"
    _TEMPLATE = (
        current_project().path("book.typst_template")
        if current_project().get("book.typst_template") is not None
        else None
    )
    return SimpleNamespace(
        BUILD_BOOK=BUILD_BOOK,
        BUILD_DIR=BUILD_DIR,
        BUILD_GRAY_DIR=BUILD_GRAY_DIR,
        BUILD_ILLUSTRATIONS=BUILD_ILLUSTRATIONS,
        CANONICAL=CANONICAL,
        ILLUSTRATIONS=ILLUSTRATIONS,
        _TEMPLATE=_TEMPLATE,
    )


def _rel(p: Path) -> Path:
    """p relative to the repo root when it is under it; otherwise p unchanged."""
    try:
        return p.relative_to(current_project().root)
    except ValueError:
        return p


def embedded_names(canonical: Path | None = None) -> set[str]:
    """Figure names the book embeds, scanned from the chapter sources.

    HTML comments are stripped first. They do not render, so an image line
    quoted inside an @-annotation is not an embed -- and a @figure mark that
    drafts its own caption and alt text (the normal way an unbuilt figure is
    specified) must not make the consistency gate demand a source that does
    not exist yet.
    """
    canonical = canonical or _settings().CANONICAL
    names: set[str] = set()
    for qmd in sorted(canonical.glob("*.qmd")):
        prose = _COMMENT.sub("", qmd.read_text(encoding="utf-8"))
        names.update(m.group("name") for m in EMBED.finditer(prose))
    return names


def unparsed_embeds(canonical: Path | None = None) -> list[str]:
    """Gallery references the embed pattern does not parse, as ``file: name``.

    A reference that fails to parse is not an error to the scanner; it is
    invisible, and an invisible figure is never staged and never checked.
    The usual cause is a caption over ``CAPTION_MAX`` characters.
    """
    canonical = canonical or _settings().CANONICAL
    out: list[str] = []
    for qmd in sorted(canonical.glob("*.qmd")):
        prose = _COMMENT.sub("", qmd.read_text(encoding="utf-8"))
        parsed = [m.group("name") for m in EMBED.finditer(prose)]
        referenced = [m.group("name") for m in _REFERENCE.finditer(prose)]
        for name in referenced:
            if parsed.count(name) < referenced.count(name):
                out.append(f"{qmd.name}: {name}")
    return out


def resolve(name: str) -> Path:
    """Source path for one figure name."""
    return _settings().ILLUSTRATIONS / f"{name}.svg"


def _ensure_source(src: Path) -> None:
    """If a source SVG is a matplotlib export that is missing or stale, rebuild it
    from its sibling .py (the only place build/png shell out for charts)."""
    py = src.with_suffix(".py")
    if not py.exists():
        return
    if src.exists() and src.stat().st_mtime >= py.stat().st_mtime:
        return
    subprocess.run([sys.executable, py.name], cwd=py.parent, check=True)


def _wellformed(path: Path) -> str | None:
    """None if the SVG parses; otherwise a short error string."""
    try:
        ET.parse(path)
        return None
    except ET.ParseError as e:
        return f"malformed SVG ({e})"


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def build(stage: Path | None = None, gray: bool = False) -> int:
    """Materialize the color figure gallery into a render stage's ``canon/`` dir.

    ``stage=None`` targets the default color stage (the module's
    ``BUILD_ILLUSTRATIONS``, which the tests monkeypatch). Pass a stage root to
    materialize into ``stage/canon/illustrations/`` instead. With ``gray=True``
    (the EPUB/DOCX ``book-noparts`` stage), the grayscale gallery is **generated**
    from the color set into ``stage/canon/illustrations-gray/`` -- never copied
    from a tracked source, so it cannot drift. The EPUB's per-format Lua filter
    swaps color embeds for that gray set at render time.
    """
    illus_out = (
        _settings().BUILD_ILLUSTRATIONS
        if stage is None
        else stage / current_project().chapter_subdir / GALLERY
    )
    canon_out = (
        _settings().BUILD_BOOK if stage is None else stage
    ) / current_project().chapter_subdir
    names = embedded_names()
    illus_out.mkdir(parents=True, exist_ok=True)
    faces = _figfonts.configured()

    problems: list[str] = []
    written = 0
    for name in sorted(names):
        src = resolve(name)
        try:
            _ensure_source(src)
        except subprocess.CalledProcessError as e:
            problems.append(f"{name}: chart build failed ({e})")
            continue
        if not src.exists():
            problems.append(f"{name}: no source at {_rel(src)}")
            continue
        err = _wellformed(src)
        if err:
            problems.append(f"{name}: {err} ({_rel(src)})")
            continue
        try:
            staged = _figfonts.embed(src.read_text(encoding="utf-8"), name, faces)
        except _figfonts.FigureFontError as e:
            problems.append(str(e))
            continue
        (illus_out / f"{name}.svg").write_text(staged, encoding="utf-8")
        written += 1

    pruned = 0
    for svg in illus_out.glob("*.svg"):
        if svg.stem not in names:
            svg.unlink()
            pruned += 1

    if problems:
        for p in problems:
            print(f"figures: {p}", file=sys.stderr)
        return 1

    gray_written = generate_gray(names, canon_out / GRAY_GALLERY) if gray else 0
    print(
        f"figures: materialized {written} color"
        f"{f' + generated {gray_written} gray' if gray else ''} into "
        f"{_rel(canon_out)}{f' (pruned {pruned})' if pruned else ''}"
    )
    return 0


def generate_gray(names: set[str], out: Path) -> int:
    """Generate the grayscale gallery for `names` from the color sources into `out`.

    Pure derivation (graybuild), never a copy of a tracked file. The configured
    figure fonts are embedded after the transform, as in the color gallery.
    Prunes stale outputs. Returns the count written.
    """
    from quirework.book import graybuild as _gray

    out.mkdir(parents=True, exist_ok=True)
    faces = _figfonts.configured()
    written = 0
    for name in sorted(names):
        text = _gray.gray_text_for(name)
        if text is None:
            continue
        text = _figfonts.embed(text, name, faces)
        (out / f"{name}.svg").write_text(text, encoding="utf-8")
        written += 1
    for svg in out.glob("*.svg"):
        if svg.stem not in names:
            svg.unlink()
    return written


# Print legibility. A figure is authored in a 960-unit viewBox and reproduced
# across the print text block, so one unit lands at
# (block width in inches) * 72 / 960 points, and a label's printed size is its
# viewBox size times that. Nobody sees this by eye until it is a proof: at the
# book's 4.15in block a 13-unit label prints at 4.0pt, and the whole figure set
# was authored to a screen scale nobody had multiplied out.
#
# The block width is the one number here that lives somewhere else --
# The configured Typst template owns the trim and margins, so they are read from that
# template rather than copied, and a margin change reaches this check for free.
# The floor is 6pt, which is where incidental type in a printed book stops
# being readable; 7 to 8pt is where it stops being a squint.
PRINT_FLOOR_PT = 6.0
VIEWBOX_UNITS = 960.0


def print_text_block_in() -> float | None:
    """The print text block's width in inches, read from the Typst template."""
    try:
        template = _settings()._TEMPLATE
        if template is None:
            return None
        src = template.read_text(encoding="utf-8")
    except OSError:
        return None
    page = re.search(r"#set page\((.*?)\n\)", src, re.S)
    if not page:
        return None
    body = page.group(1)
    width = re.search(r"width:\s*([0-9.]+)in", body)
    inside = re.search(r"inside:\s*([0-9.]+)in", body)
    outside = re.search(r"outside:\s*([0-9.]+)in", body)
    if not (width and inside and outside):
        return None
    return float(width.group(1)) - float(inside.group(1)) - float(outside.group(1))


def print_type_sizes(src: Path) -> dict[str, float]:
    """viewBox font sizes in one figure, by the class or attribute that sets them."""
    text = src.read_text(encoding="utf-8")
    sizes: dict[str, float] = {}
    for m in re.finditer(
        r"\.([A-Za-z0-9_-]+)\s*\{[^}]*?font-size:\s*([0-9.]+)px", text
    ):
        sizes[f".{m.group(1)}"] = float(m.group(2))
    for m in re.finditer(r'font-size="([0-9.]+)"', text):
        sizes.setdefault(f"inline {m.group(1)}", float(m.group(1)))
    return sizes


def print_legibility(names: set[str]) -> list[str]:
    """`name: class Npt` for every figure whose smallest type falls under the floor.

    A WARNING and never a gate. Raising a label inside a fixed canvas moves the
    artwork around it, so each of these is a per-figure layout job and not a
    number to batch-replace -- which is exactly what a gate would invite.
    """
    block = print_text_block_in()
    if block is None:
        return []
    pt_per_unit = block * 72.0 / VIEWBOX_UNITS
    out: list[str] = []
    for name in sorted(names):
        src = resolve(name)
        if not src.exists():
            continue
        sizes = print_type_sizes(src)
        under = {
            k: v * pt_per_unit
            for k, v in sizes.items()
            if v * pt_per_unit < PRINT_FLOOR_PT
        }
        if under:
            worst = min(under.values())
            detail = ", ".join(
                f"{k} {v:.1f}pt" for k, v in sorted(under.items(), key=lambda kv: kv[1])
            )
            out.append(f"{name}: smallest {worst:.1f}pt ({detail})")
    return out


def check() -> int:
    names = embedded_names()
    problems: list[str] = []

    for ref in unparsed_embeds():
        problems.append(
            f"{ref}: gallery reference the embed pattern does not parse "
            f"(caption over {CAPTION_MAX} characters?); the figure would be "
            "silently dropped"
        )

    for name in sorted(names):
        src = resolve(name)
        if not src.exists():
            if src.with_suffix(".py").exists():
                continue
            problems.append(
                f"embed illustrations/{name}.svg -> no source at {_rel(src)}"
            )
            continue
        err = _wellformed(src)
        if err:
            problems.append(f"{name}: {err} ({_rel(src)})")

    problems += _check_gray(names)
    problems += _check_fonts(names)
    from quirework.project import extension

    for reference in current_project().get("book.figure_checks", []):
        problems += extension(reference)()

    if problems:
        for p in problems:
            print(f"figures: {p}", file=sys.stderr)
        print(
            "figures: wiring inconsistent; fix the embed/source above.", file=sys.stderr
        )
        return 1

    thin = print_legibility(names)
    if thin:
        block = print_text_block_in()
        print(
            f"\n⚠ print legibility: {len(thin)} of {len(names)} figures carry type under "
            f"{PRINT_FLOOR_PT:.0f}pt when\n  reproduced across the {block:.2f}in print text block. "
            "Warning only -- the commit is\n  NOT blocked. Raising a label inside a fixed canvas "
            "moves the artwork around\n  it, so each of these is a layout job and never a "
            "batch replace.",
            file=sys.stderr,
        )
        for t in thin:
            print(f"  {t}", file=sys.stderr)

    print(f"figures OK ({len(names)} embedded, color + generated gray)")
    return 0


def _check_gray(names: set[str]) -> list[str]:
    """Validate that the grayscale gallery *generates* cleanly from the color set.

    The gray gallery is not tracked -- it is generated at build -- so there is no
    parity or orphan to check. Instead, for every embedded figure, generate its
    gray and confirm it parses and is fully neutral gray. A non-gray hex means the
    transform left a color unmapped (a bug in graybuild's map), not an untuned
    file, so it is a hard failure here.
    """
    from quirework.book import graybuild as _gray

    problems: list[str] = []
    for name in sorted(names):
        src = resolve(name)
        if not src.exists():
            continue  # a dangling color embed is already reported by the caller
        try:
            text = _gray.to_gray_svg(src.read_text(encoding="utf-8"), name)
        except ET.ParseError as e:
            problems.append(f"{name} (gray): generation produced malformed SVG ({e})")
            continue
        stray = _gray.non_gray_hexes(text)
        if stray:
            problems.append(
                f"{name} (gray): transform left color {', '.join(sorted(stray))} "
                f"(the source may state it in a form the transform does not read; "
                f"a book with its own color meanings selects book.grayscale_transform)"
            )
    return problems


def _check_fonts(names: set[str]) -> list[str]:
    """Every configured figure face can letter every figure that asks for it.

    Silent while `book.figure_fonts` is unset. Once it is set, an unreadable
    font, a missing fontTools or a character a face cannot draw fails here,
    at commit time, instead of at the next render.
    """
    from quirework.project import ConfigurationError

    try:
        faces = _figfonts.configured()
        if not faces:
            return []
        problems: list[str] = []
        for name in sorted(names):
            src = resolve(name)
            if not src.exists() or _wellformed(src):
                continue  # the caller already reports these
            gaps = _figfonts.uncovered(src.read_text(encoding="utf-8"), faces)
            if gaps:
                problems.append(f"{name}: {'; '.join(gaps)}")
        return problems
    except (ConfigurationError, _figfonts.FigureFontError) as e:
        return [str(e)]


# How one figure points at a definition: `href="#id"` (a <use>), and `url(#id)`
# anywhere a property takes one, written as an attribute, inside style="", or in
# a <style> block. A reference is a marker when the property naming it is one.
_URL = re.compile(r"url\(\s*['\"]?#([^)'\"\s]+)['\"]?\s*\)")
_DECLARATION = re.compile(r"([\w-]+)\s*:\s*([^;{}]*)")


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def references(svg: Path) -> list[tuple[str, str]]:
    """(id, how) for every in-document reference in one SVG, in order."""
    found: list[tuple[str, str]] = []

    def urls(prop: str, value: str) -> None:
        how = "marker" if prop.startswith("marker") else "url"
        found.extend((ref, how) for ref in _URL.findall(value))

    for element in ET.parse(svg).iter():
        for name, value in element.attrib.items():
            prop = _local(name)
            if prop == "href" and value.startswith("#"):
                found.append((value[1:], "use"))
            elif prop == "style":
                for declared, text in _DECLARATION.findall(value):
                    urls(declared, text)
            else:
                urls(prop, value)
        if _local(element.tag) == "style" and element.text:
            for declared, text in _DECLARATION.findall(element.text):
                urls(declared, text)
    return found


def inventory(uses: str | None = None) -> int:
    """Which figures reference each definition, or one of them."""
    import difflib
    from collections import Counter

    embedded = embedded_names()
    by_id: dict[str, Counter] = {}
    kinds: dict[str, set[str]] = {}
    # A figure that does not parse cannot be counted, and a count that left it
    # out would read as complete. It is named, and the command fails.
    unreadable: list[str] = []
    for svg in sorted(_settings().ILLUSTRATIONS.glob("*.svg")):
        try:
            found = references(svg)
        except ET.ParseError as exc:
            unreadable.append(f"figures: {_rel(svg)}: malformed SVG ({exc})")
            continue
        for ref, how in found:
            by_id.setdefault(ref, Counter())[svg.stem] += 1
            kinds.setdefault(ref, set()).add(how)
    for line in unreadable:
        print(line, file=sys.stderr)
    status = 1 if unreadable else 0

    def figure(stem: str) -> str:
        return stem if stem in embedded else f"{stem} (not embedded)"

    if uses is not None:
        uses = uses.lstrip("#")
        if uses not in by_id:
            close = difflib.get_close_matches(uses, list(by_id), n=3)
            hint = f" Close: {', '.join('#' + c for c in close)}." if close else ""
            print(f"figures: no figure references #{uses}.{hint}")
            return status
        counts = by_id[uses]
        print(
            f"#{uses} ({', '.join(sorted(kinds[uses]))}): "
            f"{len(counts)} figure(s), {sum(counts.values())} reference(s)"
        )
        for stem, n in sorted(counts.items()):
            print(f"  {n:>3}  {figure(stem)}")
        return status
    if not by_id:
        print("figures: no figure references a shared definition.")
        return status
    for ref in sorted(by_id):
        counts = by_id[ref]
        print(
            f"#{ref} ({', '.join(sorted(kinds[ref]))}): {len(counts)} figure(s), "
            f"{sum(counts.values())} reference(s)"
        )
        print(f"  {', '.join(figure(stem) for stem in sorted(counts))}")
    return status


def show_list() -> int:
    names = embedded_names()
    print(f"{'figure':<26} {'source':<50}")
    print("-" * 78)
    for name in sorted(names):
        src = resolve(name)
        miss = (
            "" if (src.exists() or src.with_suffix(".py").exists()) else "  !! MISSING"
        )
        print(f"{name:<26} {str(_rel(src)):<50}{miss}")
    print(f"\n{len(names)} embedded")
    return 0


def render_png(which: str | None, out: Path) -> int:
    if not shutil.which("rsvg-convert"):
        print(
            "figures: rsvg-convert not found (Fedora: sudo dnf install librsvg2-tools)",
            file=sys.stderr,
        )
        return 127
    # BOTH built-in sets re-materialize from source on every run, and the color
    # one used to not. It rebuilt only when the staged gallery was *empty*, so
    # once anything had populated build/book/canon/illustrations -- a render, an
    # earlier png -- an edited SVG rasterized to its previous state and the
    # eyes-only check silently passed on a stale plate. `figures --check` cannot
    # catch that: it validates the SOURCE and the gray transform, never a
    # left-over artifact. A missing figure now aborts rather than rasterizing a
    # short set, which the empty-dir call used to swallow.
    if which == "gray":
        generate_gray(embedded_names(), _settings().BUILD_GRAY_DIR)
        srcdir = _settings().BUILD_GRAY_DIR
    elif which in (None, "color", "figures", "illustrations"):
        rc = build()
        if rc:
            return rc
        srcdir = _settings().BUILD_ILLUSTRATIONS
    else:
        srcdir = Path(which)
    if not srcdir.is_dir():
        print(f"figures: no such set '{which}' ({srcdir})", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for svg in sorted(srcdir.glob("*.svg")):
        subprocess.run(
            [
                "rsvg-convert",
                "-w",
                str(RASTER_W),
                str(svg),
                "-o",
                str(out / f"{svg.stem}.png"),
            ],
            check=True,
        )
        n += 1
    print(f"figures: rendered {n} PNG(s) from {_rel(srcdir)} into {_rel(out)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="quirework book figures",
        description="Materialize the book's figure gallery for the build tree.",
    )
    ap.add_argument(
        "action",
        nargs="?",
        default="build",
        choices=["build", "check", "list", "png", "gray", "inventory"],
        help="build (default) | check | list | png | gray | inventory",
    )
    ap.add_argument(
        "--check", action="store_true", help="alias for the check action (pre-commit)"
    )
    ap.add_argument(
        "--set",
        dest="set_",
        metavar="SET",
        default=None,
        help="png: which SVG set to rasterize -- 'gray' generates + "
        "rasterizes the gray set; 'color' (the default) "
        "re-materializes and rasterizes the color set; a "
        "PATH rasterizes that directory as-is",
    )
    ap.add_argument("--out", default=None, help="png/gray: output dir")
    ap.add_argument(
        "--uses",
        metavar="ID",
        default=None,
        help="inventory: only the figures that reference this definition",
    )
    args = ap.parse_args(argv)
    if args.uses is not None and args.action != "inventory":
        print("figures: --uses belongs to the inventory action", file=sys.stderr)
        return 2

    # --check is the hook's spelling of the check action; combined with a
    # different action it would silently run check instead of what was named.
    if args.check and args.action not in ("build", "check"):
        print(
            f"figures: --check conflicts with the '{args.action}' action "
            f"(use one or the other)",
            file=sys.stderr,
        )
        return 2
    if args.check or args.action == "check":
        return check()
    if args.action == "list":
        return show_list()
    if args.action == "inventory":
        return inventory(args.uses)
    if args.action == "png":
        return render_png(
            args.set_, Path(args.out) if args.out else _settings().BUILD_DIR
        )
    if args.action == "gray":
        out = Path(args.out) if args.out else _settings().BUILD_GRAY_DIR
        n = generate_gray(embedded_names(), out)
        print(f"figures: generated {n} gray SVG(s) into {_rel(out)}")
        return 0
    return build()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
