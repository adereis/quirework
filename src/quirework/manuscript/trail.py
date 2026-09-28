"""The spec's trail sidecar: pointers into the sessions that shaped it.

The spec states what a book holds now. The provenance corpus records how the
author got there. The trail sidecar joins the two without copying either: it
mirrors the spec's level-2 headings, and under each one lists breadcrumbs,
each naming a conversation and the moment in it where the author seeded,
sharpened, corrected, reversed, accepted or probed what that section says.

    ## Quirks, not commitments {#quirks-not-commitments}

    - 2026-09-16T03:19:03Z · seed · claude-code 893d06b2-042a-458f-bc18-4f20e00032e0
      The brief names the register and refuses "commitments".

      > He calls them quirks, because "commitments" sounded like marriage.

A breadcrumb is a pointer, not a copy. The session is the only source of
truth, so the author's words are read from it whenever the trail is read, and
the reader decides from there how much of the conversation to open: the turn,
the reply it drew, the proposal it answered. A copied quote would duplicate
that source and cut its context away.

The pointer is the time the harness logged, in UTC, because anyone can find
that moment: in the captured corpus, in the harness's own transcript under the
user's home, or in the web conversation, with or without Quirework. The
author's turn number is derived from it on every read. The number is
Quirework's own count, and it would move if the wrapper filter learned to drop
one more kind of harness message. A pre-rendered Gemini review logs no times,
and a red-pen round records only the day of each mark, so there, and only
there, the breadcrumb names the turn.

The note is the agent's reading of the turn. The excerpt is optional: a
highlight for a long turn whose relevant words would otherwise be buried, and
it has to appear in that turn.

The sidecar is context, never authority. It lives wherever `manuscript.spec_trail`
puts it, normally beside the private corpus, and the spec carries none of it:
no session id, no date, no count. Leaving `manuscript.spec_trail` unset turns the
whole feature off.

A formatter cannot change what a breadcrumb says. Its header is read as
tokens, so a reflow may wrap it onto a second line, and the excerpt is a
paragraph of its own, which the column formatter keeps as a blockquote. The
writer emits that formatter's fixed point, so running it changes nothing.
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quirework.manuscript import notes
from quirework.project import ConfigurationError, current_project

# What a turn did to the section. A closed set, so every trail reads the same
# way; the nuance belongs in the note. `probe` exists so that a position the
# author only tried out is never read back later as a ruling.
KINDS = {
    "seed": "first raises the idea",
    "sharpen": "makes it more precise, or changes its words",
    "correct": "rejects a wording or claim the agent proposed",
    "reverse": "moves off an earlier position",
    "accept": "adopts a wording the agent drafted",
    "probe": "tests or wonders without settling",
}

# The harness a session ran in, as a breadcrumb names it, and the corpus
# family that holds its capture. The names are the ones a person would use to
# go and find the conversation without Quirework.
HARNESSES = {
    "claude-code": "cc",
    "codex": "codex",
    "antigravity": "agy",
    "claude.ai": "web",
    "gemini": "gemini",
    "redpen": "redpen",
}
_HARNESS_OF = {family: name for name, family in HARNESSES.items()}
# Sources that log no time for a turn, so a breadcrumb names the turn: a
# pre-rendered Gemini review, and a red-pen round, whose marks carry a day.
_TIMELESS = {"gemini", "redpen"}
_NAMED = ("gemini-", "redpen-")

_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.(\d{1,6}))?Z")
_SESSION = re.compile(r"[A-Za-z0-9][\w.-]*")
_SEPARATORS = {"·", "—", "-", "|"}
_CONTEXT = (
    "The trail is context, never authority: the spec states what the book "
    "holds now, and a request made now outranks anything on it."
)


@dataclass(frozen=True)
class Crumb:
    """One breadcrumb: a turn of the author's that shaped one spec section."""

    kind: str
    harness: str
    session: str
    stamp: str | None = None
    turn: int | None = None
    note: str = ""
    excerpt: str = ""
    line: int = 0

    @property
    def family(self):
        return HARNESSES[self.harness]

    @property
    def short(self):
        return self.session if self.session.startswith(_NAMED) else self.session[:8]

    @property
    def when(self):
        """The pointer as written: the logged time, or the turn."""
        return self.stamp if self.stamp else f"turn {self.turn}"

    @property
    def instant(self):
        if not self.stamp:
            return None
        return datetime.fromisoformat(self.stamp[:-1] + "+00:00")

    @property
    def unit(self):
        """The span the stamp names: its second, or its written fraction."""
        digits = _STAMP.fullmatch(self.stamp or "").group(1) or ""
        return timedelta(microseconds=10 ** (6 - len(digits)))

    def holds(self, logged):
        """Whether a turn logged at ``logged`` is the one this stamp names."""
        start = self.instant
        return start is not None and start <= logged < start + self.unit

    def same_turn(self, other):
        if self.session != other.session:
            return False
        if self.stamp and other.stamp:
            return self.holds(other.instant) or other.holds(self.instant)
        return self.turn is not None and self.turn == other.turn

    def header(self):
        return f"- {self.when} · {self.kind} · {self.harness} {self.session}"


@dataclass
class Section:
    slug: str | None
    title: str
    line: int
    crumbs: list[Crumb] = field(default_factory=list)


@dataclass
class Sidecar:
    path: Path
    preamble: str = ""
    sections: list[Section] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def section(self, slug):
        return next((s for s in self.sections if s.slug == slug), None)

    def crumbs(self):
        return [(s, c) for s in self.sections for c in s.crumbs]


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
def configured():
    """Whether this project keeps a trail at all."""
    return current_project().get("manuscript.spec_trail") is not None


def sidecar_path():
    if not configured():
        raise ConfigurationError(
            "this project keeps no spec trail: set manuscript.spec_trail in "
            "quirework.toml to the file that should hold it"
        )
    return current_project().path("manuscript.spec_trail")


def _rel(path):
    root = current_project().root
    path = Path(path)
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


