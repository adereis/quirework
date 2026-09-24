"""Durable records and cross-workflow leases; no model or editorial policy."""

from quirework.project import current_project
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
import tomllib
from contextlib import contextmanager
from pathlib import Path


def shown(path):
    """A path as it is worth reading: under the home directory, `~/`.

    For output a person reads, never for a record. An operator scanning a
    line wants the part that distinguishes this path from another one, and
    `/home/<user>` is the same prefix every time. Evidence keeps the literal
    path, because a receipt that abbreviates proves less than one that does
    not.
    """
    text = str(path)
    home = str(Path.home())
    if text == home:
        return "~"
    return "~/" + text[len(home) + 1 :] if text.startswith(home + os.sep) else text


def read(path):
    path = Path(path)
    text = path.read_text()
    return tomllib.loads(text) if path.suffix == ".toml" else json.loads(text)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
        temporary = f.name
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", value
    ):
        raise ValueError(f"invalid identifier: {value!r}")
    return value


def relative(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"invalid relative path: {value!r}")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path == Path("."):
        raise ValueError(f"invalid relative path: {value!r}")
    return path


def tree_digest(root):
    """One digest over every file under ``root``: names and bytes both.

    What a mounted read certifies. A seat that reads a directory in place
    receives nothing the record froze, so the record can only say which
    bytes it saw by naming them; this is that name. Symlinks are refused,
    as they are for a frozen tree, because a link can change what it
    points at without the tree changing at all.
    """
    listing = json.dumps(hashes(root), sort_keys=True).encode()
    return hashlib.sha256(listing).hexdigest()


def hashes(root):
    root = Path(root)
    if root.is_symlink():
        raise ValueError(f"symlink in frozen tree: {root}")
    result = {}
    for p in sorted(root.rglob("*")):
        if "__pycache__" in p.parts:
            continue
        if p.is_symlink():
            raise ValueError(f"symlink in frozen tree: {p}")
        if p.is_file():
            result[str(p.relative_to(root))] = digest(p)
        elif not p.is_dir():
            raise ValueError(f"non-regular file: {p}")
    return result


def process_identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
    except FileNotFoundError:
        return None
    return None if fields[0] == "Z" else fields[19]


@contextmanager
def lock(path, *, wait=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise RuntimeError(f"another operator owns {path}") from exc
        yield


@contextmanager
def slot(harness):
    # Join the existing workshop semaphore, including across worktrees. Do not
    # invent a second two-slot pool for readers. File descriptors own leases.
    if harness != "agy":
        yield
        return
    directory = current_project().account_state(harness) / ".locks"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    handles = [(directory / f"agy-{i}.lock").open("a+") for i in range(2)]
    acquired = False
    try:
        while not acquired:
            for f in handles:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    continue
            if not acquired:
                time.sleep(0.2)
        yield
    finally:
        for f in handles:
            f.close()
