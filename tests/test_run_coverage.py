from conftest import project_at, patch_setting
from quirework.runs import commands as run_commands
import json

import pytest

from quirework.runs import coverage
from quirework.runs import engine


def population(tmp_path):
    path = tmp_path / "population.json"
    path.write_text(json.dumps([{"id": "C01"}, {"id": "C02"}]))
    return path


def test_equal_row_count_cannot_hide_duplicate_and_missing_ids(tmp_path):
    p = population(tmp_path)
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps([{"id": "C01", "status": "keep"}] * 2))
    result = coverage.check(p, ledger)
    assert not result["valid"]
    assert result["duplicates"] == ["C01"]
    assert result["missing"] == ["C02"]


def test_partial_progress_is_valid_but_not_complete(tmp_path):
    p = population(tmp_path)
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text('{"id":"C01","status":"keep","reason":"Fictional frame."}\n')
    result = coverage.check(
        p, ledger, partial=True, statuses=["keep"], require=["reason"]
    )
    assert result["valid"] and not result["complete"]
    assert not coverage.check(p, ledger)["valid"]
    with ledger.open("a") as f:
        f.write('{"id":"C02","status":"finding","reason":"Conflicting statement."}\n')
    result = coverage.check(p, ledger, statuses=["keep", "finding"], require=["reason"])
    assert result["complete"] and result["covered"] == 2
    assert result["judgments_verified"] is False


@pytest.mark.parametrize(
    "row,field",
    [
        ({"id": "C99", "status": "keep"}, "unknown"),
        ({"id": "C01", "status": "maybe"}, "errors"),
        ({"id": "C01", "status": "keep", "reason": ""}, "errors"),
        ({"id": 3, "status": "keep", "reason": "Example"}, "errors"),
    ],
)
def test_invalid_rows_do_not_pass_even_during_progress(tmp_path, row, field):
    p = population(tmp_path)
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps([row]))
    result = coverage.check(
        p, ledger, partial=True, statuses=["keep"], require=["reason"]
    )
    assert not result["valid"] and result[field]


def test_concatenated_json_is_rejected_without_modifying_evidence(tmp_path):
    p = population(tmp_path)
    ledger = tmp_path / "ledger.json"
    original = '[]\n[{"id":"C01","status":"keep"}]\n'
    ledger.write_text(original)
    with pytest.raises(ValueError, match="Extra data"):
        coverage.check(p, ledger)
    assert ledger.read_text() == original


def test_duplicate_population_ids_are_rejected(tmp_path):
    p = population(tmp_path)
    p.write_text('[{"id":"C01"},{"id":"C01"}]')
    with pytest.raises(ValueError, match="population contains duplicate"):
        coverage.check(p, p)


def test_cli_reports_incomplete_coverage_as_failure(tmp_path, capsys):
    p = population(tmp_path)
    ledger = tmp_path / "ledger.json"
    ledger.write_text("[]")
    assert run_commands.main(["check-coverage", str(p), str(ledger)]) == 1
    assert json.loads(capsys.readouterr().out)["missing"] == ["C01", "C02"]
