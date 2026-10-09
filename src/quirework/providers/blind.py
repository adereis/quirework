"""The blind-launch invariant, in one place. See ``docs/workflows.md#execution-and-recovery``.

Every headless ``claude -p`` that must judge, summarize, critique or rewrite the
text runs from a **neutral CWD outside the project tree**, so the project's own
guidance (``AGENTS.md``) and project-scoped memory never load.

The neutral CWD does *not* defeat the operator's own ``~/.claude`` layer, which
loads from any directory: neither the personal ``settings.json`` nor the global
``CLAUDE.md``. Two guards close that, and both are needed.

``BLIND_SETTINGS`` pins the **output style**. An ``Explanatory`` style makes the
model stop and teach (it emits craft-analysis blocks), which swaps a blind
seat's register from answering to reviewing and silently breaks comparability
with earlier runs. ``docs/workflows.md#execution-and-recovery`` owns the rule and the post-run
check.

``NO_SETTING_SOURCES`` blocks the global ``~/.claude/CLAUDE.md``. That leak was
assessed as harmless while the cold-read only asked who wrote the *prose* --
the global file carries dev-workflow preferences, not the thesis. The **making**
scope broke that premise: it asks who designed the *working practice*, and the
global file states some of that practice outright (commit hygiene, scratch-dir
and venv rules). A blind analyst on 2026-08-13 hit exactly this and said so --
"several of the AI's visible behaviors ... match those rules exactly, so I can't
be certain how much is the AI following a standing instruction versus
improvising" -- which is the one distinction the making brief exists to draw. A
canary probe confirmed the leak verbatim ("ALWAYS use ~/tmp, NEVER /tmp") and
confirmed this flag closes it. Do not drop it because the book run seems fine
without it: the book run was fine, and that is why nobody noticed.

**The flags are half of it; the other half is the jail below.** A neutral CWD
hides the project from a seat's *attention*, never from its *hands*: every
file-reading tool in every runner takes an absolute path. Measured 2026-08-14,
one probe per runner from an empty ``~/tmp`` dir -- a ``claude -p`` seat with
``--allowedTools "Read"`` read ``AGENTS.md`` and quoted it back, and a
``codex exec`` seat did the same through its shell under ``-s read-only``
(read-only stops writes, not reads). There is no exempt shape either, since
there is no way to ask claude for zero tools (see ``MIN_TOOLS``), so EVERY
blind seat is confined at the OS level, and ``docs/workflows.md#execution-and-recovery`` ("The run
dir is not a wall") owns the rule.
"""

from __future__ import annotations

import errno
import glob
import json
import os
import shutil
import subprocess
import sys
import re
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from quirework.project import current_project

NEUTRAL_CWD = Path(os.path.expanduser("~/tmp"))

# Overrides the operator's ~/.claude/settings.json for the blind child only.
# Kept alongside NO_SETTING_SOURCES rather than replaced by it: this pins the
# style positively, where the other merely stops a file from loading.
BLIND_SETTINGS = ["--settings", '{"outputStyle":"default"}']

# Loads NO setting sources (user / project / local), which is what keeps the
# operator's global ~/.claude/CLAUDE.md out of the child. `--setting-sources
# project` does NOT close it -- verified by canary probe, which still recovered
# the file verbatim. Auth and model selection are unaffected.
NO_SETTING_SOURCES = ["--setting-sources", ""]

# The SMALLEST tool set claude will accept, not an empty one -- because there
# is no empty one. `--tools ""` was carried here for months as "every built-in
# tool OFF"; the live canary measured it on 2026-08-15 and it is a **no-op**:
# an empty value (and "none") falls back to the DEFAULT set, so every seat that
# believed itself tools-off has had `Read,Edit,Write,Glob,Grep,Bash`. Only a
# non-empty allow-list actually restricts, so the honest floor is one harmless
# tool plus the jail, where `Read` can see nothing but an empty run dir.
#
# Still an allow-list, never a deny-list: a deny-list names tools by their
# harness names and drifts when one is renamed (this one used to name "Task",
# the subagent tool's OLD name, so a blind child could still spawn a subagent).
MIN_TOOLS = ["--tools", "Read"]

# MCP servers are delivered by the ACCOUNT, not by any file, so neither
# `--setting-sources ""` nor the jail removes them: the same canary found a
# blind seat holding the operator's Gmail (including `send_message`), Calendar
# and Drive tools. An empty strict config is what removes them, and it must
# ride on every claude launch.
NO_MCP = ["--mcp-config", '{"mcpServers":{}}', "--strict-mcp-config"]

# Codex has the same door, and every codex launch left it open until it was
# found. Its `apps` feature, on by default, gives a seat the account's
# connected apps as the built-in `codex_apps` MCP server: a jailed reviewer
# held hundreds of tools for mail, calendar, documents and code hosting,
# among them ones that commit, delete files and merge. A private home and
# `--ignore-user-config` remove neither, because the account supplies them.
# Turning the feature off removes the server and the catalogue of plugins
# Codex injects into the prompt with it. The canary asks Codex's own app
# server for the seat's MCP inventory (`canary.codex_servers`), so a release
# that brings the apps back by another switch refuses the launch.
CODEX_NO_APPS = ["-c", "features.apps=false"]

# The account's plugins arrive the same way, and Codex 0.162 turns both
# switches on. Every launch into a fresh private home then installs each
# remote plugin the account enables and caches the plugin catalogue: one
# refused launch grew a home from 3 MB to 33 MB, and longer seats held about
# 55 MB each. They are the same connected apps packaged as plugins, so they
# close with the apps, on every seat.
CODEX_NO_PLUGINS = [
    "-c",
    "features.plugins=false",
    "-c",
    "features.remote_plugin=false",
]

# Codex's catalogue puts most models on its second multi-agent version, which
# ignores `features.multi_agent=false`: every seat, a tool-free one included,
# is offered `spawn_agent` with overrides naming other models, so a seat
# could hand part of its work to a model its receipt never names. No setting
# removes the tool. A session limit of one thread, the seat itself, makes
# every spawn fail with "agent thread limit reached". Codex refuses a limit
# of zero, and refuses a misspelled key, so a rename stops the launch rather
# than reopening the door. A model still on the first version is unaffected,
# and `features.multi_agent=false` removes the tools there.
CODEX_NO_SUBAGENTS = [
    "-c",
    "features.multi_agent_v2.max_concurrent_threads_per_session=1",
]

# Image generation is a tool on every codex seat with a shell, and no seat
# is asked for an image. It spends the account's quota on whatever prompt
# the model writes.
CODEX_NO_IMAGES = ["-c", "features.image_generation=false"]

# What every codex seat turns off, whatever else its launcher gives it.
# Browser and computer use, also on by default, give a `codex exec` seat on
# Linux no tool under Codex 0.160.1, measured from the tools in the request
# a seat sends; the tool-free seats name them off regardless.
CODEX_SEAT = [*CODEX_NO_APPS, *CODEX_NO_PLUGINS, *CODEX_NO_SUBAGENTS, *CODEX_NO_IMAGES]


def _options():
    """Read policy from the selected project for this operation."""
    from types import SimpleNamespace

    PROJECT_MARKERS = tuple(
        current_project()
        .data("providers.isolation_policy", {})
        .get("project_markers", ())
    )
    return SimpleNamespace(PROJECT_MARKERS=PROJECT_MARKERS)


def project_markers() -> tuple:
    """The words that prove a seat holds this project's own guidance."""
    return _options().PROJECT_MARKERS


def ensure_neutral_cwd() -> Path:
    """Create the neutral CWD if missing (a fresh machine has no ~/tmp); a
    blind child launched from a nonexistent directory dies unhandled."""
    NEUTRAL_CWD.mkdir(parents=True, exist_ok=True)
    return NEUTRAL_CWD


# --------------------------------------------------------------------------- #
# the jail
# --------------------------------------------------------------------------- #

BWRAP = "bwrap"

# Blanked wholesale, then a whitelist is bound back. An ALLOW-list is the only
# shape that stays correct: the earlier hand-rolled invocation hid the four
# directories somebody thought of (`projects`, `.claude`, `.codex/sessions`,
# `tmp`) and was one forgotten directory from a leak -- and the ones that leak
# are the ones nobody lists, a second checkout, `~/.gemini`, an old panel run.
# `/tmp` goes too: it is a cross-seat channel and nothing blind needs it.
BLANKED = ("$HOME", "/tmp", "/var/tmp")
# The cap on each blanked tmpfs, so a runaway seat cannot fill the host's
# memory. The run dir and the private runner homes are binds, not tmpfs, so
# real output never counts against it.
TMPFS_BYTES = 2 * 1024**3

# The root is an allow-list too, and for the same reason as the home. It used
# to be `--bind / /` minus a deny-list of network filesystem types, and every
# repair after that followed from the shape: a stale CIFS share under `/`
# failed the whole bind, so the root was enumerated around it; the mount
# table could change between reading it and the jail's clone; and the bind
# was read-write, so a user-writable path outside the blanked set (a mounted
# NTFS volume, the runtime directory) was writable from a seat. Binding only
# the system trees a CLI needs removes the class: the operator's home, shares
# and removable media never exist inside, and there is no mount table to
# read. A runner that needs a tree outside the list fails loudly at launch;
# add the tree here, never bind `/`.
#
# A required tree that is missing fails the launch by name; an optional one
# is bound when present. `/var/lib` rather than `/var`: on Silverblue
# `/var/mnt` and `/var/home` are where media and homes live.
REQUIRED_TREES = ("/usr", "/etc")
OPTIONAL_TREES = ("/opt", "/var/lib", "/sys", "/nix", "/snap")
# Merged-usr aliases: recreated as symlinks where the host has symlinks,
# bound read-only where it still has real directories.
USR_ALIASES = ("/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32")
# A private pid namespace also means a timeout kill of bwrap takes every
# descendant with it, which `--die-with-parent` alone does not guarantee
# for a runner's MCP helpers and git children.
NAMESPACES = ("--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try")

