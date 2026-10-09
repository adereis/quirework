"""The book's font dependencies, declared once and checkable.

Fonts are a build dependency the way Quarto is, and they are the one
dependency that fails SILENTLY. A missing Python package raises; a missing
typeface does not. Typst walks its family list and takes the next one it
finds, and rsvg substitutes for an absent SVG family without a word, so a
machine that is merely missing a package renders a whole, plausible,
*wrong* book, and nothing in the build says so. That is why this module
exists and why `render` refuses rather than warns.

Nothing is declared here twice. The families are READ from the files that
already name them -- the configured Typst template for text and display
faces, and the figure SVGs for the one the illustrations are lettered in --
so a face swap is still the single edit it was designed to be, and this
check follows it automatically. `figures.print_text_block_in()` reads the
same template for the same reason.

Availability is asked of fontconfig rather than of a package manager: the
question is whether the renderer will find the family, not whether some RPM
is installed. But WHICH renderer matters, so each family is carried with its
consumer. Typst embeds a handful of families in the binary and finds them
with no system font at all -- Libertinus Serif among them, which is what the
book is set in today -- so asking fontconfig about those reports a missing
font on a machine that renders the PDF perfectly. rsvg, which letters the
figures, has no such bundle and needs the real thing.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys

from quirework.project import current_project


# Families Typst carries inside its own binary. It resolves these before it
# ever reaches the system, so fontconfig not knowing them means nothing for the
# PDF -- and asking anyway reports a missing font on a machine that renders
# fine. They are NOT available to rsvg or to a browser.
TYPST_BUNDLED = frozenset(
    {
        "Libertinus Serif",
        "Libertinus Math",
        "New Computer Modern",
        "New Computer Modern Math",
        "DejaVu Sans Mono",
    }
)

# Package names for the distribution this is developed on. Everything here is
# libre and carries the same family name everywhere, so on another system the
# family name IS the instruction; the hint is a convenience, not the
# declaration. Keyed by family, so an unknown family degrades to "install it
# yourself" rather than to a wrong package name.
FEDORA_PACKAGES = {
    "Literata": "typetogether-literata-fonts",
    "Spectral": "production-type-spectral-fonts",
    "Source Serif 4": "adobe-source-serif-pro-fonts",
    "Libertinus Serif": "libertinus-fonts",
    "Liberation Sans": "liberation-sans-fonts",
    "DejaVu Sans": "dejavu-sans-fonts",
}


def _settings():
    """Resolve paths from the current operation, without import-time state."""
    from types import SimpleNamespace

    TEMPLATE = (
        current_project().path("publish.typst_template")
        if current_project().get("publish.typst_template") is not None
        else None
    )
    ILLUSTRATIONS = (
        current_project().path("publish.figures")
        if current_project().get("publish.figures") is not None
        else None
    )
    return SimpleNamespace(ILLUSTRATIONS=ILLUSTRATIONS, TEMPLATE=TEMPLATE)


def _families_in_template() -> list[str]:
    """The text and display faces, from the Typst template that names them."""
    if _settings().TEMPLATE is None:
        return []
    try:
        src = _settings().TEMPLATE.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    out: list[str] = []
    for var in ("text-face", "display-face"):
        m = re.search(rf"#let {var}\s*=\s*\(([^)]*)\)", src)
        if not m:
            continue
        # Only the FIRST family in each stack is required: the rest are the
        # fallbacks that exist so a clone without the licensed face still
        # renders, and requiring them would defeat the point of having them.
        names = re.findall(r'"([^"]+)"', m.group(1))
        if names:
            out.append(names[0])
    return out


# CSS's generic families are a renderer instruction, not a typeface: whatever
# `sans-serif` resolves to is installed by definition. A plate lettered in one
# of them was failing the gate with "sans-serif not installed", which is both
# unfixable and untrue, and it is what a first hand-authored SVG usually says.
CSS_GENERIC = {
    "cursive",
    "emoji",
    "fangsong",
    "fantasy",
    "math",
    "monospace",
    "sans-serif",
    "serif",
    "system-ui",
    "ui-monospace",
    "ui-rounded",
    "ui-sans-serif",
    "ui-serif",
}


_FONT_FAMILY = re.compile(r'font-family\s*[=:]\s*"?([^";]+)"?')


def _stacks_in_figures() -> list[tuple[str, ...]]:
    """Every font stack the illustrations letter in, from the SVGs themselves.

    A stack is what the author wrote: `Georgia, 'Times New Roman', serif` asks
    for Georgia and says what to do without it. Reading only the first name
    refused to render on a machine that had every fallback, which turns a
    deliberate fallback chain into a hard dependency on one face. Reading only
    the first *figure* was the other half: a second plate lettered in another
    family was never checked at all.
    """
    stacks: dict[tuple[str, ...], None] = {}
    gallery = _settings().ILLUSTRATIONS
    for svg in sorted(gallery.glob("*.svg")) if gallery is not None else []:
        for match in _FONT_FAMILY.finditer(svg.read_text(encoding="utf-8")):
            names = tuple(
                name
                for name in (
                    part.strip().strip("'\"") for part in match.group(1).split(",")
                )
                if name and name.lower() not in CSS_GENERIC
            )
            if names:
                stacks.setdefault(names, None)
    return list(stacks)


def required() -> list[tuple[tuple[str, ...], str]]:
    """(font stack, consumer) for everything the build needs, in the order asked.

    A stack is satisfied by any one of its families. The template's stacks are
    the exception and are carried one family wide on purpose: there the first
    name is the face the book is set in, and rendering in its fallback is the
    silent substitution this module exists to refuse.
    """
    seen: dict[tuple[str, ...], str] = {}
    for fam in _families_in_template():
        seen.setdefault((fam,), "typst")
    for stack in _stacks_in_figures():
        seen.setdefault(stack, "rsvg")
    return list(seen.items())


def available(stack: tuple[str, ...], consumer: str) -> bool:
    """Whether `consumer` will find any family in `stack` on this machine."""
    return any(
        (consumer == "typst" and family in TYPST_BUNDLED) or installed(family)
        for family in stack
    )


def installed(family: str) -> bool:
    """Whether fontconfig resolves `family` to that family (not a fallback).

    `fc-match` always answers with something, so the name it returns has to be
    compared: asking for an absent family and being handed DejaVu is exactly
    the silent substitution this module exists to catch.
    """
    if not shutil.which("fc-match"):
        raise RuntimeError("fontconfig is required to verify declared typefaces")
    got = subprocess.run(
        ["fc-match", "-f", "%{family}", family],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout
    return family.lower() in got.lower()


# Which build step resolves a typeface, by output format. A format absent here
# resolves its fonts on the reader's machine rather than this one: the SVG in
# an HTML page or an EPUB is lettered by the browser or the reading system, so
# refusing to render it here answers a question about somebody else's computer.
# Only the formats rasterized or typeset at build time depend on what is
# installed now.
# The print interior is declared as `typst`, which is the name that reaches
# this table; `pdf` is Quarto's LaTeX route to the same paper. Keying this on
# "pdf" alone silently exempted the one format the whole gate exists for.
FORMAT_CONSUMERS = {
    "typst": ("typst", "rsvg"),
    "pdf": ("rsvg",),
    "docx": ("rsvg",),
}


def missing(formats=None) -> list[tuple[tuple[str, ...], str]]:
    """Unsatisfied requirements, for the formats about to be rendered.

    Passing no formats asks about every consumer, which is what
    `quirework publish fonts` reports.
    """
    if formats is None:
        wanted = None
    else:
        wanted = {c for f in formats for c in FORMAT_CONSUMERS.get(f, ())}
    return [
        (stack, consumer)
        for stack, consumer in required()
        if (wanted is None or consumer in wanted) and not available(stack, consumer)
    ]


def install_hint(families: list[str]) -> str:  # noqa: D401
    known = sorted({FEDORA_PACKAGES[f] for f in families if f in FEDORA_PACKAGES})
    if not known:
        return (
            "  Install the families above from your distribution or from the foundry."
        )
    return (
        "  On Fedora:\n"
        f"      sudo dnf install {' '.join(sorted(known))}\n"
        "  Elsewhere the family names above are the instruction: each is "
        "libre and\n  carries the same name on every system."
    )


def _shown(stack: tuple[str, ...], consumer: str) -> str:
    """One requirement, naming the family that will actually be used."""
    for family in stack:
        if consumer == "typst" and family in TYPST_BUNDLED:
            return f"{family} (bundled with typst)"
        if installed(family):
            head = f"{family} ({consumer})"
            return head if family == stack[0] else f"{head}, standing in for {stack[0]}"
    return f"{' or '.join(stack)} ({consumer})"


def report(as_check: bool) -> int:
    fams = required()
    if not fams:
        print("fonts: no custom families declared; using renderer defaults")
        return 0
    absent = missing()
    if not absent:
        shown = ", ".join(_shown(stack, consumer) for stack, consumer in fams)
        print(f"fonts OK ({len(fams)} required: {shown})")
        return 0
    print(
        f"\n✗ fonts: {len(absent)} required family/families not installed:",
        file=sys.stderr,
    )
    for stack, c in absent:
        print(f"    {' or '.join(stack)}   (needed by {c})", file=sys.stderr)
    print(
        "  Nothing will fail loudly without them: Typst takes the next family "
        "in the\n  stack and rsvg substitutes, so the book renders whole and "
        "in the wrong face.",
        file=sys.stderr,
    )
    print(
        install_hint([family for stack, _ in absent for family in stack]),
        file=sys.stderr,
    )
    return 1 if as_check else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="quirework publish fonts",
        description="The typefaces the build needs, and whether they are "
        "installed. Read from the files that already name them, the Typst "
        "template and the figure sources, never declared twice.",
        epilog="Exit 1 with --check if a family is missing. Fonts fail "
        "silently by substitution, which is why the render refuses "
        "rather than warns.",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if a required family is missing",
    )
    args = ap.parse_args(argv)
    return report(as_check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
