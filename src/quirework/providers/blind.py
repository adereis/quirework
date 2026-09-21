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
    """
    home = Path.home()
    argv = [BWRAP, "--bind", "/", "/", "--dev-bind", "/dev", "/dev", "--proc", "/proc"]
    for target in BLANKED:
        argv += ["--tmpfs", str(home) if target == "$HOME" else target]
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
        argv = [
            BWRAP,
            "--bind",
            "/",
            "/",
            "--dev-bind",
            "/dev",
            "/dev",
            "--proc",
            "/proc",
            "--unshare-net",
            *argv,
        ]
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


def confine(cmd, run_dir: Path, runners=(), allow=()) -> list[str]:
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
    return jail_argv(run_dir, allow=allow, runners=runners) + list(cmd)


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
    path: Path, *, confined: bool, cmd, run_dir: Path, runners, binds, reason: str = ""
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
        binds, argv_full = [], list(cmd)
        print(
            "blind run: WARNING -- running UNCONFINED; the seat can reach "
            "the project tree.",
            file=sys.stderr,
        )
    else:
        binds, missing = runner_binds(runners)
        for m in missing:
            print(f"blind run: warning: {m}", file=sys.stderr)
        argv_full = jail_argv(run_dir, allow=a.allow, runners=runners) + list(cmd)

    if a.dry_run:
        print(" ".join(argv_full))
        return 0

    write_receipt(
        receipt,
        confined=not a.unconfined,
        cmd=cmd,
        run_dir=run_dir,
        runners=runners,
        binds=binds,
        reason=err or "",
    )
    return subprocess.run(argv_full, cwd=None if a.unconfined else run_dir).returncode


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
        argv_full = jail_argv(seat, runners=[name]) + [name, *RUNNERS[name].probe]
        try:
            r = subprocess.run(argv_full, capture_output=True, text=True, timeout=120)
        except (subprocess.TimeoutExpired, OSError) as exc:
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
        r = subprocess.run(
            confine(CANARY_ARGV[name](prompt, model), run_dir, runners=[name]),
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            cwd=str(run_dir),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
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