# `/run/user/<uid>` is never bound. The session bus there is a door out of
# any sandbox: `systemd --user` starts a process on request, outside the
# jail, so `systemd-run --user --pipe ls ~` from a seat with a shell listed
# the operator's real home. A runner that must reach a bus name declares it
# in `Runner.bus_names` and gets a socket from `xdg-dbus-proxy` filtered to
# exactly those names, and the receipt reports the door.
BUS_PROXY = "xdg-dbus-proxy"
BUS_READY_SECONDS = 15
BUS_RUNTIME_BYTES = 64 * 1024**2


def resolver_paths(resolv=Path("/etc/resolv.conf"), root=Path("/")) -> list[Path]:
    """Where the resolver file points when that is outside ``/etc``.

    Fedora and Ubuntu link ``/etc/resolv.conf`` to systemd-resolved's stub
    under ``/run``, which the allow-list does not bind. Without the target
    nothing in the jail resolves a name, and the seat cannot reach its
    provider. Only a target under ``/run`` is followed, where every resolver
    manager keeps its file; a link anywhere else is refused by name rather
    than bound, so a stray symlink cannot pull a private path into the jail.
    A dangling link is skipped: the host has no resolver either.
    """
    resolv = Path(resolv)
    if not resolv.is_symlink():
        return []
    target = resolv.resolve()
    if root / "etc" in target.parents or not target.exists():
        return []
    if root / "run" not in target.parents:
        raise BlindnessError(
            f"{resolv} points at {target}, outside /etc and /run; the jail "
            "binds only a resolver under /run. Point it at a file under /run "
            "or make it a regular file"
        )
    return [target]


def system_root(root=Path("/")) -> list[tuple[str, str, str]]:
    """``(op, source, target)`` triples that build the read-only root.

    Everything is bound at its own path, so ``/usr/bin/git`` is
    ``/usr/bin/git`` inside as well. ``root`` exists for tests that build a
    fake host tree.
    """
    out = []
    for tree in REQUIRED_TREES:
        path = root / tree.lstrip("/")
        if not path.is_dir():
            raise BlindnessError(
                f"{path} is missing; the jail binds it read-only and cannot "
                "run without it"
            )
        out.append(("--ro-bind", str(path), str(path)))
    for tree in OPTIONAL_TREES:
        path = root / tree.lstrip("/")
        if path.is_dir() and not path.is_symlink():
            out.append(("--ro-bind", str(path), str(path)))
    for alias in USR_ALIASES:
        path = root / alias.lstrip("/")
        if path.is_symlink():
            out.append(("--symlink", os.readlink(path), str(path)))
        elif path.is_dir():
            out.append(("--ro-bind", str(path), str(path)))
    for target in resolver_paths(root / "etc/resolv.conf", root):
        out.append(("--ro-bind", str(target), str(target)))
    return out


def bound_trees(triples) -> list[Path]:
    """The host paths a set of root triples makes visible."""
    return [Path(source) for op, source, _ in triples if op != "--symlink"]


def visible(path, trees) -> bool:
    """Whether ``path`` lies inside one of the bound host trees."""
    path = Path(path)
    return any(path == tree or tree in path.parents for tree in trees)


def root_argv(triples=None) -> list[str]:
    """``bwrap``'s opening: namespaces, the read-only root, ``/dev``, ``/proc``.

    ``--dev`` is bwrap's minimal device tree, not the host's: a seat needs
    ``null``, ``urandom`` and a tty, never the operator's disks.
    """
    argv = [BWRAP, "--die-with-parent", *NAMESPACES]
    for triple in system_root() if triples is None else triples:
        argv += list(triple)
    return argv + ["--dev", "/dev", "--proc", "/proc"]


def offline_argv(home=None) -> list[str]:
    """A wrapper that runs a runner with no network, for the free probes.

    The same root the seats get, plus the operator's own home read-write,
    because a probe answers whether a flag still exists in this installation,
    not whether the operator's state is hidden: the runners live under the
    home and would not start without it. Its one enforcement is
    ``--unshare-net``, which is what makes a probe free by construction.
    """
    home = str(home or Path.home())
    return root_argv() + ["--bind", home, home, "--tmpfs", "/tmp", "--unshare-net"]


def root_receipt(triples) -> dict:
    """What the jail's root is made of, for a confinement receipt."""
    return {
        "root_read_only": [str(t) for t in bound_trees(triples)],
        "root_symlinks": [
            f"{target} -> {link}" for op, link, target in triples if op == "--symlink"
        ],
        "namespaces": [
            flag.removeprefix("--unshare-").removesuffix("-try") for flag in NAMESPACES
        ],
        "dev": "minimal",
        "tmpfs_bytes": TMPFS_BYTES,
    }


def bus_proxy_missing() -> str | None:
    """Why a filtered session bus cannot be built here, or None."""
    if shutil.which(BUS_PROXY) is None:
        return (
            f"{BUS_PROXY} not found on PATH (Fedora: dnf install xdg-dbus-proxy; "
            "Debian: apt install xdg-dbus-proxy); the jail never binds the raw "
            "session bus"
        )
    return None


def host_bus_address(environ=None) -> str:
    """The operator's session bus, as the proxy will dial it."""
    environ = os.environ if environ is None else environ
    address = environ.get("DBUS_SESSION_BUS_ADDRESS")
    if address:
        return address
    runtime = environ.get("XDG_RUNTIME_DIR")
    if runtime and (Path(runtime) / "bus").exists():
        return f"unix:path={runtime}/bus"
    raise BlindnessError(
        "no session bus: DBUS_SESSION_BUS_ADDRESS is unset and "
        "$XDG_RUNTIME_DIR/bus does not exist; run from a desktop session"
    )


def bus_socket_dir(environ=None) -> Path:
    """A fresh private directory for the proxy's socket.

    Under the runtime directory when there is one, because socket paths are
    limited to 107 bytes and that is the shortest private place there is;
    under ``~/tmp`` otherwise, never ``/tmp``.
    """
    import tempfile

    environ = os.environ if environ is None else environ
    runtime = environ.get("XDG_RUNTIME_DIR")
    base = Path(runtime) / "quirework" if runtime else Path.home() / "tmp"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="bus.", dir=base))


