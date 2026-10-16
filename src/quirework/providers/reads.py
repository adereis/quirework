"""Which files a seat read and wrote, and in what order, from its native record.

A seat that fetches its own material leaves no frozen copy of what it read,
so the only evidence is the tool calls in its native session. This module
turns those calls into an ordered list of file reads, each with the lines it
covered, and of file writes, and checks a declared reading list against
them: whether each file was read in full, in order, whether each file's
reaction was written before the next file was opened, and whether it was left
alone once the next file was.

The parsing is deliberately conservative. A read it cannot recognize, such
as a command assembled at run time or a shell idiom not listed here, is not
counted, so the check can say a file was not *proven* read in full but never
that an unread file was read. Recognized forms:

- Claude's `Read` tool, with its `offset` and `limit`;
- agy's `view_file`, taken from the result agy records for it, which names
  the file and the lines it showed;
- shell commands, from Claude's `Bash` tool, agy's `run_command` or any
  Codex call: `cat`, `nl`, `sed -n 'A,Bp'` or `'Ap'`, and `head -n N` or
  `head -N`.

Writes are recognized as conservatively: Claude's `Write` and `Edit` tools,
Codex's `apply_patch` (an added or updated file), agy's `write_to_file` (from
its result, as for a view), and shell redirection or `tee`. A redirection
target is a write and never a read, and the body of a heredoc is text, not
commands.
"""

import json
import re
import shlex
from pathlib import Path
from urllib.parse import unquote

# Claude's Read tool returns this many lines when no limit is given.
CLAUDE_READ_DEFAULT_LIMIT = 2000

_SEGMENT = re.compile(r"&&|\|\||[;|]")
# A heredoc opener (`<<'EOF'`, `<<-EOF`), whose body runs to the delimiter line.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
# A file an apply_patch adds or updates. In a Codex exec snippet the patch is
# a JavaScript string, so its newlines are the two characters `\n`.
_PATCH_FILE = re.compile(r"\*\*\* (?:Add|Update) File: ([^\\\n\"']+)")
_SED_RANGE = re.compile(r"^(\d+)(?:,(\d+))?p$")
# A command string inside Codex's JavaScript exec tool: `cmd: "..."` or
# `"cmd": "..."`, with the string's own escapes.
_JS_CMD = re.compile(r"""["']?cmd["']?\s*:\s*("(?:[^"\\]|\\.)*")""")
# What agy records as the result of a view and of a write. Each names its own
# file, so neither has to be matched back to the call that asked for it, and
# a call agy rejected leaves no such result to count.
_AGY_VIEW = re.compile(
    r"File Path: `file://([^`]+)`\nTotal Lines: \d+\n(?:Total Bytes: \d+\n)?"
    r"Showing lines (\d+) to (\d+)"
)
_AGY_CREATED = re.compile(r"Created file file://(\S+) with requested content")


def _rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def _resolve(name, workdir):
    path = Path(name)
    if not path.is_absolute():
        if workdir is None:
            return None
        path = Path(workdir) / path
    return str(path)


def _segment_events(words, workdir):
    """Ordered events one simple command performs: its writes, then its reads."""
    events, rest, i = [], [], 0
    while i < len(words):
        w = words[i]
        if w in (">", ">>", "1>", "1>>") and i + 1 < len(words):
            target = _resolve(words[i + 1], workdir)
            if target and target != "/dev/null":
                events.append(("write", target, None))
            i += 2
            continue
        if re.fullmatch(r"[12]?>>?[^>&].*", w):
            target = _resolve(re.sub(r"^[12]?>>?", "", w), workdir)
            if target and target != "/dev/null":
                events.append(("write", target, None))
            i += 1
            continue
        if w.startswith("<<") or re.fullmatch(r"\d*>&\d+", w):
            i += 1
            continue
        rest.append(w)
        i += 1
    if not rest:
        return events
    tool, args = rest[0].rsplit("/", 1)[-1], rest[1:]
    files, ranges = [], None
    if tool == "tee":
        for a in args:
            if not a.startswith("-") and (target := _resolve(a, workdir)):
                events.append(("write", target, None))
        return events
    if tool in ("cat", "nl"):
        files = [a for a in args if not a.startswith("-")]
    elif tool == "sed" and len(args) >= 3 and args[0] == "-n":
        match = _SED_RANGE.match(args[1])
        if match:
            first = int(match.group(1))
            last = int(match.group(2) or first)
            ranges, files = [(first, last)], args[2:]
    elif tool == "head":
        count, names = None, []
        j = 0
        while j < len(args):
            a = args[j]
            if a == "-n" and j + 1 < len(args) and args[j + 1].isdigit():
                count, j = int(args[j + 1]), j + 2
                continue
            if re.fullmatch(r"-\d+", a):
                count = int(a[1:])
            elif not a.startswith("-"):
                names.append(a)
            j += 1
        ranges, files = [(1, count if count is not None else 10)], names
    for name in files:
        if (path := _resolve(name, workdir)) is not None:
            events.append(("read", path, ranges))
    return events


