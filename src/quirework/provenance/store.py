"""Where a project's captured sessions live, and which of them it accepted.

A provenance corpus has two halves that change at different speeds. The
sessions are large and, once settled, never edited: hundreds of megabytes of
transcripts that the work's own history should not have to carry, and that a
repository-wide grep or rewrite should not have to wade through. The decisions
about them are small and edited by people: which sessions count, which were
removed and why, which are about the apparatus. So the sessions go to a *store*
outside the checkout, and the decisions stay in the *ledger*, the directory
``provenance.root`` names, which is versioned with the work.

The store has two areas. Capture writes into ``pending/``, where a session that
is still running is simply rewritten as it grows. ``accept`` copies a pending
capture into ``accepted/`` as an immutable *version*, and the ledger's
``accepted.txt`` gains one line per file: its SHA-256 and its path in the
store. That list is what a git commit of the session used to mean ("this
session is evidence"), and it keeps what git used to guarantee: a clone can
tell whether the bytes it holds are the ones that were accepted.

A version is a directory named by a hash of what it holds, and nothing rewrites
one. Every checkout of a project shares its store, while each branch carries
its own ``accepted.txt``. If accepting a grown session overwrote the file an
older branch's list names, that branch's checks would fail over something it
never touched. With immutable versions, every list keeps resolving to exactly
the bytes it accepted.

The unit of acceptance is a *session*. For Claude Code, Codex and Antigravity
that is one transcript. For a web conversation it is every file captured from
it: the raw export, the rendered transcript, and the files and artifacts
extracted from it. A version holds the whole unit, so files a reader expects to
find side by side were accepted together.

The store is found by name. ``provenance.store`` names it under the provenance
home: ``$QUIREWORK_PROVENANCE_HOME`` when that is set, else ``provenance.home``
in the per-user configuration, else ``$XDG_DATA_HOME/quirework/provenance``.
The name is committed with the project, so a fresh worktree and a fresh clone
find the same store with nothing set per checkout. The per-user file is what
lets a hook launched outside the author's shell find it too, since such a hook
never sees a variable the shell's profile exported. ``provenance.store_dir`` names a
directory instead, for a project that keeps its sessions somewhere specific,
inside its own repository included. The store records an identity and
``accepted.txt`` names it, so a ledger is refused a store that another project
created under the same name.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import re
import shutil
import sys
import tempfile
import time
import tomllib
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from quirework.project import (
    ConfigurationError,
    current_project,
    user_config_path,
    user_settings,
)
from quirework.runs.storage import shown

CC, AGY, CODEX, WEB = "cc-sessions", "agy-sessions", "codex-sessions", "conversations"
FAMILIES = (CC, AGY, CODEX, WEB)
# The short names the ledger's lists, the catalog and `show` use.
KINDS = {"cc": CC, "agy": AGY, "codex": CODEX, "web": WEB}
KIND_OF = {family: kind for kind, family in KINDS.items()}
# The families whose captures carry their source's modification time, so the
# time says when the session last grew rather than when it was captured.
LIVE_FAMILIES = (CC, AGY, CODEX)

MANIFEST = "accepted.txt"
REMOVED = "removed-sessions.txt"
MAKING = "making-sessions.txt"
IDENTITY = "store.toml"

# A capture modified more recently than this may belong to a session that is
# still running, and accepting it would only accept a prefix of it.
SETTLE_SECONDS = 2 * 3600

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
_SID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SHA = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[0-9a-f]{16}")

_MANIFEST_HEADER = (
    "# The sessions this project accepted into its provenance corpus: each\n"
    "# file's SHA-256, then its path in the store. `quirework provenance\n"
    "# accept` and `reject` write it; edit it only through them.\n"
)
_LIST_HEADERS = {
    REMOVED: (
        "# Sessions curated out of this project's corpus, one per line, as\n"
        "# '<kind> <session id>  # reason'. Capture never brings one back.\n"
    ),
    MAKING: (
        "# Sessions about the apparatus rather than the work, one per line, as\n"
        "# '<kind> <session id>  # reason'. The cold-read reads them as scope\n"
        "# 'making'; search, show and metrics read them like any other.\n"
    ),
}


# --------------------------------------------------------------------------- #
# where the store and the ledger are
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Home:
    """The directory named stores live under, and which setting chose it."""

    path: Path
    chosen_by: str  # "environment", "user" or "default"

    def describe(self):
        """The directory and what chose it, for a person reading output."""
        if self.chosen_by == "environment":
            return f"{shown(self.path)}, from $QUIREWORK_PROVENANCE_HOME"
        config = shown(user_config_path())
        if self.chosen_by == "user":
            return f"{shown(self.path)}, from provenance.home in {config}"
        return (
            f"{shown(self.path)}, the default: $QUIREWORK_PROVENANCE_HOME is unset "
            f"and {config} sets no provenance.home"
        )


def find_home():
    """The provenance home: the variable, else the per-user file, else XDG data."""
    configured = os.environ.get("QUIREWORK_PROVENANCE_HOME")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            raise ConfigurationError(
                f"QUIREWORK_PROVENANCE_HOME must be an absolute path, not {configured!r}"
            )
        return Home(path, "environment")
    configured = user_settings().get("provenance", {}).get("home")
    if configured is not None:
        where = f"provenance.home in {shown(user_config_path())}"
        if not isinstance(configured, str) or not configured.strip():
            raise ConfigurationError(f"{where} must name a directory")
        path = Path(configured).expanduser()
        if not path.is_absolute():
            raise ConfigurationError(
                f"{where} must be an absolute path, not {configured!r}"
            )
        return Home(path, "user")
    data = os.environ.get("XDG_DATA_HOME")
    base = Path(data) if data and Path(data).is_absolute() else None
    path = (base or Path.home() / ".local/share") / "quirework" / "provenance"
    return Home(path, "default")


def home():
    """The directory a store named by ``provenance.store`` lives under."""
    return find_home().path


def describe_home(project=None):
    """The home and what chose it, or ``None`` for a store named by directory."""
    project = current_project() if project is None else project
    if project.get("provenance.store_dir") is not None:
        return None
    return find_home().describe()


def location(project=None):
    """The store directory this project's configuration selects."""
    project = current_project() if project is None else project
    name = project.get("provenance.store")
    directory = project.get("provenance.store_dir")
    if (name is None) == (directory is None):
        raise ConfigurationError(
            "declare exactly one of provenance.store, a name for the store under "
            "the provenance home, or provenance.store_dir, a directory"
        )
    if directory is not None:
        return project.path("provenance.store_dir")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ConfigurationError(
            f"provenance.store must be a plain name such as 'my-book', not {name!r}; "
            "a path belongs in provenance.store_dir"
        )
    return (home() / name).resolve()


