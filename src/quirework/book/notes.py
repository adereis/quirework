"""Extract in-source annotations from the document source.

The volatile half of the old map (to-dos, guidance, genesis, conversation
references, spec bindings) lives as structured HTML comments in a per-chapter
sidecar beside the prose they concern. They are invisible in the Quarto render
and survive a semantic rewrap, but a tool can harvest them into the grouped
views the map used to hold -- always in sync, because they are read from the
text itself.

Convention (one tag per comment; the parenthetical and body are optional):

    <!-- @guidance  how this chapter should read (voice, length, depth, goals) -->
    <!-- @genesis   where it came from: why this subject, the inspiration behind it -->
    <!-- @bind(floor-condition) -->
    <!-- @todo      rephrase the spout example to stop begging the question -->
    <!-- @variant(swe)  the k8s self-monitoring example belongs in the SWE edition -->
    <!-- @ref       conv:c00a7331 -- the Mary's Room exchange that seeded this -->
    <!-- @see(em-dashes)  the long dialogue here is the worst offender -->

Comments may span multiple lines; the body is whitespace-collapsed. Any tag name
is accepted (the list above is convention, not a closed set), so the vocabulary
can grow without code changes -- and shrink: RETIRED_KINDS below is the set that
has been withdrawn, which `check()` reports if one comes back.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from quirework.project import current_project

# <!-- @kind(args)  body --> , possibly multi-line.
_ANNO = re.compile(
    r"<!--\s*@([a-zA-Z][\w-]*)\s*(?:\(([^)]*)\))?\s*(.*?)\s*-->", re.DOTALL
)
# One heading regex for chapters, sidecars and the spec: level captured, then
# the text, then an optional Pandoc attribute block. The attrs are captured
# whole ({#id .class}, {.unnumbered}) and the {#id} extracted by _ATTR_ID, so
# an anchor with classes beside it still resolves and a class-only block never
# leaks into the heading text.
_HEADING = re.compile(r"(?m)^(#{1,6})\s+(.*?)\s*(?:\{([^{}]*)\})?\s*$")
_ATTR_ID = re.compile(r"#([\w:.-]+)")
_WS = re.compile(r"\s+")
# Heading detection must not read non-prose regions: a `# ...` line inside a
# multi-line HTML comment (a sidecar banner) or a YAML comment inside
# frontmatter is not a heading, but the line-anchored regex cannot tell.
# Masking preserves length and newlines, so offsets stay comparable.
_COMMENT = re.compile(r"(?s)<!--.*?-->")
_FRONTMATTER = re.compile(r"(?s)\A---\s*\n.*?\n---[ \t]*(?:\n|\Z)")
# Fenced code blocks: annotation *examples* in documentation live in fences and
# must not be harvested as real annotations.
_FENCE = re.compile(r"(?ms)^[ \t]*(`{3,}|~{3,}).*?^[ \t]*\1[ \t]*$")

# Three or more consecutive all-caps words: the AI-register tell `check()` lints
# BLOCK_KINDS for. Three, not two, so ordinary emphasis ("DO NOT", "NEVER") and
# initialisms in plain prose pass; contract shorthand runs far longer than three.
_CAPS_RUN = re.compile(r"\b[A-Z][A-Z'-]{2,}(\s+[A-Z][A-Z'-]{2,}){2,}")

# Decision-journaling tells `check()` lints CONTRACT_KINDS for: day-precision
# dates, authority citations, review-round references, mark-about-mark
# narration, commit shas. A mark records current state, never history
# (AGENTS.md rule 1); "who settled it, and when" reads as authority and chills
# the re-discussion that discuss-before-change is meant to allow. Deliberately
# NOT matched, each a live false positive in the corpus: bare years and
# year-ranges ("a 2025-2026 frontier run" is a world-fact, not a stamp);
# "retired / refused / withdrawn" (the legitimate present-tense guard idiom);
# "ratified" (content: the New York Declaration); "caught" (chapter vocabulary
# -- real journaling hits co-occur with a date); bare "panel" (quoted book
# prose: "when we opened the panel"); `conv:`-prefixed ids (corpus pointers,
# the @ref convention -- content, not a commit sha).
_JOURNALING = re.compile(
    r"""
      \b20\d\d-\d\d-\d\d\b                    # day-precision ISO date
    | \bauthor'?s?\s+(call|ruling|direction|point|brief)\b
    | \bred-?pen\s+V\d+\b                     # review-round citation
    | \bmultidisc\b | \bblind\s+(draft|panel|alternative)\b
    | \b(this|the)\s+(mark|note|claim|guard)\s+(previously|formerly|used\s+to)\b
    | \bpreviously\s+(said|read|known\s+only)\b
    | \bformerly\s+(said|read)\b
    | \bused\s+to\s+(say|read|carry)\b
    | (?<!conv:)\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b   # commit sha
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Line-distance pins `check()` lints every mark and spec section for: "thirty
# lines above", "at line 22", "142-143 says". AGENTS.md bans them outright --
# the next insertion anywhere above makes the number wrong, and until this
# advisory nothing checked it, so twenty-six went live. Name the scene or the
# phrase instead ("at the careful-steward passage"), which survives every edit
# that does not retire the thing itself. The match runs on the whitespace-
# collapsed body on purpose: the sidecars hard-wrap at 72 columns, so the
# phrase routinely spans a line break and a line-based grep cannot see it,
# which is exactly why the first hand count came back at seven. Deliberately
# NOT matched: a line COUNT with no direction ("in one line", "three lines: a
# scene", "the paragraph runs to four lines"), which is a budget rather than a
# locator and is legitimate throughout.
#
# Three forms were added after the first cut shipped and six live pins walked
# straight past it. "thirteen lines later" slipped because the number-word list
# stopped at twelve, which is the whole failure: a word list is a hole with an
# edge. And the colon address -- `preface:3`, `@ch(how-it-whistles):69`,
# `how-it-whistles.qmd:71` -- carries no "line" keyword at all, so nothing in
# the original pattern could see it; the one live instance was already wrong by
# two. The left side is required to look like a slug, a chapter pointer or a
# filename precisely so "Reader panel: 5 of 6" and a "15:40" timestamp do not
# match, and it reports zero false positives across every mark and the spec.
#
# A BARE "at 7-8" IS STILL INVISIBLE, and that is a measured decision rather
# than an oversight. Matching `at N` or `at N-M` was tried over the whole
# corpus and returned nine hits of which eight were legitimate -- figure needle
# readings ("the program at 0.08"), a ratio ("rings at 100/81/62/43"), a speed,
# a token rate, a percentage. One advisory that is 89% noise teaches the author
# to skip the whole report, which is the failure mode this lint exists to
# avoid. The hole is real and stays open on purpose.
_LINE_PIN = re.compile(
    r"""
      \b(?: one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve
          | thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen
          | twenty|thirty|forty|fifty|sixty|\d+ )
      [-\s]lines?\s+
      (?: above|below|earlier|later|before|after|back|down|up
        | apart|further|past|ahead|from|on )\b
    | \blines?\s+\d+(?:\s*[-–]\s*\d+)?\b            # "line 22", "lines 8-9"
    | \b\d+\s*[-–]\s*\d+\s+(?: says|reads|prints|carries )\b
    | (?: @ch\([a-z0-9-]+\)                         # the colon-address forms:
        | \b[a-z][a-z0-9-]*\.(?:qmd|md|svg)         #   how-it-whistles.qmd:71
        | \b[a-z]+(?:-[a-z0-9]+)+                   #   @ch(how-it-whistles):69
        | \bpreface )                               #   preface:3
      \s*:\s*\d+(?:\s*[-–]\s*\d+)?\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# The structural `@bind(slug)` token, used by bodyless sidecar relationships and
# by companion `Anchors:` joins. Semantic prose cites the framework as `#slug`.
_BIND_REF = re.compile(r"@bind\(([\w-]+)\)")
_BIND_TOKEN = re.compile(r"@bind\(([^)]*)\)")
_LEGACY_CLAIM_REF = re.compile(r"@claim\(([\w-]+)\)")
# A semantic citation of the framework section itself. Exclude Pandoc
# ``{#anchor}`` attributes; those name document locations, not commitments.
_SPEC_REF = re.compile(r"(?<![\w{/])#([A-Za-z][\w-]*)\b")

# `@ch(slug)`: the metadata layer's chapter pointer, resolved by chapters.py.
# A reference form only, never a mark kind -- `<!-- @ch(x) -->` would be
# harvested as a new kind and would join the vocabulary by accident, so
# `check()` rejects it as invariant 9.
_CH_REF = re.compile(r"@ch\(([^)]*)\)")


# Tokens in an **Anchors:** run that are unmistakably a chapter pointer: an
# explicit `.qmd`, or the retired numeric vocabulary the migration is clearing.
# Anything else in a run is inert prose and is left alone, which is the
# conservatism the run's parser has always promised; this is only the set that
# must never be silently skipped.
def _bare(tok: str) -> str:
    """A run's token reduced to a chapter stem: `sources.qmd` -> `sources`."""
    return tok[:-4] if tok.endswith(".qmd") else tok


def _chapter_shaped(tok: str) -> bool:
    import quirework.book.chapters as _options_quirework_book_chapters

    return (
        tok.endswith(".qmd")
        or tok in _options_quirework_book_chapters._options().RETIRED_TOKENS
    )


# The imperative register the spec must not use: a commitment is stated
# ("temperature has no zero"), never ordered ("NEVER restore temperature") —
# a well-stated commitment makes its own instruction derivable. All-caps forms
# only: indicative prose legitimately says "never".
_IMPERATIVE = re.compile(r"\b(NEVER|DO NOT|DON'T|MUST NOT|MAY NOT|BANNED)\b")


# The reader-facing companion collections: the worked thought experiments and
# the real-case evidence. AGENTS.md makes them the deliberate exception to
# canon self-containment (a mark may point at them, because they are tracked
# and stable where `scratchpad/` notes are allowed to vanish), and the exception
# runs both ways: the entries cite the spec by slug and quote the book by the
# paragraph. Neither direction had a gate. `notes --check` never read these
# directories at all, so a spec rename or chapter edit could strand a citation
# with every sidecar gate green.
def companion_dirs() -> tuple[str, ...]:
    """The reader-facing companion collections this book declares, if any.

    `book.companion_dirs` names them. The default is none: a book with no
    companion collection has nothing here to check, and naming another
    project's directories would check a tree this one does not have.
    """
    return tuple(current_project().get("book.companion_dirs", ()) or ())


# `_template.md` carries placeholder citations (`<chapter files and @bind
# slugs>`), not real ones; it is skipped so a template edit cannot fail a gate
# about the entries.
COMPANION_SKIP = {"_template.md"}

# A quotation attributed to the book. Both forms are used by the entries and
# both have failed: a double-quoted span lifted from a chapter, and a bolded
# span attributed to the framework. The length floor keeps single-word emphasis
# (**resolved**) out;
# the ceiling keeps a runaway span from swallowing a paragraph.
# Whitespace-only differences are never misquotes and _norm already collapses
# them: the book wraps one sentence per line and the entries wrap at 72
# columns, so a quotation spanning a sentence boundary is wrapped differently
# on each side.
# An entry's header paragraph carries a "**Anchors:**" run naming the chapters
# and commitments its position rests on. It is READER-FACING prose and stays
# that way: minting a parallel @-mark would put the same fact in two places, and
# a duplicated fact is the drift this whole layer exists to prevent. So the
# visible run is the single owner and the check reads it where it stands.
_ANCHORS_RUN = re.compile(r"\*\*Anchors:\*\*(.*?)(?:\n[ \t]*\n|\Z)", re.S)
_BACKTICKED = re.compile(r"`([^`]+)`")


def _code_spec_refs(text: str) -> list[tuple[str, int]]:
    """Framework ``#slug`` citations inside Markdown code spans.

    Companion prose uses code spans to distinguish framework slugs from local
    Markdown anchors and URL fragments. Return each slug with its absolute
    offset in ``text`` so diagnostics retain correct line numbers.
    """
    out: list[tuple[str, int]] = []
    for span in _BACKTICKED.finditer(text):
        content = span.group(1)
        if "://" in content:
            continue
        for ref in _SPEC_REF.finditer(content):
            out.append((ref.group(1), span.start(1) + ref.start()))
    return out


# Only the curly form can be matched by a delimiter pair, because the marks are
# the only place curly quotes appear. Straight quotes are the entries'
# convention and open and close identically, so they are paired by ALTERNATION
# in `_quoted_spans` instead.
_QUOTED_SPAN = re.compile(r"[“”]((?:[^“”]|\n(?!\s*\n)){25,400})[“”]")
# A bold span stays single-line, and the asymmetry is load-bearing rather than
# an oversight. A quotation wraps, because these files hard-wrap at 72 columns;
# a bold label never does. Letting `**` cross a newline pairs one label's close
# with the next one's open and swallows the prose between, which was a quarter
# of the advisories in the run that tried it.
_BOLD_SPAN = re.compile(r"\*\*([^*\n]{25,400})\*\*")
_ELIDED = re.compile(r"\s*(?:\.\.\.|…)\s*")


def _quoted_spans(block: str) -> list[tuple[str, int]]:
    """Quotations in one block, INCLUDING the ones that wrap.

    A straight quote is the same character opening and closing, so a regex with
    a delimiter pair cannot tell one from the other and has to be told where a
    span may not run to. Excluding the newline was that fence, and it is the
    wrong one: these files hard-wrap at 72 columns, so it silently skipped
    every quotation longer than about ten words -- which is the length at which
    a span stops being a coined term and starts being a citation. The check saw
    10 spans across the 24 entries where it should see 38, and both live
    defects it was missing were wrapped canon sentences an edit had moved out
    from under.

    The honest fence is that straight quotes ALTERNATE: pair them 1-2, 3-4,
    within a block. A pair enclosing a paragraph break is a mispairing rather
    than a long quotation, and dropping those is what stops one quotation's
    close being read as the next one's open.
    """
    out: list[tuple[str, int]] = []
    marks = [m.start() for m in re.finditer(r'"', block)]
    for a, b in zip(marks[0::2], marks[1::2]):
        span = block[a + 1 : b]
        if re.search(r"\n\s*\n", span) or not 25 <= len(span) <= 400:
            continue
        out.append((span, a))
    for m in _QUOTED_SPAN.finditer(block):
        out.append((m.group(1), m.start()))
    return out


def _blank(m: re.Match) -> str:
    """The matched span with every non-newline blanked (length-preserving)."""
    return "".join(c if c == "\n" else " " for c in m.group(0))


def _mask_fences(text: str) -> str:
    """Blank fenced code (keeping length + newlines, so offsets/lines are stable)."""
    return _FENCE.sub(_blank, text)


def _mask_nonprose(text: str) -> str:
    """Blank HTML comments and YAML frontmatter, for heading detection only.

    Length-preserving like _mask_fences, so heading offsets computed on the
    masked text index straight into the unmasked one."""
    return _COMMENT.sub(_blank, _FRONTMATTER.sub(_blank, text))


DEFAULT_GLOBS = ("*.qmd", "*.md")

# Developer-guide files carry annotation *examples* in prose (e.g. the literal
# `<!-- @see(slug) rider -->` in AGENTS.md), not book content. Skip them when
# globbing a directory, so the harvest is the book's marks, not the repo's own
# guidance. An explicitly named --path still reads them (nothing is masked).
SKIP_NAMES = {"AGENTS.md", "CLAUDE.md", "GEMINI.md"}

# A chapter's metadata marks live in a sidecar `<dir>/_marks/<stem>.md` beside
# it, never inline in the prose. scan() attributes a sidecar's marks to the
# sibling chapter `<dir>/<stem>.qmd`; `notes --check` verifies the layout stays
# honest. Book-wide review notes live in this manifest under docs/canon/ and are
# the target a chapter's @see(slug) resolves against.
MARKS_DIR = "_marks"
BOOK_NOTES_FILE = "_review-notes.md"

# The framework spec (docs/canon/): the central commitments register. Its
# level-2 sections (`## Title {#slug}`) are the binding namespace — a chapter's
# @bind(slug) resolves against these anchors, and any number of chapters may
# bind one section. A slug the spec does not declare is a hard error: there is
# no independent claim layer.
SPEC_FILE = "_framework.md"

# The audience split, and the single place it is declared. These three kinds are
# HUMAN-FACING and are surfaced in the red-pen review block; @bind is structural
# and @todo is working state, so both deliberately stay out of it.
# redpen imports this rather than restating the list, because the split has to
# hold in two places at once: the kinds a block prints directly, and the kinds a
# chapter's @see(slug) pointer is allowed to resolve to. It once held only in the
# first, so @see became a back door that echoed binding shorthand into the
# author's review copy — twelve of eighteen pointers, naming notes the .docx
# never printed. `check()` also lints these three for AI-register prose.
BLOCK_KINDS = ("guidance", "genesis", "variant")

# The discuss-before-change layers `check()` lints for decision-journaling.
# Guidance carries current state, so "author call, dated" in one reads as
# settled authority a later session must not question -- the opposite of the
# intended openness. @genesis is deliberately absent: an origin story is
# history by design, and its dates are content. The working and provenance
# kinds (@todo, @ref, @source) legitimately carry dates and stay out.
CONTRACT_KINDS = ("guidance",)

# Withdrawn vocabulary. The tag set is open by design, which is why a retired
# kind can re-seed silently: a session reading an old SOP or an old session log
# writes one and nothing objects. Each of these had a live run and was cleared.
# @expand ("add X here") is a @todo whose whole semantics argues for new prose,
# against the standing rule that a finding is not a license to add any; a
# reaction with no action is @guidance if it is about how the chapter reads and
# @todo if it is a request, so @feedback had no residue of its own. @flavor was
# renamed to @variant. @decision is the one AGENTS.md already forbids in words
# ("do not reintroduce one"): an open call is a @todo, and a settled one folds
# into the spec section or guidance as present-tense rationale.
RETIRED_KINDS = {
    "concept": "derive chapter summaries from current prose when needed",
    "claim": "renamed to @bind; the framework owns the commitment",
    "expand": "a request is @todo; adding prose is not a default",
    "feedback": "a reaction is @guidance (how it reads) or @todo (a request)",
    "flavor": "renamed to @variant",
    "intuition": "never adopted; the author's own words are @genesis/@guidance",
    "decision": "an open call is @todo; a settled one is a spec section",
}

# One-line gloss per kind, for `notes --list`. The tag set is open and `--list`
# only shows kinds actually present, so this maps the kinds in live use; a new
# (or revived) kind lists without a gloss until one is added here.
KIND_DESCRIPTIONS = {
    "bind": "chapter-to-framework bindings",
    "guidance": "how a chapter should read (the author's intent)",
    "genesis": "where a chapter came from (origin, not direction)",
    "todo": "open working state / the agent's worklist",
    "variant": "what a derived edition should do differently here",
    "figure": "built illustrations (docs/canon/illustrations/)",
    "ref": "prior-art / conversation pointers",
    "source": "per-claim citation",
    "see": "pointer to a book-wide review note",
}

# The `--primer` preset: what a fresh session must act from or stay consistent
# with -- the bindings and the constraints on how the book reads
# (@guidance). The framework spec is printed first and owns what is true.
# Not @genesis: that is provenance a session reaches for deliberately.
# Absent members are skipped silently (a preset is not a typo).
PRIMER_KINDS = ("bind", "guidance")


@dataclass
class Annotation:
    kind: str
    args: list[str]
    body: str
    file: str
    line: int
    section: str | None
    # The file the mark was actually read from, which is the sidecar and not
    # `file` (the chapter it is attributed to). `line` counts within THIS file,
    # so `source:line` is the only pair that resolves; `file:line` names a
    # chapter and a line inside its sidecar, and the chapter is usually shorter.
    source: str = ""


def _headings(text: str) -> list[tuple[int, str]]:
    """(char offset, label) for each heading; label prefers the {#id}.

    Comments and frontmatter are masked first, so a `# ...` line inside a
    sidecar banner or a YAML comment is never taken for a heading."""
    out = []
    for m in _HEADING.finditer(_mask_nonprose(text)):
        anchor = _ATTR_ID.search(m.group(3)) if m.group(3) else None
        out.append((m.start(), anchor.group(1) if anchor else m.group(2).strip()))
    return out


def _section_for(headings: list[tuple[int, str]], pos: int) -> str | None:
    label = None
    for off, lbl in headings:
        if off <= pos:
            label = lbl
        else:
            break
    return label


def scan_text(text: str, rel: str, source: str | None = None) -> list[Annotation]:
    """Harvest one file's marks. `rel` is what the marks are attributed to (the
    chapter); `source` is where they were read from (the sidecar), and defaults
    to `rel` for a file that indexes itself."""
    text = _mask_fences(text)  # offsets/line numbers preserved; fenced examples blanked
    headings = _headings(text)
    src = source or rel
    out = []
    for m in _ANNO.finditer(text):
        kind = m.group(1).lower()
        args = [a.strip() for a in (m.group(2) or "").split(",") if a.strip()]
        body = _WS.sub(" ", m.group(3)).strip()
        line = text.count("\n", 0, m.start()) + 1
        out.append(
            Annotation(
                kind, args, body, rel, line, _section_for(headings, m.start()), src
            )
        )
    return out


def _chapter_for(f: Path) -> Path:
    """The prose file a source path is attributed to. A sidecar
    `<dir>/_marks/<stem>.md` is attributed to its sibling chapter
    `<dir>/<stem>.qmd`; every other file is attributed to itself. This is the one
    place the sidecar↔chapter mapping lives — the harvest reads the sidecar's
    text (so its mirrored headings drive section attribution) but stamps every
    mark with the chapter path, so `--by chapter`, reading order, and the chapter
    title all resolve exactly as they did when the marks were inline."""
    if f.parent.name == MARKS_DIR:
        return f.parent.parent / (f.stem + ".qmd")
    return f


def _configured_book_file(docs, key, fallback):
    project = current_project()
    if project.get(key) is None:
        return Path(docs) / project.chapter_subdir / fallback
    configured = project.path(key)
    source = project.path("book.source", "docs")
    if configured.is_relative_to(source):
        return Path(docs) / configured.relative_to(source)
    return configured


def _spec_path(docs=None) -> Path:
    """This project's spec file. `_framework.md` is one book's choice of name."""
    docs = (
        Path(docs)
        if docs is not None
        else current_project().path("book.source", "docs")
    )
    return _configured_book_file(docs, "book.spec", SPEC_FILE)


def _spec_name(docs=None) -> str:
    return _spec_path(docs).name


def spec_sections(docs: Path | None = None) -> list[tuple[str | None, str, str]]:
    """(anchor, title, body) for each level-2 section of the framework spec,
    in file order. The anchor is None where a section is missing its {#slug}
    (a hard `check()` problem). Empty if the spec does not exist."""
    docs = (
        Path(docs)
        if docs is not None
        else current_project().path("book.source", "docs")
    )
    spec = _spec_path(docs)
    if not spec.exists():
        return []
    text = _mask_fences(spec.read_text())
    lvl2 = []
    for m in _HEADING.finditer(_mask_nonprose(text)):
        if len(m.group(1)) != 2:
            continue
        anchor = _ATTR_ID.search(m.group(3)) if m.group(3) else None
        lvl2.append(
            (
                m.start(),
                m.end(),
                m.group(2).strip(),
                anchor.group(1) if anchor else None,
            )
        )
    out = []
    for i, (_, end, title, anchor) in enumerate(lvl2):
        stop = lvl2[i + 1][0] if i + 1 < len(lvl2) else len(text)
        out.append((anchor, title, text[end:stop].strip()))
    return out


def spec_slugs(docs: Path | None = None) -> set[str]:
    """The binding namespace: every anchored level-2 section of the spec."""
    return {slug for slug, _, _ in spec_sections(docs) if slug}


def scan(paths: list[Path] | None = None) -> list[Annotation]:
    """Harvest annotations from every doc under the given paths (default docs/)."""
    roots = paths or [current_project().path("book.source", "docs")]
    out: list[tuple[Path, str, str]] = []  # (file to read, chapter, source)
    for root in roots:
        root = Path(root)
        files = (
            [root]
            if root.is_file()
            else [
                f
                for g in DEFAULT_GLOBS
                for f in root.rglob(g)
                if f.name not in SKIP_NAMES
            ]
        )
        for f in sorted(set(files)):
            out.append((f, _rel(_chapter_for(f)), _rel(f)))
    annos: list[Annotation] = []
    for f, rel, src in out:
        annos.extend(scan_text(f.read_text(), rel, src))
    return annos


_QMD_TOKEN = re.compile(r"([\w./-]+\.qmd)")
_FRONT_TITLE = re.compile(r'(?m)^title:\s*["\']?(.+?)["\']?\s*$')


def _reading_order() -> dict[str, int]:
    """Map each rel path to its position in the book, read from docs/_quarto.yml.

    Lets `--by chapter` follow the book's order (the welcome landing first, part
    dividers in place) instead of a lexical filename sort, which misplaces
    index.qmd and part-two.qmd. Empty if the config is absent (e.g. a custom
    --path scan), in which case the caller falls back to sorting by path.
    """
    try:
        text = (current_project().path("book.quarto", "docs/_quarto.yml")).read_text()
    except OSError:
        return {}
    order: dict[str, int] = {}
    for line in text.splitlines():
        if line.lstrip().startswith("#"):  # skip YAML comments
            continue
        m = _QMD_TOKEN.search(line)
        if m:
            order.setdefault(f"docs/{m.group(1)}", len(order))
    return order


def _chapter_title(rel: str) -> str:
    """A human label for a source file: its first heading, else a frontmatter
    title (some front-matter pages are titled via YAML, with no `#`), else the stem."""
    try:
        text = _mask_fences((current_project().root / rel).read_text())
    except OSError:
        return Path(rel).stem
    m = _HEADING.search(_mask_nonprose(text))
    if m:
        return m.group(2).strip()
    fm = re.match(r"(?s)\A---\s*\n(.*?)\n---\s*\n", text)
    if fm:
        tm = _FRONT_TITLE.search(fm.group(1))
        if tm:
            return tm.group(1).strip()
    return Path(rel).stem


def _print_spec_toc(docs: Path | None = None) -> None:
    """The framework spec's table of contents, printed at the top of the
    primer: the commitments register is the book's skeleton, so a fresh
    session reads it before the per-chapter marks. Silent while the spec has
    no sections (early in the migration)."""
    sections = spec_sections(docs)
    if not sections:
        return
    print(
        f"spec — {_rel(_spec_path(docs))} "
        f"({len(sections)} section(s); read it before the marks):"
    )
    for slug, title, _ in sections:
        anchor = f"#{slug}" if slug else "(no anchor)"
        print(f"  {anchor:<26} {title}")


def _render_by_kind(annos: list[Annotation]) -> None:
    by_kind: dict[str, list[Annotation]] = {}
    for a in annos:
        by_kind.setdefault(a.kind, []).append(a)
    for kind in sorted(by_kind):
        items = by_kind[kind]
        print(f"\n@{kind}  ({len(items)})")
        print("-" * 60)
        for a in items:
            argstr = f"({', '.join(a.args)}) " if a.args else ""
            sec = f"  [{a.section}]" if a.section else ""
            # source, not file: the mark lives in the sidecar, and the sidecar's
            # name carries the chapter stem, so nothing is lost by naming it.
            payload = f"{argstr}{a.body}".rstrip()
            print(f"  {a.source or a.file}:{a.line}{sec}\n    {payload}")
    print(f"\ntotal: {len(annos)} mark(s) across {len(by_kind)} kind(s)")


def _render_by_chapter(annos: list[Annotation]) -> None:
    by_file: dict[str, list[Annotation]] = {}
    for a in annos:
        by_file.setdefault(a.file, []).append(a)
    order = _reading_order()
    # book order where known (welcome landing first, part dividers in place); path sort
    # for anything the config doesn't list (a stray file, or a custom --path scan).
    for rel in sorted(by_file, key=lambda r: (order.get(r, len(order)), r)):
        items = sorted(by_file[rel], key=lambda a: a.line)
        print(f"\n{rel} — {_chapter_title(rel)}  ({len(items)})")
        # The L-numbers below count within the sidecar, not the chapter, so name
        # the file they index whenever the two differ.
        srcs = {a.source for a in items if a.source and a.source != rel}
        for src in sorted(srcs):
            print(f"  marks: {src}")
        print("-" * 60)
        for a in items:
            argstr = f"({', '.join(a.args)})" if a.args else ""
            suffix = f"  {a.body}" if a.body else ""
            print(f"  L{a.line:<4} @{a.kind}{argstr}{suffix}")
    print(f"\ntotal: {len(annos)} mark(s) across {len(by_file)} chapter(s)")


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(current_project().root))
    except ValueError:
        return str(p)


