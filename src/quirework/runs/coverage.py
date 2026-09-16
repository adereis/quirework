"""Check a declared population against a model's disposition ledger.

This validates identity, structure and completeness, never the judgments.
Inputs remain unchanged; malformed JSON is an error, not a repair request.
"""

import json
from collections import Counter
from pathlib import Path


def rows(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        values = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                values.append(json.loads(line))
            except ValueError as exc:
                raise ValueError(f"{path}:{number}: {exc}") from exc
    else:
        values = json.loads(text)
    if not isinstance(values, list):
        raise ValueError(f"{path}: expected a JSON array or .jsonl rows")
    return values


def check(population, ledger, *, partial=False, statuses=None, require=()):
    expected_rows = rows(population)
    expected = []
    for number, row in enumerate(expected_rows, 1):
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not row["id"].strip()
        ):
            raise ValueError(
                f"population row {number}: a nonempty string id is required"
            )
        expected.append(row["id"])
    if len(expected) != len(set(expected)):
        raise ValueError("population contains duplicate ids")
    observed_rows = rows(ledger)
    observed = []
    errors = []
    counts = Counter()
    for number, row in enumerate(observed_rows, 1):
        if not isinstance(row, dict):
            errors.append(f"ledger row {number}: expected an object")
            continue
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            errors.append(f"ledger row {number}: a nonempty string id is required")
            continue
        observed.append(identifier)
        status = row.get("status")
        if not isinstance(status, str) or not status.strip():
            errors.append(f"{identifier}: a nonempty string status is required")
        elif statuses is not None and status not in statuses:
            errors.append(f"{identifier}: unsupported status {status!r}")
        else:
            counts[status] += 1
        for field in require:
            if not isinstance(row.get(field), str) or not row[field].strip():
                errors.append(f"{identifier}: nonempty string {field!r} is required")
    duplicates = sorted(key for key, count in Counter(observed).items() if count > 1)
    observed_ids, expected_ids = set(observed), set(expected)
    unknown = sorted(observed_ids - expected_ids)
    missing = [key for key in expected if key not in observed_ids]
    structurally_valid = not (errors or duplicates or unknown)
    return {
        "valid": structurally_valid and (partial or not missing),
        "complete": structurally_valid and not missing,
        "expected": len(expected),
        "rows": len(observed_rows),
        "covered": len(observed_ids & expected_ids),
        "statuses": dict(sorted(counts.items())),
        "missing": missing,
        "unknown": unknown,
        "duplicates": duplicates,
        "errors": errors,
        "judgments_verified": False,
    }