# --------------------------------------------------------------------------- #
# the file: parse and write
# --------------------------------------------------------------------------- #
def parse(text, path):
    """A ``Sidecar`` from its text, with every line it cannot read recorded.

    The file is strict because a tool rewrites it: anything the parser does not
    understand would be lost on the next write, so it is refused instead.
    """
    sidecar = Sidecar(Path(path))
    where = _rel(path)
    lines = text.split("\n")
    shown = notes._mask_nonprose(notes._mask_fences(text)).split("\n")
    preamble, item = [], None

    def close():
        nonlocal item
        if item is not None:
            crumb, trouble = _crumb(item[1], item[0])
            if trouble:
                sidecar.problems.append(f"{where}:{item[0]}: {trouble}")
            else:
                sidecar.sections[-1].crumbs.append(crumb)
        item = None

    for number, (line, seen) in enumerate(zip(lines, shown), 1):
        if not sidecar.sections:
            head = notes._HEADING.match(seen)
            if not (head and len(head.group(1)) == 2):
                if seen.startswith("- "):
                    sidecar.problems.append(
                        f"{where}:{number}: a breadcrumb before any section heading"
                    )
                preamble.append(line)
                continue
        if line.strip() and not seen.strip():
            close()
            sidecar.problems.append(
                f"{where}:{number}: a comment inside a section is lost when the "
                f"trail is rewritten; keep notes in the banner at the top"
            )
            continue
        head = notes._HEADING.match(seen)
        if head:
            close()
            if len(head.group(1)) != 2:
                sidecar.problems.append(
                    f"{where}:{number}: only level-2 headings, mirroring the "
                    f"spec's sections, belong below the banner"
                )
                continue
            anchor = notes._ATTR_ID.search(head.group(3) or "")
            if not anchor:
                sidecar.problems.append(
                    f"{where}:{number}: heading '{head.group(2)}' has no {{#slug}} "
                    f"naming its spec section"
                )
            sidecar.sections.append(
                Section(anchor.group(1) if anchor else None, head.group(2), number)
            )
        elif line.startswith("- "):
            close()
            item = (number, [line[2:]])
        elif not line.strip():
            if item is not None:
                item[1].append("")
        elif line[0].isspace() and item is not None:
            item[1].append(line)
        else:
            close()
            sidecar.problems.append(
                f"{where}:{number}: not part of a breadcrumb; each one starts "
                f"'- TIME · KIND · HARNESS SESSION'"
            )
    close()
    sidecar.preamble = "\n".join(preamble).strip("\n")
    return sidecar


def _crumb(lines, number):
    """``(Crumb, None)`` from one list item's lines, or ``(None, trouble)``."""
    paragraphs, current = [], []
    for line in lines:
        if line.strip():
            current.append(line.strip())
        elif current:
            paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)
    if not paragraphs:
        return None, "an empty breadcrumb"
    first, rest = paragraphs[0], paragraphs[1:]
    excerpt = [
        " ".join(line[1:].strip() if line.startswith(">") else line for line in p)
        for p in rest
        if p[0].startswith(">")
    ]
    extra = [" ".join(p) for p in rest if not p[0].startswith(">")]

    tokens = " ".join(first).split()
    fields = []
    while tokens and len(fields) < 4:
        token = tokens.pop(0)
        if token in _SEPARATORS:
            continue
        if not fields and token == "turn":
            if not tokens:
                return None, "'turn' names no number"
            token = f"turn {tokens.pop(0)}"
        fields.append(token)
    if len(fields) < 4:
        return None, "a breadcrumb names its time, kind, harness and session"
    when, kind, harness, session = fields
    while tokens and tokens[0] in _SEPARATORS:
        tokens.pop(0)
    note = " ".join([" ".join(tokens), *extra]).strip()

    if kind not in KINDS:
        return None, f"unknown kind '{kind}' (kinds: {', '.join(KINDS)})"
    if harness not in HARNESSES:
        return None, f"unknown harness '{harness}' (harnesses: {', '.join(HARNESSES)})"
    if not _SESSION.fullmatch(session):
        return None, f"'{session}' is not a session id"
    stamp = turn = None
    if when.startswith("turn "):
        if harness not in _TIMELESS:
            return None, (
                f"a {harness} session logs its times, so its breadcrumb names the "
                f"logged time, not a turn number"
            )
        try:
            turn = int(when[5:])
        except ValueError:
            return None, f"'{when}' is not a turn number"
        if turn < 1:
            return None, "turns are counted from 1"
    elif _STAMP.fullmatch(when):
        if harness in _TIMELESS:
            return None, f"a {harness} review logs no times; name the turn instead"
        stamp = when
    else:
        return None, (
            f"'{when}' is neither a logged UTC time such as "
            f"2026-09-16T03:19:03Z nor 'turn N'"
        )
    return (
        Crumb(kind, harness, session, stamp, turn, note, " ".join(excerpt), number),
        None,
    )


def load(path=None):
    """The configured sidecar, parsed; an empty one when the file is absent."""
    path = Path(path) if path is not None else sidecar_path()
    if not path.exists():
        return Sidecar(path)
    return parse(path.read_text(encoding="utf-8"), path)


def _banner():
    spec = notes._spec_name()
    return (
        f"<!--\n  Trail sidecar for {spec}: the conversations that shaped each\n"
        "  section, as pointers into the sessions. Context for whoever edits\n"
        "  the spec, never authority over it. Written by `quirework spec\n"
        "  trail add`; the headings mirror the spec's.\n-->"
    )


def _days(crumbs):
    """``{session: day}`` for each breadcrumb that points by turn, not by time.

    A red-pen round and a Gemini review log no times but record their date,
    so their breadcrumbs take their place in the story by that day, as the
    backfill worklist places them. Without provenance, or for a session the
    corpus does not hold, a breadcrumb stays undated.
    """
    wanted = {c.session for c in crumbs if c.instant is None}
    if not wanted or not current_project().get("provenance.enabled", False):
        return {}
    from quirework.provenance import show

    captured = Corpus().captured()
    days = {}
    for sid in wanted:
        if sid in captured:
            day = show.session_date(*captured[sid])
            if day is not None:
                days[sid] = day
    return days


def _chronology(crumb, days=None):
    """Logged order; a turn with no time takes the start of its session's day,
    and one with neither follows the dated turns."""
    if crumb.instant is not None:
        return (0, crumb.instant, 0)
    day = (days or {}).get(crumb.session)
    if day is not None:
        start = datetime.combine(day, datetime.min.time(), timezone.utc)
        return (0, start, crumb.turn or 0)
    return (1, datetime.min.replace(tzinfo=timezone.utc), crumb.turn or 0)


def dump(sidecar, spec=None):
    """The sidecar's text: spec order, chronological, at the formatter's fixed point."""
    from quirework.text import mdwrap

    spec = notes.spec_sections() if spec is None else spec
    order = {s.slug: i for i, s in enumerate(spec) if s.slug}
    titles = {s.slug: s.title for s in spec if s.slug}
    parts = [sidecar.preamble or _banner()]
    held = [s for s in sidecar.sections if s.crumbs]
    days = _days([c for s in held for c in s.crumbs])
    for section in sorted(held, key=lambda s: order.get(s.slug, len(order))):
        title = titles.get(section.slug, section.title)
        parts.append(f"## {title} {{#{section.slug}}}")
        # Pointer, note and excerpt are separate paragraphs of one list item,
        # so a reflow that wraps the pointer never runs it into the prose.
        for crumb in sorted(section.crumbs, key=lambda c: _chronology(c, days)):
            parts.append(crumb.header())
            if crumb.note:
                parts.append(f"  {crumb.note}")
            if crumb.excerpt:
                parts.append(f"  > {crumb.excerpt}")
    return mdwrap.reflow_columns("\n\n".join(parts) + "\n")


