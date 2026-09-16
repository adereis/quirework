"""Small preparation helpers over existing artifact formats, not a renderer."""

from pathlib import Path
from quirework.project import current_project

from quirework.runs.storage import digest
from quirework.runs.storage import read
from quirework.runs.storage import write


def book(
    kit,
    output,
    *,
    harness,
    model,
    effort="xhigh",
    seat=None,
    delivery="pages",
    persona=None,
    instrument=None,
):
    kit = Path(kit).expanduser().resolve()
    # The argument is the packet directory, not one of its PDFs, and a packet
    # built by an older version can be missing fields this compiler reads. Both
    # mistakes are cheap to make and were reaching the operator as a stray
    # KeyError or a stat failure, so name the fix instead.
    if not (kit / "manifest.json").is_file():
        raise ValueError(
            f"{kit} is not a prepared packet directory: build one with "
            "`quirework book packet --out DIR`"
        )
    manifest = read(kit / "manifest.json")
    missing = [
        key
        for key in ("stem", "file", "kind")
        if any(key not in row for row in manifest.get("chapters", []))
    ]
    if missing:
        raise ValueError(
            f"{kit}/manifest.json predates this tool ("
            + ", ".join(f"no {key}" for key in missing)
            + "); rebuild the packet with `quirework book packet`"
        )
    seat = seat or current_project().get("sops.default_reader")
    presets = current_project().get("sops.reader_presets", {})
    preset = presets.get(seat, {})
    instrument = instrument or preset.get("instrument")
    if not instrument:
        raise ValueError(
            "select --instrument or configure sops.default_reader and reader_presets"
        )
    if delivery not in ("pages", "file"):
        raise ValueError("book delivery must be pages or file")
    include_matter = preset.get("include_matter", True)
    if type(include_matter) is not bool:
        raise ValueError("reader preset include_matter must be boolean")
    seat = seat or "reader"
    rows = (
        manifest["chapters"]
        if include_matter
        else [r for r in manifest["chapters"] if r["kind"] == "chapter"]
    )
    turns = []
    for i, row in enumerate(rows):
        turns.append(
            dict(
                id=row["stem"],
                ask="matter"
                if row["kind"] != "chapter"
                else "first"
                if i == 0
                else "next",
                materials=[dict(source=str(kit / row["file"]), delivery=delivery)],
            )
        )
    turns.append(dict(id="wrap", ask="wrap"))
    policy = dict(
        tools="none" if delivery == "pages" and harness != "agy" else "read",
        filesystem="audited" if harness == "agy" else "enforced",
    )
    # The window is no longer written down here: the policy default asks for
    # the resolved model's own ceiling, for every harness, and preparation
    # fills in the number. The literals this replaced (1,000,000 / 800,000)
    # outlived the model they were sized for.
    plan = dict(
        title=f"{seat} read",
        source={k: manifest.get(k) for k in ("source_sha", "dirty")},
        metadata=dict(instrument="reader-understanding", delivery=delivery),
        seats=[
            dict(
                id=seat,
                harness=harness,
                model=model,
                effort=effort,
                instrument=instrument,
                **({"persona": persona} if persona is not None else {}),
                scope="book",
                policy=policy,
                turns=turns,
            )
        ],
    )
    if Path(output).exists():
        raise ValueError("plan output exists")
    write(output, plan)
    return str(output)