def _norm(text: str) -> str:
    """Collapse whitespace, so a quotation matches across two wrap styles."""
    return _WS.sub(" ", text).strip()


def _searchable(quote: str) -> str:
    """A quotation reduced to what must actually exist in canon.

    Case is dropped and trailing punctuation trimmed, because both differ
    legitimately in a correct quotation: lifting a sentence into the middle of
    another lowercases its initial capital, and the book is written in
    American English, where the comma or period goes inside the closing quote
    whether or not it belongs to the quoted sentence. Keeping either strict
    flagged three correct quotations for every real one, which is how an
    advisory teaches the author to ignore it."""
    return _norm(quote).rstrip(".,;:").lower()


def companion_files(root: Path | None = None) -> list[Path]:
    """Every entry in the reader-facing companion collections."""
    base = Path(root) if root is not None else current_project().root
    out: list[Path] = []
    for d in companion_dirs():
        if (base / d).is_dir():
            out += [
                p
                for p in sorted((base / d).glob("*.md"))
                if p.name not in COMPANION_SKIP
            ]
    return out


def _canon_corpus(docs: Path) -> str:
    """Everything an entry may legitimately be quoting: the prose, the marks,
    the spec, the curated index and the book-wide notes, whitespace-collapsed
    into one searchable string. Built from raw file text rather than parsed
    bodies so a quotation that straddles a comment or a heading still matches;
    the collapse is what makes a sidecar's hanging-indented mark body
    searchable as the single sentence it is."""
    parts: list[str] = []
    for pat in ("*.qmd", "*.md"):
        for f in sorted(docs.rglob(pat)):
            parts.append(f.read_text())
    return _searchable("\n".join(parts))


