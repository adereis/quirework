"""Build an audience or medium variant of the canonical book.

Four entry points, dispatched from ``main``:

    quirework publish variant                       list the variant kinds
    quirework publish variant <kind>                print the variant SOP
    quirework publish variant build <kind>          draft it blind, through the run engine
    quirework publish variant import <kind> RECORD  bring a finished run's drafts in

``build`` is the part the project's ``variant-common`` procedure hands to a
tool: assemble a *blindness-safe* bundle of the canonical book -- every
``<!-- ... -->`` comment stripped, so the ``@bind``/``@todo``/``@figure`` marks
(the project's own self-understanding) never reach the drafter -- then plan
one seat per chapter and run the plan through the run engine. Each seat is
fed the whole book for grounding but asked to rewrite only its chapter, with
no tools, in the engine's isolated workspace, so it is structurally an
outside reader. Any harness the engine drives can draft, and every draft
has the engine's receipt, which names the model that actually served it.
The drafts land under ``variants/<kind>/_raw/``, with a ``receipts.json``
beside them, for the operator's fidelity, honesty and voice pass.

The tool stops at raw drafts on purpose: the *analyze* and *compose* steps
(checking each draft against what the book commits to, fixing any drift, wiring
the figures, writing the final ``.qmd`` and ``_quarto.yml``) are human
judgment, and the project's own procedures own them. The shared rule for
blind runs lives in docs/workflows.md#execution-and-recovery.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

from quirework.project import current_project
from quirework.manuscript import notes as _notes
from quirework.publish.editions import RAW


# Blind drafters are run-engine seats with no tools, each in its own isolated
# workspace. Subagents inherit the project framing and are NOT blind -- never
# use them here (docs/workflows.md#execution-and-recovery).

DEFAULT_TIMEOUT_S = 600
DEFAULT_PAR = 5
# The engine needs an effort for every seat, and every harness it drives
# accepts this one, so a project that names none still gets a valid plan.
DEFAULT_EFFORT = "high"
TURN = "draft"
RECEIPTS = "receipts.json"

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_QMD = re.compile(r"([\w./-]+\.qmd)")
# The attribute block is any Pandoc one, `{.unnumbered #sec-preface}` as well
# as a bare `{#sec-opening}`; a title read with its classes left on told every
# drafter the preface was called "Preface {.unnumbered #sec-preface}".
_H1 = re.compile(r"(?m)^#\s+(.*?)\s*(?:\{[^}]*\})?\s*$")
_FM_TITLE = re.compile(r'(?ms)\A---\s*\n.*?^title:\s*"?(.*?)"?\s*$.*?^---\s*$')

PROMPT_INTRO = """\
You are a book editor. You are making the {KIND} edition of the supplied book. Your one job in this message:
rewrite a SINGLE chapter of it for the target reader, keeping every idea and every
honest doubt, but recast for that reader.

The message has five parts:
  [1] THE WHOLE BOOK, so your chapter fits the argument around it. Read it. Do
      NOT rewrite all of it.
  [2] YOUR TASK: the one chapter to rewrite.
  [3] THE READER you are writing for.
  [4] VOICE RULES.
  [5] ILLUSTRATIONS that already exist; lean on them instead of describing them
      in full.

