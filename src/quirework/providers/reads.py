"""Which files a seat read, and how much of each, from its native record.

A seat that fetches its own material leaves no frozen copy of what it read,
so the only evidence is the tool calls in its native session. This module
turns those calls into an ordered list of file reads, each with the lines it
covered, and checks a declared reading list against them.

The parsing is deliberately conservative. A read it cannot recognize, such
as a command assembled at run time or a shell idiom not listed here, is not
counted, so the check can say a file was not *proven* read in full but never
that an unread file was read. Recognized forms:

- Claude's `Read` tool, with its `offset` and `limit`;
- shell commands, from Claude's `Bash` tool or any Codex call: `cat`, `nl`,
  `sed -n 'A,Bp'` or `'Ap'`, and `head -n N` or `head -N`.
"""

import json
import re
import shlex
from pathlib import Path

# Claude's Read tool returns this many lines when no limit is given.
CLAUDE_READ_DEFAULT_LIMIT = 2000

_SEGMENT = re.compile(r"&&|\|\||[;|\n]")
_SED_RANGE = re.compile(r"^(\d+)(?:,(\d+))?p$")
# A command string inside Codex's JavaScript exec tool: `cmd: "..."` or
# `"cmd": "..."`, with the string's own escapes.
_JS_CMD = re.compile(r"""["']?cmd["']?\s*:\s*("(?:[^"\\]|\\.)*")""")


def _rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def _shell_reads(command, workdir=None):
    """(path, ranges) for each file a shell command prints. ranges is None
    for the whole file, else a list of inclusive (first, last) line pairs."""
    found = []
    for segment in _SEGMENT.split(command):
        try:
            words = shlex.split(segment)
        except ValueError:
            continue
        if not words:
            continue
        tool, args = words[0].rsplit("/", 1)[-1], words[1:]
        files, ranges = [], None
        if tool in ("cat", "nl"):
            files = [a for a in args if not a.startswith("-")]
        elif tool == "sed" and len(args) >= 3 and args[0] == "-n":
            match = _SED_RANGE.match(args[1])
            if match:
                first = int(match.group(1))
                last = int(match.group(2) or first)
                ranges, files = [(first, last)], args[2:]
        elif tool == "head":
            count, rest = None, []
            i = 0
            while i < len(args):
                a = args[i]
                if a == "-n" and i + 1 < len(args) and args[i + 1].isdigit():
                    count, i = int(args[i + 1]), i + 2
                    continue
                if re.fullmatch(r"-\d+", a):
                    count = int(a[1:])
                elif not a.startswith("-"):
                    rest.append(a)
                i += 1
            ranges, files = [(1, count if count is not None else 10)], rest
        for name in files:
            path = Path(name)
            if not path.is_absolute():
                if workdir is None:
                    continue
                path = Path(workdir) / path
            found.append((str(path), ranges))
    return found


def _codex_commands(payload):
    """Every shell command string one Codex response item carries."""
    kind = payload.get("type", "")
    if kind == "local_shell_call":
        command = payload.get("action", {}).get("command")
        workdir = payload.get("action", {}).get("working_directory")
        if isinstance(command, list):
            return [(shlex.join(command), workdir)]
        return [(command, workdir)] if isinstance(command, str) else []
    if kind == "function_call":
        try:
            args = json.loads(payload.get("arguments") or "{}")
        except json.JSONDecodeError:
            return []
        command = args.get("cmd") or args.get("command")
        if isinstance(command, list):
            command = shlex.join(command)
        return [(command, args.get("workdir"))] if isinstance(command, str) else []
    if kind == "custom_tool_call":
        text = payload.get("input") or ""
        commands = []
        for literal in _JS_CMD.findall(text):
            try:
                commands.append((json.loads(literal), None))
            except json.JSONDecodeError:
                continue
        return commands
    return []


def file_reads(rollout):
    """Ordered (path, ranges) reads recorded in a Claude or Codex session."""
    reads = []
    for row in _rows(rollout):
        if row.get("type") == "response_item":
            for command, workdir in _codex_commands(row.get("payload", {})):
                reads += _shell_reads(command, workdir)
            continue
        if row.get("type") != "assistant":
            continue
        for block in row.get("message", {}).get("content", []):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            args = block.get("input", {})
            if block.get("name") == "Read" and args.get("file_path"):
                first = int(args.get("offset") or 1)
                count = int(args.get("limit") or CLAUDE_READ_DEFAULT_LIMIT)
                reads.append((args["file_path"], [(first, first + count - 1)]))
            elif block.get("name") == "Bash" and args.get("command"):
                reads += _shell_reads(args["command"])
    return reads


def coverage(reads, referenced):
    """Check a declared reading list against the reads a session recorded.

    Returns ``(fully_read, in_order, unproven)``: how many of the
    ``referenced`` files had every line covered, whether each file was first
    opened after the one listed before it, and the names of the files whose
    full reading is not proven.
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
    order = [first_seen.get(p) for p in referenced]
    in_order = None not in order and order == sorted(order)
    return fully, in_order, unproven