def _blocks(text: str) -> list[tuple[str, int]]:
    """(block, offset) for each blank-line-separated block.

    Offsets are taken from the separator matches themselves, so a gap of
    several blank lines cannot drift the line numbers reported for every
    block after it (a fixed `+ 2` once assumed the separator was exactly
    one blank line)."""
    out, pos = [], 0
    for m in re.finditer(r"\n\s*\n", text):
        out.append((text[pos : m.start()], pos))
        pos = m.end()
    out.append((text[pos:], pos))
    return out


def _attributed_spans(
    block: str, stems: set[str], spec_name: str = SPEC_FILE
) -> list[tuple[str, int]]:
    """Quotations in one block that are attributed to canon, with offsets.

    Two filters do the discriminating, and both were forced by real false
    positives on the live collections. A bold span opening a block or a list
    item is one of the entries' own subheadings ("**It costs the reader
    first.**"), never a quotation. And attribution is read from the 200
    characters *before* the span rather than from the block as a whole: an
    entry quotes Nozick, an objector's voice and its own falsifier wording in
    the same paragraph that cites a chapter, so block-level attribution flags
    all three."""
    out: list[tuple[str, int]] = []
    candidates = [(sp, i, False) for sp, i in _quoted_spans(block)]
    candidates += [(m.group(1), m.start(), True) for m in _BOLD_SPAN.finditer(block)]
    for span, start, is_bold in candidates:
        before = block[:start]
        # Block-start or list-marker only. NOT a bare line start: the entries
        # wrap at 72 columns, so a mid-sentence quotation lands at the head of
        # a line about as often as not, and excluding those loses the exact
        # shape this check exists for.
        if is_bold and re.search(
            r"(?:\A\s*|(?:\A|\n)[ \t]*(?:[-*>]|\d+\.)[ \t]+)$", before
        ):
            continue  # the entry's own heading
        window = before[-200:]
        # `@ch()` is read alongside the bare stems: attribution is what decides
        # whether a span is measured against the book at all, so a form this
        # predicate does not know makes the advisory quietly NARROWER -- fewer
        # quotations checked, and no message saying so. That is the same silent
        # degradation anchor_pairs had, in a second place.
        if not (
            _BIND_REF.search(window)
            or _code_spec_refs(window)
            or _CH_REF.search(window)
            or spec_name in window
            or any(s in window for s in stems)
        ):
            continue
        out.append((span, start))
    return out