def ledger(project=None):
    """The versioned directory that holds the decisions about the sessions."""
    project = current_project() if project is None else project
    return project.path("provenance.root", ".quirework/provenance")


def versioned(path):
    """The repository holding ``path``, or ``None`` when no git work tree does."""
    from quirework.provenance import worktrees

    path = Path(path)
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate if worktrees.common_git_dir(candidate) else None
    return None


# --------------------------------------------------------------------------- #
# sessions, files and versions
# --------------------------------------------------------------------------- #
def unit_of(family, rel):
    """The session a file in one family belongs to.

    A transcript is its own session and is named by its stem. A web
    conversation's files share its id: ``<id>.raw.json``, ``<id>.md``, and
    whatever was extracted under ``files/<id>/`` and ``artifacts/<id>/``.
    """
    parts = PurePosixPath(rel).parts
    if family == WEB and len(parts) > 2 and parts[0] in ("files", "artifacts"):
        return parts[1]
    return parts[-1].split(".", 1)[0]


@dataclass(frozen=True)
class Entry:
    """One accepted file: its family, version, path within the family, hash."""

    family: str
    version: str
    rel: str
    sha: str

    @property
    def unit(self):
        return unit_of(self.family, self.rel)

    @property
    def reference(self):
        """How the file is named wherever a version would only get in the way."""
        return f"{self.family}/{self.rel}"

    @property
    def stored(self):
        return f"{self.family}/{self.version}/{self.rel}"


def _safe_rel(rel):
    parts = PurePosixPath(rel).parts
    return bool(parts) and not PurePosixPath(rel).is_absolute() and ".." not in parts


# Parsed manifests, keyed by the file's absolute path and checked against its
# inode, size and modification time before use. Every write replaces the file,
# so a changed list never matches a stale parse, and two ledgers never share an
# entry.
_PARSED = {}