class Bus:
    """A session bus filtered to named services, for one jail.

    The proxy writes a byte to its ``--fd`` once it listens and exits when
    every reader of that pipe is gone. bwrap holds the read end through
    ``--sync-fd``, so the proxy lives exactly as long as the jail. The
    launcher passes that descriptor to bwrap and then drops its own copy
    (``release``); ``close`` reaps the proxy and its socket directory, and
    is safe to call whether or not a jail ever started.
    """

    # Every flag this door raises starts with this, so a plan can name the
    # flag it accepts without matching the service list that follows.
    FLAG_PREFIX = "Session bus proxied into the jail for "

    def __init__(self, names):
        reason = bus_proxy_missing()
        if reason:
            raise BlindnessError(reason)
        self.names = tuple(names)
        address = host_bus_address()
        self.directory = bus_socket_dir()
        self.socket = self.directory / "bus"
        if len(str(self.socket).encode()) > 107:
            shutil.rmtree(self.directory, ignore_errors=True)
            raise BlindnessError(
                f"socket path {self.socket} exceeds 107 bytes; set "
                "XDG_RUNTIME_DIR to a shorter directory"
            )
        import select

        reader, writer = os.pipe()
        argv = [BUS_PROXY, f"--fd={writer}", address, str(self.socket), "--filter"]
        argv += [f"--talk={name}" for name in self.names]
        try:
            self.process = subprocess.Popen(
                argv,
                pass_fds=(writer,),
                stdin=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError:
            os.close(reader)
            os.close(writer)
            shutil.rmtree(self.directory, ignore_errors=True)
            raise
        os.close(writer)
        ready, _, _ = select.select([reader], [], [], BUS_READY_SECONDS)
        if not ready or os.read(reader, 1) == b"":
            os.close(reader)
            self.process.kill()
            error = (self.process.communicate()[1] or b"").decode(errors="replace")
            shutil.rmtree(self.directory, ignore_errors=True)
            raise BlindnessError(
                f"{BUS_PROXY} did not come up on {self.socket}"
                + (f": {error.strip()}" if error.strip() else "")
            )
        self.fd: int | None = reader

    @property
    def pass_fds(self) -> tuple[int, ...]:
        return () if self.fd is None else (self.fd,)

    def argv(self) -> list[str]:
        """The jail's side: a private runtime dir holding only the socket."""
        runtime = Path(f"/run/user/{os.getuid()}")
        if self.fd is None:
            raise BlindnessError("the session bus was already released")
        return [
            "--size",
            str(BUS_RUNTIME_BYTES),
            "--tmpfs",
            str(runtime),
            "--ro-bind",
            str(self.socket),
            str(runtime / "bus"),
            "--sync-fd",
            str(self.fd),
            "--setenv",
            "XDG_RUNTIME_DIR",
            str(runtime),
            "--setenv",
            "DBUS_SESSION_BUS_ADDRESS",
            f"unix:path={runtime / 'bus'}",
        ]

    def receipt(self) -> dict:
        return {"names": list(self.names), "proxy": BUS_PROXY, "filtered": True}

    @staticmethod
    def flag(receipt) -> str:
        """The red flag a receipt carries when this door was opened.

        A flag and not a failure: the runner cannot start without it. But the
        secrets service answers for every secret in the operator's login
        keyring, so a receipt that did not say so would claim a confinement
        the jail did not have.
        """
        return (
            Bus.FLAG_PREFIX
            + ", ".join(receipt["names"])
            + ": the operator's login keyring is readable by the seat."
        )

    def release(self) -> None:
        """Drop this process's copy of the pipe once the jail holds it."""
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def close(self) -> None:
        self.release()
        try:
            self.process.wait(timeout=BUS_READY_SECONDS)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stderr.close()
        shutil.rmtree(self.directory, ignore_errors=True)


def bus_names(runners) -> tuple[str, ...]:
    """The bus names these runners need, in a stable order."""
    return tuple(sorted({n for r in runners for n in RUNNERS[r].bus_names}))


@contextmanager
def session_bus(runners):
    """The filtered bus a jail for ``runners`` needs, or None if it needs none."""
    names = bus_names(runners)
    if not names:
        yield None
        return
    bus = Bus(names)
    try:
        yield bus
    finally:
        bus.close()


def popen(argv, bus=None, **kwargs) -> subprocess.Popen:
    """``subprocess.Popen`` for a jail, handing it the bus's pipe if it has one.

    The pipe is released here, right after the jail holds it, so this
    process's copy never keeps a proxy alive past the seat it served.
    """
    try:
        return subprocess.Popen(argv, pass_fds=bus.pass_fds if bus else (), **kwargs)
    finally:
        if bus is not None:
            bus.release()


def _project_root() -> Path | None:
    """The selected project's root, or None when no project is selected.

    The jail hides it when it lies outside the blanked paths; a caller that
    wants it visible binds it back read-only afterwards, as the review driver
    does. Tests and probes run without a project and hide nothing extra.
    """
    from quirework.project import ConfigurationError

    try:
        return Path(current_project().root).resolve()
    except ConfigurationError:
        return None


@dataclass(frozen=True)
class Probe:
    """A FREE check that one flag a blind run depends on is still live.

    Costs nothing: every one of these is rejected during argument or config
    parsing, before a token is spent.

    The reason this exists is codex's asymmetry, which generalizes: an unknown
    ``-c`` **key** is accepted in silence while an invalid **value** errors
    loudly, and ``--strict-config`` does not close the gap. So a knob that is
    renamed upstream does not break -- it quietly stops doing anything, and a
    blind seat gets its web tool or its personality back with every gate still
    green. Feeding a deliberately invalid value turns that silent failure into
    a loud one: if the error stops arriving, the key is dead.

    ``needles`` are matched against the combined output, and they pin the
    *values* too -- codex's error enumerates the valid ones, so requiring
    "disabled" proves both that ``web_search`` exists and that the setting we
    pass it is still spelled that way.
    """

    label: str
    args: tuple[str, ...]
    expect: str = "error"  # "error" | "ok"
    needles: tuple[str, ...] = ()
    why: str = ""
    # Run with the network cut, which makes "this probe is free" a property of
    # the sandbox rather than a claim in a docstring: a probe that can reach no
    # API cannot buy a token, so the day a key goes dead the probe FAILS
    # instead of quietly running the prompt it was carrying.
    offline: bool = True
    # What no needle can say, such as which value one row of a table holds:
    # given the output, the problem it shows, or None.
    verdict: Callable[[str], str | None] | None = None


@dataclass(frozen=True)
class Runner:
    """What one runner needs bound back into a blanked home.

    ``creds`` is runner-specific knowledge and cannot be derived; the launcher
    path is derived from ``PATH`` so a version bump does not rot the list. The
    guard against either going stale is ``blind check``, which starts the real
    binary inside the jail rather than trusting this table.
    """

    name: str
    home: str  # the runner's state dir, given a private copy
    creds: tuple[str, ...] = ()  # bound read-only ON TOP of that private dir
    # Where the login is kept. A "file" login is one of `creds`, so a missing
    # one means the runner cannot sign in and the seat is refused before it
    # starts. A "keyring" login is in the desktop keyring, reached over the
    # session bus: `creds` are then files an older release kept, bound back
    # when present and never required, and the canary proves the login.
    login: str = "file"
    probe: tuple[str, ...] = ("--version",)
    probes: tuple[Probe, ...] = ()  # free flag-liveness contract
    notes: str = ""
    # Session-bus names the runner must reach to run at all. The jail never
    # binds the raw bus; it proxies exactly these in (see `Bus`).
    bus_names: tuple[str, ...] = ()
    # Where a session lives in the private home, as a glob with `{id}` for the
    # session id. A resume continues that file, so its absence is the whole
    # question `require_session` answers.
    session: str = ""
    # Directories in the home the runner fills with what it unpacks for one
    # launch and needs from no other. Each launch gets them as a fresh tmpfs,
    # so what is unpacked there never reaches the disk.
    scratch: tuple[str, ...] = ()


def _reads_false(feature: str) -> Callable[[str], str | None]:
    """A verdict: ``feature`` is listed, and false, in ``codex features list``."""

    def verdict(out: str) -> str | None:
        for line in out.splitlines():
            cols = line.split()
            if cols and cols[0] == feature:
                if cols[-1] == "false":
                    return None
                return f"{feature} reads {cols[-1]} under the override"
        return f"{feature} is not in `codex features list`, so the switch names nothing"

    return verdict


def _codex_seat_probes() -> tuple[Probe, ...]:
    """The free contract for ``CODEX_SEAT``, derived from the set itself.

    Codex accepts an unknown feature name in silence. A switch renamed
    upstream would hand every seat back what it turns off, with every
    launch still green and the canary blind to anything but MCP servers.
    Codex's own feature list under the override is the free witness, so
    each ``features.NAME=false`` in the set must leave NAME listed and
    false. A switch added to the set is probed here with no edit.

    The thread limit is a setting rather than a feature, and follows the
    invalid-value rule: Codex refuses a limit of zero, and the day that
    refusal stops arriving the key is dead. No free check can show that a
    spawn still fails, only that the limit is still read.
    """
    probes = []
    for arg in CODEX_SEAT[1::2]:
        key, _, value = arg.partition("=")
        name = key.removeprefix("features.")
        if value == "false" and name != key and "." not in name:
            probes.append(
                Probe(
                    key,
                    ("-c", arg, "features", "list"),
                    expect="ok",
                    verdict=_reads_false(name),
                    why="a seat switch every codex launch carries; if it "
                    "stops reading false, the seat gets back what it turns off",
                )
            )
    limit = CODEX_NO_SUBAGENTS[1].partition("=")[0]
    probes.append(
        Probe(
            limit,
            ("-c", f"{limit}=0", "features", "list"),
            needles=(limit, "at least 1"),
            why="the one-thread limit is all that stops a seat's spawn_agent "
            "from handing work to a model its receipt never names",
        )
    )
    return tuple(probes)


# Each runner gets a PRIVATE, PER-RUN home bound over its real one. Three jobs
# at once: the operator's real state (the global brief, `~/.claude/projects`,
# codex's session and memory stores) never appears; the seat's own state
# survives from one turn to the next, which a bare tmpfs would not, since every
# `blind run` is a separate process with a fresh tmpfs and a multi-turn seat
# would find no session to resume; and two seats in the same run never share a
# store. Credentials are bound read-only on top rather than copied, so no
# secret is duplicated onto disk.
RUNNERS = {
    "claude": Runner(
        "claude",
        home="~/.claude",
        creds=("~/.claude/.credentials.json",),
        probes=(
            Probe(
                "--setting-sources",
                ("-p", "x", "--setting-sources", "bogus"),
                needles=("Invalid setting source", "user, project, local"),
                why="the only thing keeping the global CLAUDE.md out",
            ),
            Probe(
                "--settings",
                ("-p", "x", "--settings", "NOTJSONNOTAPATH"),
                needles=("Settings file not found",),
                why="pins the output style; an Explanatory style swaps a "
                "seat's register",
            ),
            Probe(
                "--tools",
                ("--help",),
                expect="ok",
                needles=("--tools",),
                why="the allow-nothing shape; NOTE claude does not validate "
                "this flag's VALUE, so only its existence is provable "
                "for free",
            ),
        ),
        notes="global CLAUDE.md and ~/.claude/projects replaced by the private home",
        session="projects/**/{id}.jsonl",
    ),
    "agy": Runner(
        "agy",
        home="~/.gemini",
        creds=(
            "~/.gemini/oauth_creds.json",
            "~/.gemini/google_accounts.json",
            "~/.gemini/settings.json",
            "~/.gemini/installation_id",
        ),
        probes=(
            # The one probe that CANNOT be run offline: agy refreshes its
            # OAuth token before it validates anything, so with the network
            # cut it stops at a login URL and never reaches the flag. It is
            # still free, and the needle is what proves it: "Available models"
            # can only be printed by the model-selection check, which happens
            # before any inference. A token spent would mean an answer, not a
            # model list.
            Probe(
                "--model",
                ("-p", "x", "--model", "bogus-model-xyz"),
                needles=("not recognized", "Available models"),
                offline=False,
                why="agy renames its models often (the list moved 3.6 -> 3.7 "
                "mid-week), and a seat's model is its only attribution "
                "receipt",
            ),
        ),
        notes="~/.gemini/GEMINI.md (the global brief) replaced with the home",
        # agy 1.3 signs in through the keyring and writes none of `creds` in a
        # home it has never seen; they are what the Gemini CLI it grew out of
        # left behind. A machine that has them binds them, and one that has
        # not still runs: requiring them refused a seat whose canary had just
        # passed.
        login="keyring",
        # Its token lives in the login keyring, reached over the session bus.
        # The proxy lets this one name through, which still exposes every
        # secret the keyring holds, so the receipt flags it.
        bus_names=("org.freedesktop.secrets",),
        session="antigravity-cli/brain/{id}/.system_generated/logs/transcript_full.jsonl",
        # agy unpacks its 17 MB browser recorder here before every session in
        # a home that lacks it, and only after a model has been chosen. 93
        # seat homes on one machine held 1.5 GB of copies. A clone of the
        # operator's own copy was tried first, and agy replaced it: an older
        # release had unpacked that one, as it will have for anyone who runs
        # agy mostly through seats. The directory holds nothing a resumed
        # session needs, and agy writes it afresh when it is empty.
        scratch=("antigravity-cli/bin",),
    ),
    "codex": Runner(
        "codex",
        home="~/.codex",
        creds=("~/.codex/auth.json",),
        probes=(
            Probe(
                "web_search",
                ("exec", "--ignore-user-config", "-c", 'web_search="bogus"', "x"),
                needles=("unknown variant", "disabled"),
                why="codex ships a live web tool ON by default; if this key "
                "goes dead the seat silently regains it",
            ),
            Probe(
                "personality",
                ("exec", "--ignore-user-config", "-c", 'personality="bogus"', "x"),
                needles=("unknown variant", "none"),
                why="the register pin, codex's analogue of --settings",
            ),
            Probe(
                "--ignore-user-config",
                ("exec", "--ignore-user-config", "--help"),
                expect="ok",
                why="keeps the operator's config and the project AGENTS.md "
                "out of the seat",
            ),
            *_codex_seat_probes(),
        ),
        notes="sessions, logs and the memory store replaced by the private home",
        session="sessions/**/*{id}.jsonl",
    ),
}


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p)).resolve()