Output ONLY the rewritten chapter, as Markdown, beginning with a single "# "
title line. No preamble, no sign-off, no notes about your choices. Everything you
need is in this message; do not use any tools."""


# --------------------------------------------------------------------------- #
# pure helpers (unit-tested; no I/O)
# --------------------------------------------------------------------------- #


def _settings():
    """Resolve paths from the current operation, without import-time state."""
    from types import SimpleNamespace

    SOPS = current_project().path("sops.root")
    DOCS = current_project().path("manuscript.source")
    QUARTO = current_project().path("manuscript.quarto")
    return SimpleNamespace(DOCS=DOCS, QUARTO=QUARTO, SOPS=SOPS)


def procedure(name: str) -> Path | None:
    """The project's `variant-...` procedure, wherever its layout puts it.

    A project files procedures directly under the procedure root, in
    `workflows/` or in `protocols/`, and the catalog finds all three. This once
    looked only in the root, so a project that followed its own documented
    convention and filed the shared voice rules under `protocols/` got "no SOP
    'variant-manager'" from a command that was standing next to the file.
    """
    root = _settings().SOPS
    found = [
        candidate
        for folder in ("", "workflows", "protocols")
        if (candidate := root / folder / f"{name}.md").is_file()
    ]
    return found[0] if found else None


def kinds() -> list[str]:
    """Every variant kind the project declares, by its procedure's name."""
    root = _settings().SOPS
    return sorted(
        {
            path.stem[len("variant-") :]
            for folder in ("", "workflows", "protocols")
            for path in (root / folder).glob("variant-*.md")
            if path.stem != "variant-common"
        }
    )


def _rel(path: Path) -> str:
    """A path as the operator would type it, from the project root."""
    try:
        return str(path.relative_to(current_project().root))
    except ValueError:
        return str(path)


def strip_comments(text: str) -> str:
    """Remove every HTML comment, then collapse the blank runs they leave.

    The blindness guarantee for the bundle: the ``@bind``/``@todo``/``@figure``
    marks are HTML comments, so this drops all of them along with any other
    marginalia, leaving only the reader-facing prose. Since the marks now live in
    per-chapter sidecars rather than inline (see ``notes.MARKS_DIR``), the source
    prose is already mark-free and this is near a no-op — but it stays as the
    belt-and-braces guard ``assert_blind`` relies on.
    """
    text = _COMMENT.sub("", text)
    text = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text)
    return text.strip() + "\n"


def section_body(md: str, heading: str) -> str | None:
    """The body under a ``## heading`` (case-insensitive), up to the next heading.

    Lets the SOP stay the single source of truth for the blind-draft brief: the
    reader profile and voice rules are read straight out of the playbook prose.
    """
    lines = md.splitlines()
    want = heading.strip().lower()
    start = None
    level = None
    for i, ln in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*?)\s*$", ln)
        if m and m.group(2).strip().lower() == want:
            start, level = i + 1, len(m.group(1))
            break
    if start is None:
        return None
    out = []
    for ln in lines[start:]:
        m = re.match(r"^(#{1,6})\s+", ln)
        if m and len(m.group(1)) <= level:
            break
        out.append(ln)
    return "\n".join(out).strip() or None


def clean_figure(body: str) -> tuple[str, str]:
    """(visual description, built path) from a ``@figure`` body.

    Keeps the description before ``Built:`` and drops the operator metadata
    (reader-panel counts, guardrails, back-port notes) that trails it.
    """
    desc = re.sub(r"\s+", " ", body.split("Built:")[0]).strip().rstrip(".")
    m = re.search(r"Built:\s*(\S+)", body)
    return desc, (m.group(1).rstrip(".") if m else "")


# --------------------------------------------------------------------------- #
# assembling the inputs from the tree
# --------------------------------------------------------------------------- #
def _title_of(path: Path) -> str:
    text = path.read_text()
    m = _FM_TITLE.match(text)
    if m and m.group(1).strip():
        return m.group(1).strip()
    m = _H1.search(text)
    return m.group(1).strip() if m else path.stem


def has_prose(path: Path) -> bool:
    """Whether a unit holds anything to retell beyond its title.

    A title page that is only a heading still belongs in the bundle, since it
    is part of the book a reader reads, but a drafter given it can only hand
    the title back, at the price of a whole-book prompt.
    """
    text = strip_comments(path.read_text())
    front = _FM_TITLE.match(text)
    if front:
        text = text[front.end() :]
    return bool(_H1.sub("", text, count=1).strip())


