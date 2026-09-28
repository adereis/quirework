"""Extract in-source annotations from the document source.

The volatile half of the old map (to-dos, guidance, genesis, conversation
references, spec bindings) lives as structured HTML comments in a per-unit
sidecar: beside a book's chapter, or wherever a site keeps its pages' marks
(`units` owns the layout). They never reach the rendered text and survive a
semantic rewrap, but a tool can harvest them into the grouped views the map
used to hold -- always in sync, because they are read from the text itself.

Convention (one tag per comment; the parenthetical and body are optional):

    <!-- @guidance  how this chapter should read (voice, length, depth, goals) -->
    <!-- @genesis   where it came from: why this subject, the inspiration behind it -->
    <!-- @bind(kettle-signal) -->
    <!-- @todo      make the kettle example concrete -->
    <!-- @variant(audio)  describe the signal for the audio edition -->
    <!-- @ref       source:field-notes -- the interview that seeded this -->
    <!-- @see(em-dashes)  the long dialogue here is the worst offender -->

Comments may span multiple lines; the body is whitespace-collapsed. Any tag name
is accepted. The list above is convention, not a closed set.
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from quirework.core import frontmatter
from quirework.manuscript import formats, units
from quirework.project import ConfigurationError, current_project

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
# A `/` is admitted so a trail heading can carry a unit's section by its full
# name (`{#the-kettle/thesis}`); a spec anchor that contains one is refused,
# since `/` is what separates the unit from its section.
_ATTR_ID = re.compile(r"#([\w:./-]+)")
_WS = re.compile(r"\s+")
# Heading detection must not read non-prose regions: a `# ...` line inside a
# multi-line HTML comment (a sidecar banner) or a YAML comment inside
# frontmatter is not a heading, but the line-anchored regex cannot tell.
# Masking preserves length and newlines, so offsets stay comparable.
_COMMENT = re.compile(r"(?s)<!--.*?-->")
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
# "ratified" (a word the prose itself uses); "caught" (chapter vocabulary
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
# phrase instead ("at the train-platform passage"), which survives every edit
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
# edge. And the colon address -- `preface:3`, `@unit(the-sidecar):69`,
# `the-sidecar.qmd:71` -- carries no "line" keyword at all, so nothing in
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
    | (?: @unit\([a-z0-9-]+\)                         # the colon-address forms:
        | \b[a-z][a-z0-9-]*\.(?:qmd|md|svg)         #   the-sidecar.qmd:71
        | \b[a-z]+(?:-[a-z0-9]+)+                   #   @unit(the-sidecar):69
        | \bpreface )                               #   preface:3
      \s*:\s*\d+(?:\s*[-–]\s*\d+)?\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# The structural `@bind(slug)` token, used by bodyless sidecar relationships and
# by companion `Anchors:` joins. Semantic prose cites the spec as `#slug`.
_BIND_REF = re.compile(r"@bind\(([\w-]+(?:/[\w-]+)?)\)")
_BIND_TOKEN = re.compile(r"@bind\(([^)]*)\)")
# A semantic citation of the spec section itself. Exclude Pandoc
# ``{#anchor}`` attributes; those name document locations, not commitments.
_SPEC_REF = re.compile(r"(?<![\w{/])#([A-Za-z][\w-]*(?:/[A-Za-z][\w-]*)?)\b")

# `@unit(slug)`: the metadata layer's pointer to a chapter or a post,
# resolved by units.py. A reference form only, never a mark kind -- `<!--
# @unit(x) -->` would be harvested as a new kind and would join the vocabulary
# by accident, so `check()` rejects it as invariant 9.
_UNIT_REF = units.UNIT_REF


# Tokens in an **Anchors:** run that are unmistakably unit pointers use
# `@unit(slug)` or the unit's file, as the format writes it
# (`the-sidecar.qmd`, `posts/the-kettle/index.md`). Other tokens are prose.
def _bare(tok: str) -> str:
    """A run's token reduced to the slug it names: its file's slug when it is
    written as a prose file, else the token itself."""
    return formats.current().prose_token(tok) or tok


def _unit_shaped(tok: str) -> bool:
    return formats.current().prose_token(tok) is not None


# The imperative register the spec must not use: a commitment is stated
# ("the archive records source hashes"), never ordered ("NEVER omit hashes") —
# a well-stated commitment makes its own instruction derivable. All-caps forms
# only: indicative prose legitimately says "never".
_IMPERATIVE = re.compile(r"\b(NEVER|DO NOT|DON'T|MUST NOT|MAY NOT|BANNED)\b")


# Companion documents are a declared exception to canon self-containment. They
# can cite the spec by slug and quote the canon by passage, so the check follows
# both directions without assuming their directory names.
def companion_dirs() -> tuple[str, ...]:
    """The reader-facing companion collections this project declares, if any.

    `manuscript.companion_dirs` names them. The default is none: a work with no
    companion collection has nothing here to check, and naming another
    project's directories would check a tree this one does not have.
    """
    return tuple(current_project().get("manuscript.companion_dirs", ()) or ())


def _is_entry(path: Path) -> bool:
    """Whether a file in a companion collection is one of its entries.

    A README indexes the collection, and a `_`-prefixed file is its apparatus
    (a template carrying placeholder citations, a partial), so neither is
    held to the gate about entries. It is the same rule a site's pages
    follow."""
    return path.name != "README.md" and not path.name.startswith("_")


# A quotation attributed to the book. Both forms are used by the entries and
# both have failed: a double-quoted span lifted from a chapter, and a bolded
# span attributed to the spec. The length floor keeps single-word emphasis
# (**resolved**) out;
# the ceiling keeps a runaway span from swallowing a paragraph.
# Whitespace-only differences are never misquotes and _norm already collapses
# them: the book wraps one sentence per line and the entries wrap at 72
# columns, so a quotation spanning a sentence boundary is wrapped differently
# on each side.
# An entry's header paragraph carries a bold-labelled run naming the units
# and commitments its position rests on, "**Anchors:**" unless the project
# names its own label (`manuscript.companion_anchors`). It is READER-FACING
# prose and stays that way: minting a parallel @-mark would put the same fact in
# two places, and a duplicated fact is the drift this whole layer exists to
# prevent. So the visible run is the single owner and the check reads it where
# it stands.
def _anchors_label() -> str:
    return str(current_project().get("manuscript.companion_anchors", "Anchors"))


def _anchors_run(text: str) -> re.Match | None:
    label = re.escape(_anchors_label())
    return re.search(rf"\*\*{label}:\*\*(.*?)(?:\n[ \t]*\n|\Z)", text, re.S)


_BACKTICKED = re.compile(r"`([^`]+)`")


def _code_spec_refs(text: str) -> list[tuple[str, int]]:
    """Spec ``#slug`` citations inside Markdown code spans.

    Companion prose uses code spans to distinguish spec slugs from local
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
    """Blank HTML comments and frontmatter, for heading detection only.

    Frontmatter is either convention, YAML or TOML: a TOML comment line starts
    with `#` exactly like a heading. Length-preserving like _mask_fences, so
    heading offsets computed on the masked text index straight into the
    unmasked one."""
    found = frontmatter.block(text)
    if found:
        text = _blank(found) + text[found.end() :]
    return _COMMENT.sub(_blank, text)