@contextlib.contextmanager
def _locked(path):
    """Serialize one read-modify-write of ``path`` across processes.

    The lock lives in the project's cache, not beside the sidecar, where it
    would show up as an untracked file in a private tree that is under review.
    """
    key = hashlib.sha256(os.fsencode(Path(path).resolve())).hexdigest()[:20]
    lock = current_project().cache_root / "locks" / f"trail-{key}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write(path, text):
    """Replace ``path`` atomically. A new sidecar is readable by its owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o7777)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def structure_problems(slugs=None, sidecar=None):
    """What stops the sidecar from mirroring the spec, without reading a session.

    A heading whose slug the spec no longer declares is how a renamed or
    retired section shows up, so `spec notes --check` runs this beside the
    bindings it would break in the same edit.
    """
    slugs = notes.spec_slugs() if slugs is None else set(slugs)
    sidecar = load() if sidecar is None else sidecar
    problems = list(sidecar.problems)
    where = _rel(sidecar.path)
    seen = {}
    for section in sidecar.sections:
        if section.slug is None:
            continue
        if section.slug in seen:
            problems.append(
                f"{where}:{section.line}: '#{section.slug}' is headed twice "
                f"(also at line {seen[section.slug]})"
            )
        seen.setdefault(section.slug, section.line)
        if section.slug not in slugs:
            close = difflib.get_close_matches(section.slug, sorted(slugs), n=1)
            hint = f"; renamed to '{close[0]}'?" if close else ""
            problems.append(
                f"{where}:{section.line}: '#{section.slug}' names no spec "
                f"section{hint} A renamed section takes its trail with it"
            )
        for i, crumb in enumerate(section.crumbs):
            if any(crumb.same_turn(other) for other in section.crumbs[:i]):
                problems.append(
                    f"{where}:{crumb.line}: {crumb.harness} {crumb.short} "
                    f"{crumb.when} is already on this section's trail"
                )
    return problems


def counts():
    """Breadcrumbs per spec slug, read from the file alone."""
    if not configured():
        return {}
    return {s.slug: len(s.crumbs) for s in load().sections if s.slug and s.crumbs}


# --------------------------------------------------------------------------- #
# the sessions a breadcrumb points into
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Turn:
    """One of the author's turns, wherever it was read from."""

    family: str
    session: str
    path: Path
    number: int
    logged: datetime | None
    text: str
    live: bool = False

    @property
    def harness(self):
        return _HARNESS_OF[self.family]

    @property
    def short(self):
        return self.session if self.session.startswith(_NAMED) else self.session[:8]


# Quotation marks and apostrophes, straight and curly. A commit quoting the
# author writes whichever its editor produced ('mind', "mind", \"mind\"), and
# the author typed whatever the keyboard produced, so matching ignores them.
_QUOTE_MARKS = str.maketrans(
    "", "", "\"'`\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u201f\u00b4"
)


def _squash(text):
    return " ".join(text.translate(_QUOTE_MARKS).split()).casefold()


def _pieces(phrase):
    """A phrase as the ordered pieces its elisions leave."""
    return [p for p in (_squash(s) for s in re.split(r"\.\.\.|…", phrase)) if p]


def contains(text, phrase):
    """Whether ``phrase`` appears in ``text``, ignoring case, spacing and elisions."""
    pieces, haystack, at = _pieces(phrase), _squash(text), 0
    if not pieces:
        return False
    for piece in pieces:
        at = haystack.find(piece, at)
        if at < 0:
            return False
        at += len(piece)
    return True


def _probe(phrase):
    """A word that must appear raw in any log file holding ``phrase``.

    Log files are JSON, which escapes quotes, newlines and non-ASCII text, so
    only a plain ASCII word is safe to look for in the raw bytes.
    """
    words = [w for w in re.findall(r"[A-Za-z0-9]+", phrase) if len(w) >= 4]
    return max(words, key=len).casefold() if words else None


class Corpus:
    """The sessions one command reads: the captured corpus, then live logs.

    Reading sessions is provenance's business, so it needs provenance enabled
    for this project; a trail can still be kept, and read as pointers, without.
    """

    def __init__(self):
        project = current_project()
        if not project.get("provenance.enabled", False):
            raise ConfigurationError(
                "reading the sessions a trail points into needs provenance: "
                "enable [provenance] and declare its root in quirework.toml"
            )
        project.require("provenance")
        from quirework.provenance import show

        self._show = show
        self._index = None
        self._live = None
        self._turns = {}

    def captured(self):
        """Full session id -> ``(family, path)`` for every captured session."""
        if self._index is None:
            self._index = {
                self._show.session_id(family, path): (family, path)
                for family, path in self._show.sessions()
            }
        return self._index

    def live(self):
        """Sessions only the harness holds so far: this project's own transcripts.

        Read-only. Claude Code and Codex list their transcripts without writing
        anything; Antigravity's live logs are laid out differently from its
        captures, so its sessions are found once captured.
        """
        if self._live is None:
            from quirework.provenance import worktrees
            from quirework.provenance.ingestion import claude, codex

            roots = worktrees.project_trees(current_project().root)
            found = {}
            for directory in claude.sibling_session_dirs():
                for path in sorted(directory.glob("*.jsonl")):
                    if claude.belongs(path, roots):
                        found.setdefault(path.stem, ("cc", path))
            for path in codex.sweep()[0]:
                sid = codex.session_id(path)
                if sid:
                    found.setdefault(sid, ("codex", path))
            captured = self.captured()
            self._live = {k: v for k, v in found.items() if k not in captured}
        return self._live

    def turns(self, sid, family, path, live=False):
        key = (family, Path(path))
        if key not in self._turns:
            self._turns[key] = [
                Turn(family, sid, Path(path), n, logged, text, live)
                for n, logged, text in self._show.author_turns(family, path)
            ]
        return self._turns[key]

    def holding(self, needles, table=None):
        """Which sessions' log files hold each needle, ignoring case.

        One pass over the files, one file in memory at a time, every needle
        tested against it. Folding the corpus again for each phrase cost a
        backfill suggestion 54 of its 75 seconds on 500 sessions, and keeping
        the folded corpus in memory instead peaked at 845 MB: a string holding
        one emoji stores every character in four bytes.
        """
        table = self.captured() if table is None else table
        wanted = {needle: needle.casefold() for needle in needles if needle}
        found = {needle: set() for needle in wanted}
        for sid, (_, path) in table.items():
            text = Path(path).read_text(encoding="utf-8", errors="replace").casefold()
            for needle, folded in wanted.items():
                if folded in text:
                    found[needle].add(sid)
        return found

    def session(self, prefix):
        """``(sid, family, path, live)`` for an id prefix, or raise ``LookupError``."""
        hits = [
            (sid, family, path, live)
            for live, table in ((False, self.captured()), (True, self.live()))
            for sid, (family, path) in table.items()
            if sid.startswith(prefix)
        ]
        if not hits:
            raise LookupError(f"no session, captured or live, matches '{prefix}'")
        if len(hits) > 1:
            names = ", ".join(sid for sid, *_ in hits)
            raise LookupError(f"'{prefix}' is ambiguous: {names}")
        return hits[0]

    def find(self, phrase, prefix="", *, within=None, live=True):
        """Every author turn holding ``phrase``: captured ones, else live ones.

        ``within`` is a set of captured session ids already known to hold the
        phrase's probe word, for a caller that looked many phrases up in one
        pass; ``live=False`` keeps the search to the captured corpus.
        """
        probe = _probe(phrase)
        tables = [(False, self.captured())] + [(True, self.live())] * live
        for is_live, table in tables:
            table = {k: v for k, v in table.items() if k.startswith(prefix)}
            if within is not None and not is_live:
                sids = within & set(table)
            elif probe:
                sids = self.holding([probe], table)[probe]
            else:
                sids = set(table)
            hits = [
                t
                for sid in sorted(sids)
                for t in self.turns(sid, *table[sid], is_live)
                if contains(t.text, phrase)
            ]
            if hits:
                return hits
        return []


