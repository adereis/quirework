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
  the file and the lines it showed, and its newer `read_file`, whose result
  names only the lines, so the file comes from the call it answers;
- shell commands, from Claude's `Bash` tool, agy's `run_command` or any
  Codex call: `cat`, `nl`, `sed -n 'A,Bp'` or `'Ap'`, and `head -n N` or
  `head -N`.

A relative path is resolved where the shell resolved it: the directory
the call names, or else the one the harness recorded for the row or the
turn. Codex runs a call with no `workdir` in the turn's working
directory, which its rollout records, so a seat told `input/notes.md`
is counted at that file. A call whose directory cannot be known leaves
its relative paths uncounted.

Writes are recognized as conservatively: Claude's `Write` and `Edit` tools,
Codex's `apply_patch` (an added or updated file), agy's `write_to_file` and
its newer `write_file` and `edit_file` (from the result, as for a view), and
shell redirection or `tee`. A redirection target is a write and never a
read, and the body of a heredoc is text, not commands.
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
# `sed -n 'Np'`, `'N,Mp'`, or `'N,$p'`, which prints from line N to the end.
_SED_RANGE = re.compile(r"^(\d+)(?:,(\d+|\$))?p$")
# A command string inside Codex's JavaScript exec tool: `cmd: "..."` or
# `"cmd": "..."`, with the string's own escapes.
_JS_CMD = re.compile(r"""["']?cmd["']?\s*:\s*("(?:[^"\\]|\\.)*")""")
# The same call's working directory, when it names one. A `workdir` written
# any other way than a plain string cannot be read, and then the command's
# directory is unknown rather than the turn's.
_JS_WORKDIR = re.compile(r"""["']?workdir["']?\s*:\s*""")
_JS_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
# What agy records as the result of a view and of a write. Each names its own
# file, so neither has to be matched back to the call that asked for it, and
# a call agy rejected leaves no such result to count.
_AGY_VIEW = re.compile(
    r"File Path: `file://([^`]+)`\nTotal Lines: \d+\n(?:Total Bytes: \d+\n)?"
    r"Showing lines (\d+) to (\d+)"
)
_AGY_CREATED = re.compile(r"Created file file://(\S+) with requested content")
# What agy's newer `read_file` records: the whole lines A to B, or, for a read
# cut to agy's size limit, which bytes of the line range it showed. A cut
# read's body can begin and end inside a line, and those lines were not shown
# whole. Each shown line is numbered `n: `.
_AGY_SHOWN = re.compile(r"Showing lines (\d+)-(\d+) of \d+")
# A piece that finishes the range ends its sentence there. One that stops at
# the size limit goes on, "truncated at the N-byte limit", and tells the
# model how to fetch the rest; the first piece of a long file always does.
_AGY_PARTIAL = re.compile(
    r"Partial read \(lines (\d+)-(\d+) of \d+\)\. "
    r"Showing bytes (\d+)-(\d+) of (\d+) in this line range"
    r"(?:\.|, truncated at the \d+-byte limit\..*)"
)
_AGY_LINE = re.compile(r"(\d+):(?: |$)")
# What its `edit_file` records when it changed something: how many
# replacements it made, in which file. `write_file` says `Created file.`
# whether it created the file or wrote over it.
_AGY_EDITED = re.compile(r"Made [1-9]\d* replacements? in ")
# The newer file tools, which are read with their call, and the steps that
# answer a call in agy's command-line transcripts: its result, or the error
# it failed with.
_AGY_PAIRED = ("read_file", "write_file", "edit_file")
_AGY_ANSWERS = ("GENERIC", "ERROR_MESSAGE")


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
            first, end = int(match.group(1)), match.group(2)
            # The last line's number is the file's to say, so `$` stays open
            # here and coverage closes it against the file it counts.
            last = None if end == "$" else int(end or first)
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
    inclusive (first, last) line pairs, where a last of None runs to the
    file's end. A heredoc's body is skipped."""
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


def _workdir(named, cwd):
    """The directory a call ran in: the one it names, resolved against the
    turn's, or the turn's when it names none. None when neither is known."""
    if named is None:
        return cwd
    if not isinstance(named, str):
        return None
    return _resolve(named, cwd)


def _patched(name, cwd):
    """The file a patch names. A relative name is kept as written when the
    directory is unknown, since an answer file is matched by its ending."""
    name = name.strip()
    return _resolve(name, cwd) or name


def _js_objects(text):
    """(start, end) of each `{...}` in a JavaScript snippet, innermost first
    within each nesting, with string literals and comments skipped so a
    brace inside one does not count. An unbalanced brace leaves its object
    out, and a call found in no object has no directory that can be known."""
    spans, opened, i, n = [], [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'`":
            i += 1
            while i < n and text[i] != c:
                i += 2 if text[i] == "\\" else 1
        elif text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i < 0 else i
        elif text.startswith("/*", i):
            i = text.find("*/", i + 2)
            i = n if i < 0 else i + 1
        elif c == "{":
            opened.append(i)
        elif c == "}" and opened:
            spans.append((opened.pop(), i + 1))
        i += 1
    return spans


def _js_call_workdir(text, at, spans, cwd):
    """The directory of the exec call whose `cmd` sits at ``at``: the
    `workdir` in the same object, or the turn's when the object names none."""
    inside = [s for s in spans if s[0] < at < s[1]]
    if not inside:
        return None
    start, end = max(inside)
    named = _JS_WORKDIR.search(text, start, end)
    if not named:
        return cwd
    value = _JS_STRING.match(text, named.end(), end)
    if not value:
        return None
    try:
        return _workdir(json.loads(value.group(0)), cwd)
    except json.JSONDecodeError:
        return None