def chapter_order() -> list[dict]:
    """Every canonical chapter in the configured Quarto reading order.

    Returns dicts of ``{path, slug, title}``. Order comes from the book config so
    the bundle a drafter reads is the book a reader reads.
    """
    if not _settings().QUARTO.exists():
        raise FileNotFoundError(f"no book config at {_settings().QUARTO}")
    seen: set[str] = set()
    chapters = []
    for tok in _QMD.findall(_settings().QUARTO.read_text()):
        if tok in seen:
            continue
        seen.add(tok)
        p = (_settings().DOCS / tok).resolve()
        if p.exists():
            chapters.append({"path": p, "slug": p.stem, "title": _title_of(p)})
    return chapters


def build_bundle(chapters: list[dict]) -> str:
    """The whole book, comment-stripped, with plain-text chapter separators."""
    parts = []
    for ch in chapters:
        clean = strip_comments(ch["path"].read_text())
        parts.append(
            f"----- SOURCE CHAPTER: {ch['title']} ({ch['path'].name}) -----\n\n{clean}"
        )
    return "\n\n\n".join(parts)


def figures_for(path: Path) -> list[dict]:
    """Sanitized ``@figure`` manifest for one chapter (no operator metadata).

    ``@figure`` marks live in the chapter's sidecar ``_marks/<stem>.md`` (the
    prose ``.qmd`` carries only the rendered ``![...]`` embed, not the design
    mark), so read them from there. No sidecar → no chapter-specific figures."""
    side = path.parent / _notes.MARKS_DIR / f"{path.stem}.md"
    if not side.exists():
        return []
    out = []
    for a in _notes.scan_text(side.read_text(), path.name):
        if a.kind != "figure":
            continue
        desc, _built = clean_figure(a.body)
        name = a.args[0] if a.args else ""
        out.append({"name": name, "desc": desc})
    return out


def _figure_block(figs: list[dict]) -> str:
    if not figs:
        return (
            "No illustration is assigned to this chapter specifically. If a simple "
            "picture would obviously help, you may suggest one in a single bracketed "
            "line like [PICTURE: a short caption], but do not force it."
        )
    lines = [
        "Pictures that already exist for THIS chapter (refer to them, don't re-describe):"
    ]
    for f in figs:
        lines.append(f'  - "{f["name"]}": {f["desc"]}')
    lines.append(
        "If one fits, point to it with a caption line like "
        "[PICTURE: name - a one-line caption in the reader's language]."
    )
    return "\n".join(lines)


def read_brief(kind: str) -> tuple[str, str]:
    """(shared voice rules, this variant's reader profile) from the SOP prose.

    Both are read from a ``## Blind-draft brief`` section -- the shared voice
    rules from variant-common.md, the reader profile from variant-<kind>.md -- so
    the playbook a human reads is the same text the drafter is handed.
    """
    common = procedure("variant-common")
    per = procedure(f"variant-{kind}")
    voice = section_body(common.read_text(), "Blind-draft brief") if common else None
    reader = section_body(per.read_text(), "Blind-draft brief") if per else None
    if not voice:
        raise ValueError("variant-common.md has no `## Blind-draft brief` section")
    if not reader:
        raise ValueError(f"variant-{kind}.md has no `## Blind-draft brief` section")
    return voice, reader


def assemble_prompt(
    kind_label: str, bundle: str, ch: dict, reader: str, voice: str, figs: list[dict]
) -> str:
    task = (
        f'Rewrite the chapter titled "{ch["title"]}" (file {ch["path"].name}) in the '
        f"book below, and ONLY that chapter, as it should read in the {kind_label} "
        f"edition. Keep its job in the book's argument. Keep every hedge and every "
        f'"maybe" the original holds; simpler is allowed, more confident is not.'
    )
    return (
        f"{current_project().data('publish.variant_policy', {}).get('prompt_intro', PROMPT_INTRO).format(KIND=kind_label.upper())}\n\n"
        f"=== [1] THE WHOLE BOOK (context; rewrite only the one chapter named in part 2) ===\n\n"
        f"{bundle}\n\n"
        f"=== [2] YOUR TASK ===\n\n{task}\n\n"
        f"=== [3] THE READER ===\n\n{reader}\n\n"
        f"=== [4] VOICE RULES ===\n\n{voice}\n\n"
        f"=== [5] ILLUSTRATIONS ===\n\n{_figure_block(figs)}\n\n"
        f'=== END. Now output ONLY the {kind_label} version of "{ch["title"]}" as Markdown. ===\n'
    )


