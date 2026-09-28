"""Do the phrases a mark quotes still exist in the book?

Some metadata bodies assert things about text living elsewhere, often through
verbatim quotations: the line a chapter prints, the caption a figure carries,
the wording a guard pins. Nothing checked them, so they rotted silently.

The check exists because the failure is exactly the kind a reading pass is
worst at and a string comparison is perfect at. What it cannot do is judge,
so it never gates: an absent phrase is a defect or a working guard
depending on what the mark is doing with it, and only a reader can say
which.

**Why it is narrow on purpose.** Checking every quoted span flags 185 of
410, which is not a report anybody reads. The misses sort into four kinds
and three are correct:

  1. wording quoted precisely to FORBID it, which is most of them. A
     guard mark pins the variant that must not return, by convention with
     the offending words in capitals, and those phrases are supposed to be
     absent.
  2. quotations of things outside the book -- a person quoted, a paper,
     the author's own conversation -- which live in @source and @ref.
  3. spans carrying an elision, where the quotation is deliberately partial.
  4. real drift.

So only spans introduced by an explicit verbatim cue are considered, from
kinds that assert presence, with the forbidden-variant conventions filtered
out. On a book-length manuscript that cut reported six candidates, of
which two were real: quotations off by a single word, which is the shape a
reading pass misses every time.

**Where it also asks WHICH chapter.** Existing-in-the-book is the weaker
half of the question. A mark says `@ch(a-chapter)` prints a line; the line
exists, so the check passed, while the chapter naming it was wrong -- which
is precisely what a chapter SPLIT produces, because the prose moves and the
quotation does not. Two consecutive rounds had that as their dominant
defect, each time found by hand, each time after the split had run
unchecked for dozens of commits.

So a span whose quotation is attributed to a named chapter is checked
against *that chapter*, and a quotation that resolves in some other chapter
is reported as a misattribution, naming where the words actually live. The
attribution is only trusted when it is unambiguous: an `@ch(slug)` within
ATTR_WINDOW characters of the quotation, or a mark whose body names no
chapter at all and therefore speaks about its own. A body that names
chapters but none near the quotation falls back to the book-wide check,
because guessing the referent is how an advisory earns its way into being
ignored.

**When the narrowing is the wrong trade.** After a term retirement or a
sentence rewrite, the stale quotations are exactly the ones the narrow scan
skips: short (a coinage with its article falls under the twelve-character
floor) or uncued (a note recording a rewrite writes `now "…"`, and "now" is
no cue). `--all` adds every other quoted span in every mark, reported in a
group of its own so the high-precision set stays readable, and `--grep`
narrows that group to the spans a retired term appears in. Most of what it
lists is correct -- guards, outside sources, elisions -- and the author
reads it once, after the edit it was run for.
"""

from __future__ import annotations

from quirework.project import current_project
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from quirework.manuscript import notes, units

# Kinds whose bodies assert that something IS in the book. @source and @ref
# quote the world outside it; @todo is working state and may quote anything,
# including wording being proposed and not yet written.
ASSERTING_KINDS = ("figure", "guidance", "variant", "see")

# The quotation has to be introduced as a quotation. Without a cue the span is
# as likely to be a scare quote, a coined term, or a variant being named.
# `caption:` is the LABEL form and not the bare noun, which is the whole of why
# it is safe. A @figure mark transcribes the plate's caption as `Caption: "…"`,
# and that is a citation; the bare noun appears in sentences ABOUT a caption,
# including the ones recording wording a pass took back out of it, where the
# absence is correct. Requiring the colon caught the live defect three separate
# review passes had to find by hand, and reported nothing else across the book.
_CUE = re.compile(
    r"(?:\b(verbatim|reads?|prints?|printed|says?|in-?prose|quotes?|stated|"
    r"carries|the line|the sentence|the phrase|the wording|word for word)\b"
    r"|\b(caption)\s*:)"
    r"\W{0,4}$",
    re.I,
)

# ... and the negation has to attach to the CUE, not merely share the window
# with it. Scanning the whole window loses real drift: "the earlier chapter
# refuses the dismissal, so this chapter may say the sentence X" is a positive
# citation whose window happens to contain "refuses", and X is off by a word.
_NEGATED = re.compile(
    r"\b(never|not|nobody|none|banned|forbid\w*|retired|refus\w*|avoid\w*|"
    r"may not|does not|do not|stale|wrong|former|replaced|"
    r"instead of|rather than|used to)\b\W*\w*\W*$",
    re.I,
)
NEG_WINDOW = 34

_QUOTED = re.compile(r'"([^"\n]{12,400})"')
# `--all`: any quoted span at all, straight or curly, however short.
_ANY_QUOTED = re.compile(r'"([^"\n]{1,400})"|“([^”\n]{1,400})”')
_ELISION = re.compile(r"\s*(?:\.\.\.|…)\s*")

