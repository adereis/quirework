"""Build an audience or medium variant of the canonical book.

Three entry points, dispatched from ``main``:

    quirework book variant                 list the variant kinds
    quirework book variant <kind>          print the variant SOP (operator playbook)
    quirework book variant build <kind>    run the MECHANICAL layer of the build

``build`` is the part the project's ``variant-common`` procedure hands to a
tool: assemble a *blindness-safe* bundle of the canonical book -- every
``<!-- ... -->`` comment stripped, so the ``@bind``/``@todo``/``@figure`` marks
(the project's own self-understanding) never reach the drafter -- then fan out
one headless, blind ``claude -p`` per chapter. Each drafter is fed
the whole book for grounding but asked to rewrite only its chapter, from a
neutral CWD with tools off (no project ``CLAUDE.md``/memory loads there), so it
is structurally an outside reader. The raw drafts land under
``variants/<kind>/_raw/`` for the operator's fidelity/honesty/voice pass.

The tool stops at raw drafts on purpose: the *analyze* and *compose* steps
(checking each draft against what the book commits to, fixing any drift, wiring
the figures, writing the final ``.qmd`` and ``_quarto.yml``) are human
judgment, and the project's own procedures own them. This mirrors
``coldread analyze``'s blind-``claude -p`` mechanism; the shared rule lives in
docs/workflows.md#execution-and-recovery.
"""

from __future__ import annotations

import concurrent.futures as cf
import re
import subprocess
import sys
from pathlib import Path

from quirework.project import current_project
from quirework.providers.blind import BLIND_SETTINGS
from quirework.providers.blind import NO_SETTING_SOURCES
from quirework.providers.blind import MIN_TOOLS
from quirework.providers.blind import NO_MCP
from quirework.providers.blind import confine
from quirework.providers.blind import seat_dir
from quirework.book import notes as _notes


# Blind drafters run headless from a neutral CWD with a minimal tool allow-list,
# exactly like the cold-read analysts.
# Subagents inherit the project framing and are NOT blind -- never use them
# here (docs/workflows.md#execution-and-recovery).

DEFAULT_TIMEOUT_S = 600
DEFAULT_PAR = 5

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_QMD = re.compile(r"([\w./-]+\.qmd)")
_H1 = re.compile(r"(?m)^#\s+(.*?)\s*(?:\{#[\w:-]+\})?\s*$")
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


def _options():
    """Read policy from the selected project for this operation."""
    from types import SimpleNamespace

    DEFAULT_MODEL = (
        current_project().data("book.variant_policy", {}).get("default_model", None)
    )
    return SimpleNamespace(DEFAULT_MODEL=DEFAULT_MODEL)


def _settings():
    """Resolve paths from the current operation, without import-time state."""
    from types import SimpleNamespace

    SOPS = current_project().path("sops.root")
    DOCS = current_project().path("book.source")
    QUARTO = current_project().path("book.quarto")
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
        f"{current_project().data('book.variant_policy', {}).get('prompt_intro', PROMPT_INTRO).format(KIND=kind_label.upper())}\n\n"
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
# the blind claude -p fan-out
# --------------------------------------------------------------------------- #
def _have_claude() -> bool:
    from shutil import which

    return which("claude") is not None