def read_manifest(path):
    """``(identity, entries)`` from a ledger's ``accepted.txt``.

    Strict, because the file is written by this module and nothing else: a line
    that does not parse is damage, and reading around it would quietly shrink
    the corpus.
    """
    path = Path(path).absolute()
    try:
        st = path.stat()
    except FileNotFoundError:
        return None, []
    signature = (st.st_ino, st.st_size, st.st_mtime_ns)
    cached = _PARSED.get(str(path))
    if cached is not None and cached[0] == signature:
        return cached[1], list(cached[2])
    identity, entries = _parse_manifest(path)
    _PARSED[str(path)] = (signature, identity, tuple(entries))
    return identity, entries


def _parse_manifest(path):
    identity, entries, seen = None, [], set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        where = f"{shown(path)}:{lineno}"
        if identity is None:
            word, _, value = text.partition(" ")
            if word != "store" or not _UUID.fullmatch(value.strip()):
                raise ConfigurationError(
                    f"{where}: the first entry must be 'store <id>', naming the "
                    "store this ledger belongs to"
                )
            identity = value.strip()
            continue
        sha, _, stored = text.partition("  ")
        parts = PurePosixPath(stored.strip()).parts
        if (
            not _SHA.fullmatch(sha)
            or len(parts) < 3
            or parts[0] not in FAMILIES
            or not _VERSION.fullmatch(parts[1])
            or not _safe_rel("/".join(parts[2:]))
        ):
            raise ConfigurationError(
                f"{where}: not '<sha256>  <family>/<version>/<file>': {text!r}"
            )
        entry = Entry(parts[0], parts[1], "/".join(parts[2:]), sha)
        if entry.reference in seen:
            raise ConfigurationError(f"{where}: {entry.reference} is listed twice")
        seen.add(entry.reference)
        entries.append(entry)
    if identity is None:
        raise ConfigurationError(f"{shown(path)} names no store")
    return identity, entries


def write_manifest(path, identity, entries):
    lines = [_MANIFEST_HEADER, f"store {identity}\n"]
    lines += [
        f"{e.sha}  {e.stored}\n" for e in sorted(entries, key=lambda e: e.reference)
    ]
    _write_atomic(Path(path), "".join(lines))


def _write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def version_of(files):
    """The name of the version holding ``{rel: sha}``: a hash of exactly that."""
    digest = hashlib.sha256()
    for rel, sha in sorted(files.items()):
        digest.update(f"{rel}\t{sha}\n".encode())
    return digest.hexdigest()[:16]


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #
def _identity(root):
    path = Path(root) / IDENTITY
    if not path.is_file():
        return None
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8")).get("id")
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot read {shown(path)}: {exc}") from exc
    if not isinstance(value, str) or not _UUID.fullmatch(value):
        raise ConfigurationError(f"{shown(path)} records no valid store id")
    return value


@dataclass(frozen=True)
class Store:
    root: Path
    ledger: Path
    identity: str

    @property
    def manifest(self):
        return self.ledger / MANIFEST

    def entries(self):
        identity, entries = read_manifest(self.manifest)
        if identity != self.identity:
            raise ConfigurationError(
                f"{shown(self.manifest)} changed its store while it was read"
            )
        return entries

    def pending_dir(self, family):
        return self.root / "pending" / family

    def accepted_dir(self, family):
        return self.root / "accepted" / family

    def path(self, entry):
        return self.accepted_dir(entry.family) / entry.version / entry.rel

    def pending_files(self, family):
        """``{rel: path}`` for every pending file of one family."""
        base = self.pending_dir(family)
        if not base.is_dir():
            return {}
        return {
            p.relative_to(base).as_posix(): p
            for p in sorted(base.rglob("*"))
            if p.is_file()
            and not any(part.startswith(".") for part in p.relative_to(base).parts)
        }

    def pending_units(self):
        """``{(family, unit): {rel: path}}`` for everything waiting to be accepted."""
        units = {}
        for family in FAMILIES:
            for rel, path in self.pending_files(family).items():
                units.setdefault((family, unit_of(family, rel)), {})[rel] = path
        return units


def _relocate(project):
    """How to point this project at a store kept somewhere else, as a clause."""
    if project.get("provenance.store_dir") is not None:
        return "correct provenance.store_dir"
    return (
        "point $QUIREWORK_PROVENANCE_HOME or provenance.home in "
        f"{shown(user_config_path())} at the directory that holds it"
    )


