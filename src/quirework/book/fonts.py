"""The book's font dependencies, declared once and checkable.

Fonts are a build dependency the way Quarto is, and they are the one
dependency that fails SILENTLY. A missing Python package raises; a missing
typeface does not. Typst walks its family list and takes the next one it
finds, and rsvg substitutes for an absent SVG family without a word, so a
machine that is merely missing a package renders a whole, plausible,
*wrong* book, and nothing in the build says so. That is why this module
exists and why `render` refuses rather than warns.

Nothing is declared here twice. The families are READ from the files that
already name them -- `docs/typst-show.typ` for the book's text and display
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

    TEMPLATE = current_project().path("book.typst_template", "docs/typst-show.typ")
    ILLUSTRATIONS = current_project().path("book.figures", "docs/canon/illustrations")
    return SimpleNamespace(ILLUSTRATIONS=ILLUSTRATIONS, TEMPLATE=TEMPLATE)


def _families_in_template() -> list[str]:
    """The text and display faces, from the Typst template that names them."""
    try:
        src = _settings().TEMPLATE.read_text(encoding="utf-8")
    except FileNotFoundError:
        if current_project().get("book.typst_template") is not None:
            raise
        return []
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


def _family_in_figures() -> list[str]:
    """The family the illustrations are lettered in, from the SVGs themselves."""
    for svg in sorted(_settings().ILLUSTRATIONS.glob("*.svg")):
        m = re.search(r'font-family="\'?([^",\']+)', svg.read_text(encoding="utf-8"))
        if m:
            return [m.group(1)]
    return []


def required() -> list[tuple[str, str]]:
    """(family, consumer) for everything the build needs, in the order asked."""
    seen: dict[str, str] = {}
    for fam in _families_in_template():
        seen.setdefault(fam, "typst")
    for fam in _family_in_figures():
        seen.setdefault(fam, "rsvg")
    return list(seen.items())


def available(family: str, consumer: str) -> bool:
    """Whether `consumer` will actually find `family` on this machine."""
    if consumer == "typst" and family in TYPST_BUNDLED:
        return True
    return installed(family)


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


def missing() -> list[tuple[str, str]]:
    return [(f, c) for f, c in required() if not available(f, c)]


def install_hint(families: list[str]) -> str:  # noqa: D401
    known = [FEDORA_PACKAGES[f] for f in families if f in FEDORA_PACKAGES]
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


def report(as_check: bool) -> int:
    fams = required()
    if not fams:
        print("fonts: no custom families declared; using renderer defaults")
        return 0
    absent = missing()
    if not absent:
        shown = ", ".join(
            f"{f} ({'bundled with typst' if c == 'typst' and f in TYPST_BUNDLED else c})"
            for f, c in fams
        )
        print(f"fonts OK ({len(fams)} required: {shown})")
        return 0
    print(
        f"\n✗ fonts: {len(absent)} required family/families not installed:",
        file=sys.stderr,
    )
    for f, c in absent:
        print(f"    {f}   (needed by {c})", file=sys.stderr)
    print(
        "  Nothing will fail loudly without them: Typst takes the next family "
        "in the\n  stack and rsvg substitutes, so the book renders whole and "
        "in the wrong face.",
        file=sys.stderr,
    )
    print(install_hint([f for f, _ in absent]), file=sys.stderr)
    return 1 if as_check else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="quirework book fonts",
        description="The typefaces the build needs, and whether they are "
        "installed. Read from the files that name them "
        "(docs/typst-show.typ and the figure SVGs), never "
        "declared twice.",
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