def _codex_events(payload, cwd=None):
    """Ordered read and write events one Codex response item carries.

    ``cwd`` is the turn's working directory from the rollout, where Codex
    runs a call that names no directory of its own.
    """
    kind = payload.get("type", "")
    if kind == "local_shell_call":
        command = payload.get("action", {}).get("command")
        workdir = _workdir(payload.get("action", {}).get("working_directory"), cwd)
        if isinstance(command, list):
            command = shlex.join(command)
        return _shell_events(command, workdir) if isinstance(command, str) else []
    if kind == "function_call":
        try:
            args = json.loads(payload.get("arguments") or "{}")
        except json.JSONDecodeError:
            return []
        workdir = _workdir(args.get("workdir"), cwd)
        if payload.get("name") == "apply_patch" or "input" in args:
            patch = args.get("input") or ""
            return [
                ("write", _patched(p, workdir), None)
                for p in _PATCH_FILE.findall(patch)
            ]
        command = args.get("cmd") or args.get("command")
        if isinstance(command, list):
            command = shlex.join(command)
        if not isinstance(command, str):
            return []
        return _shell_events(command, workdir)
    if kind == "custom_tool_call":
        # A JavaScript snippet awaits its tool calls in the order it states
        # them, so its commands and patches are ordered by position.
        text = payload.get("input") or ""
        spans = _js_objects(text)
        found = []
        for match in _JS_CMD.finditer(text):
            try:
                command = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            workdir = _js_call_workdir(text, match.start(), spans, cwd)
            found.append((match.start(), _shell_events(command, workdir)))
        for match in _PATCH_FILE.finditer(text):
            found.append(
                (match.start(), [("write", _patched(match.group(1), cwd), None)])
            )
        return [e for _, evs in sorted(found, key=lambda f: f[0]) for e in evs]
    return []


def _agy_results(row, steps):
    """The step that answers each of a response's calls, or None for each.

    agy records a response and then one step per call it made, in the order
    of the calls, so call i is answered by the step i + 1 places after the
    response. A call that failed is answered in its place by an error. When
    the steps that follow are not exactly that many answers, which one
    answers which call would be a guess, and none is matched.
    """
    calls = row.get("tool_calls") or []
    index = row.get("step_index")
    if not isinstance(index, int):
        return [None] * len(calls)
    results = [steps.get(index + 1 + n) for n in range(len(calls))]
    after = steps.get(index + 1 + len(calls))
    if any(r is None or r.get("type") not in _AGY_ANSWERS for r in results) or (
        after is not None and after.get("type") in _AGY_ANSWERS
    ):
        return [None] * len(calls)
    return results


def _agy_whole_lines(partial, body):
    """(first, last) of the lines a cut read showed whole, or None.

    The numbered lines must run on without a gap to the end of the body, after
    at most one line cut at its start, so text that only looks numbered is
    never taken for a line. A read that starts past its range's first byte
    may also begin with a cut line that happens to look numbered, and one
    that stops short of the range's last byte ends inside a line, so each
    such edge line is left out.
    """
    first, last, start, end, size = (int(g) for g in partial.groups())
    if body and not body[-1]:
        body = body[:-1]
    numbers = []
    for line in reversed(body):
        found = _AGY_LINE.match(line)
        if not found or (numbers and int(found.group(1)) != numbers[-1] - 1):
            break
        numbers.append(int(found.group(1)))
    lead = len(body) - len(numbers)
    if not numbers or lead > 1 or (lead == 1 and start == 1):
        return None
    low, high = numbers[-1], numbers[0]
    if start > 1 and lead == 0:
        low += 1
    if end < size:
        high -= 1
    return (low, high) if first <= low <= high <= last else None


def _covers(spans, size):
    """Whether byte spans ``[start, end)`` cover ``0`` to ``size`` with no gap."""
    reached = 0
    for start, end in sorted(spans):
        if start > reached:
            return False
        reached = max(reached, end)
    return reached >= size


