"""What a harness puts in a seat's context before the prompt, and the proof.

Every harness loads a project's instruction files on its own, before the
first word of the prompt, and none of that shows in its flags. A frame that
asks a seat to read one of those files again pays for it twice, on every
later request, since each request carries the whole conversation; a frame
that tells a seat a file is already loaded when it is not leaves the seat
without it. So a driver needs to know, per harness, what was loaded and
whether whole, and to read the harness's own record afterwards to check.

Measured, not assumed:

- **Codex** reads `AGENTS.override.md`, or else `AGENTS.md`, in every
  directory from the repository root down to the working directory, and
  joins them with a blank line. It stops at `project_doc_max_bytes` of the
  joined text, 32 KiB by default, mid-word and without a word to the seat:
  a 43 KB guide arrived three quarters whole, and the seat re-read the file
  to find the rest. `codex_doc_bytes` sizes the limit to the chain, so a
  driver that passes it gets every file whole. The session record holds
  the loaded text under `# AGENTS.md instructions for <dir>`.
- **Claude Code** reads `CLAUDE.md` and `CLAUDE.local.md` in the working
  directory and the directories above it, the files those import with
  `@path`, and a subdirectory's `CLAUDE.md` when the seat first works
  there. It reads `AGENTS.md` only when one of them imports it or a plugin
  setting in the user's own configuration asks for it, and a jailed seat's
  private home carries no such setting: on Claude Code 2.1.316 a reviewer
  in a project whose `CLAUDE.md` imports nothing loaded that file alone,
  where the operator's session in the same checkout held `AGENTS.md` too.
  Its session record lists each file it loaded with the text it loaded.
- **agy** shows no such record to this driver, so nothing is claimed for it.

The same facts serve twice: before launch, to tell a seat what it already
holds (`sentence`) and to show an operator a frame that asks for a loaded
file again (`frame_leads`); after the turn, to prove what was loaded
(`receipt`) and count the seat's reads of it (`rereads`).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

# Codex's own limit on the joined instruction text when nothing sets one.
CODEX_DOC_DEFAULT = 32 * 1024
# What Codex puts between two files of the chain.
CODEX_JOIN = "\n\n"
CODEX_HEADER = "# AGENTS.md instructions for "
_CODEX_BODY = re.compile(r"<INSTRUCTIONS>\n(.*)\n</INSTRUCTIONS>\s*\Z", re.DOTALL)

CLAUDE_NAMES = ("CLAUDE.md", "CLAUDE.local.md")
# How deep Claude Code follows `@path` imports.
CLAUDE_IMPORT_DEPTH = 5
# An import is `@path` at the start of a line or after a space, never inside
# code, which `_imports` strips first.
_IMPORT = re.compile(r"(?:^|(?<=\s))@([^\s`]+)", re.MULTILINE)
_FENCED = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
_CODE_SPAN = re.compile(r"`[^`\n]*`")


def _top(cwd):
    """The repository root above ``cwd``, or ``cwd`` outside a repository."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    proc = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    top = proc.stdout.strip() if proc.returncode == 0 else ""
    return Path(top).resolve() if top else Path(cwd).resolve()


def _dirs(cwd):
    """The directories from the repository root down to ``cwd``."""
    cwd = Path(cwd).resolve()
    top = _top(cwd)
    if cwd != top and top not in cwd.parents:
        return [cwd]
    out = [cwd]
    while out[-1] != top:
        out.append(out[-1].parent)
    return list(reversed(out))


def codex_chain(cwd):
    """The instruction files Codex loads for a session started in ``cwd``."""
    out = []
    for d in _dirs(cwd):
        for name in ("AGENTS.override.md", "AGENTS.md"):
            path = d / name
            if path.is_file() and path.stat().st_size:
                out.append(path)
                break
    return out


