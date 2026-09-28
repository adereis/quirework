"""Small preparation helpers over existing artifact formats, not a renderer."""

import json
import sys
from pathlib import Path
from quirework.project import current_project

from quirework.runs.storage import digest
from quirework.runs.storage import read
from quirework.runs.storage import write


DELIVERIES = ("pages", "file", "self-driven")


def _resolve_reader(seat, instrument, delivery):
    """Pick a preset's questions, and refuse ones written for another delivery.

    A reader instrument opens by telling the seat what it has been handed, and
    that sentence is part of the measured question rather than packaging. Handed
    to the wrong delivery it becomes a lie in the first line: a seat promised
    attached pages and given a directory to walk answers the courier instead of
    the book, and one that reports it could not see the figures is right.

    A preset may therefore declare the deliveries its questions were written
    for. Declaring nothing keeps the old behavior, because a project with one
    delivery should not have to say so; declaring them is how a project with
    several buys the refusal."""
    project = current_project()
    # Naming an instrument is the operator overriding the preset, and overriding
    # it is how a deliberate mismatch is run.
    explicit = instrument is not None
    seat = seat or project.get("sops.default_reader")
    preset = project.get("sops.reader_presets", {}).get(seat, {})
    instrument = instrument or preset.get("instrument")
    if not instrument:
        raise ValueError(
            "select --instrument or configure sops.default_reader and reader_presets"
        )
    declared = preset.get("delivery")
    if declared is not None:
        if not isinstance(declared, list) or any(d not in DELIVERIES for d in declared):
            raise ValueError(
                "reader preset delivery must list " + ", ".join(DELIVERIES)
            )
        if delivery not in declared and not explicit:
            raise ValueError(
                f"reader preset {seat!r} declares its questions for "
                f"{', '.join(declared)}, not {delivery}; its opening sentence "
                "tells the seat what it was handed, so select a preset written "
                "for this delivery or override it with --instrument"
            )
    return seat, preset, instrument


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
    tier=None,
):
    kit = Path(kit).expanduser().resolve()
    # The argument is the packet directory, not one of its PDFs, and a packet
    # built by an older version can be missing fields this compiler reads. Both
    # mistakes are cheap to make and were reaching the operator as a stray
    # KeyError or a stat failure, so name the fix instead.
    if not (kit / "manifest.json").is_file():
        raise ValueError(
            f"{kit} is not a prepared packet directory: build one with "
            "`quirework publish packet --out DIR`"
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
            + "); rebuild the packet with `quirework publish packet`"
        )
    if delivery not in ("pages", "file"):
        raise ValueError("book delivery must be pages or file")
    seat, preset, instrument = _resolve_reader(seat, instrument, delivery)
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
                scope="work",
                policy=policy,
                turns=turns,
                **({"tier": tier} if tier is not None else {}),
            )
        ],
    )
    if Path(output).exists():
        raise ValueError("plan output exists")
    write(output, plan)
    return str(output)


