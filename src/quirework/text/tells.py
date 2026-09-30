"""Machine-habit advisory (`quirework text tells`).

A manuscript drafted with language models keeps some of their habits after
line editing. The vocabulary tells are the first to go, because readers
already know them and a word list finds them. What survives is a move: saying
what a thing is by saying what it is not. One such sentence is often exactly
right. The trouble is recurrence, since a reader who meets the same move a
third time starts to hear it everywhere and stops trusting the sentence it
sits in.

Two kinds of rule run here:

  1. The contrast move, in four surface forms: a sentence ending on a
     correction ("..., not Y."), the two-sentence "It is not X. It is Y.",
     "not X, but Y", and "Not X: Y". The two-sentence form is deliberately
     narrow, a negated copula followed by a pronoun and an affirmed one,
     because a looser version flags ordinary narration. The detectors are
     English; a policy for another language turns them off with
     `contrast = false`.
  2. Phrases from the project's policy, each a regular expression mapped to
     why it reads as machine-made. The list is the project's, and it goes
     stale, since each model version has habits of its own.

It warns and never gates. Many contrasts are the argument itself, and only a
reader can tell a claim from a cadence. The useful warning is therefore
narrow: `added_since` reports only what a revision range adds, meaning a rule
whose count in a file went up, and within it the sentences that are new. A
typo fixed inside an old sentence changes no count and prints nothing.
"""

from __future__ import annotations

import argparse
import bisect
import re
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from quirework.project import current_project

POLICY = "text.tells_policy"

# --------------------------------------------------------------------------- #
# prose and sentences
# --------------------------------------------------------------------------- #

_COMMENT = re.compile(r"(?s)<!--.*?-->")
# Headings, figure embeds, fenced divs and tables are not running prose.
_NOT_PROSE = re.compile(r"^\s*(?:#|!\[|:::|\|)")
# A sentence ends at . ! or ?, after any closing quote or bracket, and the
# next opens on a capital, a digit or an opening quote.
_BOUNDARY = re.compile(r"(?<=[.!?])[\"”')\]]*\s+(?=[\"“(]?[A-Z0-9])")


@dataclass(frozen=True)
class Sentence:
    line: int
    text: str


def paragraphs(text: str) -> list[list[Sentence]]:
    """Each prose paragraph as its sentences, with the line each one starts on.

    Lines are joined within a paragraph before splitting, so a sentence
    wrapped across lines, or a line ending at a semicolon, is read whole."""
    text = _COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    blocks: list[list[tuple[int, str]]] = []
    block: list[tuple[int, str]] = []
    front = fence = False
    for no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if no == 1 and stripped in ("---", "+++"):
            front = stripped
            continue
        if front:
            front = False if stripped == front else front
            continue
        is_fence = stripped.startswith("```")
        if is_fence:
            fence = not fence
        if fence or is_fence or not stripped or _NOT_PROSE.match(line):
            if block:
                blocks.append(block)
                block = []
            continue
        block.append((no, stripped.replace("*", "")))
    if block:
        blocks.append(block)

    out = []
    for block in blocks:
        starts, joined = [], ""
        for _, line in block:
            starts.append(len(joined))
            joined += line + " "
        spans, begin = [], 0
        for m in _BOUNDARY.finditer(joined):
            spans.append((begin, m.end()))
            begin = m.end()
        spans.append((begin, len(joined)))
        out.append(
            [
                Sentence(
                    block[bisect.bisect_right(starts, b) - 1][0], joined[b:e].strip()
                )
                for b, e in spans
                if joined[b:e].strip()
            ]
        )
    return out


# --------------------------------------------------------------------------- #
# the rules
# --------------------------------------------------------------------------- #

CONTRAST_WHY = "defines a thing by what it is not"

# One detector per surface form. A sentence counts once, under the first
# form it shows.
FORMS = (
    (
        "..., not Y.",
        re.compile(r",\s+not\s+[^.;:!?,]{1,40}[.!?][\"”']?$", re.I),
    ),
    ("not X, but Y", re.compile(r"\bnot\b[^.;:!?]{1,60},\s*but\b", re.I)),
    (
        "Not X: Y",
        re.compile(
            r"^not\b[^.:!?]{1,60}:"
            r"|\bnot\b[^.;:!?]{1,60}:\s+(?:it|they|he|she|that|this)"
            r"\s+(?:is|was|are|were)\b",
            re.I,
        ),
    ),
)
PAIR = "It is not X. It is Y."
_NEGATED = re.compile(r"\b(?:is|was|are|were)(?:\s+not\b|n't\b)", re.I)
_AFFIRMED = re.compile(
    r"(?:It|That|This|They|He|She)\s+(?:is|was|are|were)\b(?!\s+not\b)"
)


