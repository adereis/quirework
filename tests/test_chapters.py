"""Where the chapter registry looks for a book's metadata sidecars.

The failure this guards is silent by construction: a glob that matches no
files reports no problems, so a check that scans nothing passes and tells the
author the metadata is consistent. Every case below therefore asserts the set
of files found, never just the absence of complaints.
"""

from quirework.book import chapters
from quirework.project import Project, project_scope


def _book(tmp_path, source):
    root = tmp_path / "book"
    marks = root / source / "sections" / "_marks"
    marks.mkdir(parents=True)
    sidecar = marks / "opening.md"
    sidecar.write_text("# The opening {#sec-opening}\n\n<!-- @bind(a-slug) -->\n")
    return root, sidecar


def _project(root, **book):
    return Project.from_settings(root, {"book": book})


def test_sidecars_are_found_under_a_source_not_named_docs(tmp_path):
    root, sidecar = _book(tmp_path, "manuscript")
    project = _project(root, source="manuscript", chapters="manuscript/sections")
    with project_scope(project):
        assert chapters._layer_files() == [sidecar]


def test_a_source_named_docs_resolves(tmp_path):
    root, sidecar = _book(tmp_path, "docs")
    project = _project(root, source="docs", chapters="docs/sections")
    with project_scope(project):
        assert chapters._layer_files() == [sidecar]


def test_a_sidecar_beside_the_front_matter_is_included(tmp_path):
    """A book keeps sidecars at more than one depth under its source."""
    root, sidecar = _book(tmp_path, "manuscript")
    front = root / "manuscript" / "_marks"
    front.mkdir()
    preface = front / "preface.md"
    preface.write_text("# Preface {#sec-preface}\n")
    project = _project(root, source="manuscript", chapters="manuscript/sections")
    with project_scope(project):
        assert chapters._layer_files() == sorted([sidecar, preface])


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
        assert chapters._layer_files() == [other]
        assert sidecar not in chapters._layer_files()
