"""Fetch a pinned third-party tool into the cache, verified before it unpacks.

Some jobs need a program this package has no business vendoring: an EPUB
validator is 32 MB of jars, an e-reader renderer is 29 MB of Lua, and neither
belongs in a prose repository. They are downloaded instead, and a download is
a supply-chain hole unless it is pinned and checked, so every caller supplies
a version, a URL and a SHA-256.

The order of operations is the point, and it is why this lives in one place
rather than once per tool: the archive's bytes are hashed BEFORE anything is
unpacked, so an archive that was corrupted or substituted never reaches the
filesystem as an executable. A second copy of that reasoning is a second
chance to get it wrong.

Everything lands under the cache root, one directory per tool and version, so
a clone shares the download and `quirework config template tmpfiles` collects
it when it stops being used.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path


class PinnedToolError(RuntimeError):
    """The tool could not be made available; the message is operator-facing."""


def cache_root() -> Path:
    """Where pinned tools live. XDG, so it is shared across clones."""
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "quirework"


def tool_root(name: str, version: str) -> Path:
    return cache_root() / f"{name}-{version}"


def find(name, version, member, *, env_override=None) -> Path | None:
    """The tool, by explicit override, then by cache. Never by PATH guess.

    An override is honored first so a distribution package or a CI image can
    supply its own copy. A PATH lookup is deliberately not a fallback: an
    unpinned binary of the right name is exactly what the pin exists to
    exclude.
    """
    if env_override:
        supplied = os.environ.get(env_override)
        if supplied:
            path = Path(supplied).expanduser()
            return path if path.exists() else None
    path = tool_root(name, version) / member
    return path if path.exists() else None


def _unpack(archive: Path, into: Path, member: str) -> None:
    """Extract, stripping the archive's own root directory if it has one.

    Some releases wrap everything in a `tool-1.2.3/` directory and some
    unpack straight into place, and the caller should not have to know which.
    The wanted member decides it rather than a shape heuristic: a tarball
    whose only top-level entry happens to be `bin/` is not wrapped, and
    guessing from the shape alone would strip exactly the directory the
    member is named against.

    Extracting to a staging directory and renaming is both simpler and safer
    than rewriting member names by hand: it keeps the standard library's own
    traversal guards and its handling of modes, so the executable bit
    survives.
    """
    staging = Path(tempfile.mkdtemp(prefix=f".{into.name}.", dir=into.parent))
    try:
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(staging)
        else:
            with tarfile.open(archive) as bundle:
                bundle.extractall(staging, filter="data")
        root = staging
        if not (staging / member).exists():
            entries = list(staging.iterdir())
            if len(entries) == 1 and entries[0].is_dir():
                root = entries[0]
        if into.exists():
            shutil.rmtree(into)
        # The tool appears at its final path complete or not at all, so an
        # interrupted unpack cannot be mistaken for an installed tool.
        os.replace(root, into)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def ensure(
    name,
    version,
    url,
    sha256,
    member,
    *,
    label=None,
    force=False,
    timeout=120,
    log=None,
) -> Path:
    """Make the pinned tool available and return the path to `member`.

    Raises `PinnedToolError` with an operator-facing message on any failure.
    """
    label = label or name
    log = log if log is not None else (lambda message: print(message))
    target = tool_root(name, version) / member
    if target.exists() and not force:
        return target

    root = cache_root()
    root.mkdir(parents=True, exist_ok=True)
    archive = root / url.rsplit("/", 1)[-1]

    if not archive.exists() or force:
        log(f"fetching {label} {version} ...")
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
                archive.write_bytes(response.read())
        except KeyboardInterrupt:
            # A half-written archive would be SKIPPED by the exists() test on
            # the next run and then fail its checksum, costing a second run to
            # recover from one ctrl-c.
            archive.unlink(missing_ok=True)
            raise
        except Exception as exc:  # network, TLS, 404
            archive.unlink(missing_ok=True)
            raise PinnedToolError(f"{name}: download failed: {exc}") from exc

    got = hashlib.sha256(archive.read_bytes()).hexdigest()
    if got != sha256:
        archive.unlink(missing_ok=True)
        raise PinnedToolError(
            f"{name}: checksum mismatch for {url}\n"
            f"  expected {sha256}\n  got      {got}\n"
            f"  the download was discarded."
        )

    _unpack(archive, tool_root(name, version), member)
    if not target.exists():
        raise PinnedToolError(f"{name}: {target} missing after unpack.")
    log(f"{name} {version} installed: {target}")
    return target


def install(name, version, url, sha256, member, *, label=None, force=False) -> int:
    """`ensure` as a command: prints, and returns a process exit status."""
    target = tool_root(name, version) / member
    if target.exists() and not force:
        print(f"{name} {version} already installed: {target}")
        return 0
    try:
        ensure(name, version, url, sha256, member, label=label, force=force)
    except PinnedToolError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0