def _stamp(turn, siblings):
    """The logged time a breadcrumb stores: to the second, finer only if needed."""
    utc = turn.logged.astimezone(timezone.utc)
    second = utc.replace(microsecond=0)
    crowded = any(
        other.number != turn.number
        and other.logged is not None
        and other.logged.astimezone(timezone.utc).replace(microsecond=0) == second
        for other in siblings
    )
    if crowded:
        return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond:06d}Z"
    return second.strftime("%Y-%m-%dT%H:%M:%SZ")


def _normal_stamp(value):
    """A time given on the command line, as the UTC stamp a breadcrumb stores."""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"--at {value!r} is not a time such as 2026-09-16T03:19:03Z")
    if moment.tzinfo is None:
        raise ValueError(
            f"--at {value!r} names no zone; write the logged time in UTC, "
            f"ending in Z, so it means one moment everywhere"
        )
    utc = moment.astimezone(timezone.utc)
    if utc.microsecond:
        return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond:06d}Z"
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Located:
    """Where a breadcrumb leads: ``state`` says how far it could be followed."""

    state: str  # turn | uncaptured | removed | harness | no-turn | crowded
    turn: Turn | None = None
    detail: str = ""


def _removed():
    from quirework.provenance import catalog

    return {entry["id"] for entry in catalog._removed()}


def locate(crumb, corpus, removed=frozenset()):
    """Follow one breadcrumb into the corpus as far as it goes."""
    if crumb.session in removed:
        return Located("removed")
    captured = corpus.captured().get(crumb.session)
    if captured is None:
        return Located("uncaptured")
    family, path = captured
    if family != crumb.family:
        return Located("harness", detail=_HARNESS_OF[family])
    turns = corpus.turns(crumb.session, family, path)
    if crumb.turn is not None:
        match = [t for t in turns if t.number == crumb.turn]
    else:
        match = [t for t in turns if t.logged is not None and crumb.holds(t.logged)]
    if not match:
        return Located("no-turn", detail=str(len(turns)))
    if len(match) > 1:
        return Located("crowded", detail=", ".join(str(t.number) for t in match))
    return Located("turn", match[0])


# --------------------------------------------------------------------------- #
# adding a breadcrumb
# --------------------------------------------------------------------------- #
def _spec_titles():
    return {s.slug: s.title for s in notes.spec_sections() if s.slug}


def _known_slug(slug, titles):
    if slug not in titles:
        close = difflib.get_close_matches(slug, sorted(titles), n=3)
        hint = f" (close: {', '.join(close)})" if close else ""
        raise LookupError(f"the spec has no section '#{slug}'{hint}")


def _target(corpus, *, find, session, turn):
    """The one author turn named by ``find`` or by ``session`` and ``turn``."""
    if find:
        hits = corpus.find(find, session or "")
        if not hits:
            where = f" in sessions matching '{session}'" if session else ""
            raise LookupError(
                f"no author turn holds {find!r}{where}, in the corpus or in this "
                f"project's live Claude Code and Codex transcripts. An "
                f"Antigravity session is found once captured "
                f"(quirework provenance ingest agy)"
            )
        if len(hits) > 1:
            rows = "\n".join(
                f"  {t.harness} {t.short}  Human {t.number}"
                f"{'  (live)' if t.live else ''}  {_opening(t.text, 70)}"
                for t in hits[:10]
            )
            more = f"\n  … and {len(hits) - 10} more" if len(hits) > 10 else ""
            raise LookupError(
                f"{len(hits)} author turns hold {find!r}; name one with "
                f"--session ID --turn N, or give a longer phrase:\n{rows}{more}"
            )
        hit = hits[0]
    else:
        sid, family, path, live = corpus.session(session)
        turns = corpus.turns(sid, family, path, live)
        hit = next((t for t in turns if t.number == turn), None)
        if hit is None:
            raise LookupError(
                f"{_HARNESS_OF[family]} {sid[:8]} holds {len(turns)} author "
                f"turn(s); there is no turn {turn}"
            )
    siblings = corpus.turns(hit.session, hit.family, hit.path, hit.live)
    return hit, siblings


def prepare(
    slug,
    kind,
    *,
    find=None,
    session=None,
    turn=None,
    at=None,
    harness=None,
    note="",
    excerpt="",
    corpus=None,
    titles=None,
):
    """``(crumb, target)`` for one breadcrumb, checked but not yet written.

    The turn is named one of three ways. ``find`` is a phrase the author typed,
    looked up in the corpus and then in this project's live transcripts, which
    is how a turn from the current conversation is pointed at before it has
    been captured. ``session`` and ``turn`` take a number search or show
    printed. ``harness``, ``session`` and ``at`` write a pointer by hand, for a
    conversation Quirework cannot read, and are verified only if it can.
    ``corpus`` and ``titles`` let a caller checking many share one read.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown kind '{kind}' (kinds: {', '.join(KINDS)})")
    titles = _spec_titles() if titles is None else titles
    _known_slug(slug, titles)
    note, excerpt = " ".join(note.split()), " ".join(excerpt.split())

    target = None
    if at is not None:
        if find or turn is not None:
            raise ValueError("--at names the turn by hand; drop --find and --turn")
        if not (harness and session):
            raise ValueError("a hand-written pointer needs --harness and --session")
        if harness not in HARNESSES:
            raise ValueError(
                f"unknown harness '{harness}' (harnesses: {', '.join(HARNESSES)})"
            )
        if not _SESSION.fullmatch(session):
            raise ValueError(f"'{session}' is not a session id")
        if harness in _TIMELESS:
            raise ValueError(f"a {harness} source logs no times; use --turn")
        crumb = Crumb(kind, harness, session, _normal_stamp(at), None, note, excerpt)
        if current_project().get("provenance.enabled", False):
            found = locate(crumb, corpus or Corpus())
            if found.state not in ("turn", "uncaptured"):
                raise LookupError(_explain(crumb, found))
            target = found.turn
    else:
        if harness:
            raise ValueError("--harness goes with --at; --find and --turn know it")
        if not find and not (session and turn is not None):
            raise ValueError(
                "name the turn: --find PHRASE, --session ID --turn N, or "
                "--harness H --session ID --at TIME"
            )
        if find and turn is not None:
            raise ValueError("--find and --turn each name a turn; give one")
        target, siblings = _target(
            corpus or Corpus(), find=find, session=session, turn=turn
        )
        if target.logged is None:
            crumb = Crumb(
                kind, target.harness, target.session, None, target.number, note, excerpt
            )
        else:
            stamp = _stamp(target, siblings)
            crumb = Crumb(
                kind, target.harness, target.session, stamp, None, note, excerpt
            )
    if excerpt and target is not None and not contains(target.text, excerpt):
        raise LookupError(
            f"the excerpt is not in {target.harness} {target.short} Human "
            f"{target.number}; an excerpt highlights words the turn holds"
        )
    return crumb, target


def append(slug, crumbs, titles=None):
    """Write checked breadcrumbs onto one section's trail, all or none."""
    titles = _spec_titles() if titles is None else titles
    path = sidecar_path()
    with _locked(path):
        sidecar = load(path)
        trouble = structure_problems(titles, sidecar)
        if trouble:
            raise LookupError(
                "the trail has problems to fix before it is rewritten:\n  "
                + "\n  ".join(trouble)
            )
        section = sidecar.section(slug)
        if section is None:
            section = Section(slug, titles[slug], 0)
            sidecar.sections.append(section)
        for i, crumb in enumerate(crumbs):
            if any(crumb.same_turn(other) for other in [*section.crumbs, *crumbs[:i]]):
                raise LookupError(
                    f"{crumb.harness} {crumb.short} {crumb.when} is already on "
                    f"#{slug}'s trail"
                )
        section.crumbs.extend(crumbs)
        _write(path, dump(sidecar))


