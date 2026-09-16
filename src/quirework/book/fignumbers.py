"""Prove that every rendered edition numbers the figures identically.

A figure number is only worth printing if it names ONE figure across the whole
book and means the same thing in every edition. Nothing about the Quarto config
makes that true: the four writers reach their numbers by four different routes,
and each has broken on its own at least once.

- The **HTML** is multi-page, so each chapter is a separate Pandoc run whose
  float counter starts at 1. Straight-through numbering is therefore not
  expressible there at all, which is why the book numbers by chapter.
- The **EPUB** and **DOCX** are one Pandoc run over the whole book, so they
  honor `crossref: chapters` directly and were the only two ever correct.
- The **Typst PDF** takes its chapter prefix from a `set figure(numbering:)`
  rule that Quarto supplies inside a book template this project replaces, and
  its per-chapter restart from a show rule Quarto injects (which fired even
  when chapter numbering was off, so the PDF once restarted at a bare
  "Figure 1" in every chapter while the EPUB ran 1..36).

So the numbering is checked against the artifacts, never against the config.
Two properties, and the first is the one that makes numbers meaningful:

1. Within each edition, every figure has a DISTINCT number, and there are
   exactly as many numbered figures as the book embeds.
2. Across editions, each figure carries the SAME number. Figures are matched by
   their caption rather than by position, because Typst floats a figure to the
   top or bottom of its page, so the printed order is not always the source
   order -- and a check that assumed it was would fail on a book that is
   perfectly fine.

Both are computed from the built artifacts in `build/book/_site/`, so this runs
after a render, never in the pre-commit hook.
"""

from __future__ import annotations

import html as _html
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from quirework.book import bookstage as _bookstage
from quirework.book import figures as _figures
from quirework.project import current_project


def _named(path) -> str:
    """A path as the project's own operator would type it."""
    try:
        return str(Path(path).relative_to(current_project().root))
    except ValueError:
        return str(path)

# The rendered caption, reduced to something the four writers cannot disagree
# about: no markup, no entities, no typographic quotes, no ligatures-turned-
# whatever. What survives is letters and digits, and that is the join key.
_KEY_LEN = 48

