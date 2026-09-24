"""A flat export: one seat's evidence in the shape a reader's directory wants.

The full export mirrors the record, one directory per seat and turn, which
is right for a many-seat run and awkward for the common case of one seat
whose answers are files. Flattening by hand is where a file gets renamed or
dropped, so the tool does it, and refuses rather than overwrite.
"""

import json

import pytest

from quirework.providers import harness
from quirework.runs import engine


def seat(turns):
    return dict(
        id="reader",
        harness="codex",
        model="fixture-model",
        effort="high",
        policy=dict(tools="write"),
        turns=turns,
    )


def finished(tmp_path, monkeypatch, turns, seats=None):
    plan = dict(title="Fictitious read", seats=seats or [seat(turns)])
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    engine.prepare(path, tmp_path / "record", resolve=False)
    run = engine.Run(tmp_path / "record")
    monkeypatch.setattr(harness, "history_image_hashes", lambda *a: [])
    for index, turn in enumerate(turns):
        state = run.state("reader")
        state["pending"] = dict(turn=turn["id"], mode="start", parent_id="sid")
        run.save("reader", state)
        run.record("reader", turn["id"]).mkdir(parents=True)
        for output in turn.get("outputs", []):
            target = run.workspace("reader") / output
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"Notes for {turn['id']} in {output}.\n")
        native = tmp_path / f"native-{index}.jsonl"
        native.write_text("{}\n")
        monkeypatch.setattr(
            harness,
            "receipt",
            lambda *a, n=native, t=turn: dict(
                session_id="sid",
                model="fixture-model",
                rollout=str(n),
                answer=f"Answer to {t['id']}.",
                forked_from=None,
            ),
        )
        run.collect_turn("reader", index)
    run.run_seat("reader")
    return run


def test_answers_are_numbered_by_turn_and_the_receipt_is_one_seat(
    tmp_path, monkeypatch
):
    run = finished(
        tmp_path,
        monkeypatch,
        [dict(id="opening", prompt="Read."), dict(id="wrap", prompt="Sum up.")],
    )
    out = tmp_path / "out"
    run.export(out, flat=True)
    assert sorted(p.name for p in out.iterdir()) == [
        "01-opening.md",
        "02-wrap.md",
        "receipt.json",
    ]
    assert (out / "02-wrap.md").read_text() == "Answer to wrap.\n"
    receipt = json.loads((out / "receipt.json").read_text())
    assert receipt["seat"] == "reader" and "seats" not in receipt
    assert set(receipt["turns"]) >= {"opening", "wrap", "_completeness"}


def test_declared_outputs_keep_their_own_names(tmp_path, monkeypatch):
    run = finished(
        tmp_path,
        monkeypatch,
        [
            dict(
                id="wholebook",
                prompt="Read every chapter.",
                outputs=["drafts/01-opening.md", "drafts/wrap.md"],
            )
        ],
    )
    out = tmp_path / "out"
    run.export(out, flat=True)
    assert sorted(p.name for p in out.iterdir()) == [
        "01-opening.md",
        "receipt.json",
        "wrap.md",
    ]


def test_two_files_with_one_name_are_refused_before_anything_is_written(
    tmp_path, monkeypatch
):
    run = finished(
        tmp_path,
        monkeypatch,
        [
            dict(id="a", prompt="One.", outputs=["drafts/notes.md"]),
            dict(id="b", prompt="Two.", outputs=["drafts/more/notes.md"]),
        ],
    )
    with pytest.raises(ValueError, match="twice"):
        run.export(tmp_path / "out", flat=True)
    assert not (tmp_path / "out").exists()


def test_a_many_seat_record_is_not_flattened(tmp_path):
    other = seat([dict(id="opening", prompt="Read.")]) | {"id": "second"}
    plan = dict(
        title="Two seats", seats=[seat([dict(id="opening", prompt="Read.")]), other]
    )
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    engine.prepare(path, tmp_path / "record", resolve=False)
    with pytest.raises(ValueError, match="one-seat record"):
        engine.Run(tmp_path / "record").export(tmp_path / "out", flat=True)