DEFAULT_GLOBS = ("*.qmd", "*.md")

# Developer-guide files carry annotation *examples* in prose (e.g. the literal
# `<!-- @see(slug) rider -->` in AGENTS.md), not book content. Skip them when
# globbing a directory, so the harvest is the book's marks, not the repo's own
# guidance. An explicitly named --path still reads them (nothing is masked).
SKIP_NAMES = units.SKIP_NAMES

# A unit's metadata marks live in a sidecar, never inline in the prose. scan()
# attributes a sidecar's marks to the unit it belongs to (`units.attributed`);
# `notes --check` verifies the layout stays honest. A configured book-wide
# review manifest is the target a unit's @see(slug) resolves against.
MARKS_DIR = units.MARKS_DIR

# The spec is the central commitments register. Its
# level-2 sections (`## Title {#slug}`) are the binding namespace — a chapter's
# @bind(slug) resolves against these anchors, and any number of chapters may
# bind one section. A slug the spec does not declare is a hard error: there is
# no independent claim layer.

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

# One-line gloss per kind, for `notes --list`. The tag set is open and `--list`
# only shows kinds actually present. A new kind lists without a gloss until one
# is added here.
KIND_DESCRIPTIONS = {
    "bind": "unit-to-spec bindings",
    "guidance": "how a unit should read (the author's intent)",
    "genesis": "where a unit came from (origin, not direction)",
    "todo": "open working state / the agent's worklist",
    "variant": "what a derived edition should do differently here",
    "figure": "built illustrations",
    "ref": "prior-art / conversation pointers",
    "source": "per-claim citation",
    "see": "pointer to a work-wide review note",
}

# Other names a kind answers to on the command line. A harness that guards a
# worktree by refusing any command containing "bind" made `spec notes bind`
# unrunnable, and `--kind bind` did not help because the word was still in
# argv. The alias spells the same filter without it.
KIND_ALIASES = {"relations": "bind"}

# The `--primer` preset: what a fresh session must act from or stay consistent
# with -- the bindings and the constraints on how the book reads
# (@guidance). The spec is printed first and owns what is true.
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
    """The prose file a source path is attributed to: a sidecar's unit, and
    every other file itself. The harvest reads the sidecar's text (so its
    mirrored headings drive section attribution) but stamps every mark with the
    unit's path, so `--by unit`, reading order, and the title all resolve
    exactly as they would if the marks were inline. `units` owns the mapping."""
    return units.attributed(f)


def _configured_book_file(docs, key):
    project = current_project()
    if project.get(key) is None:
        return None
    configured = project.path(key)
    source = project.path("manuscript.source")
    if configured.is_relative_to(source):
        return Path(docs) / configured.relative_to(source)
    return configured


class Section(NamedTuple):
    """One commitment: a level-2 section of a spec file.

    A section of the shared spec is named by its anchor. A section of a unit's
    own spec is named `unit/anchor`, so two posts can each have a `#thesis`
    and a binding still names exactly one of them.
    """

    slug: str | None  # the full name; None is a hard `check()` problem
    title: str
    body: str
    path: Path  # the spec file that holds it
    anchor: str | None = None  # the {#anchor} as written in that file


def anchor_of(slug: str) -> str:
    """The anchor a section's full name carries in its own file."""
    return slug.split("/", 1)[1] if "/" in slug else slug


def resolve(slug: str, unit: str | None, known: set[str]) -> str:
    """The section a name means where it is written.

    In a unit's own marks, a bare name finds that unit's own section first,
    so a post's sidecar can bind `thesis` rather than spell out its own slug.
    Everywhere else a name means what it says, and a unit's section is named
    in full."""
    if unit and "/" not in slug and f"{unit}/{slug}" in known:
        return f"{unit}/{slug}"
    return slug


def _unit_spec_pattern() -> str | None:
    """`manuscript.unit_spec`, a path pattern with {slug}, or None."""
    return formats.slug_pattern("manuscript.unit_spec")


def unit_spec_path(slug: str) -> Path | None:
    """Where the unit ``slug`` keeps its own spec, or would; None if units keep none."""
    pattern = _unit_spec_pattern()
    if pattern is None:
        return None
    return current_project().root / pattern.replace("{slug}", slug)


def spec_owner(path: Path) -> str | None:
    """The unit whose own spec ``path`` is, or None for the shared spec."""
    pattern = _unit_spec_pattern()
    if pattern is None:
        return None
    root = current_project().root.resolve()
    try:
        rel = Path(path).resolve().relative_to(root).as_posix()
    except ValueError:
        return None
    m = formats.pattern_regex(pattern).match(rel)
    return m.group("slug") if m else None


def spec_files(docs=None) -> list[Path]:
    """Every file of the register, in the order its sections are read.

    A project states its commitments in one shared spec (`manuscript.spec`),
    in one spec per unit (`manuscript.unit_spec`), or both. The shared file
    comes first, because it holds what several units lean on; then each
    unit's own, in reading order; then the specs of units that do not exist
    yet, by slug, since a post is often specified before it is drafted.
    """
    docs = (
        Path(docs) if docs is not None else current_project().path("manuscript.source")
    )
    shared = _configured_book_file(docs, "manuscript.spec")
    pattern = _unit_spec_pattern()
    if shared is None and pattern is None:
        raise ConfigurationError(
            "configure manuscript.spec or manuscript.unit_spec in quirework.toml"
        )
    files = [shared] if shared is not None and shared.exists() else []
    if pattern is not None:
        root = current_project().root
        found = {}
        for path in root.glob(pattern.replace("{slug}", "*")):
            owner = spec_owner(path)
            if path.is_file() and owner is not None:
                found[owner] = path
        try:
            order = [u.slug for u in units.registry(docs)]
        except (OSError, ValueError):
            order = []
        files += [found.pop(slug) for slug in order if slug in found]
        files += [found[slug] for slug in sorted(found)]
    return files


def _spec_name(docs=None) -> str:
    """How messages name the register: its file when there is one, else "the spec"."""
    files = spec_files(docs)
    return files[0].name if len(files) == 1 else "the spec"


def _spec_names(docs=None) -> tuple[str, ...]:
    """Every register file's name, which is how an entry cites the spec."""
    return tuple(f.name for f in spec_files(docs))