def _no_store(project, root, led):
    """Why there is no store at ``root``, and what to do about it.

    A missing store is a first run only when the ledger has accepted nothing.
    Once it has, the store exists somewhere, and the likelier cause is a home
    chosen by other settings than the ones the store was made under: a hook
    whose harness never saw the variable a shell profile exports finds the
    default home instead. ``store init`` there would make a second, empty store
    under the same identity, and later captures would land in it unremarked.
    """
    chosen = describe_home(project)
    where = (
        f"there is no provenance store at {shown(root)}; its home is {chosen}"
        if chosen
        else f"there is no provenance store at {shown(root)}, the directory "
        "provenance.store_dir names"
    )
    _, entries = read_manifest(led / MANIFEST)
    if not entries:
        return f"{where}. Run `quirework provenance store init` to create it"
    return (
        f"{where}. {shown(led / MANIFEST)} lists {len(entries)} accepted "
        "file(s), so this project's store is likely elsewhere on this machine: "
        f"{_relocate(project)}. On a machine that has none of them, `quirework "
        "provenance store init` creates an empty store here to copy them into"
    )


def open_store(project=None):
    """The project's store, refused unless it exists and its ledger names it."""
    project = current_project() if project is None else project
    root = location(project)
    led = ledger(project)
    stored = _identity(root)
    if stored is None:
        raise ConfigurationError(_no_store(project, root, led))
    listed, _ = read_manifest(led / MANIFEST)
    if listed is None:
        raise ConfigurationError(
            f"{shown(led / MANIFEST)} is missing, so nothing binds this ledger to "
            "its store; run `quirework provenance store init`"
        )
    if listed != stored:
        raise ConfigurationError(
            f"the store at {shown(root)} is not this project's: it is store "
            f"{stored}, and {shown(led / MANIFEST)} names store {listed}. Give "
            "this project its own provenance.store name"
        )
    return Store(root, led, stored)


def init(project=None):
    """Create the store or bind the ledger to it; return ``(store, created)``.

    A ledger that already names a store id keeps it, so a second machine or a
    fresh clone creates an empty store under the identity its ledger was
    accepted into, and copies the sessions in afterwards. A store that exists
    is never adopted by a ledger that names nothing, since that is what two
    projects choosing the same name looks like.
    """
    project = current_project() if project is None else project
    root = location(project)
    led = ledger(project)
    listed, _ = read_manifest(led / MANIFEST)
    stored = _identity(root)
    if stored is not None:
        if listed is None:
            raise ConfigurationError(
                f"a store already exists at {shown(root)}, and this ledger names "
                "none. It belongs to another project unless this ledger's "
                f"{MANIFEST} was lost; give this project its own provenance.store "
                "name"
            )
        if listed != stored:
            raise ConfigurationError(
                f"the store at {shown(root)} is store {stored}, and "
                f"{shown(led / MANIFEST)} names store {listed}"
            )
        return Store(root, led, stored), False
    if root.exists() and any(root.iterdir()):
        raise ConfigurationError(
            f"{shown(root)} is not empty and is not a provenance store; "
            "choose another location"
        )
    identity = listed or str(uuid.uuid4())
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    for family in FAMILIES:
        (root / "pending" / family).mkdir(parents=True, exist_ok=True)
        (root / "accepted" / family).mkdir(parents=True, exist_ok=True)
    _write_atomic(
        root / IDENTITY,
        "# A Quirework provenance store. A project's ledger names this id in\n"
        f"# its {MANIFEST}, and a ledger naming another id is refused.\n"
        f'id = "{identity}"\n',
    )
    if listed is None:
        write_manifest(led / MANIFEST, identity, [])
    return Store(root, led, identity), True