def codex_doc_bytes(cwd):
    """A `project_doc_max_bytes` that holds the whole chain, never below Codex's
    own default, rounded up to a kibibyte."""
    chain = codex_chain(cwd)
    need = sum(p.stat().st_size for p in chain) + len(CODEX_JOIN) * max(
        0, len(chain) - 1
    )
    return max(CODEX_DOC_DEFAULT, -(-need // 1024) * 1024)


def _imports(path, top, depth, seen):
    """The files ``path`` imports, depth first, inside the repository."""
    text = _CODE_SPAN.sub("", _FENCED.sub("", path.read_text(errors="replace")))
    out = []
    for match in _IMPORT.finditer(text):
        target = (path.parent / match.group(1).rstrip(".,;:)")).resolve()
        if (
            target in seen
            or not target.is_file()
            or (target != top and top not in target.parents)
        ):
            continue
        seen.add(target)
        out.append(target)
        if depth > 1:
            out += _imports(target, top, depth - 1, seen)
    return out


def claude_chain(cwd):
    """The instruction files Claude Code loads at the start of a session in
    ``cwd``, from the repository root down, each followed by its imports."""
    top = _top(cwd)
    out, seen = [], set()
    for d in _dirs(cwd):
        for name in CLAUDE_NAMES:
            path = (d / name).resolve()
            if path.is_file() and path not in seen:
                seen.add(path)
                out.append(path)
                out += _imports(path, top, CLAUDE_IMPORT_DEPTH, seen)
    return out


def loaded(harness, cwd):
    """The files ``harness`` loads whole before the prompt, or None when this
    driver cannot know. Codex's are whole only under `codex_doc_bytes`."""
    if harness == "codex":
        return codex_chain(cwd)
    if harness == "claude":
        return claude_chain(cwd)
    return None


def _shown(path, root):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(path)


def sentence(harness, root):
    """What a frame's `{preloaded}` field says to the seat.

    It names what the harness loaded, and then the project guides it did not,
    the `AGENTS.md` chain every harness here shares, as files to read once:
    a frame that stops asking for the guide because one harness loads it
    must not leave a seat on another harness without it.
    """
    files = loaded(harness, root)
    guides = [_shown(p, root) for p in codex_chain(root)]
    if files is None:
        said = (
            "This driver cannot see what your harness loaded into your context "
            "before this message."
        )
        if guides:
            said += (
                f" Read {_and(guides)} once, in full, unless your context already "
                f"holds {'it' if len(guides) == 1 else 'them'} whole."
            )
        return said
    held = [_shown(p, root) for p in files]
    missing = [g for g in guides if g not in held]
    if held:
        said = (
            f"Your harness loaded {_and(held)} into your context, whole, before "
            f"this message. Do not read {'it' if len(held) == 1 else 'them'} again."
        )
    else:
        said = "Your harness loaded no project guide into your context."
    if missing:
        said += (
            f" It did not load {_and(missing)}: read "
            f"{'it' if len(missing) == 1 else 'them'} once, in full."
        )
    return said


def _and(items):
    items = list(items)
    if len(items) < 3:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def frame_leads(text, harness, root, *, skip=""):
    """Lines of a composed prompt that ask the seat to read a file its harness
    already loaded whole: the line names the file first, as a reading list
    does, or follows a "read" with it. A lead, never a refusal; ``skip`` is
    text to leave out, such as the `{preloaded}` sentence itself."""
    files = loaded(harness, root)
    if not files:
        return []
    if skip:
        text = text.replace(skip, "")
    out = []
    for path in files:
        name = re.escape(_shown(path, root))
        asked = re.compile(
            rf"^\s*(?:[-*]\s+)?`?{name}`?(?![\w/.-])"
            rf"|\bread\b[^.\n]{{0,40}}?(?<![\w/.-])`?{name}`?(?![\w/.-])",
            re.IGNORECASE,
        )
        for number, line in enumerate(text.splitlines(), 1):
            if asked.search(line):
                out.append(
                    {"line": number, "file": _shown(path, root), "text": line.strip()}
                )
    return out


def _rows(path):
    out = []
    for line in Path(path).read_text(errors="replace").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _codex_loaded(rows):
    for row in rows:
        payload = row.get("payload") or {}
        if row.get("type") != "response_item" or payload.get("type") != "message":
            continue
        for part in payload.get("content") or []:
            text = part.get("text") if isinstance(part, dict) else None
            if text and text.startswith(CODEX_HEADER):
                found = _CODEX_BODY.search(text)
                return found.group(1) if found else None
    return None


def _codex_receipt(rows, root):
    body = _codex_loaded(rows)
    chain = codex_chain(root)
    if body is None:
        return [
            {"path": _shown(p, root), "bytes": p.stat().st_size, "loaded": "absent"}
            for p in chain
        ]
    texts = [p.read_text(errors="replace") for p in chain]
    joined = CODEX_JOIN.join(texts)
    have = len(body.encode())
    differs = not joined.encode().startswith(body.encode())
    out, start = [], 0
    for path, text in zip(chain, texts):
        size = len(text.encode())
        end = start + size
        if differs:
            state = "differs from the file now"
        elif have >= end:
            state = "whole"
        elif have > start:
            state = f"cut at {have - start} bytes"
        else:
            state = "absent"
        out.append({"path": _shown(path, root), "bytes": size, "loaded": state})
        start = end + len(CODEX_JOIN.encode())
    return out


def _claude_receipt(rows, root):
    out = []
    for row in rows:
        attachment = row.get("attachment") if row.get("type") == "attachment" else None
        if not isinstance(attachment, dict):
            continue
        if attachment.get("type") == "instructions":
            files, when = attachment.get("files") or [], "start"
        elif attachment.get("type") == "nested_memory":
            files, when = [attachment.get("content") or {}], "on entry"
        else:
            continue
        for item in files:
            path = item.get("path")
            if not path:
                continue
            disk = Path(path)
            content = item.get("content") or ""
            if not disk.is_file():
                state = "not on disk now"
            elif " ".join(content.split()) == " ".join(
                disk.read_text(errors="replace").split()
            ):
                state = "whole"
            else:
                state = "differs from the file now"
            out.append(
                {
                    "path": _shown(path, root),
                    "bytes": disk.stat().st_size if disk.is_file() else None,
                    "loaded": state,
                    "when": when,
                }
            )
    return out or None


def receipt(harness, natives, root):
    """What the harness's own session record says it loaded, file by file, or
    None when the record cannot say."""
    rows = [r for path in natives or [] for r in _rows(path)]
    if not rows:
        return None
    if harness == "codex":
        return _codex_receipt(rows, root)
    if harness == "claude":
        return _claude_receipt(rows, root)
    return None


def rereads(natives, files, root):
    """How many times the seat read each of ``files`` itself."""
    from quirework.providers import reads

    wanted = {str(Path(root) / f): f for f in files}
    counts = dict.fromkeys(files, 0)
    for native in natives or []:
        for path, _ in reads.file_reads(native):
            key = wanted.get(str(Path(path)))
            if key:
                counts[key] += 1
    return counts


def summary(harness, loaded_files, counts):
    """One receipt line: what was loaded, and what the seat read again."""
    if loaded_files is None:
        return (
            f"preload not shown: no {harness} session record read here says "
            "what it loaded"
        )
    start = [f for f in loaded_files if f.get("when", "start") == "start"]
    if not start:
        return f"preload: {harness} loaded no project guide before the prompt"
    parts = []
    for f in start:
        state = f["loaded"]
        part = f"{f['path']} {state.upper() if state != 'whole' else state}"
        if f.get("bytes") is not None and state != "whole":
            part += f" ({f['bytes']} bytes on disk)"
        again = counts.get(f["path"], 0)
        if again:
            part += f", read again {again} time{'s' if again > 1 else ''}"
        parts.append(part)
    entered = [f["path"] for f in loaded_files if f.get("when") == "on entry"]
    line = f"preload: {harness} loaded " + "; ".join(parts)
    if entered:
        line += f"; on entry, {_and(sorted(set(entered)))}"
    return line
