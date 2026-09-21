"""Which paths hold saved run records, for a project that says so.

A file inside a run record is kept exactly as the run wrote it. The formatter
skips it and the mark advisories do not treat it as authority, so a project
whose records are invisible here has them quietly reflowed, and a project whose
declared roots do not exist protects nothing while reporting success.
"""

from quirework.core import artifact_paths
from quirework.project import Project, project_scope


def _project(**settings):
    return Project.from_settings("/nowhere", settings)


def test_a_project_with_one_review_directory_needs_no_list():
    with project_scope(_project(runs={"reviews": ".quirework/reviews"})):
        assert artifact_paths.run_roots() == (".quirework/reviews",)
        assert artifact_paths.run_evidence(".quirework/reviews/a-read/seat/answer.md")
        assert not artifact_paths.run_evidence("manuscript/sections/opening.qmd")


def test_several_roots_are_declared_together():
    with project_scope(
        _project(
            runs={"reviews": "reviews", "evidence_roots": ["reviews", "old/rounds"]}
        )
    ):
        assert artifact_paths.run_evidence("old/rounds/a-read/seat/answer.md")
        assert artifact_paths.run_evidence("reviews/a-read/seat/answer.md")


def test_a_root_may_sit_at_any_depth():
    with project_scope(_project(runs={"reviews": "private/reviews/rounds"})):
        assert artifact_paths.run_evidence("private/reviews/rounds/a-read/answer.md")
        assert not artifact_paths.run_evidence("private/reviews/notes.md")


def test_the_operator_note_at_the_top_of_a_record_is_not_evidence():
    """The operator's own summary is written by hand and formatted like prose."""
    with project_scope(_project(runs={"reviews": ".quirework/reviews"})):
        note = ".quirework/reviews/a-read/_summary.md"
        assert artifact_paths.run_operator_note(note)
        assert not artifact_paths.run_evidence(note)
        deeper = ".quirework/reviews/a-read/seat/_notes.md"
        assert not artifact_paths.run_operator_note(deeper)
        assert artifact_paths.run_evidence(deeper)


def test_a_project_that_declares_nothing_has_no_evidence_paths():
    with project_scope(_project()):
        assert artifact_paths.run_roots() == ()
        assert not artifact_paths.run_evidence(".quirework/reviews/a-read/answer.md")