@contextmanager
def _locked(store):
    """One writer at a time across every checkout sharing the store.

    The lock is taken on the store's own directory rather than on a lock file,
    so a store kept inside a repository never gains a file nobody accepted.
    """
    fd = os.open(store.root, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


# --------------------------------------------------------------------------- #
# reading the corpus
# --------------------------------------------------------------------------- #
def _present(store, entries):
    paths = [store.path(e) for e in entries]
    missing = [e for e, p in zip(entries, paths) if not p.is_file()]
    if missing:
        raise ConfigurationError(
            f"the store at {shown(store.root)} lacks {len(missing)} accepted "
            f"file(s), e.g. {missing[0].stored}; run `quirework provenance store "
            "verify`"
        )
    return paths


def files(family, pattern="*", project=None):
    """Accepted files at the top of one family's sessions, in name order.

    The corpus every reader shares. Files extracted below a web conversation
    (``files/``, ``artifacts/``) are not sessions and are not listed.
    """
    store = open_store(project)
    chosen = [
        e
        for e in sorted(store.entries(), key=lambda e: e.rel)
        if e.family == family and "/" not in e.rel and fnmatch(e.rel, pattern)
    ]
    return _present(store, chosen)


def pending(family, pattern="*", project=None):
    """Pending files at the top of one family's sessions, in name order."""
    store = open_store(project)
    return [
        path
        for rel, path in sorted(store.pending_files(family).items())
        if "/" not in rel and fnmatch(rel, pattern)
    ]


def path_of(family, rel, project=None):
    """The accepted file a family-relative name refers to, or ``None``."""
    store = open_store(project)
    for entry in store.entries():
        if entry.family == family and entry.rel == rel:
            return _present(store, [entry])[0]
    return None


def newest(family, rel, project=None):
    """The latest capture of a file: pending if there is one, else accepted."""
    store = open_store(project)
    waiting = store.pending_dir(family) / rel
    return waiting if waiting.is_file() else path_of(family, rel, project)


def split(path, project=None):
    """``(family, rel)`` for a file in the store, or ``None`` for any other path.

    A project that declares no store has none to hold the path, so the answer
    is ``None`` there too, rather than a demand to configure one.
    """
    project = current_project() if project is None else project
    if (
        project.get("provenance.store") is None
        and project.get("provenance.store_dir") is None
    ):
        return None
    path = Path(path).resolve()
    root = location(project)
    if not path.is_relative_to(root):
        return None
    parts = path.relative_to(root).parts
    if len(parts) >= 3 and parts[0] == "pending" and parts[1] in FAMILIES:
        return parts[1], "/".join(parts[2:])
    if len(parts) >= 4 and parts[0] == "accepted" and parts[1] in FAMILIES:
        return parts[1], "/".join(parts[3:])
    return None


def area(path, project=None):
    """``'pending'`` or ``'accepted'`` for a file in the store, else ``None``."""
    if split(path, project) is None:
        return None
    return Path(path).resolve().relative_to(location(project)).parts[0]


def family_of(path, project=None):
    """The family a store file belongs to, or ``None`` outside the store."""
    found = split(path, project)
    return found[0] if found else None


def family_of_reference(name, project=None):
    """The family a path or a recorded reference names, or ``None``.

    A recorded reference (``cc-sessions/<id>.jsonl``) carries its family as
    its first component; an absolute path is asked of the store.
    """
    if Path(name).is_absolute():
        return family_of(name, project)
    first = PurePosixPath(str(name)).parts[:1]
    return first[0] if first and first[0] in FAMILIES else None


def reference(path, project=None):
    """How a source is named in a record that must outlive where it is kept.

    A file in the store is named by its family and its name there, never by
    the store's location or the version holding it, so a cold-read report or a
    catalog row keeps its key when the store moves. Anything else is named
    relative to the project, or absolutely when it lies outside.
    """
    project = current_project() if project is None else project
    found = split(path, project)
    if found:
        return f"{found[0]}/{found[1]}"
    path = Path(path).resolve()
    return (
        str(path.relative_to(project.root))
        if path.is_relative_to(project.root)
        else str(path)
    )


# --------------------------------------------------------------------------- #
# the ledger's lists
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Listed:
    kind: str
    session: str
    reason: str


def _normal_id(value):
    return value.lower() if _UUID.fullmatch(value.lower()) else value


def listed(name, project=None):
    """The entries of one of the ledger's lists, in file order.

    A line that names no session is reported and skipped, never guessed at:
    every reader of the list goes through here, so none of them can disagree
    with another about what a line means.
    """
    path = ledger(project) / name
    out = []
    if not path.exists():
        return out
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        entry, _, reason = line.partition("#")
        fields = entry.split()
        if not fields:
            continue
        if len(fields) != 2 or fields[0] not in KINDS or not _SID.fullmatch(fields[1]):
            print(
                f"{name}:{lineno}: not '<kind> <session id>', ignoring "
                f"{entry.strip()!r}",
                file=sys.stderr,
            )
            continue
        out.append(Listed(fields[0], _normal_id(fields[1]), reason.strip()))
    return out


def removed_ids(kind=None, project=None):
    """Ids curated out of the corpus, of one kind or of every kind."""
    return {
        e.session for e in listed(REMOVED, project) if kind is None or e.kind == kind
    }


def add_to_list(name, kind, session, reason, project=None):
    """Append one entry unless the session is already listed; True if added."""
    if any(e.session == session for e in listed(name, project)):
        return False
    path = ledger(project) / name
    text = path.read_text(encoding="utf-8") if path.exists() else _LIST_HEADERS[name]
    if text and not text.endswith("\n"):
        text += "\n"
    text += f"{kind} {session}  # {reason}\n" if reason else f"{kind} {session}\n"
    _write_atomic(path, text)
    return True


# --------------------------------------------------------------------------- #
# accepting and rejecting
# --------------------------------------------------------------------------- #
def _select(selectors, candidates, verb):
    """The ``(family, unit)`` keys the selectors name, each by an id prefix."""
    chosen = []
    for selector in selectors:
        sel = _normal_id(selector)
        found = sorted(k for k in candidates if k[1] == sel) or sorted(
            k for k in candidates if k[1].startswith(sel)
        )
        if not found:
            raise ValueError(f"no session to {verb} matches '{selector}'")
        if len(found) > 1:
            names = ", ".join(f"{KIND_OF[f]}:{u}" for f, u in found)
            raise ValueError(f"'{selector}' is ambiguous: {names}")
        if found[0] not in chosen:
            chosen.append(found[0])
    return chosen


def settled(paths, family, now=None):
    """Whether a capture has stopped growing long enough to accept."""
    if family not in LIVE_FAMILIES:
        return True
    now = time.time() if now is None else now
    return all(now - p.stat().st_mtime >= SETTLE_SECONDS for p in paths)


def _link_or_copy(source, target):
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _freeze(directory):
    for path in directory.rglob("*"):
        if path.is_file():
            path.chmod(path.stat().st_mode & ~0o222)


def _store_version(store, family, previous, waiting):
    """Write the version holding ``previous`` overlaid with ``waiting``.

    ``previous`` maps a file to the accepted entry it keeps; ``waiting`` maps a
    file to the pending capture that replaces or adds it. Pending files are
    hashed as copied, so a capture rewritten while this runs is either taken
    whole or left pending, never recorded under the wrong hash.
    """
    base = store.accepted_dir(family)
    base.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(dir=base, prefix=".accepting-"))
    try:
        hashes, taken = {}, {}
        for rel, entry in previous.items():
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            _link_or_copy(store.path(entry), target)
            hashes[rel] = entry.sha
        for rel, source in waiting.items():
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)
            before = source.stat()
            shutil.copy2(source, target)
            hashes[rel] = sha256_file(target)
            taken[rel] = (before.st_ino, before.st_size, before.st_mtime_ns)
        version = version_of(hashes)
        final = base / version
        if final.exists():
            for rel, sha in hashes.items():
                if not (final / rel).is_file() or sha256_file(final / rel) != sha:
                    raise RuntimeError(
                        f"{shown(final)} exists but does not hold what its name "
                        "promises; run `quirework provenance store verify`"
                    )
        else:
            _freeze(stage)
            os.rename(stage, final)
            stage = None
        return version, hashes, taken
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)