def assert_blind(bundle: str, tells: tuple[str, ...] = ()) -> None:
    """Fail fast if any project marginalia survived into the drafter's bundle.

    The built-in tells are Quirework's own annotation syntax, which every book
    shares. A book whose metadata layer has private vocabulary of its own adds
    those words through ``blind_tells`` in its variant policy, because the tool
    cannot know them and must not carry one book's words for the next.
    """
    if _COMMENT.search(bundle):
        raise AssertionError("bundle still contains HTML comments -- blindness leak")
    for tell in ("@bind", "@todo", "@figure", *tells):
        if tell in bundle:
            raise AssertionError(
                f"bundle contains project marginalia {tell!r} -- blindness leak"
            )


# --------------------------------------------------------------------------- #
# the blind drafters, as a run plan
# --------------------------------------------------------------------------- #
def drafter(kind: str, harness=None, model=None, effort=None) -> dict:
    """The harness, model and effort the ``kind`` edition's drafters run on.

    An invocation's own choice wins, then the kind's table under ``kinds`` in
    the variant policy, then the policy's defaults. A harness left unnamed is
    inferred from the model, which recognizes Claude's names and sends
    anything else to Codex; Antigravity is always named.
    """
    from quirework.providers import models

    policy = current_project().data("publish.variant_policy", {})
    table = (policy.get("kinds") or {}).get(kind) or {}
    model = model or table.get("model") or policy.get("default_model")
    harness = (
        harness
        or table.get("harness")
        or policy.get("default_harness")
        or (models.harness_for(model) if model else None)
    )
    effort = (
        effort or table.get("effort") or policy.get("default_effort") or DEFAULT_EFFORT
    )
    return {"harness": harness, "model": model, "effort": effort}


def seat_id(unit: str) -> str:
    return f"draft-{unit}"


def plan(kind: str, drafts: list[dict], seat: dict) -> dict:
    """One run-engine seat per draft, each a single no-tools turn.

    ``drafts`` are ``{unit, title, source, prompt}``. The metadata is what
    ``import_drafts`` reads back: which edition, and which unit each answer
    is the draft of.
    """
    harness = seat["harness"]
    return {
        "title": f"The {kind} edition: {len(drafts)} blind draft(s)",
        "metadata": {"variant": {"kind": kind}},
        "seats": [
            {
                "id": seat_id(d["unit"]),
                **seat,
                # agy audits file access and cannot enforce isolation; the seat
                # has no tools either way, and its receipt says which it was.
                "policy": {
                    "tools": "none",
                    "filesystem": "audited" if harness == "agy" else "enforced",
                },
                # agy keeps its login in the keyring, which every agy jail can
                # reach; a seat with no tools cannot use it.
                **({"accept_red_flags": ["session-bus"]} if harness == "agy" else {}),
                "metadata": {
                    "variant": {
                        "kind": kind,
                        "unit": d["unit"],
                        "title": d["title"],
                        "source": d["source"],
                    }
                },
                "turns": [{"id": TURN, "prompt": d["prompt"], "min_bytes": 20}],
            }
            for d in drafts
        ],
    }


def run_directory(kind: str, model: str) -> Path:
    """A new directory in the project's private state for one drafting run.

    The plan holds the whole book once per seat and the record holds every
    prompt and answer, so both stay outside the checkout. A second run on
    the same day takes the next free suffix.
    """
    root = current_project().state_root
    stem = re.sub(
        r"[^A-Za-z0-9.-]+", "-", f"variant-{kind}-{date.today().isoformat()}-{model}"
    )
    candidate, n = root / stem, 1
    while candidate.exists():
        n += 1
        candidate = root / f"{stem}-{n}"
    return candidate


