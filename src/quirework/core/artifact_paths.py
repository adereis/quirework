"""Placement shared by review-evidence guards and Markdown formatting.

Legacy run roots remain recognized so historical paths keep the same evidence
semantics. Directory names alone never endorse an analysis's conclusions.
"""

RUN_ROOTS = ("analysis/reads", "analysis/rounds", "ideas/reads", "ideas/rounds")
# Where a note's own stamp is the only date it has. `analysis/` is tracked by
# construction, so Git already records when a record landed and against what;
# the untracked legacy and disposable roots have no such record.
DATED_ROOTS = ("ideas/", "scratchpad/")
# Canon marks must contain their own commitments, even when the supporting
# report is retained. This advisory is about authority, not Git durability, so
# it covers retained analysis too and does not track the stamp roots.
NONCANONICAL_ROOTS = ("analysis/", "ideas/", "scratchpad/")


def run_operator_note(path: str) -> bool:
    """Only top-level underscore Markdown notes belong to the operator."""
    parts = path.split("/")
    return (
        len(parts) == 4
        and "/".join(parts[:2]) in RUN_ROOTS
        and parts[3].startswith("_")
        and parts[3].endswith(".md")
    )


def run_evidence(path: str) -> bool:
    """Other files inside a recorded run stay verbatim, at any depth."""
    parts = path.split("/")
    return (
        len(parts) > 3
        and "/".join(parts[:2]) in RUN_ROOTS
        and not run_operator_note(path)
    )
