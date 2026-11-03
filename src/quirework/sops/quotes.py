"""Quotations in a review report, looked for in the sources they cite.

A reviewer is told to quote both sides of every finding as they actually
appear, and a model can still put a sentence in quotation marks that no
file holds, inside a finding whose substance is sound. An operator who
takes the quotation on trust then verifies the wrong text, and one who
does not has to script the check by hand for every report. So when a turn
is banked, every quotation the report carries is looked for in the
sources its own paragraph cites, and the ones that are not there are
listed beside the receipt checks.

What comes out is a list of leads, never a verdict. A paraphrase in
quotation marks, a quotation of text the range removed, and a correct
quotation under the wrong citation all look the same to a script, and the
operator tells them apart by reading the file. The list says which
quotations to read first; it does not say a finding fails.

A quotation is either a blockquote, whose sources are what the nearest
paragraph above it cites, or a quoted span of at least `MIN_WORDS` words
inside a paragraph, whose sources are everything that paragraph cites, or
those of the paragraph above when it cites nothing and ends in a colon,
introducing it. A source is a file path
(`docs/x.md`, `docs/x.md:12-14`, an absolute path inside the checkout, or
a bare file name that only one tracked file carries), a bare `:12`, which
means the last file the report named, or a backticked commit id, whose
message and diff are searched.

The comparison is looser than `spec quotes`, which checks the work's own
quotations of itself, because a report reformats what it quotes: it
backticks a section anchor the source leaves plain, swaps a quotation's
inner double marks for single ones, and appends `(#anchor)` to say where
a sentence came from. So both sides are compared with whitespace
collapsed, case folded, quotation marks, apostrophes, backticks, emphasis
markers and underscores dropped, and dashes made one mark. An ellipsis,
a bracketed insertion, a ` / ` line break, a parenthesis of anchors and a
blockquote's paragraph break split a quotation into pieces, and each
piece must be found in one of the sources the quotation cites.
"""

from __future__ import annotations

import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# A quoted span shorter than this is a term or a title, not a quotation, and
# finding it anywhere proves nothing. Five words is the minimum `spec quotes`
# keeps for the same reason.
MIN_WORDS = 5

_FENCE = re.compile(r"^\s*(```|~~~)")
_BLOCK = re.compile(r"^\s*>\s?(.*)$")
_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_HEADING = re.compile(r"^\s*#{1,6}\s")
# A path with an extension, optionally carrying a line locator. Whatever it
# matches counts only if it names a file, so "e.g." costs nothing.
_PATH = re.compile(
    r"(?<![\w/.:@-])"
    r"((?:/|\.{1,2}/)?(?:[\w@+-][\w.@+-]*/)*[\w@+-][\w.@+-]*\.[A-Za-z][A-Za-z0-9]{0,7})"
    r"(?::\d+(?:[-\u2013,]\d+)*)?"
)
# `:608`, a line in the file the report named last.
_BARE_LINE = re.compile(r"`:\d+(?:[-\u2013,]\d+)*`")
_DOMAIN = re.compile(r"^[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}$")
# A commit id is a source only when backticked: prose has hex-looking words.
_SHA = re.compile(r"`([0-9a-f]{7,40})`")
_CURLY = re.compile("\u201c([^\u201c\u201d]+)\u201d")
_STRAIGHT = re.compile(r'"([^"]+)"')
# An ellipsis, bracketed or bare, and the ` / ` that marks a line break.
_ELISION = re.compile(r"\[\s*(?:\.\.\.|\u2026)\s*\]|\.\.\.|\u2026|\s/\s")
_INSERTION = re.compile(r"\[[^\]]*\](?!\()")
# A parenthesis holding nothing but section anchors, `(#a, #b)`. Reviewers
# append one inside a quotation to say where it came from, and the source may
# carry its own in the same place, so it splits a quotation rather than being
# part of it.
_ANCHORS = re.compile(r"\(\s*`?#[\w-]+`?(?:\s*[,;]\s*`?#[\w-]+`?)*\s*\)")
_PLAIN = str.maketrans(
    {
        # Quotation marks and apostrophes are dropped: a reviewer quoting a
        # passage that holds a quotation swaps its double marks for single
        # ones or leaves them out, and a curly apostrophe turns straight.
        '"': None,
        "'": None,
        "\u201c": None,
        "\u201d": None,
        "\u201e": None,
        "\u201f": None,
        "\u2033": None,
        "\u2018": None,
        "\u2019": None,
        "\u201a": None,
        "\u201b": None,
        "\u2032": None,
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00a0": " ",
        "`": None,
        "*": None,
        "_": None,
    }
)


def normalize(text):
    """The form both sides are compared in."""
    plain = unicodedata.normalize("NFC", text).translate(_PLAIN)
    plain = re.sub(r"\s*-+\s*", "-", plain)
    return " ".join(plain.split()).casefold().strip(" .,;:!?()")