def chapter_bindings(docs: Path | None = None) -> dict[str, set[str]]:
    """chapter stem -> the spec slugs its sidecar actually binds."""
    out: dict[str, set[str]] = {}
    roots = [docs] if docs is not None else None
    for a in scan(roots):
        if a.kind == "bind" and a.args:
            out.setdefault(Path(a.file).stem, set()).add(a.args[0])
    return out


def anchor_pairs(
    text: str, stems: set[str], spec: set[str]
) -> tuple[list[tuple[str | None, str]], list[str]]:
    """(pairs, unresolved) for the anchors in an entry's **Anchors:** run.

    The run is prose, so it is read positionally: a chapter reference sets the
    chapter that the `@bind(slug)` tokens after it belong to, until the next
    one. A bare `#slug` is the cases/ collection's form and names a spec
    section directly, with no chapter claimed, so it yields None.

    Three buckets, and the third is the point. A token is a chapter reference
    (`@ch(slug)`, or a bare stem while the migration is in flight), a slug, or
    inert -- and a run legitimately trails off into prior art, so inert is the
    common case and stays silent. What must NOT stay silent is a token that is
    unmistakably a chapter pointer and resolves to nothing: the old grammar
    ended `elif tok in stems`, so an unknown one fell off the chain, `current`
    kept its previous value, and every `@bind` after it was attributed to the
    wrong chapter or to none. Invariant 8 then had nothing left to check and
    printed nothing -- a rename could strand all 21 entries with every gate
    green. Those are returned as `unresolved` and reported by the caller.
    """
    m = _ANCHORS_RUN.search(text)
    if not m:
        return [], []
    run = _WS.sub(" ", m.group(1))
    pairs: list[tuple[str | None, str]] = []
    unresolved: list[str] = []
    current: str | None = None
    for t in _BACKTICKED.finditer(run):
        tok = t.group(1).strip()
        binding = _BIND_REF.search(tok)
        ch = _CH_REF.search(tok)
        if binding:
            pairs.append((current, binding.group(1)))
        elif ch:
            if ch.group(1) in stems:
                current = ch.group(1)
            else:
                current = None
                unresolved.append(tok)
        elif tok.startswith("#") and tok[1:] in spec:
            pairs.append((None, tok[1:]))
        elif _bare(tok) in stems:
            # A run may name a file with its extension (`sources.qmd`'s floor
            # entry), which is a real pointer and not a stale one; the stem is
            # what the binding map is keyed by.
            current = _bare(tok)
        elif _chapter_shaped(tok):
            current = None
            unresolved.append(tok)
    return pairs, unresolved