def backend_error() -> str | None:
    """``None`` if the machine can confine a seat, else why it cannot."""
    if sys.platform != "linux":
        return (
            f"confinement is Linux-only for now (this is {sys.platform}); "
            "docs/workflows.md#execution-and-recovery, 'The run dir is not a wall'"
        )
    if shutil.which(BWRAP) is None:
        return "bwrap not found on PATH (dnf install bubblewrap)"
    return None


def _launcher_paths(link: Path, hops: int = 10) -> list[Path]:
    """Every directory needed to follow a launcher's symlink chain in the jail.

    One hop at a time (not ``resolve()``), collecting each element's parent, so
    an intermediate link like codex's ``current`` still has somewhere to point.
    """
    out, seen, cur = [], set(), link
    for _ in range(hops):
        if cur in seen:
            break
        seen.add(cur)
        out.append(cur.parent)
        if not cur.is_symlink():
            break
        cur = Path(os.path.normpath(os.path.join(cur.parent, os.readlink(cur))))
    out.append(cur if cur.is_dir() else cur.parent)
    return out


def runner_binds(names) -> tuple[list[Path], list[str]]:
    """``(paths, missing)`` to bind back read-only for these runners.

    The launcher is resolved through ``PATH`` and its symlink chain walked one
    hop at a time, because the chain has *intermediate* links: codex goes
    `~/.local/bin/codex` -> `~/.codex/packages/standalone/current/bin/codex`,
    and `current` is itself a symlink to the versioned release. Binding only
    the fully-resolved target leaves `current` missing inside the jail and the
    runner unfindable -- which `blind check` caught on its first run, and is
    exactly the staleness this table would otherwise hide.
    """
    paths: list[Path] = []
    missing: list[str] = []
    for name in names:
        launcher = shutil.which(name)
        if launcher is None:
            missing.append(f"{name}: not on PATH")
            continue
        paths += _launcher_paths(Path(launcher))
        # Credentials are NOT returned here: they are bound by `jail_argv`
        # after the runner's private home, because they live inside it and a
        # bind in the wrong order is silently replaced by the one over it.
        for c in RUNNERS[name].creds if RUNNERS[name].login == "file" else ():
            if not _expand(c).exists():
                missing.append(f"{name}: {c} not found")
        if RUNNERS[name].bus_names and (reason := bus_proxy_missing()):
            missing.append(f"{name}: {reason}")
    return paths, missing


# The one suffix that names a run's private runner homes. Every workflow that
# confines a seat -- the run engine, the workshop, a room, a contextual
# review, a chapter comparison -- reaches its homes through `state_root`, so
# this module owns the name and is therefore the only place that can find
# them all again afterwards.
STATE_SUFFIX = ".blind-state"

# https://bford.info/cachedir/: borg, restic and GNU tar skip a directory
# holding this file when asked to exclude caches, wherever it sits.
CACHEDIR_SIGNATURE = "Signature: 8a477f597d28d172789f06886806bc55"
CACHEDIR_TAG = (
    f"{CACHEDIR_SIGNATURE}\n"
    "# This file is a cache directory tag created by quirework.\n"
    "# Private runner homes of confined seats; their runs' records are elsewhere.\n"
    "# For information about cache directory tags see https://bford.info/cachedir/\n"
)


def homes_root() -> Path:
    """`$XDG_CACHE_HOME/quirework/homes`, where every private runner home lives.

    A home is a harness's own installation and scratch state: tens of
    megabytes, holding nothing a record needs once the run is over. Kept
    beside the records under the state directory, homes were nearly all of
    its bytes, and backups keep a state directory by default. The cache
    directory is where data that can be lost lives, and the tag makes
    backup tools skip it wherever it is. Not `/tmp` or the runtime
    directory: both are emptied at reboot or logout, and a run held on a
    provider limit waits hours to resume its sessions.
    """
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base).expanduser() / "quirework" / "homes"


def state_root(run_dir: Path) -> Path:
    """The path a run's private runner homes are reached by, beside the run dir.

    Never inside it, for the same reason the receipt is outside -- a seat
    that can see its own jail furniture can tell it is in one -- and because
    a runner's state dir is bound *over* its real path, so it must not also
    be visible as an ordinary directory in the seat's workspace.

    What sits there is a link into `homes_root` (`make_state_root`). A run
    whose homes were made before they moved holds a directory instead, and a
    pruned run a tombstone naming when (`tombstone`).
    """
    return run_dir.parent / f"{run_dir.name}{STATE_SUFFIX}"


def _tagged(base: Path) -> bool:
    try:
        return (base / "CACHEDIR.TAG").read_text().startswith(CACHEDIR_SIGNATURE)
    except OSError:
        return False


def home_target(link: Path) -> Path | None:
    """The cache directory a home link leads to, if this module made it.

    Only a directory directly inside a tagged `homes` directory qualifies,
    under the cache in use now or the one in use when the home was made.
    A link leading anywhere else is never followed into a removal.
    """
    target = Path(os.readlink(link))
    if not target.is_absolute():
        target = link.parent / target
    if target.is_symlink() or not target.is_dir():
        return None
    if target.parent.name != "homes" or not _tagged(target.parent):
        return None
    return target


def tombstone(entry: Path) -> dict | None:
    """What `prune` left where a run's homes were, or None if it left nothing."""
    if entry.is_symlink() or not entry.is_file():
        return None
    try:
        return json.loads(entry.read_text())
    except (OSError, ValueError):
        return {}


def make_state_root(run_dir: Path) -> Path:
    """A run's private homes, made in the cache and linked beside the run dir.

    Returns the link. A run that has its homes keeps them. A run whose homes
    were made before they moved has them moved into the cache the first time
    it launches again, so a run already under way leaves the backed-up state
    without losing its sessions; a cache on another filesystem cannot take
    a rename, and the directory then stays where it is. A tombstone, or a
    link the cache cleaner left dangling, gives way to a fresh home: a launch
    that needed the old session was refused before it got here.
    """
    link = state_root(Path(run_dir))
    if link.is_symlink() and link.is_dir():
        return link
    base = homes_root()
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not _tagged(base):
        (base / "CACHEDIR.TAG").write_text(CACHEDIR_TAG)
    import tempfile

    stem = f"{Path(run_dir).parent.name}.{Path(run_dir).name}".lstrip(".")[:60]
    target = Path(tempfile.mkdtemp(prefix=f"{stem}-", dir=base))
    if link.is_dir():
        try:
            # Onto the empty directory just made, which rename replaces.
            os.rename(link, target)
        except OSError as exc:
            os.rmdir(target)
            if exc.errno == errno.EXDEV:
                return link
            raise
    elif link.is_symlink() or link.exists():
        link.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link)
    except FileExistsError:
        # Another launch of the same run made its homes first.
        shutil.rmtree(target)
        if not (link.is_symlink() and link.is_dir()):
            raise
    return link


# What keeps a run's homes from `prune`, at the root of the homes, beside the
# runners' directories. Only those are bound into a jail, so no seat sees it.
HOLD = "held.json"