def _sections_of(spec: Path) -> list[Section]:
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
    owner = spec_owner(spec)
    out = []
    for i, (_, end, title, anchor) in enumerate(lvl2):
        stop = lvl2[i + 1][0] if i + 1 < len(lvl2) else len(text)
        slug = f"{owner}/{anchor}" if owner and anchor else anchor
        out.append(Section(slug, title, text[end:stop].strip(), spec, anchor))
    return out


def spec_sections(docs: Path | None = None) -> list[Section]:
    """Every level-2 section of the register, file by file in `spec_files`
    order. A section's slug is None where it is missing its {#slug} (a hard
    `check()` problem). Empty while no spec file exists."""
    return [s for spec in spec_files(docs) for s in _sections_of(spec)]


def spec_slugs(docs: Path | None = None) -> set[str]:
    """The binding namespace: every anchored level-2 section of the register."""
    return {s.slug for s in spec_sections(docs) if s.slug}


def spec_file_of(slug: str, docs: Path | None = None) -> Path | None:
    """The spec file whose section is anchored ``slug``."""
    for s in spec_sections(docs):
        if s.slug == slug:
            return s.path
    return None


def scan(paths: list[Path] | None = None) -> list[Annotation]:
    """Harvest annotations below the given paths or the manuscript's own files."""
    roots = paths or units.harvest()
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


def _reading_order() -> dict[str, int]:
    """Map each unit's relative path to its position in the manuscript.

    Lets `--by unit` follow the reading order (a book's welcome landing
    first, part dividers in place; a site's pages by date) instead of a lexical
    filename sort. Empty when the order cannot be read (e.g. a custom --path
    scan of a tree with no Quarto file), in which case the caller falls back to
    sorting by path.
    """
    try:
        return {u.rel: i for i, u in enumerate(units.registry())}
    except (OSError, ValueError):
        return {}


def _chapter_title(rel: str) -> str:
    """A human label for a source file: a frontmatter title, else its first
    heading, else the stem."""
    try:
        text = _mask_fences((current_project().root / rel).read_text())
    except OSError:
        return Path(rel).stem
    try:
        title = frontmatter.as_text(frontmatter.fields(text).get("title"))
    except ValueError:
        title = None
    if title:
        return title
    m = _HEADING.search(_mask_nonprose(text))
    if m:
        return m.group(2).strip()
    return Path(rel).stem


def _print_spec_toc(docs: Path | None = None, only: set[str] | None = None) -> None:
    """The register's table of contents, printed at the top of the primer:
    the commitments are the work's skeleton, so a fresh session reads them
    before the per-unit marks. Silent while the register has no sections.

    With ``only``, a set of unit slugs, the listing is the part of the
    register those units touch: their own spec files whole, and the shared
    sections they bind. A site with a spec per post would otherwise hand a
    session every post's commitments to change one.
    """
    sections = spec_sections(docs)
    if only is not None:
        bound = set().union(
            *(v for k, v in chapter_bindings(docs).items() if k in only)
        )
        sections = [
            s
            for s in sections
            if spec_owner(s.path) in only
            or (spec_owner(s.path) is None and s.slug in bound)
        ]
    if not sections:
        return
    # Where a trail is kept, each section says how many breadcrumbs lead back
    # to the conversations that shaped it. Counting reads only the sidecar, so
    # the primer stays as cheap as it was; following them is `spec trail`.
    from quirework.manuscript import trail

    held = trail.counts()
    by_file: dict[Path, list[Section]] = {}
    for s in sections:
        by_file.setdefault(s.path, []).append(s)
    width = max((len(s.slug or "") + 3 for s in sections), default=0)
    width = max(width, 26)
    for path, group in by_file.items():
        owner = spec_owner(path)
        whose = f"{units.noun()} {owner}" if owner else "shared"
        print(
            f"spec — {_rel(path)} ({whose}; {len(group)} section(s); "
            f"read it before the marks):"
        )
        for s in group:
            anchor = f"#{s.slug}" if s.slug else "(no anchor)"
            mark = f"  [trail ×{held[s.slug]}]" if held.get(s.slug) else ""
            print(f"  {anchor:<{width}} {s.title}{mark}")
    if trail.configured():
        with_trail = sum(1 for s in sections if held.get(s.slug))
        print(
            f"  {with_trail} of {len(sections)} section(s) have a trail of the "
            f"sessions that shaped them: quirework spec trail SLUG"
        )


def _trail_marker(a: Annotation, held: dict[str, int], known: set[str]) -> str:
    """``  [trail ×N]`` after a binding whose section has breadcrumbs.

    A binding is how a reader gets from prose to a commitment, so it is where
    they should learn that the commitment's history can be read. A section
    with no breadcrumbs carries no marker: the hint appears where there is
    something to follow, and a trail still being filled never teaches a
    reader to skip it.
    """
    if a.kind != "bind" or not a.args:
        return ""
    slug = resolve(a.args[0], units.slug_of(a.file), known)
    return f"  [trail ×{held[slug]}]" if held.get(slug) else ""


def _held():
    """Breadcrumb counts per section, and the names a binding resolves against.
    Both empty where no trail is kept, which is also where no spec need be."""
    from quirework.manuscript import trail

    held = trail.counts()
    return held, (spec_slugs() if held else set())


def _render_by_kind(annos: list[Annotation]) -> None:
    held, known = _held()
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
            payload = f"{argstr}{a.body}".rstrip() + _trail_marker(a, held, known)
            print(f"  {a.source or a.file}:{a.line}{sec}\n    {payload}")
    print(f"\ntotal: {len(annos)} mark(s) across {len(by_kind)} kind(s)")


def _render_by_chapter(annos: list[Annotation]) -> None:
    held, known = _held()
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
            suffix += _trail_marker(a, held, known)
            print(f"  L{a.line:<4} @{a.kind}{argstr}{suffix}")
    print(f"\ntotal: {len(annos)} mark(s) across {len(by_file)} {units.noun()}(s)")


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(current_project().root))
    except ValueError:
        return str(p)


def _norm(text: str) -> str:
    """Collapse whitespace, so a quotation matches across two wrap styles."""
    return _WS.sub(" ", text).strip()


