"""Odd folios on right-hand pages, read back from the built PDF.

A printed book puts every odd page number on a right-hand page. A PDF
viewer shows one page at a time, so nothing on screen shows a violation,
and the arabic count of a whole body flips when one page before it is
gained or lost: a contents page that grows by two lines can put every odd
folio of a book on the left. So the render reads the side back from the
artifact rather than trusting the layout.

Typst writes each page's number into the PDF as that page's label, which
is what makes the check exact: no text is scraped, and a page whose folio
is not printed (a chapter opening) still carries its number. Wherever a
label is an arabic number, its parity must match the page's place in the
file, the first page being a right-hand one. Roman and empty labels are
left alone, since front matter and unnumbered pages carry no arabic folio.

A book opts in with `folio_sides = true` in its render policy. A PDF made
for screens has no sides, and a setting describes nothing until declared.
"""

from __future__ import annotations

import sys
from pathlib import Path


def wrong_sides(labels: list[str]) -> list[tuple[int, str]]:
    """`(page, label)` for every arabic label on the wrong side of the spread.

    `labels` are the PDF's page labels in file order; page numbers in the
    result count from 1, and page 1 is a right-hand page."""
    return [
        (i, label)
        for i, label in enumerate(labels, start=1)
        if label.isdigit() and int(label) % 2 != i % 2
    ]


def verify(site: Path) -> int:
    """Check every PDF in `site`; return 1 if any has a folio on the wrong side."""
    import pypdf

    failed = False
    for pdf in sorted(site.glob("*.pdf")):
        labels = list(pypdf.PdfReader(str(pdf)).page_labels)
        wrong = wrong_sides(labels)
        arabic = sum(label.isdigit() for label in labels)
        if wrong:
            failed = True
            shown = ", ".join(
                f"page {page} is folio {label}" for page, label in wrong[:6]
            )
            more = f" and {len(wrong) - 6} more" if len(wrong) > 6 else ""
            print(
                f"folios: {pdf.name}: {len(wrong)} of {arabic} arabic folios fall on "
                f"the wrong side ({shown}{more}). Odd folios belong on right-hand "
                "pages; look at what moved the body's first page.",
                file=sys.stderr,
            )
        else:
            print(f"folios: {pdf.name}: all {arabic} arabic folios on their side")
    return 1 if failed else 0
