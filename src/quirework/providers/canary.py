"""The launch canary: prove a seat blind on the runner version it will run.

A runner is a third-party CLI that updates itself several times a week, and a
seat's blindness is spelled out in that CLI's flags. A release can change what
a flag does without rejecting it: claude's ``--tools ""`` fell back to the
default tool set, and a Codex release left an image viewer on in a seat whose
flags turned it off. The free probes in ``blind`` catch a flag the parser
stops accepting; only a live seat can say what it is holding. The canary is
that seat: one short exchange on the cheapest model, asking what it can read,
which tools it has, and what instructions it was given (``CANARY_PROMPT``).

``require`` runs it at launch, the moment the answer matters, and never from a
test suite, where a vendor release fails the suite whether or not anything is
about to run. Every launch site calls it before its seat starts, and
``blind.jail_argv`` refuses to build a runner's jail without the record it
returns, so a launch that skipped it cannot start. A passing canary is keyed on
three things:

- the runner and its **exact** version. Neither vendor's numbering says which
  releases change behaviour: Codex's minor number counts releases, and Claude
  ships everything as a patch. Keying on less was too loud for one runner and
  silent for the other.
- a fingerprint of the command the seat will run, rebuilt by the launch's own
  builder with the canary's prompt, model and session in place of the seat's.
  The canary therefore exercises the seat's flags, not a copy of them, and a
  change to those flags in Quirework is a new key as surely as a release is.
- the seat's contract: what it may hold on purpose (``Contract``).

A key with a passing record launches at once. Without one the canary runs
first, and a breach, or an answer that proves nothing, refuses the launch.
Records live under the project's cache, per machine, because blindness is a
property of the runner installed here: a canary passed on another machine says
nothing about this one. Each launch writes the record it relied on into its own
receipt, so the evidence travels with the run.

What the canary does not reproduce is the launch site's own jail furniture:
it runs in a fresh jail of the shared shape, not with the materials or the
repository a site binds on purpose. Those binds are Quirework's code and its
tests' business. The runner's behaviour under the seat's flags is what changes
without warning, and that is what this checks.
"""

from __future__ import annotations

import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from quirework.project import current_project
from quirework.providers import blind


@dataclass(frozen=True)
class Contract:
    """What a seat may hold on purpose; anything else it holds is a breach.

    A blind seat may hold none of it. A contextual review stands in the
    repository and is meant to read it and its guidance (``tree``,
    ``context``). A research seat is meant to search the web (``web``). No
    contract allows the operator's connected services (an ``mcp__`` tool), or
    Codex's injected personality.
    """

    tree: bool = False
    context: bool = False
    web: bool = False


BLIND = Contract()


@dataclass(frozen=True)
class Twin:
    """The command a seat would run, rebuilt with the canary's inputs.

    ``argv`` is the runner's command without the jail and ``stdin`` what it
    reads. ``binds`` are ``(source, target)`` pairs the command expects inside
    the jail, and ``files`` maps a name to text written into the canary's work
    directory first. A builder must not touch the filesystem itself, since it
    is also called with placeholder inputs to compute the fingerprint.
    """

    argv: list
    stdin: bytes | None = None
    binds: tuple = ()
    files: dict = field(default_factory=dict)


# build(prompt, model, workdir, session) -> Twin
Build = Callable[[str, str, Path, str], Twin]

# The canary's own effort and, where a seat has a system prompt, the one it is
# given instead. Neither changes what a seat can reach, and both keep a canary
# cheap and its fingerprint the same whatever the seat itself asked for.
EFFORT = "low"
SYSTEM_PROMPT = "You are a careful assistant. Answer exactly as you are asked."
# A context ceiling for a twin whose launcher pins one. Over-requested on
# purpose: Codex clamps it to the model's own ceiling, and a seat's real value
# would make every seat model a canary of its own.
WINDOW = 1_000_000

# The inputs the fingerprint is computed with. A real canary runs with a fresh
# directory and session, which must not make every canary a new key.
_PLACEHOLDER = dict(
    prompt="<canary prompt>",
    workdir=Path("/canary/work"),
    session="00000000-0000-4000-8000-000000000000",
)