def _shell_events(command, workdir=None):
    """Ordered ("read", path, ranges) and ("write", path, None) events a shell
    command performs. ranges is None for a whole file, else a list of
    inclusive (first, last) line pairs. A heredoc's body is skipped."""
    events, delimiter = [], None
    for line in command.split("\n"):
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        opener = _HEREDOC.search(line)
        for segment in _SEGMENT.split(line):
            try:
                words = shlex.split(segment)
            except ValueError:
                continue
            if words:
                events += _segment_events(words, workdir)
        if opener:
            delimiter = opener.group(2)
    return events


def _shell_reads(command, workdir=None):
    """(path, ranges) for each file a shell command prints."""
    return [(p, r) for kind, p, r in _shell_events(command, workdir) if kind == "read"]


def _codex_events(payload):
    """Ordered read and write events one Codex response item carries."""
    kind = payload.get("type", "")
    if kind == "local_shell_call":
        command = payload.get("action", {}).get("command")
        workdir = payload.get("action", {}).get("working_directory")
        if isinstance(command, list):
            command = shlex.join(command)
        return _shell_events(command, workdir) if isinstance(command, str) else []
    if kind == "function_call":
        try:
            args = json.loads(payload.get("arguments") or "{}")
        except json.JSONDecodeError:
            return []
        if payload.get("name") == "apply_patch" or "input" in args:
            patch = args.get("input") or ""
            return [("write", p.strip(), None) for p in _PATCH_FILE.findall(patch)]
        command = args.get("cmd") or args.get("command")
        if isinstance(command, list):
            command = shlex.join(command)
        if not isinstance(command, str):
            return []
        return _shell_events(command, args.get("workdir"))
    if kind == "custom_tool_call":
        # A JavaScript snippet awaits its tool calls in the order it states
        # them, so its commands and patches are ordered by position.
        text = payload.get("input") or ""
        found = []
        for match in _JS_CMD.finditer(text):
            try:
                command = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            found.append((match.start(), _shell_events(command)))
        for match in _PATCH_FILE.finditer(text):
            found.append((match.start(), [("write", match.group(1).strip(), None)]))
        return [e for _, evs in sorted(found, key=lambda f: f[0]) for e in evs]
    return []


def _agy_events(row):
    """Ordered read and write events one step of an agy transcript carries.

    A planner step lists the tools it called and each result is a step of its
    own, so a view or a write is read from its result: the file it touched and,
    for a view, the lines agy actually showed, which can be fewer than the call
    asked for. A shell command is read from the call, as Claude's Bash is.
    """
    if row.get("type") == "PLANNER_RESPONSE":
        found = []
        for call in row.get("tool_calls") or []:
            args = call.get("args") or {}
            command = args.get("CommandLine")
            if call.get("name") == "run_command" and isinstance(command, str):
                found += _shell_events(command, args.get("Cwd"))
        return found
    if row.get("status") != "DONE":
        return []
    content = row.get("content") or ""
    if view := _AGY_VIEW.search(content):
        first, last = int(view.group(2)), int(view.group(3))
        return [("read", unquote(view.group(1)), [(first, last)])]
    if created := _AGY_CREATED.search(content):
        return [("write", unquote(created.group(1)), None)]
    return []


def events(rollout):
    """Ordered ("read", path, ranges) and ("write", path, None) events
    recorded in a Claude, Codex or agy session."""
    out = []
    for row in _rows(rollout):
        if row.get("type") == "response_item":
            out += _codex_events(row.get("payload", {}))
            continue
        if "step_index" in row:
            out += _agy_events(row)
            continue
        if row.get("type") != "assistant":
            continue
        for block in row.get("message", {}).get("content", []):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            args = block.get("input", {})
            name = block.get("name")
            if name == "Read" and args.get("file_path"):
                first = int(args.get("offset") or 1)
                count = int(args.get("limit") or CLAUDE_READ_DEFAULT_LIMIT)
                out.append(("read", args["file_path"], [(first, first + count - 1)]))
            elif name in ("Write", "Edit", "MultiEdit") and args.get("file_path"):
                out.append(("write", args["file_path"], None))
            elif name == "Bash" and args.get("command"):
                out += _shell_events(args["command"])
    return out


