"""Reader-facing terminology advisory (`quirework text check`).

The selected project supplies the terms, allowed contexts, source globs, and
excluded filenames. The check reports candidates with enough context for an
author to decide whether a term needs a gloss. It warns and does not gate.

Three suppressions keep the signal honest, and all three are mechanical:

  1. code spans -- `#kettle-signal` is a slug citation, not a
     use of the word.
  2. quoted material -- a paper title or a
     verbatim citation of the metadata layer. Quoting a register that
     licenses a word is not adopting it.
  3. an adjacent gloss -- the term introduced and explained on the spot,
     which is option two of the rule and already compliant.

Matching runs per paragraph over whitespace-normalized text, not per
line, because a term can straddle a hard line break.

Retired phrases are the other half, and they gate. A term here is a
candidate for judgment; a phrase the project has retired is a judgment
already made, so none of the suppressions above apply to it. A gloss does
not excuse it, a quotation still puts it in front of the reader, and a
heading or table is as reader-facing as a paragraph. Every occurrence is
reported, not the first, and only a settled keep (`allow`) excuses one.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from quirework.project import current_project

# Terms that are essentially never right *bare* in reader-facing prose.
# Each maps to the plain alternative the book already owns, which is what
# makes the warning actionable rather than a scold. Keep this list to
# terms with no ordinary-English life: a word that is also everyday
# ("charge", "content", "extension", "patient" on its own) produces
# homograph noise, and a warning nobody trusts is worse than no warning.


# Settled keeps the mechanical suppressions cannot see. Each entry is
# (path-suffix, substring-that-must-appear-in-the-window, reason).
# Matched against a window of the normalized paragraph around the hit
# (so a hard-wrapped phrase still matches), not the whole paragraph: a
# paragraph-wide suppression would also hide a NEW bare term that lands
# in the same paragraph as a settled keep.


# Option two of the rule is "introduced-and-glossed on first use", so the
# question a hit has to answer is whether the reader is told what the word
# means where they meet it. The patterns recognize immediate, surrounding, and
# preceding explanations.
#
# The windowed tier is deliberately permissive. This is a warning, and a
# warning that cries wolf is one nobody reads; a missed bare term costs a
# later sweep, a false positive costs the check's credibility.
GLOSS_IMMEDIATE = re.compile(
    r"^[a-z]*(?:"
    r"\s*[,(:]\s*(?:the\s+)?(?:view|idea|claim|position|doctrine|thesis|"
    r"notion|puzzle|question|being|which|that|many|no\b|a\b|an\b|whether)"
    r"|\s*,?\s*being\b"
    r"|\s*,\s*the\s+\w+\s+that\b"
    r")",
    re.I,
)

GLOSS_WINDOWED = re.compile(
    r"(?:the\s+(?:view|idea|claim|position|doctrine|thesis|notion)\s+(?:that|is)"
    r"|view\s+is\s+that|argues\s+that|holds\s+that|means\s+that"
    r"|grounds?\s+\w+\s+in|is\s+the\s+view|:\s*[a-z])",
    re.I,
)

# A sentence beginning "For the specialist, ..." can define a position. Both
# the opener and the comma that starts the explanation are required.
GLOSS_FOR_THE_BEFORE = re.compile(r"(?:^|(?<=[.;!?])\s)(?:For|To)\s+(?:the|a|an)\s*$")
GLOSS_FOR_THE_AFTER = re.compile(r"^[a-z]*\s*,")

# A gloss that precedes the term: "what the field calls X", "known as X".
GLOSS_BEFORE = re.compile(
    r"(?:what\s+(?:the\s+)?[a-z-]+\s+calls|[a-z-]+\s+calls|known\s+as|"
    r"called|call(?:s|ed)?\s+it|the\s+name\s+for)\s*$",
    re.I,
)

# A following academic citation marks a work being named, not a term being
# used: "A Field Guide (Rivera, 2025)".
CITATION_AFTER = re.compile(
    r"^[a-z*]*\s*\(\s*[A-Z][\w.'-]+(?:\s*(?:and|&|,)\s*[A-Z][\w.'-]+)*,?\s*\d{4}",
)


def _options():
    """Read policy from the selected project for this operation."""
    from types import SimpleNamespace

    TERMS: dict[str, str] = (
        current_project().data("text.terminology", {}).get("terms", {})
    )
    ALLOW: list[tuple[str, str, str]] = (
        current_project().data("text.terminology", {}).get("allow", [])
    )
    SCOPE_GLOB = tuple(
        current_project().data("text.terminology", {}).get("scope_glob", ())
    )
    EXCLUDE_NAMES = set(
        current_project().data("text.terminology", {}).get("exclude_names", set())
    )
    RETIRED: dict[str, str] = (
        current_project().data("text.terminology", {}).get("retired", {})
    )
    return SimpleNamespace(
        ALLOW=ALLOW,
        EXCLUDE_NAMES=EXCLUDE_NAMES,
        RETIRED=RETIRED,
        SCOPE_GLOB=SCOPE_GLOB,
        TERMS=TERMS,
    )


class Hit:
    __slots__ = ("path", "line", "term", "window", "hint")

    def __init__(self, path: str, line: int, term: str, window: str, hint: str):
        self.path = path
        self.line = line
        self.term = term
        self.window = window
        self.hint = hint


def _paragraphs(text: str, *, visible: bool = False) -> list[tuple[str, list[int]]]:
    """Split into paragraphs of whitespace-normalized text.

    Returns (normalized_text, line_of_each_char). Blocks that are never
    reader-facing prose (HTML comments, fenced code, tables, headings)
    are dropped whole: a banner comment or a table row is not prose the
    reader meets as an argument. With `visible`, headings and table rows
    are kept, each as a paragraph of its own: the reader does not meet
    them as argument, but does read them.
    """
    out: list[tuple[str, list[int]]] = []
    buf: list[tuple[str, int]] = []
    in_fence = ""  # the marker that opened the current fence, or ""
    in_comment = False

    def flush() -> None:
        if not buf:
            return
        chars: list[str] = []
        lines: list[int] = []
        for i, (ln, no) in enumerate(buf):
            if i:
                chars.append(" ")
                lines.append(no)
            for ch in ln:
                chars.append(ch)
                lines.append(no)
        out.append(("".join(chars), lines))
        buf.clear()

    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        stripped = line.strip()

        if in_fence:
            if stripped.startswith(in_fence):
                in_fence = ""
            continue
        if stripped.startswith(
            ("```", "~~~")
        ):  # both fence styles close on their own marker
            in_fence = stripped[:3]
            flush()
            continue

        # HTML comments: the stamp banners and any stray mark. Never prose.
        if not in_comment and "<!--" in stripped:
            in_comment = "-->" not in stripped.split("<!--", 1)[1]
            flush()
            continue
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue

        if not stripped:
            flush()
            continue
        # Tables and headings are not argued prose.
        if stripped.startswith(("|", "#")):
            flush()
            if visible:
                buf.append((stripped, no))
                flush()
            continue

        buf.append((stripped, no))

    flush()
    return out


def _mask_spans(text: str) -> list[tuple[int, int]]:
    """Spans where a term is cited rather than used: code and quotes."""
    spans: list[tuple[int, int]] = []
    for m in re.finditer(r"`[^`]*`", text):
        spans.append(m.span())
    # Straight and curly double quotes. Capped so an unbalanced quote
    # cannot swallow a paragraph and silence everything after it.
    for m in re.finditer(r"[\"“][^\"“”]{0,400}?[\"”]", text):
        spans.append(m.span())
    # Emphasis is deliberately NOT masked. Italics and bold mark work
    # titles here, but they also mark ordinary stress ("the *other* way"), so
    # masking them would silence a bare
    # term for the wrong reason. A named work is recognized instead by the
    # citation that follows it (CITATION_AFTER), which is the actual
    # signal.
    return spans


def _in_span(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


def _is_glossed(para: str, start: int, end: int) -> bool:
    after = para[end : end + 120]
    before = para[max(0, start - 40) : start]
    if GLOSS_FOR_THE_BEFORE.search(before) and GLOSS_FOR_THE_AFTER.match(after):
        return True
    return bool(
        GLOSS_IMMEDIATE.match(after)
        or GLOSS_WINDOWED.search(after)
        or CITATION_AFTER.match(after)
        or GLOSS_BEFORE.search(before)
    )


def scan_text(text: str, path: str) -> list[Hit]:
    """Report the first bare use of each term, per file.

    The rule is "introduced-and-glossed *on first use*", so a term the
    file explains anywhere is a term the reader has been given, and
    later bare uses of it are correct rather than sloppy. Reporting every
    occurrence would turn one editorial decision
    into four identical warnings.
    """
    paras = _paragraphs(text)
    allowed = [frag for p, frag, _ in _options().ALLOW if path.endswith(p)]

    # occurrences per term, in document order
    seen: dict[str, list[tuple[int, str, bool]]] = {}
    for para, lines in paras:
        spans = _mask_spans(para)
        for pattern in _options().TERMS:
            for m in re.finditer(rf"\b{pattern}", para, re.I):
                if _in_span(m.start(), spans):
                    continue
                # A keep suppresses hits near its fragment, not the whole
                # paragraph (see the ALLOW note above).
                if any(
                    frag in para[max(0, m.start() - 250) : m.end() + 250]
                    for frag in allowed
                ):
                    continue
                lo = max(0, m.start() - 55)
                hi = min(len(para), m.end() + 55)
                seen.setdefault(pattern, []).append(
                    (
                        lines[m.start()],
                        para[lo:hi],
                        _is_glossed(para, m.start(), m.end()),
                    )
                )

    hits: list[Hit] = []
    for pattern, occurrences in seen.items():
        if any(glossed for _, _, glossed in occurrences):
            continue
        line, window, _ = occurrences[0]
        term = re.search(rf"\b{pattern}", window, re.I)
        hits.append(
            Hit(
                path,
                line,
                term.group(0) if term else pattern,
                window,
                _options().TERMS[pattern],
            )
        )
    return sorted(hits, key=lambda h: h.line)


def _phrase(phrase: str) -> re.Pattern:
    """A retired phrase as written, in any case, across any line break."""
    body = r"\s+".join(re.escape(w) for w in phrase.split())
    # The word guards keep "al" out of "real". They belong only at an end that
    # is a word character: a retired mark of punctuation is written touching
    # the words on both sides ("the lamp—the light"), and a guard there would
    # make the ruling unmatchable in the one place it is ever broken.
    head = r"(?<!\w)" if re.match(r"\w", phrase.strip()) else ""
    tail = r"(?!\w)" if re.search(r"\w\Z", phrase.strip()) else ""
    return re.compile(head + body + tail, re.I)


def scan_retired(text: str, path: str) -> list[Hit]:
    """Every occurrence of a retired phrase that no settled keep excuses."""
    allowed = [frag for p, frag, _ in _options().ALLOW if path.endswith(p)]
    hits: list[Hit] = []
    for para, lines in _paragraphs(text, visible=True):
        # A code span cites a name; it is not the phrase being used.
        code = [m.span() for m in re.finditer(r"`[^`]*`", para)]
        for phrase, instead in _options().RETIRED.items():
            for m in _phrase(phrase).finditer(para):
                if _in_span(m.start(), code) or any(
                    frag in para[max(0, m.start() - 250) : m.end() + 250]
                    for frag in allowed
                ):
                    continue
                lo = max(0, m.start() - 55)
                hi = min(len(para), m.end() + 55)
                hits.append(
                    Hit(path, lines[m.start()], m.group(0), para[lo:hi], instead)
                )
    return sorted(hits, key=lambda h: h.line)


def _targets(root: Path) -> list[Path]:
    found: list[Path] = []
    for sub, glob in _options().SCOPE_GLOB:
        d = root / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob(glob)):
            if p.name in _options().EXCLUDE_NAMES or p.name.startswith("_"):
                continue
            found.append(p)
    return found


def _expand(paths: list[str]) -> tuple[list[Path], list[str]]:
    """Explicit CLI paths resolved to scannable files: (files, missing).

    A directory expands to its `.md`/`.qmd` files, minus the excluded names
    and anything in an underscore directory (`_marks/`, the metadata layer) —
    the same shape as the default scope. A named file is scanned as given.
    A path that exists as neither is returned in `missing`: silently
    skipping it would make `jargon manuscript` print a clean pass over
    nothing, which is the worst answer a check can give.
    """
    files: list[Path] = []
    missing: list[str] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files += [
                f
                for g in ("*.qmd", "*.md")
                for f in sorted(p.rglob(g))
                if f.name not in _options().EXCLUDE_NAMES
                and not f.name.startswith("_")
                and not any(part.startswith("_") for part in f.relative_to(p).parts)
            ]
        elif p.is_file():
            files.append(p)
        else:
            missing.append(raw)
    return list(dict.fromkeys(files)), missing


def _files(root: Path, paths: list[str] | None) -> list[tuple[Path, str]]:
    if paths:
        files, missing = _expand(paths)
        if missing:
            raise FileNotFoundError(", ".join(missing))
    else:
        files = _targets(root)
    found = []
    for p in files:
        try:
            rel = str(p.resolve().relative_to(root))
        except ValueError:
            rel = str(p)
        found.append((p, rel))
    return found


def scan(root: Path | None = None, paths: list[str] | None = None) -> list[Hit]:
    root = root or (lambda: current_project().root)()
    hits: list[Hit] = []
    for p, rel in _files(root, paths):
        hits.extend(scan_text(p.read_text(encoding="utf-8"), rel))
    return hits


def retired(root: Path | None = None, paths: list[str] | None = None) -> list[Hit]:
    root = root or (lambda: current_project().root)()
    hits: list[Hit] = []
    for p, rel in _files(root, paths):
        hits.extend(scan_retired(p.read_text(encoding="utf-8"), rel))
    return hits


def report(hits: list[Hit], stream=sys.stderr) -> None:
    by_file: dict[str, list[Hit]] = {}
    for h in hits:
        by_file.setdefault(h.path, []).append(h)
    print(
        f"\nadvisory ({len(hits)} bare field term(s) in reader-facing prose; "
        "warning, not gated):",
        file=stream,
    )
    for path, items in by_file.items():
        print(f"\n  {path}", file=stream)
        for h in items:
            print(f"    L{h.line:<4} {h.term}", file=stream)
            print(f"          ...{h.window}...", file=stream)
            print(f"          -> {h.hint}", file=stream)
    print(
        "\n  Each term needs the treatment declared by project policy. "
        "The author rules; do not batch-replace.",
        file=stream,
    )


def report_retired(hits: list[Hit], stream=None) -> None:
    stream = stream or sys.stderr
    by_file: dict[str, list[Hit]] = {}
    for h in hits:
        by_file.setdefault(h.path, []).append(h)
    print(
        f"\nretired ({len(hits)} retired phrase(s) back in reader-facing prose; "
        "gated):",
        file=stream,
    )
    for path, items in by_file.items():
        print(f"\n  {path}", file=stream)
        for h in items:
            print(f"    L{h.line:<4} {h.term}", file=stream)
            print(f"          ...{h.window}...", file=stream)
            print(f"          -> {h.hint}", file=stream)
    print(
        "\n  The project retired these. Replace each one, or record a settled "
        "keep in `allow` for a use that has to stay.",
        file=stream,
    )


def main(argv: list[str] | None = None) -> int:
    # argparse owns the help (description + option help below), so the text
    # can never drift from the real interface the way a hand-rolled USAGE
    # string can — and an unknown flag is an error instead of a silent skip.
    ap = argparse.ArgumentParser(
        prog="quirework text check",
        description="Report bare field terms in the reader-facing prose the "
        "project's terminology policy selects. Every term must be avoided, "
        "introduced and glossed on first use, or confined to the files the "
        "policy excludes, which is where a book sends its field vocabulary "
        "on purpose.",
        epilog="This WARNS and never gates. The author is the instrument for "
        'this test and an agent\'s sense of what is "standard" does '
        "not count, so the check produces candidates to rule on, not "
        "verdicts.",
    )
    ap.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="file or directory to scan (default: the whole reader-facing scope)",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if any bare term is found (the "
        "pre-commit hook runs this and prints the result "
        "without blocking); a retired phrase fails the run "
        "with or without it",
    )
    ap.add_argument(
        "--list",
        dest="list_",
        action="store_true",
        help="print the term list, the plain alternatives, and the settled keeps",
    )
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if args.list_:
        print(
            f"jargon: {len(_options().TERMS)} term pattern(s); "
            f"{len(_options().ALLOW)} documented keep(s)\n"
        )
        for pattern, hint in _options().TERMS.items():
            print(f"  {pattern:<40} {hint}")
        if _options().RETIRED:
            print("\nretired (gated):")
            for phrase, instead in _options().RETIRED.items():
                print(f"  {phrase:<40} {instead}")
        if _options().ALLOW:
            print("\nallowed (settled keeps):")
            for path, frag, why in _options().ALLOW:
                print(f'  {path}\n    "{frag}"\n    {why}')
        return 0

    try:
        hits = scan(paths=args.paths or None)
        back = retired(paths=args.paths or None)
    except FileNotFoundError as e:
        print(f"jargon: no such file or directory: {e}", file=sys.stderr)
        return 2
    if back:
        report_retired(back)
    elif _options().RETIRED:
        print("✓ retired: no retired phrase in reader-facing prose.")
    if not hits:
        print("✓ jargon: no bare field terms in reader-facing prose.")
    else:
        report(hits)
    # A retired phrase is a ruling already made, so it fails the run on its
    # own. Bare terms are candidates for one, and fail it only when asked.
    return 1 if back or (hits and args.check) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
