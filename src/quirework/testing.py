"""Keep a test session out of the person's own Quirework state.

Quirework keeps state in the running user's home: run records and usage
under the XDG state tree, seat homes and locks under the cache tree, the
per-user configuration, a provenance home, and the harnesses' logins and
sessions. A test suite that drives Quirework and redirects only some of
these writes into the rest, silently: a suite that moved the state tree
but not the cache left seat homes in its author's real cache.

A suite opts in with one line in its top-level ``conftest.py``::

    pytest_plugins = ["quirework.testing"]

or with ``-p quirework.testing`` in its pytest options. Quirework's own
suite does the same, so the list below is the one Quirework tests itself
against, and a location Quirework adds is covered here, in the same
release, with no edit on the suite's side.

For the whole session the plugin gives the process a home of its own:

- ``HOME`` and the four XDG trees (state, cache, config, data) point into
  a sandbox, so every location derived from either lands there, including
  ones a later release adds;
- ``QUIREWORK_PROVENANCE_HOME`` and ``CODEX_HOME`` are dropped, since each
  names a real directory outright;
- ``QUIREWORK_USAGE=0`` records no command use, and ``QUIREWORK_CANARY=0``
  refuses any launch whose canary a test did not stub, rather than spend a
  real model's tokens;
- ``PYTHONUSERBASE`` keeps naming the real user base, so a tool installed
  with ``pip --user`` (Quarto, for one) still runs in a subprocess, and the
  user's fonts stay visible through links into the real font directories.

``XDG_RUNTIME_DIR`` is left alone. The session bus is found through it,
and a proxy's socket path must fit in 107 bytes, which a sandbox path
would not; what Quirework puts there is a socket that lives as long as
one launch.

A write that still reaches a real location is a leak, and leaks fail.
Before redirecting, the plugin notes the real XDG trees, the harnesses'
homes and the real provenance home. An audit hook then refuses any write
into them from this process with `IsolationLeak`, and the test it happened
in fails, even when the code under test swallowed the exception. A leak
outside any test fails the session. Writes by subprocesses are not seen by
the hook; they inherit the redirected environment instead.

The sandbox sits beside ``--basetemp`` when the suite chooses one, and in
the system's temporary directory otherwise. A passing session removes it;
a failing one keeps it and says where.

Nothing here is imported at run time: only pytest loads this module.
"""

from __future__ import annotations

import os
import shutil
import site
import sys
import tempfile
from pathlib import Path

import pytest

# Set by the plugin, so a nested session (pytest run by a test, or a worker
# process) keeps the first session's notion of what is real.
_ACTIVE = "QUIREWORK_TEST_SANDBOX"
_GUARDED = "QUIREWORK_TEST_GUARDED"
_TREES = {
    "XDG_STATE_HOME": ".local/state",
    "XDG_CACHE_HOME": ".cache",
    "XDG_CONFIG_HOME": ".config",
    "XDG_DATA_HOME": ".local/share",
}
_DROPPED = ("QUIREWORK_PROVENANCE_HOME", "CODEX_HOME")
_SWITCHED_OFF = {"QUIREWORK_USAGE": "0", "QUIREWORK_CANARY": "0"}

_SANDBOX = pytest.StashKey[Path]()
_OWNED = pytest.StashKey[bool]()

_leaks: list[str] = []
_reported = 0
_guarded: tuple[str, ...] = ()
_exempt: tuple[str, ...] = ()
_hooked = False


class IsolationLeak(AssertionError):
    """A test wrote where the session promised nothing would be written."""


def _tree(home: Path, variable: str) -> Path:
    value = os.environ.get(variable)
    if value and Path(value).is_absolute():
        return Path(value)
    return home / _TREES[variable]


def _real_locations() -> list[Path]:
    """The person's own per-user locations, read before anything is redirected."""
    home = Path.home()
    places = [_tree(home, variable) for variable in _TREES]
    places += [
        home / ".claude",
        home / ".claude.json",
        Path(os.environ.get("CODEX_HOME") or home / ".codex"),
        home / ".gemini",
    ]
    from quirework.project import ConfigurationError
    from quirework.provenance import store

    try:
        places.append(store.find_home().path)
    except ConfigurationError:
        pass  # an unreadable per-user file names no home to protect
    return places


def _forms(path: Path) -> set[str]:
    """A path as written and as resolved, since either may be the one used."""
    literal = os.path.normpath(os.path.abspath(path))
    return {literal, os.path.realpath(literal)}


# Each write-like audit event, and the positions of the paths it writes.
_WRITES = {
    "os.mkdir": (0,),
    "os.rename": (0, 1),
    "os.remove": (0,),
    "os.rmdir": (0,),
    "os.symlink": (1,),
    "os.link": (1,),
    "os.truncate": (0,),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.utime": (0,),
    "shutil.copyfile": (1,),
    "shutil.copymode": (1,),
    "shutil.copystat": (1,),
    "shutil.copytree": (1,),
    "shutil.move": (0, 1),
    "shutil.rmtree": (0,),
    "sqlite3.connect": (0,),
}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


def _under(path: str, roots: tuple[str, ...]) -> str | None:
    for root in roots:
        if path == root or path.startswith(root + os.sep):
            return root
    return None


