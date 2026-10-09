"""Where a derived edition lives, and how it renders as if it were the book.

An edition is one directory per kind under `publish.variants`. The blind
drafts that `publish variant build` fetches land in its `_raw/`, which is
evidence of what a model wrote and never a source. The author composes the
edition beside them: its documents, its own `_quarto.yml`, and their marks
in `_marks/`. An edition with a `_quarto.yml` is composed, and
`publish build` renders it.

Rendering reuses the book's pipeline whole. The edition's view of the
project moves the manuscript's source, chapters and Quarto file to the
edition's directory, and its build tree to `build/variants/KIND/` under the
book's. Everything else is the book's own: the figure gallery, the fonts
and the render policies. So an edition renders from staged copies as the
book does, never in its own directory, where Quarto would leave a cache
behind, and its figures come from the same sources as the book's.

An edition keeps its documents at its top level, beside its `_quarto.yml`,
because that is where figures are looked for: an embed is
`illustrations/NAME.svg` relative to the document, as in the book.
"""

from __future__ import annotations

from quirework.core.artifact_paths import EDITION_DRAFTS
from quirework.manuscript.editions import composed, root  # noqa: F401
from quirework.manuscript.quarto import BOOK_CONFIG
from quirework.project import current_project

# An edition's blind drafts: what each model wrote, kept as evidence. Never
# staged, reflowed or read as a source.
RAW = EDITION_DRAFTS


def project(kind: str):
    """The project as the edition ``kind`` renders: see the module docstring."""
    book = current_project()
    folder = root() / kind
    return book.replaced(
        {
            "manuscript.source": str(folder),
            "manuscript.chapters": str(folder),
            "manuscript.quarto": str(folder / BOOK_CONFIG),
            "publish.build": str(
                book.path("publish.build", "build") / "variants" / kind
            ),
        }
    )