CUE_WINDOW = 70
MIN_WORDS = 4
# A span mostly in capitals is the house convention for a forbidden variant.
CAPS_RATIO = 0.30

# How far back from the cue verb to look for the `@ch(slug)` that is its
# subject. This is a search window, NOT the adjacency test -- `_SUBJECT_BREAK`
# is that -- so it has to be wide enough to contain the whole token, and a
# descriptive slug readily makes an `@ch(...)` citation thirty characters or
# more. Sizing this as if it were the gap silently blinded the check to
# every long-slug citation, which is most of the ones worth catching.
ATTR_GAP = 96

# What may stand between the cited chapter and the cue verb without displacing
# it as the subject. A possessive noun phrase does NOT displace it -- the
# caption in "@ch(x)'s own caption reads Q" is x's, so Q is being attributed to
# x exactly as in the bare "@ch(x) reads Q". What displaces it is a new subject,
# and the tells for that are a comma, a preposition, or a relativizer:
# "@ch(x)'s own positions, on the page that says Q" cites the page, not x.
_SUBJECT_HOLDS = re.compile(r"(?:'s)?(?:\s+\w+){0,3}\s*\Z")
_SUBJECT_LOST = re.compile(
    r"[,;:]|\b(on|in|at|from|for|to|by|with|of|that|which|who|where|when|"
    r"and|but|so|because|though|while)\b",
    re.I,
)


def _subject_survives(gap: str) -> bool:
    return bool(_SUBJECT_HOLDS.fullmatch(gap)) and not _SUBJECT_LOST.search(gap)


@dataclass
class Hit:
    source: str  # the sidecar the mark lives in
    line: int
    kind: str
    slug: str
    quote: str
    # Chapter-attribution verdict. `target` is the chapter the mark attributes
    # the quotation to (None when the check was book-wide); `found_in` names
    # the chapters the words actually appear in, and is non-empty exactly when
    # this is a misattribution rather than an absence.
    target: str | None = None
    found_in: tuple[str, ...] = ()

    @property
    def misattributed(self) -> bool:
        return bool(self.found_in)


def _prose_corpus(docs: Path) -> str:
    """What a mark may be quoting, MINUS the marks themselves.

    notes._canon_corpus folds the sidecars in, which is right for the
    companion check (an entry may quote a mark) and fatal here: every span
    would match the very mark it was lifted from.

    The illustrations are in, because a @figure mark quotes the plate as
    readily as the caption ("and not all of it" is a label on ch2's dial and
    appears in no chapter), and their text is what the reader sees.
    """
    parts = [f.read_text() for f in units.prose_files(docs)]
    for key in ("manuscript.spec", "manuscript.review_notes"):
        f = notes._configured_book_file(docs, key)
        if f is not None and f.exists():
            parts.append(f.read_text())
    for svg in sorted(docs.rglob("illustrations/*.svg")):
        # element text only; the geometry would match anything by accident
        parts.extend(re.findall(r">([^<>]+)<", svg.read_text()))
    return notes._searchable("\n".join(parts))


def _chapter_corpora(docs: Path) -> tuple[dict[str, str], str]:
    """(slug -> that chapter's searchable prose, everything a quotation may
    carry that belongs to NO chapter).

    The second half is what keeps the attribution check honest. A `@figure`
    mark quotes the plate as readily as the caption, and an SVG's label lives
    in `illustrations/` under a filename that names no chapter; the spec and
    the book-wide manifest are likewise chapter-less. Words found there are
    correct wherever they are cited from, so they are matched before any
    misattribution is declared.
    """
    per: dict[str, str] = {}
    for f in units.prose_files(docs):
        per[units.slug_of(f)] = notes._searchable(f.read_text())
    loose: list[str] = []
    for key in ("manuscript.spec", "manuscript.review_notes"):
        f = notes._configured_book_file(docs, key)
        if f is not None and f.exists():
            loose.append(f.read_text())
    for svg in sorted(docs.rglob("illustrations/*.svg")):
        loose.extend(re.findall(r">([^<>]+)<", svg.read_text()))
    return per, notes._searchable("\n".join(loose))


def _known_slugs(docs: Path, per_chapter: dict[str, str]) -> set[str]:
    """The slugs an `@ch()` may name, narrowed to the book where we can tell.

    `_quarto.yml` is the sole source of what is in the book, so it is the right
    filter -- but it is absent from any tree that is not the live one, and the
    attribution check must not be the reason a synthetic source tree raises. Without
    it every `.qmd` stem is treated as nameable, which is looser and harmless:
    the check compares where words ARE against where a mark SAYS they are, and
    a stem that is not really a chapter fails both halves alike.
    """
    try:
        return units.slugs(docs) & set(per_chapter)
    except (FileNotFoundError, OSError):
        return set(per_chapter)