def hold(run_dir: Path, why: str) -> None:
    """Keep a run's homes from `prune` until `release`, saying why.

    A home holds a session a later command resumes: a room's next round, a
    review continued next week, a fork's parent its children are checked
    against. Only the procedure that runs the seats knows when that is
    over, so it holds the homes when it launches a seat and releases them
    where it knows the run is finished. One it never releases is kept
    until a prune is forced or the cache rule collects it, which is the
    safe way to be wrong.
    """
    entry = state_root(Path(run_dir))
    if entry.is_dir():
        (entry / HOLD).write_text(
            json.dumps(
                {
                    "why": why,
                    "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )
            + "\n"
        )


def release(run_dir: Path) -> None:
    """Let `prune` take a run's homes once it is finished with them."""
    entry = state_root(Path(run_dir))
    if entry.is_dir():
        (entry / HOLD).unlink(missing_ok=True)


def held(home: Path) -> str | None:
    """Why a run still needs these homes, or None once it has let them go."""
    try:
        return json.loads((home / HOLD).read_text())["why"]
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        return "held, for a reason that can no longer be read"


def discard_state_root(run_dir: Path) -> None:
    """Remove a run's private homes, and whatever leads to them."""
    link = state_root(Path(run_dir))
    if link.is_symlink():
        target = home_target(link) if link.exists() else None
        if target is not None:
            shutil.rmtree(target)
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link)
    elif link.exists():
        link.unlink()


class SessionGone(RuntimeError):
    """A resume or a fork whose session the private home no longer holds."""


def gone(entry: Path, home: Path) -> str:
    """Why a run's private home is missing, as far as anything recorded it."""
    buried = tombstone(entry)
    if buried is not None:
        when = buried.get("pruned_at", "an unrecorded time")
        held = buried.get("held")
        return f"its private homes were pruned at {when}" + (
            f", by force, while held: {held}" if held else ""
        )
    if entry.is_symlink():
        return (
            f"its private home {home} is gone: the cache it lived in, "
            f"{os.readlink(entry)}, was cleared"
        )
    return f"its private home {home} is gone"


def session_paths(home: Path, runner: str, session_id: str) -> list[Path]:
    """The native file(s) holding one session in a runner's private home.

    The id is matched literally, so an id that happens to contain a glob
    character can never match another session. A resumed codex session
    opens a second rollout under the same id, so there may be several.
    """
    pattern = RUNNERS[runner].session.format(id=glob.escape(session_id))
    return sorted(Path(home).glob(pattern))


def require_session(run_dir: Path, runner: str, session_id: str) -> Path:
    """The file holding ``session_id`` in this run's private home.

    A resume or a fork continues a conversation the harness keeps in the
    seat's private home, so it can run only while that home holds the
    session. `prune` removes homes it judges finished, and a home removed
    by hand goes the same way. Launched anyway, the harness fails for a
    reason it does not name, often after the verb has already filed the
    turn it meant to continue. Every verb that resumes or forks calls this
    first and changes nothing on disk when it raises.
    """
    entry = state_root(Path(run_dir))
    home = entry / runner
    if not home.is_dir():
        why = gone(entry, home)
    else:
        found = session_paths(home, runner, session_id)
        if found:
            return found[0]
        why = f"its private home {home} no longer holds it"
    raise SessionGone(
        f"cannot continue {runner} session {session_id}: {why}, and a new "
        "session cannot carry on its conversation. Nothing was changed, and "
        "the run's records are intact"
    )


def private_homes(root: Path) -> list[Path]:
    """Where each run under ``root`` reaches its private homes, newest last.

    A link into the cache, or the directory of a run whose homes were made
    before they moved there. A tombstone, and a link whose home the cache
    cleaner already took, lead to nothing left to remove.
    """
    found = [p for p in Path(root).rglob(f"*{STATE_SUFFIX}") if p.is_dir()]
    return sorted(found, key=lambda p: (_touched(p), str(p)))


def _home_of(entry: Path) -> Path | None:
    """The directory pruning ``entry`` removes, or None where it may not."""
    if entry.is_symlink():
        return home_target(entry)
    if entry.is_dir() and entry.name.endswith(STATE_SUFFIX):
        return entry
    return None


def _touched(home: Path) -> float:
    """When anything inside this home last changed."""
    stamps = [home.stat().st_mtime]
    stamps += [p.stat().st_mtime for p in home.rglob("*") if not p.is_symlink()]
    return max(stamps)


def _bytes(home: Path) -> int:
    """Disk actually occupied, counted the way ``du`` counts it.

    Blocks rather than apparent size, and each inode once: a runner install is
    heavily hardlinked, and summing every path's ``st_size`` reported 1.1 GB
    for a seat holding 52 MB. A prune that overstates what it frees by twenty
    times is worse than one that does not report at all.
    """
    seen, total = set(), 0
    for item in home.rglob("*"):
        if item.is_symlink() or not item.is_file():
            continue
        try:
            info = item.stat()
        except OSError:
            continue
        key = (info.st_dev, info.st_ino)
        if key in seen:
            continue
        seen.add(key)
        total += info.st_blocks * 512
    return total


def liveness_known() -> bool:
    """Whether this machine can say which seats still have a live process.

    ``occupied`` reads ``/proc``. Without it there is no answer, and no
    answer must never be read as "nobody is here": a running seat's home
    removed out from under it breaks the run it is in the middle of.
    """
    return Path("/proc").is_dir()


def occupied(home: Path) -> int | None:
    """The pid of a process still working in this home's seat, or None.

    A confined harness keeps its working directory inside the seat workspace
    while its runner home is bound over the real one, so either path turning up
    as a live process's cwd means that seat has not finished. ``home`` is
    where the run reaches its homes, and the cache directory it leads to
    counts too. Best effort by construction: ``/proc`` refuses processes this
    user does not own, and a seat merely paused between turns looks idle,
    which is what the age window in ``prune`` is for.
    """
    seat = home.parent / home.name[: -len(STATE_SUFFIX)]
    places = {home, home.resolve(), seat}
    procs = Path("/proc")
    if not procs.is_dir():
        return None
    for entry in procs.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cwd = (entry / "cwd").readlink()
        except OSError:
            continue
        if cwd in places or any(place in cwd.parents for place in places):
            return int(entry.name)
    return None


def bury(entry: Path, record: dict) -> None:
    """Leave a tombstone where a run reached its homes, saying they went.

    A later resume then reports that they were pruned, and when, rather
    than that they vanished.
    """
    if entry.is_symlink():
        entry.unlink()
    entry.write_text(json.dumps(record, indent=2) + "\n")


def prune(roots, *, grace: float = 3600, force: bool = False, dry_run: bool = False):
    """Remove finished seats' private runner homes; keep every record.

    A home is the harness's own installation and scratch state, bound over the
    real one so a seat cannot reach the operator's credentials or memory. It is
    large -- one measured codex seat kept 53 MB against 1.6 MB of records for
    the whole room. The cache cleaner collects a home untouched for 30 days
    where its rule is installed; this removes finished ones sooner, and
    reaches homes made before they moved into the cache.

    A home its run still holds (`hold`) is kept unless ``force`` says
    otherwise, and the tombstone then records what the run was holding it
    for. Age is the only test for a home nothing holds: one made before
    holds existed, or by a command that runs one seat and is done.

    Nothing here reads a receipt, an answer or a transcript, so pruning cannot
    remove evidence: the native transcripts inside a home have already been
    read into the receipts beside it, and `launch.json` records the home's path
    whether or not the directory still exists. Each run pruned keeps a
    tombstone where it reached its homes.
    """
    removed, kept, freed = [], [], 0
    known = liveness_known()
    for root in roots:
        for entry in private_homes(Path(root).expanduser().resolve()):
            home = _home_of(entry)
            if home is None:
                kept.append(
                    (entry, 0, "leads outside the homes directory; not followed")
                )
                continue
            size, pid = _bytes(home), occupied(entry) if known else None
            age = time.time() - _touched(home)
            needed = held(home)
            # The age window and a hold are advice, and `force` overrides
            # both. A live seat is not: removing the home out from under a
            # running harness would break the run it is in the middle of, and
            # no flag here should be able to do that. Nor may "cannot tell"
            # pass for "not in use".
            if not known:
                kept.append(
                    (
                        entry,
                        size,
                        f"liveness unknown without /proc on {sys.platform}; "
                        "check for a live seat and remove the home by hand",
                    )
                )
            elif pid is not None:
                kept.append((entry, size, f"in use by pid {pid}"))
            elif needed and not force:
                kept.append((entry, size, f"held: {needed}"))
            elif age < grace and not force:
                kept.append((entry, size, f"touched {int(age)}s ago"))
            elif dry_run:
                removed.append((entry, size, "would remove"))
                freed += size
            else:
                shutil.rmtree(home)
                bury(
                    entry,
                    {
                        "pruned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "freed_bytes": size,
                        # What a forced prune overrode, for the resume it fails.
                        **({"held": needed} if needed else {}),
                    },
                )
                removed.append((entry, size, "removed"))
                freed += size
    return {"removed": removed, "kept": kept, "freed": freed}


class _Probe:
    """The canary argument for a jailed process that is not a seat."""

    def __repr__(self):
        return "blind.PROBE"


PROBE = _Probe()


def _require_canary(runners, canary) -> None:
    """Refuse a runner's jail unless a passing canary covers that runner."""
    if not runners or canary is PROBE:
        return
    entries = canary if isinstance(canary, (list, tuple)) else [canary]
    covered = {e.get("runner") for e in entries if isinstance(e, dict)}
    missing = [r for r in runners if r not in covered]
    if missing:
        raise BlindnessError(
            f"no launch canary for {', '.join(missing)}: call canary.require "
            f"before building a seat's jail, and pass its record as canary="
        )


def jail_argv(
    run_dir: Path,
    allow=(),
    runners=(),
    env=None,
    bind_credentials: bool = True,
    expose_run_dir: bool = True,
    project: Path | None = None,
    bus: Bus | None = None,
    canary=None,
) -> list[str]:
    """The bwrap argv that confines a seat to ``run_dir``.

    Order is load-bearing, because bwrap applies operations in sequence:
    ``$HOME`` is blanked before anything is bound back into it; each runner's
    private home is bound before its credentials and its install tree, both of
    which sit *inside* that home for at least one runner (codex keeps its
    binary under ``~/.codex/packages``); and the writable run dir goes last so
    no read-only bind can shadow it. Callers supplying an access-only token
    through the subprocess environment can disable credential-file bindings;
    this keeps a shared OAuth refresh token out of the isolated process.

    ``expose_run_dir=False`` stops before the writable run-dir bind and the
    chdir, leaving ``run_dir`` as nothing but the anchor for ``state_root``.
    A caller that is not confining a seat to a frozen packet -- an editorial
    reviewer stands in the repository, read-only, and writes only its report
    -- then appends its own binds and its own ``--chdir``. It owns the tail of
    the sequence, and the ordering rules above still apply to it: read-only
    binds first, the writable one last, so nothing shadows it.

    ``bus`` is the filtered session bus from ``session_bus``, required when a
    runner declares ``bus_names`` and refused otherwise, and the caller
    launches through ``popen`` so the jail receives the proxy's pipe.

    ``canary`` is the passing record ``canary.require`` returned for each
    runner in ``runners`` (one record, or a list), and the jail is refused
    without it: this is the one function every runner's jail is built by, so
    it is where a launch that skipped its canary is stopped. ``PROBE`` stands
    in where the process is not a seat answering a question, such as a
    runner started only to show it starts, or the canary itself.
    """
    _require_canary(runners, canary)
    needed = bus_names(runners)
    if needed and (bus is None or bus.names != needed):
        raise BlindnessError(
            f"{', '.join(runners)} needs the session bus for {', '.join(needed)}; "
            "open it with blind.session_bus and pass it to the jail"
        )
    if bus is not None and not needed:
        raise BlindnessError("no runner in this jail needs a session bus")
    home = Path.home()
    triples = system_root()
    trees = bound_trees(triples)
    argv = root_argv(triples)
    blanked = [Path(str(home) if b == "$HOME" else b) for b in BLANKED]
    for target in blanked:
        argv += ["--size", str(TMPFS_BYTES), "--tmpfs", str(target)]
    # A project inside a bound tree (say `/opt/src/book`) would otherwise be
    # readable through that tree. One outside every bound tree does not exist
    # in the jail and needs nothing. A caller that stands a reviewer in the
    # repository binds it back read-only after this, and a later bind wins.
    project = _project_root() if project is None else project
    if (
        project is not None
        and visible(project, trees)
        and not visible(project, blanked)
    ):
        argv += ["--tmpfs", str(project)]
    # Bound at the directory the link leads to, so the launch record names
    # where the home really lives.
    root = make_state_root(run_dir).resolve() if runners else None
    creds: list[Path] = []
    for name in runners:
        r = RUNNERS[name]
        private = root / name
        private.mkdir(exist_ok=True)
        argv += ["--bind", str(private), str(_expand(r.home))]
        for rel in r.scratch:
            # A copy unpacked before scratch existed would only sit under the
            # tmpfs, taking room and never read again. The seat could have
            # put a link there, and removing one never follows it.
            stale = private / rel
            if stale.is_symlink() or stale.is_file():
                stale.unlink()
            elif stale.is_dir():
                shutil.rmtree(stale)
            argv += ["--size", str(TMPFS_BYTES), "--tmpfs", str(_expand(r.home) / rel)]
        if bind_credentials:
            creds += [_expand(c) for c in r.creds if _expand(c).exists()]
    binds, _ = runner_binds(runners)
    for p in binds + creds + [_expand(str(a)) for a in allow]:
        argv += ["--ro-bind", str(p), str(p)]
    if bus is not None:
        argv += bus.argv()
    if expose_run_dir:
        argv += ["--bind", str(run_dir), str(run_dir), "--chdir", str(run_dir)]
        # --chdir moves the process but not the shell variables it inherited,
        # and a seat that echoes $PWD/$OLDPWD would read back the launcher's
        # path -- naming the project directory it cannot open. The content is
        # safe either way; the point is to leave nothing to notice.
        argv += ["--setenv", "PWD", str(run_dir)]
    argv += ["--unsetenv", "OLDPWD"]
    for k, v in (env or {}).items():
        argv += ["--setenv", k, v]
    return argv


_SEMVER = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def runner_version(name: str) -> str | None:
    """``x.y.z`` from ``<runner> --version``, or None if it cannot be read."""
    try:
        r = subprocess.run(
            [name, "--version"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = _SEMVER.search(r.stdout or r.stderr or "")
    return m.group(0) if m else None


def run_probe(name: str, probe: Probe) -> tuple[bool, str]:
    """``(ok, detail)`` for one free flag-liveness probe.

    The output is matched, never the exit status: claude prints
    ``Invalid setting source: bogus`` and still exits 0, so a returncode test
    would pass a dead flag.

    ``offline`` probes run with the network unshared, which is what makes the
    free suite free *by construction*. Verified: claude and codex both reject
    these values with no network at all, so they never reach an API.
    """
    argv = [name, *probe.args]
    if probe.offline and backend_error() is None:
        argv = [*offline_argv(), *argv]
    try:
        r = subprocess.run(
            argv, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"probe did not run ({exc})"
    out = (r.stdout or "") + (r.stderr or "")
    low = out.lower()
    if probe.expect == "ok":
        # An accepted flag exits 0. Never sniff the text here: help output
        # says "error" and "unknown" in passing, which read as a rejection.
        if r.returncode != 0:
            return False, f"rejected (exit {r.returncode}): {out.strip()[:120]}"
    else:
        # An invalid VALUE is the reverse: claude prints
        # "Invalid setting source: bogus" and still exits 0, so only the text
        # can distinguish a rejection from a run.
        if not any(
            w in low
            for w in ("error", "invalid", "unknown", "not recognized", "not found")
        ):
            return False, (
                "accepted a deliberately invalid value -- the key "
                "is DEAD and the flag is now a silent no-op"
            )
    missing = [n for n in probe.needles if n.lower() not in low]
    if missing:
        return False, f"output no longer mentions {', '.join(missing)}"
    problem = probe.verdict(out) if probe.verdict else None
    if problem:
        return False, problem
    return True, ""


class BlindnessError(RuntimeError):
    """Raised when a seat cannot be confined. Never downgraded to a warning."""


_SEAT_DIR: Path | None = None


def seat_dir() -> Path:
    """A fresh, empty run dir for a tools-off seat, made once per process.

    The tooled paths (`coldread analyze`, `variant build`) pipe the artifact in
    on stdin and run with every tool off, so their seat never needs to read
    anything -- but they used to run with ``cwd=~/tmp``, which is the *shared*
    scratch every other seat writes into. An empty private dir costs nothing
    and removes the one thing a tools-off seat could still be pointed at if it
    ever gained a tool.
    """
    global _SEAT_DIR
    if _SEAT_DIR is None:
        import atexit
        import tempfile

        d = Path(tempfile.mkdtemp(dir=ensure_neutral_cwd(), prefix=".seat-"))
        atexit.register(shutil.rmtree, d, True)
        # Registered second so it runs first; the home is its own tree, in
        # the cache, and removing the seat's directory never reached it.
        atexit.register(discard_state_root, d)
        _SEAT_DIR = d
    return _SEAT_DIR


def confine(
    cmd, run_dir: Path, runners=(), allow=(), bus=None, canary=None
) -> list[str]:
    """``cmd`` wrapped in the jail, or ``BlindnessError`` if it cannot be.

    Raising is the point. A blind run that quietly proceeds unconfined
    produces evidence that looks identical to a confined run's and is not,
    which is the failure this whole layer exists to prevent; the operator opts
    out explicitly (``blind run --unconfined``) or not at all.
    """
    err = backend_error()
    if err:
        raise BlindnessError(
            f"cannot confine a blind seat: {err}. Run it by hand with "
            f"`quirework sop blind run --unconfined` if an unconfined seat is "
            f"genuinely acceptable, and say so in the write-up."
        )
    return jail_argv(
        run_dir, allow=allow, runners=runners, bus=bus, canary=canary
    ) + list(cmd)


def detect_runners(cmd) -> list[str]:
    """Runner names appearing as a whole token in the command.

    Whole-token equality, not a substring: a prompt is passed as one argument,
    so a prompt *mentioning* claude is a token like "Read the file..." and
    never the bare token "claude". Scanning every token (rather than just the
    first) is what handles `stdbuf -oL -eL agy -p ...` and `timeout 900 codex
    exec ...`, where the runner is not argv[0].
    """
    return [n for n in RUNNERS if any(Path(t).name == n for t in cmd)]


def write_receipt(
    path: Path,
    *,
    confined: bool,
    cmd,
    run_dir: Path,
    runners,
    binds,
    reason: str = "",
    bus: Bus | None = None,
    canary=None,
) -> None:
    """Stamp what confined this run, **outside the run dir**.

    Outside on purpose: a receipt the seat can read tells the seat it is a
    blind seat in an experiment, and names the operator's home layout. The
    parent of the run dir is not bound into the jail, so a file written there
    is invisible from inside. ``canary`` is the record, or records, the
    launch relied on.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "confined": confined,
                "backend": "bwrap" if confined else "none",
                "reason": reason,
                "run_dir": str(run_dir),
                "runners": list(runners),
                "blanked": [str(Path.home()) if b == "$HOME" else b for b in BLANKED],
                **(root_receipt(system_root()) if confined else {}),
                "session_bus": bus.receipt() if bus else None,
                "read_only_binds": [str(b) for b in binds],
                "command": list(cmd),
                "canary": canary,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

USAGE = """\
usage: quirework sop blind run   [--run-dir DIR] [--runner NAME]... [--allow PATH]...
                              [--receipt PATH] [--unconfined] [--dry-run] -- CMD...
       quirework sop blind check  [--runner NAME]...
       quirework sop blind canary [--runner NAME]... [--model M]
       quirework sop blind prune  [PATH]... [--grace SECONDS] [--force] [--dry-run]

Confine a blind seat to its run directory, so the project is not merely
out of sight but out of reach. See docs/workflows.md#execution-and-recovery, "The run dir is
not a wall".

  run     run CMD inside the jail; the exit status is CMD's own. A
          runner not yet proven blind at its installed version is
          canaried first (one short exchange on the cheapest model),
          and a breach refuses the run
  check   FREE self-test: every flag a blind run depends on is still
          live, each runner starts inside the jail, and the tree, the
          global briefs and a sibling seat's dir are all unreachable
  prune   remove finished seats' private runner homes, keeping every
          record. These are the harness installations bound over the
          real ones; they are large, they live in the cache, which
          collects one only after 30 idle days, and no receipt depends
          on one surviving. A home its run still holds, because a
          later command resumes it, is kept. A tombstone stays where
          each one was reached. Defaults to this project's run store.
  canary  COSTS TOKENS: one live seat per runner, asserting it really
          cannot read the tree and really has no web or MCP tool. Every
          launch already runs one when its runner release is new; this
          runs it again on demand and records it the same way.

options for `prune`:
  PATH            where to look (default: the project's run store)
  --grace SECONDS keep a home touched more recently than this
                  (default: 3600), where no run holds it
  --force         prune a home its run still holds, and regardless of
                  age; the tombstone records the hold. Still refuses a
                  live seat
  --dry-run       report what would go, and how much, without removing

options for `run`:
  --run-dir DIR   the seat's directory, bound writable (default: cwd)
  --runner NAME   bind this runner's launcher and credentials back in;
                  repeatable. Auto-detected from CMD when it names one.
  --allow PATH    bind PATH read-only as well; repeatable
  --receipt PATH  where to stamp what confined the run (default: beside
                  the run dir, NOT inside it -- a receipt the seat can
                  read tells the seat it is a blind seat)
  --unconfined    run without a jail. Stamps confined:false and warns;
                  a run that cannot be confined otherwise FAILS rather
                  than quietly degrading.
  --dry-run       print the argv that would run, and exit
"""


def _split_cmd(argv):
    """``(our_args, command)`` around the first bare ``--``.

    Empty command when there is no ``--`` -- which is the normal case, because
    the Typer/Click layer in ``cli.py`` consumes exactly one bare ``--`` before
    ``ctx.args`` reaches here. The caller falls back to ``parse_known_args``,
    so ``quirework sop blind run --run-dir X -- claude -p ...`` and the same line
    without the separator both work, and the SOPs can keep writing the ``--``
    that reads correctly to a human.
    """
    if "--" not in argv:
        return argv, []
    i = argv.index("--")
    return argv[:i], argv[i + 1 :]


def cmd_run(argv) -> int:
    import argparse

    ours, cmd = _split_cmd(argv)
    p = argparse.ArgumentParser(prog="quirework sop blind run", add_help=False)
    p.add_argument("--run-dir", default=None)
    p.add_argument("--runner", action="append", default=[])
    p.add_argument("--allow", action="append", default=[])
    p.add_argument("--receipt", default=None)
    p.add_argument("--unconfined", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    a, cmd = (p.parse_args(ours), cmd) if cmd else p.parse_known_args(argv)
    if not cmd:
        print(USAGE, file=sys.stderr)
        print("blind run: no command (put it after `--`)", file=sys.stderr)
        return 2

    run_dir = Path(a.run_dir).expanduser().resolve() if a.run_dir else Path.cwd()
    if not run_dir.is_dir():
        print(f"blind run: run dir does not exist: {run_dir}", file=sys.stderr)
        return 2
    runners = list(dict.fromkeys(a.runner + detect_runners(cmd)))
    unknown = [r for r in runners if r not in RUNNERS]
    if unknown:
        print(
            f"blind run: unknown runner(s): {', '.join(unknown)}; "
            f"known: {', '.join(RUNNERS)}",
            file=sys.stderr,
        )
        return 2
    receipt = (
        Path(a.receipt).expanduser()
        if a.receipt
        else run_dir.parent / f"{run_dir.name}.blind-receipt.json"
    )

    err = backend_error()
    if err and not a.unconfined:
        # Never degrade in silence: an unconfined seat and a confined one are
        # not the same evidence, so the operator has to say so out loud.
        print(
            f"blind run: cannot confine -- {err}\n"
            f"  Re-run with --unconfined to accept an unconfined seat; the "
            f"receipt will say so and the\n  write-up must too.",
            file=sys.stderr,
        )
        return 3

    if a.unconfined:
        print(
            "blind run: WARNING -- running UNCONFINED; the seat can reach "
            "the project tree.",
            file=sys.stderr,
        )
        if not a.dry_run:
            write_receipt(
                receipt,
                confined=False,
                cmd=cmd,
                run_dir=run_dir,
                runners=runners,
                binds=[],
                reason=err or "",
            )
        if a.dry_run:
            print(" ".join(cmd))
            return 0
        return subprocess.run(list(cmd)).returncode

    binds, missing = runner_binds(runners)
    for m in missing:
        print(f"blind run: warning: {m}", file=sys.stderr)
    # A command written by hand cannot be rebuilt with the canary's inputs,
    # so each runner in it is proven under the shared blind flag set instead.
    proven = PROBE
    if not a.dry_run:
        from quirework.providers import canary

        try:
            proven = [canary.require("blind run", r, generic_twin(r)) for r in runners]
        except canary.CanaryError as exc:
            print(f"blind run: {exc}", file=sys.stderr)
            return 3
    with session_bus(runners) as bus:
        argv_full = jail_argv(
            run_dir, allow=a.allow, runners=runners, bus=bus, canary=proven
        )
        argv_full += list(cmd)
        if a.dry_run:
            print(" ".join(argv_full))
            return 0
        write_receipt(
            receipt,
            confined=True,
            cmd=cmd,
            run_dir=run_dir,
            runners=runners,
            binds=binds,
            bus=bus,
            canary=proven,
        )
        proc = popen(argv_full, bus, cwd=run_dir)
        try:
            return proc.wait()
        except BaseException:
            # What `subprocess.run` does, so an interrupted run leaves no
            # jail behind and the bus can be reaped on the way out.
            proc.kill()
            proc.wait()
            raise


def cmd_check(argv) -> int:
    import argparse

    from quirework.project import current_project

    p = argparse.ArgumentParser(prog="quirework sop blind check", add_help=False)
    p.add_argument("--runner", action="append", default=[])
    a = p.parse_args(argv)
    names = a.runner or list(RUNNERS)

    err = backend_error()
    if err:
        print(f"✗ backend: {err}")
        return 1
    ver = subprocess.run(
        [BWRAP, "--version"], capture_output=True, text=True
    ).stdout.strip()
    print(f"✓ backend: {ver}")
    try:
        trees = bound_trees(system_root())
    except BlindnessError as exc:
        print(f"✗ jail root: {exc}")
        return 1
    print(
        "  root of every jail: "
        + ", ".join(str(t) for t in trees)
        + " read-only; nothing else of the host exists inside"
    )
    proxy = shutil.which(BUS_PROXY)
    print(
        f"  {BUS_PROXY}: {proxy}"
        if proxy
        else f"  {BUS_PROXY} missing: an agy seat cannot be confined "
        "(Fedora: dnf install xdg-dbus-proxy)"
    )

    root = ensure_neutral_cwd() / ".quirework-blind-check"
    seat, sibling = root / "seat", root / "other-seat"
    seat.mkdir(parents=True, exist_ok=True)
    sibling.mkdir(parents=True, exist_ok=True)
    (sibling / "reaction-ch01.md").write_text("another seat's output\n")

    from quirework.providers import canary

    bad = 0
    # 0. The free flag-liveness contract. This is the half that catches a
    #    runner CHANGING under us rather than a jail that is misbuilt, and it
    #    is the dangerous half: a dead config key does not fail, it silently
    #    stops blinding. Which releases have been canaried is reported, never
    #    failed: a launch canaries its own release before it starts.
    for name in names:
        if shutil.which(name) is None:
            continue
        for probe in RUNNERS[name].probes:
            ok, detail = run_probe(name, probe)
            if ok:
                print(f"✓ {name}: {probe.label} is live")
            else:
                print(f"✗ {name}: {probe.label} -- {detail}\n    ({probe.why})")
                bad += 1
        have = runner_version(name)
        held = canary.proven(name, have)
        print(
            f"  {name} {have}: {len(held)} launch flag set(s) canaried at this release"
            if held
            else f"  {name} {have}: not canaried at this release yet; the next "
            "launch runs one first"
        )

    # 1. Each runner actually starts inside the jail. This is what keeps the
    #    credential/launcher table from rotting silently on a version bump.
    for name in names:
        binds, missing = runner_binds([name])
        for m in missing:
            print(f"✗ {name}: {m}")
            bad += 1
        try:
            # The bus comes up exactly as it would for a seat, so a missing
            # proxy or a dead session bus fails here rather than mid-run.
            with session_bus([name]) as bus:
                argv_full = jail_argv(seat, runners=[name], bus=bus, canary=PROBE)
                argv_full += [name, *RUNNERS[name].probe]
                r = subprocess.run(
                    argv_full,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    pass_fds=bus.pass_fds if bus else (),
                )
        except (subprocess.TimeoutExpired, OSError, BlindnessError) as exc:
            print(f"✗ {name}: probe failed to run ({exc})")
            bad += 1
            continue
        if r.returncode == 0:
            print(
                f"✓ {name}: starts inside the jail "
                f"({r.stdout.strip().splitlines()[0][:48] if r.stdout.strip() else 'ok'})"
            )
        else:
            print(
                f"✗ {name}: probe exited {r.returncode}: "
                f"{(r.stderr or r.stdout).strip()[:160]}"
            )
            bad += 1

    # 2. Containment itself, runner-independent: what must NOT be reachable.
    unreachable = {
        "the project tree": current_project().root,
        "the global CLAUDE.md": Path.home() / ".claude/CLAUDE.md",
        "the global GEMINI.md": Path.home() / ".gemini/GEMINI.md",
        "a sibling seat's run dir": sibling,
        # The session bus is a door out of any sandbox: `systemd --user`
        # starts processes on request outside the jail.
        "the runtime directory and its session bus": Path(f"/run/user/{os.getuid()}"),
    }
    for label, path in unreachable.items():
        r = subprocess.run(
            jail_argv(seat) + ["test", "-e", str(path)], capture_output=True
        )
        if r.returncode == 0:
            print(f"✗ reachable from inside the jail: {label} ({path})")
            bad += 1
        else:
            print(f"✓ unreachable: {label}")
    # 3. …and what MUST be, or the seat cannot read its own materials.
    (seat / "materials.md").write_text("ok\n")
    r = subprocess.run(
        jail_argv(seat) + ["test", "-e", str(seat / "materials.md")],
        capture_output=True,
    )
    print(
        (
            "✓ readable: the seat's own run dir"
            if r.returncode == 0
            else "✗ the seat cannot read its own run dir"
        )
    )
    bad += r.returncode != 0

    print(
        "✓ blind check: confinement clean."
        if not bad
        else f"✗ blind check: {bad} problem(s)."
    )
    return 1 if bad else 0


# One live seat per runner. Costs tokens, so it is opt-in and cheap-by-default;
# everything here is a fact no free probe can reach, because it is a property
# of the running seat rather than of the CLI's argument parser.
CANARY_PROMPT = (
    "Reply with EXACTLY four lines and nothing else.\n"
    "READ=<OK if you can read the file {target}, FAIL if you cannot>\n"
    "TOOLS=<comma-separated names of every tool you have, or NONE>\n"
    "PERSONA=<NONE if you have no custom personality/tone/style instruction, "
    "else quote it>\n"
    "CONTEXT=<NONE if you have no project or repository instructions in your "
    "context (an AGENTS.md, CLAUDE.md, GEMINI.md, or developer guidance about "
    "some particular person's project), else quote the first 15 words>\n"
    "Actually attempt the read before answering."
)

# CONTEXT is the assertion the whole apparatus exists for, and it is the one a
# free probe can never make: whether the seat is holding the project's own
# guidance. codex reads AGENTS.md from the working directory upward, claude
# reads CLAUDE.md, agy reads GEMINI.md, and none of that is visible in any
# flag -- only in what the running model turns out to know.
#
# The markers themselves are project data (providers.isolation_policy), because
# the words that prove a leak are the book's own and the tool cannot know them.
# Two rules govern what belongs on that list, and both were learned by getting
# them wrong. A word the seat echoes back from the path it was asked to read
# cannot be a marker: it fires on a perfectly blind seat. Neither can a phrase
# that is ordinary English, such as a book title made of common words, for the
# same reason. A marker that a blind seat could produce innocently reports a breach that never
# happened, which is the one failure this list cannot afford.


# What a canary's answer may and may not show is judged in `canary.judge`,
# which owns the forbidden tool names and the personality wording.

# The canary spends real money, so it spends the least available. claude's
# haiku and codex's mini are the cheapest tiers each offers and are named
# outright; agy's are RESOLVED at run time because its model list churns (it
# moved 3.6 -> 3.7 mid-week), and because leaving `--model` off does not mean
# "cheap" -- it means the ACCOUNT DEFAULT, which is whatever the operator last
# selected and was a Pro tier the first time this ran.


def cheapest_model(name: str) -> str | None:
    """The least expensive model this runner offers, or None to let it choose.

    For agy: the lowest effort tier of a flash model, picked out of
    ``agy models`` rather than pinned, so a rename cannot silently promote the
    canary to a Pro seat.

    A configured name resolves the way a seat's ``--model`` does: a family
    to its newest member at every launch, so the next release of the cheap
    model is canaried as itself without an edit, and an exact slug to
    itself. A name the runner's catalog cannot resolve raises
    ``ValueError``, which the canary turns into a refusal naming the
    setting.
    """
    configured = current_project().get("providers.canary_models", {})
    if name in configured:
        from quirework.providers import canary, models

        return models.resolve(name, configured[name], canary.EFFORT)["model"]
    if name != "agy":
        return None
    try:
        out = subprocess.run(
            ["agy", "models"], capture_output=True, text=True, timeout=120
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    slugs = [
        ln.split()[0]
        for ln in out.splitlines()
        if ln.strip() and not ln.startswith(("Fetching", " "))
    ]
    for want in (lambda s: "flash" in s and s.endswith("-low"), lambda s: "flash" in s):
        hit = [s for s in slugs if want(s)]
        if hit:
            return hit[0]
    # `agy models` needs the network and was seen to fail transiently. Falling
    # through to None would mean "no --model", which is NOT "cheap" -- it is
    # the account default, a Pro tier. So fall back to the model we last
    # actually validated, and if there is none, return None so the caller can
    # REFUSE rather than quietly buy the expensive seat.
    from quirework.providers import canary

    return canary.last_model(name)


# The shared blind flag set. A command Quirework builds is canaried under its
# own flags, rebuilt by its own builder; a command written by hand for
# `blind run` cannot be rebuilt, so its runners are proven under these.
CANARY_ARGV = {
    "claude": lambda p, m: [
        "claude",
        "-p",
        p,
        "--model",
        m,
        *MIN_TOOLS,
        *BLIND_SETTINGS,
        *NO_SETTING_SOURCES,
        *NO_MCP,
    ],
    "codex": lambda p, m: [
        "codex",
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "-c",
        'web_search="disabled"',
        "-c",
        'personality="none"',
        *CODEX_SEAT,
        "-s",
        "read-only",
        "-m",
        m,
        p,
    ],
    "agy": lambda p, m: ["agy", "-p", p, "--model", m],
}


def generic_twin(name: str):
    """The canary builder for one runner's shared blind flag set."""
    from quirework.providers.canary import Twin

    def build(prompt, model, workdir, session):
        return Twin(CANARY_ARGV[name](prompt, model))

    return build


def canary_once(name: str, model: str | None = None, timeout: int = 600) -> dict:
    """Run ONE live blind seat under the shared flag set, and judge it.

    Records nothing: the live tests watch the runners through it without
    touching the records launches rely on. A launch, and `blind canary`, go
    through `canary.require`, which judges with the same code. Returns
    ``canary.run``'s result.
    """
    from quirework.providers import canary

    why = (
        "could not resolve a cheapest model; refusing to run on the "
        "account default (pass --model to override)"
    )
    try:
        model = model or cheapest_model(name)
    except ValueError as exc:
        model, why = None, f"providers.canary_models does not resolve: {exc}"
    if model is None:
        # Refuse rather than run on whatever the account defaults to: "I could
        # not work out the cheap model" must never mean "use the expensive one".
        return {
            "ok": False,
            "problems": [why],
            "inconclusive": True,
            "fields": {},
            "model": None,
            "version": runner_version(name),
            "raw": "",
            "kept": None,
        }
    return canary.run(name, generic_twin(name), model, canary.BLIND, timeout=timeout)


def cmd_canary(argv) -> int:
    import argparse

    from quirework.providers import canary

    p = argparse.ArgumentParser(prog="quirework sop blind canary", add_help=False)
    p.add_argument("--runner", action="append", default=[])
    p.add_argument("--model", default=None)
    a = p.parse_args(argv)
    names = a.runner or list(RUNNERS)
    err = backend_error()
    if err:
        print(f"\u2717 backend: {err}")
        return 1

    bad = 0
    for name in names:
        if shutil.which(name) is None:
            print(f"  {name}: not installed -- skipped")
            continue
        try:
            entry = canary.require(
                "blind run", name, generic_twin(name), model=a.model, fresh=True
            )
        except canary.CanaryError as exc:
            print(f"\u2717 {name}: {exc}")
            bad += 1
            continue
        print(
            f"\u2713 {name} {entry['version']} [{entry['model']}]: "
            f"blind (no project context, no tree, no web, no MCP)"
        )
    print(
        "\u2713 blind canary: clean."
        if not bad
        else f"\u2717 blind canary: {bad} runner(s) failed."
    )
    return 1 if bad else 0


def cmd_prune(argv) -> int:
    grace, force, dry, roots = 3600.0, False, False, []
    rest = list(argv)
    while rest:
        item = rest.pop(0)
        if item == "--grace":
            grace = float(rest.pop(0))
        elif item == "--force":
            force = True
        elif item == "--dry-run":
            dry = True
        elif item.startswith("-"):
            print(f"blind prune: unknown option {item!r}", file=sys.stderr)
            return 2
        else:
            roots.append(item)
    if not roots:
        roots = [current_project().state_root]
    if not Path(roots[0]).exists() and len(roots) == 1:
        print(f"nothing to prune: {roots[0]} does not exist")
        return 0
    report = prune(roots, grace=grace, force=force, dry_run=dry)
    for home, size, why in report["kept"]:
        print(f"keep    {size / 1e6:8.1f} MB  {home}  ({why})")
    for home, size, why in report["removed"]:
        print(f"{why:<7} {size / 1e6:8.1f} MB  {home}")
    total = report["freed"] / 1e6
    verb = "would free" if dry else "freed"
    print(f"{verb} {total:.1f} MB across {len(report['removed'])} private homes")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    sub, rest = argv[0], argv[1:]
    if sub == "run":
        return cmd_run(rest)
    if sub == "check":
        return cmd_check(rest)
    if sub == "canary":
        return cmd_canary(rest)
    if sub == "prune":
        return cmd_prune(rest)
    print(USAGE, file=sys.stderr)
    print(f"blind: unknown subcommand {sub!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