def recovery_lines(kind: str, record: Path) -> list[str]:
    return [
        f"  quirework sop runs inspect {record}",
        f"  quirework sop runs start {record}",
        f"  quirework publish variant import {kind} {record}",
    ]


def import_drafts(kind: str, record, out_dir: Path | None = None) -> int:
    """Copy each finished seat's answer into the edition's drafts; 0 if all.

    The record is verified first, so a changed answer or input is refused. A
    seat with a red flag its plan did not accept is held, and a seat with no
    answer yet is reported, so neither reaches ``_raw/``. Each draft that
    does is entered in ``receipts.json`` beside it under its unit, replacing
    that unit's earlier entry and leaving the others, so redrafting one
    chapter keeps the rest of the edition's record.
    """
    from quirework.runs.engine import Run

    run = Run(record)
    run.verify()
    if (run.plan.get("metadata") or {}).get("variant", {}).get("kind") != kind:
        raise ValueError(f"{record} is not a run of the {kind} edition's drafters")
    raw = (out_dir or (current_project().path("publish.variants") / kind)) / RAW
    raw.mkdir(parents=True, exist_ok=True)
    ledger = raw / RECEIPTS
    receipts = (
        json.loads(ledger.read_text(encoding="utf-8"))
        if ledger.is_file()
        else {"drafts": {}}
    )
    missing = 0
    for name, seat in run.seats.items():
        meta = (seat.get("metadata") or {}).get("variant") or {}
        unit = meta.get("unit")
        if not unit:
            continue
        done = run.state(name)["done"].get(TURN)
        if not done:
            missing += 1
            print(f"  WAIT  {unit:28} not answered")
            continue
        open_flags, accepted = run.red_flags(name)
        if open_flags:
            missing += 1
            print(f"  HELD  {unit:28} {'; '.join(open_flags)}")
            continue
        answer = (run.record(name, TURN) / "answers" / "answer.md").read_text(
            encoding="utf-8"
        )
        body = answer.strip() + "\n"
        (raw / f"{unit}.md").write_text(body, encoding="utf-8")
        receipt = done["receipt"]
        config = seat["config"]
        receipts["drafts"][unit] = {
            "source": meta.get("source"),
            "harness": config["harness"],
            "requested_model": config.get("requested_model", config["model"]),
            "model": receipt.get("model"),
            "effort": receipt.get("effort", config.get("effort")),
            "cost_usd": receipt.get("cost_usd"),
            "billing": receipt.get("billing"),
            "session_id": receipt.get("session_id"),
            "accepted_flags": accepted,
            "run": run.plan.get("id"),
            "record": Path(record).resolve().parent.name,
            "seat": name,
            "sha256": hashlib.sha256(body.encode()).hexdigest(),
        }
        cost = receipt.get("cost_usd")
        priced = ""
        if isinstance(cost, (int, float)):
            priced = f"  ${cost:.2f}" if cost >= 0.01 else f"  ${cost:.4f}"
        print(
            f"  OK    {unit:28} ({len(body.split())} words)  "
            f"{config['harness']} {receipt.get('model')}{priced}"
        )
    receipts["drafts"] = dict(sorted(receipts["drafts"].items()))
    ledger.write_text(json.dumps(receipts, indent=2) + "\n", encoding="utf-8")
    print(f"drafts in {_rel(raw)}, receipts in {_rel(ledger)}")
    if missing:
        print(f"{missing} draft(s) not imported. Reconcile the run, then import:")
        print("\n".join(recovery_lines(kind, Path(record).resolve())))
        return 1
    return 0


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def _print_sop(kind: str | None) -> int:
    avail = kinds()
    if kind is None:
        print("variant kinds (quirework publish variant <kind> prints its SOP):")
        for k in avail:
            print(f"  {k}")
        print("\nbuild the drafts: quirework publish variant build <kind>")
        return 0
    f = procedure(f"variant-{kind}")
    if f is None:
        print(
            f"no SOP 'variant-{kind}'. available: {', '.join(avail) or '(none yet)'}",
            file=sys.stderr,
        )
        return 2
    print(f.read_text(), end="")
    return 0