def run_blind(prompt: str, model: str, timeout: int) -> tuple[bool, str]:
    """One headless, blind ``claude -p``, confined. (ok, text-or-error)."""
    sd = seat_dir()
    try:
        res = subprocess.run(
            confine(
                [
                    "claude",
                    "-p",
                    "--model",
                    model,
                    *BLIND_SETTINGS,
                    *NO_SETTING_SOURCES,
                    *MIN_TOOLS,
                    *NO_MCP,
                ],
                sd,
                runners=["claude"],
            ),
            input=prompt,
            text=True,
            capture_output=True,
            cwd=str(sd),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT after {timeout}s"
    if res.returncode != 0:
        return False, f"claude exited {res.returncode}: {res.stderr.strip()[:200]}"
    body = res.stdout.strip()
    return (bool(body), body or "empty response")


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def _print_sop(kind: str | None) -> int:
    avail = kinds()
    if kind is None:
        print("variant kinds (quirework book variant <kind> prints its SOP):")
        for k in avail:
            print(f"  {k}")
        print("\nbuild the drafts: quirework book variant build <kind>")
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
    model = _options().DEFAULT_MODEL
    par = DEFAULT_PAR
    timeout = DEFAULT_TIMEOUT_S
    only = None
    dry = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print(USAGE, end="")
            return 0
        elif a == "--out":
            out_dir = Path(argv[i + 1])
            i += 2
        elif a == "--model":
            model = argv[i + 1]
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
        elif not a.startswith("-") and kind is None:
            kind = a
            i += 1
        else:
            print(f"build: unknown arg {a!r}", file=sys.stderr)
            return 2
    if kind is None:
        print("usage: quirework book variant build <kind> [...]", file=sys.stderr)
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
    policy = current_project().data("book.variant_policy", {})
    assert_blind(bundle, tuple(policy.get("blind_tells", ())))

    out_dir = out_dir or (current_project().path("book.variants") / kind)
    raw_dir = out_dir / "_raw"
    print(
        f"variant build: {kind}  ({len(chapters)} chapter(s), "
        f"model={model or 'unset'}, par={par})"
    )
    print(f"  bundle: {len(bundle.split())} words, blindness check OK")
    print(f"  raw drafts -> {raw_dir}")
    if dry:
        for c in chapters:
            print(
                f'  would draft  {c["slug"]:28}  "{c["title"]}"  '
                f"({len(figures_for(c['path']))} figure(s))"
            )
        print("dry run: no claude -p launched.")
        return 0
    if model is None:
        print(
            "no model selected: pass --model, or set default_model in the file "
            "book.variant_policy names.",
            file=sys.stderr,
        )
        return 2
    if not _have_claude():
        print(
            "`claude` not found on PATH; cannot run the blind drafters.",
            file=sys.stderr,
        )
        return 127

    raw_dir.mkdir(parents=True, exist_ok=True)
    prompts = {
        c["slug"]: assemble_prompt(
            kind_label, bundle, c, reader, voice, figures_for(c["path"])
        )
        for c in chapters
    }

    def _one(c):
        ok, body = run_blind(prompts[c["slug"]], model, timeout)
        if ok:
            (raw_dir / f"{c['slug']}.md").write_text(body + "\n")
        return c["slug"], ok, body

    fails = 0
    with cf.ThreadPoolExecutor(max_workers=max(1, par)) as pool:
        for slug, ok, body in pool.map(_one, chapters):
            if ok:
                nwords = len((raw_dir / f"{slug}.md").read_text().split())
                print(f"  OK    {slug:28} ({nwords} words)")
            else:
                fails += 1
                print(f"  FAIL  {slug:28} {body}")

    print(f"\n{len(chapters) - fails}/{len(chapters)} drafts written to {raw_dir}")
    print(
        "next (operator): the analyze and compose steps in "
        f"{_rel(procedure('variant-common'))}. Composing the edition "
        "is judgment, so the tool stops here."
    )
    return 1 if fails else 0


USAGE = """usage: quirework book variant [KIND | build KIND [OPTIONS]]

Derived editions of the book, one per audience or medium the project declares
as a variant-KIND procedure. The canonical text is always the master; a variant
is generated from it, never the other way round.

  quirework book variant                 list the kinds
  quirework book variant KIND            print that kind's operator playbook (SOP)
  quirework book variant build KIND      run the mechanical layer: assemble a
                                     blindness-safe bundle, then fan out one
                                     blind `claude -p` per chapter into
                                     book.variants/KIND/_raw/ for the operator's
                                     analyze + compose pass

build options:
  --out DIR      output dir (default: book.variants/KIND/)
  --model M      model for the per-chapter runs (default: the variant
                 policy's default_model)
  --par N        how many chapters to run in parallel (default: 5)
  --only SUBSTR  restrict to chapters whose slug contains SUBSTR
  --timeout S    per-chapter timeout in seconds (default: 600)
  --dry-run      print what would run, run nothing

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
    if not argv:
        return _print_sop(None)
    return _print_sop(argv[0])


if __name__ == "__main__":
    raise SystemExit(main())