def coverage(
    docs: Path | None = None, root: Path | None = None
) -> tuple[dict[str, list[str]], list[tuple[str, str]]]:
    """Which commitments the companion collections actually exercise.

    Returns (slug -> the entries anchored to it, unexercised (anchor, title)
    pairs in spec order). This is the measure the project did not have: the
    marks say which chapters *express* a commitment, and this says which ones
    have been *worked* against a case or a thought experiment.

    It is a map and never a scorecard. A section with no entry is not a gap by
    itself -- the vocabulary-discipline, positioning and spec-organizing
    sections belong to no chapter and to no exercise either ("The reverse
    direction is not an invariant" in AGENTS.md, for the same reason).
    """
    docs = (
        Path(docs)
        if docs is not None
        else current_project().path("book.source", "docs")
    )
    spec = spec_slugs(docs)
    stems = {p.stem for p in docs.rglob("*.qmd")}
    by_slug: dict[str, list[str]] = {}
    for f in companion_files(root if root is not None else docs.parent):
        if f.name == "README.md":
            continue
        pairs, _ = anchor_pairs(_mask_fences(f.read_text()), stems, spec)
        for _, slug in pairs:
            if slug in spec:
                by_slug.setdefault(slug, []).append(f.stem)
    unexercised = [
        (anchor, title)
        for anchor, title, _ in spec_sections(docs)
        if anchor and anchor not in by_slug
    ]
    return by_slug, unexercised


def companion_check(
    docs: Path, spec: set[str], root: Path | None = None
) -> tuple[list[str], list[str], list[str]]:
    """Resolve the companion collections against canon, in both directions.

    Returns (problems, advisories). A `@bind(slug)` citation that names no
    spec section is a hard problem: it is a broken pointer with one right
    answer, the same defect invariant 5 gates inside the book, and the entries
    are cited from canon marks in turn.

    A quotation that appears nowhere in canon is an advisory, because the
    detection is necessarily a heuristic -- an entry quotes Nozick and Parfit
    as well as the book, and only a paragraph that also cites canon is
    considered. What it catches is the failure that has actually happened:
    prose or a mark body edited out from under a live quotation."""
    problems: list[str] = []
    advisories: list[str] = []
    anchors: list[str] = []
    corpus = _canon_corpus(docs)
    stems = {p.stem for p in docs.rglob("*.qmd")}
    binds = chapter_bindings(docs)
    for f in companion_files(root):
        if f.name == "README.md":
            continue
        text = _mask_fences(f.read_text())
        rel = _rel(f)
        anchor_run = _ANCHORS_RUN.search(text)
        for m in _BIND_TOKEN.finditer(text):
            if not re.fullmatch(r"\s*[\w-]+\s*", m.group(1)):
                ln = text.count("\n", 0, m.start()) + 1
                problems.append(
                    f"{rel}:{ln}: @bind takes exactly one slug. Write one "
                    f"relationship token per framework section."
                )
        pairs, unresolved = anchor_pairs(text, stems, spec)
        for tok in unresolved:
            problems.append(
                f"{rel}: **Anchors:** names `{tok}`, which is no chapter. The "
                f"anchors after it are attributed to nothing, so the join that "
                f"holds this entry to the book is silently broken."
            )
        if not pairs:
            # A README indexes the collection and anchors nothing; every other
            # file is an entry, and an entry with no resolving anchors has
            # nothing holding it to the book.
            anchors.append(
                f"  {rel}: no **Anchors:** run resolves, so nothing holds this "
                f"entry to a commitment when canon moves"
            )
        for chapter, slug in pairs:
            if chapter is None or slug not in spec:
                continue  # the slug half is invariant 7's to report
            if slug not in binds.get(chapter, set()):
                holder = sorted(k for k, v in binds.items() if slug in v)
                problems.append(
                    f"{rel}: anchored to `{chapter}` `@bind({slug})`, which "
                    f"{chapter} does not bind. Bound by: "
                    f"{', '.join(holder) or 'no chapter'}. Repoint the anchor "
                    f"at the chapter that expresses it."
                )
        for m in _BIND_REF.finditer(text):
            if m.group(1) in spec:
                if (
                    anchor_run is None
                    or not anchor_run.start() <= m.start() < anchor_run.end()
                ):
                    ln = text.count("\n", 0, m.start()) + 1
                    problems.append(
                        f"{rel}:{ln}: @bind({m.group(1)}) appears outside the "
                        f"**Anchors:** run. Use '#{m.group(1)}' in semantic "
                        f"prose; @bind is only the relationship join."
                    )
                continue
            ln = text.count("\n", 0, m.start()) + 1
            problems.append(
                f"{rel}:{ln}: @bind('{m.group(1)}') names no section of "
                f"{_spec_name(docs)}. The entry cites a commitment that has been "
                f"renamed or folded away: repoint it at the section that "
                f"holds the content now."
            )
        for m in _LEGACY_CLAIM_REF.finditer(text):
            ln = text.count("\n", 0, m.start()) + 1
            problems.append(
                f"{rel}:{ln}: @claim({m.group(1)}) is retired. Use "
                f"@bind({m.group(1)}) in **Anchors:** or '#{m.group(1)}' "
                f"in semantic prose."
            )
        for slug, pos in _code_spec_refs(text):
            if slug in spec:
                continue
            ln = text.count("\n", 0, pos) + 1
            problems.append(
                f"{rel}:{ln}: spec section '#{slug}' does not exist in "
                f"{_spec_name(docs)}. The entry cites a commitment that has been "
                f"renamed or folded away."
            )
        for block, offset in _blocks(text):
            for span, pos in _attributed_spans(block, stems, _spec_name(docs)):
                quote = _norm(span)
                # An elided quotation is deliberately partial; measure its
                # longest piece, or the ellipsis alone reports it as absent.
                probe = max(_ELIDED.split(quote), key=len)
                if len(probe.split()) < 5 or _searchable(probe) in corpus:
                    continue
                ln = text.count("\n", 0, offset + pos) + 1
                advisories.append(
                    f"  {rel}:{ln}: {quote[:96]}{'…' if len(quote) > 96 else ''}"
                )
    return problems, advisories, anchors