def add(slug, kind, **pointer):
    """Put one breadcrumb on a section's trail; return it and where it leads.

    ``pointer`` names the turn as ``prepare`` takes it.
    """
    titles = _spec_titles()
    crumb, target = prepare(slug, kind, titles=titles, **pointer)
    append(slug, [crumb], titles)
    return crumb, target


# --------------------------------------------------------------------------- #
# reading a trail
# --------------------------------------------------------------------------- #
def _opening(text, width):
    one = " ".join(text.split())
    return one if len(one) <= width else one[: width - 1] + "…"


def _date(crumb, utc, days=None):
    if crumb.instant is None:
        day = (days or {}).get(crumb.session)
        return f"{day:%Y-%m-%d}" if day is not None else "undated"
    zone = timezone.utc if utc else None
    return crumb.instant.astimezone(zone).strftime("%Y-%m-%d")


def _explain(crumb, found):
    """A sentence saying why a breadcrumb could not be followed to its turn."""
    pointer = f"{crumb.harness} {crumb.short} {crumb.when}"
    if found.state == "removed":
        return (
            f"{pointer}: the session was curated out of the corpus, and the trail "
            f"still relies on it. Keep the session, or drop the breadcrumb"
        )
    if found.state == "harness":
        return (
            f"{pointer}: the corpus holds this session as a {found.detail} "
            f"session, not {crumb.harness}"
        )
    if found.state == "no-turn":
        what = f"turn {crumb.turn}" if crumb.turn else f"at {crumb.stamp}"
        return (
            f"{pointer}: no author turn {what}; the session holds "
            f"{found.detail} author turn(s)"
        )
    if found.state == "crowded":
        return (
            f"{pointer}: turns {found.detail} were all logged then; write the "
            f"time to the millisecond"
        )
    return pointer


def _reader(crumb):
    """How to open the turn: with Quirework when captured, by hand otherwise."""
    turn = f"logged {crumb.stamp}" if crumb.stamp else str(crumb.turn)
    return f"find it: {crumb.harness} session {crumb.session}, the author's turn {turn}"


def _states(crumbs):
    """Where each of ``crumbs`` leads, by identity; ``None`` when provenance is off."""
    if not crumbs or not current_project().get("provenance.enabled", False):
        return None
    corpus, removed = Corpus(), _removed()
    return {id(crumb): locate(crumb, corpus, removed) for crumb in crumbs}


def _bound_by():
    out = {}
    for chapter, slugs in notes.chapter_bindings().items():
        for slug in slugs:
            out.setdefault(slug, set()).add(chapter)
    return out


def _tally(crumbs):
    kinds = {}
    for crumb in crumbs:
        kinds[crumb.kind] = kinds.get(crumb.kind, 0) + 1
    return " · ".join(f"{k} {kinds[k]}" for k in KINDS if k in kinds)


def _span(crumbs, utc, days=None):
    dated = (_date(c, utc, days) for c in crumbs)
    months = sorted({d[:7] for d in dated if d != "undated"})
    if not months:
        return "undated"
    return months[0] if len(months) == 1 else f"{months[0]} → {months[-1]}"


def _crumb_lines(crumb, found, *, brief, utc, days=None):
    turn = found.turn if found is not None else None
    if turn is not None:
        where = f"Human {turn.number}"
    elif found is None:
        where = "(sessions not read)"
    elif found.state == "uncaptured":
        where = "not in the corpus yet"
    else:
        where = "unfollowable"
    head = f"{_date(crumb, utc, days)}  {crumb.short:8}  {crumb.kind:8} {where}"
    if brief:
        gist = crumb.note or crumb.excerpt or (turn.text if turn else "")
        return [f"  {head}  {_opening(gist, 70)}" if gist else f"  {head}"]
    lines = [f"  {head}"]
    if crumb.note:
        lines.append(f"      note     {crumb.note}")
    if crumb.excerpt:
        lines.append(f"      excerpt  {crumb.excerpt}")
    elif turn is not None:
        lines.append(f"      opens    {_opening(turn.text, 200)}")
    if turn is not None:
        lines.append(
            f"      read     quirework provenance show {crumb.short} "
            f"--turn {turn.number}"
        )
    else:
        if found is not None and found.state not in ("uncaptured",):
            lines.append(f"      problem  {_explain(crumb, found)}")
        lines.append(f"      {_reader(crumb)}")
    return lines


def story(slug, *, brief=False, utc=False, sidecar=None, states=None):
    """One section's trail, oldest first, as printable lines."""
    titles = _spec_titles()
    _known_slug(slug, titles)
    sidecar = load() if sidecar is None else sidecar
    section = sidecar.section(slug)
    days = _days(section.crumbs) if section else {}
    crumbs = (
        sorted(section.crumbs, key=lambda c: _chronology(c, days)) if section else []
    )
    chapters = sorted(_bound_by().get(slug, ()))
    lines = [f"#{slug} — {titles[slug]}"]
    if chapters:
        lines.append(f"bound by: {', '.join(chapters)}")
    if not crumbs:
        lines.append("no breadcrumbs yet (quirework spec trail add adds one)")
        return lines
    sessions = len({c.session for c in crumbs})
    lines.append(
        f"{len(crumbs)} breadcrumb{'s' * (len(crumbs) != 1)} from {sessions} "
        f"session{'s' * (sessions != 1)}, {_span(crumbs, utc, days)} · "
        f"{_tally(crumbs)}"
    )
    states = _states(crumbs) if states is None else states
    for crumb in crumbs:
        found = states.get(id(crumb)) if states else None
        if not brief:
            lines.append("")
        lines += _crumb_lines(crumb, found, brief=brief, utc=utc, days=days)
    return lines


def overview(*, utc=False):
    sidecar = load()
    sections = notes.spec_sections()
    held = {s.slug: s for s in sidecar.sections if s.crumbs}
    days = _days([c for s in held.values() for c in s.crumbs])
    lines = [f"spec trail: {_rel(sidecar.path)}"]
    width = max((len(slug) for slug in held), default=0) + 1
    for slug in (s.slug for s in sections):
        if slug in held:
            crumbs = held[slug].crumbs
            lines.append(
                f"  #{slug:<{width}} {len(crumbs):>3}  {_span(crumbs, utc, days)}  "
                f"{_tally(crumbs)}"
            )
    lines.append(
        f"{len(held)} of {len(sections)} spec sections have a trail. "
        f"Read one: quirework spec trail SLUG"
    )
    return lines