# "Figure 3.2:" at the head of a caption. Matched after `_flatten`, so the
# writers' several spellings of that space (an `&nbsp;` entity, a literal
# U+00A0, an ordinary one) have already converged.
_LABEL = re.compile(r"Figure[\s ]+([0-9]+(?:\.[0-9]+)*)\s*:\s*", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_FIGCAPTION = re.compile(
    r"<figcaption[^>]*>(.*?)</figcaption>", re.DOTALL | re.IGNORECASE
)
# An embed, with its caption and its optional attribute block. The `#fig-` id in
# that block is what makes Quarto number a figure, so its presence is what says
# whether this embed is expected to carry a label in the artifacts.
_EMBED = re.compile(
    rf"!\[(?P<cap>.*?)\]\({_figures.GALLERY}/[A-Za-z0-9][A-Za-z0-9_-]*\.svg\)"
    r"(?P<attrs>\{[^}]*\})?"
)
_FIGID = re.compile(r"#fig-[A-Za-z0-9][A-Za-z0-9_-]*")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _flatten(text: str) -> str:
    """Markup and entities out, so four writers' spellings of one caption converge."""
    return _html.unescape(_TAG.sub(" ", text))


def caption_key(text: str) -> str:
    """A caption reduced to its join key (see `_KEY_LEN`).

    Flattens markup itself rather than trusting the caller to have done it: this
    is the join, so it has to give the same answer for a DOCX run of plain text
    and an HTML caption with an `<em>` in it.
    """
    return re.sub(r"[^a-z0-9]", "", _flatten(text).lower())[:_KEY_LEN]


def _pairs(chunks: list[str]) -> list[tuple[str, str]]:
    """(number, caption key) for every chunk that opens with a figure label.

    A chunk with no label is not a numbered figure (a `redpen --gray` twin, a
    stray caption) and is dropped rather than guessed at.
    """
    out: list[tuple[str, str]] = []
    for chunk in chunks:
        flat = _flatten(chunk)
        m = _LABEL.search(flat)
        if not m:
            continue
        key = caption_key(flat[m.end() :])
        if key:
            out.append((m.group(1), key))
    return out


def expected_count(stage: Path) -> tuple[int, set[str]]:
    """(how many figures the book NUMBERS, the caption keys of those it does not).

    HTML comments are stripped first, exactly as `figures.embedded_names` does:
    an image line quoted inside a comment does not render and must not be
    counted against the artifacts.

    Not every embed is a numbered figure. An embed with no `#fig-` id is not
    cross-referenceable and no writer labels it, which is the correct form for a
    plate in an UNNUMBERED chapter: back matter has no chapter number, so HTML
    (numbering per chapter) restarts at a bare "Figure 1" while DOCX (one run
    over the book) reaches for the position and prints "22.1". That is not a
    bug to fix in the config, it is a number that does not exist, and the book
    never refers to a figure by one anyway.

    So the unnumbered ones are returned rather than dropped: the caller proves
    they carry no label in ANY edition, which is a stronger claim than the count
    it replaces. Exempting them from the count and then not checking them would
    be exactly the hole this module exists to close.
    """
    numbered = 0
    unnumbered: set[str] = set()
    for rel in _bookstage.book_chapters(stage):
        src = stage / rel
        if not src.exists():
            continue
        body = _COMMENT.sub("", src.read_text(encoding="utf-8"))
        for m in _EMBED.finditer(body):
            if _FIGID.search(m.group("attrs") or ""):
                numbered += 1
            else:
                unnumbered.add(caption_key(m.group("cap")))
    return numbered, unnumbered


def from_html(site: Path) -> list[tuple[str, str]]:
    chunks: list[str] = []
    for html in sorted(site.rglob("*.html")):
        chunks += _FIGCAPTION.findall(
            html.read_text(encoding="utf-8", errors="replace")
        )
    return _pairs(chunks)


def from_epub(path: Path) -> list[tuple[str, str]]:
    chunks: list[str] = []
    with zipfile.ZipFile(path) as z:
        for name in sorted(n for n in z.namelist() if n.endswith((".xhtml", ".html"))):
            chunks += _FIGCAPTION.findall(z.read(name).decode("utf-8", "replace"))
    return _pairs(chunks)


def from_docx(path: Path) -> list[tuple[str, str]]:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    # One chunk per paragraph: a DOCX caption is a paragraph, and splitting on
    # anything coarser would run a caption into the body text after it.
    return _pairs(re.findall(r"<w:p[ >].*?</w:p>", xml, re.DOTALL))


def from_pdf(path: Path) -> list[tuple[str, str]]:
    """Figure numbers from the PDF's text layer, via poppler's pdftotext.

    A caption is a block that starts a line with the label and runs to the next
    blank line, which is how Typst sets one and how pdftotext reproduces it.
    """
    exe = shutil.which("pdftotext")
    if exe is None:
        raise FileNotFoundError(
            "pdftotext not found: the PDF's figure numbers cannot be read, so the "
            "cross-format check cannot run. Install poppler-utils "
            "(Fedora: sudo dnf install poppler-utils)."
        )
    text = subprocess.run(
        [exe, "-layout", str(path), "-"],
        capture_output=True,
        check=True,
        text=True,
        errors="replace",
    ).stdout
    return _pairs(re.split(r"\n\s*\n", text))


# The artifact each format lands as in build/book/_site/. HTML is the directory
# itself (one page per chapter), which is exactly the shape that broke.
_READERS = {
    "html": (None, from_html),
    "typst": (".pdf", from_pdf),
    "epub": (".epub", from_epub),
    "docx": (".docx", from_docx),
}


def _artifact(site: Path, suffix: str) -> Path | None:
    found = sorted(site.glob(f"*{suffix}"))
    return found[0] if found else None


def verify(site: Path, stage: Path, formats: list[str]) -> int:
    """Check properties 1 and 2 over the artifacts this render produced.

    Returns 0 when the editions agree, 1 when they do not; prints what differs.
    """
    want, unnumbered = expected_count(stage)
    reports: dict[str, list[tuple[str, str]]] = {}
    problems: list[str] = []

    for fmt in formats:
        entry = _READERS.get(fmt)
        if entry is None:
            continue
        suffix, reader = entry
        if suffix is None:
            reports[fmt] = reader(site)
            continue
        art = _artifact(site, suffix)
        if art is None:
            problems.append(f"{fmt}: no {suffix} in {site}")
            continue
        reports[fmt] = reader(art)

    for fmt, pairs in sorted(reports.items()):
        if len(pairs) != want:
            problems.append(
                f"{fmt}: {len(pairs)} numbered figure(s), but the book numbers {want}"
            )
        # The other half of property 1: an embed the book declines to number must
        # come out unnumbered EVERYWHERE. A writer that labelled one anyway would
        # print a number naming a chapter the reader never sees.
        for n, key in pairs:
            if key in unnumbered:
                problems.append(
                    f"{fmt}: numbered a figure the book leaves unnumbered "
                    f"(got {n}, caption '{key}'). An embed with no #fig- id is "
                    f"a plate in an unnumbered chapter; it carries no number in "
                    f"any other edition, so this one disagrees with all of them."
                )
        numbers = [n for n, _ in pairs]
        dupes = sorted({n for n in numbers if numbers.count(n) > 1})
        if dupes:
            problems.append(
                f"{fmt}: number(s) used by more than one figure: {', '.join(dupes)} "
                f"(a figure number has to name one figure book-wide)"
            )

    # Property 2, against the first format as the reference. Which one is
    # reference does not matter: any disagreement names both sides.
    if len(reports) > 1:
        names = sorted(reports)
        ref = names[0]
        ref_map = dict((k, n) for n, k in reports[ref])
        for other in names[1:]:
            for n, k in reports[other]:
                if k not in ref_map:
                    problems.append(
                        f"{other}: figure {n} has a caption that {ref} does not carry "
                        f"(key {k!r})"
                    )
                elif ref_map[k] != n:
                    problems.append(
                        f"figure numbered {ref_map[k]} in {ref} but {n} in {other} "
                        f"(caption {k!r})"
                    )

    if problems:
        print("\nfigure numbering: the editions do not agree.")
        for p in problems:
            print(f"  {p}")
        config = current_project().path("book.quarto", "docs/_quarto.yml")
        template = current_project().get("book.typst_template")
        where = f"`crossref` in {_named(config)}"
        if template:
            where += f" and the numbering rule in {template}"
        print(
            "  A figure number must name one figure, and the same one, in every "
            f"edition.\n  See {where}."
        )
        return 1

    shown = ", ".join(sorted(reports))
    print(f"✓ figure numbering: {want} figures, identical in {shown}.")
    return 0
