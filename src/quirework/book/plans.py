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
    for kind in ("read", "bundle"):
        child = sub.add_parser(kind)
        child.add_argument("source")
        child.add_argument("output")
        child.add_argument("--persona")
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
    print(json.dumps(result, indent=2))
    return 0