# Tool names that give a seat the web, by runner; any `mcp__` tool means the
# operator's connected services reached it.
WEB_TOOLS = ("web__run", "web_search", "websearch", "webfetch", "google_web_search")
MCP_TOOLS = ("mcp__",)

# The wording `-c personality="none"` actually removes, verbatim from a seat
# launched without it: "As Codex, you are an excellent communicator with a
# curious, rich personality." The base identity line is NOT this and is not
# removable, so it must not be matched here.
PERSONA_MARKERS = ("excellent communicator", "rich personality")

_FIELDS = re.compile(r"(?mi)^\s*(READ|TOOLS|PERSONA|CONTEXT)\s*=\s*(.*)$")
_FEATURE_OFF = re.compile(r"features\.(\w+)=false")


class CanaryError(blind.BlindnessError):
    """A launch was refused because its seat could not be proven blind."""


def store_path() -> Path:
    """This project's canary records on this machine."""
    return current_project().cache_root / "providers" / "canaries.json"


def records() -> dict:
    path = store_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise CanaryError(f"cannot read the canary records at {path}: {exc}") from exc


def _write(entries: dict) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".canaries-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


@contextmanager
def _locked(runner: str):
    """One canary per runner at a time across processes.

    Seats are often launched together. The first launch on a new release runs
    the canary, and the others wait for its record instead of each paying for
    their own.
    """
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / f"canary-{runner}.lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def fingerprint(twin: Twin, contract: Contract) -> str:
    """What the canary proved, apart from the runner and its version."""
    text = json.dumps(
        {
            "argv": [str(a) for a in twin.argv],
            "stdin": (twin.stdin or b"").decode("utf-8", "replace"),
            "binds": [[str(s), str(t)] for s, t in twin.binds],
            "files": twin.files,
            "contract": asdict(contract),
        },
        sort_keys=True,
    )
    return hashlib.sha256(text.encode()).hexdigest()


def key(runner: str, version: str, print_: str) -> str:
    return f"{runner} {version} {print_[:16]}"


def proven(runner: str, version: str | None) -> list[dict]:
    """The passing records this machine holds for one runner release."""
    return [
        e
        for e in records().values()
        if e.get("runner") == runner and e.get("version") == version
    ]


def last_model(runner: str) -> str | None:
    """The model the most recent passing canary for ``runner`` ran on."""
    entries = [e for e in records().values() if e.get("runner") == runner]
    entries.sort(key=lambda e: e.get("at", ""))
    return entries[-1].get("model") if entries else None


def require(
    label: str,
    runner: str,
    build: Build,
    contract: Contract = BLIND,
    *,
    model: str | None = None,
    fresh: bool = False,
    timeout: int = 600,
) -> dict:
    """The passing canary record this launch relies on, running one if needed.

    ``label`` names the launch in messages and in the record. Raises
    ``CanaryError`` when the seat cannot be proven blind; nothing launches
    then, because a seat that might be holding the project's guidance or the
    web produces evidence that looks the same as one that is not. ``fresh``
    runs the canary even when a record exists, which is what the canary
    command does when someone asks it to look again.
    """
    if runner not in blind.RUNNERS:
        raise CanaryError(f"{label}: no canary knows the runner {runner!r}")
    version = blind.runner_version(runner)
    if version is None:
        raise CanaryError(
            f"{label}: cannot read `{runner} --version`, so nothing can say "
            f"which release this seat would run"
        )
    model = model or blind.cheapest_model(runner)
    if model is None:
        # Never the account default: that is whatever the operator selected
        # last, and was a Pro tier the first time an unnamed model ran.
        raise CanaryError(
            f"{label}: could not resolve the cheapest {runner} model for its "
            f"canary; name one in providers.canary_models"
        )
    placeholder = build(
        _PLACEHOLDER["prompt"], model, _PLACEHOLDER["workdir"], _PLACEHOLDER["session"]
    )
    print_ = fingerprint(placeholder, contract)
    k = key(runner, version, print_)
    with _locked(runner):
        held = records().get(k)
        if held and not fresh:
            return held
        print(
            f"canary: proving {runner} {version} blind for {label} "
            f"(one short exchange on {model})",
            file=sys.stderr,
            flush=True,
        )
        result = run(runner, build, model, contract, timeout=timeout)
        if result["inconclusive"]:
            raise CanaryError(
                f"{label}: the canary for {runner} {version} proved nothing: "
                f"{result['problems'][0]}. Nothing was launched; its output is "
                f"kept in {result['kept']}"
            )
        if result["problems"]:
            raise CanaryError(
                f"{label}: {runner} {version} is NOT blind under this seat's "
                f"flags: {'; '.join(result['problems'])}. Nothing was launched; "
                f"the canary's answer is kept in {result['kept']}"
            )
        entry = {
            "runner": runner,
            "version": version,
            "fingerprint": print_,
            "label": label,
            "model": model,
            "contract": asdict(contract),
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(
                timespec="seconds"
            ),
        }
        entries = records()
        entries[k] = entry
        _write(entries)
        print(f"canary: {runner} {version} is blind", file=sys.stderr, flush=True)
        return entry