def pieces(text):
    """The parts of a quotation that must each appear, split at elisions."""
    out = []
    for part in _ELISION.split(_ANCHORS.sub("\u2026", text)):
        for piece in _INSERTION.split(part):
            piece = normalize(piece)
            if piece:
                out.append(piece)
    return out


@dataclass
class Quote:
    line: int
    kind: str
    text: str
    cited: list = field(default_factory=list)


@dataclass
class _Unit:
    kind: str
    lines: list = field(default_factory=list)

    @property
    def text(self):
        return " ".join(text for _, text in self.lines)


def _units(body):
    """Blockquote runs and prose blocks, each with its report line numbers.

    A blank line, a heading or a new list item starts a new block, and a
    fenced code block belongs to none, since what sits in one is code.
    """
    units, current, fenced = [], None, False
    for number, raw in enumerate(body.splitlines(), 1):
        if _FENCE.match(raw):
            fenced, current = not fenced, None
            continue
        if fenced:
            continue
        block = _BLOCK.match(raw)
        if block:
            if current is None or current.kind != "block":
                current = _Unit("block")
                units.append(current)
            current.lines.append((number, block.group(1)))
            continue
        if not raw.strip():
            current = None
            continue
        if (
            current is None
            or current.kind != "prose"
            or _ITEM.match(raw)
            or _HEADING.match(raw)
        ):
            current = _Unit("prose")
            units.append(current)
        current.lines.append((number, raw))
    return units


def _relative(token, root):
    path = Path(token)
    if path.is_absolute():
        try:
            path = path.relative_to(root)
        except ValueError:
            return None
    path = Path(os.path.normpath(path))
    if not path.parts or path.parts[0] == "..":
        return None
    return path.as_posix()


def _citations(text, root, last, resolve):
    """The sources one block names, in order, and the last file it named."""
    found = []
    events = []
    for match in _PATH.finditer(text):
        rel = _relative(match.group(1), root)
        rel = resolve(rel) if rel else None
        if rel:
            events.append((match.start(), "file", rel))
    for match in _BARE_LINE.finditer(text):
        events.append((match.start(), "line", None))
    for match in _SHA.finditer(text):
        events.append((match.start(), "commit", match.group(1)))
    for _, kind, value in sorted(events, key=lambda e: e[0]):
        if kind == "file":
            last = value
            source = value
        elif kind == "line":
            source = last
        else:
            source = f"commit:{value}"
        if source and source not in found:
            found.append(source)
    return found, last


def _inline(unit):
    """Quoted spans long enough to be quotations, with the line each opens on.

    The block is searched as one text, so a quotation a hard-wrapped report
    breaks across lines is still one quotation.
    """
    text, starts = "", []
    for number, raw in unit.lines:
        starts.append((len(text), number))
        text += raw.replace("`", "") + " "
    spans = []
    for pattern in (_CURLY, _STRAIGHT):
        for match in pattern.finditer(text):
            quoted = " ".join(match.group(1).split())
            if len(quoted.split()) >= MIN_WORDS:
                number = max(n for offset, n in starts if offset <= match.start())
                spans.append((match.start(), number, quoted))
    return [(number, quoted) for _, number, quoted in sorted(spans)]


def extract(body, root, resolve=None):
    """Every quotation in a report, with the sources its block cites.

    ``resolve`` turns a cited path into the file it names, or None when it
    names nothing; without one, a path is taken as written.
    """
    root = Path(root)
    resolve = resolve or (lambda rel: rel)
    quotes, last = [], None
    cited_by = []
    units = _units(body)
    for unit in units:
        if unit.kind == "prose":
            cited, last = _citations(unit.text, root, last, resolve)
        else:
            cited = []
        cited_by.append(cited)
    for index, unit in enumerate(units):
        if unit.kind == "block":
            sources = next(
                (
                    cited_by[back]
                    for back in range(index - 1, max(-1, index - 4), -1)
                    if units[back].kind == "prose" and cited_by[back]
                ),
                [],
            )
            # A `>` line with nothing on it parts two paragraphs, which a
            # reviewer often quotes from different places in one file.
            text = " ".join(t if t.strip() else "\u2026" for _, t in unit.lines)
            if len(text.split()) >= MIN_WORDS:
                quotes.append(Quote(unit.lines[0][0], "block", text, list(sources)))
            continue
        # A paragraph that cites nothing borrows the sources of one that
        # introduces it, as "Evidence, `x.md:12`:" introduces a quotation.
        sources = cited_by[index] or (
            cited_by[index - 1]
            if index
            and units[index - 1].kind == "prose"
            and units[index - 1].text.rstrip().endswith(":")
            else []
        )
        for number, quoted in _inline(unit):
            quotes.append(Quote(number, "inline", quoted, list(sources)))
    return quotes