def comparison(prepared, output, *, persona=None):
    """Compile an existing *prepared* chapter artifact into the shared engine.

    Existing active/completed records are not converted or resumed here. The
    original preparation tool remains the owner of clean render/patch checks.
    """
    prepared = Path(prepared).expanduser().resolve()
    p = read(prepared / "plan.json")
    if (prepared / "state.json").exists():
        raise ValueError("do not convert an active or completed legacy experiment")
    if p.get("protocol") != "chapter-experiment-v2":
        raise ValueError("unsupported chapter artifact protocol")
    if digest(prepared / "plan.json") != (prepared / "plan.sha256").read_text().strip():
        raise ValueError("prepared comparison plan changed")
    for rel, value in p["hashes"].items():
        if digest(prepared / rel) != value:
            raise ValueError("prepared comparison input changed")
    baseline = p.get("baseline_manifest")
    if baseline and baseline.get("dirty"):
        raise ValueError("comparison baseline is not clean")

    def images(arm):
        return [
            digest(prepared / f)
            for segment in p["arms"][arm]["segments"]
            for f in segment["pages"]
        ]

    if images("a") != images("b"):
        raise ValueError("duplicate-original control differs")

    from quirework.sops import personas

    synthetic = bool(p.get("canary_markers"))
    old_role, selected = "", None
    if synthetic and persona is not None:
        raise ValueError("a transport canary keeps its literal prompts")
    if not synthetic:
        reader = p.get("seat", "trade")
        boundaries = {
            "trade": " This is about the EXPERIENCE of reading",
            "skeptic": "\n\nYou are still a reader, not a reviewer.",
        }
        if reader not in boundaries:
            raise ValueError("unrecognized frozen reader kind")
        old_role, boundary, _ = p["prompts"]["first"].partition(boundaries[reader])
        if (
            not boundary
            or not old_role.strip()
            or any(p["prompts"][key].count(old_role) != 1 for key in ("first", "next"))
        ):
            raise ValueError(
                "cannot identify the frozen role without changing its questions"
            )
        # This is a compatibility record derived from the frozen prompt, not
        # an assertion that an old run used a current catalog revision.
        original = {
            "schema_version": 1,
            "id": f"legacy-{reader}-reader",
            "revision": 1,
            "label": f"Frozen {reader} reader",
            "tags": ["reader"],
            "description": "Exact perspective recovered from this prepared comparison.",
            "prompt": old_role,
        }
        selected = personas.resolve(
            persona if persona is not None else {"inline": original}
        )

    def turn(row, index, first=False):
        return dict(
            id=f"part-{index:03d}",
            prompt=p["prompts"]["first" if first else "next"].replace(
                old_role, "{{persona}}"
            )
            if selected
            else p["prompts"]["first" if first else "next"],
            headings=p["prompts"]["headings"],
            materials=[
                dict(source=str(prepared / f), delivery="image") for f in row["pages"]
            ],
        )

    policy = dict(tools="none", filesystem="enforced")
    common = dict(
        harness=p["runner"],
        model=p["model"],
        effort=p["effort"],
        policy=policy,
        persona={"frozen": selected} if selected else None,
        scope="passage",
    )
    parent = [turn(r, i, i == 0) for i, r in enumerate(p["prefix"])]
    if not parent:
        raise ValueError("comparison needs a completed prefix boundary")
    seats = [dict(common, id="parent", turns=parent)]
    for arm in p["order"]:
        seats.append(
            dict(
                common,
                id=arm,
                parent=dict(seat="parent", after=parent[-1]["id"]),
                turns=[turn(r, i) for i, r in enumerate(p["arms"][arm]["segments"])],
            )
        )
    value = dict(
        title="Forked chapter comparison",
        source=dict(source_sha=p["source_sha"]),
        metadata=dict(
            legacy_preparation_sha=digest(prepared / "plan.json"),
            arm_order=p["order"],
            prompt_revision=p["prompt_revision"],
            control_pair=["a", "b"],
        ),
        seats=seats,
    )
    if Path(output).exists():
        raise ValueError("plan output exists")
    write(output, value)
    return str(output)