def _agy_paired(name, path, result, pieces=None):
    """What a call to one of agy's newer file tools did, from the step that
    answers it: the file is the call's, the lines are the result's."""
    if result is None or result.get("type") != "GENERIC":
        return []
    if result.get("status") != "DONE":
        return []
    if not isinstance(path, str) or not path.startswith("/"):
        return []
    lines = (result.get("content") or "").split("\n")
    while lines and lines[0].startswith(("Created At:", "Completed At:")):
        lines.pop(0)
    head, body = (lines[0], lines[1:]) if lines else ("", [])
    if name == "write_file":
        return [("write", path, None)] if head.startswith("Created file.") else []
    if name == "edit_file":
        # Its result names the file too, and it must be the call's.
        made = _AGY_EDITED.match(head)
        named = made and head[made.end() :].startswith(path + " ")
        return [("write", path, None)] if named else []
    if shown := _AGY_SHOWN.fullmatch(head):
        return [("read", path, [(int(shown.group(1)), int(shown.group(2)))])]
    if partial := _AGY_PARTIAL.fullmatch(head):
        if pieces is not None:
            first, last, start, end, size = (int(g) for g in partial.groups())
            pieces.append((path, first, last, start, end, size))
        if span := _agy_whole_lines(partial, body):
            return [("read", path, [span])]
    return []


def _agy_events(row, steps, pieces=None):
    """Ordered read and write events one step of an agy transcript carries.

    A planner step lists the tools it called and each result is a step of its
    own. A view or a write by the older tools is read from its result, which
    names the file it touched and, for a view, the lines agy actually showed,
    which can be fewer than the call asked for. The newer `read_file` and
    `write_file` results name no file, so each is read with the call it
    answers (see `_agy_results`), and so is an `edit_file`, whose result
    must name the call's file. A shell command is read from the call, as
    Claude's Bash is.
    """
    if row.get("type") == "PLANNER_RESPONSE":
        found = []
        calls = row.get("tool_calls") or []
        for call, result in zip(calls, _agy_results(row, steps)):
            args = call.get("args") or {}
            command = args.get("CommandLine")
            if call.get("name") == "run_command" and isinstance(command, str):
                found += _shell_events(command, args.get("Cwd"))
            elif call.get("name") in _AGY_PAIRED:
                found += _agy_paired(call["name"], args.get("Path"), result, pieces)
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
    rows = _rows(rollout)
    steps = {r["step_index"]: r for r in rows if isinstance(r.get("step_index"), int)}
    # Byte spans agy showed of each line range, read in pieces.
    ranges, whole = {}, set()
    # Codex records the working directory once for the session and again for
    # every turn, and a call that names none runs in the turn's.
    cwd = None
    for row in rows:
        if row.get("type") in ("session_meta", "turn_context"):
            cwd = (row.get("payload") or {}).get("cwd") or cwd
            continue
        if row.get("type") == "response_item":
            out += _codex_events(row.get("payload", {}), cwd)
            continue
        if "step_index" in row:
            pieces = []
            out += _agy_events(row, steps, pieces)
            for path, first, last, start, end, size in pieces:
                # A long range is read in pieces, each cut at agy's size
                # limit, and the line a cut falls in is whole in neither
                # piece. Once the pieces of one range cover all its bytes,
                # every line of the range has been shown.
                key = (path, first, last, size)
                spans = ranges.setdefault(key, [])
                spans.append((start, end))
                if key not in whole and _covers(spans, size):
                    whole.add(key)
                    out.append(("read", path, [(first, last)]))
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
                # Claude Code records the directory each row ran in, so a
                # relative path resolves as the shell resolved it.
                out += _shell_events(args["command"], row.get("cwd"))
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


def coverage(reads, referenced, local=None):
    """Check a declared reading list against the reads a session recorded.

    Returns ``(fully_read, in_order, unproven)``: how many of the
    ``referenced`` files had every line covered, whether each file was first
    opened after the one listed before it, and the names of the files whose
    full reading is not proven. ``in_order`` is False only when the record
    shows two files opened out of order, and None when nothing is out of
    order but a file was never seen opening, since a read the record cannot
    parse leaves exactly that gap.

    ``referenced`` names each file as the seat saw it. ``local`` maps such a
    name to the copy the lines are counted in, for material a seat reads at
    a path inside its jail that is not where the file sits on the host.
    """
    local = local or {}
    covered = {}
    # The first line of the earliest read that ran to the file's end.
    tails = {}
    first_seen = {}
    for index, (path, ranges) in enumerate(reads):
        first_seen.setdefault(path, index)
        lines = covered.setdefault(path, set())
        if ranges is None:
            lines.add("all")
        else:
            for first, last in ranges:
                if last is None:
                    tails[path] = min(first, tails.get(path, first))
                else:
                    lines.update(range(first, last + 1))
    fully, unproven = 0, []
    for path in referenced:
        total = len(
            Path(local.get(path, path)).read_text(encoding="utf-8").splitlines()
        )
        seen = covered.get(path, set())
        tail = tails.get(path, total + 1)
        if "all" in seen or all(n in seen or n >= tail for n in range(1, total + 1)):
            fully += 1
        else:
            unproven.append(Path(path).name)
    seen = [first_seen[p] for p in referenced if p in first_seen]
    if seen != sorted(seen):
        in_order = False
    else:
        in_order = len(seen) == len(referenced) or None
    return fully, in_order, unproven