def check(docs: Path | None = None) -> int:
    """Lint the split-metadata layout; replaces physical co-location as the
    drift guard. Ten hard invariants (any failure → exit 1):

      1. no book `.qmd` under docs/ carries an @-mark (marks live in sidecars);
      2. every `_marks/<stem>.md` has a sibling `<stem>.qmd` (no orphan sidecar);
      3. every heading in a sidecar resolves to a real heading/anchor in its
         chapter (the mirror can never drift from the prose it indexes);
      4. the framework spec's level-2 sections each carry an {#slug} anchor,
         unique across the spec (they are the binding namespace);
      5. every @bind is bodyless and its slug names a spec section (any
         number of chapters may bind the same one);
      6. every @see(slug) resolves to a book-wide note in _review-notes.md
         (pointers into the spec are bindings or inline #slug refs,
         never @see);
      7. every @bind(slug) cited in the companion collections
         (`book.companion_dirs`) names a spec section — the entries are canon's
         one licensed outward dependency, and the citation runs back in.
      8. every anchor in an entry's **Anchors:** run names a chapter that
         really binds that slug. Invariant 7 gates the slug; this gates the
         chapter attributed to it, which nothing checked, so an anchor could
         name a chapter that had never expressed the commitment.
      9. every chapter reference in an **Anchors:** run resolves, and `@ch` is
         never used as a mark kind. A chapter-shaped token that resolves to
         nothing used to be skipped in silence, which turned invariant 8 into
         a no-op rather than a failure.
     10. `docs/_quarto.yml` and the chapter files on disk are a bijection.
         The dual of invariant 2: every listed file exists, and every chapter
         on disk is listed, so nothing renders nowhere and nothing is named
         that is not there.

    Plus six advisories, printed but never fatal.

    Quotations in the companion collections that appear nowhere in canon.
    Heuristic by necessity (the entries quote Nozick and Searle too, so only a
    paragraph that also cites canon is read), and an advisory for that reason;
    what it catches is a chapter or other metadata edited out from under a live
    quotation, which no gate saw because none of them read `exercises/`.

    Spec sections in the imperative or contract register (a
    NEVER / DO NOT / BANNED order, or an all-caps run). The spec states and
    prices — "temperature has no zero" — and never orders, because a
    well-stated commitment makes its own instruction derivable; an imperative
    is almost always an under-stated commitment.

    Marks that reference analyses, scratch notes or legacy ideas, which the
    self-containment rule forbids. Retaining evidence does not make it a
    semantic owner. Stable reader-facing companions remain permitted. The
    check stays advisory; it does not decide whether a finding was adopted.

    Marks written in a retired kind (`RETIRED_KINDS`) are hard errors. Accepting
    any tag name lets the vocabulary grow without a code change, so an old SOP
    or session log can otherwise re-seed a withdrawn kind silently.

    And block-surfaced marks (`BLOCK_KINDS`) written in the AI register, detected
    by a run of three or more consecutive all-caps words — contract shorthand
    that human-facing prose never uses. These three kinds are what the author
    reads in the review .docx, so such a body is miscast: it is almost always a
    preservation contract or a decision
    record filed as authorial voice. Advisory rather than fatal because the
    register is a judgment call and the fix is a recast, not a mechanical edit.

    And decision-journaling in the contract layers (`CONTRACT_KINDS` and the
    spec's section bodies): dates, authority citations ("author call", a
    red-pen round, a panel), commit shas, and mark-about-mark narration. A
    mark — and a spec section — records current state, never history
    (AGENTS.md rule 1): the story of who settled a call and when belongs in
    git, and inside guidance it reads as authority that chills the
    re-discussion discuss-before-change is meant to allow. The pattern set and
    its recorded false-positive exclusions live at `_JOURNALING`; @genesis is
    exempt by design (an origin story is history).

    And dangling inline framework `#slug` references in any mark body. Inline
    refs are not part of the binding graph, and a section merge or rename is
    exactly the edit that dangles them; they resolve against the spec anchors.

    Figure sources are deliberately NOT checked here: a @figure mark may be
    drafted before its SVG is built (docs/AGENTS.md, "Figures"), and
    `figures --check`
    already gates every rendered embed against its source."""
    docs = (
        Path(docs)
        if docs is not None
        else current_project().path("book.source", "docs")
    )
    problems: list[str] = []

    # 1. every book .qmd is mark-free.
    for qmd in sorted(docs.rglob("*.qmd")):
        text = _mask_fences(qmd.read_text())
        for m in _ANNO.finditer(text):
            ln = text.count("\n", 0, m.start()) + 1
            problems.append(
                f"{_rel(qmd)}:{ln}: @{m.group(1).lower()} mark left in prose — "
                f"move it to {MARKS_DIR}/{qmd.stem}.md"
            )

    sidecars = sorted(docs.rglob(f"{MARKS_DIR}/*.md"))

    # 2. no orphan sidecars; 3. mirror integrity.
    for side in sidecars:
        chapter = side.parent.parent / (side.stem + ".qmd")
        if not chapter.exists():
            problems.append(
                f"{_rel(side)}: orphan sidecar — no chapter {_rel(chapter)} beside it"
            )
            continue
        ch_labels = {lbl for _, lbl in _headings(_mask_fences(chapter.read_text()))}
        for _, lbl in _headings(_mask_fences(side.read_text())):
            if lbl not in ch_labels:
                problems.append(
                    f"{_rel(side)}: heading '{lbl}' has no matching heading or "
                    f"{{#anchor}} in {_rel(chapter)} (the mirror has drifted)"
                )

    annos = scan([docs])

    # 4. the spec's binding namespace: anchored, unique level-2 sections.
    spec_rel = _rel(_configured_book_file(docs, "book.spec", SPEC_FILE))
    sections = spec_sections(docs)
    spec_seen: dict[str, str] = {}
    for slug, title, _ in sections:
        if not slug:
            problems.append(
                f"{spec_rel}: section '{title}' has no {{#slug}} "
                f"anchor (level-2 sections are the binding namespace)"
            )
        elif slug in spec_seen:
            problems.append(
                f"{spec_rel}: duplicate section anchor '#{slug}' "
                f"(also on '{spec_seen[slug]}')"
            )
        else:
            spec_seen[slug] = title
    spec = set(spec_seen)

    # 5. every @bind slug resolves to a spec section. Duplicates across
    # chapters are the point: any number of chapters may express one section.
    for a in annos:
        if a.kind != "bind":
            continue
        slug = a.args[0] if a.args else None
        # The sidecar and line the mark actually lives at, so a gate failure
        # under pre-commit pressure points at the editable file (a.file is the
        # chapter, whose line numbers do not apply).
        at = f"{a.source or a.file}:{a.line}"
        if len(a.args) != 1:
            problems.append(
                f"{at}: @bind takes exactly one slug (got {len(a.args)}). "
                f"Write one bodyless relationship per framework section."
            )
        if not slug:
            continue
        if a.body:
            problems.append(
                f"{at}: @bind('{slug}') has a body. Bindings are relationships, "
                f"not preservation contracts; route the content to the framework, "
                f"guidance, a todo, the prose, or git."
            )
        if slug in spec:
            continue
        problems.append(
            f"{at}: @bind('{slug}') binds no section of "
            f"{spec_rel}. Every @bind names a framework section: "
            f"name the section the chapter expresses, or add it."
        )

    # 6. @see resolves to a book-wide note.
    rn = _configured_book_file(docs, "book.review_notes", BOOK_NOTES_FILE)
    rn_slugs = (
        {a.args[0] for a in scan_text(rn.read_text(), BOOK_NOTES_FILE) if a.args}
        if rn.exists()
        else set()
    )
    for a in annos:
        if a.kind != "see":
            continue
        slug = a.args[0] if a.args else None
        at = f"{a.source or a.file}:{a.line}"
        if not slug:
            problems.append(f"{at}: @see with no slug")
        elif slug not in rn_slugs:
            problems.append(
                f"{at}: @see('{slug}') resolves to no book-wide "
                f"note in {BOOK_NOTES_FILE}"
            )

    # 9. `@ch` is a reference form, never a mark kind. A comment that opens
    # with it (`<!-- @ch(a-chapter) ... -->`) is harvested as a new kind by
    # _ANNO and would quietly join the vocabulary, which is how the tag set
    # grows by accident. The pointer belongs inside a mark's body.
    for a in annos:
        if a.kind == "ch":
            problems.append(
                f"{a.source or a.file}:{a.line}: @ch is a chapter *reference*, "
                f"not a mark kind — put the pointer in a mark's body"
            )

    # 10. `_quarto.yml` <-> disk, both directions. The dual of invariant 2, and
    # under slug-only the only remaining way to orphan a chapter: rename the
    # file and forget the config. Owned by chapters.py, which owns the registry.
    from quirework.book.chapters import bijection_problems

    problems += bijection_problems(docs)

    # 7/8. the companion collections resolve back into the spec, their
    # quotations still exist in the book they quote, and each entry's
    # **Anchors:** run names chapters that really bind those slugs. Computed
    # here, before the problems are printed; the quotation half and the
    # missing-run half are advisories and print below with the others.
    # The collections are siblings of docs/, so a caller pointing check() at a
    # temporary tree (the tests do) gets that tree's companions or none, never
    # the live repo's measured against a synthetic spec.
    comp_problems, comp_quotes, comp_anchors = companion_check(
        docs, spec, root=docs.parent
    )
    problems += comp_problems

    revived = [a for a in annos if a.kind in RETIRED_KINDS]
    for a in revived:
        argstr = f"({a.args[0]})" if a.args else ""
        problems.append(
            f"{a.source or a.file}:{a.line}: @{a.kind}{argstr} uses a retired "
            f"kind — {RETIRED_KINDS[a.kind]}"
        )
    for a in annos:
        if a.kind == "bind" or not a.body:
            continue
        for m in _BIND_REF.finditer(a.body):
            problems.append(
                f"{a.source or a.file}:{a.line}: @{a.kind} cites "
                f"@bind({m.group(1)}). Use '#{m.group(1)}' for the framework "
                f"section; @bind is reserved for bodyless relationships."
            )
        for m in _LEGACY_CLAIM_REF.finditer(a.body):
            problems.append(
                f"{a.source or a.file}:{a.line}: @{a.kind} cites retired "
                f"@claim({m.group(1)}). Use '#{m.group(1)}' for the framework "
                f"section."
            )

    for p in problems:
        print(p, file=sys.stderr)

    # Directories whose contents are working material rather than authority. A
    # mark that rests on one has put a commitment somewhere the book does not
    # carry. Which directories those are is the book's own placement, so an
    # undeclared project gets no advisory rather than a report about a tree it
    # does not have.
    noncanonical = tuple(current_project().get("book.noncanonical_roots", ()) or ())
    advisory = [a for a in annos if any(root in a.body for root in noncanonical)]
    if advisory:
        print(
            f"\nadvisory ({len(advisory)} mark(s) reference "
            f"{', '.join(noncanonical)}; a mark states its own commitment "
            f"— advisory, not yet gated):",
            file=sys.stderr,
        )
        for a in advisory:
            argstr = f"({a.args[0]})" if a.args else ""
            print(
                f"  {a.source or a.file}:{a.line}: @{a.kind}{argstr}", file=sys.stderr
            )

    register = [
        (a, m)
        for a in annos
        if a.kind in BLOCK_KINDS and a.body
        for m in [_CAPS_RUN.search(a.body)]
        if m
    ]
    if register:
        print(
            f"\nadvisory ({len(register)} block-surfaced mark(s) in AI register; "
            f"these are what the author reads in the .docx):",
            file=sys.stderr,
        )
        for a, m in register:
            argstr = f"({a.args[0]})" if a.args else ""
            print(
                f"  {a.source or a.file}:{a.line}: @{a.kind}{argstr} — '{m.group(0)}'",
                file=sys.stderr,
            )

    journ: list[str] = []
    for a in annos:
        if a.kind in CONTRACT_KINDS and a.body:
            m = _JOURNALING.search(a.body)
            if m:
                argstr = f"({a.args[0]})" if a.args else ""
                journ.append(
                    f"  {a.source or a.file}:{a.line}: "
                    f"@{a.kind}{argstr} — '{m.group(0)}'"
                )
    for slug, title, body in sections:
        m = _JOURNALING.search(body)
        if m:
            journ.append(f"  {spec_rel}#{slug or title} — '{m.group(0)}'")
    if journ:
        print(
            f"\nadvisory ({len(journ)} contract mark(s)/spec section(s) "
            f"carry decision-journaling; current state, never history — "
            f"the story belongs in git):",
            file=sys.stderr,
        )
        for line in journ:
            print(line, file=sys.stderr)

    # Every pin, not the first per mark: a mark carrying two of them is exactly
    # where reporting one is worst, because the author fixes what is printed
    # and the survivor reads as cleared. The live instance had two in one
    # sentence, and only the second was wrong.
    pins: list[str] = []
    for a in annos:
        if a.body:
            argstr = f"({a.args[0]})" if a.args else ""
            for m in _LINE_PIN.finditer(a.body):
                pins.append(
                    f"  {a.source or a.file}:{a.line}: "
                    f"@{a.kind}{argstr} — '{m.group(0)}'"
                )
    for slug, title, body in sections:
        for m in _LINE_PIN.finditer(body):
            pins.append(f"  {spec_rel}#{slug or title} — '{m.group(0)}'")
    if pins:
        print(
            f"\nadvisory ({len(pins)} mark(s)/spec section(s) pin a line "
            f"distance; the next insertion above makes the number wrong — "
            f"name the scene or the phrase instead):",
            file=sys.stderr,
        )
        for line in pins:
            print(line, file=sys.stderr)

    if comp_quotes:
        print(
            f"\nadvisory ({len(comp_quotes)} companion quotation(s) appear "
            f"nowhere in canon; a quote is primary evidence and is never "
            f"paraphrased into existence):",
            file=sys.stderr,
        )
        for line in comp_quotes:
            print(line, file=sys.stderr)

    if comp_anchors:
        print(
            f"\nadvisory ({len(comp_anchors)} companion entry/entries anchor "
            f"nothing; an entry with no resolving **Anchors:** run is invisible "
            f"to the join that keeps the collection in step):",
            file=sys.stderr,
        )
        for line in comp_anchors:
            print(line, file=sys.stderr)

    dangling = [
        (a, slug)
        for a in annos
        if a.body
        for slug in _SPEC_REF.findall(a.body)
        if slug not in spec
    ]
    if dangling:
        print(
            f"\nadvisory ({len(dangling)} inline framework #slug reference(s) "
            f"resolve to no spec section):",
            file=sys.stderr,
        )
        for a, slug in dangling:
            argstr = f"({a.args[0]})" if a.args else ""
            print(
                f"  {a.source or a.file}:{a.line}: @{a.kind}{argstr} — #{slug}",
                file=sys.stderr,
            )

    # The spec is prose and is held to both tells. Bindings are bodyless
    # relationships, so only the spec can carry an imperative here.
    spec_register: list[str] = []
    for slug, title, body in sections:
        m = _IMPERATIVE.search(body) or _CAPS_RUN.search(body)
        if m:
            spec_register.append(f"  {spec_rel}#{slug or title} — '{m.group(0)}'")
    if spec_register:
        print(
            f"\nadvisory ({len(spec_register)} spec section(s) in "
            f"the imperative or contract register; the spec states and "
            f"prices, never orders):",
            file=sys.stderr,
        )
        for line in spec_register:
            print(line, file=sys.stderr)

    if problems:
        print(
            f"\n✗ notes --check: {len(problems)} problem(s); the sidecar "
            f"layout is not clean.",
            file=sys.stderr,
        )
        return 1
    print("✓ notes --check: sidecar layout clean.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="quirework book notes",
        description="Harvest the book's @-annotations from the source. "
        "Positional args filter by kind (bind, guidance, todo, ...); "
        "run `quirework book notes --list` to see the kinds and their counts.",
        epilog="examples:\n"
        "  quirework book notes                     all marks, grouped by kind\n"
        "  quirework book notes bind                only @bind relationships\n"
        "  quirework book notes --primer            the fresh-session primer\n"
        "                                       (bind+guidance, by chapter)\n"
        "  quirework book notes bind --slug two-faces\n"
        "                                       who binds that spec section\n"
        "  quirework book notes --by chapter        the whole book's marks, in order\n"
        "  quirework book notes --list              the kind vocabulary + counts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "kinds",
        nargs="*",
        metavar="KIND",
        help="kind(s) to include, unioned (default: all)",
    )
    ap.add_argument(
        "--kind",
        action="append",
        default=[],
        metavar="KIND",
        help="another kind to include (repeatable; comma-separated ok)",
    )
    ap.add_argument(
        "--by",
        choices=("kind", "chapter"),
        default=None,
        help="group output by kind (default) or by chapter, in reading order",
    )
    ap.add_argument(
        "-c",
        "--chapter",
        metavar="SUBSTR",
        help="only marks in files whose path matches this substring "
        "(e.g. the-sidecar, sidecar)",
    )
    ap.add_argument(
        "--section",
        metavar="SUBSTR",
        help="only marks under a heading matching this substring",
    )
    ap.add_argument(
        "--slug",
        metavar="SUBSTR",
        help="only marks whose parenthetical slug matches this substring "
        "(`notes bind --slug X` = which chapters bind spec section X)",
    )
    ap.add_argument(
        "--primer",
        action="store_true",
        help="shortcut: @bind + @guidance, by chapter (fresh-session context)",
    )
    ap.add_argument(
        "--list",
        dest="list_kinds",
        action="store_true",
        help="list the kinds present, with counts, and exit",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="lint the sidecar metadata layout and exit (for the pre-commit hook)",
    )
    ap.add_argument(
        "--coverage",
        action="store_true",
        help="which spec sections a companion entry exercises, and which none does",
    )
    ap.add_argument(
        "--path",
        action="append",
        default=[],
        metavar="PATH",
        help="scan this file/dir instead of docs/ (repeatable)",
    )
    args = ap.parse_args(argv)

    if args.check:
        # --check is the layout gate, not a view: the filters below do not
        # apply to it, and silently ignoring one would return a verdict about
        # a different question than the one asked. A single --path forwards
        # as the docs root to lint (what the tests do via check(docs=...)).
        ignored = [
            name
            for name, val in (
                ("KIND", args.kinds or args.kind),
                ("--by", args.by),
                ("-c/--chapter", args.chapter),
                ("--section", args.section),
                ("--slug", args.slug),
                ("--primer", args.primer),
                ("--list", args.list_kinds),
            )
            if val
        ]
        if ignored:
            print(
                f"--check runs the layout gate and takes no filters "
                f"(got: {', '.join(ignored)}).",
                file=sys.stderr,
            )
            return 2
        if len(args.path) > 1:
            print(
                "--check takes at most one --path (the docs root to lint).",
                file=sys.stderr,
            )
            return 2
        return check(Path(args.path[0])) if args.path else check()

    if args.coverage:
        by_slug, unexercised = coverage()
        total = len(spec_slugs())
        print(
            f"companion coverage: {len(by_slug)}/{total} spec section(s) "
            f"exercised by an entry\n"
        )
        for slug in sorted(by_slug):
            entries = sorted(set(by_slug[slug]))
            print(f"  {slug}")
            print(f"      {', '.join(entries)}")
        if unexercised:
            print(f"\n  not exercised by any entry ({len(unexercised)}):")
            for anchor, title in unexercised:
                print(f"      {anchor:<34} {title}")
            print(
                "\n  A map, not a scorecard. The vocabulary-discipline, "
                "positioning and\n  spec-organizing sections belong to no "
                'chapter and to no exercise\n  either; see AGENTS.md, "The '
                'reverse direction is not an invariant".'
            )
        return 0

    paths = [Path(p) for p in args.path] or None
    annos = scan(paths)
    present = Counter(a.kind for a in annos)

    if args.list_kinds:
        total = sum(present.values())
        print(f"kinds present ({total} mark(s)):")
        width = max((len(k) for k in present), default=0)
        for kind, n in sorted(present.items(), key=lambda kv: (-kv[1], kv[0])):
            gloss = KIND_DESCRIPTIONS.get(kind, "")
            print(f"  {kind:<{width}}  {n:>3}  {gloss}".rstrip())
        return 0

    # Explicitly requested kinds: positional + --kind (comma-splittable), unioned.
    explicit: list[str] = [k.lower() for k in args.kinds]
    for spec in args.kind:
        explicit += [p.strip().lower() for p in spec.split(",") if p.strip()]
    explicit = list(dict.fromkeys(explicit))  # de-dup, preserve order

    by = args.by or ("chapter" if args.primer else "kind")

    # An explicitly named kind the corpus doesn't hold is a typo -- error loudly
    # (the tag set is open, so "valid" == "present"). A preset (--primer) is not a
    # typo, so its absent members are dropped silently below, not validated here.
    unknown = [k for k in explicit if k not in present]
    if unknown:
        avail = ", ".join(
            f"{k}({present[k]})"
            for k, _ in sorted(present.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        for k in unknown:
            if k in RETIRED_KINDS:
                print(f"retired kind '{k}': {RETIRED_KINDS[k]}", file=sys.stderr)
                continue
            sugg = difflib.get_close_matches(k, list(present), n=1)
            hint = f" did you mean '{sugg[0]}'?" if sugg else ""
            if "/" in k or Path(k).exists():
                hint += " (a path is scanned with --path PATH)"
            print(f"unknown kind '{k}'.{hint}", file=sys.stderr)
        print(f"available kinds: {avail}", file=sys.stderr)
        return 2

    if explicit:
        requested = explicit
    elif args.primer:
        requested = [k for k in PRIMER_KINDS if k in present]
    else:
        requested = []  # no kind filter: every kind

    # Validate the chapter filter against the scanned files, likewise.
    all_files = sorted({a.file for a in annos})
    if args.chapter:
        sub = args.chapter.lower()
        if not any(sub in f.lower() for f in all_files):
            print(f"no chapter matches '{args.chapter}'. chapters:", file=sys.stderr)
            for f in all_files:
                print(f"  {f}", file=sys.stderr)
            return 2

    if requested:
        want = set(requested)
        annos = [a for a in annos if a.kind in want]
    if args.chapter:
        sub = args.chapter.lower()
        annos = [a for a in annos if sub in a.file.lower()]
    # --slug and --section are validated against what the earlier filters
    # left, so the error is scoped to the kinds/chapter actually asked for
    # (a typo errors with rc 2 like -c; an empty *combination* stays rc 0).
    if args.slug:
        sub = args.slug.lower()
        matching = [a for a in annos if any(sub in arg.lower() for arg in a.args)]
        if not matching and annos:
            slugs = sorted({arg for a in annos for arg in a.args})
            sugg = difflib.get_close_matches(args.slug, slugs, n=3)
            hint = f" close: {', '.join(sugg)}." if sugg else ""
            print(f"no mark slug matches '{args.slug}'.{hint}", file=sys.stderr)
            return 2
        annos = matching
    if args.section:
        sub = args.section.lower()
        matching = [a for a in annos if a.section and sub in a.section.lower()]
        if not matching and annos:
            secs = sorted({a.section for a in annos if a.section})
            sugg = difflib.get_close_matches(args.section, secs, n=3)
            hint = f" close: {', '.join(sugg)}." if sugg else ""
            print(f"no section matches '{args.section}'.{hint}", file=sys.stderr)
            return 2
        annos = matching

    if not annos:
        # Every filter here was already validated as matching *something*, so an
        # empty result means the combination is empty -- say which combination.
        active = []
        if requested:
            active.append("kind(s) " + ", ".join(requested))
        if args.chapter:
            active.append(f"chapter ~ '{args.chapter}'")
        if args.slug:
            active.append(f"slug ~ '{args.slug}'")
        if args.section:
            active.append(f"section ~ '{args.section}'")
        where = "; ".join(active) if active else "the scanned sources"
        print(f"no marks match ({where}).")
        return 0

    if args.primer and not args.path:
        _print_spec_toc()  # a custom --path scan has no spec of its own
    if by == "chapter":
        _render_by_chapter(annos)
    else:
        _render_by_kind(annos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