def run(
    runner: str, build: Build, model: str, contract: Contract, *, timeout: int = 600
) -> dict:
    """Run one canary seat and judge its answer; record nothing.

    Returns ``{ok, problems, inconclusive, fields, model, version, raw,
    kept}``. ``inconclusive`` means the seat never answered in the format --
    auth, quota or a timeout -- and is reported apart from a breach, because
    "no answer to look at" and "no breach found" are different results.
    ``kept`` is the file holding the output when the canary did not pass. The
    canary's work directory and runner state are removed either way: the
    output is what an operator reads, and the state is jail furniture.

    ``QUIREWORK_CANARY=0`` forbids this in a process, as ``QUIREWORK_USAGE=0``
    forbids recording. A test suite sets it, because a test that drives a
    launch without stubbing the gate would otherwise reach a real runner and
    spend a real model's tokens. Refused, never skipped: a launch whose seat
    cannot be proven does not start.
    """
    if os.environ.get("QUIREWORK_CANARY") == "0":
        raise CanaryError(
            "live canaries are switched off in this process (QUIREWORK_CANARY=0), "
            "so no seat can be proven blind and none launches"
        )
    err = blind.backend_error()
    if err:
        raise CanaryError(f"cannot confine a canary seat: {err}")
    workdir = Path(tempfile.mkdtemp(prefix=".canary-", dir=blind.ensure_neutral_cwd()))
    out = {
        "ok": False,
        "problems": [],
        "inconclusive": False,
        "fields": {},
        "model": model,
        "version": blind.runner_version(runner),
        "raw": "",
        "kept": None,
    }
    twin = build(
        blind.CANARY_PROMPT.format(target=current_project().root / "AGENTS.md"),
        model,
        workdir,
        str(uuid.uuid4()),
    )
    for name, text in twin.files.items():
        (workdir / name).write_text(text, encoding="utf-8")
    try:
        with blind.session_bus([runner]) as bus:
            argv = blind.jail_argv(
                workdir, runners=[runner], bus=bus, canary=blind.PROBE
            )
            for source, target in twin.binds:
                argv += ["--bind", str(source), str(target)]
            proc = subprocess.run(
                argv + list(twin.argv),
                input=twin.stdin,
                stdin=None if twin.stdin is not None else subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout,
                cwd=str(workdir),
                pass_fds=bus.pass_fds if bus else (),
            )
        text = (proc.stdout or b"").decode("utf-8", "replace")
        text += (proc.stderr or b"").decode("utf-8", "replace")
    except (subprocess.TimeoutExpired, OSError, blind.BlindnessError) as exc:
        out["inconclusive"] = True
        out["problems"].append(f"seat did not run ({exc})")
        text = ""
    out["raw"] = text
    if not out["inconclusive"]:
        answer, init = extract(proc.stdout.decode("utf-8", "replace"))
        fields = dict(_FIELDS.findall(answer))
        out["fields"] = fields
        if len(fields) < 4:
            out["inconclusive"] = True
            out["problems"].append(
                "seat did not answer in format (auth? quota? timeout?): "
                + (answer or text).strip()[:200]
            )
        else:
            out["problems"] = judge(runner, twin.argv, fields, contract, init)
    out["ok"] = not out["problems"]
    if not out["ok"]:
        kept = workdir.parent / f"{workdir.name}.output.txt"
        kept.write_text(text)
        out["kept"] = str(kept)
    shutil.rmtree(workdir, ignore_errors=True)
    shutil.rmtree(blind.state_root(workdir), ignore_errors=True)
    return out


