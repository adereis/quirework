"""Compile explicit model/persona allocations into ordinary shared-run seats."""

import copy
import hashlib
import json
import subprocess
from pathlib import Path

from quirework.sops import personas
from quirework.project import current_project
from quirework.sops import instruments
from quirework.runs.storage import identifier
from quirework.runs.storage import write


def _object(value, required, optional=()):
    if (
        not isinstance(value, dict)
        or not set(required) <= set(value)
        or set(value) - set(required) - set(optional)
    ):
        raise ValueError("expected fields: " + ", ".join(sorted(required)))


def _strings(value, name):
    if (
        not isinstance(value, list)
        or any(not isinstance(v, str) or not v.strip() for v in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{name} must be unique nonempty strings")


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must contain text")


def panel(name, *, root=None):
    if root is None:
        root = current_project().root
    _text(name, "panel id")
    identifier(name)
    path = (
        current_project().path_at(root, "sops.panels", "sops/panels") / f"{name}.json"
    )
    if path.is_symlink():
        raise ValueError("panel source cannot be a symlink")
    raw = path.read_bytes().decode("utf-8")
    data = personas.parse(raw)
    _object(
        data,
        {"schema_version", "id", "revision", "description", "seats", "required_tags"},
        {"forbidden_tags"},
    )
    if (
        type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or data["id"] != name
        or type(data["revision"]) is not int
        or data["revision"] < 1
    ):
        raise ValueError("invalid panel identity")
    if not isinstance(data["description"], str) or not data["description"].strip():
        raise ValueError("panel description must contain text")
    if not isinstance(data["seats"], list) or not data["seats"]:
        raise ValueError("panel needs seats")
    ids, available = set(), set()
    for seat in data["seats"]:
        _object(seat, {"persona", "instrument", "ask", "addenda"})
        _strings(seat["addenda"], "panel addenda")
        _text(seat["instrument"], "panel instrument")
        _text(seat["ask"], "panel ask")
        profile = personas.resolve(seat["persona"], root=root)["profile"]
        if profile["id"] in ids:
            raise ValueError("duplicate panel persona")
        ids.add(profile["id"])
        available.update(profile["tags"])
        instrument = instruments.load(seat["instrument"], root=root)
        for ask in [seat["ask"], *seat["addenda"]]:
            if ask not in instrument["prompts"]:
                raise ValueError(f"unknown panel ask: {ask}")
    _strings(data["required_tags"], "required_tags")
    _strings(data.get("forbidden_tags", []), "forbidden_tags")
    if set(data["required_tags"]) - available:
        raise ValueError("panel requires tags absent from its roster")
    if set(data.get("forbidden_tags", [])) & available:
        raise ValueError("panel roster contains a forbidden perspective")
    return data, hashlib.sha256(raw.encode()).hexdigest()


def check_panels(*, root=None, base=None):
    if root is None:
        root = current_project().root
    root = Path(root)
    values = {
        p.stem: panel(p.stem, root=root)[0]
        for p in sorted(
            (current_project().path_at(root, "sops.panels", "sops/panels")).glob(
                "*.json"
            )
        )
    }
    if base:

        def git(*args):
            return subprocess.run(
                ["git", "-C", str(root), *args],
                check=True,
                capture_output=True,
                text=True,
            ).stdout

        sha = git(
            "rev-parse", "--verify", "--end-of-options", base + "^{commit}"
        ).strip()
        directory = personas._project_relative(
            root, current_project().path_at(root, "sops.panels", "sops/panels")
        )
        paths = set(
            git(
                "ls-tree",
                "-r",
                # Repository-relative on both sides: without this the
                # pathspec and the printed names are relative to the
                # working directory, and a project below the checkout
                # root compares two different spellings of one file.
                "--full-tree",
                "--name-only",
                sha,
                "--",
                personas.git_path(root, directory),
            ).splitlines()
        )
        for name, value in values.items():
            rel = personas.git_path(root, f"{directory}/{name}.json")
            if rel not in paths:
                continue
            old = personas.parse(git("show", f"{sha}:{rel}"))
            changed = any(
                value.get(k, []) != old.get(k, [])
                for k in ("seats", "required_tags", "forbidden_tags")
            )
            if value["revision"] < old["revision"] or (
                changed and value["revision"] <= old["revision"]
            ):
                raise ValueError(f"changed panel needs a new revision: {name}")
    return values


def compile_plan(spec, *, directory, root=None):
    if root is None:
        root = current_project().root
    allowed = {
        "panel",
        "models",
        "personas",
        "assignments",
        "scope",
        "exposure",
        "materials",
        "segments",
        "turns",
        "policy",
        "title",
        "source",
    }
    _object(spec, {"panel", "models"}, allowed - {"panel", "models"})
    if sum(key in spec for key in ("materials", "segments", "turns")) != 1:
        raise ValueError("declare exactly one of materials, segments or turns")
    preset, preset_hash = panel(spec["panel"], root=root)
    scope = spec.get("scope", "artifact")
    if scope not in ("book", "artifact", "passage", "changes"):
        raise ValueError("invalid panel scope")
    models = spec.get("models", [])
    if not isinstance(models, list) or not models:
        raise ValueError("declare a nonempty models list")
    by_model = {}
    for model in models:
        _object(model, {"id", "harness", "model"}, {"effort", "mode"})
        for key, value in model.items():
            _text(value, f"model {key}")
        key = identifier(model["id"])
        if key in by_model:
            raise ValueError("duplicate model allocation id")
        by_model[key] = {k: v for k, v in model.items() if k != "id"}
        by_model[key].setdefault("effort", "xhigh")
    definitions = {
        personas.resolve(s["persona"], root=root)["profile"]["id"]: s
        for s in preset["seats"]
    }
    selected = spec.get("personas", list(definitions))
    if not isinstance(selected, list) or not selected:
        raise ValueError("select at least one persona")
    resolved = {}
    for ref in selected:
        profile = personas.resolve(ref, root=root)
        key = profile["profile"]["id"]
        if key in resolved:
            raise ValueError("duplicate selected persona")
        resolved[key] = profile
    assignments = spec.get("assignments")
    if assignments is None:
        assignments = [{"persona": p, "model": m} for p in resolved for m in by_model]
    if not isinstance(assignments, list) or not assignments:
        raise ValueError("assignments must be a nonempty list")
    active = set()
    pairs = set()
    for assignment in assignments:
        _object(assignment, {"persona", "model"})
        p, m = assignment["persona"], assignment["model"]
        _text(p, "assignment persona")
        _text(m, "assignment model")
        if p not in resolved or m not in by_model:
            raise ValueError("assignment references an unselected persona or model")
        if (p, m) in pairs:
            raise ValueError("duplicate model/persona assignment")
        pairs.add((p, m))
        active.update(resolved[p]["profile"]["tags"])
    if set(preset["required_tags"]) - active:
        raise ValueError(
            "panel selection is missing required perspectives: "
            + ", ".join(sorted(set(preset["required_tags"]) - active))
        )
    if set(preset.get("forbidden_tags", [])) & active:
        raise ValueError("panel selection contains a forbidden perspective")

    def material_paths(rows):
        if not isinstance(rows, list):
            raise ValueError("materials must be a list")
        result = copy.deepcopy(rows)
        for row in result:
            _object(row, {"source", "delivery"}, {"label"})
            for key, value in row.items():
                _text(value, f"material {key}")
            path = Path(row["source"]).expanduser()
            row["source"] = str(path if path.is_absolute() else Path(directory) / path)
        return result

    seats = []
    for assignment in assignments:
        p, m = assignment["persona"], assignment["model"]
        definition = definitions.get(p)
        if definition is None and "turns" not in spec:
            raise ValueError("a custom persona needs explicit turns")
        instrument_name = definition["instrument"] if definition else None
        ask = definition["ask"] if definition else None
        addenda = definition["addenda"] if definition else []
        instrument = (
            instruments.load(instrument_name, root=root) if instrument_name else None
        )
        variant = (instrument or {}).get("scope_variants", {}).get(scope)
        if variant:
            instrument_name, ask = variant["instrument"], variant["ask"]
            instrument = instruments.load(instrument_name, root=root)
        ask = (instrument or {}).get("scope_asks", {}).get(scope, ask)
        if "turns" in spec:
            turns = copy.deepcopy(spec["turns"])
        elif spec.get("segments"):
            sequence = (instrument or {}).get("sequence")
            if not sequence:
                raise ValueError(
                    "instrument has no segment sequence; supply its complete artifact or explicit turns"
                )
            turns = []
            for i, segment in enumerate(spec["segments"]):
                _object(segment, {"id", "materials"})
                current = sequence["first" if i == 0 else "next"]
                turns.append(
                    {
                        "id": segment["id"],
                        "ask": current,
                        "addenda": addenda if i == 0 else [],
                        "materials": segment["materials"],
                    }
                )
            if scope == "book" and sequence.get("wrap"):
                turns.append({"id": "wrap", "ask": sequence["wrap"]})
        else:
            if scope == "book" and (instrument or {}).get("sequence"):
                raise ValueError(
                    "a progressive whole-book read requires segments or explicit turns"
                )
            turns = [
                {
                    "id": "review",
                    "ask": ask,
                    "addenda": addenda,
                    "materials": spec.get("materials", []),
                }
            ]
            if instrument and instrument.get("followup_ask"):
                turns[0]["addenda"] = []
                turns.append(
                    {
                        "id": "critique",
                        "ask": instrument["followup_ask"],
                        "addenda": addenda,
                    }
                )
        if (
            not isinstance(turns, list)
            or not turns
            or any(not isinstance(t, dict) for t in turns)
        ):
            raise ValueError("panel needs turns")
        for turn in turns:
            turn["materials"] = material_paths(turn.get("materials", []))
        if not any(t.get("materials") for t in turns):
            raise ValueError("panel review requires supplied material")
        if scope == "changes":
            source = spec.get("source", {})
            if not isinstance(source, dict) or any(
                not isinstance(source.get(k), str) or not source[k].strip()
                for k in ("base", "target")
            ):
                raise ValueError(
                    "change review requires source base and target identities"
                )
            labels = [
                r.get("label", "").lower()
                for t in turns
                for r in t.get("materials", [])
            ]
            if not all(
                any(label.startswith(version) for label in labels)
                for version in ("before", "after")
            ):
                raise ValueError(
                    "change material labels must identify Before and After"
                )
        for i, turn in enumerate(turns):
            instruments.prompt(
                turn, instrument, persona=resolved[p], first=i == 0, scope=scope
            )
        seat = {
            "id": identifier(f"{p}--{m}"),
            **by_model[m],
            "persona": {"frozen": resolved[p]},
            "scope": scope,
            "exposure": spec.get("exposure", "packet"),
            "policy": spec.get("policy", {}),
            "turns": turns,
        }
        if instrument_name:
            seat["instrument"] = instrument_name
        seats.append(seat)
    return {
        "title": spec.get("title", preset["description"]),
        "source": spec.get("source", {}),
        "seats": seats,
        "metadata": {
            "panel": preset,
            "panel_sha256": preset_hash,
            "assignments": assignments,
        },
    }


def prepare_plan(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("plan output exists")
    plan = compile_plan(personas.parse(source.read_text()), directory=source.parent)
    write(output, plan)
    return {
        "plan": str(output),
        "seats": len(plan["seats"]),
        "turns": sum(len(s["turns"]) for s in plan["seats"]),
    }


def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(
        prog="quirework sop panel",
        description="Compile a declared panel into a run plan.",
    )
    p.add_argument("spec")
    p.add_argument("output")
    args = p.parse_args(argv)
    print(prepare_plan(args.spec, args.output))
    return 0