def _attributed_chapter(
    body: str, cue_start: int, own: str | None, known: set[str]
) -> str | None:
    """Which chapter this quotation is being attributed to, or None if there
    is no chapter to check it against.

    The attribution is grammatical, not positional: `_CUE` has already found
    the verb introducing the quotation, so the chapter being cited is the one
    that is the SUBJECT of that verb -- an `@ch(slug)` sitting immediately
    before it, separated by nothing but a possessive or whitespace.

    Nearness alone is not a usable proxy, and the four candidates the first
    cut produced were all it getting this wrong. Two shapes defeat it. The
    citation may read "@ch(x) says Q" with an unrelated "@ch(y)" a few words
    the other side of Q, so the nearest chapter is the one NOT doing the
    saying. And the subject may be something else entirely -- "@ch(x)'s own
    positions, on the page that says Q" cites *this* mark's page, not x, and
    the intervening "the page" is the whole signal.

    So: the adjacent chapter if there is one, otherwise the chapter the mark
    annotates, which is what a mark is talking about when it names nobody.
    """
    gap_start = max(0, cue_start - ATTR_GAP)
    for m in reversed(list(notes._CH_REF.finditer(body, gap_start, cue_start))):
        if not _subject_survives(body[m.end() : cue_start]):
            continue
        slug = m.group(1).strip()
        return slug if slug in known else None
    # Naming no chapter, a mark speaks about the one it annotates.
    return own if own in known else None


def _longest_fragment(span: str) -> str:
    """An elided quotation cannot match whole; measure its longest piece."""
    return max(_ELISION.split(span), key=len)


def _is_forbidden_variant(span: str) -> bool:
    letters = [c for c in span if c.isalpha()]
    if not letters:
        return True
    return sum(c.isupper() for c in letters) / len(letters) > CAPS_RATIO


def scan(docs: Path | None = None) -> list[Hit]:
    docs = (
        Path(docs)
        if docs is not None
        else notes.current_project().path("manuscript.source")
    )
    corpus = _prose_corpus(docs)
    per_chapter, loose = _chapter_corpora(docs)
    known = _known_slugs(docs, per_chapter)
    hits: list[Hit] = []
    for anno in notes.scan():
        if anno.kind not in ASSERTING_KINDS:
            continue
        own = Path(anno.file).stem
        body = notes._norm(anno.body)
        for m in _QUOTED.finditer(body):
            span = m.group(1)
            base = max(0, m.start() - CUE_WINDOW)
            before = body[base : m.start()]
            cue = _CUE.search(before)
            if not cue:
                continue
            # Bare past-tense "read" is how this corpus cites wording it has
            # RETIRED ("It read X and was fixed"; "Its sub-label read X, which
            # sits crosswise to ..."), and that wording is supposed to be gone.
            # A live citation uses the present tense, "reads". Every standing
            # false positive the first cut produced was this construction.
            # group(1) is None on the `Caption:` branch, which has no tense to
            # read and is never the retired-wording form.
            if (cue.group(1) or "").lower() == "read":
                continue
            # the negation must sit just before the cue verb ("never reads X"),
            # not anywhere in the run-up to it
            if _NEGATED.search(before[max(0, cue.start() - NEG_WINDOW) : cue.end()]):
                continue
            if _is_forbidden_variant(span):
                continue
            fragment = _longest_fragment(span)
            if len(fragment.split()) < MIN_WORDS:
                continue
            needle = notes._searchable(fragment)
            present = needle in corpus
            target = _attributed_chapter(body, base + cue.start(), own, known)
            mark = Hit(
                anno.source or anno.file,
                anno.line,
                anno.kind,
                anno.args[0] if anno.args else "",
                span,
            )
            if present:
                # The words exist. The remaining question is whether they exist
                # where the mark says they do -- unanswerable without a target,
                # and not asked of a quotation that belongs to no chapter.
                if target is None or needle in loose:
                    continue
                if needle in per_chapter.get(target, ""):
                    continue
                elsewhere = tuple(
                    s for s in sorted(per_chapter) if needle in per_chapter[s]
                )
                if not elsewhere:
                    continue  # lives in some other non-chapter corner; not ours to judge
                hits.append(replace(mark, target=target, found_in=elsewhere))
            else:
                hits.append(replace(mark, target=target))
    return hits


def _present(needle: str, corpus: str) -> bool:
    """Whether the words stand in the prose as words, not inside longer ones.

    A retired "fuse" is not still present because the book says "fuses".
    """
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", corpus) is not None