def unit_view(chapter, *, utc=False):
    """The trails of every section a unit binds, one line per breadcrumb."""
    from quirework.manuscript import units

    bindings = notes.chapter_bindings()
    stems = [stem for stem in bindings if chapter == stem] or [
        stem for stem in bindings if chapter in stem
    ]
    if not stems:
        raise LookupError(
            f"no {units.noun()} matching '{chapter}' binds a spec section "
            f"({units.noun()}s with bindings: {', '.join(sorted(bindings))})"
        )
    if len(stems) > 1:
        raise LookupError(f"'{chapter}' matches {', '.join(sorted(stems))}")
    bound = bindings[stems[0]]
    sidecar = load()
    states = _states([c for s, c in sidecar.crumbs() if s.slug in bound]) or {}
    lines = [f"{stems[0]} binds {len(bound)} spec section(s)"]
    for slug in (s.slug for s in notes.spec_sections()):
        if slug in bound:
            lines.append("")
            lines += story(slug, brief=True, utc=utc, sidecar=sidecar, states=states)
    return lines


def session_view(prefix, *, utc=False):
    """Which sections a session fed, and where in it."""
    sidecar = load()
    cited = [(s, c) for s, c in sidecar.crumbs() if c.session.startswith(prefix)]
    if not cited:
        return [f"no breadcrumb points into a session matching '{prefix}'"]
    titles = _spec_titles()
    states = _states([c for _, c in cited]) or {}
    days = _days([c for _, c in cited])
    lines = []
    for section in sidecar.sections:
        mine = [c for s, c in cited if s is section]
        if mine:
            lines.append(f"#{section.slug} — {titles.get(section.slug, section.title)}")
            for crumb in sorted(mine, key=lambda c: _chronology(c, days)):
                found = states.get(id(crumb))
                lines += _crumb_lines(crumb, found, brief=True, utc=utc, days=days)
    return lines


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #
def check():
    """Every breadcrumb parses, mirrors the spec, and leads to its author turn.

    Fatal: a structure problem; a breadcrumb into a session curation removed;
    a time or turn matching no author turn, or several; an excerpt the turn
    does not hold. Advisory: a session not captured yet, or captured and not
    yet kept, which is the price of pointing at a conversation while it is
    still going on. Without provenance only the structure can be checked, and
    the report says so.
    """
    sidecar = load()
    problems = structure_problems(sidecar=sidecar)
    advisories = []
    project = current_project()
    if not project.get("provenance.enabled", False):
        if sidecar.crumbs():
            advisories.append(
                "  provenance is off, so no breadcrumb was followed into its session"
            )
    elif sidecar.crumbs():
        corpus, removed = Corpus(), _removed()
        kept = _kept_paths(corpus)
        live = None
        for section, crumb in sidecar.crumbs():
            found = locate(crumb, corpus, removed)
            at = f"{_rel(sidecar.path)}:{crumb.line}"
            if found.state == "uncaptured":
                live = corpus.live() if live is None else live
                still = (
                    "its live transcript is still on this machine"
                    if crumb.session in live
                    else "no live transcript for it is on this machine"
                )
                advisories.append(
                    f"  {at}: {crumb.harness} {crumb.short} is not in the corpus "
                    f"yet; {still}"
                )
            elif found.state != "turn":
                problems.append(f"{at}: {_explain(crumb, found)}")
            else:
                if crumb.excerpt and not contains(found.turn.text, crumb.excerpt):
                    problems.append(
                        f"{at}: the excerpt is not in {crumb.harness} "
                        f"{crumb.short} Human {found.turn.number}"
                    )
                if kept is not None and found.turn.path.resolve() not in kept:
                    advisories.append(
                        f"  {at}: {crumb.harness} {crumb.short} is captured but "
                        f"not yet kept in the corpus"
                    )
    if advisories:
        print(
            f"advisory ({len(advisories)} breadcrumb(s) could not be followed "
            f"all the way):",
            file=sys.stderr,
        )
        for line in advisories:
            print(line, file=sys.stderr)
    if problems:
        print(f"\n✗ trail --check: {len(problems)} problem(s):", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1
    count = len(sidecar.crumbs())
    print(f"✓ trail --check: {count} breadcrumb(s) in {_rel(sidecar.path)}.")
    return 0


def _kept_paths(corpus):
    """Captured files curation has accepted, or ``None`` when presence is kept."""
    from quirework.provenance.sources import accepted_files

    if current_project().get("provenance.curation", "tracked") == "presence":
        return None
    kept = set()
    directories = {
        (Path(p).parent, Path(p).suffix) for _, p in corpus.captured().values()
    }
    for directory, suffix in directories:
        kept |= {p.resolve() for p in accepted_files(directory, suffix)}
    return kept


# --------------------------------------------------------------------------- #
# backfill candidates
# --------------------------------------------------------------------------- #
_TRAILER = re.compile(r"(?im)^Claude-Session:\s*(\S+)\s*$")


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout


def _section_range(text, slug):
    """``(first, last)`` 1-based lines of the section anchored ``slug``."""
    masked = notes._mask_nonprose(notes._mask_fences(text))
    heads = [m for m in notes._HEADING.finditer(masked) if len(m.group(1)) == 2]
    for i, m in enumerate(heads):
        anchor = notes._ATTR_ID.search(m.group(3) or "")
        if anchor and anchor.group(1) == slug:
            first = text.count("\n", 0, m.start()) + 1
            last = (
                text.count("\n", 0, heads[i + 1].start())
                if i + 1 < len(heads)
                else text.count("\n") + (not text.endswith("\n"))
            )
            return first, last
    return None


def _commits(slug, *, patches=False):
    """The commits that changed the section, oldest first, or a reason there are none.

    Each is ``(sha, when, subject, body, patch)``; ``patch`` is the change to
    the section's lines when ``patches`` is asked for, and empty otherwise.
    """
    spec = notes.spec_file_of(slug)
    if spec is None:
        return [], f"'#{slug}' is in no spec file"
    spec = spec.resolve()
    try:
        root = Path(_git(spec.parent, "rev-parse", "--show-toplevel").strip())
    except (subprocess.CalledProcessError, OSError):
        return [], "the spec is not in a git repository"
    relative = spec.relative_to(root).as_posix()
    try:
        head = _git(root, "show", f"HEAD:{relative}")
    except subprocess.CalledProcessError:
        return [], "the spec has no committed history"
    lines = _section_range(head, notes.anchor_of(slug))
    if lines is None:
        return [], f"'#{slug}' is not in the committed spec yet"
    out = _git(
        root,
        "log",
        *(() if patches else ("-s",)),
        f"-L{lines[0]},{lines[1]}:{relative}",
        "--format=%x1e%H%x1f%aI%x1f%s%x1f%b%x1f",
    )
    commits = []
    for record in out.split("\x1e"):
        parts = record.strip("\n").split("\x1f")
        if len(parts) == 5:
            sha, when, subject, body, patch = parts
            commits.append(
                (sha, datetime.fromisoformat(when), subject, body, _changed(patch))
            )
    return commits[::-1], None


def _changed(patch):
    """The lines a patch removed and added, without its headers."""
    return "\n".join(
        line
        for line in patch.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    )


def _quotes(body):
    """The blockquotes in a commit body, the words a commit quoted."""
    blocks, current = [], []
    for line in body.splitlines():
        if line.startswith(">"):
            current.append(line[1:].strip())
        elif current:
            blocks.append(" ".join(current))
            current = []
    if current:
        blocks.append(" ".join(current))
    # A message written through a shell can keep its escapes: \" for ".
    blocks = [re.sub(r"\\([\"'])", r"\1", b) for b in blocks]
    return [b.strip().strip('"“”').strip() for b in blocks if b.strip()]


# A quote this short ("agreed", "go ahead") is in half the corpus, so it is
# looked for only in the sessions that made its commit, where it means what
# the commit says it meant.
QUOTE_MIN_WORDS = 4


def _strings(value):
    """Every string inside a JSON value, depth first."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _model_wrote(family, path, needles):
    """Which ``needles`` appear in text the model wrote in a session's log.

    A model writes a commit's subject when it makes the commit: in the shell
    command, in the message file it writes, or in the reply reporting it. A
    subject found only in a tool's output (a ``git log`` listing), in a message
    the harness queued, or in the author's own turn names a commit the session
    read about, not one it made.
    """
    found, wanted = set(), set(needles)

    def look(value):
        for text in _strings(value):
            for needle in wanted - found:
                if needle in text:
                    found.add(needle)

    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not any(n in line or json.dumps(n)[1:-1] in line for n in wanted):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            if family == "cc":
                if record.get("type") == "assistant":
                    look((record.get("message") or {}).get("content"))
            elif family == "codex":
                payload = record.get("payload")
                if record.get("type") == "response_item" and isinstance(payload, dict):
                    kind = payload.get("type")
                    if kind == "message" and payload.get("role") == "assistant":
                        look(payload.get("content"))
                    elif kind in ("function_call", "custom_tool_call", "action"):
                        look(payload)
            elif family == "agy":
                if record.get("type") == "PLANNER_RESPONSE":
                    look([record.get("content"), record.get("tool_calls")])
            if found == wanted:
                break
    return found


@dataclass(frozen=True)
class CommitLead:
    """One commit that changed a section, and the author's turns it leads to.

    ``sessions`` holds ``(sid, family, turns)`` for each session that made the
    commit, with the author's last three turns before it; ``how`` says how
    that session was recognized. ``quotes`` holds ``(quote, turns, note)`` for
    each passage the commit quoted, with the turns found holding it and, when
    the search was narrowed, why. ``patch`` is the commit's change to the
    section, when it was asked for.
    """

    sha: str
    when: datetime
    subject: str
    how: str | None
    sessions: tuple
    quotes: tuple
    patch: str = ""


def suggest_leads(slug, corpus=None, *, patches=False):
    """``(leads, reason)``: the commits that changed a section, as data.

    The commits lead to the sessions that made them, found by the commit's
    subject in what the session's model wrote or by its session trailer, and
    to any author words the commit quoted. ``reason`` says why there is no
    history to follow, when there is none. Nothing is written.
    """
    corpus = corpus or Corpus()
    commits, reason = _commits(slug, patches=patches)
    # Everything this section's history will look for, found in one pass: a
    # commit's subject as its own commit command wrote it into the log, its
    # session trailer, and a probe word for each passage it quoted.
    tools = {
        k: v for k, v in corpus.captured().items() if v[0] in ("cc", "codex", "agy")
    }
    asks = {}
    for sha, _, subject, body, _ in commits:
        asks[sha] = {
            "subject": {
                json.dumps(subject)[1:-1],
                json.dumps(subject, ensure_ascii=False)[1:-1],
            },
            "links": set(_TRAILER.findall(body)),
            "quotes": _quotes(body),
        }
    made_by = corpus.holding(
        {n for ask in asks.values() for n in ask["subject"] | ask["links"]}, tools
    )
    # A raw hit is only where to look: the session made the commit if its own
    # model wrote the subject, which a listing of the history never does.
    subjects = {sha: subject for sha, _, subject, _, _ in commits}
    for sha, ask in asks.items():
        hits = set().union(*(made_by[n] for n in ask["subject"]))
        for sid in hits:
            if not _model_wrote(*tools[sid], [subjects[sha]]):
                for needle in ask["subject"]:
                    made_by[needle].discard(sid)
    quotes = [
        variant
        for ask in asks.values()
        for quote in ask["quotes"]
        for variant in (quote, " ".join(quote.split()[:8]))
    ]
    probes = corpus.holding({_probe(q) for q in quotes} - {None})
    leads = []
    for sha, when, subject, body, patch in commits:
        ask = asks[sha]
        made = set().union(*(made_by[n] for n in ask["subject"]))
        how = "by the commit's subject"
        if not made and ask["links"]:
            made = set().union(*(made_by[n] for n in ask["links"]))
            how = "by its session trailer"
        sessions = []
        for sid in sorted(made):
            family, path = tools[sid]
            turns = corpus.turns(sid, family, path)
            before = [t for t in turns if t.logged is not None and t.logged <= when]
            sessions.append((sid, family, tuple(before[-3:])))
        found = []
        for quote in ask["quotes"]:
            if len(quote.split()) < QUOTE_MIN_WORDS:
                hits = [
                    t
                    for sid, family, _ in sessions
                    for t in corpus.turns(sid, family, tools[sid][1])
                    if contains(t.text, quote)
                ]
                note = (
                    "too short to look for beyond the session that made the commit"
                    if sessions
                    else "too short to look for, and no session made the commit"
                )
                found.append((quote, tuple(hits[:5]), note))
                continue
            hits = []
            for variant in (quote, " ".join(quote.split()[:8])):
                probe = _probe(variant)
                within = probes[probe] if probe else None
                hits = corpus.find(variant, within=within, live=False)
                if hits:
                    break
            found.append((quote, tuple(hits[:5]), ""))
        leads.append(
            CommitLead(
                sha,
                when,
                subject,
                how if made else None,
                tuple(sessions),
                tuple(found),
                patch,
            )
        )
    return leads, reason


# How much of a commit's change to the section `suggest --diff` prints.
DIFF_LINES = 40


def suggest(slug, *, utc=False, diff=False):
    """Candidate turns for a section's backfill, each with why it was suggested.

    ``suggest_leads`` as the lines an agent reads. With ``diff``, each commit
    also shows what it changed in the section, which is what tells a ruler
    which paragraph a session's turns went into. These are leads for an
    agent to read, never breadcrumbs: nothing is written.
    """
    titles = _spec_titles()
    _known_slug(slug, titles)
    leads, reason = suggest_leads(slug, patches=diff)
    on_trail = [c for s, c in load().crumbs() if s.slug == slug]
    lines = [f"#{slug} — {titles[slug]}"]
    if reason:
        lines.append(f"no commit history to follow: {reason}.")
    else:
        lines.append(
            f"{len(leads)} commit(s) changed this section, oldest first. These "
            f"are leads, not breadcrumbs: read each turn before adding it."
        )
    zone = timezone.utc if utc else None
    for lead in leads:
        lines += [
            "",
            f"{lead.when.astimezone(zone):%Y-%m-%d}  {lead.sha[:8]}  {lead.subject}",
        ]
        for sid, family, before in lead.sessions:
            lines.append(f"  made in {_HARNESS_OF[family]} {sid[:8]} ({lead.how})")
            for t in before:
                lines.append("    " + _lead(t, on_trail, zone))
        if not lead.sessions:
            lines.append("  made in: no captured session names this commit")
        for quote, hits, note in lead.quotes:
            label = _opening(quote, 60)
            if note:
                lines.append(f"  quoted “{label}”: {note}")
            elif not hits:
                lines.append(f"  quoted “{label}”: not found in the author's turns")
            for t in hits:
                if not note:
                    lines.append(f"  quoted “{label}”")
                lines.append("    " + _lead(t, on_trail, zone))
        if diff and lead.patch:
            changed = lead.patch.splitlines()
            lines.append("  changed in the section:")
            lines += [f"    {line}" for line in changed[:DIFF_LINES]]
            if len(changed) > DIFF_LINES:
                lines.append(f"    … {len(changed) - DIFF_LINES} more line(s)")
    lines.append("")
    lines.append(
        "Words the author used without a commit behind them: quirework "
        "provenance search WORDS, and --semantic 'the idea in plain words'."
    )
    return lines


def _points_at(crumb, turn):
    if crumb.session != turn.session:
        return False
    if crumb.stamp:
        return turn.logged is not None and crumb.holds(turn.logged)
    return crumb.turn == turn.number


def _lead(turn, on_trail, zone):
    stamp = f"{turn.logged.astimezone(zone):%Y-%m-%d %H:%M}" if turn.logged else ""
    mark = "  (on the trail)" if any(_points_at(c, turn) for c in on_trail) else ""
    return (
        f"{turn.harness} {turn.short} Human {turn.number} · {stamp}{mark}  "
        f"{_opening(turn.text, 90)}"
    )


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #
USAGE_EPILOG = """\
views:
  quirework spec trail                      which sections have a trail
  quirework spec trail SLUG                 one section's trail, oldest first
  quirework spec trail SLUG --brief         one line per breadcrumb
  quirework spec trail --unit U             the trails of every section U binds
  quirework spec trail --session ID         which sections a session fed
  quirework spec trail --check              the gate

writing:
  quirework spec trail add SLUG --kind K --find PHRASE [--session ID]
  quirework spec trail add SLUG --kind K --session ID --turn N
  quirework spec trail add SLUG --kind K --harness H --session ID --at TIME
      options: --note TEXT (your reading of the turn), --excerpt TEXT
      (words the turn holds, when they would be buried in it)

backfill:
  quirework spec trail suggest SLUG         leads from the commits that
                                            changed the section; --diff adds
                                            what each one changed in it
  quirework spec trail backfill             cheap readers flag, a strong
                                            reader rules: see its help

kinds, what the turn did to the section:
""" + "\n".join(f"  {k:<8}  {v}" for k, v in KINDS.items())


def _add_main(argv):
    ap = argparse.ArgumentParser(
        prog="quirework spec trail add",
        description="Put a breadcrumb to one of the author's turns on a "
        "spec section's trail.",
    )
    ap.add_argument("slug", help="the spec section, by its {#slug}")
    ap.add_argument("--kind", required=True, choices=tuple(KINDS))
    ap.add_argument(
        "--find",
        metavar="PHRASE",
        help="words the author typed in the turn; found in the corpus, then in "
        "this project's live Claude Code and Codex transcripts",
    )
    ap.add_argument("--session", metavar="ID", help="a session id or its prefix")
    ap.add_argument("--turn", type=int, metavar="N", help="the author's turn number")
    ap.add_argument(
        "--at", metavar="TIME", help="the turn's logged time, for a pointer by hand"
    )
    ap.add_argument(
        "--harness", choices=tuple(HARNESSES), help="with --at: where the session ran"
    )
    ap.add_argument("--note", default="", help="the agent's reading of the turn")
    ap.add_argument(
        "--excerpt", default="", help="words the turn holds, to highlight them"
    )
    args = ap.parse_args(argv)
    try:
        crumb, target = add(
            args.slug,
            args.kind,
            find=args.find,
            session=args.session,
            turn=args.turn,
            at=args.at,
            harness=args.harness,
            note=args.note,
            excerpt=args.excerpt,
        )
    except (LookupError, ValueError) as exc:
        print(f"trail add: {exc}", file=sys.stderr)
        return 2
    print(f"added to #{args.slug}: {crumb.header()[2:]}")
    if target is None:
        print("  not verified: Quirework cannot read this session")
    elif target.live:
        print(
            f"  Human {target.number}, read from the live transcript: the "
            f"session is not in the corpus yet, so capture it before relying "
            f"on this breadcrumb"
        )
    else:
        print(
            f"  Human {target.number}: quirework provenance show {crumb.short} "
            f"--turn {target.number}"
        )
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "add":
        return _add_main(argv[1:])
    if argv and argv[0] == "backfill":
        from quirework.manuscript import backfill

        return backfill.main(argv[1:])
    ap = argparse.ArgumentParser(
        prog="quirework spec trail",
        description="Read, check and keep the spec's trail sidecar: pointers "
        "into the sessions where the author shaped each spec section.",
        epilog=USAGE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("slug", nargs="?", help="a spec section, or 'suggest SLUG'")
    ap.add_argument("rest", nargs="?", help=argparse.SUPPRESS)
    ap.add_argument("--brief", action="store_true", help="one line per breadcrumb")
    ap.add_argument("-c", "--unit", metavar="U", help="every section a unit binds")
    ap.add_argument("--session", metavar="ID", help="which sections a session fed")
    ap.add_argument("--check", action="store_true", help="the trail's gate")
    ap.add_argument("--utc", action="store_true", help="date turns in UTC")
    ap.add_argument(
        "--diff",
        action="store_true",
        help="with suggest: what each commit changed in the section",
    )
    args = ap.parse_args(argv)
    try:
        sidecar_path()
        if args.slug == "suggest":
            if not args.rest:
                ap.error("suggest needs a SLUG")
            lines = suggest(args.rest, utc=args.utc, diff=args.diff)
        elif args.rest:
            ap.error(f"unexpected argument: {args.rest}")
        elif args.diff:
            ap.error("--diff goes with suggest SLUG")
        elif args.check:
            if args.slug or args.unit or args.session or args.brief:
                ap.error("--check runs the gate and takes no view options")
            return check()
        elif args.unit:
            lines = unit_view(args.unit, utc=args.utc)
        elif args.session:
            lines = session_view(args.session, utc=args.utc)
        elif args.slug:
            lines = story(args.slug, brief=args.brief, utc=args.utc)
            lines += ["", _CONTEXT]
        else:
            lines = overview(utc=args.utc)
    except LookupError as exc:
        print(f"trail: {exc}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