class Sources:
    """The text a citation names, read once and kept normalized.

    ``at`` reads files from that revision instead of the working tree;
    ``base`` is where a quotation that is not in the file now is looked for
    next, so a quotation of text the range changed reads as that rather than
    as missing.
    """

    def __init__(self, root, *, at=None, base=None):
        self.root, self.at, self.base = Path(root), at, base
        self._cache = {}
        self._names = None

    def resolve(self, rel):
        """The file a cited path names, or None when it names nothing.

        A path that exists, now or before the delta, is itself. A bare file
        name, as a reviewer writes `_notes.md` for a file two directories
        down, is the one tracked file of that name. A path with a directory
        that names no file stays as written, since a broken citation is a
        lead; a bare word that names none, such as "e.g", is dropped.
        """
        if self.text(rel) is not None or self.text(rel, base=True) is not None:
            return rel
        if "/" in rel:
            # `example.org/paper.pdf` is a web address written without its
            # scheme, and nothing in the checkout can hold its text.
            return None if _DOMAIN.match(rel.split("/", 1)[0]) else rel
        if self._names is None:
            listed = (
                self._git("ls-tree", "-r", "--name-only", self.at)
                if self.at
                else self._git("ls-files", "--cached", "--others", "--exclude-standard")
            )
            self._names = {}
            for path in (listed or "").splitlines():
                self._names.setdefault(path.rsplit("/", 1)[-1], []).append(path)
        hits = self._names.get(rel, [])
        return hits[0] if len(hits) == 1 else None

    def _git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        proc = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            errors="replace",
            env=env,
            check=False,
        )
        return proc.stdout if proc.returncode == 0 else None

    def _read(self, source, rev):
        if source.startswith("commit:"):
            sha = source.split(":", 1)[1]
            shown = self._git("show", "--format=%B", "--no-color", sha)
            if shown is None:
                return None
            lines = []
            for line in shown.splitlines():
                if line.startswith(("+++", "---", "@@", "diff --git", "index ")):
                    continue
                lines.append(line[1:] if line[:1] in "+- " else line)
            return "\n".join(lines)
        if rev is not None:
            return self._git("show", f"{rev}:{source}")
        path = self.root / source
        if not path.is_file():
            return None
        return path.read_text(errors="replace")

    def text(self, source, *, base=False):
        """The normalized text of a source, or None where it names nothing.

        A commit is the same text at every revision, so it has no state
        before the delta to fall back on.
        """
        commit = source.startswith("commit:")
        if base and (commit or self.base is None):
            return None
        rev = None if commit else (self.base if base else self.at)
        key = (source, rev)
        if key not in self._cache:
            raw = self._read(source, rev)
            self._cache[key] = None if raw is None else normalize(raw)
        return self._cache[key]


def _shown(quote, limit=160):
    flat = " ".join(quote.text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "\u2026"


def check(body, root, *, at=None, base=None):
    """Look for each quotation in its sources; return counts and the leads.

    A quotation that cites no source cannot be looked for, so it is counted
    as unchecked and never listed as a lead.
    """
    sources = Sources(root, at=at, base=base)
    checked = found = unchecked = 0
    leads = []
    for quote in extract(body, root, resolve=sources.resolve):
        parts = pieces(quote.text)
        if not quote.cited or not parts:
            unchecked += 1
            continue
        checked += 1
        # Each piece may come from any source the paragraph cites: a
        # reviewer setting two files side by side quotes from both under one
        # line that names both.
        texts = [t for t in (sources.text(s) for s in quote.cited) if t is not None]
        now = [any(p in t for t in texts) for p in parts]
        if all(now):
            found += 1
            continue
        olds = [
            t
            for t in (sources.text(s, base=True) for s in quote.cited)
            if t is not None
        ]
        then = [n or any(p in t for t in olds) for n, p in zip(now, parts)]
        if olds and all(then):
            why = (
                f"in what it cites as it stood at {base[:9]}, before the delta, "
                "and not there now"
            )
        elif not texts:
            why = f"cites no source that exists: {', '.join(quote.cited)}"
        else:
            why = "not in " + (
                "the source it cites"
                if len(texts) == 1
                else f"any of the {len(texts)} sources it cites"
            )
            if len(parts) > 1:
                why += f" ({sum(now)} of {len(parts)} pieces found)"
        leads.append(
            {
                "line": quote.line,
                "kind": quote.kind,
                "quote": _shown(quote),
                "cited": quote.cited,
                "why": why,
            }
        )
    return {
        "checked": checked,
        "found": found,
        "unchecked": unchecked,
        "leads": leads,
    }


def summary(result):
    """One receipt line for a turn's quotations."""
    if result.get("error"):
        return f"QUOTATIONS NOT CHECKED: {result['error']}"
    checked, leads = result["checked"], result["leads"]
    if not checked and not result["unchecked"]:
        return "quotations: none in the report"
    line = f"quotations: {checked} checked against the sources their paragraphs cite"
    if leads:
        line += (
            f", {len(leads)} NOT FOUND there: leads to read before weighing "
            "the findings they support, never verdicts"
        )
    elif checked:
        line += ", all found"
    if result["unchecked"]:
        line += f"; {result['unchecked']} cite no source and were not checked"
    return line