def file_reads(rollout):
    """Ordered (path, ranges) reads recorded in a Claude, Codex or agy session."""
    return [(p, r) for kind, p, r in events(rollout) if kind == "read"]


def reaction_pairs(referenced, outputs):
    """The declared output that is each referenced file's reaction, or None.

    A reaction shares its file's name, so `chapters/02-later.md` pairs with
    `drafts/02-later.md`. The pairing applies only when every referenced file
    has one; a turn that declares a single notes file has no per-file
    reactions to order.
    """
    by_name = {Path(o).name: o for o in outputs}
    pairs = [by_name.get(Path(r).name) for r in referenced]
    return pairs if referenced and None not in pairs else None


def reactions_before_next(evs, referenced, pairs):
    """Whether each referenced file was first opened only after the reaction
    to the file before it was written.

    This is what keeps a reaction from drawing on later material, and reading
    in order does not show it: a seat can open every chapter in order and
    still open three before writing about the first. Returns True or False,
    or None when a paired reaction's write is not in the record, which is
    unproven rather than out of order. A file never opened is left to the
    coverage check.
    """
    first_open, first_write = {}, {}
    for index, (kind, path, _) in enumerate(evs):
        if kind == "read":
            first_open.setdefault(path, index)
            continue
        for output in pairs:
            if path == output or path.endswith("/" + output):
                first_write.setdefault(output, index)
    if any(output not in first_write for output in pairs):
        return None
    for previous, following in zip(pairs, referenced[1:]):
        opened = first_open.get(following)
        if opened is not None and opened < first_write[previous]:
            return False
    return True


def reactions_unrevised(evs, referenced, pairs):
    """Whether no reaction was written again once the next file had opened.

    A seat told not to revise a reaction can still go back and rewrite one,
    and the order check cannot see it, because it times each reaction by its
    first write. A later write turns the file into what the seat thought after
    reading on. Reopening an earlier file to look at it is not a revision. The
    last reaction has no next file, so nothing it is written after counts.
    Returns None where `reactions_before_next` would, since a reaction whose
    first write is missing cannot be dated. A later write the parser cannot
    recognize is not seen, so True means none was recorded.
    """
    first_open, writes = {}, {}
    for index, (kind, path, _) in enumerate(evs):
        if kind == "read":
            first_open.setdefault(path, index)
            continue
        for output in pairs:
            if path == output or path.endswith("/" + output):
                writes.setdefault(output, []).append(index)
    if any(output not in writes for output in pairs):
        return None
    for output, following in zip(pairs, referenced[1:]):
        opened = first_open.get(following)
        if opened is not None and any(later > opened for later in writes[output][1:]):
            return False
    return True


def coverage(reads, referenced):
    """Check a declared reading list against the reads a session recorded.

    Returns ``(fully_read, in_order, unproven)``: how many of the
    ``referenced`` files had every line covered, whether each file was first
    opened after the one listed before it, and the names of the files whose
    full reading is not proven. ``in_order`` is False only when the record
    shows two files opened out of order, and None when nothing is out of
    order but a file was never seen opening, since a read the record cannot
    parse leaves exactly that gap.
    """
    covered = {}
    first_seen = {}
    for index, (path, ranges) in enumerate(reads):
        first_seen.setdefault(path, index)
        lines = covered.setdefault(path, set())
        if ranges is None:
            lines.add("all")
        else:
            for first, last in ranges:
                lines.update(range(first, last + 1))
    fully, unproven = 0, []
    for path in referenced:
        total = len(Path(path).read_text(encoding="utf-8").splitlines())
        seen = covered.get(path, set())
        if "all" in seen or all(n in seen for n in range(1, total + 1)):
            fully += 1
        else:
            unproven.append(Path(path).name)
    seen = [first_seen[p] for p in referenced if p in first_seen]
    if seen != sorted(seen):
        in_order = False
    else:
        in_order = len(seen) == len(referenced) or None
    return fully, in_order, unproven
