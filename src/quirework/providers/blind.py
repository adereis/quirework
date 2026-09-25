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

import json
import os
import shutil
import subprocess
import sys
import re
import time
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


def _options():
    """Read policy from the selected project for this operation."""
    from types import SimpleNamespace

    PROJECT_MARKERS = tuple(
        current_project()
        .data("providers.isolation_policy", {})
        .get("project_markers", ())
    )
    return SimpleNamespace(PROJECT_MARKERS=PROJECT_MARKERS)


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
    probe: tuple[str, ...] = ("--version",)
    probes: tuple[Probe, ...] = ()  # free flag-liveness contract
    notes: str = ""
    # Session-bus names the runner must reach to run at all. The jail never
    # binds the raw bus; it proxies exactly these in (see `Bus`).
    bus_names: tuple[str, ...] = ()


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
        # Its token lives in the login keyring, reached over the session bus.
        # The proxy lets this one name through, which still exposes every
        # secret the keyring holds, so the receipt flags it.
        bus_names=("org.freedesktop.secrets",),
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
        ),
        notes="sessions, logs and the memory store replaced by the private home",
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
        for c in RUNNERS[name].creds:
            if not _expand(c).exists():
                missing.append(f"{name}: {c} not found")
        if RUNNERS[name].bus_names and (reason := bus_proxy_missing()):
            missing.append(f"{name}: {reason}")
    return paths, missing


# The one suffix that mints a private runner home. Every workflow that
# confines a seat -- the run engine, the workshop, a room, a contextual
# review, a chapter comparison -- gets its home from `state_root`, so this
# module owns the name and is therefore the only place that can find them all
# again afterwards.
STATE_SUFFIX = ".blind-state"


def state_root(run_dir: Path) -> Path:
    """Where a run's private runner homes live: beside the run dir, never in it.

    Outside for the same reason the receipt is outside -- a seat that can see
    its own jail furniture can tell it is in one -- and because a runner's
    state dir is bound *over* its real path, so it must not also be visible as
    an ordinary directory in the seat's workspace.
    """
    return run_dir.parent / f"{run_dir.name}{STATE_SUFFIX}"


def private_homes(root: Path) -> list[Path]:
    """Every private runner home under ``root``, newest last."""
    found = [p for p in Path(root).rglob(f"*{STATE_SUFFIX}") if p.is_dir()]
    return sorted(found, key=lambda p: (_touched(p), str(p)))


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
    as a live process's cwd means that seat has not finished. Best effort by
    construction: ``/proc`` refuses processes this user does not own, and a
    seat merely paused between turns looks idle, which is what the age window
    in ``prune`` is for.
    """
    seat = home.parent / home.name[: -len(STATE_SUFFIX)]
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
        if cwd in (home, seat) or home in cwd.parents or seat in cwd.parents:
            return int(entry.name)
    return None


def prune(roots, *, grace: float = 3600, force: bool = False, dry_run: bool = False):
    """Remove finished seats' private runner homes; keep every record.

    A home is the harness's own installation and scratch state, bound over the
    real one so a seat cannot reach the operator's credentials or memory. It is
    large -- one measured codex seat kept 53 MB against 1.6 MB of records for
    the whole room -- and nothing collects it, because the state directory is
    deliberately outside what the cache tmpfiles rule touches.

    Nothing here reads a receipt, an answer or a transcript, so pruning cannot
    remove evidence: the native transcripts inside a home have already been
    read into the receipts beside it, and `launch.json` records the home's path
    whether or not the directory still exists.
    """
    removed, kept, freed = [], [], 0
    known = liveness_known()
    for root in roots:
        for home in private_homes(Path(root).expanduser().resolve()):
            if home.name[-len(STATE_SUFFIX) :] != STATE_SUFFIX or not home.is_dir():
                continue  # belt and braces: never rmtree something else
            size, pid = _bytes(home), occupied(home) if known else None
            age = time.time() - _touched(home)
            # The age window is advice and `force` overrides it. A live seat
            # is not: removing the home out from under a running harness would
            # break the run it is in the middle of, and no flag here should be
            # able to do that. Nor may "cannot tell" pass for "not in use".
            if not known:
                kept.append(
                    (
                        home,
                        size,
                        f"liveness unknown without /proc on {sys.platform}; "
                        "check for a live seat and remove the home by hand",
                    )
                )
            elif pid is not None:
                kept.append((home, size, f"in use by pid {pid}"))
            elif age < grace and not force:
                kept.append((home, size, f"touched {int(age)}s ago"))
            elif dry_run:
                removed.append((home, size, "would remove"))
                freed += size
            else:
                shutil.rmtree(home)
                removed.append((home, size, "removed"))
                freed += size
    return {"removed": removed, "kept": kept, "freed": freed}


def jail_argv(
    run_dir: Path,
    allow=(),
    runners=(),
    env=None,
    bind_credentials: bool = True,
    expose_run_dir: bool = True,
    project: Path | None = None,
    bus: Bus | None = None,
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
    """
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
    root = state_root(run_dir)
    creds: list[Path] = []
    for name in runners:
        r = RUNNERS[name]
        private = root / name
        private.mkdir(parents=True, exist_ok=True)
        argv += ["--bind", str(private), str(_expand(r.home))]
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
    return True, ""