def selfdriven(
    source,
    output,
    *,
    harness,
    model,
    effort="medium",
    seat=None,
    persona=None,
    instrument=None,
    ask=None,
    wrap_ask=None,
    mount=None,
    withhold_wrap=False,
    tier=None,
):
    """A ONE-TURN plan for a seat that reads a mounted book with its own tools.

    Sequential delivery re-launches the harness once per chapter and the
    operator controls every reveal. This launches once: the seat walks
    `chapters/NN-slug.md` in order, opens the figures each chapter references,
    and writes a reaction file before opening the next chapter. One session's
    prefix only ever grows, so nearly all of its input is served from cache.

    What it gives up is the frozen-packet guarantee. The material is MOUNTED
    rather than copied into the record, so the bytes live outside it: pin them
    separately and say so in the charter. The seat can also see its own
    surroundings, so `mount` must be a path that reveals neither the project nor
    the run, and this refuses the ones it can recognize.

    `source` is a `publish packet --prose-dir` directory. The instrument is the
    caller's to choose and is used unaltered. A printed-book instrument does not
    belong here: its opening sentence tells the seat that its pages are attached,
    which in this mode is a lie told in the first line, and a seat answering it
    is answering the courier rather than the book. Declare a reader preset whose
    questions describe a seat that fetches its own material.
    """
    source = Path(source).expanduser().resolve()
    chapters = sorted((source / "chapters").glob("*.md"))
    figures = (
        sorted(f for f in (source / "figures").glob("*") if f.is_file())
        if (source / "figures").is_dir()
        else []
    )
    if not chapters:
        raise ValueError(
            f"{source} holds no chapter files: build it with "
            "`quirework publish packet --prose-dir DIR`"
        )
    seat, preset, instrument = _resolve_reader(seat, instrument, "self-driven")
    mount = Path(mount).expanduser().resolve() if mount else source
    _check_mount(mount)
    source_record, pinned = _pin_material(source, mount)

    from quirework.sops import instruments as _instruments
    from quirework.sops import personas as _personas

    questions = _instruments.load(instrument)
    sequence = questions.get("sequence") or {}
    # The seat reads every chapter inside one turn, so the block it repeats has
    # to stand on its own. That is what an opening ask is: a continuation ask
    # says "reply in the same form as before", and in a one-turn read there is
    # no before. An instrument written for this mode has no continuation ask at
    # all and therefore no declarable sequence, so its preset names the asks
    # instead and the operator types nothing extra.
    ask = ask or preset.get("ask") or sequence.get("first")
    if not ask:
        raise ValueError(
            f"{instrument} declares no sequence, so name the ask this read "
            "repeats per chapter with --ask"
        )
    wrap_ask = wrap_ask or preset.get("wrap_ask") or sequence.get("wrap")
    chosen = persona or questions.get("default_persona_reference")
    scope = questions.get("default_scope", "work")
    per_chapter, _ = _instruments.prompt(
        {"ask": ask},
        questions,
        persona=_personas.resolve(chosen) if chosen else None,
        first=True,
        scope=scope,
    )
    wrap = None
    if wrap_ask:
        wrap, _ = _instruments.prompt(
            {"ask": wrap_ask}, questions, persona=None, first=False, scope=scope
        )
    if withhold_wrap and wrap is None:
        raise ValueError(
            "--withhold-wrap needs a closing question: name it with --wrap-ask "
            "or give the instrument a sequence that declares one"
        )
    listing = "\n".join(
        f"  {i}. chapters/{c.name}" for i, c in enumerate(chapters, start=1)
    )
    # A withheld final question is the instrument, not a convenience: a read that
    # measures what the seat believed while reading must not let it see what is
    # being tested. So its wrap is a second turn into the same session, and the
    # reading prompt must not hint that one is coming.
    closing = (
        "Stop when the last reaction file is written."
        if withhold_wrap or wrap is None
        else f"When, and only when, all {len(chapters)} reaction files exist, write "
        "your\nwhole-book response to `drafts/wrap.md`."
    )
    wrap_block = (
        ""
        if withhold_wrap or wrap is None
        else f"\nAnd `drafts/wrap.md` contains exactly these headings:\n\n{wrap}\n"
    )
    prompt = f"""You are going to read a book from files on disk, chapter by chapter, and write
down how each chapter felt to read.

The book is at `{mount}`. Its {len(chapters)} chapter files are in reading order:

{listing}

Illustrations are in `{mount}/figures/`. Each chapter's text references its own
figures by relative path from the chapter file, under the names the text gives.
Open every figure a chapter references and look at it.

FOLLOW THIS PROTOCOL EXACTLY, one chapter at a time:

  1. Open chapter n and read it completely, including every illustration it
     references.
  2. Write your reaction to chapter n to `drafts/NN-<slug>.md`, using the same
     NN-slug as the chapter file. Write the file before going any further.
  3. Only then open chapter n+1, and repeat from step 1.

Do NOT open a later chapter before you have written the current chapter's
reaction file. Do not read ahead, do not skim the ending, and do not revise a
reaction file once you have written it. Write each reaction as the reader you
were at that point in the book, having read only up to and including that
chapter.

{closing}

Each reaction file contains exactly these headings and nothing else:

{per_chapter}
{wrap_block}"""
    outputs = [f"drafts/{c.stem}.md" for c in chapters]
    if not withhold_wrap and wrap is not None:
        outputs.append("drafts/wrap.md")
    turns = [
        dict(
            id="wholebook",
            prompt=prompt,
            min_bytes=200,
            outputs=outputs,
            materials=[],
            referenced_images=len(figures),
            referenced_files=[str(mount / "chapters" / c.name) for c in chapters],
            metadata=dict(instrument=instrument, ask=ask),
        )
    ]
    if withhold_wrap:
        turns.append(
            dict(
                id="wrap",
                prompt=wrap,
                min_bytes=200,
                outputs=["drafts/wrap.md"],
                materials=[],
                metadata=dict(instrument=instrument, ask=wrap_ask),
            )
        )
    plan = dict(
        title=f"{seat} read, self-driven: {model} at {effort}",
        source=source_record,
        metadata=dict(instrument=instrument, delivery="self-driven-files"),
        seats=[
            dict(
                id=seat,
                harness=harness,
                model=model,
                effort=effort,
                **({"persona": chosen} if chosen is not None else {}),
                policy=dict(
                    tools="write",
                    filesystem="audited" if harness == "agy" else "enforced",
                    allow_compaction=False,
                    read_roots=[str(mount)],
                    read_root_digests={str(mount): pinned},
                ),
                turns=turns,
                **({"tier": tier} if tier is not None else {}),
            )
        ],
    )
    if Path(output).exists():
        raise ValueError("plan output exists")
    write(output, plan)
    return str(output)