def _clear_pending(store, family, waiting, taken):
    """Remove the pending files that were accepted, unless they grew meanwhile."""
    base = store.pending_dir(family)
    for rel, source in waiting.items():
        try:
            st = source.stat()
        except FileNotFoundError:
            continue
        if (st.st_ino, st.st_size, st.st_mtime_ns) == taken[rel]:
            source.unlink()
        parent = source.parent
        while parent != base and parent.is_relative_to(base):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def accept(selectors=(), everything=False, live=False, project=None):
    """Accept pending sessions into this ledger; return ``(accepted, skipped)``.

    Each session becomes one new version holding its previously accepted files
    with its pending ones laid over them, and its lines in ``accepted.txt`` are
    replaced. A session still growing is refused unless ``live`` says to take
    the prefix captured so far. Named sessions are refused outright; with
    ``everything`` a running one is skipped and reported instead, since the
    session doing the accepting is always among them.
    """
    project = current_project() if project is None else project
    store = open_store(project)
    with _locked(store):
        units = store.pending_units()
        keys = sorted(units) if everything else _select(selectors, units, "accept")
        removed = removed_ids(project=project)
        skipped = []
        for family, unit in list(keys):
            if unit in removed:
                raise ValueError(
                    f"{KIND_OF[family]} {unit} is on {REMOVED}; take it off the "
                    "list before accepting it"
                )
            if not live and not settled(units[(family, unit)].values(), family):
                if not everything:
                    raise ValueError(
                        f"{KIND_OF[family]} {unit} changed in the last "
                        f"{SETTLE_SECONDS // 3600} hours and may still be running; "
                        "accept it later, or pass --live to accept what was "
                        "captured"
                    )
                keys.remove((family, unit))
                skipped.append((family, unit))
        entries = store.entries()
        done = []
        for family, unit in keys:
            previous = {
                e.rel: e for e in entries if e.family == family and e.unit == unit
            }
            waiting = units[(family, unit)]
            version, hashes, taken = _store_version(store, family, previous, waiting)
            entries = [
                e for e in entries if not (e.family == family and e.unit == unit)
            ] + [Entry(family, version, rel, sha) for rel, sha in hashes.items()]
            write_manifest(store.manifest, store.identity, entries)
            _clear_pending(store, family, waiting, taken)
            done.append((family, unit, version, bool(previous)))
        return done, skipped


