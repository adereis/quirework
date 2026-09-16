#!/usr/bin/env python3
"""Wrap Markdown to the repo's line conventions, and strip trailing whitespace.

The repo wraps two ways, by how each file is consumed (see the root AGENTS.md
"Markdown conventions"):

* **Prose** (the canonical book: `docs/**/*.qmd`) is wrapped *one sentence per
  line* ("semantic line breaks"). The book is read rendered or via the red-pen
  `.docx`, almost never as source; sentence-per-line makes a sentence-level edit
  a one-line diff, and Markdown still renders the paragraph as one flow (a lone
  newline is a "soft break" = a space).

* **Technical docs** (`AGENTS.md`/`CLAUDE.md`/`GEMINI.md`, `README.md`, the
  canonical index `docs/canon/_map.md`, `analysis/`, `scratchpad/`, `ideas/`,
  `books-and-papers/`, `exercises/`, `cases/`, provenance READMEs) are
  hard-wrapped at 72 columns, one blank line between paragraphs, like most
  open-source Markdown. These are read
  as source in editors and terminals, so a bounded column keeps them legible; a
  paragraph reflows only within itself, so the diff stays local.

Every mode also strips trailing whitespace from every line. Files that are
generated or verbatim (the provenance transcripts/indexes, the panel raw output)
are skipped entirely; other hand-authored files we don't reflow (SOPs, the
preface variants, the verbatim third-party `paper-full-text.md` dumps) still
get trailing-whitespace cleanup ("trailing" mode).

The mode is chosen by path (`classify`), so both the CLI and the pre-commit hook
just pass files and get the right treatment. A hard self-check (`_canonical`)
compares the file before and after with all whitespace collapsed; if anything
other than whitespace changed, the run aborts and writes nothing.

This module and its sibling artifact_paths helper are stdlib-only. It also
runs as `python ../quirework/src/quirework/text/mdwrap.py` without installing the package.

Usage:
    python ../quirework/src/quirework/text/mdwrap.py FILE [FILE ...]       # rewrap in place
    python ../quirework/src/quirework/text/mdwrap.py --check FILE [...]    # exit 1 if not wrapped
    python ../quirework/src/quirework/text/mdwrap.py --width 72 FILE ...   # column-mode width
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if __package__:
    from quirework.core.artifact_paths import run_evidence
else:  # Direct script invocation loads the stdlib-only sibling helper.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from quirework.core.artifact_paths import run_evidence

DEFAULT_WIDTH = 72

# ---------------------------------------------------------------------------
# Sentence mode (semantic line breaks) — the canonical book prose.
# ---------------------------------------------------------------------------

# Long sentences are additionally broken at semicolons; short ones are left whole.
_CLAUSE_SPLIT_MIN_LEN = 100

# Abbreviations whose trailing "." must not be read as a sentence end.
_ABBREVS = [
    "e.g.",
    "i.e.",
    "etc.",
    "vs.",
    "cf.",
    "al.",
    "esp.",
    "approx.",
    "Mr.",
    "Mrs.",
    "Ms.",
    "Dr.",
    "Prof.",
    "St.",
    "Fig.",
    "No.",
    "Inc.",
    "Ltd.",
    "Jr.",
    "Sr.",
    "Ph.D.",
    "a.m.",
    "p.m.",
]

# Matched as whole words, never as a substring. A bare `str.replace` finds the
# "al." of "et al." inside "real.", "moral.", "animal.", "final." — protecting a
# period that really does end a sentence, so the break after it silently
# disappears. In a book about morals and animals that is not an edge case.
_ABBREV_RE = re.compile(
    r"(?<![A-Za-z0-9.])(?:"
    + "|".join(re.escape(a) for a in sorted(_ABBREVS, key=len, reverse=True))
    + ")"
)

_SENT = "\x00"  # sentinel standing in for a protected "."


def _protect(text: str) -> str:
    """Replace dots that are not sentence ends with a sentinel."""
    text = _ABBREV_RE.sub(lambda m: m.group(0).replace(".", _SENT), text)
    # Single-capital-letter initials: "W. V. O. Quine".
    text = re.sub(r"\b([A-Z])\.(?=\s)", r"\1" + _SENT, text)
    return text


def _restore(text: str) -> str:
    return text.replace(_SENT, ".")


# A boundary is whitespace that follows .!? (optionally then a closing quote)
# and precedes something that can start a sentence.
#
# Known limitation: a quoted title ending in ? or ! that continues mid-sentence
# with a space then ( or a digit reads as a boundary here, so `"...Bat?" (1974)`
# splits and orphans the year. It is cosmetic (a soft break renders as a space),
# but write such citations in the house style the rest of sources.qmd uses --
# `"Title?", 1974` (comma butting the closing quote), or year-in-front -- which
# leaves no space+quote boundary to trip. Not fixed in the splitter on purpose:
# `?"`/`!"` before a capital is genuinely ambiguous (title mid-sentence vs. a
# quoted question ending a sentence), and any heuristic trades this cosmetic
# miss for an equally cosmetic one.
_BOUNDARY = re.compile(r'(?:(?<=[.!?])|(?<=[.!?]["”’\')]))\s+(?=[A-Z0-9"“(\[*])')
_SEMI = re.compile(r"(?<=;)\s+")


def _split_paragraph(text: str) -> list[str]:
    """One prose paragraph (already joined to a single string) -> lines."""
    protected = _protect(text)
    sentences = _BOUNDARY.split(protected)
    lines: list[str] = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(s) > _CLAUSE_SPLIT_MIN_LEN and ";" in s:
            lines.extend(part.strip() for part in _SEMI.split(s) if part.strip())
        else:
            lines.append(s)
    return [_restore(s) for s in lines]


def _is_blank(line: str) -> bool:
    return line.strip() == ""


# Lines that begin a non-prose block and must pass through verbatim (sentence
# mode leaves list items alone, unlike column mode which reflows them).
_PASSTHROUGH = re.compile(
    r"""^\s*(
        \#            |   # heading
        !\[           |   # block-level image (figure): caption + attrs stay verbatim
        :{3,}         |   # pandoc/quarto fenced div (::: callout)
        >             |   # blockquote
        \|            |   # table row
        [-*+]\s       |   # bullet list item
        \d+[.)]\s     |   # ordered list item
        ---+\s*$      |   # thematic break / front-matter rule
        \*\*\*+\s*$   |   # thematic break
        ___+\s*$      |   # thematic break
        \[[^\]]+\]:       # link reference definition
    )""",
    re.VERBOSE,
)


def _passthrough_line(line: str) -> bool:
    # Any indented line is left alone: prose paragraphs start at column 0, so an
    # indented line is a list-item continuation or indented code — never prose to
    # reflow. (This keeps hard-wrapped, list-heavy docs safe.)
    if line[:1].isspace():
        return True
    return bool(_PASSTHROUGH.match(line))


def _consume_frontmatter(lines: list[str], out: list[str]) -> list[str]:
    """If `lines` opens with a YAML `---` block, copy it verbatim to `out`.

    Returns the remaining lines. Shared by both modes so a multi-key block is
    never reflowed into prose. Leading single-line HTML comments (such as the
    ``Added:`` stamp) are skipped when looking for the opening ``---``.
    """
    start = 0
    while (
        start < len(lines)
        and lines[start].strip().startswith("<!--")
        and lines[start].strip().endswith("-->")
    ):
        start += 1
    if start < len(lines) and lines[start].strip() == "---":
        for j in range(start):
            out.append(lines[j])
        out.append(lines[start])
        i = start + 1
        while i < len(lines):
            out.append(lines[i])
            i += 1
            if lines[i - 1].strip() == "---":
                break
        return lines[i:]
    return lines


_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _find_open(s: str) -> int:
    """Index of the next `<!--` that is a real delimiter, or -1.

    A `<!--` inside an inline code span is text about a comment, not a comment.
    """
    i = s.find("<!--")
    while i >= 0:
        if s.count("`", 0, i) % 2 == 0:
            return i
        i = s.find("<!--", i + 4)
    return -1


def _split_comment_lines(lines: list[str]) -> list[str]:
    """Give every HTML comment whole lines of its own.

    The comment machinery in both modes assumes a comment starts a line and that
    the line holding `-->` ends it. Two shapes break that assumption, and both
    occur in practice when an annotation is hand-deleted from a stack of them:

    * `... v1.0. --><!-- @ref ...` — one comment closes and the next opens on the
      same line. The scanner would end the comment there and hand the tail to
      `_reflow_comment`, which cannot parse it and passes the whole mess through
      verbatim; the joined line then survives `--check` untouched.
    * `Some prose. <!-- @todo ... -->` — a comment opening after prose is not
      seen as a comment at all. Sentence mode reflows it *as prose*, splitting
      the annotation at its internal sentence breaks.

    Splitting at the delimiters first makes both shapes ordinary. Fenced code is
    untouched, and an unterminated `<!--` keeps the rest of the input verbatim.
    """
    out: list[str] = []
    in_fence = False
    in_comment = False
    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence or not line.strip():
            out.append(line)
            continue

        rest = line
        pieces: list[str] = []
        while rest:
            if in_comment:
                i = rest.find("-->")
                if i < 0:
                    pieces.append(rest)
                    break
                pieces.append(rest[: i + 3])
                # Whatever follows the close starts fresh at column 0: left
                # indented, sentence mode would read it as a passthrough block.
                rest = rest[i + 3 :].lstrip()
                in_comment = False
            else:
                i = _find_open(rest)
                if i < 0:
                    pieces.append(rest)
                    break
                if rest[:i].strip():
                    pieces.append(rest[:i].rstrip())
                rest = rest[i:]
                in_comment = True
        out.extend(pieces or [line])
    return out


def rewrap(text: str) -> str:
    """Sentence-per-line rewrap (the canonical-prose mode)."""
    lines = text.split("\n")
    out: list[str] = []
    para: list[str] = []
    in_fence = False
    fence_re = _FENCE_RE
    in_comment = False
    comment_acc: list[str] = []

    lines = _split_comment_lines(_consume_frontmatter(lines, out))

    def flush() -> None:
        if para:
            joined = " ".join(s.strip() for s in para)
            out.extend(_split_paragraph(joined))
            para.clear()

    for line in lines:
        # Fenced code blocks: copy verbatim.
        if fence_re.match(line):
            flush()
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue

        # HTML comments (possibly multi-line): copy verbatim, except for @-annotations.
        if in_comment:
            comment_acc.append(line)
            if "-->" in line:
                in_comment = False
                out.extend(_reflow_comment(comment_acc, DEFAULT_WIDTH))
            continue
        if line.lstrip().startswith("<!--"):
            flush()
            if "-->" not in line:
                in_comment = True
                comment_acc = [line]
            else:
                out.extend(_reflow_comment([line], DEFAULT_WIDTH))
            continue

        if _is_blank(line):
            flush()
            out.append(line)
            continue

        if _passthrough_line(line):
            flush()
            out.append(line)
            continue

        # Plain prose line: accumulate into the current paragraph.
        para.append(line)

    if in_comment:  # unterminated comment: keep it verbatim, never drop it
        out.extend(comment_acc)
    flush()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Column mode (hard wrap at N columns) — the technical docs.
# ---------------------------------------------------------------------------

# Inline spans that must never be split across a line break: a soft break inside
# them changes what Markdown renders (a link destination or code span cannot hold
# a newline). Everything else breaks at whitespace. The `_canonical` self-check
# is the backstop: any accidental break inside one of these inserts a space that
# was not there and aborts the run rather than corrupting the file.
_ATOMIC = re.compile(
    r"""
        `[^`\n]*`                 |   # inline code span
        !?\[[^\]]*\]\([^)]*\)     |   # inline link / image
        !?\[[^\]]*\]\[[^\]]*\]    |   # reference-style link / image
        <[^>\s]+>                     # autolink
    """,
    re.VERBOSE,
)

# A list item: indent, marker (bullet or ordered), then its text.
_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")

# A token that must never be the first on a wrapped line: at line-start (followed
# by a space) it would be parsed as a new block — a list marker, heading,
# blockquote, table row, or thematic/setext rule. A mid-prose "+" (as in "comments
# + tracked runs") landing at line-start would silently become a sublist. When a
# break would put one of these first, we keep it on the current line instead.
#
# This is about Markdown, so it applies to Markdown. Inside an HTML comment no
# block can start, and the guard buys nothing there but over-width lines: the
# repo's annotations use " -- " as an em-dash and cite years as "(Russell 1975)",
# and both "--" and "1975)" match here. `_reflow_comment` waives it.
_UNSAFE_START = re.compile(
    r"^(#{1,6}|[-*+]|\d{1,9}[.)]|>|\|+|-{2,}|={2,}|_{3,}|\*{3,})$"
)

# Non-list blocks that pass through verbatim in column mode.
_PASS_COL = re.compile(
    r"""^\s*(
        \#            |   # heading
        !\[           |   # block-level image
        :{3,}         |   # fenced div
        >             |   # blockquote (kept verbatim: often a quote)
        \|            |   # table row
        ---+\s*$      |   # thematic break
        \*\*\*+\s*$   |   # thematic break
        ___+\s*$      |   # thematic break
        \[[^\]]+\]:       # link reference definition
    )""",
    re.VERBOSE,
)


def _tokenize(text: str) -> list[str]:
    """Split into whitespace-separated tokens, keeping atomic spans whole.

    Whitespace *inside* an atomic span (a link title, a code span) is not a break
    point; whitespace outside it is. Atomic spans stay glued to any adjacent
    non-space characters (so `see [x](y).` keeps the trailing period attached).
    """
    tokens: list[str] = []
    cur: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            if cur:
                tokens.append("".join(cur))
                cur = []
            i += 1
            continue
        m = _ATOMIC.match(text, i)
        if m and m.end() > i:
            cur.append(m.group(0))
            i = m.end()
            continue
        cur.append(ch)
        i += 1
    if cur:
        tokens.append("".join(cur))
    return tokens


def _wrap_tokens(
    tokens: list[str],
    first_prefix: str,
    cont_prefix: str,
    width: int,
    guard_line_start: bool = True,
) -> list[str]:
    """Greedy-wrap `tokens` to `width`, with a first-line and continuation prefix.

    A token that alone overflows `width` (a long URL, a long code span) still
    goes on its own line rather than being broken.

    `guard_line_start` keeps a block-starting token off the front of a wrapped
    line (see `_UNSAFE_START`). Callers wrapping text that Markdown never parses
    as blocks — the inside of an HTML comment — pass False and get the width
    honored instead.
    """
    if not tokens:
        return [first_prefix]
    lines: list[str] = []
    cur = first_prefix + tokens[0]
    for tok in tokens[1:]:
        # A break before a block-starting token would corrupt the render, so keep
        # such a token on the current line (accepting an over-width line) rather
        # than letting it lead a wrapped line.
        unsafe = guard_line_start and _UNSAFE_START.match(tok)
        if len(cur) + 1 + len(tok) > width and not unsafe:
            lines.append(cur)
            cur = cont_prefix + tok
        else:
            cur += " " + tok
    lines.append(cur)
    return lines


def reflow_columns(text: str, width: int = DEFAULT_WIDTH) -> str:
    """Hard-wrap prose paragraphs and list items to `width` columns.

    Verbatim (never reflowed): fenced code, HTML comments, YAML frontmatter,
    tables, blockquotes, headings, thematic breaks, block images, fenced-div
    markers, link reference definitions. List items reflow with a hanging indent
    aligned under their text; nested items keep their indent.
    """
    lines = text.split("\n")
    out: list[str] = []

    acc: list[str] = []
    first_prefix = ""
    cont_prefix = ""
    in_list = False
    # The continuation indent of the list item we are inside, if any. Unlike
    # `in_list`, this survives a blank line: a list item may hold several
    # paragraphs, and the blank between them ends the paragraph, not the item.
    list_indent = ""

    def flush() -> None:
        nonlocal acc, first_prefix, cont_prefix, in_list
        if acc:
            tokens = _tokenize(" ".join(s.strip() for s in acc))
            out.extend(_wrap_tokens(tokens, first_prefix, cont_prefix, width))
        acc = []
        first_prefix = ""
        cont_prefix = ""
        in_list = False

    in_fence = False
    fence_re = _FENCE_RE
    in_comment = False
    comment_acc: list[str] = []

    lines = _split_comment_lines(_consume_frontmatter(lines, out))

    for line in lines:
        if fence_re.match(line):
            flush()
            if not line[:1].isspace():  # a column-0 fence ends any list
                list_indent = ""
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue

        if in_comment:
            comment_acc.append(line)
            if "-->" in line:
                in_comment = False
                out.extend(_reflow_comment(comment_acc, width))
            continue
        if line.lstrip().startswith("<!--"):
            flush()
            if not line[:1].isspace():  # a column-0 comment ends any list
                list_indent = ""
            if "-->" not in line:
                in_comment = True
                comment_acc = [line]
            else:
                out.extend(_reflow_comment([line], width))
            continue

        if _is_blank(line):
            flush()
            out.append(line)
            continue

        m = _LIST_RE.match(line)
        if m:
            # A new list item (nested items match too, by their deeper indent).
            flush()
            indent, marker, rest = m.group(1), m.group(2), m.group(3)
            first_prefix = f"{indent}{marker} "
            cont_prefix = " " * len(first_prefix)
            acc = [rest]
            in_list = True
            list_indent = cont_prefix
            continue

        # A continuation line of the current list item: indented, not a marker.
        if in_list and line[:1].isspace():
            acc.append(line)
            continue

        # A further paragraph of the list item we are still inside, after the
        # blank line that ended the previous one. Markdown draws the line by
        # indent: content indented to the item's own text continues the item,
        # and four columns beyond that is an indented code block. So reflow the
        # first case with the item's hanging indent and leave the second alone.
        if list_indent and not in_list and line[:1].isspace():
            indent = line[: len(line) - len(line.lstrip())]
            if len(list_indent) <= len(indent) < len(list_indent) + 4:
                first_prefix = cont_prefix = list_indent
                acc = [line]
                in_list = True
                continue

        # Any other indented line (not a list continuation): could be indented
        # code or a block we don't reflow. Keep verbatim — never dedent it. The
        # `_canonical` check can't guard this (it collapses leading whitespace).
        if line[:1].isspace():
            flush()
            out.append(line)
            continue

        if _PASS_COL.match(line):
            flush()
            list_indent = ""
            out.append(line)
            continue

        # Column-0 prose. If we were in a list, this ends it.
        if in_list:
            flush()
        list_indent = ""
        acc.append(line)

    flush()
    if in_comment:  # unterminated comment: keep it verbatim, never drop it
        out.extend(comment_acc)
    return "\n".join(out)


def _reflow_comment(lines: list[str], width: int) -> list[str]:
    """Hard-wrap an HTML comment at `width` if it's an @-annotation.

    Other HTML comments pass through verbatim.
    """
    raw = "\n".join(lines)
    if not re.search(r"<!--\s*@", raw):
        return lines

    m = re.match(r"^(\s*<!--)(.*?)(-->\s*)$", raw, re.DOTALL)
    if not m:
        return lines

    start_marker, inner, end_marker = m.groups()
    inner = re.sub(r"\s+", " ", inner).strip()

    if not inner:
        return [start_marker + " " + end_marker.strip()]

    tokens = _tokenize(inner)
    first_prefix = start_marker + " "
    cont_prefix = " " * len(first_prefix)

    wrapped = _wrap_tokens(
        tokens, first_prefix, cont_prefix, width, guard_line_start=False
    )
    if wrapped:
        # The closer rides on the last line and may carry it to width + 4. That
        # is deliberate (see AGENTS.md, "Markdown conventions"): `-->` is a
        # delimiter, not content, and wrapping it as a token would orphan it
        # onto a line of its own. Pinned by a test — don't "fix" it.
        wrapped[-1] += " -->"

    return wrapped


# ---------------------------------------------------------------------------
# Shared: self-check, path classification, file processing.
# ---------------------------------------------------------------------------


def _canonical(text: str) -> list[str]:
    """Paragraph blocks with all whitespace collapsed.

    Two texts with the same canonical form differ only in whitespace and so
    render identically and carry identical content.
    """
    blocks: list[str] = []
    cur: list[str] = []
    for line in text.split("\n"):
        if _is_blank(line):
            if cur:
                blocks.append(re.sub(r"\s+", " ", " ".join(cur)).strip())
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append(re.sub(r"\s+", " ", " ".join(cur)).strip())
    return blocks


def _strip_trailing(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.split("\n"))


def _project_rel(path: str) -> str:
    """Path relative to the selected project's root, posix.

    Every other path setting is written relative to the directory holding
    `quirework.toml`, and this one has to agree with them, because what it
    produces is compared against `runs.evidence_roots`. It used to anchor on
    the nearest `pyproject.toml` instead, which is the same directory for a
    book that is its own repository and a different one for a book kept inside
    a larger project: there, a declared evidence root matched nothing, saved
    run records were reflowed, and the configuration looked right on the page.

    Falls back to the path as given when it lies outside the project, so
    classification still works on a bare filename.
    """
    from quirework.project import current_project

    p = Path(path)
    try:
        resolved = p.resolve()
    except OSError:
        resolved = p
    try:
        return resolved.relative_to(current_project().root).as_posix()
    except ValueError:
        return p.as_posix()


def classify(path: str) -> str:
    """Choose a format, with project-specific classification explicitly selected."""
    from quirework.project import current_project, extension

    configured = current_project().get("text.classifier")
    if configured:
        mode = extension(configured)(path)
        if mode not in {"sentence", "column", "trailing", "skip"}:
            raise ValueError(f"text classifier returned an unknown mode: {mode}")
        return mode
    if Path(path).name == "GEMINI.md":
        return "trailing"
    if path.endswith(".qmd"):
        return "sentence"
    if path.endswith(".md"):
        return "skip" if run_evidence(_project_rel(path)) else "column"
    return "skip"


def process(path: str, check: bool, width: int = DEFAULT_WIDTH) -> bool:
    """Return True if the file already matches its convention."""
    mode = classify(path)
    if mode == "skip":
        return True

    with open(path, encoding="utf-8") as fh:
        original = fh.read()
    trailing_nl = original.endswith("\n")
    body = original.rstrip("\n")

    if mode == "sentence":
        result = rewrap(body)
    elif mode == "column":
        result = reflow_columns(body, width)
    else:  # trailing
        result = body
    result = _strip_trailing(result)
    if trailing_nl:
        result += "\n"

    if _canonical(original) != _canonical(result):
        raise SystemExit(
            f"ABORT: {path}: content would change (not just whitespace). "
            "This is a bug in the wrapper; nothing written."
        )

    if result == original:
        return True
    if check:
        print(f"needs wrapping: {path}")
        return False
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(result)
    print(f"wrapped: {path}")
    return False


MODES = """\
The mode is chosen by path, so just pass files and each gets the right one:

  sentence   one sentence per line (semantic line breaks)
             chapter prose: any .qmd
  column     hard wrap at 72 columns
             every other Markdown file, including guides, READMEs, the
             spec, and the per-chapter metadata sidecars (_marks/*.md),
             whose @-mark bodies are reflowed to the width with a
             hanging indent
  trailing   strip trailing whitespace, never reflow
             GEMINI.md
  skip       never touched
             files inside a saved run record, which stay verbatim

A project chooses differently by selecting text.classifier in its
configuration: a function that takes a path and returns one of these
four names. That is where a book puts rules only it has.
"""


def main(argv: list[str]) -> int:
    # argparse (stdlib, so the module stays standalone for the hook) rather
    # than the old hand-rolled loop: that loop silently swallowed any unknown
    # --flag, so a typo like `--chek` REWROTE the files it was asked to check.
    # For a tool whose safety hinges on one flag, unknown flags must be fatal.
    ap = argparse.ArgumentParser(
        prog="quirework text wrap",
        description="Wrap Markdown to the repo's line conventions and strip "
        "trailing whitespace.",
        epilog=MODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "files", nargs="+", metavar="FILE", help="Markdown file(s) to wrap in place"
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if a file is unwrapped or has trailing "
        "whitespace; write nothing (what the pre-commit hook runs)",
    )
    ap.add_argument(
        "--width",
        type=int,
        default=DEFAULT_WIDTH,
        metavar="N",
        help="override the 72-column target for column mode",
    )
    args = ap.parse_args(argv)

    ok = True
    missing = False
    for path in args.files:
        try:
            if not process(path, args.check, args.width):
                ok = False
        except OSError as e:
            print(f"wrap: cannot read {path}: {e.strerror or e}", file=sys.stderr)
            missing = True
    if missing:
        return 2
    return 0 if (ok or not args.check) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