def _searchable(quote: str) -> str:
    """A quotation reduced to what must actually exist in the work.

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
            out += [p for p in sorted((base / d).glob("*.md")) if _is_entry(p)]
    return out


def _canon_corpus(docs: Path) -> str:
    """Everything an entry may legitimately be quoting: the prose, the marks,
    the spec, the curated index and the book-wide notes, whitespace-collapsed
    into one searchable string. Built from raw file text rather than parsed
    bodies so a quotation that straddles a comment or a heading still matches;
    the collapse is what makes a sidecar's hanging-indented mark body
    searchable as the single sentence it is."""
    files: set[Path] = set()
    for pat in ("*.qmd", "*.md"):
        files.update(docs.rglob(pat))
    # A site keeps its marks and its spec outside the source it renders.
    files.update(units.sidecars(docs))
    files.update(spec_files(docs))
    return _searchable("\n".join(f.read_text() for f in sorted(files)))


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
    block: str, stems: set[str], spec_names: tuple[str, ...]
) -> list[tuple[str, int]]:
    """Quotations in one block that are attributed to the work, with offsets.

    Two filters do the discriminating, and both were forced by real false
    positives on the live collections. A bold span opening a block or a list
    item is one of the entries' own subheadings ("**It costs the reader
    first.**"), never a quotation. And attribution is read from the 200
    characters *before* the span rather than from the block as a whole: an
    entry quotes an outside source, an objector's voice, and its own wording in
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
        # `@unit()` is read alongside the bare stems: attribution is what decides
        # whether a span is measured against the book at all, so a form this
        # predicate does not know makes the advisory quietly NARROWER -- fewer
        # quotations checked, and no message saying so. That is the same silent
        # degradation anchor_pairs had, in a second place.
        if not (
            _BIND_REF.search(window)
            or _code_spec_refs(window)
            or _UNIT_REF.search(window)
            or any(name in window for name in spec_names)
            or any(s in window for s in stems)
        ):
            continue
        out.append((span, start))
    return out


def chapter_bindings(docs: Path | None = None) -> dict[str, set[str]]:
    """unit slug -> the spec slugs it expresses.

    A unit binds what its sidecar's `@bind` marks name, and, where units keep
    their own spec files, every section of its own: a post expresses its own
    commitments by being the post they describe. An explicit `@bind` of an
    own section is still how a sidecar says which heading carries it.
    """
    out: dict[str, set[str]] = {}
    roots = units.harvest(docs) if docs is not None else None
    own = _unit_spec_pattern() is not None
    sections = spec_sections(docs) if own else []
    known = {x.slug for x in sections if x.slug}
    for a in scan(roots):
        if a.kind == "bind" and a.args:
            unit = units.slug_of(a.file)
            out.setdefault(unit, set()).add(resolve(a.args[0], unit, known))
    if own:
        present = units.names(docs)
        for s in sections:
            owner = spec_owner(s.path)
            if owner in present and s.slug:
                out.setdefault(owner, set()).add(s.slug)
    return out


def anchor_pairs(
    text: str, stems: set[str], spec: set[str]
) -> tuple[list[tuple[str | None, str]], list[str]]:
    """(pairs, unresolved) for the anchors in an entry's anchors run.

    The run is prose, so it is read positionally: a chapter reference sets the
    chapter that the `@bind(slug)` tokens after it belong to, until the next
    one. A bare `#slug` names a spec
    section directly, with no chapter claimed, so it yields None.

    A token is a chapter reference, a spec slug, or inert prose. An explicit
    chapter pointer that resolves to nothing is returned in `unresolved`.
    """
    m = _anchors_run(text)
    if not m:
        return [], []
    run = _WS.sub(" ", m.group(1))
    pairs: list[tuple[str | None, str]] = []
    unresolved: list[str] = []
    current: str | None = None
    for t in _BACKTICKED.finditer(run):
        tok = t.group(1).strip()
        binding = _BIND_REF.search(tok)
        ref = _UNIT_REF.search(tok)
        if binding:
            pairs.append((current, binding.group(1)))
        elif ref:
            if ref.group(1) in stems:
                current = ref.group(1)
            else:
                current = None
                unresolved.append(tok)
        elif tok.startswith("#") and tok[1:] in spec:
            pairs.append((None, tok[1:]))
        elif _bare(tok) in stems:
            # A run may name a file with its extension. The stem is
            # what the binding map is keyed by.
            current = _bare(tok)
        elif _unit_shaped(tok):
            current = None
            unresolved.append(tok)
    return pairs, unresolved


def coverage(
    docs: Path | None = None, root: Path | None = None
) -> tuple[dict[str, list[str]], list[tuple[str, str]]]:
    """Which commitments the companion collections actually exercise.

    Returns (slug -> the entries anchored to it, unexercised (anchor, title)
    pairs in spec order). The marks say which units *express* a commitment,
    and this says which ones have been *worked* in a companion entry.

    It is a map and never a scorecard. A section with no entry is not a gap by
    itself: a section about the spec's own organization or vocabulary belongs
    to no unit and to no entry either.
    """
    docs = (
        Path(docs) if docs is not None else current_project().path("manuscript.source")
    )
    spec = spec_slugs(docs)
    stems = units.names(docs)
    by_slug: dict[str, list[str]] = {}
    for f in companion_files(root if root is not None else docs.parent):
        pairs, _ = anchor_pairs(_mask_fences(f.read_text()), stems, spec)
        for _, slug in pairs:
            if slug in spec:
                by_slug.setdefault(slug, []).append(f.stem)
    unexercised = [
        (s.slug, s.title)
        for s in spec_sections(docs)
        if s.slug and s.slug not in by_slug
    ]
    return by_slug, unexercised


def companion_check(
    docs: Path, spec: set[str], root: Path | None = None
) -> tuple[list[str], list[str], list[str]]:
    """Resolve the companion collections against the work, in both directions.

    Returns (problems, advisories). A `@bind(slug)` citation that names no
    spec section is a hard problem: it is a broken pointer with one right
    answer, the same defect invariant 5 gates inside the book, and the entries
    are cited from canon marks in turn.

    A quotation that appears nowhere in canon is an advisory, because the
    detection is necessarily a heuristic. An entry may quote outside sources
    as well as the book, so only a paragraph that also cites canon is
    considered. What it catches is the failure that has actually happened:
    prose or a mark body edited out from under a live quotation."""
    problems: list[str] = []
    advisories: list[str] = []
    anchors: list[str] = []
    corpus = _canon_corpus(docs)
    stems = units.names(docs)
    binds = chapter_bindings(docs)
    label = f"**{_anchors_label()}:**"
    for f in companion_files(root):
        text = _mask_fences(f.read_text())
        rel = _rel(f)
        anchor_run = _anchors_run(text)
        for m in _BIND_TOKEN.finditer(text):
            if not re.fullmatch(r"\s*[\w-]+\s*", m.group(1)):
                ln = text.count("\n", 0, m.start()) + 1
                problems.append(
                    f"{rel}:{ln}: @bind takes exactly one slug. Write one "
                    f"relationship token per spec section."
                )
        pairs, unresolved = anchor_pairs(text, stems, spec)
        for tok in unresolved:
            problems.append(
                f"{rel}: {label} names `{tok}`, which is no {units.noun()}. "
                f"The anchors after it are attributed to nothing, so the join "
                f"that holds this entry to the work is silently broken."
            )
        if not pairs:
            # An entry with no resolving anchors has nothing holding it to
            # the work.
            anchors.append(
                f"  {rel}: no {label} run resolves, so nothing holds this "
                f"entry to a commitment when the work moves"
            )
        for chapter, slug in pairs:
            if chapter is None or slug not in spec:
                continue  # the slug half is invariant 7's to report
            if slug not in binds.get(chapter, set()):
                holder = sorted(k for k, v in binds.items() if slug in v)
                problems.append(
                    f"{rel}: anchored to `{chapter}` `@bind({slug})`, which "
                    f"{chapter} does not bind. Bound by: "
                    f"{', '.join(holder) or f'no {units.noun()}'}. Repoint the "
                    f"anchor at the {units.noun()} that expresses it."
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
                        f"{label} run. Use '#{m.group(1)}' in semantic "
                        f"prose; @bind is only the relationship join."
                    )
                continue
            ln = text.count("\n", 0, m.start()) + 1
            problems.append(
                f"{rel}:{ln}: @bind('{m.group(1)}') names no section of "
                f"{_spec_name(docs)}. The entry cites a missing commitment; "
                f"point it at the section that "
                f"holds the content now."
            )
        for slug, pos in _code_spec_refs(text):
            if slug in spec:
                continue
            ln = text.count("\n", 0, pos) + 1
            problems.append(
                f"{rel}:{ln}: spec section '#{slug}' does not exist in "
                f"{_spec_name(docs)}. The entry cites a commitment that has been "
                f"missing."
            )
        for block, offset in _blocks(text):
            for span, pos in _attributed_spans(block, stems, _spec_names(docs)):
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