def reject(selectors, reason, project=None):
    """Curate sessions out: drop them from this ledger and list them as removed.

    A pending capture is deleted. An accepted version stays in the store,
    because another branch's ledger may still name it; it only leaves this
    ledger's list.
    """
    if not reason or not reason.strip():
        raise ValueError("say why with --reason; the removed list keeps the reason")
    project = current_project() if project is None else project
    store = open_store(project)
    with _locked(store):
        units = store.pending_units()
        entries = store.entries()
        candidates = set(units) | {(e.family, e.unit) for e in entries}
        keys = _select(selectors, candidates, "reject")
        for family, unit in keys:
            for path in units.get((family, unit), {}).values():
                path.unlink()
            entries = [
                e for e in entries if not (e.family == family and e.unit == unit)
            ]
            write_manifest(store.manifest, store.identity, entries)
            add_to_list(REMOVED, KIND_OF[family], unit, reason.strip(), project)
        return keys


# --------------------------------------------------------------------------- #
# status and verification
# --------------------------------------------------------------------------- #
def elsewhere(store, entries=None):
    """Sessions with a version in the store that this ledger does not list.

    Another branch's ledger accepted them, or a branch that did was abandoned.
    Either way their bytes are safe in the store and nothing here reads them.
    """
    entries = store.entries() if entries is None else entries
    mine = {(e.family, e.unit) for e in entries}
    found = set()
    for family in FAMILIES:
        base = store.accepted_dir(family)
        if not base.is_dir():
            continue
        for version in base.iterdir():
            if not _VERSION.fullmatch(version.name):
                continue
            for path in version.rglob("*"):
                if path.is_file():
                    key = (
                        family,
                        unit_of(family, path.relative_to(version).as_posix()),
                    )
                    if key not in mine:
                        found.add(key)
    return found


def verify(project=None):
    """Problems with the accepted files, one line each; empty when sound."""
    store = open_store(project)
    problems = []
    for entry in store.entries():
        path = store.path(entry)
        if not path.is_file():
            problems.append(f"missing   {entry.stored}")
        elif sha256_file(path) != entry.sha:
            problems.append(f"altered   {entry.stored}")
    return problems


def describe_location(store):
    """One line saying where the store is and what keeps a copy of it."""
    repository = versioned(store.root)
    if repository is None:
        return f"{shown(store.root)} (outside version control; back it up)"
    return f"{shown(store.root)} (inside the repository at {shown(repository)})"


def _age(seconds):
    if seconds < 3600:
        return f"{int(seconds // 60)} min"
    if seconds < 172800:
        return f"{int(seconds // 3600)} h"
    return f"{int(seconds // 86400)} days"


