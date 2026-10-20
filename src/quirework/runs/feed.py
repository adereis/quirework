"""A procedure told as it runs, for the person following it.

Every procedure that seats a model sends the harness's own stream into the
turn's ``events.jsonl`` while the model works, and a room or a roundtable
also appends its own steps to ``room.log``. Both are written as evidence,
and neither says in plain words who has started, what they read and wrote,
what they said, or how the turn ended. Following a round meant opening them
one by one.

A procedure builds a feed over its own record. ``watch`` prints one until
interrupted, for a side terminal. ``narrating`` prints a brief one on stderr
while the procedure's own command runs, so that command's log is not silent
for the length of every turn. Brief keeps who started and finished and which
files they wrote, and leaves the words to ``watch``, because the assistant
that launched the command reads its log too.

Everything here reads and nothing writes, so following a run cannot change
it, and a run prepared by older code is followed the same way.
"""

from __future__ import annotations

import difflib
import json
import re
import shutil
import sys
import textwrap
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from quirework.providers import blind
from quirework.runs.storage import identifier, read, shown

# A complete line that is not JSON. Said, not skipped: a damaged stream is
# worth knowing about while the turn is still running.
UNREADABLE = object()

FILES = ("read", "wrote", "edited", "deleted", "listed")
CHANGES = ("wrote", "edited", "deleted")
WORDS = ("said", "thought")


@dataclass(frozen=True)
class Show:
    """What a feed prints beyond who started and finished and what changed.

    The defaults are the side terminal's. ``BRIEF`` is a running command's
    own log: no words and no reads, because the assistant that launched the
    command reads that log, and a round's posts would fill its context.
    """

    words: bool = True
    thinking: bool = False
    actions: bool = True
    sizes: bool = True


FULL = Show()
BRIEF = Show(words=False, actions=False)


class Tail:
    """The new complete lines of a file someone is still appending to.

    Read by offset, so each line is taken once, and only up to the last
    newline, so a row the writer is halfway through waits for the next poll.
    A file replaced under the same name, as a turn's stream is when a
    continuation archives the stopped attempt, is read again from its start.
    """

    def __init__(self, path, *, at_end=False):
        self.path, self.offset, self.inode = Path(path), 0, None
        if at_end and self.path.exists():
            stat = self.path.stat()
            self.offset, self.inode = stat.st_size, stat.st_ino

    def lines(self):
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return []
        if stat.st_ino != self.inode or stat.st_size < self.offset:
            self.inode, self.offset = stat.st_ino, 0
        if stat.st_size == self.offset:
            return []
        with self.path.open("rb") as stream:
            stream.seek(self.offset)
            data = stream.read()
        cut = data.rfind(b"\n") + 1
        self.offset += cut
        return data[:cut].decode("utf-8", errors="replace").splitlines()

    def rows(self):
        found = []
        for line in self.lines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                found.append(UNREADABLE)
                continue
            found.append(value if isinstance(value, dict) else UNREADABLE)
        return found


class Growing:
    """A document still being written, told by what each change added.

    A reviewer writes its report as it goes, so the report is the turn's
    discussion. Printing the whole file at every change would repeat it, and
    printing only appended bytes would miss a section inserted above the
    last one. Each version is compared line by line with the one seen
    before, and what it added or replaced is what is shown.
    """

    def __init__(self, path, *, at_end=False):
        self.path, self.seen, self.mark = Path(path), None, None
        if at_end:
            self.poll()

    def poll(self):
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return []
        mark = (stat.st_mtime_ns, stat.st_size)
        if mark == self.mark:
            return []
        self.mark = mark
        lines = self.path.read_text(errors="replace").splitlines()
        before, self.seen = self.seen, lines
        if before is None:
            return lines
        added = []
        matcher = difflib.SequenceMatcher(None, before, lines, autojunk=False)
        for tag, _, _, start, end in matcher.get_opcodes():
            if tag in ("insert", "replace"):
                if added:
                    added.append("⋯")
                added += lines[start:end]
        return added