def _findings(docs: Path) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Lint the split-metadata layout; replaces physical co-location as the
    drift guard. Eleven hard invariants (any failure → exit 1):

      1. no prose file carries an @-mark (marks live in sidecars);
      2. every sidecar belongs to a unit on disk (no orphan sidecar);
      3. every heading in a sidecar resolves to a real heading/anchor in its
         unit (the mirror can never drift from the prose it indexes);
      4. the spec's level-2 sections each carry an {#slug} anchor,
         unique across the spec (they are the binding namespace);
      5. every @bind is bodyless and its slug names a spec section (any
         number of chapters may bind the same one);
      6. every @see(slug) resolves to a work-wide note in
         `manuscript.review_notes` (pointers into the spec are bindings or
         inline #slug refs, never @see);
      7. every @bind(slug) cited in the companion collections
         (`manuscript.companion_dirs`) names a spec section — the entries are
         the work's one licensed outward dependency, and the citation runs
         back in.
      8. every anchor in an entry's anchors run (`**Anchors:**`, or the label
         `manuscript.companion_anchors` names) names a unit that really binds
         that slug. Invariant 7 gates the slug; this gates the unit
         attributed to it, which nothing checked, so an anchor could name a
         unit that had never expressed the commitment.
      9. every unit reference in an anchors run resolves, and `@unit` is
         never used as a mark kind. A unit-shaped token that resolves to
         nothing used to be skipped in silence, which turned invariant 8 into
         a no-op rather than a failure.
     10. the units on disk are the manuscript's units: for a book, the Quarto
         file and the chapter files are a bijection (every listed file
         exists, every chapter on disk is listed); for a site, no two pages
         share a slug. The dual of invariant 2.
     11. where `manuscript.spec_trail` is set, the trail sidecar parses and each of
         its headings names a spec section. It points into the sessions that
         shaped the spec, and `spec trail --check` follows those pointers.

    Plus six advisories, printed but never fatal.

    Quotations in the companion collections that appear nowhere in canon.
    Heuristic by necessity (entries quote outside sources too, so only a
    paragraph that also cites canon is read), and an advisory for that reason;
    what it catches is a chapter or other metadata edited out from under a live
    quotation, which no gate saw before companion documents were configured.

    Spec sections in the imperative or contract register (a
    NEVER / DO NOT / BANNED order, or an all-caps run). The spec states and
    prices — "the archive records source hashes" — and never orders, because a
    well-stated commitment makes its own instruction derivable; an imperative
    is almost always an under-stated commitment.

    Marks that reference declared non-canonical working material. Retaining
    evidence does not make it a semantic owner. Stable reader-facing companion
    documents remain permitted. The check stays advisory.

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

    And dangling inline spec `#slug` references in any mark body. Inline
    refs are not part of the binding graph, and a section merge or rename is
    exactly the edit that dangles them; they resolve against the spec anchors.

    Figure sources are deliberately NOT checked here: a @figure mark may be
    drafted before its SVG is built, and
    `figures --check`
    already gates every rendered embed against its source."""
    docs = (
        Path(docs) if docs is not None else current_project().path("manuscript.source")
    )
    problems: list[str] = []

    # 1. every prose file is mark-free.
    for prose in units.prose_files(docs):
        text = _mask_fences(prose.read_text())
        for m in _ANNO.finditer(text):
            ln = text.count("\n", 0, m.start()) + 1
            home = units.marks_path(units.slug_of(prose), docs)
            where = _rel(home) if home is not None else "a marks sidecar"
            problems.append(
                f"{_rel(prose)}:{ln}: @{m.group(1).lower()} mark left in prose — "
                f"move it to {where}"
            )

    # 2. no orphan sidecars; 3. mirror integrity.
    for side in units.sidecars(docs):
        unit = units.prose_for_sidecar(side, docs)
        if unit is None or not unit.exists():
            named = _rel(unit) if unit is not None else f"'{units.sidecar_slug(side)}'"
            problems.append(
                f"{_rel(side)}: orphan sidecar — no {units.noun()} {named} for it"
            )
            continue
        ch_labels = {lbl for _, lbl in _headings(_mask_fences(unit.read_text()))}
        for _, lbl in _headings(_mask_fences(side.read_text())):
            if lbl not in ch_labels:
                problems.append(
                    f"{_rel(side)}: heading '{lbl}' has no matching heading or "
                    f"{{#anchor}} in {_rel(unit)} (the mirror has drifted)"
                )

    annos = scan(units.harvest(docs))

    # 4. the register's binding namespace: anchored level-2 sections, unique
    # by full name, since a binding names a section and not a file. A unit's
    # own sections are named `unit/anchor`, so they only need to be unique in
    # their file; the shared spec's are unique across the register.
    sections = spec_sections(docs)
    spec_seen: dict[str, Section] = {}
    shared = {s.slug for s in sections if s.slug and spec_owner(s.path) is None}
    for s in sections:
        if not s.slug:
            problems.append(
                f"{_rel(s.path)}: section '{s.title}' has no {{#slug}} "
                f"anchor (level-2 sections are the binding namespace)"
            )
        elif "/" in (s.anchor or ""):
            problems.append(
                f"{_rel(s.path)}: anchor '#{s.anchor}' contains '/', which "
                f"separates a {units.noun()} from its section; drop it"
            )
        elif spec_owner(s.path) is not None and s.anchor in shared:
            # In the unit's own marks a bare name finds its own section first,
            # so the shared one would be out of that unit's reach.
            problems.append(
                f"{_rel(s.path)}: anchor '#{s.anchor}' is also a shared section; "
                f"in this {units.noun()}'s marks the name could mean either, so "
                f"rename one"
            )
        elif s.slug in spec_seen:
            first = spec_seen[s.slug]
            also = (
                f"'{first.title}'"
                if first.path == s.path
                else f"'{first.title}' in {_rel(first.path)}"
            )
            problems.append(
                f"{_rel(s.path)}: duplicate section anchor '#{s.slug}' (also on {also})"
            )
        else:
            spec_seen[s.slug] = s
    spec = set(spec_seen)

    # 11. the trail sidecar, where the project keeps one, mirrors the spec. A
    # renamed or retired section breaks its trail in the same edit that breaks
    # its bindings, so the two are reported together. Following a breadcrumb
    # into its session is `spec trail --check`'s job: that reads the corpus.
    if current_project().get("manuscript.spec_trail") is not None:
        from quirework.manuscript import trail

        problems.extend(trail.structure_problems(spec))

    # 5. every @bind slug resolves to a spec section. Duplicates across
    # units are the point: any number of units may express one section.
    for a in annos:
        if a.kind != "bind":
            continue
        slug = resolve(a.args[0], units.slug_of(a.file), spec) if a.args else None
        # The sidecar and line the mark actually lives at, so a gate failure
        # under pre-commit pressure points at the editable file (a.file is the
        # chapter, whose line numbers do not apply).
        at = f"{a.source or a.file}:{a.line}"
        if len(a.args) != 1:
            problems.append(
                f"{at}: @bind takes exactly one slug (got {len(a.args)}). "
                f"Write one bodyless relationship per spec section."
            )
        if not slug:
            continue
        if a.body:
            problems.append(
                f"{at}: @bind('{slug}') has a body. Bindings are relationships, "
                f"not preservation contracts; route the content to the spec, "
                f"guidance, a todo, the prose, or git."
            )
        if slug in spec:
            continue
        problems.append(
            f"{at}: @bind('{slug}') binds no section of "
            f"{_spec_name(docs)}. Every @bind names a spec section: "
            f"name the section the {units.noun()} expresses, or add it."
        )

    # 6. @see resolves to a book-wide note.
    rn = _configured_book_file(docs, "manuscript.review_notes")
    rn_slugs = (
        {a.args[0] for a in scan_text(rn.read_text(), rn.name) if a.args}
        if rn is not None and rn.exists()
        else set()
    )
    for a in annos:
        if a.kind != "see":
            continue
        slug = a.args[0] if a.args else None
        at = f"{a.source or a.file}:{a.line}"
        if not slug:
            problems.append(f"{at}: @see with no slug")
        elif rn is None:
            problems.append(
                f"{at}: @see('{slug}') requires configured manuscript.review_notes"
            )
        elif slug not in rn_slugs:
            problems.append(
                f"{at}: @see('{slug}') resolves to no work-wide note in {rn.name}"
            )

    # 9. `@unit` is a reference form, never a mark kind. A comment that opens
    # with it (`<!-- @unit(a-chapter) ... -->`) is harvested as a new kind by
    # _ANNO and would quietly join the vocabulary, which is how the tag set
    # grows by accident. The pointer belongs inside a mark's body.
    for a in annos:
        if a.kind == "unit":
            problems.append(
                f"{a.source or a.file}:{a.line}: @unit is a *reference* to a "
                f"{units.noun()}, not a mark kind — put the pointer in a mark's body"
            )

    # 10. the units on disk are the manuscript's units. The dual of invariant
    # 2: under slug-only, the remaining way to orphan a book chapter is to
    # rename the file and forget the config. Owned by units, which owns the
    # registry.
    problems += units.order_problems(docs)

    # 7/8. the companion collections resolve back into the spec, their
    # quotations still exist in the book they quote, and each entry's
    # **Anchors:** run names chapters that really bind those slugs. Computed
    # here, before the problems are printed; the quotation half and the
    # missing-run half are advisories and print below with the others.
    # Companion paths resolve from the project root, so a caller pointing check() at a
    # temporary tree (the tests do) gets that tree's companions or none, never
    # the live repo's measured against a synthetic spec.
    comp_problems, comp_quotes, comp_anchors = companion_check(
        docs, spec, root=docs.parent
    )
    problems += comp_problems

    for a in annos:
        if a.kind == "bind" or not a.body:
            continue
        for m in _BIND_REF.finditer(a.body):
            problems.append(
                f"{a.source or a.file}:{a.line}: @{a.kind} cites "
                f"@bind({m.group(1)}). Use '#{m.group(1)}' for the spec "
                f"section; @bind is reserved for bodyless relationships."
            )

    def at(a) -> str:
        return f"{a.source or a.file}:{a.line}: @{a.kind}" + (
            f"({a.args[0]})" if a.args else ""
        )

    # Each advisory is a (header, lines) group, printed by `check`. The header
    # carries `{n}` for its count, so a subset of the lines (what `--since`
    # prints) is announced with its own number.
    groups: list[tuple[str, list[str]]] = []

    # A unit's own spec written before the unit is legitimate, which is why
    # this is an advisory. It is also exactly what a mistyped slug looks like.
    present = units.names(docs)
    groups.append(
        (
            f"{{n}} spec file(s) belong to no {units.noun()} yet; a planned "
            f"{units.noun()} is fine, a mistyped slug is not",
            [
                f"  {_rel(f)}"
                for f in spec_files(docs)
                if (owner := spec_owner(f)) is not None and owner not in present
            ],
        )
    )

    # Directories whose contents are working material rather than authority. A
    # mark that rests on one has put a commitment somewhere the book does not
    # carry. Which directories those are is the book's own placement, so an
    # undeclared project gets no advisory rather than a report about a tree it
    # does not have.
    noncanonical = tuple(
        current_project().get("manuscript.noncanonical_roots", ()) or ()
    )
    groups.append(
        (
            f"{{n}} mark(s) reference {', '.join(noncanonical)}; a mark states "
            f"its own commitment — advisory, not yet gated",
            [
                f"  {at(a)}"
                for a in annos
                # An origin story names where the material came from, which
                # is often a brief in working material. That records history
                # rather than resting a commitment on it, the same reason
                # @genesis is exempt from the journaling lint.
                if a.kind != "genesis" and any(root in a.body for root in noncanonical)
            ],
        )
    )

    groups.append(
        (
            "{n} block-surfaced mark(s) in AI register; these are what the "
            "author reads in the .docx",
            [
                f"  {at(a)} — '{m.group(0)}'"
                for a in annos
                if a.kind in BLOCK_KINDS and a.body
                for m in [_CAPS_RUN.search(a.body)]
                if m
            ],
        )
    )

    journ: list[str] = []
    for a in annos:
        if a.kind in CONTRACT_KINDS and a.body:
            m = _JOURNALING.search(a.body)
            if m:
                journ.append(f"  {at(a)} — '{m.group(0)}'")
    for s in sections:
        m = _JOURNALING.search(s.body)
        if m:
            journ.append(f"  {_rel(s.path)}#{s.slug or s.title} — '{m.group(0)}'")
    groups.append(
        (
            "{n} contract mark(s)/spec section(s) carry decision-journaling; "
            "current state, never history — the story belongs in git",
            journ,
        )
    )

    # Every pin, not the first per mark: a mark carrying two of them is exactly
    # where reporting one is worst, because the author fixes what is printed
    # and the survivor reads as cleared. The live instance had two in one
    # sentence, and only the second was wrong.
    pins: list[str] = []
    for a in annos:
        if a.body:
            for m in _LINE_PIN.finditer(a.body):
                pins.append(f"  {at(a)} — '{m.group(0)}'")
    for s in sections:
        for m in _LINE_PIN.finditer(s.body):
            pins.append(f"  {_rel(s.path)}#{s.slug or s.title} — '{m.group(0)}'")
    groups.append(
        (
            "{n} mark(s)/spec section(s) pin a line distance; the next "
            "insertion above makes the number wrong — name the scene or the "
            "phrase instead",
            pins,
        )
    )

    groups.append(
        (
            "{n} companion quotation(s) appear nowhere in the work; a quote is "
            "primary evidence and is never paraphrased into existence",
            comp_quotes,
        )
    )
    groups.append(
        (
            f"{{n}} companion entry/entries anchor nothing; an entry with no "
            f"resolving **{_anchors_label()}:** run is invisible to the join "
            f"that keeps the collection in step",
            comp_anchors,
        )
    )

    groups.append(
        (
            "{n} inline spec #slug reference(s) resolve to no spec section",
            [
                f"  {at(a)} — #{slug}"
                for a in annos
                if a.body
                for slug in _SPEC_REF.findall(a.body)
                if resolve(slug, units.slug_of(a.file), spec) not in spec
            ],
        )
    )

    # The spec is prose and is held to both tells. Bindings are bodyless
    # relationships, so only the spec can carry an imperative here.
    spec_register: list[str] = []
    for s in sections:
        m = _IMPERATIVE.search(s.body) or _CAPS_RUN.search(s.body)
        if m:
            spec_register.append(
                f"  {_rel(s.path)}#{s.slug or s.title} — '{m.group(0)}'"
            )
    groups.append(
        (
            "{n} spec section(s) in the imperative or contract register; the "
            "spec states and prices, never orders",
            spec_register,
        )
    )
    return problems, [(header, lines) for header, lines in groups if lines]


def _advise(groups: list[tuple[str, list[str]]]) -> None:
    for header, lines in groups:
        print(
            f"\nadvisory ({header.replace('{n}', str(len(lines)))}):", file=sys.stderr
        )
        for line in lines:
            print(line, file=sys.stderr)


# A line number is where an advisory sits, not what it says: an edit above a
# mark moves it without changing it, and must not make it read as new.
_LINE_NO = re.compile(r":\d+:")


def introduced(groups, before):
    """The advisory lines in `groups` that `before` did not have.

    Compared per group, as text with line numbers taken out, and counted: a
    second identical advisory in the same file is new even though the first
    was not.
    """
    had = {
        header: Counter(_LINE_NO.sub(":", line) for line in lines)
        for header, lines in before
    }
    new, standing = [], 0
    for header, lines in groups:
        left = Counter(had.get(header, {}))
        fresh = []
        for line in lines:
            key = _LINE_NO.sub(":", line)
            if left[key]:
                left[key] -= 1
                standing += 1
            else:
                fresh.append(line)
        if fresh:
            new.append((header, fresh))
    return new, standing


def _extract(top: Path, ref: str, prefix: str, dest: Path) -> None:
    """Write the files `ref` holds under `prefix` into `dest`, byte for byte.

    Not `git archive`, which honours export-ignore and export-subst and so can
    leave out or rewrite a file the check reads, making its advisories look
    new. Not a checkout either, which runs smudge filters and can reach the
    network. The committed blobs are the revision. They are read one at a
    time, so memory is bounded by the largest file rather than the tree.
    """
    listing = subprocess.run(
        ["git", "-C", str(top), "ls-tree", "-r", "-z", "--full-tree", ref]
        + (["--", prefix] if prefix else []),
        capture_output=True,
        check=True,
    )
    blobs = []
    for record in listing.stdout.split(b"\0"):
        meta, _, path = record.partition(b"\t")
        if not record or meta.split()[1] != b"blob":
            continue  # a submodule entry is another repository's commit
        mode, _, sha = meta.split()
        blobs.append((mode, sha, Path(os.fsdecode(path))))
    with subprocess.Popen(
        ["git", "-C", str(top), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    ) as batch:
        for mode, sha, path in blobs:
            batch.stdin.write(sha + b"\n")
            batch.stdin.flush()
            size = int(batch.stdout.readline().split()[2])
            body = batch.stdout.read(size)
            batch.stdout.read(1)  # the newline after each object
            target = dest / (path.relative_to(prefix) if prefix else path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == b"120000":
                target.symlink_to(os.fsdecode(body))
            else:
                target.write_bytes(body)
        batch.stdin.close()
    if batch.returncode:
        raise ValueError(f"--since {ref}: git cat-file failed")


def _findings_at(ref: str, docs: Path):
    """The same findings for the book as it stood at a git revision.

    The tree at `ref` is written into a private directory that does not
    outlive the call, and checked in a project rooted there with today's
    settings, so both trees answer to the same rules and name their files
    relative to their own root. Nothing touches the index or adds a worktree.

    The project is rebuilt from its settings rather than copied with a new
    root: a copy keeps each setting's origin, so every configured path would
    still resolve into the live checkout. A site's marks and spec sit outside
    its source, and would then be read from today's tree while its pages came
    from the revision.
    """
    import tempfile

    from quirework.project import Project, private_tmp_parent, project_scope

    project = current_project()
    try:
        inside = Path(docs).resolve().relative_to(project.root)
    except ValueError:
        raise ValueError(
            f"--since needs the manuscript source inside the project: {docs}"
        ) from None

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(project.root), *args], capture_output=True, text=True
        )

    if git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode:
        raise ValueError(f"--since {ref}: not a commit in this repository")
    top = Path(git("rev-parse", "--show-toplevel").stdout.strip()).resolve()
    prefix = project.root.relative_to(top).as_posix()
    prefix = "" if prefix == "." else prefix
    with tempfile.TemporaryDirectory(
        prefix="quirework-notes-since-", dir=private_tmp_parent()
    ) as tmp:
        root = Path(tmp).resolve()
        _extract(top, ref, prefix, root)
        then = Project.from_settings(root, project.describe()["settings"])
        with project_scope(then):
            return _findings(root / inside)


def check(docs: Path | None = None, since: str | None = None) -> int:
    """Run `_findings`, print them, and exit non-zero on a hard problem.

    With `since`, the advisories the book did not have at that revision are
    printed first, then only a count of the ones it already had: a change's
    own advisories were otherwise found by grepping a long standing list.
    """
    docs = (
        Path(docs) if docs is not None else current_project().path("manuscript.source")
    )
    problems, groups = _findings(docs)
    for p in problems:
        print(p, file=sys.stderr)
    if since is None:
        _advise(groups)
    else:
        new, standing = introduced(groups, _findings_at(since, docs)[1])
        if new:
            print(f"\nnew since {since}:", file=sys.stderr)
            _advise(new)
        else:
            print(f"\nno advisory new since {since}.", file=sys.stderr)
        print(
            f"\n{standing} standing advisory line(s) the work already had at "
            f"{since}; run without --since to list them.",
            file=sys.stderr,
        )
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
        prog="quirework spec notes",
        description="Harvest the manuscript's @-annotations from its sidecars. "
        "Positional args filter by kind (bind, guidance, todo, ...); "
        "run `quirework spec notes --list` to see the kinds and their counts.",
        epilog="examples:\n"
        "  quirework spec notes                     all marks, grouped by kind\n"
        "  quirework spec notes bind                only @bind relationships\n"
        "  quirework spec notes --primer            the fresh-session primer\n"
        "                                       (bind+guidance, by unit)\n"
        "  quirework spec notes bind --slug authorship-by-log\n"
        "                                       who binds that spec section\n"
        "  quirework spec notes relations --slug authorship-by-log\n"
        "                                       the same, without the word bind\n"
        "  quirework spec notes --by unit           every unit's marks, in order\n"
        "  quirework spec notes --list              the kind vocabulary + counts",
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
        choices=("kind", "unit"),
        default=None,
        help="group output by kind (default) or by unit, in reading order",
    )
    ap.add_argument(
        "-c",
        "--unit",
        metavar="SUBSTR",
        help="only marks of units whose path matches this substring "
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
        "(`notes bind --slug X` = which units bind spec section X)",
    )
    ap.add_argument(
        "--primer",
        action="store_true",
        help="shortcut: @bind + @guidance, by unit (fresh-session context)",
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
        "--since",
        metavar="REF",
        default=None,
        help="with --check, print first the advisories the work did not have "
        "at this git revision, then only a count of the standing ones",
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
        help="scan this file/dir instead of manuscript.source (repeatable)",
    )
    args = ap.parse_args(argv)

    if args.check:
        # --check is the layout gate, not a view: the filters below do not
        # apply to it, and silently ignoring one would return a verdict about
        # a different question than the one asked. A single --path forwards
        # as the source root to lint (what the tests do via check(docs=...)).
        ignored = [
            name
            for name, val in (
                ("KIND", args.kinds or args.kind),
                ("--by", args.by),
                ("-c/--unit", args.unit),
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
                "--check takes at most one --path (the source root to lint).",
                file=sys.stderr,
            )
            return 2
        docs = Path(args.path[0]) if args.path else None
        try:
            return check(docs, since=args.since)
        except (ValueError, subprocess.CalledProcessError) as exc:
            print(f"notes --check: {exc}", file=sys.stderr)
            return 2

    if args.since:
        print("--since compares advisories, so it needs --check.", file=sys.stderr)
        return 2

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
                "\n  A map, not a scorecard. A section about the spec's own "
                "organization or\n  vocabulary belongs to no entry, and that "
                "is not a gap."
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
    # A positional kind may be an alias; --kind is always the literal name.
    positional = [k.lower() for k in args.kinds]
    clash = [k for k in positional if k in KIND_ALIASES and k in present]
    if clash:
        # The tag set is open, so a book may have its own kind by that name.
        # Guessing which one was meant would answer a different question.
        for k in clash:
            print(
                f"'{k}' is both a kind in this project and an alias for "
                f"'{KIND_ALIASES[k]}'; name the one you mean with --kind",
                file=sys.stderr,
            )
        return 2
    explicit: list[str] = [KIND_ALIASES.get(k, k) for k in positional]
    for spec in args.kind:
        explicit += [p.strip().lower() for p in spec.split(",") if p.strip()]
    explicit = list(dict.fromkeys(explicit))  # de-dup, preserve order

    by = args.by or ("unit" if args.primer else "kind")

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

    # Validate the unit filter against the scanned files, likewise. A unit
    # with no marks yet still matches when the registry has it: a post is
    # specified before its sidecar exists, and the primer is how a session
    # reads that spec.
    all_files = sorted({a.file for a in annos})
    scoped: set[str] | None = None
    if args.unit:
        sub = args.unit.lower()
        try:
            listed = units.registry()
        except (OSError, ValueError):
            listed = []
        scoped = {u.slug for u in listed if sub in u.rel.lower()}
        scoped |= {units.slug_of(f) for f in all_files if sub in f.lower()}
        if not scoped:
            kind = units.noun()
            print(f"no {kind} matches '{args.unit}'. {kind}s:", file=sys.stderr)
            for f in all_files:
                print(f"  {f}", file=sys.stderr)
            return 2

    if requested:
        want = set(requested)
        annos = [a for a in annos if a.kind in want]
    if args.unit:
        sub = args.unit.lower()
        annos = [a for a in annos if sub in a.file.lower()]
    # --slug and --section are validated against what the earlier filters
    # left, so the error is scoped to the kinds/chapter actually asked for
    # (a typo errors with rc 2 like -c; an empty *combination* stays rc 0).
    if args.slug:
        sub = args.slug.lower()
        # A bare name in a unit's own marks means that unit's section, so the
        # filter matches the full name a binding resolves to as well.
        try:
            known = spec_slugs()
        except ConfigurationError:
            known = set()

        def names(a):
            return [
                *a.args,
                *(resolve(x, units.slug_of(a.file), known) for x in a.args),
            ]

        matching = [a for a in annos if any(sub in n.lower() for n in names(a))]
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

    if args.primer and not args.path:
        _print_spec_toc(only=scoped)  # a custom --path scan has no spec of its own
    if not annos:
        # Every filter here was already validated as matching *something*, so an
        # empty result means the combination is empty -- say which combination.
        active = []
        if requested:
            active.append("kind(s) " + ", ".join(requested))
        if args.unit:
            active.append(f"{units.noun()} ~ '{args.unit}'")
        if args.slug:
            active.append(f"slug ~ '{args.slug}'")
        if args.section:
            active.append(f"section ~ '{args.section}'")
        where = "; ".join(active) if active else "the scanned sources"
        print(f"no marks match ({where}).")
        return 0

    if by == "unit":
        _render_by_chapter(annos)
    else:
        _render_by_kind(annos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