def uncaptured():
    """The Claude Code sessions no capture holds as they stand, or ``None``.

    Only Claude Code's, because it is the one harness captured unattended, by a
    ``SessionEnd`` hook, and the one whose own cleanup deletes the transcripts.
    A hook that fails does so as its session closes, with nobody reading, so
    without this its session would wait unseen until that cleanup took it.
    Codex and Antigravity are captured by hand, and their sweeps report what
    they did. ``None`` when Claude Code has never run in this project.
    """
    from quirework.provenance.ingestion import claude

    return claude.survey()


def cmd_status(_args):
    store = open_store()
    entries = store.entries()
    accepted = {(e.family, e.unit) for e in entries}
    units = store.pending_units()
    now = time.time()
    print(f"store     {describe_location(store)}")
    chosen = describe_home()
    if chosen:
        print(f"home      {chosen}")
    print(f"ledger    {shown(store.manifest)}")
    print(f"accepted  {len(accepted)} session(s), {len(entries)} file(s)")
    print(f"pending   {len(units)} session(s)")
    for (family, unit), found in sorted(
        units.items(), key=lambda kv: max(p.stat().st_mtime for p in kv[1].values())
    ):
        newest_mtime = max(p.stat().st_mtime for p in found.values())
        state = "grown" if (family, unit) in accepted else "new"
        live = "" if settled(found.values(), family, now) else ", may still be running"
        print(
            f"  {KIND_OF[family]:5} {unit}  {state}, changed "
            f"{_age(now - newest_mtime)} ago{live}"
        )
    missed = uncaptured()
    if missed is not None:
        print(f"uncaptured {len(missed.due)} Claude Code session(s)")
        for path in sorted(missed.due, key=lambda p: p.stat().st_mtime):
            state = (
                "grown since its capture" if path in missed.grown else "never captured"
            )
            live = "" if settled([path], CC, now) else ", may still be running"
            print(
                f"  {'cc':5} {path.stem}  {state}, changed "
                f"{_age(now - path.stat().st_mtime)} ago{live}"
            )
    others = elsewhere(store, entries)
    if others:
        print(
            f"elsewhere {len(others)} session(s) in the store that this ledger does "
            "not list (accepted on another branch)"
        )
    if units:
        print(
            "next: read each with `quirework provenance show ID`, then "
            "`quirework provenance accept ID` or "
            "`quirework provenance reject ID --reason TEXT`"
        )
    if missed is not None and any(settled([p], CC, now) for p in missed.due):
        print(
            "next: capture the uncaptured sessions with `quirework provenance "
            "ingest claude`; one that is no longer running was missed by its "
            "capture hook, and Claude Code deletes a transcript 30 days after "
            "it last changed, by default"
        )
    return 0


def cmd_verify(_args):
    store = open_store()
    problems = verify()
    for line in problems:
        print(line)
    count = len(store.entries())
    if problems:
        print(f"{len(problems)} of {count} accepted file(s) do not match the ledger")
        return 1
    print(f"verified {count} accepted file(s) against {shown(store.manifest)}")
    return 0


def cmd_init(_args):
    if not current_project().get("provenance.enabled", False):
        raise ConfigurationError(
            "provenance is not enabled; set provenance.enabled = true and name "
            "the store before creating it"
        )
    store, created = init()
    if created:
        print(f"created store {store.identity} at {describe_location(store)}")
        chosen = describe_home()
        if chosen:
            print(f"its home is {chosen}")
        missing = [e for e in store.entries() if not store.path(e).is_file()]
        if missing:
            print(
                f"the ledger lists {len(missing)} accepted file(s) this new store "
                "lacks; copy the store's accepted/ directory from a machine that "
                "has it, then run `quirework provenance store verify`. If this "
                "machine already keeps them in another store, this one is in the "
                f"wrong place: {_relocate(current_project())}, and delete the "
                "empty store just created"
            )
    else:
        print(f"store {store.identity} is ready at {describe_location(store)}")
    return 0


def main(argv=None):
    """``quirework provenance store``: create, inspect and verify the store."""
    parser = argparse.ArgumentParser(
        prog="quirework provenance store",
        description="Create, inspect and verify the store that holds the sessions.",
    )
    parser.add_argument("action", choices=("init", "status", "verify"))
    args = parser.parse_args(argv)
    return {"init": cmd_init, "status": cmd_status, "verify": cmd_verify}[args.action](
        args
    )