def bundle(
    artifact,
    output,
    *,
    harness,
    model,
    prompt_file,
    effort="xhigh",
    images=True,
    persona=None,
):
    """Use the established whole-book text/PNG artifact, retaining its order.

    The artifact builder already owns title/contents/Part scaffolding. This
    compiles its manifest into delivery descriptors instead of rebuilding it.
    """
    from quirework.runs.storage import relative

    artifact = Path(artifact).expanduser().resolve()
    manifest = read(artifact / "manifest.json")
    if manifest.get("kind") != "codex-multidisc-markdown-png":
        raise ValueError("expected the established Markdown/PNG book artifact")
    materials = []
    figures = {row["file"]: row for row in manifest["figures"]}
    for row in manifest["reading_order"]:
        path = artifact / relative(row["file"])
        if path.is_symlink() or digest(path) != row["sha256"]:
            raise ValueError("book artifact entry changed")
        materials.append({"source": str(path), "delivery": "text"})
        if images:
            for name in row["figures"]:
                if name not in figures:
                    raise ValueError("book artifact references an absent figure")
                path = artifact / relative(name)
                if path.is_symlink() or digest(path) != figures[name]["sha256"]:
                    raise ValueError("book artifact figure changed")
                materials.append({"source": str(path), "delivery": "image"})
    if not materials:
        raise ValueError("book artifact has no material")
    import hashlib

    fingerprint = hashlib.sha256()
    for row in manifest["reading_order"]:
        fingerprint.update((artifact / relative(row["file"])).read_bytes())
    for row in manifest["figures"]:
        path = artifact / relative(row["file"])
        if path.is_symlink() or digest(path) != row["sha256"]:
            raise ValueError("book artifact figure changed")
        fingerprint.update(path.read_bytes())
    if fingerprint.hexdigest() != manifest["artifact_sha256"]:
        raise ValueError("book artifact order or digest changed")
    value = {
        "title": "Whole-book artifact review",
        "source": {
            "source_sha": manifest["source_sha"],
            "artifact_sha256": manifest["artifact_sha256"],
        },
        "metadata": {
            "delivery": "text-images" if images else "text-only",
            "layout_tested": False,
        },
        "seats": [
            {
                "id": "reviewer",
                "harness": harness,
                "model": model,
                "effort": effort,
                "persona": persona,
                "scope": "book",
                "policy": {
                    "filesystem": "audited" if harness == "agy" else "enforced",
                    "tools": "read" if images and harness == "agy" else "none",
                },
                "turns": [
                    {
                        "id": "review",
                        "prompt": Path(prompt_file).read_text(),
                        "materials": materials,
                    }
                ],
            }
        ],
    }
    if Path(output).exists():
        raise ValueError("plan output exists")
    write(output, value)
    return str(output)


def main(argv=None):
    import argparse
    import json

    p = argparse.ArgumentParser(
        prog="quirework book plan",
        description="Compile declared book materials into run plans.",
    )
    sub = p.add_subparsers(dest="kind", required=True)
    for kind in ("read", "bundle", "compare"):
        child = sub.add_parser(kind)
        child.add_argument("source")
        child.add_argument("output")
        child.add_argument("--persona")
        if kind != "compare":
            child.add_argument("--harness", required=True)
            child.add_argument("--model", required=True)
            child.add_argument("--effort", default="xhigh")
        if kind == "read":
            child.add_argument("--seat", help="configured reader preset")
            child.add_argument("--instrument", help="explicit question instrument")
            child.add_argument("--delivery", choices=("pages", "file"), default="pages")
        if kind == "bundle":
            child.add_argument("--prompt-file", required=True)
            child.add_argument("--text-only", action="store_true")
    args = p.parse_args(argv)
    common = {"persona": args.persona}
    if args.kind != "compare":
        common.update(harness=args.harness, model=args.model, effort=args.effort)
    if args.kind == "read":
        result = book(
            args.source,
            args.output,
            seat=args.seat,
            delivery=args.delivery,
            instrument=args.instrument,
            **common,
        )
    elif args.kind == "bundle":
        result = bundle(
            args.source,
            args.output,
            prompt_file=args.prompt_file,
            images=not args.text_only,
            **common,
        )
    else:
        result = comparison(args.source, args.output, **common)
    print(json.dumps(result, indent=2))
    return 0