def validated_path() -> Path:
    from quirework.project import current_project

    return current_project().path(
        "providers.validation_record",
        str(current_project().cache_root / "providers/validated.json"),
    )


def load_validated() -> dict:
    p = validated_path()
    return json.loads(p.read_text()) if p.exists() else {}


def minor(v: str | None) -> str | None:
    """``2.1.233`` -> ``2.1``. Patch churn is noise; a minor bump is an API."""
    return None if not v else ".".join(v.split(".")[:2])


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
        _SEAT_DIR = d
    return _SEAT_DIR


def confine(cmd, run_dir: Path, runners=(), allow=(), bus=None) -> list[str]:
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
    return jail_argv(run_dir, allow=allow, runners=runners, bus=bus) + list(cmd)


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
) -> None:
    """Stamp what confined this run, **outside the run dir**.

    Outside on purpose: a receipt the seat can read tells the seat it is a
    blind seat in an experiment, and names the operator's home layout. The
    parent of the run dir is not bound into the jail, so a file written there
    is invisible from inside.
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
       quirework sop blind canary [--runner NAME]... [--model M] [--record]
       quirework sop blind prune  [PATH]... [--grace SECONDS] [--force] [--dry-run]

Confine a blind seat to its run directory, so the project is not merely
out of sight but out of reach. See docs/workflows.md#execution-and-recovery, "The run dir is
not a wall".

  run     run CMD inside the jail; the exit status is CMD's own
  check   FREE self-test: every flag a blind run depends on is still
          live, each runner starts inside the jail, and the tree, the
          global briefs and a sibling seat's dir are all unreachable
  prune   remove finished seats' private runner homes, keeping every
          record. These are the harness installations bound over the
          real ones; they are large, nothing collects them, and no
          receipt depends on one surviving. Defaults to this project's
          run store.
  canary  COSTS TOKENS: one live seat per runner, asserting it really
          cannot read the tree and really has no web or MCP tool.
          --record stamps the validated version; `check` then reports
          drift when a runner is upgraded past it.

options for `prune`:
  PATH            where to look (default: the project's run store)
  --grace SECONDS keep a home touched more recently than this
                  (default: 3600); a paused seat looks idle
  --force         prune regardless of age; still refuses a live seat
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
    with session_bus(runners) as bus:
        argv_full = jail_argv(run_dir, allow=a.allow, runners=runners, bus=bus)
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

    bad = 0
    val = load_validated()
    # 0. The free flag-liveness contract, plus version drift. This is the half
    #    that catches a runner CHANGING under us rather than a jail that is
    #    misbuilt, and it is the dangerous half: a dead config key does not
    #    fail, it silently stops blinding.
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
        have, want = runner_version(name), val.get(name, {}).get("version")
        if want is None:
            print(
                f"  {name}: {have} (never canaried; `quirework sop blind "
                f"canary --runner {name}` costs tokens and records it)"
            )
        elif minor(have) != minor(want):
            print(
                f"✗ {name}: {have} installed, canaried at {want} -- a minor "
                f"bump is an API change.\n    Re-run `quirework sop blind "
                f"canary --runner {name}` and commit the record."
            )
            bad += 1
        else:
            print(
                f"✓ {name}: {have} (canaried at {want} on {val[name].get('date', '?')})"
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
                argv_full = jail_argv(seat, runners=[name], bus=bus)
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


# Any of these in a blind seat's tool list is a breach: a web tool defeats
# blindness, and an mcp__ tool means the operator's connected services (Gmail,
# Drive) reached the seat.
FORBIDDEN_TOOLS = ("web__run", "websearch", "webfetch", "google_web_search", "mcp__")

# The wording `-c personality="none"` actually removes, verbatim from a seat
# launched without it: "As Codex, you are an excellent communicator with a
# curious, rich personality." The base identity line is NOT this and is not
# removable, so it must not be matched here.
PERSONA_MARKERS = ("excellent communicator", "rich personality")

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
    """
    configured = current_project().get("providers.canary_models", {})
    if name in configured:
        return configured[name]
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
    return load_validated().get(name, {}).get("model")


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
        "-s",
        "read-only",
        "-m",
        m,
        p,
    ],
    "agy": lambda p, m: ["agy", "-p", p] + (["--model", m] if m else []),
}


