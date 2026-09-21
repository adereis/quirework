"""Chapter titles in the review copy belong to the selected project.

`resolve_ch_refs` turns `@ch(slug)` into a chapter's name for the one block
the author reads, and it caches the lookup because a build resolves it once
per mark. The cache is the hazard: a single process can select more than one
project -- a test suite does, and so does any caller that opens a second book
-- and a cache that ignores the selection hands the second book the first
one's chapter names, silently and in the author-facing output.
"""

from quirework.book import redpen
from quirework.project import Project, project_scope


def _book(root, slug, title):
    source = root / "docs"
    (source / "canon").mkdir(parents=True)
    (source / "_quarto.yml").write_text(f"book:\n  chapters:\n    - canon/{slug}.qmd\n")
    (source / "canon" / f"{slug}.qmd").write_text(f"# {title}\n\nProse.\n")
    return Project.from_settings(
        root,
        {
            "book": {
                "source": "docs",
                "chapters": "docs/canon",
                "quarto": "docs/_quarto.yml",
            }
        },
    )


def test_a_second_project_gets_its_own_chapter_titles(tmp_path):
    first = _book(tmp_path / "first", "the-sidecar", "The sidecar")
    second = _book(tmp_path / "second", "the-sidecar", "A different chapter")

    with project_scope(first):
        assert redpen.resolve_ch_refs("see @ch(the-sidecar)") == "see The sidecar"
    with project_scope(second):
        assert (
            redpen.resolve_ch_refs("see @ch(the-sidecar)") == "see A different chapter"
        )


def test_an_unknown_slug_stays_visible(tmp_path):
    with project_scope(_book(tmp_path / "book", "the-sidecar", "The sidecar")):
        assert "@ch(absent)" in redpen.resolve_ch_refs("see @ch(absent)")