def extract(stdout: str) -> tuple[str, dict | None]:
    """The seat's final answer, and Claude's init event when there is one.

    A runner writing an event stream echoes its input among its events, and
    the canary's prompt contains every field name followed by a template
    saying FAIL, which would read as a passing answer. So the answer is only
    ever taken from the event each runner uses for the model's own reply,
    never from whatever text a stream happens to contain; a stream with none
    of them yields no answer, which is inconclusive. Plain output is the
    answer as it stands.
    """
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    if not events:
        return stdout, None
    init = next(
        (e for e in events if e.get("type") == "system" and e.get("subtype") == "init"),
        None,
    )
    claude = [
        e["result"]
        for e in events
        if e.get("type") == "result" and isinstance(e.get("result"), str)
    ]
    codex = [
        e["item"].get("text", "")
        for e in events
        if e.get("type") == "item.completed"
        and isinstance(e.get("item"), dict)
        and e["item"].get("type") == "agent_message"
    ]
    agy = [
        e["result"].get("response", "")
        for e in events
        if e.get("event") == "result" and isinstance(e.get("result"), dict)
    ]
    said = claude or codex or agy
    return (said[-1] if said else ""), init


def judge(
    runner: str, argv, fields: dict, contract: Contract, init: dict | None = None
) -> list[str]:
    """Every way this answer shows the seat holding what its contract denies."""
    problems = []
    if not contract.tree and "fail" not in fields["READ"].lower():
        problems.append(f"READ the project tree ({fields['READ'][:60]})")
    said = fields["TOOLS"].lower()
    forbidden = MCP_TOOLS + (() if contract.web else WEB_TOOLS)
    hits = [t for t in forbidden if t in said]
    if hits:
        problems.append(f"forbidden tool(s): {', '.join(hits)}")
    argv = [str(a) for a in argv]
    if init is not None:
        # Claude's own list of what it loaded, which the model cannot misreport.
        held = [str(t) for t in init.get("tools") or []]
        if init.get("mcp_servers"):
            problems.append("MCP servers reached the seat")
        listed = [t for t in held if any(f in t.lower() for f in forbidden)]
        if listed:
            problems.append(f"the runner loaded forbidden tool(s): {', '.join(listed)}")
        if "--tools" in argv:
            allowed = set(argv[argv.index("--tools") + 1].split(","))
            extra = sorted(set(held) - allowed)
            if extra:
                problems.append(
                    f"tools its --tools list does not name: {', '.join(extra)}"
                )
    # A feature a seat's flags turn off must be off. The model's tool names
    # are matched whole, so only a tool that kept the feature's exact name is
    # caught -- as an image viewer that survived its flag once did.
    named = {re.sub(r"[^\w]", "", t.strip().lower()) for t in said.split(",")}
    off = [m.group(1) for a in argv if (m := _FEATURE_OFF.fullmatch(a))]
    on = [f for f in off if f.lower() in named]
    if on:
        problems.append(f"a tool its flags turn off is on: {', '.join(on)}")
    if runner == "codex" and any(
        m in fields["PERSONA"].lower() for m in PERSONA_MARKERS
    ):
        problems.append(f"the injected personality is back: {fields['PERSONA'][:70]}")
    if not contract.context:
        markers = blind.project_markers()
        leaked = [m for m in markers if m in fields["CONTEXT"].lower()]
        if leaked:
            problems.append(
                f"the project's own guidance is in the seat's context "
                f"({', '.join(leaked)}): {fields['CONTEXT'][:80]}"
            )
    return problems
