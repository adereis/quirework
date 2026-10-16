"""A copy on a Kindle, and the notes made on it brought back as review marks.

`publish copy` makes a numbered proof for one reader. When that reader reads
on a Kindle, the copy has to reach the reader's library, then the devices,
and the highlights and notes made on it are marks on the book as surely as a
red pen's. This module is the record-keeping and the reading for that loop.
It never talks to the store itself.

The store's pages do that, from a browser tab the reader is already signed
into. `kindle.js` beside this module is a script for such a tab: it uploads
the copy through Send to Kindle, lists the library and the reading devices,
and delivers a document to a device or takes it off one, using the calls
those pages make themselves. A session that can run script in the reader's
browser injects it (`driver` prints its path). The signed-in session is the
only credential, and nothing here reads one.

The loop has an order, because the store keeps a document's notes with the
document. Sending a new copy creates a new document with none, so the notes
on the copy it replaces live only on the old one:

1. `plan` says what to do: the copy to send, its fingerprint and the title
   the library should show, the devices to deliver it to, and each earlier
   copy still in the library, with whether its notes have been read back.
2. The copy is sent and delivered from the browser, and `record` logs each
   step in the log of copies, beside the copy's number and fingerprint.
3. The notes on an earlier copy are exported from the Kindle app as a
   notebook, and `notes` reads the export, finds each highlight in the
   manuscript, and archives the marks where the project keeps red-pen
   rounds.
4. Only then may the earlier copy leave the devices. The plan says which
   may; one whose notes were neither read nor declared absent may not.

No step deletes a document from the library, for deletion is permanent and
takes the notes with it. Leaving the devices keeps the document, and its
notes, in the library until the reader deletes it there.

The devices are chosen by rules in the per-user configuration,
`kindle.targets`, so that a reader who replaces a phone does not edit a
project: a rule names a kind of device as the store does ("Kindle for
Android") or one device by name, and takes the most recently registered
match unless it says `pick = "all"`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from quirework.project import current_project, user_config_path, user_settings
from quirework.publish import proof as _proof
from quirework.publish import redpen as _redpen

DRIVER = Path(__file__).with_name("kindle.js")

# The log's events for this loop. `sent` and `delivered` say where a copy
# went; `removed` that it left some devices; `notes` that its notes were read
# back and archived; `notes-waived` that the reader declared there were none
# worth keeping. Only the last two let a copy leave the devices.
EVENTS = ("sent", "delivered", "removed", "notes-waived")
PREFIX = "kindle-"

HELP = """\
usage: quirework publish kindle COMMAND [options]

Put a copy made by `publish copy` into a Kindle library, deliver it to the
reading devices the per-user configuration names, and read the highlights
and notes made on it back as review marks. The store's pages are driven
from a signed-in browser tab by the script `driver` names; this command
plans each step and keeps the record in the log of copies.

commands:
  driver                 print the path of the script for the browser tab
  plan [--copy N] [--snapshot FILE]
                         what to send, where to deliver it, and which
                         earlier copies may leave the devices, as JSON;
                         FILE is what the script's snapshot() returned
  record EVENT --copy N [--device NAME ...]
                         log a step done in the browser: sent, delivered,
                         removed, or notes-waived (no notes worth keeping)
  notes EXPORT --copy N [--out PATH] [--dated YYYY-MM-DD]
                         read a notebook the Kindle app exported, find
                         each highlight in the manuscript, and archive the
                         marks beside the red-pen rounds

options:
  -h, --help             show this help message and exit

