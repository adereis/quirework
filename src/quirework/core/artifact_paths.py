"""Placement shared by review-evidence guards and Markdown formatting.

A saved run record is evidence: its files are kept exactly as the run wrote
them, so no formatter may reflow them and no advisory may treat them as
authority. Which directories hold those records is the project's choice and
was, until this module read configuration, a fixed list of four directory
names belonging to the one book this tool was extracted from. Every other
project therefore protected paths it does not have while leaving its own
unprotected, and nothing reported either.

Directory names alone never endorse an analysis's conclusions.
"""

from quirework.project import current_project


def run_roots() -> tuple[str, ...]:
    """Project-relative directories holding saved run records.

    `runs.evidence_roots` lists them for a project that keeps several,
    including the legacy locations of records it still reads. A project that
    keeps one names it as `runs.reviews` and needs nothing further. A project
    with neither has no saved records, and nothing is treated as evidence.
    """
    project = current_project()
    declared = project.get("runs.evidence_roots")
    if declared:
        return tuple(str(root).strip("/") for root in declared if str(root).strip("/"))
    reviews = project.get("runs.reviews")
    return (str(reviews).strip("/"),) if reviews else ()


def _within(path: str, root: str) -> tuple[list[str], list[str]] | None:
    """The split path and root when `path` names a file inside a run record."""
    parts = path.split("/")
    segments = root.split("/")
    return (parts, segments) if parts[: len(segments)] == segments else None


def run_operator_note(path: str) -> bool:
    """Only a top-level underscore Markdown note belongs to the operator."""
    for root in run_roots():
        found = _within(path, root)
        if found is None:
            continue
        parts, segments = found
        name = parts[-1]
        if (
            len(parts) == len(segments) + 2
            and name.startswith("_")
            and name.endswith(".md")
        ):
            return True
    return False


def run_evidence(path: str) -> bool:
    """Other files inside a recorded run stay verbatim, at any depth."""
    for root in run_roots():
        found = _within(path, root)
        if found is not None and len(found[0]) > len(found[1]) + 1:
            return not run_operator_note(path)
    return False
