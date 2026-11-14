"""A spec section's summary: the labeled paragraph that opens it.

A reader scanning a long spec, a person or an agent, wants each section's
gist before its text. A project that opens each section with one names the
label the summary carries in bold, and the feature is off until it does:

    [manuscript]
    spec_summary_label = "Summary."
    spec_summary_words = 70           # the advisory limit, 70 by default
    spec_summary_min_words = 40       # a lower bound, unset by default
    spec_digest = "spec/digest.md"    # every summary, by part

A section's summary is then its first paragraph, directly under its heading,
when that paragraph opens with the label in bold:

    ## The flash {#flash}

    **Summary.** The flash marks the rock on a clear night.

The summary is that paragraph without its label, comments dropped and
whitespace collapsed. The spec index carries it, and the digest lists every
section's summary under its part, in reading order (`specindex`).

A summary is read where its section is not, in the index, the digest or a
page built from them, so it says what the section holds in words of its own.
`spec notes --check` refuses a summary that cites a section (`#slug`, in a
code span or not), since its reader has the gist and not the section to
follow the citation from, and a label with no summary after it. A summary
longer than the word limit is an advisory, and so is one shorter than the
lower bound, where the project sets one, and the count of published sections
that have none yet, since a project may add them gradually.
"""

from __future__ import annotations

import re
from pathlib import Path

from quirework.manuscript import notes
from quirework.project import current_project

LABEL = "manuscript.spec_summary_label"
WORDS = "manuscript.spec_summary_words"
MIN_WORDS = "manuscript.spec_summary_min_words"
DIGEST = "manuscript.spec_digest"
DEFAULT_WORDS = 70

_PARAGRAPH_END = re.compile(r"\n[ \t]*\n")


def label(project=None) -> str | None:
    """The bold label a summary opens with, or None while summaries are off."""
    return (project or current_project()).get(LABEL)


def word_limit(project=None) -> int:
    """How many words a summary may run to before the advisory names it."""
    return (project or current_project()).get(WORDS, DEFAULT_WORDS)


def word_floor(project=None) -> int | None:
    """How many words a summary needs before the advisory stops naming it, or
    None where the project sets no lower bound."""
    return (project or current_project()).get(MIN_WORDS)


def summary(paragraph: str, label: str) -> str | None:
    """The summary ``paragraph`` carries: None unless it opens with ``label``
    in bold, and an empty string for a label with nothing after it."""
    bold = f"**{label}**"
    if not paragraph.startswith(bold):
        return None
    rest = paragraph[len(bold) :]
    if rest and not rest[0].isspace():
        return None  # `**Summary.**s` is not the label standing alone
    return " ".join(notes._COMMENT.sub(" ", rest).split())


def of_file(path: Path, label: str) -> dict[str, str | None]:
    """Each anchored section of the spec file ``path``, by its full name, and
    its summary or None. The paragraph is read as the file holds it, so a
    comment or a code block between the heading and a labeled paragraph means
    the section does not open with a summary."""
    text = path.read_text(encoding="utf-8")
    masked = notes._mask_nonprose(notes.mask_fences(text))
    stops = notes.section_stops(masked)
    owner = notes.spec_owner(path)
    out: dict[str, str | None] = {}
    for m in notes._HEADING.finditer(masked):
        if len(m.group(1)) != 2:
            continue
        anchor = notes._ATTR_ID.search(m.group(3)) if m.group(3) else None
        if not anchor:
            continue
        stop = next((s for s in stops if s > m.start()), len(text))
        # From the end of the heading's own line: the heading pattern's
        # trailing space runs on over a masked comment below it.
        line_end = masked.find("\n", m.start())
        start = len(text) if line_end == -1 else line_end
        first = _PARAGRAPH_END.split(text[start:stop].strip(), 1)[0]
        name = anchor.group(1)
        out[f"{owner}/{name}" if owner else name] = summary(first, label)
    return out


def findings(docs: Path, spec: set[str]) -> tuple[list[str], list]:
    """What `spec notes --check` reports about the summaries: the problems,
    and the advisory groups as `notes._findings` returns them. Nothing while
    the label is unset.

    Every spec file's summaries are held to the rules, published or not,
    since a rule about what a summary is applies wherever one is written. The
    count of sections without one is taken over the published spec, the
    sections the index and the digest read."""
    found = label()
    if found is None:
        return [], []
    from quirework.manuscript import specindex

    limit, least = word_limit(), word_floor()
    problems: list[str] = []
    long: list[str] = []
    short: list[str] = []
    read = {path: of_file(path, found) for path in notes.spec_files(docs)}
    for path, held in read.items():
        owner = notes.spec_owner(path)
        for slug, text in held.items():
            at = f"{notes._rel(path)}#{slug}"
            if text == "":
                problems.append(
                    f"{at}: the summary label **{found}** has no summary after "
                    f"it; write the summary or drop the label"
                )
                continue
            if text is None:
                continue
            for m in notes._SPEC_REF.finditer(text):
                if notes.resolve(m.group(1), owner, spec) in spec:
                    problems.append(
                        f"{at}: the summary cites '#{m.group(1)}'. A summary is "
                        f"read where its section is not, so it says what the "
                        f"section holds without a citation; the section's text "
                        f"can carry it."
                    )
            words = specindex._words(text)
            if words > limit:
                long.append(f"  {at} — {words} words")
            elif least is not None and words < least:
                short.append(f"  {at} — {words} words")
    published = {
        slug: text
        for path in specindex._published(docs)
        for slug, text in (read[path] if path in read else of_file(path, found)).items()
    }
    missing = sum(1 for text in published.values() if not text)
    groups = [
        (
            f"{{n}} spec summary/summaries run past {limit} words; a summary "
            f"is the section's gist, read where its text is not",
            long,
        )
    ]
    if least is not None:
        groups.append(
            (
                f"{{n}} spec summary/summaries run under {least} words; a "
                f"summary is the section's gist, read where its text is not",
                short,
            )
        )
    if missing:
        groups.append(
            (
                "spec sections with no summary yet; a project may add them gradually",
                [
                    f"  {missing} of {len(published)} published section(s) open "
                    f"with no **{found}** paragraph"
                ],
            )
        )
    return problems, groups