def short_names(labels, limit=20):
    """The names a feed's who-column uses: the labels, unless one is too long.

    A persona's label can be a description, such as "Keeper of the
    lighthouse tide tables", and a column that wide leaves the actions a
    third of the line. Then every seat goes by its id instead, so the column holds
    one kind of name and a header can list the labels once.
    """
    if all(len(value) <= limit for value in labels.values()):
        return dict(labels)
    return {key: key for key in labels}


def clock(t):
    """A row's local time, or a blank column for a moment with no time."""
    return time.strftime("%H:%M:%S", time.localtime(t)) if t else " " * 8


def duration(seconds):
    whole = round(seconds or 0)
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m{whole % 60:02d}s"
    return f"{whole // 3600}h{whole % 3600 // 60:02d}m"


def size(count):
    if count < 1000:
        return f"{count} B"
    if count < 1_000_000:
        return f"{count / 1000:.1f} kB"
    return f"{count / 1_000_000:.1f} MB"


class Page:
    """How a feed lays out its lines: the time, who, what they did, the rest.

    One column each, so a reader scanning down sees the seats change and the
    verbs line up. Text wraps under itself rather than under the clock, and a
    post or a report goes in a frame of its own, whole.
    """

    VERB = 9

    def __init__(self, width=None, who=0):
        terminal = shutil.get_terminal_size((100, 24)).columns
        self.width = width or min(max(terminal, 60), 120)
        self.who = who

    def _wrap(self, head, text):
        hang = " " * len(head)
        room = max(self.width - len(head), 20)
        lines = []
        for line in str(text).strip("\n").splitlines() or [""]:
            if not line.strip():
                if lines and lines[-1]:
                    lines.append("")
                continue
            lines += textwrap.wrap(
                line.rstrip(), room, break_on_hyphens=False, break_long_words=True
            ) or [""]
        while lines and not lines[-1]:
            lines.pop()
        return [
            (head if number == 0 else hang if line else "") + line
            for number, line in enumerate(lines or [""])
        ]

    def event(self, t, text):
        return self._wrap(f"{clock(t)}  ", text)

    def act(self, t, who, verb, text):
        return self._wrap(f"{clock(t)}  {who:<{self.who}}  {verb:<{self.VERB}}", text)

    def command(self, t, who, verb, text):
        """One line, cut: a heredoc of three hundred lines is not the point."""
        head = f"{clock(t)}  {who:<{self.who}}  {verb:<{self.VERB}}"
        first = str(text).strip().splitlines()[0] if str(text).strip() else ""
        room = max(self.width - len(head), 20)
        if len(first) > room or "\n" in str(text).strip():
            first = first[: room - 1] + "…"
        return [head + first]

    def block(self, title, text):
        pad = " " * 10
        room = max(self.width - len(pad) - 2, 20)
        out = [f"{pad}┌─ {title}"]
        for line in str(text).strip("\n").splitlines():
            if not line.strip():
                out.append(f"{pad}│")
                continue
            indent = line[: len(line) - len(line.lstrip())]
            for piece in textwrap.wrap(
                line.rstrip(),
                room,
                subsequent_indent=indent,
                break_on_hyphens=False,
            ) or [""]:
                out.append(f"{pad}│ {piece}")
        return out + [f"{pad}└─"]


def _text(value):
    """A tool result's text, whichever of its shapes the harness used."""
    if isinstance(value, list):
        return "\n".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in value
        )
    return "" if value is None else str(value)


def _shell(command):
    """The command a harness wrapped in a login shell, without the wrapper."""
    found = re.fullmatch(
        r"\s*(?:/usr)?(?:/bin/)?(?:ba|z)?sh\s+-l?c\s+(['\"])(.*)\1\s*",
        command,
        re.DOTALL,
    )
    if not found:
        return command
    inner = found.group(2)
    # Inside double quotes a shell unescapes only these four.
    return re.sub(r'\\([$`"\\])', r"\1", inner) if found.group(1) == '"' else inner