The devices come from `kindle.targets` in ~/.config/quirework/config.toml,
a list of rules such as { kind = "Kindle for Android" } or { name = "..." }.
Each takes the most recently registered match unless it says pick = "all".
No command deletes a document from the library: that is permanent, it takes
the document's notes with it, and it is left to the reader.
"""


# --------------------------------------------------------------------------- #
# the log of copies
# --------------------------------------------------------------------------- #
@dataclass
class Copy:
    number: int
    commit: str | None = None
    files: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

    @property
    def epub(self) -> dict | None:
        return next((f for f in self.files if f["name"].endswith(".epub")), None)

    def last(self, event: str) -> dict | None:
        found = [e for e in self.events if e["event"] == PREFIX + event]
        return found[-1] if found else None

    @property
    def notes(self) -> str:
        """`kept`, `waived` or `missing`: whether the notes were dealt with."""
        if self.last("notes"):
            return "kept"
        if self.last("notes-waived"):
            return "waived"
        return "missing"


def copies() -> dict[int, Copy]:
    """Every issued copy in the log, by number, with its Kindle events."""
    log = _proof._log_path()
    if not log.exists():
        return {}
    with _proof._locked(log) as handle:
        records = _proof._records(handle)
    found: dict[int, Copy] = {}
    for record in records:
        number = record["copy"]
        entry = found.setdefault(number, Copy(number))
        event = record.get("event")
        if event == "started":
            entry.commit = record.get("commit")
        elif event == "issued":
            entry.files = list(record.get("files") or [])
        elif isinstance(event, str) and event.startswith(PREFIX):
            entry.events.append(record)
    return {n: c for n, c in found.items() if c.files}


def _copy_file(entry: Copy) -> Path:
    """The copy's EPUB on disk, refused when its bytes are not the logged ones."""
    record = entry.epub
    if record is None:
        raise ValueError(f"copy {entry.number} was made without an EPUB")
    path = current_project().path("publish.build", "build") / "copies" / record["name"]
    if not path.is_file():
        raise ValueError(
            f"copy {entry.number}'s EPUB is not in {path.parent}; a copy made in "
            "another checkout is sent from there"
        )
    if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
        raise ValueError(
            f"{path.name} is not the file the log issued as copy {entry.number}; "
            "a numbered copy is never changed after it is made"
        )
    return path


# --------------------------------------------------------------------------- #
# what the EPUB says it is
# --------------------------------------------------------------------------- #
_OPF = "{http://www.idpf.org/2007/opf}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_CONTAINER = "{urn:oasis:names:tc:opendocument:xmlns:container}"


def identity(epub: Path) -> tuple[str, str]:
    """The EPUB's library title and author, as its package document gives them.

    A proof's library title says what it is and carries its number, so the
    same title is what the Kindle library shows.
    """
    with zipfile.ZipFile(epub) as z:
        container = ET.fromstring(z.read("META-INF/container.xml"))
        rootfile = container.find(f".//{_CONTAINER}rootfile")
        if rootfile is None:
            raise ValueError(f"{epub.name} names no package document")
        package = ET.fromstring(z.read(rootfile.get("full-path")))
    metadata = package.find(f"{_OPF}metadata")
    title = metadata.findtext(f"{_DC}title") if metadata is not None else None
    author = metadata.findtext(f"{_DC}creator") if metadata is not None else None
    if not title or not title.strip():
        raise ValueError(f"{epub.name} has no title")
    return " ".join(title.split()), " ".join((author or "").split())


def comparable(title: str) -> str:
    """A title as the store and this module can both compare it.

    The store hands titles back with markup escaped, and in some lists with
    a file name's look-alike characters (a title's parentheses come back as
    U+FE59 and U+FE5A); compatibility normalization folds those back.
    """
    text = unicodedata.normalize("NFKC", html.unescape(title or ""))
    return " ".join(text.split()).casefold()


def known_titles(entry: Copy) -> set[str]:
    """Every title under which the library may hold this copy."""
    names = set()
    sent = entry.last("sent")
    if sent and sent.get("title"):
        names.add(sent["title"])
    record = entry.epub
    if record is not None:
        names.add(Path(record["name"]).stem)
        path = (
            current_project().path("publish.build", "build") / "copies" / record["name"]
        )
        if path.is_file():
            names.add(identity(path)[0])
    return {comparable(n) for n in names}