@dataclass(frozen=True)
class Hit:
    line: int
    rule: str  # a contrast form, or a phrase pattern from the policy
    shown: str  # what to print for the rule
    reason: str
    text: str
    contrast: bool = False


def _plain(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'")


def scan(text: str, phrases: dict[str, str], contrast: bool = True):
    """(hits, sentence count) for one file's prose."""
    compiled = [(p, re.compile(p, re.I), why) for p, why in phrases.items()]
    hits: list[Hit] = []
    total = 0
    for para in paragraphs(text):
        total += len(para)
        for i, s in enumerate(para):
            plain = _plain(s.text)
            if contrast:
                for form, rx in FORMS:
                    if rx.search(plain):
                        hits.append(Hit(s.line, form, form, CONTRAST_WHY, s.text, True))
                        break
                else:
                    before = _plain(para[i - 1].text) if i else ""
                    if i and _AFFIRMED.match(plain) and _NEGATED.search(before):
                        pair = f"{para[i - 1].text} {s.text}"
                        hits.append(Hit(s.line, PAIR, PAIR, CONTRAST_WHY, pair, True))
            for pattern, rx, why in compiled:
                m = rx.search(plain)
                if m:
                    hits.append(Hit(s.line, pattern, f'"{m.group(0)}"', why, s.text))
    return hits, total


def added(before: str, after: str, phrases: dict[str, str], contrast: bool = True):
    """The hits `after` adds over `before`.

    For each rule whose count went up, the hits whose sentence is new. A
    sentence edited around a habit it already had changes no count, so it is
    not reported; that is what keeps the warning short enough to read."""
    old, _ = scan(before, phrases, contrast)
    new, _ = scan(after, phrases, contrast)
    was = Counter(h.rule for h in old)
    now = Counter(h.rule for h in new)
    seen = {h.text for h in old}
    return [h for h in new if now[h.rule] > was[h.rule] and h.text not in seen]


# --------------------------------------------------------------------------- #
# policy and scope
# --------------------------------------------------------------------------- #


def _policy() -> dict:
    return current_project().data(POLICY, {})


def configured() -> bool:
    """Whether the project declares a tells policy at all."""
    return current_project().get(POLICY) is not None


def _phrases() -> dict[str, str]:
    return _policy().get("phrases", {})


def _contrast() -> bool:
    return bool(_policy().get("contrast", True))


def targets() -> list[str]:
    """Project-relative files in scope, in reading order where the manuscript
    has one: `scope_glob` where the policy declares one, else the
    manuscript's prose files, minus `exclude_names` and underscore files."""
    project = current_project()
    root = project.root
    policy = _policy()
    exclude = set(policy.get("exclude_names", ()))
    candidates: list[Path] = []
    if policy.get("scope_glob"):
        for sub, glob in policy["scope_glob"]:
            if (root / sub).is_dir():
                candidates += sorted((root / sub).glob(glob))
    elif project.get("manuscript.source") is not None:
        from quirework.manuscript import units

        candidates = units.prose_files()
    rels = [
        str(p.resolve().relative_to(root.resolve()))
        for p in candidates
        if p.is_file() and p.name not in exclude and not p.name.startswith("_")
    ]
    order: dict[str, int] = {}
    if project.get("manuscript.source") is not None:
        from quirework.manuscript import units

        order = {u.rel: n for n, u in enumerate(units.registry())}
    return sorted(dict.fromkeys(rels), key=lambda r: (order.get(r, len(order)), r))


def triggers() -> tuple[str, ...]:
    """Path patterns whose change makes the advisory relevant."""
    scope = _policy().get("scope_glob")
    if scope:
        return tuple(f"{sub}/{glob}" for sub, glob in scope)
    return tuple(targets())


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(current_project().root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _at(rev: str, rel: str) -> str:
    """The file at `rev`, or nothing where `rev` does not have it."""
    res = _git("show", f"{rev}:{rel}")
    return res.stdout if res.returncode == 0 else ""


def added_since(rev: str, paths: list[str] | None = None) -> list[tuple[str, Hit]]:
    """(path, hit) for everything the working tree adds over `rev`, across
    `paths` or the whole scope. A file absent at `rev` adds all its hits.

    The revision is verified first. Otherwise a mistyped one would make every
    file look new and report every hit in scope as added."""
    if _git("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").returncode:
        raise ValueError(f"unknown revision: {rev}")
    root = current_project().root
    scope = set(targets())
    out = []
    for rel in paths if paths is not None else sorted(scope):
        path = root / rel
        if rel not in scope or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for h in added(_at(rev, rel), text, _phrases(), _contrast()):
            out.append((rel, h))
    return out


def measure() -> list[tuple[str, list[Hit], int]]:
    """(path, hits, sentence count) for every file in scope."""
    root = current_project().root
    return [
        (rel, *scan((root / rel).read_text(encoding="utf-8"), _phrases(), _contrast()))
        for rel in targets()
    ]


def density(hits: list[Hit], sentences: int) -> float:
    """Contrast sentences per 1,000 sentences."""
    return 1000 * sum(h.contrast for h in hits) / max(sentences, 1)


def summary() -> str:
    """Two lines for a whole-tree check; empty when nothing is in scope."""
    rows = measure()
    if not rows:
        return ""
    dens = sorted(((density(h, n), Path(r).stem) for r, h, n in rows), reverse=True)
    phrase_hits = sum(not x.contrast for _, h, _ in rows for x in h)
    top = ", ".join(f"{name} {d:.0f}" for d, name in dens[:3])
    median = statistics.median(d for d, _ in dens)
    return (
        f"contrast move per 1,000 sentences: median {median:.0f}, highest {top}.\n"
        f"  {phrase_hits} phrase hit(s) in scope. Report: quirework text tells --hits"
    )


# --------------------------------------------------------------------------- #
# the report
# --------------------------------------------------------------------------- #


def _noun() -> str:
    if current_project().get("manuscript.source") is None:
        return "file"
    from quirework.manuscript import units

    return units.noun()


def report(show_hits: bool) -> str:
    rows = measure()
    if not rows:
        return "tells: nothing in scope."
    noun = _noun()
    lines = [f"{noun:32}{'sentences':>10}{'contrast':>10}{'per 1k':>8}{'phrases':>9}"]
    for rel, hits, n in rows:
        c = sum(h.contrast for h in hits)
        name = Path(rel).stem
        lines.append(f"{name:32}{n:10}{c:10}{density(hits, n):8.0f}{len(hits) - c:9}")
    by_rule: Counter[str] = Counter()
    where: dict[str, set[str]] = {}
    wording: dict[str, Counter[str]] = {}
    for rel, hits, _ in rows:
        for h in hits:
            by_rule[h.rule] += 1
            where.setdefault(h.rule, set()).add(rel)
            label = h.shown if h.contrast else h.shown.lower()
            wording.setdefault(h.rule, Counter())[label] += 1
    lines += ["", f"{'rule (commonest wording)':44}{'hits':>6}{f'{noun}s':>10}"]
    for rule, count in by_rule.most_common():
        forms = wording[rule]
        label = forms.most_common(1)[0][0]
        if len(forms) > 1:
            label += f" +{len(forms) - 1} more"
        lines.append(f"{label[:44]:44}{count:6}{len(where[rule]):10}")
    if show_hits:
        for rel, hits, _ in rows:
            for h in hits:
                lines.append(f"\n{rel}:{h.line}: {h.shown}\n  {h.text}")
    return "\n".join(lines)


def format_added(found: list[tuple[str, Hit]]) -> list[str]:
    """One printable entry per added hit."""
    return [
        f"{rel}:{h.line}: {h.shown}, {h.reason}\n      {h.text[:110]}"
        for rel, h in found
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="quirework text tells",
        description="Report the contrast move and the policy's machine-habit "
        "phrases in the prose the project's tells policy selects: a table per "
        "file, or, with --since, only what the working tree adds over a "
        "revision.",
        epilog="This WARNS and never gates. Many contrasts are the argument "
        "itself, and only a reader can tell a claim from a cadence.",
    )
    ap.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="project-relative file to consider with --since (default: the scope)",
    )
    ap.add_argument("--hits", action="store_true", help="also print every hit")
    ap.add_argument(
        "--since",
        metavar="REV",
        help="print only the hits the working tree adds over REV",
    )
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    if not configured():
        print("tells: no text.tells_policy is configured.", file=sys.stderr)
        return 0
    if args.since:
        outside = sorted(set(args.paths) - set(targets()))
        if outside:
            print(f"tells: not in scope: {', '.join(outside)}", file=sys.stderr)
            return 2
        try:
            found = added_since(args.since, args.paths or None)
        except ValueError as exc:
            print(f"tells: {exc}", file=sys.stderr)
            return 2
        if not found:
            print(f"✓ tells: nothing added since {args.since}.")
            return 0
        print(f"tells: {len(found)} hit(s) added since {args.since}:\n")
        print("\n".join(format_added(found)))
        return 0
    print(report(args.hits))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
