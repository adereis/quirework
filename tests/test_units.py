"""Where the unit registry looks for a manuscript's metadata sidecars.

The failure this guards is silent by construction: a glob that matches no
files reports no problems, so a check that scans nothing passes and tells the
author the metadata is consistent. Every case below therefore asserts the set
of files found, never just the absence of complaints.
"""

from quirework.manuscript import units
from quirework.project import Project, project_scope


def _book(tmp_path, source):
    root = tmp_path / "book"
    marks = root / source / "sections" / "_marks"
    marks.mkdir(parents=True)
    sidecar = marks / "opening.md"
    sidecar.write_text("# The opening {#sec-opening}\n\n<!-- @bind(a-slug) -->\n")
    return root, sidecar


def _project(root, **book):
    return Project.from_settings(root, {"manuscript": book})


def test_sidecars_are_found_under_a_source_not_named_docs(tmp_path):
    root, sidecar = _book(tmp_path, "manuscript")
    project = _project(root, source="manuscript", chapters="manuscript/sections")
    with project_scope(project):
        assert units.layer_files() == [sidecar]


def test_a_source_named_docs_resolves(tmp_path):
    root, sidecar = _book(tmp_path, "docs")
    project = _project(root, source="docs", chapters="docs/sections")
    with project_scope(project):
        assert units.layer_files() == [sidecar]


def test_a_sidecar_beside_the_front_matter_is_included(tmp_path):
    """A book keeps sidecars at more than one depth under its source."""
    root, sidecar = _book(tmp_path, "manuscript")
    front = root / "manuscript" / "_marks"
    front.mkdir()
    preface = front / "preface.md"
    preface.write_text("# Preface {#sec-preface}\n")
    project = _project(root, source="manuscript", chapters="manuscript/sections")
    with project_scope(project):
        assert units.layer_files() == sorted([sidecar, preface])


def test_a_declared_policy_still_wins(tmp_path):
    root, sidecar = _book(tmp_path, "manuscript")
    other = root / "notes.md"
    other.write_text("a layer the project names for itself\n")
    policy = root / "chapters.toml"
    policy.write_text('metadata_layers = ["notes.md"]\n')
    project = _project(
        root,
        source="manuscript",
        chapters="manuscript/sections",
        chapter_policy="chapters.toml",
    )
    with project_scope(project):
        assert units.layer_files() == [other]
        assert sidecar not in units.layer_files()


def test_a_declared_policy_needs_no_book_source(tmp_path):
    """The default glob is derived from `manuscript.source`, and a project that
    declares its own layers never uses it. Computing it anyway made the
    declared answer depend on a setting it does not read, so a tool-only
    project asking which layers to scan failed instead of being told."""
    root = tmp_path / "book"
    root.mkdir()
    (root / "notes.md").write_text("a layer the project names for itself\n")
    policy = root / "chapters.toml"
    policy.write_text('metadata_layers = ["notes.md"]\n')
    project = _project(root, chapter_policy="chapters.toml")
    with project_scope(project):
        assert units.layers() == ("notes.md",)


# --------------------------------------------------------------------------- #
# spelled-out chapter counts
# --------------------------------------------------------------------------- #
def _counted_book(tmp_path, marks=""):
    """Two numbered chapters about a notebook that has eleven of its own."""
    root = tmp_path / "counted"
    sections = root / "manuscript" / "sections"
    (sections / "_marks").mkdir(parents=True)
    (root / "manuscript" / "_quarto.yml").write_text(
        "project:\n  type: book\nbook:\n  chapters:\n"
        "    - sections/opening.qmd\n    - sections/closing.qmd\n"
    )
    (sections / "opening.qmd").write_text(
        "# The opening {#sec-opening}\n\n"
        "The old notebook has eleven chapters.\n"
        "This one will have twelve chapters, eventually.\n"
    )
    (sections / "closing.qmd").write_text(
        "# The closing {#sec-closing}\n\nThe notebook still has eleven chapters.\n"
    )
    (sections / "_marks" / "opening.md").write_text(
        f"# The opening {{#sec-opening}}\n\n{marks}"
    )
    return _project(
        root,
        source="manuscript",
        quarto="manuscript/_quarto.yml",
        chapters="manuscript/sections",
    )


def test_a_count_out_of_step_with_the_book_is_reported(tmp_path):
    with project_scope(_counted_book(tmp_path)):
        found = units.count_advisory()
    assert len(found) == 3
    assert all("the book has 2 numbered chapters" in line for line in found)


def test_a_count_marked_as_another_books_is_not_compared(tmp_path):
    """The mark excuses its phrase in its own unit, and nowhere else."""
    marks = "<!-- @count(eleven chapters)  the old notebook's, not this one's -->\n"
    with project_scope(_counted_book(tmp_path, marks)):
        found = units.count_advisory()
        assert units.stale_count_marks() == []
    assert [line.split(":")[0].strip() for line in found] == [
        "manuscript/sections/opening.qmd",
        "manuscript/sections/closing.qmd",
    ]
    assert "twelve chapters" in found[0] and "eleven chapters" in found[1]


def test_a_count_mark_its_unit_no_longer_says_is_reported(tmp_path):
    marks = "<!-- @count(nineteen chapters)  a count that was cut -->\n"
    with project_scope(_counted_book(tmp_path, marks)):
        stale = units.stale_count_marks()
    assert len(stale) == 1
    assert "@count(nineteen chapters) is not in opening" in stale[0]