def canary_once(
    name: str, model: str | None = None, run_dir: Path | None = None, timeout: int = 600
) -> dict:
    """Run ONE live blind seat and report what it turned out to be holding.

    The single implementation behind both `blind canary` and the live test
    suite, so the CLI and the tests can never drift into asserting different
    things. Costs one short exchange on the cheapest model the runner offers.

    Returns ``{ok, problems, inconclusive, fields, model, version, raw}``.
    ``inconclusive`` means the seat never answered in format -- auth, quota or
    a timeout -- and is reported separately from a breach, because "no answer
    to look at" and "no breach found" are not the same result.
    """
    from quirework.project import current_project

    run_dir = run_dir or (ensure_neutral_cwd() / ".quirework-blind-canary")
    run_dir.mkdir(parents=True, exist_ok=True)
    model = model or cheapest_model(name)
    prompt = CANARY_PROMPT.format(target=current_project().root / "AGENTS.md")
    out = {
        "ok": False,
        "problems": [],
        "inconclusive": False,
        "fields": {},
        "model": model,
        "version": runner_version(name),
        "raw": "",
    }
    if model is None:
        # Refuse rather than run on whatever the account defaults to. This is
        # the only test that spends money, so "I could not work out the cheap
        # model" must never resolve to "use the expensive one".
        out["inconclusive"] = True
        out["problems"].append(
            "could not resolve a cheapest model; refusing to run on the "
            "account default (pass --model to override)"
        )
        return out
    try:
        with session_bus([name]) as bus:
            r = subprocess.run(
                confine(
                    CANARY_ARGV[name](prompt, model), run_dir, runners=[name], bus=bus
                ),
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                cwd=str(run_dir),
                pass_fds=bus.pass_fds if bus else (),
            )
    except (subprocess.TimeoutExpired, OSError, BlindnessError) as exc:
        out["inconclusive"] = True
        out["problems"].append(f"seat did not run ({exc})")
        return out
    out["raw"] = text = (r.stdout or "") + (r.stderr or "")
    fields = dict(
        re.findall(r"(?mi)^\s*(READ|TOOLS|PERSONA|CONTEXT)\s*=\s*(.*)$", text)
    )
    out["fields"] = fields
    if len(fields) < 4:
        out["inconclusive"] = True
        out["problems"].append(
            f"seat did not answer in format (auth? quota? timeout?): "
            f"{text.strip()[:200]}"
        )
        return out

    if "fail" not in fields["READ"].lower():
        out["problems"].append(f"READ the project tree ({fields['READ'][:60]})")
    hits = [t for t in FORBIDDEN_TOOLS if t in fields["TOOLS"].lower()]
    if hits:
        out["problems"].append(f"forbidden tool(s): {', '.join(hits)}")
    if name == "codex" and any(m in fields["PERSONA"].lower() for m in PERSONA_MARKERS):
        out["problems"].append(
            f"the injected personality is back: {fields['PERSONA'][:70]}"
        )
    leaked = [m for m in _options().PROJECT_MARKERS if m in fields["CONTEXT"].lower()]
    if leaked:
        out["problems"].append(
            f"the project's own guidance is in the seat's context "
            f"({', '.join(leaked)}): {fields['CONTEXT'][:80]}"
        )
    out["ok"] = not out["problems"]
    return out


def cmd_canary(argv) -> int:
    import argparse
    import datetime

    from quirework.project import current_project

    p = argparse.ArgumentParser(prog="quirework sop blind canary", add_help=False)
    p.add_argument("--runner", action="append", default=[])
    p.add_argument("--model", default=None)
    p.add_argument(
        "--record",
        action="store_true",
        help="stamp tools/blind-runner/validated.json on success",
    )
    a = p.parse_args(argv)
    names = a.runner or list(RUNNERS)
    err = backend_error()
    if err:
        print(f"\u2717 backend: {err}")
        return 1

    val, bad = load_validated(), 0
    for name in names:
        if shutil.which(name) is None:
            print(f"  {name}: not installed -- skipped")
            continue
        try:
            res = canary_once(name, a.model)
        except BlindnessError as exc:
            print(f"\u2717 {name}: {exc}")
            bad += 1
            continue
        if res["inconclusive"]:
            print(f"\u2717 {name}: INCONCLUSIVE -- {res['problems'][0]}")
            bad += 1
            continue
        if not res["ok"]:
            for prob in res["problems"]:
                print(f"\u2717 {name}: {prob}")
            bad += 1
            continue
        print(
            f"\u2713 {name} {res['version']} [{res['model'] or 'default'}]: "
            f"blind (no project context, no tree, no web, no MCP)"
        )
        val[name] = {
            "version": res["version"],
            "model": res["model"],
            "date": datetime.date.today().isoformat(),
            "canary": "no project context; tree unreachable; "
            "no web/MCP tool" + ("; no personality" if name == "codex" else ""),
        }
    if a.record and not bad:
        path = validated_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(val, indent=2, sort_keys=True) + "\n")
        print(f"recorded {path.relative_to(current_project().root)}")
    elif a.record:
        print("NOT recorded: a canary failed.")
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