# What a moment is: (verb, text, path). A file verb carries the path and no
# text; a search may carry both; the rest carry text.
CLAUDE_TOOLS = {
    "Read": ("read", "file_path"),
    "Write": ("wrote", "file_path"),
    "Edit": ("edited", "file_path"),
    "MultiEdit": ("edited", "file_path"),
    "NotebookEdit": ("edited", "notebook_path"),
    "Bash": ("ran", "command"),
    "Glob": ("searched", "pattern"),
    "Grep": ("searched", "pattern"),
    "WebSearch": ("searched", "query"),
    "WebFetch": ("fetched", "url"),
}


def claude(row):
    """What one row of Claude Code's stream shows the seat doing."""
    message = row.get("message") if isinstance(row.get("message"), dict) else {}
    content = message.get("content")
    blocks = (
        [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    )
    found = []
    if row.get("type") == "assistant":
        if isinstance(content, str):
            found.append(("said", content, None))
        for block in blocks:
            shape = block.get("type")
            if shape == "text":
                found.append(("said", block.get("text") or "", None))
            elif shape == "thinking":
                found.append(("thought", block.get("thinking") or "", None))
            elif shape == "tool_use":
                name = block.get("name") or "a tool"
                given = (
                    block.get("input") if isinstance(block.get("input"), dict) else {}
                )
                verb, key = CLAUDE_TOOLS.get(name, ("used", None))
                value = str(given.get(key) or "") if key else name
                if verb in FILES:
                    found.append((verb, "", value))
                elif verb == "searched" and given.get("path"):
                    found.append((verb, value, str(given["path"])))
                else:
                    found.append((verb, value, None))
    elif row.get("type") == "user":
        for block in blocks:
            if block.get("type") == "tool_result" and block.get("is_error"):
                found.append(("error", _text(block.get("content")), None))
    elif row.get("type") == "result" and row.get("is_error"):
        reason = row.get("result") or row.get("subtype") or "the turn ended in an error"
        found.append(("error", str(reason), None))
    return [m for m in found if m[1].strip() or m[2]]


def codex(row):
    """What one row of Codex's ``exec --json`` stream shows the seat doing.

    Only completed items, so a command is told once, with how it ended. An
    error *item* is Codex remarking on its own setup and the turn goes on; a
    top-level error is the turn failing, and Codex then says it twice.
    """
    kind = row.get("type")
    if kind == "turn.failed":
        error = row.get("error") if isinstance(row.get("error"), dict) else {}
        return [("error", str(error.get("message") or "the turn failed"), None)]
    if kind == "error":
        return [("error", str(row.get("message") or "the turn failed"), None)]
    if kind != "item.completed":
        return []
    item = row.get("item") if isinstance(row.get("item"), dict) else {}
    shape = item.get("type")
    if shape == "agent_message":
        found = [("said", str(item.get("text") or ""), None)]
    elif shape == "reasoning":
        found = [("thought", str(item.get("text") or ""), None)]
    elif shape == "command_execution":
        command = _shell(str(item.get("command") or ""))
        code = item.get("exit_code")
        found = [
            (
                "ran",
                command + (f"  (exit {code})" if code not in (0, None) else ""),
                None,
            )
        ]
    elif shape == "file_change":
        verbs = {"add": "wrote", "delete": "deleted"}
        found = [
            (verbs.get(change.get("kind"), "edited"), "", str(change.get("path") or ""))
            for change in item.get("changes") or []
            if isinstance(change, dict)
        ]
    elif shape == "mcp_tool_call":
        found = [("used", f"{item.get('server')}.{item.get('tool')}", None)]
    elif shape == "web_search":
        found = [("searched", str(item.get("query") or ""), None)]
    elif shape == "error":
        found = [("notice", str(item.get("message") or ""), None)]
    else:
        found = []
    return [m for m in found if m[1].strip() or m[2]]


# Each tool's verb, and the arguments that can name what it acted on. agy's
# file tools go by two sets of names: the one it recorded through 1.4.2,
# which names a file by `AbsolutePath` or `TargetFile`, and the one seen from
# 1.4.3, which names it by `Path`. A record keeps the names it was made with,
# so both are read.
AGY_TOOLS = {
    "view_file": ("read", ("AbsolutePath",)),
    "write_to_file": ("wrote", ("TargetFile",)),
    "replace_file_content": ("edited", ("TargetFile",)),
    "multi_replace_file_content": ("edited", ("TargetFile",)),
    "list_dir": ("listed", ("DirectoryPath",)),
    "read_file": ("read", ("Path",)),
    "write_file": ("wrote", ("Path",)),
    "edit_file": ("edited", ("Path", "TargetFile")),
    "run_command": ("ran", ("CommandLine",)),
    "search_web": ("searched", ("query",)),
    "read_url_content": ("fetched", ("Url",)),
}
AGY_PATHS = tuple(
    dict.fromkeys(
        key for verb, keys in AGY_TOOLS.values() if verb in FILES for key in keys
    )
)


def agy_names(name, given):
    """A tool by its name and the file its arguments name, for a tool this
    table does not know: a bare `used read_file` says nothing of which file."""
    where = next((given[k] for k in AGY_PATHS if isinstance(given.get(k), str)), "")
    return f"{name} {where}".strip()


def agy(row):
    """What one row of agy's stream shows. Its words are elsewhere; see Turn."""
    step = row.get("step_update")
    if row.get("event") != "step_update" or not isinstance(step, dict):
        return []
    if step.get("step_type") != "tool" or step.get("state") not in ("DONE", "ERROR"):
        return []
    info = step.get("tool_info") if isinstance(step.get("tool_info"), dict) else {}
    name = step.get("tool_name") or info.get("name") or "a tool"
    given = info.get("parameters") if isinstance(info.get("parameters"), dict) else {}
    if step.get("state") == "ERROR":
        error = info.get("error") if isinstance(info.get("error"), dict) else {}
        return [("error", f"{name}: {error.get('message') or 'failed'}", None)]
    verb, keys = AGY_TOOLS.get(name, ("used", ()))
    value = next((str(given[k]) for k in keys if given.get(k)), "")
    if not value:
        return [("used", agy_names(name, given), None)]
    if verb in FILES:
        return [(verb, "", value)]
    return [(verb, value, None)]


READERS = {"claude": claude, "codex": codex, "agy": agy}


class Turn:
    """One seat's turn, followed through its harness's stream as it works.

    ``places`` maps the paths a seat sees to the names a reader knows them
    by, as ``(seen, label, real)``: a seat's workspace becomes the bare
    relative path, so ``outbox/post.md`` rather than a state root's forty
    characters, and ``real`` is where the file is on this machine when the
    seat saw it somewhere else. agy writes no words to its stream, only that
    a response is done, so its words come from its own transcript under
    ``private``, matched by step, and wait there in order until they arrive.
    """

    def __init__(
        self,
        page,
        who,
        harness,
        stream,
        *,
        places=(),
        private=None,
        show=FULL,
        at_end=False,
    ):
        self.page, self.who, self.harness, self.show = page, who, harness, show
        self.tail = Tail(stream, at_end=at_end)
        self.places = [
            (str(seen).rstrip("/"), label, Path(real)) for seen, label, real in places
        ]
        self.reader = READERS.get(harness)
        self.private = Path(private) if private else None
        self.queue, self.conversation = [], None
        self.last_error, self.noted, self.spoken = None, False, None

    def place(self, path):
        text = str(path)
        for seen, label, real in self.places:
            if text == seen or text.startswith(seen + "/"):
                rest = text[len(seen) :].lstrip("/")
                return (label + rest) or ".", real / rest
        if not Path(text).is_absolute() and self.places:
            return text, self.places[0][2] / text
        return shown(text), Path(text)

    def poll(self, t, *, final=False):
        if self.reader is None:
            if self.noted:
                return []
            self.noted = True
            return self.page.act(
                t, self.who, "note", f"a {self.harness} stream cannot be followed yet"
            )
        for row in self.tail.rows():
            if row is UNREADABLE:
                self.queue.append(
                    ("note", "an unreadable line in the turn's stream", None)
                )
                continue
            if self.harness == "agy":
                self._agy_step(row)
            self.queue += self.reader(row)
        return self._emit(t, final)

    def finish(self, t):
        """Everything left in the stream, with nothing held back for later."""
        return self.poll(t, final=True)

    def _emit(self, t, final):
        lines, words = [], None
        while self.queue:
            item = self.queue[0]
            if isinstance(item, int):
                if words is None:
                    words = self._agy_words()
                if words is False:
                    self.queue = [m for m in self.queue if not isinstance(m, int)]
                    if not self.noted:
                        self.noted = True
                        lines += self.page.act(
                            t,
                            self.who,
                            "note",
                            "agy's words are not in its transcript where this "
                            "version looks; only its actions are shown",
                        )
                    continue
                spoken, latest = words
                if item in spoken:
                    self.queue.pop(0)
                    said, thought = spoken[item]
                    if thought:
                        lines += self._line(t, "thought", thought, None)
                    if said:
                        lines += self._line(t, "said", said, None)
                    continue
                if final or latest > item:
                    self.queue.pop(0)
                    continue
                break
            self.queue.pop(0)
            lines += self._line(t, *item)
        return lines

    def _agy_step(self, row):
        if row.get("event") == "init":
            self.conversation = row.get("conversation_id") or self.conversation
        step = row.get("step_update")
        if not isinstance(step, dict):
            return
        self.conversation = self.conversation or step.get("conversation_id")
        if (
            step.get("step_type") == "agent_response"
            and step.get("state") == "DONE"
            and (self.show.words or self.show.thinking)
        ):
            try:
                self.queue.append(int(step.get("step_index")))
            except (TypeError, ValueError):
                pass

    def _agy_words(self):
        """``({step: (said, thought)}, latest step)``, or False if not found.

        Read whole, not tailed, because agy rewrites a step in place when it
        finishes it.
        """
        if not self.private or not self.conversation:
            return False
        path = (
            self.private
            / "antigravity-cli"
            / "brain"
            / self.conversation
            / ".system_generated"
            / "logs"
            / "transcript_full.jsonl"
        )
        try:
            stat = path.stat()
        except FileNotFoundError:
            return False
        mark = (stat.st_mtime_ns, stat.st_size)
        if self.spoken and self.spoken[0] == mark:
            return self.spoken[1]
        spoken, latest = {}, -1
        for line in path.read_text(errors="replace").splitlines():
            try:
                row = json.loads(line)
                step = int(row.get("step_index"))
            except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
                continue
            latest = max(latest, step)
            if row.get("type") == "PLANNER_RESPONSE" and row.get("status") == "DONE":
                spoken[step] = (
                    (row.get("content") or "").strip(),
                    (row.get("thinking") or "").strip(),
                )
        self.spoken = (mark, (spoken, latest))
        return spoken, latest

    def _line(self, t, verb, text, path):
        show = self.show
        if verb == "said" and not show.words or verb == "thought" and not show.thinking:
            return []
        if verb not in CHANGES and verb not in WORDS and not show.actions:
            return []
        if verb == "error":
            if text == self.last_error:
                return []
            self.last_error = text
        if path:
            name, real = self.place(path)
            if verb not in FILES:
                text = f"{text} in {name}"
            else:
                text = name
                if verb in ("wrote", "edited") and show.sizes and real.is_file():
                    text += f" ({size(real.stat().st_size)})"
        if verb in ("ran", "searched", "fetched", "used"):
            return self.page.command(t, self.who, verb, text)
        return self.page.act(t, self.who, verb, text)


def emit(lines, out):
    if lines:
        out.write("\n".join(lines) + "\n")
        out.flush()


def watch(feed, *, poll=1.0, out=None):
    """Print a feed until interrupted; a person stops it, not the run."""
    out = out or sys.stdout
    while True:
        emit(feed.poll(), out)
        time.sleep(poll)


@contextmanager
def narrating(feed, *, poll=1.0, out=None):
    """Print a feed on stderr while the block runs, and once more after it.

    The feed is read on a thread of its own, so a turn that takes an hour
    still reports each file it writes when it writes it. A failure of the
    feed is printed once and stops the narration, never the run: nothing in
    a procedure's evidence depends on anyone following it.
    """
    out = out or sys.stderr
    stop, broken = threading.Event(), []

    def tell():
        if broken:
            return
        try:
            emit(feed.poll(), out)
        except Exception as exc:  # noqa: BLE001 -- reported, and the run goes on
            broken.append(exc)
            emit([f"progress: stopped following ({type(exc).__name__}: {exc})"], out)

    def loop():
        while not stop.wait(poll):
            tell()

    thread = threading.Thread(target=loop, name="quirework-progress", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
        tell()


# -- The run engine's own record ----------------------------------------------


def _mtime(path):
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None


class RunFeed:
    """A run record's seats, turn by turn, from the files the engine keeps.

    The engine keeps no trail, so its progress is read from what it already
    writes: a seat's state names the turn it has submitted, the turn's
    record holds its stream, and a banked turn holds its answer. When a turn
    began and ended is read from its record's own files, so a record made
    before this feed existed is told the same way.
    """

    def __init__(self, root, *, history="all", show=FULL, width=None):
        self.root = Path(root).expanduser().resolve()
        plan = read(self.root / "plan.json")
        self.seats = {identifier(s["id"]): s for s in plan["seats"]}
        self.show, self.history, self.first = show, history, True
        self.page = Page(width, who=max(len(n) for n in self.seats))
        self.active, self.banked, self.finished = {}, {}, set()
        if history == "none":
            for name in self.seats:
                state = self._state(name)
                self.banked[name] = set(state.get("done", {}))
                if state.get("complete"):
                    self.finished.add(name)
                if pending := state.get("pending"):
                    attempt = state.get("attempts", {}).get(pending["turn"], 0)
                    self.active[name] = (
                        pending["turn"],
                        attempt,
                        self._turn(name, pending["turn"], attempt, at_end=True),
                    )

    def header(self):
        lines = [f"following the run at {shown(self.root)}; interrupt to stop"]
        for number, (name, seat) in enumerate(self.seats.items(), 1):
            config = seat["config"]
            lines.append(
                f"  {number}. {name}  {config['harness']}/{config.get('model')}"
            )
        return lines

    def _state(self, name):
        path = self.root / "state" / f"{name}.json"
        return read(path) if path.exists() else {}

    def _record(self, name, turn, attempt):
        return self.root / "records" / name / turn / f"attempt-{attempt:03d}"

    def _turn(self, name, turn, attempt, *, at_end=False):
        harness = self.seats[name]["config"]["harness"]
        work = self.root / "work" / name / "seat"
        return Turn(
            self.page,
            name,
            harness,
            self._record(name, turn, attempt) / "events.jsonl",
            places=[(work, "", work)],
            private=blind.state_root(work) / harness,
            show=self.show,
            at_end=at_end,
        )

    def _starts(self, name, turn, attempt, t):
        config = self.seats[name]["config"]
        again = f", attempt {attempt + 1}" if attempt else ""
        return self.page.event(
            t,
            f"{name} [{config['harness']}/{config.get('model')}] starts turn {turn}{again}",
        )

    def poll(self):
        if self.first:
            self.first = False
            if self.history == "all":
                return self._replay()
        now, lines = time.time(), []
        for name in self.seats:
            state = self._state(name)
            done, pending = state.get("done", {}), state.get("pending")
            attempts = state.get("attempts", {})
            if name in self.active:
                turn, attempt, follower = self.active[name]
                lines += follower.poll(now)
                if turn in done or not pending or pending["turn"] != turn:
                    lines += follower.finish(now)
                    del self.active[name]
                    if turn not in done:
                        lines += self._stopped(name, turn, attempt)
                elif attempts.get(turn, 0) != attempt:
                    lines += follower.finish(now)
                    del self.active[name]
                    lines += self._stopped(name, turn, attempt)
            seen = self.banked.setdefault(name, set())
            for turn, banked in done.items():
                if turn not in seen:
                    seen.add(turn)
                    lines += self._answered(name, turn, banked.get("attempt", 0))
            if pending and name not in self.active and pending["turn"] not in done:
                turn = pending["turn"]
                attempt = attempts.get(turn, 0)
                lines += self._starts(
                    name, turn, attempt, pending.get("created_at") or now
                )
                follower = self._turn(name, turn, attempt)
                self.active[name] = (turn, attempt, follower)
                lines += follower.poll(now)
            if state.get("complete") and name not in self.finished:
                self.finished.add(name)
                lines += self.page.event(now, f"{name} has taken all its turns")
        return lines

    def _replay(self):
        """Every banked turn in the order the turns began, then those in flight."""
        lines, banked = [], []
        for name in self.seats:
            state = self._state(name)
            self.banked[name] = set(state.get("done", {}))
            for turn, value in state.get("done", {}).items():
                attempt = value.get("attempt", 0)
                began = _mtime(self._record(name, turn, attempt) / "launch.json")
                banked.append((began or 0, name, turn, attempt))
        for began, name, turn, attempt in sorted(banked):
            lines += self._starts(name, turn, attempt, began or None)
            lines += self._turn(name, turn, attempt).finish(None)
            lines += self._answered(name, turn, attempt)
        for name in self.seats:
            state = self._state(name)
            if pending := state.get("pending"):
                attempt = state.get("attempts", {}).get(pending["turn"], 0)
                follower = self._turn(name, pending["turn"], attempt)
                lines += self._starts(
                    name, pending["turn"], attempt, pending.get("created_at")
                )
                lines += follower.poll(None)
                self.active[name] = (pending["turn"], attempt, follower)
            if state.get("complete"):
                self.finished.add(name)
                lines += self.page.event(None, f"{name} has taken all its turns")
        return lines

    def _answered(self, name, turn, attempt):
        record = self._record(name, turn, attempt)
        began, ended = _mtime(record / "launch.json"), _mtime(record / "events.jsonl")
        took = f" in {duration(ended - began)}" if began and ended else ""
        lines = self.page.event(ended, f"{name} answered {turn}{took}")
        answers = record / "answers"
        answer = answers / "answer.md"
        if self.show.words and answer.is_file() and answer.read_text().strip():
            lines += self.page.block(f"{name}, {turn}", answer.read_text())
        for path in sorted(answers.rglob("*")) if answers.is_dir() else []:
            if path.is_file() and path != answer:
                weight = f" ({size(path.stat().st_size)})" if self.show.sizes else ""
                lines += self.page.event(
                    None, f"its declared output {path.relative_to(answers)}{weight}"
                )
        return lines

    def _stopped(self, name, turn, attempt):
        retired = self._record(name, turn, attempt) / "retirement.json"
        why = read(retired).get("reason") if retired.is_file() else None
        return self.page.event(
            time.time(),
            f"{name}'s turn {turn} stopped without an answer"
            + (f": {why}" if why else "; `quirework sop runs inspect` says why"),
        )