def cmd_build(argv: list[str]) -> int:
    kind = None
    out_dir = None
    chosen = {}
    par = DEFAULT_PAR
    timeout = DEFAULT_TIMEOUT_S
    only = None
    dry = False
    plan_only = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print(USAGE, end="")
            return 0
        elif a in (
            "--out",
            "--harness",
            "--model",
            "--effort",
            "--par",
            "--only",
            "--timeout",
        ) and i + 1 >= len(argv):
            print(f"build: {a} needs a value", file=sys.stderr)
            return 2
        elif a == "--out":
            out_dir = Path(argv[i + 1])
            i += 2
        elif a in ("--harness", "--model", "--effort"):
            chosen[a[2:]] = argv[i + 1]
            i += 2
        elif a == "--par":
            par = int(argv[i + 1])
            i += 2
        elif a == "--only":
            only = argv[i + 1]
            i += 2
        elif a == "--timeout":
            timeout = int(argv[i + 1])
            i += 2
        elif a == "--dry-run":
            dry = True
            i += 1
        elif a == "--plan-only":
            plan_only = True
            i += 1
        elif not a.startswith("-") and kind is None:
            kind = a
            i += 1
        else:
            print(f"build: unknown arg {a!r}", file=sys.stderr)
            return 2
    if kind is None:
        print("usage: quirework publish variant build <kind> [...]", file=sys.stderr)
        return 2
    if procedure(f"variant-{kind}") is None:
        print(f"no SOP 'variant-{kind}.md' to build against", file=sys.stderr)
        return 2

    kind_label = kind.replace("-", " ")
    voice, reader = read_brief(kind)
    all_chapters = chapter_order()
    chapters = all_chapters
    if only:
        chapters = [c for c in all_chapters if only in c["slug"]]
        if not chapters:
            print(f"--only {only!r} matched no chapter", file=sys.stderr)
            return 2
    bundle = build_bundle(all_chapters)  # always the WHOLE book for grounding
    policy = current_project().data("publish.variant_policy", {})
    assert_blind(bundle, tuple(policy.get("blind_tells", ())))
    titles_only = [c for c in chapters if not has_prose(c["path"])]
    chapters = [c for c in chapters if c not in titles_only]

    seat = drafter(kind, **chosen)
    out_dir = out_dir or (current_project().path("publish.variants") / kind)
    print(
        f"variant build: {kind}  ({len(chapters)} chapter(s), "
        f"{seat['harness'] or 'harness unset'} {seat['model'] or 'model unset'} "
        f"at {seat['effort']}, par={par})"
    )
    print(f"  bundle: {len(bundle.split())} words, blindness check OK")
    print(f"  raw drafts -> {_rel(out_dir / RAW)}")
    for c in titles_only:
        print(
            f'  skip         {c["slug"]:28}  "{c["title"]}": a title, nothing to retell'
        )
    if not chapters:
        print("no chapter has prose to retell", file=sys.stderr)
        return 2
    if dry:
        for c in chapters:
            print(
                f'  would draft  {c["slug"]:28}  "{c["title"]}"  '
                f"({len(figures_for(c['path']))} figure(s))"
            )
        print("dry run: no plan written, no seat started.")
        return 0
    if seat["model"] is None:
        print(
            "no model selected: pass --model, or set default_model in the file "
            "publish.variant_policy names.",
            file=sys.stderr,
        )
        return 2

    drafts = [
        {
            "unit": c["slug"],
            "title": c["title"],
            "source": _rel(c["path"]),
            "prompt": assemble_prompt(
                kind_label, bundle, c, reader, voice, figures_for(c["path"])
            ),
        }
        for c in chapters
    ]
    run_dir = run_directory(kind, seat["model"])
    (run_dir / "plan").mkdir(parents=True)
    plan_path = run_dir / "plan" / "plan.json"
    plan_path.write_text(json.dumps(plan(kind, drafts, seat), indent=2) + "\n")
    record = run_dir / "record"
    print(f"  plan: {plan_path}", flush=True)
    if plan_only:
        print("next:")
        print(f"  quirework sop runs prepare {plan_path} {record}")
        print("\n".join(recovery_lines(kind, record)[1:]))
        return 0

    from quirework.runs import commands as runs
    from quirework.runs import engine

    try:
        engine.prepare(plan_path, record)
    except (ValueError, RuntimeError, OSError) as exc:
        # Nothing ran and nothing else names this directory yet, so a plan
        # the engine refuses leaves nothing behind.
        shutil.rmtree(run_dir)
        print(f"variant: cannot prepare the drafters' run: {exc}", file=sys.stderr)
        return 2
    print(f"  record: {record}", flush=True)
    runs.main(["start", str(record), "--jobs", str(par), "--timeout", str(timeout)])
    rc = import_drafts(kind, record, out_dir)
    if rc == 0:
        print(
            "next (operator): the analyze and compose steps in "
            f"{_rel(procedure('variant-common'))}. Composing the edition "
            "is judgment, so the tool stops here."
        )
    return rc