# --------------------------------------------------------------------------- #
# choosing the devices
# --------------------------------------------------------------------------- #
def targets() -> list[dict]:
    """The device rules in the per-user configuration, checked."""
    rules = user_settings().get("kindle", {}).get("targets")
    where = user_config_path()
    if rules is None:
        raise ValueError(
            f"set kindle.targets in {where} to the reading devices a copy goes "
            'to, for example [{ kind = "Kindle for Android" }]'
        )
    if not isinstance(rules, list) or not rules:
        raise ValueError(f"kindle.targets in {where} must be a list of rules")
    for rule in rules:
        if not isinstance(rule, dict) or not {"name", "kind"} & set(rule):
            raise ValueError(
                f"each rule in kindle.targets names a kind or a name: {rule!r}"
            )
        unknown = set(rule) - {"name", "kind", "pick"}
        if unknown:
            raise ValueError(f"kindle.targets has no field {sorted(unknown)[0]!r}")
        if rule.get("pick", "latest") not in ("latest", "all"):
            raise ValueError(
                f'pick is "latest" or "all" in kindle.targets, not {rule["pick"]!r}'
            )
    return rules


def resolve(rules: list[dict], devices: list[dict]) -> list[dict]:
    """The devices the rules choose, each once, in the rules' order.

    A rule that matches no device stops the plan: a copy that silently
    skips a device the reader expects it on reads, on that device, as a
    copy that never came.
    """
    chosen: list[dict] = []
    for rule in rules:
        matches = [
            d
            for d in devices
            if all(d.get(k) == rule[k] for k in ("name", "kind") if k in rule)
        ]
        if not matches:
            described = ", ".join(
                f"{k} {rule[k]!r}" for k in ("kind", "name") if k in rule
            )
            raise ValueError(f"no reading device in the library matches {described}")
        if rule.get("pick", "latest") == "latest":
            matches = [max(matches, key=lambda d: d.get("registered") or "")]
        for d in matches:
            device = {"name": d["name"], "kind": d["kind"]}
            if device not in chosen:
                chosen.append(device)
    return chosen


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #
def _head() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=current_project().root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _snapshot(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read the snapshot {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        raise ValueError(f"{path} is not what the script's snapshot() returns")
    if not isinstance(data.get("library"), list):
        raise ValueError(f"{path} lists no library")
    return data


def plan(number: int | None = None, snapshot: Path | None = None) -> dict:
    found = copies()
    if not found:
        raise ValueError(
            "the log of copies holds no copy yet; make one with publish copy"
        )
    if number is None:
        with_epub = [n for n, c in found.items() if c.epub]
        if not with_epub:
            raise ValueError("no copy in the log was made with an EPUB")
        number = max(with_epub)
    if number not in found:
        raise ValueError(f"the log of copies holds no copy {number}")
    entry = found[number]
    path = _copy_file(entry)
    title, author = identity(path)
    result: dict = {
        "copy": number,
        "commit": entry.commit,
        "at_head": entry.commit is not None and entry.commit == _head(),
        "file": str(path),
        "bytes": path.stat().st_size,
        "sha256": entry.epub["sha256"],
        "title": title,
        "author": author,
        "notes_on_this_copy": entry.notes,
    }
    rules = targets()
    if snapshot is None:
        result["targets"] = rules
        return result

    data = _snapshot(snapshot)
    deliver_to = resolve(rules, data["devices"])
    mine = comparable(title)
    titled = {n: known_titles(c) for n, c in found.items()}
    in_library, earlier, unrecognized = None, [], []
    for doc in data["library"]:
        name = doc.get("title") or ""
        key = comparable(name)
        holders = [
            {"name": d["name"], "kind": d["kind"]} for d in doc.get("devices") or []
        ]
        if key == mine:
            in_library = {"title": name, "on": holders}
            continue
        owner = next((n for n, names in titled.items() if key in names), None)
        if owner is None:
            unrecognized.append(name)
            continue
        notes = found[owner].notes
        earlier.append(
            {
                "copy": owner,
                "title": name,
                "on": holders,
                "notes": notes,
                "may_leave_devices": notes != "missing",
            }
        )
    # The store counts a device as holding a document once the device has
    # downloaded it, which waits for the device's next sync; a delivery the
    # log already records is not asked for again.
    holding = in_library["on"] if in_library else []
    asked = {
        n
        for e in entry.events
        if e["event"] == PREFIX + "delivered"
        for n in e.get("devices") or []
    }
    result["send"] = in_library is None
    result["deliver_to"] = deliver_to
    result["on"] = holding
    result["deliver"] = [
        d for d in deliver_to if d not in holding and d["name"] not in asked
    ]
    result["earlier"] = sorted(earlier, key=lambda e: e["copy"])
    result["unrecognized"] = unrecognized
    return result


# --------------------------------------------------------------------------- #
# record
# --------------------------------------------------------------------------- #
def record(
    event: str, number: int, devices: list[str] | None = None, reason: str | None = None
) -> dict:
    if event not in EVENTS:
        raise ValueError(f"record one of {', '.join(EVENTS)}, not {event!r}")
    found = copies()
    if number not in found:
        raise ValueError(f"the log of copies holds no copy {number}")
    entry = found[number]
    row: dict = {"copy": number, "event": PREFIX + event}
    if event == "sent":
        title, _ = identity(_copy_file(entry))
        row["title"] = title
        row["sha256"] = entry.epub["sha256"]
    elif event in ("delivered", "removed"):
        if not devices:
            raise ValueError(f"name the devices the copy was {event} on: --device NAME")
        row["devices"] = devices
    elif reason:
        row["reason"] = reason
    row["at"] = _proof._now()
    _proof._append(_proof._log_path(), row)
    return row


# --------------------------------------------------------------------------- #
# reading a notebook export
# --------------------------------------------------------------------------- #
@dataclass
class Mark:
    kind: str  # "highlight", "note" or "bookmark"
    heading: str  # the export's own line, kept verbatim
    section: str
    color: str | None = None
    page: str | None = None
    location: str | None = None
    text: str = ""
    note: str | None = None  # a note the reader attached to this highlight
    source: str = "unlocated"


@dataclass
class Notebook:
    title: str
    authors: str
    marks: list[Mark]


_CLASSES = ("bookTitle", "authors", "sectionHeading", "noteHeading", "noteText")
_BLOCK = {"div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "td", "li"}
_HEADING = re.compile(
    r"^(?P<kind>Highlight|Note|Bookmark)\s*(?:\((?P<color>[^)]*)\))?\s*-\s*(?P<where>.*)$",
    re.I,
)
_LOCATION = re.compile(r"\bLocation\s+([\d,]+(?:-[\d,]+)?)", re.I)
_PAGE = re.compile(r"\bPage\s+([\w.-]+)", re.I)


class _Export(HTMLParser):
    """The notebook's entries, in order, as (class, text).

    An exported notebook marks each entry with a class, and its markup is
    not well formed: an entry may close with a tag that never opened. So an
    entry runs from its classed tag to the next block's end or the next
    classed tag, whichever comes first.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str]] = []
        self._class: str | None = None
        self._text: list[str] = []

    def _flush(self):
        if self._class is not None:
            self.items.append((self._class, "".join(self._text)))
        self._class, self._text = None, []

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        hit = next((c for c in classes if c in _CLASSES), None)
        if hit:
            self._flush()
            self._class = hit
        elif tag == "br" and self._class:
            self._text.append("\n")

    def handle_endtag(self, tag):
        if tag in _BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._class:
            self._text.append(data)

    def close(self):
        super().close()
        self._flush()


def _clean(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def parse(export: Path) -> Notebook:
    """The marks in an exported Kindle notebook, a note joined to its highlight."""
    raw = export.read_bytes()
    parser = _Export()
    parser.feed(raw.decode("utf-8", errors="replace"))
    parser.close()
    items = [(cls, _clean(text)) for cls, text in parser.items]
    title = next((t for c, t in items if c == "bookTitle"), "")
    if not title or not any(c == "noteHeading" for c, _ in items):
        raise ValueError(
            f"{export.name} is not a notebook exported by a Kindle app: it has no "
            "book title or no highlight or note"
        )
    authors = next((t for c, t in items if c == "authors"), "")
    marks: list[Mark] = []
    section = ""
    pending: Mark | None = None
    for cls, text in items:
        if cls == "sectionHeading":
            section = text
        elif cls == "noteHeading":
            match = _HEADING.match(text)
            if not match:
                raise ValueError(f"{export.name}: an entry this cannot read: {text!r}")
            where = match.group("where")
            loc, page = _LOCATION.search(where), _PAGE.search(where)
            pending = Mark(
                kind=match.group("kind").lower(),
                heading=text,
                section=section,
                color=(match.group("color") or "").strip().lower() or None,
                page=page.group(1) if page else None,
                location=loc.group(1).replace(",", "") if loc else None,
            )
            marks.append(pending)
        elif cls == "noteText":
            if pending is None:
                raise ValueError(f"{export.name}: text before any entry: {text[:60]!r}")
            pending.text = text
            pending = None
    return Notebook(title, authors, _join_notes(marks))


def _join_notes(marks: list[Mark]) -> list[Mark]:
    """Attach each note to the highlight it was written on.

    The app writes a note as its own entry right after the highlight it was
    made on, at the same location. A note anywhere else stands alone.
    """
    out: list[Mark] = []
    for mark in marks:
        before = out[-1] if out else None
        if (
            mark.kind == "note"
            and before is not None
            and before.kind == "highlight"
            and before.note is None
            and mark.location is not None
            and mark.location == _end(before.location)
        ):
            before.note = mark.text
            continue
        out.append(mark)
    return out


def _end(location: str | None) -> str | None:
    return location.split("-")[-1] if location else None


# --------------------------------------------------------------------------- #
# finding a highlight in the manuscript
# --------------------------------------------------------------------------- #
_ATTRS = re.compile(r"\{[#.][^}]*\}")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_SPAN = re.compile(r"\[([^\]]*)\]\{[^}]*\}")
_FOOTREF = re.compile(r"\[\^[^\]]+\]:?")
_TAG = re.compile(r"<[^>]+>")
_MARKUP = re.compile(r"(?<!\w)[*_]+|[*_]+(?!\w)|`")
_LEAD = re.compile(r"^\s*(?:#+\s+|>\s*|[-*+]\s+|\d+\.\s+|:::.*$)")
_FOOTNUM = re.compile(r"(?<=[.,;:!?)\]\"'])\d{1,3}(?=\s|$)")


def _plain(line: str) -> str:
    """A source line as a reader sees it: the markup gone, the words kept."""
    s = _LEAD.sub("", line)
    s = _ATTRS.sub("", s)
    s = _LINK.sub(r"\1", s)
    s = _SPAN.sub(r"\1", s)
    s = _FOOTREF.sub("", s)
    s = _TAG.sub("", s)
    s = _MARKUP.sub("", s)
    return s


def _norm(text: str) -> str:
    return _redpen._norm(unicodedata.normalize("NFKC", text).replace("­", ""))


@dataclass
class _Text:
    """One source file's words as a single string, with each line's span."""

    rel: str
    text: str
    spans: list[tuple[int, int, int]]  # (start, end, line number)

    def line_at(self, offset: int) -> int:
        for start, end, number in self.spans:
            if offset < end:
                return number
        return self.spans[-1][2]


def _index(files: list[tuple[str, str]]) -> list[_Text]:
    out = []
    for rel, body in files:
        parts, spans, offset, comment = [], [], 0, False
        for number, line in enumerate(body.splitlines(), 1):
            # An HTML comment carries the book's marks, not its words, and
            # may run over several lines.
            if comment:
                if "-->" in line:
                    comment, line = False, line.split("-->", 1)[1]
                else:
                    continue
            line = re.sub(r"<!--.*?-->", "", line)
            if "<!--" in line:
                line, comment = line.split("<!--", 1)[0], True
            if line.lstrip().startswith(("```", "![")):
                continue
            words = _norm(_plain(line))
            if not words:
                continue
            spans.append((offset, offset + len(words) + 1, number))
            parts.append(words)
            offset += len(words) + 1
        if parts:
            out.append(_Text(rel, " ".join(parts), spans))
    return out


def _span(t: _Text, start: int, end: int) -> str:
    lo, hi = t.line_at(start), t.line_at(max(start, end - 1))
    return f"{t.rel}:{lo}" if lo == hi else f"{t.rel}:{lo}-{hi}"


def locate(text: str, index: list[_Text]) -> str | None:
    """Where a highlight sits, as `path:line` or `path:first-last`, or None.

    A highlight is the words as the reader saw them, which may cross
    several of the source's one-sentence lines, so each file is searched as
    one string. Where the whole run is not found, perhaps because a
    footnote's number was swept into it, its two ends are, close together.
    """
    t = _norm(text)
    if len(t) < 8:
        return None
    # A footnote's number renders as digits glued to the punctuation they
    # follow, and comes along when the reader highlights across it.
    for candidate in dict.fromkeys((t, _FOOTNUM.sub("", t))):
        for doc in index:
            at = doc.text.find(candidate)
            if at >= 0:
                return _span(doc, at, at + len(candidate))
    if len(t) < 60:
        return None
    head, tail = t[:40], t[-40:]
    for doc in index:
        at = doc.text.find(head)
        if at < 0:
            continue
        end = doc.text.find(tail, at + len(head))
        if 0 <= end - at <= len(t) * 2:
            return "near " + _span(doc, at, end + len(tail))
    return None


def _files_now(chapters: Path) -> list[tuple[str, str]]:
    root = current_project().root
    out = []
    for path in _redpen._in_reading_order(chapters):
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = path.name
        out.append((rel, path.read_text(encoding="utf-8")))
    return out


def _files_at(commit: str, chapters: Path) -> list[tuple[str, str]]:
    root = current_project().root
    try:
        rel_dir = str(chapters.relative_to(root))
        names = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", commit, "--", rel_dir],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (ValueError, OSError, subprocess.CalledProcessError):
        return []
    out = []
    for name in sorted(n for n in names if n.endswith(".qmd")):
        shown = subprocess.run(
            ["git", "show", f"{commit}:{name}"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if shown.returncode == 0:
            out.append((name, shown.stdout))
    return out


def place(marks: list[Mark], chapters: Path, commit: str | None) -> None:
    """Find each mark in the manuscript as it stands, else as the copy had it.

    A highlight is read against the current text, where any change will be
    made. Text revised since the copy was made is found in the copy's own
    commit instead, and the mark says so, since the line it names is then
    history rather than a place to edit.
    """
    now = _index(_files_now(chapters))
    then = None
    for mark in marks:
        if not mark.text or mark.kind == "note":
            continue
        where = locate(mark.text, now)
        if where is None and commit:
            if then is None:
                then = _index(_files_at(commit, chapters))
            earlier = locate(mark.text, then)
            if earlier is not None:
                where = f"{earlier} in copy's commit {commit[:8]}, changed since"
        mark.source = where or "unlocated"


# --------------------------------------------------------------------------- #
# the archive
# --------------------------------------------------------------------------- #
def _anchor_tally(marks: list[Mark]) -> tuple[int, int, int, int]:
    exact = near = earlier = lost = 0
    for m in marks:
        if not m.text or m.kind == "note":
            continue
        if m.source == "unlocated":
            lost += 1
        elif "changed since" in m.source:
            earlier += 1
        elif m.source.startswith("near "):
            near += 1
        else:
            exact += 1
    return exact, near, earlier, lost


def _entry(mark: Mark, dated: str) -> str:
    """One mark as one block, with no blank line inside it: a reader of the
    archive takes each blank-line-separated block for one of the reader's
    marks."""
    what = [mark.kind]
    if mark.color:
        what.append(mark.color)
    if mark.location:
        what.append(f"location {mark.location}")
    if mark.page:
        what.append(f"page {mark.page}")
    lines = [f"[Kindle] {dated}  ({', '.join(what)})"]
    if mark.section:
        lines.append(f"  section: {mark.section}")
    if mark.kind == "note":
        lines.append(f"  note: {_indent(mark.text)}")
        lines.append("  -> a note on no highlight; find it by its location")
        return "\n".join(lines)
    if mark.text:
        lines.append(f"  highlight: “{_indent(mark.text)}”")
        lines.append(f"  -> {mark.source}")
    if mark.note:
        lines.append(f"  note: {_indent(mark.note)}")
    return "\n".join(lines)


def _indent(text: str) -> str:
    return "\n    ".join(text.splitlines())


def _chapter_of(mark: Mark) -> str:
    match = re.match(r"(?:near )?([^:\s]+):\d", mark.source)
    return match.group(1) if match and "changed since" not in mark.source else ""


def _archive_dir() -> Path:
    project = current_project()
    if project.get("provenance.redpen") is None:
        raise ValueError(
            "the project declares no provenance.redpen to archive the marks in; "
            "name a file with --out"
        )
    return project.path("provenance.redpen")


def notes(
    export: Path, number: int, out: Path | None = None, dated: str | None = None
) -> int:
    found = copies()
    if number not in found:
        raise ValueError(f"the log of copies holds no copy {number}")
    entry = found[number]
    book = parse(export)
    if comparable(book.title) not in known_titles(entry):
        raise ValueError(
            f"{export.name} is the notebook of {book.title!r}, which is not copy "
            f"{number}; read it against the copy it was made on"
        )
    day = dated or dt.date.fromtimestamp(export.stat().st_mtime).isoformat()
    dt.date.fromisoformat(day)
    chapters = current_project().path("manuscript.chapters")
    place(book.marks, chapters, entry.commit)

    out = out or _archive_dir() / f"kindle-copy-{number:02d}-{day}.md"
    if out.exists():
        raise ValueError(f"{out} already exists; an archive is never overwritten")
    exact, near, earlier, lost = _anchor_tally(book.marks)
    counts = {
        k: sum(1 for m in book.marks if m.kind == k)
        for k in ("highlight", "note", "bookmark")
    }
    attached = sum(1 for m in book.marks if m.note)
    digest = hashlib.sha256(export.read_bytes()).hexdigest()
    head = [
        f"# Kindle notes, copy {number}",
        "",
        "Durable archive of the highlights and notes the reader made on one",
        "Kindle copy, read from the notebook the Kindle app exported and mapped",
        "against the manuscript. The export itself is kept verbatim in the",
        "sibling `.raw/` directory.",
        "",
        "```",
        f"source-export:  {export.name}",
        f"export-sha256:  {digest}",
        f"copy:           {number}",
        f"copy-commit:    {entry.commit or '?'}",
        f"marks-dated:    {day} (the day exported; the app records no earlier date)",
        f"extracted:      {dt.date.today():%Y-%m-%d}",
        f"book revision:  {_redpen._git_short()}",
        f"highlights:     {counts['highlight']} ({attached} with a note)",
        f"notes alone:    {counts['note']}",
        f"bookmarks:      {counts['bookmark']}",
        f"anchors:        exact {exact} / near {near} / changed since {earlier} / unlocated {lost}",
        "```",
        "",
        "## MARKS  (`quirework publish kindle notes`)",
    ]
    body: list[str] = []
    chapter = None
    for mark in book.marks:
        where = _chapter_of(mark) or "(not found in the current manuscript)"
        if where != chapter:
            body.append(f"### {where}")
            chapter = where
        body.append(_entry(mark, day))
    text = "\n".join(head) + "\n\n" + "\n\n".join(body) + "\n"

    out.parent.mkdir(parents=True, exist_ok=True)
    raw = out.with_suffix(".raw")
    raw.mkdir(parents=True, exist_ok=True)
    shutil.copy2(export, raw / export.name)
    out.write_text(text, encoding="utf-8")
    _proof._append(
        _proof._log_path(),
        {
            "copy": number,
            "event": PREFIX + "notes",
            "archive": str(out),
            "export_sha256": digest,
            "marks": len(book.marks),
            "at": _proof._now(),
        },
    )
    try:
        shown = out.relative_to(current_project().root)
    except ValueError:
        shown = out
    print(
        f"kindle: archived copy {number}'s notes -> {shown}  "
        f"({counts['highlight']} highlights, {attached} with a note, "
        f"{counts['note']} notes alone; anchors {exact}/{near}/{earlier}/{lost})"
    )
    return 0


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #
def _options(argv: list[str], flags: set[str], repeat: set[str] = frozenset()):
    """Positional words and `--flag value` options, refusing any other."""
    words, values = [], {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            name, eq, value = a.partition("=")
            if name not in flags | repeat:
                raise ValueError(f"unknown option {name} (see --help)")
            if not eq:
                if i + 1 >= len(argv):
                    raise ValueError(f"{name} needs a value")
                value, i = argv[i + 1], i + 1
            if name in repeat:
                values.setdefault(name, []).append(value)
            else:
                values[name] = value
        else:
            words.append(a)
        i += 1
    return words, values


def _number(value: str | None) -> int | None:
    if value is None:
        return None
    if not value.isdigit():
        raise ValueError(f"--copy takes a copy's number, not {value!r}")
    return int(value)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if (
        not argv
        or argv[0] in ("-h", "--help")
        or any(a in ("-h", "--help") for a in argv)
    ):
        print(HELP, end="")
        return 0
    command, rest = argv[0], argv[1:]
    try:
        if command == "driver":
            if rest:
                raise ValueError("driver takes no arguments")
            print(DRIVER)
            return 0
        if command == "plan":
            words, opts = _options(rest, {"--copy", "--snapshot"})
            if words:
                raise ValueError(f"plan takes no argument {words[0]!r}")
            snapshot = Path(opts["--snapshot"]) if "--snapshot" in opts else None
            print(
                json.dumps(
                    plan(_number(opts.get("--copy")), snapshot),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        if command == "record":
            words, opts = _options(rest, {"--copy", "--reason"}, {"--device"})
            if len(words) != 1:
                raise ValueError(f"record takes one event: {', '.join(EVENTS)}")
            number = _number(opts.get("--copy"))
            if number is None:
                raise ValueError("record names the copy: --copy N")
            row = record(words[0], number, opts.get("--device"), opts.get("--reason"))
            print(f"kindle: copy {number} {row['event'][len(PREFIX) :]}")
            return 0
        if command == "notes":
            words, opts = _options(rest, {"--copy", "--out", "--dated"})
            if len(words) != 1:
                raise ValueError("notes reads one exported notebook")
            number = _number(opts.get("--copy"))
            if number is None:
                raise ValueError(
                    "notes names the copy the marks were made on: --copy N"
                )
            export = Path(words[0])
            if not export.is_file():
                raise ValueError(f"no such file: {export}")
            out = Path(opts["--out"]) if "--out" in opts else None
            return notes(export, number, out, opts.get("--dated"))
        raise ValueError(f"unknown command {command!r} (see --help)")
    except ValueError as exc:
        print(f"kindle: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