def _inside(target) -> str | None:
    """The guarded location ``target`` falls in, or None.

    Resolved, so a link from the sandbox into a real directory, such as the
    one that keeps the user's fonts visible, is no way out."""
    if isinstance(target, int) or target is None:
        return None  # a descriptor: its opening was already seen
    try:
        path = os.fsdecode(os.fspath(target))
    except TypeError:
        return None
    path = os.path.realpath(os.path.join(os.getcwd(), path))
    if _under(path, _exempt):
        return None
    return _under(path, _guarded)


def _audit(event: str, args: tuple) -> None:
    if event == "open":
        target, mode, flags = args
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & _WRITE_FLAGS
        )
        targets = (target,) if writing else ()
    elif event in _WRITES:
        targets = tuple(args[i] for i in _WRITES[event] if i < len(args))
    else:
        return
    for target in targets:
        root = _inside(target)
        if root is not None:
            message = (
                f"{event} {os.fsdecode(os.fspath(target))!r} reaches {root}, a real "
                "per-user location this test session was isolated from"
            )
            _leaks.append(message)
            raise IsolationLeak(message)


def _interpreter() -> list[Path]:
    """Where this Python keeps its own files. An environment may live inside
    a guarded tree (pipenv keeps them under the data tree), and the bytecode
    Python caches there as it imports is not a test's doing."""
    places = {sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix}
    places.add(site.getusersitepackages())
    if sys.pycache_prefix:
        places.add(sys.pycache_prefix)
    return [Path(p) for p in places]


def _guard(places: list[Path]) -> None:
    global _guarded, _exempt, _hooked
    _guarded = tuple(sorted({form for place in places for form in _forms(place)}))
    _exempt = tuple(sorted({f for p in _interpreter() for f in _forms(p)}))
    if not _hooked:
        sys.addaudithook(_audit)  # process-wide, and never removed
        _hooked = True


def _link_fonts(real_home: Path, real_data: Path, home: Path, data: Path) -> None:
    """Keep the user's fonts visible from the sandbox. A write through either
    link resolves into a guarded tree and is refused like any other."""
    for source, link in (
        (real_data / "fonts", data / "fonts"),
        (real_home / ".fonts", home / ".fonts"),
    ):
        if source.is_dir() and not link.exists():
            link.symlink_to(source, target_is_directory=True)


@pytest.hookimpl(trylast=True)
def pytest_configure(config):
    """Redirect every per-user location before the first test is collected.

    Last, so a suite's own ``pytest_configure`` may choose ``--basetemp``
    first and the sandbox can sit beside it.
    """
    nested = os.environ.get(_ACTIVE)
    if nested:
        # A session inside an isolated one: the real locations are the ones
        # the outer session noted, and the sandbox is already in force.
        config.stash[_SANDBOX] = Path(nested)
        config.stash[_OWNED] = False
        _guard([Path(p) for p in os.environ[_GUARDED].split(os.pathsep) if p])
        return
    real = _real_locations()
    real_home, real_data = Path.home(), _tree(Path.home(), "XDG_DATA_HOME")
    user_base = site.getuserbase()
    basetemp = config.option.basetemp
    parent = Path(basetemp).resolve().parent if basetemp else None
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)
    sandbox = Path(tempfile.mkdtemp(prefix="quirework-home-", dir=parent))
    config.stash[_SANDBOX] = sandbox
    config.stash[_OWNED] = True
    home = sandbox / "home"
    home.mkdir(mode=0o700)
    os.environ["HOME"] = str(home)
    for variable in _TREES:
        tree = sandbox / variable.removeprefix("XDG_").removesuffix("_HOME").lower()
        tree.mkdir(mode=0o700)
        os.environ[variable] = str(tree)
    for variable in _DROPPED:
        os.environ.pop(variable, None)
    os.environ.update(_SWITCHED_OFF)
    os.environ.setdefault("PYTHONUSERBASE", user_base)
    _link_fonts(real_home, real_data, home, Path(os.environ["XDG_DATA_HOME"]))
    os.environ[_ACTIVE] = str(sandbox)
    os.environ[_GUARDED] = os.pathsep.join(str(p) for p in real)
    _guard(real)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """Fail the test a leak happened in, even if the code swallowed it."""
    global _reported
    report = yield
    fresh, _reported = _leaks[_reported:], len(_leaks)
    if fresh and not report.failed:
        report.outcome = "failed"
        report.longrepr = "\n".join(["IsolationLeak:", *fresh])
    return report


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """Fail a session that leaked outside any test; tidy a passing one."""
    stray = _leaks[_reported:]
    if stray:
        sys.stderr.write("\nIsolationLeak outside any test:\n  " + "\n  ".join(stray))
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    sandbox = session.config.stash.get(_SANDBOX, None)
    if sandbox is None or not session.config.stash.get(_OWNED, False):
        return
    if session.exitstatus == pytest.ExitCode.OK:
        if sandbox.exists():  # a suite may have removed the run around it
            shutil.rmtree(sandbox)
    else:
        sys.stderr.write(f"\nquirework.testing kept the session's home: {sandbox}\n")