def scan_all(docs: Path | None = None, grep: str | None = None, skip=()) -> list[Hit]:
    """Every quoted span in every mark that the prose no longer contains.

    No cue, no length floor, no filter for guards or outside sources: this is
    the coverage the default scan gives up for precision. `grep` is a
    case-insensitive regular expression a span must match, which is how a
    term retirement checks every quotation that carried the term. Spans the
    default scan already reported (`skip`) are left to that report.
    """
    docs = (
        Path(docs)
        if docs is not None
        else notes.current_project().path("manuscript.source")
    )
    corpus = _prose_corpus(docs)
    pattern = re.compile(grep, re.I) if grep else None
    reported = {(h.source, h.line, h.quote) for h in skip}
    hits: list[Hit] = []
    for anno in notes.scan():
        body = notes._norm(anno.body)
        source = anno.source or anno.file
        for m in _ANY_QUOTED.finditer(body):
            span = m.group(1) or m.group(2)
            if pattern and not pattern.search(span):
                continue
            if (source, anno.line, span) in reported:
                continue
            needle = notes._searchable(_longest_fragment(span))
            if not needle or _present(needle, corpus):
                continue
            slug = anno.args[0] if anno.args else ""
            hits.append(Hit(source, anno.line, anno.kind, slug, span))
    return hits


def _group(hits: list[Hit], stream, header: str, tail: str) -> None:
    print(
        f"\nadvisory ({header} — warning, not gated):",
        file=stream,
    )
    by_file: dict[str, list[Hit]] = {}
    for h in hits:
        by_file.setdefault(h.source, []).append(h)
    for src, items in by_file.items():
        print(f"\n  {src}", file=stream)
        for h in items:
            tag = f"@{h.kind}" + (f"({h.slug})" if h.slug else "")
            print(f"    L{h.line:<4} {tag}", file=stream)
            print(
                f'          "{h.quote[:110]}{"…" if len(h.quote) > 110 else ""}"',
                file=stream,
            )
            if h.misattributed:
                print(
                    f"          cited as @ch({h.target}) — but the words are in "
                    f"{', '.join('@ch(%s)' % s for s in h.found_in)}",
                    file=stream,
                )
    print(tail, file=stream)


def report(hits: list[Hit], stream=None) -> None:
    stream = stream or sys.stderr
    if not hits:
        print(
            "✓ quotes: every cited phrase resolves, in the chapter that cites it.",
            file=stream,
        )
        return
    wrong = [h for h in hits if h.misattributed]
    absent = [h for h in hits if not h.misattributed]
    if wrong:
        _group(
            wrong,
            stream,
            f"{len(wrong)} quotation(s) attributed to the wrong chapter",
            "\n  The words are in the book, but not where the mark says. This is\n"
            "  what a chapter split leaves behind: the prose moved and the\n"
            "  citation did not. Repoint the mark, or fix the chapter it names.",
        )
    if absent:
        _group(
            absent,
            stream,
            f"{len(absent)} cited phrase(s) not found in the book",
            "\n  Each is a quotation the prose no longer matches, a phrase the\n"
            "  mark is pinning as forbidden, or a quotation of something outside\n"
            "  the book. Only the first is a defect. The author rules.",
        )


def report_all(hits: list[Hit], grep: str | None, stream=None) -> None:
    stream = stream or sys.stderr
    scope = f" matching {grep!r}" if grep else ""
    if not hits:
        print(
            f"✓ quotes --all: every other quoted phrase{scope} is in the book.",
            file=stream,
        )
        return
    _group(
        hits,
        stream,
        f"{len(hits)} other quoted phrase(s){scope} not found in the book",
        "\n  Every quoted span, with no filter for guards, outside sources or\n"
        "  short coinages, so most of these are correct. The ones to fix are\n"
        "  quotations of wording the prose has since changed.",
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="quirework spec quotes",
        description="Check that phrases the marks quote still exist in the book.",
    )
    p.add_argument(
        "--source",
        type=Path,
        default=None,
        help="book source root (default: configured)",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="also check every other quoted span, however short or uncued, "
        "in a separate group",
    )
    p.add_argument(
        "--grep",
        metavar="PATTERN",
        default=None,
        help="with --all (implied), only spans matching this case-insensitive "
        "regular expression, such as a retired term",
    )
    args = p.parse_args(argv)
    if args.grep is not None:
        try:
            re.compile(args.grep)
        except re.error as exc:
            p.error(f"--grep: {exc}")
    hits = scan(args.source)
    report(hits)
    if args.all or args.grep is not None:
        report_all(scan_all(args.source, args.grep, hits), args.grep)
    return 0