def _pin_material(source, mount):
    """(source record, mount digest) for a self-driven plan.

    The record freezes nothing the seat reads, so the plan names it instead:
    the book state from the packet's manifest, and a digest of the mounted
    bytes that launch checks again before anything is submitted. The mount
    may be a copy of the packet placed at a neutral path; it must then be an
    exact copy, or the plan would pin one book and describe another.
    """
    from quirework.publish.panelkit import manifest_path
    from quirework.runs.storage import tree_digest

    pinned = tree_digest(mount)
    record = manifest_path(source)
    if not record.is_file():
        print(
            f"plan: no provenance beside {source} ({record.name}); the plan "
            "pins the mounted bytes but cannot say which book state they are. "
            "Rebuild with `quirework publish packet --prose-dir` to record it.",
            file=sys.stderr,
        )
        return {}, pinned
    manifest = json.loads(record.read_text(encoding="utf-8"))
    built = manifest.get("digest")
    if built and tree_digest(source) != built:
        raise ValueError(
            f"{source} has changed since the packet built it; rebuild it rather "
            "than plan a read of bytes no manifest describes"
        )
    if built and pinned != built:
        raise ValueError(
            f"the mount {mount} is not an exact copy of {source}; copy the "
            "packet again or mount the packet directory itself"
        )
    return {k: manifest.get(k) for k in ("source_sha", "dirty")}, pinned


def _check_mount(mount):
    """Refuse a mount path that tells the seat whose book it is reading.

    A self-driven seat has a shell, and the first thing one does is look around:
    it reads its own working directory, walks up a level, and searches for an
    agent guide. Everything it can reach is part of the read whether or not the
    operator meant it to be, and a path is the cheapest of those leaks.

    The containment check always runs. Name matching needs declared data, so it
    reads `mount_markers` from the isolation policy: path words that would
    identify this project. It is deliberately not `project_markers`, which the
    blind canary matches against a seat's ANSWER -- a book about kettles wants
    that word to fire in a path and never in a verdict."""
    project = current_project()
    root = project.root.resolve()
    if mount == root or root in mount.parents or mount in root.parents:
        raise ValueError(
            f"mount {mount} is inside the project (or contains it); a seat with "
            "a shell reads its own paths"
        )
    markers = tuple(
        project.data("providers.isolation_policy", {}).get("mount_markers", ())
    )
    hit = next((m for m in markers if m and m.lower() in str(mount).lower()), None)
    if hit:
        raise ValueError(
            f"mount {mount} names the project ({hit!r} is a declared mount "
            "marker); a seat with a shell reads its own paths"
        )


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
                "scope": "work",
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
        prog="quirework publish plan",
        description="Compile declared book materials into run plans.",
    )
    sub = p.add_subparsers(dest="kind", required=True)
    for kind, text in (
        ("read", "a reader plan over a packet from `publish packet`"),
        ("selfdriven", "a one-turn plan for a seat that reads a mounted book"),
        ("bundle", "a plan over the whole-book text and page artifact"),
    ):
        child = sub.add_parser(kind, help=text)
        child.add_argument("source")
        child.add_argument("output")
        child.add_argument("--persona")
        child.add_argument("--harness", required=True)
        child.add_argument("--model", required=True)
        # A reader instrument is measured per model and an effort word promises
        # nothing across them, so the reading default is not the analysis one.
        child.add_argument(
            "--effort", default="medium" if kind == "selfdriven" else "xhigh"
        )
        if kind in ("read", "selfdriven"):
            child.add_argument("--seat", help="configured reader preset")
            child.add_argument("--instrument", help="explicit question instrument")
            child.add_argument(
                "--tier",
                choices=("cheap", "premium", "standard"),
                help="spend-order tier for this seat",
            )
        if kind == "read":
            child.add_argument("--delivery", choices=("pages", "file"), default="pages")
        if kind == "selfdriven":
            child.add_argument(
                "--ask",
                help="instrument ask the seat repeats per chapter "
                "(default: the instrument's opening ask)",
            )
            child.add_argument(
                "--wrap-ask", help="instrument ask for the closing question"
            )
            child.add_argument(
                "--mount",
                help="read-only path the seat will see; must not name the project",
            )
            child.add_argument(
                "--withhold-wrap",
                action="store_true",
                help="ask the closing question in a second turn, unseen while reading",
            )
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
            tier=args.tier,
            **common,
        )
    elif args.kind == "selfdriven":
        result = selfdriven(
            args.source,
            args.output,
            seat=args.seat,
            instrument=args.instrument,
            ask=args.ask,
            wrap_ask=args.wrap_ask,
            mount=args.mount,
            withhold_wrap=args.withhold_wrap,
            tier=args.tier,
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