def cmd_import(argv: list[str]) -> int:
    out_dir = None
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] in ("-h", "--help"):
            print(USAGE, end="")
            return 0
        if argv[i] == "--out" and i + 1 < len(argv):
            out_dir = Path(argv[i + 1])
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    if len(rest) != 2 or any(a.startswith("-") for a in rest):
        print("usage: quirework publish variant import KIND RECORD", file=sys.stderr)
        return 2
    kind, record = rest
    try:
        return import_drafts(kind, Path(record).expanduser(), out_dir)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"variant: {exc}", file=sys.stderr)
        return 2


USAGE = """usage: quirework publish variant [KIND | build KIND [OPTIONS] | import KIND RECORD]

Derived editions of the book, one per audience or medium the project declares
as a variant-KIND procedure. The canonical text is always the master; a variant
is generated from it, never the other way round.

  quirework publish variant                 list the kinds
  quirework publish variant KIND            print that kind's operator playbook (SOP)
  quirework publish variant build KIND      run the mechanical layer: assemble a
                                     blindness-safe bundle, plan one blind,
                                     no-tools seat per chapter, run the plan
                                     through the run engine, and import the
                                     drafts into publish.variants/KIND/_raw/
                                     with receipts.json beside them
  quirework publish variant import KIND RECORD
                                     import a run's finished drafts, after
                                     `sop runs start RECORD` finished or
                                     resumed it

build options:
  --harness H    claude, codex or agy (default: the kind's table in the
                 variant policy, then default_harness, then the one the
                 model's name implies)
  --model M      model for the drafters (default: the kind's table, then
                 default_model)
  --effort E     effort for the drafters (default: the kind's table, then
                 default_effort, then high)
  --par N        how many drafters to run at once (default: 5)
  --only SUBSTR  restrict to chapters whose slug contains SUBSTR
  --timeout S    per-draft timeout in seconds (default: 600)
  --out DIR      edition dir (default: publish.variants/KIND/)
  --plan-only    write the plan and print the commands that run it
  --dry-run      print what would run, write and run nothing

options:
  -h, --help     show this help message and exit
"""


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print(USAGE, end="")
        return 0
    if argv and argv[0] == "build":
        return cmd_build(argv[1:])
    if argv and argv[0] == "import":
        return cmd_import(argv[1:])
    if not argv:
        return _print_sop(None)
    return _print_sop(argv[0])


if __name__ == "__main__":
    raise SystemExit(main())
