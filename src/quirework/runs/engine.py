"""Prepare, execute, recover and inspect explicitly designed model runs.

The operator chooses questions, scope, delivery and acceptance. This controller
only performs the declared turns, in order, preserving their evidence.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from quirework.providers import blind
from quirework.providers import claude_login
from quirework.providers import exits
from quirework.providers import quota
from quirework.providers.quota import rewind_sessions, session_marks
from quirework.providers import models as workshop_drivers
from quirework.sops import personas
from quirework.project import current_project
from quirework.project import submit
from quirework.core import usage as usage_records
from quirework.delivery import artifacts
from quirework.providers import harness
from quirework.providers import reads
from quirework.sops import instruments
from quirework.runs.storage import digest
from quirework.runs.storage import hashes
from quirework.runs.storage import tree_digest
from quirework.runs.storage import identifier
from quirework.runs.storage import Held
from quirework.runs.storage import lock
from quirework.runs.storage import process_identity
from quirework.runs.storage import read
from quirework.runs.storage import relative
from quirework.runs.storage import slot
from quirework.runs.storage import write

SCHEMA = 1
# Waiting out a provider's own reset is the default, because the alternative
# is losing a long read to a limit that clears itself.
WAIT_QUOTA_DEFAULT = 21600
# The continuation scope of a turn the operator chose to continue after a stop
# the runner could not classify, as against a provider's own limit scope.
OPERATOR_CONTINUATION = "operator"
# The continuation scope of a turn the runner continued itself after the seat's
# borrowed Claude login was renewed under it (``claude_login``), and how many
# times one turn may be continued that way before the runner stops and leaves
# it to the operator: a login that keeps failing is not a renewal.
AUTH_CONTINUATION = "auth"
AUTH_CONTINUATION_CAP = 2
POLICY = dict(
    tools="none",
    system_prompt=None,
    filesystem="enforced",
    allow_compaction=False,
    # Left unset here and filled at preparation with the resolved model's own
    # ceiling (see resolve_window). A hardcoded number goes stale silently:
    # the previous 1,000,000 outlived the ceiling it was written for and only
    # looked right because the client clamps. Callers that build a policy
    # without preparing one still get plain numbers, never a sentinel.
    context_window=None,
    minimum_context_window=0,
    read_roots=[],
    # A digest per mounted root (`storage.tree_digest`). A mounted read has
    # no frozen copy of its material, so this is how the record names the
    # bytes the seat was given: launch refuses if they have changed, and the
    # receipt flags a change during the turn.
    read_root_digests={},
    # Environment variable names a declared gateway model needs passed
    # through to its runner (see ``providers.claude.gateway_env``). Empty
    # for every seat that runs on the host's own login.
    gateway_env=(),
    # The most a Claude seat may write in one response, thinking included.
    # Unset, the CLI picks its own default per model, which for some models
    # is half the ceiling the model itself allows. A response cut at any
    # other count is flagged on the turn's receipt.
    output_ceiling=None,
)
# The provider serves a share of the ceiling, and rounds it. Assert we were
# given essentially the whole window, not the much smaller session default,
# without failing a run over a rounding change.
WINDOW_TOLERANCE = 0.99
# Claude publishes no catalog. Its CLI accepts an auto-compact window up to
# this, which is also the largest window its current models carry.
CLAUDE_MAX_WINDOW = 1_000_000
# What `start` reports for a seat another operator holds. It is not a
# failure: that operator's own command runs the seat and reports it.
HELD = "held by another operator"


def order(seats):
    """The order seats run in: spend order, with every fork after its parent.

    Two constraints, and they cannot conflict. A fork needs an idle completed
    parent, so the topological rule is absolute and is enforced by the walk
    below. Within what that allows, the walk visits seats in the order
    `quota.spend_order` gives -- one cheap seat, then the premium seat, then
    the rest -- so a run that has declared its tiers finds a broken assignment
    on its cheapest seat and meets the quota wall on a replaceable one. A plan
    that declares no tiers is unaffected and keeps its declared order.
    """
    by_id = {s["id"]: s for s in seats}
    done, active, result = set(), set(), []

    def visit(name):
        if name in active:
            raise ValueError("cycle in seat parents")
        if name in done:
            return
        active.add(name)
        parent = by_id[name].get("parent")
        if parent:
            if parent["seat"] not in by_id:
                raise ValueError("unknown parent seat")
            source = by_id[parent["seat"]]
            if parent["after"] != source["turns"][-1]["id"]:
                raise ValueError("fork boundary must be the declared last parent turn")
            if source["mode"] != "auto":
                raise ValueError("external history cannot supply a native fork")
            if not personas.same_perspective(
                source.get("persona"), by_id[name].get("persona")
            ):
                raise ValueError("a different persona requires a fresh seat")
            if (
                source["config"] != by_id[name]["config"]
                or source["policy"] != by_id[name]["policy"]
            ):
                raise ValueError(
                    "a fork must retain the parent model and execution policy"
                )
            visit(parent["seat"])
        active.remove(name)
        done.add(name)
        result.append(name)

    for seat in quota.spend_order(seats):
        visit(seat["id"])
    return result


def policy(value):
    if not isinstance(value, dict) or set(value) - set(POLICY):
        raise ValueError(f"policy fields: {sorted(POLICY)}")
    p = dict(POLICY, **value)
    if p["tools"] not in ("none", "read", "write", "research") or p[
        "filesystem"
    ] not in ("enforced", "audited"):
        raise ValueError("invalid tools/filesystem policy")
    if p["system_prompt"] is not None and not isinstance(p["system_prompt"], str):
        raise ValueError("system_prompt must be text or null")
    if type(p["allow_compaction"]) is not bool:
        raise ValueError("allow_compaction must be boolean")
    if not isinstance(p["gateway_env"], (list, tuple)) or not all(
        isinstance(v, str) and v for v in p["gateway_env"]
    ):
        raise ValueError("gateway_env must be a list of environment variable names")
    for key in ("context_window", "minimum_context_window"):
        if p[key] is not None and (type(p[key]) is not int or p[key] < 0):
            raise ValueError(f"{key} must be a nonnegative integer")
    if p["output_ceiling"] is not None and (
        type(p["output_ceiling"]) is not int or p["output_ceiling"] < 1
    ):
        raise ValueError("output_ceiling must be a positive integer or null")
    if not isinstance(p["read_roots"], list):
        raise ValueError("read_roots must be a list of explicit directory paths")
    p["read_roots"] = [
        str(Path(x).expanduser().resolve(strict=True)) for x in p["read_roots"]
    ]
    for root in p["read_roots"]:
        if Path(root) in (Path("/"), Path.home()) or not Path(root).is_dir():
            raise ValueError(
                "read_roots must name specific directories, not the entire home or filesystem"
            )
    if not isinstance(p["read_root_digests"], dict):
        raise ValueError("read_root_digests must map a read root to its digest")
    digests = {}
    for root, value in p["read_root_digests"].items():
        resolved = str(Path(root).expanduser().resolve(strict=True))
        if resolved not in p["read_roots"]:
            raise ValueError(
                f"read_root_digests names a path outside read_roots: {root}"
            )
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("read_root_digests values must be sha256 hex digests")
        digests[resolved] = value
    p["read_root_digests"] = digests
    return p


def resolve_window(p, config):
    """Turn a "max" window request into this model's actual ceiling.

    Done at preparation, where the model is resolved and the catalog is
    already in hand, so the frozen record states a concrete number and the
    run can be audited against it later.
    """
    ceiling = (
        config.get("max_context_window")
        or config.get("context_window")
        or (CLAUDE_MAX_WINDOW if config["harness"] == "claude" else None)
    )
    if p["context_window"] is None:
        p["context_window"] = ceiling
    if not p["minimum_context_window"]:
        # Only Codex reports an effective window per turn. Asserting a
        # minimum where no receipt can evidence it would fail every run, so
        # Claude and agy rely on the compaction guard instead -- an asymmetry
        # in what the providers publish, not a weaker standard.
        percent = config.get("effective_context_window_percent") or 100
        p["minimum_context_window"] = (
            int(ceiling * percent / 100 * WINDOW_TOLERANCE)
            if ceiling and config["harness"] == "codex"
            else 0
        )
    return p


def freeze_executor(destination):
    """Freeze the generic executor and its resolved project settings."""
    package = Path(__file__).resolve().parents[1]
    target = destination / "executor/src/quirework"
    needed = {
        "__init__.py",
        "project.py",
        "settings.py",  # imported by project.py
        "core/__init__.py",
        "core/usage.py",
        "sops/__init__.py",
        "sops/personas.py",
        "sops/instruments.py",
    }
    for folder in ("runs", "providers", "delivery"):
        needed.update(
            str(p.relative_to(package)) for p in (package / folder).rglob("*.py")
        )
    for rel in sorted(needed):
        source = package / rel
        if source.is_symlink():
            raise ValueError("symlink in executor source")
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    write(destination / "executor/project.json", current_project().describe())
    return hashes(destination / "executor")


# The red flags a plan may accept in advance, each with the seat policy that
# makes it harmless. A seat's receipt still records an accepted flag; it
# only stops counting against the seat's status. Compaction and every other
# flag are never acceptable: a failed run condition does not become a clean
# result by being declared.
ACCEPTABLE_FLAGS = {
    # agy reaches its login over a filtered session bus, so the keyring is
    # inside the jail. A seat with no tools cannot ask it for anything.
    "session-bus": (blind.Bus.FLAG_PREFIX, lambda policy: policy["tools"] == "none"),
}


def flag_code(text):
    """The code a plan accepts ``text`` under, or ``None`` for any other flag."""
    for code, (prefix, _) in ACCEPTABLE_FLAGS.items():
        if text.startswith(prefix):
            return code
    return None


def canary_model(runner, seat, known):
    """Refuse a seat whose isolation canary has no model to run on.

    Every seat is proven blind by a short exchange on its runner's cheapest
    model before it launches, and the canary refuses when no such model is
    known. A record keeps the project configuration it was prepared with, so
    a model named after preparing never reaches it: the seat would stop at
    its first launch and no resume could start it. ``known`` caches each
    runner's answer across a plan's seats.
    """
    from quirework.providers import canary as canaries

    if runner in known:
        return
    try:
        known[runner] = canaries.model_for(runner, f"seat {seat}")
    except canaries.CanaryError as exc:
        raise ValueError(
            f"{exc}. A record keeps the configuration it was prepared with, "
            "so the model must be named before preparing"
        ) from exc


def prepare(plan_path, destination, *, resolve=True):
    plan_path = Path(plan_path).resolve()
    destination = Path(destination).expanduser().resolve()
    raw = read(plan_path)
    if set(raw) - {"title", "source", "seats", "metadata"}:
        raise ValueError("plan fields: title, source, seats, metadata")
    if not isinstance(raw.get("seats"), list) or not raw["seats"]:
        raise ValueError("declare at least one seat")
    if destination.exists():
        raise ValueError("destination exists; inspect or resume it, do not replace it")
    # Records contain raw sessions. Inside a checkout they belong in build/.
    if destination.is_relative_to(
        current_project().root
    ) and not destination.is_relative_to(
        current_project().path("publish.build", "build")
    ):
        raise ValueError("raw run records belong outside the checkout or under build/")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".prepare-", dir=destination.parent))
    try:
        seats = []
        names = set()
        material_cache = {}
        canaries = {}
        for supplied in raw["seats"]:
            if set(supplied) - {
                "id",
                "harness",
                "model",
                "effort",
                "policy",
                "turns",
                "library",
                "parent",
                "instrument",
                "mode",
                "metadata",
                "persona",
                "scope",
                "exposure",
                "tier",
                "accept_red_flags",
            }:
                raise ValueError("unknown seat field")
            name = identifier(supplied["id"])
            if name in names:
                raise ValueError("duplicate seat id")
            names.add(name)
            tier = supplied.get("tier", "standard")
            if tier not in quota.TIERS:
                raise ValueError(f"seat tier must be one of {quota.TIERS}")
            config = {k: supplied[k] for k in ("harness", "model", "effort")}
            mode = supplied.get("mode", "auto")
            if mode not in ("auto", "external"):
                raise ValueError("seat mode must be auto or external")
            declared = [
                str(m).strip().lower()
                for m in current_project().get("providers.claude.models", [])
            ]
            if resolve and mode == "auto":
                config = workshop_drivers.resolve(
                    config["harness"],
                    config["model"],
                    config["effort"],
                    declared=declared,
                )
                canary_model(config["harness"], name, canaries)
            p = resolve_window(policy(supplied.get("policy", {})), config)
            if (
                config.get("harness") == "claude"
                and str(config.get("model", "")).strip().lower() in declared
                and (
                    gateway_env := current_project().get("providers.claude.gateway_env")
                )
            ):
                # A declared claude model runs through the project's gateway:
                # the launch passes those variables through and never borrows
                # the host login. Unsourced, it refuses rather than fall back.
                p["gateway_env"] = list(gateway_env)
            instrument = (
                instruments.load(supplied["instrument"])
                if supplied.get("instrument")
                else None
            )
            # A literal prompt is a complete supplied assignment. Never infer
            # a second role for it. Higher-level SOP helpers select profiles.
            default_profile = (
                (instrument or {}).get("default_persona")
                if (supplied.get("turns") and "prompt" not in supplied["turns"][0])
                else None
            )
            reference = supplied.get("persona", default_profile)
            persona = personas.resolve(reference) if reference is not None else None
            scope = supplied.get(
                "scope", (instrument or {}).get("default_scope", "artifact")
            )
            if scope not in instruments.SCOPES:
                raise ValueError(
                    f"scope must be one of {', '.join(instruments.SCOPES)}"
                )
            exposure = supplied.get(
                "exposure", "project" if p["read_roots"] else "packet"
            )
            if exposure not in ("packet", "project") or bool(p["read_roots"]) != (
                exposure == "project"
            ):
                raise ValueError(
                    "project exposure requires project read roots, and conversely"
                )
            turns = []
            labels = set()
            if not supplied.get("turns"):
                raise ValueError("seat must declare turns")
            for index, turn in enumerate(supplied["turns"]):
                if set(turn) - {
                    "id",
                    "prompt",
                    "ask",
                    "materials",
                    "headings",
                    "outputs",
                    "allow_unchanged_outputs",
                    "min_bytes",
                    "metadata",
                    "addenda",
                    "referenced_images",
                    "referenced_files",
                }:
                    raise ValueError("unknown turn field")
                # How many images the material references, or the image files
                # themselves, which lets the receipt match what was opened by
                # bytes rather than by count.
                referenced = turn.get("referenced_images", 0)
                if isinstance(referenced, list):
                    if any(not isinstance(r, str) for r in referenced):
                        raise ValueError("referenced_images must list paths")
                    referenced = [
                        str(Path(r).expanduser().resolve(strict=True))
                        for r in referenced
                    ]
                    for r in referenced:
                        if not Path(r).is_file() or not any(
                            Path(r).is_relative_to(root) for root in p["read_roots"]
                        ):
                            raise ValueError(
                                f"referenced_images must be files under read_roots: {r}"
                            )
                elif type(referenced) is not int or referenced < 0:
                    raise ValueError(
                        "referenced_images must be a non-negative integer "
                        "or a list of paths"
                    )
                # The files a seat is to read in full, in reading order. Like
                # the image count, it turns the native record into a check: a
                # seat that skimmed or skipped would otherwise look the same.
                texts = turn.get("referenced_files", [])
                if not isinstance(texts, list) or any(
                    not isinstance(t, str) for t in texts
                ):
                    raise ValueError("referenced_files must be a list of paths")
                texts = [str(Path(t).expanduser().resolve(strict=True)) for t in texts]
                for t in texts:
                    if not Path(t).is_file() or not any(
                        Path(t).is_relative_to(r) for r in p["read_roots"]
                    ):
                        raise ValueError(
                            f"referenced_files must be files under read_roots: {t}"
                        )
                label = identifier(turn["id"])
                if label in labels:
                    raise ValueError("duplicate turn id")
                labels.add(label)
                prompt, headings = instruments.prompt(
                    turn, instrument, persona=persona, first=index == 0, scope=scope
                )
                default_min = (
                    instrument["prompts"][turn["ask"]].get("min_bytes", 1)
                    if instrument and "ask" in turn and "prompt" not in turn
                    else 1
                )
                min_bytes = turn.get("min_bytes", default_min)
                if type(min_bytes) is not int or min_bytes < 1:
                    raise ValueError("min_bytes must be a positive integer")
                if not isinstance(headings, list) or any(
                    not isinstance(h, str) for h in headings
                ):
                    raise ValueError("headings must be strings")
                outputs = turn.get("outputs", [])
                if type(turn.get("allow_unchanged_outputs", False)) is not bool:
                    raise ValueError("allow_unchanged_outputs must be boolean")
                if not isinstance(outputs, list):
                    raise ValueError("outputs must be a list")
                for output in outputs:
                    if relative(output).parts[0] != "drafts":
                        raise ValueError("model-written outputs must be under drafts/")
                if outputs and p["tools"] != "write":
                    raise ValueError("declared output files require tools=write")
                frozen = temporary / "inputs" / name / f"turn-{index:03d}"
                items = artifacts.prepare(
                    turn.get("materials", []), plan_path.parent, frozen, material_cache
                )
                turns.append(
                    dict(
                        id=label,
                        prompt=prompt,
                        headings=headings,
                        min_bytes=min_bytes,
                        outputs=outputs,
                        referenced_images=referenced,
                        referenced_files=texts,
                        materials=items,
                        allow_unchanged_outputs=turn.get(
                            "allow_unchanged_outputs", False
                        ),
                        directory=str(frozen.relative_to(temporary)),
                        metadata=turn.get("metadata", {}),
                        prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                        prompt_source={
                            "kind": "literal" if "prompt" in turn else "instrument",
                            "ask": turn.get("ask") if "prompt" not in turn else None,
                            "addenda": turn.get("addenda", []),
                        },
                    )
                )
            library = artifacts.prepare(
                supplied.get("library", []),
                plan_path.parent,
                temporary / "inputs" / name / "library",
                material_cache,
            )
            accepted = supplied.get("accept_red_flags", [])
            if not isinstance(accepted, list) or set(accepted) - set(ACCEPTABLE_FLAGS):
                raise ValueError(
                    f"accept_red_flags may name only {', '.join(ACCEPTABLE_FLAGS)}"
                )
            for code in accepted:
                if not ACCEPTABLE_FLAGS[code][1](p):
                    raise ValueError(
                        f"a seat can accept the {code} flag only under the "
                        "policy that makes it harmless"
                    )
            if library and p["tools"] == "none":
                raise ValueError(
                    "a reference library needs file tools; use turn text for no-tools delivery"
                )
            parent = supplied.get("parent")
            if parent and (
                not isinstance(parent, dict) or set(parent) != {"seat", "after"}
            ):
                raise ValueError("parent fields: seat, after")
            if mode == "auto":
                harness.validate(config, p, turns, parent)
            seats.append(
                dict(
                    id=name,
                    config=config,
                    policy=p,
                    turns=turns,
                    library=library,
                    instrument=instrument,
                    persona=persona,
                    scope=scope,
                    exposure=exposure,
                    metadata=supplied.get("metadata", {}),
                    mode=mode,
                    tier=tier,
                    accept_red_flags=sorted(set(accepted)),
                    parent=parent,
                    runner_version=blind.runner_version(config["harness"])
                    if resolve and mode == "auto"
                    else None,
                )
            )
        sequence = order(seats)
        frozen = dict(
            schema=SCHEMA,
            id=str(uuid.uuid4()),
            title=raw.get("title", "Model run"),
            source=raw.get("source", {}),
            metadata=raw.get("metadata", {}),
            seats=seats,
            order=sequence,
            input_hashes=hashes(temporary / "inputs"),
            executor_hashes=freeze_executor(temporary),
            prepared_at=time.time(),
        )
        write(temporary / "plan.json", frozen)
        write(
            temporary / "plan-digest.json", {"sha256": digest(temporary / "plan.json")}
        )
        # Rename only into an absent path; do not merge with another preparation.
        if destination.exists():
            raise ValueError("destination was created by another operator")
        temporary.rename(destination)
        return dict(
            record=str(destination),
            seats=len(seats),
            input_bytes=sum(
                (destination / "inputs" / r).stat().st_size
                for r in frozen["input_hashes"]
            ),
        )
    except BaseException:
        # Only this transaction's newly created private staging tree is removed.
        shutil.rmtree(temporary)
        raise


class Run:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.plan = read(self.root / "plan.json")
        if self.plan["schema"] != SCHEMA:
            raise ValueError("unsupported run schema")
        self.seats = {s["id"]: s for s in self.plan["seats"]}

    def red_flags(self, name):
        """``(open, accepted)``: a seat's red flags, split by what its plan accepts.

        A record prepared before a plan could accept a flag declares none, so
        every flag it holds stays open.
        """
        accepted = set(self.seats[name].get("accept_red_flags", ()))
        flags = [
            flag
            for done in self.state(name)["done"].values()
            for flag in done["receipt"].get("red_flags", [])
        ]
        return (
            [f for f in flags if flag_code(f) not in accepted],
            [f for f in flags if flag_code(f) in accepted],
        )

    def verify(self):
        if (
            digest(self.root / "plan.json")
            != read(self.root / "plan-digest.json")["sha256"]
        ):
            raise RuntimeError("frozen plan changed")
        if hashes(self.root / "inputs") != self.plan["input_hashes"]:
            raise RuntimeError("frozen input changed")
        if hashes(self.root / "executor") != self.plan["executor_hashes"]:
            raise RuntimeError("frozen executor changed")
        for seat in self.seats:
            state = self.state(seat)
            for label, attempts in state.get("retired_attempts", {}).items():
                for attempt, retired in attempts.items():
                    record = (
                        self.root
                        / "records"
                        / seat
                        / label
                        / f"attempt-{int(attempt):03d}"
                    )
                    if hashes(record) != retired["record_hashes"]:
                        raise RuntimeError("retired attempt evidence changed")
            for label, done in state["done"].items():
                record = self.record(seat, label)
                if done["receipt"] != read(record / "receipt.json"):
                    raise RuntimeError("banked state and receipt disagree")
                if (
                    done.get("record_hashes")
                    and hashes(record) != done["record_hashes"]
                ):
                    raise RuntimeError(f"banked receipt changed: {seat}/{label}")
                if hashes(record / "answers") != done["answer_hashes"]:
                    raise RuntimeError(f"banked answer changed: {seat}/{label}")
        return True

    def state(self, seat):
        path = self.root / "state" / f"{seat}.json"
        return (
            read(path)
            if path.exists()
            else dict(done={}, session_id=None, pending=None, attempts={})
        )

    def save(self, seat, state):
        write(self.root / "state" / f"{seat}.json", state)

    def record(self, seat, turn):
        state = self.state(seat)
        attempt = (
            state["done"]
            .get(turn, {})
            .get("attempt", state.get("attempts", {}).get(turn, 0))
        )
        return self.root / "records" / seat / turn / f"attempt-{attempt:03d}"

    def workspace(self, seat):
        return self.root / "work" / seat / "seat"

    def private(self, seat):
        return (
            blind.state_root(self.workspace(seat))
            / self.seats[seat]["config"]["harness"]
        )

    def require_parent(self, seat):
        """Refuse once a child's parent home is gone, rather than misname it.

        The child is cloned from, and checked against, the parent's live
        home. A pruned one hashes as a home that changed, which would read
        as a parent that moved after the child branched from it.
        """
        name = self.seats[seat]["parent"]["seat"]
        blind.require_session(
            self.workspace(name),
            self.seats[name]["config"]["harness"],
            self.state(name)["session_id"],
        )

    def check_parent(self, seat):
        parent = self.seats[seat].get("parent")
        if not parent:
            return
        name = parent["seat"]
        snap = self.root / "checkpoints" / name
        state = self.state(name)
        if state.get("pending") or not state.get("complete"):
            raise RuntimeError("parent is not completed and idle")
        self.require_parent(seat)
        self.require_snapshot(name)
        expected = read(snap / "hashes.json")
        if hashes(self.workspace(name)) != read(
            snap / "workspace-hashes.json"
        ) or hashes(snap / "workspace") != read(snap / "workspace-hashes.json"):
            raise RuntimeError("parent workspace changed after checkpoint")
        if (
            hashes(self.private(name)) != expected
            or hashes(self.snapshot_home(name)) != expected
        ):
            raise RuntimeError("parent/checkpoint changed after branching")

    def snapshot_home(self, parent):
        """The copy of a parent's home its checkpoint keeps for its children.

        It lives in the cache with every other home, linked from the
        checkpoint, while the workspace copy and the hashes stay in the
        record. A checkpoint taken before homes moved keeps its copy in the
        record, and that copy is read where it is.
        """
        snap = self.root / "checkpoints" / parent
        if (snap / "home").is_dir():
            return snap / "home"
        harness = self.seats[parent]["config"]["harness"]
        return blind.state_root(snap / "home") / harness

    def require_snapshot(self, parent):
        """Refuse once the checkpoint's home copy is gone, rather than misname it.

        A pruned or cleared copy hashes as one that changed, which would read
        as a checkpoint tampered with after its children branched from it.
        """
        home = self.snapshot_home(parent)
        if not home.is_dir():
            entry = blind.state_root(self.root / "checkpoints" / parent / "home")
            raise blind.SessionGone(
                f"seat {parent}'s checkpoint, which its forked seats are cloned "
                f"from and checked against, has lost its home copy: "
                f"{blind.gone(entry, home)}. The run's records are intact"
            )

    def checkpoint(self, seat):
        parent = self.seats[seat]["parent"]["seat"]
        with lock(self.root / "locks" / f"checkpoint-{parent}.lock", wait=True):
            snap = self.root / "checkpoints" / parent
            if not snap.exists():
                snap.parent.mkdir(parents=True, exist_ok=True)
                temporary = Path(
                    tempfile.mkdtemp(prefix=".checkpoint-", dir=snap.parent)
                )
                try:
                    home_hashes = hashes(self.private(parent))
                    work_hashes = hashes(self.workspace(parent))
                    # The home's copy goes to the cache with every other home,
                    # linked from inside the checkpoint, so the rename that
                    # publishes the checkpoint publishes the link with it.
                    copy = blind.make_state_root(temporary / "home")
                    copy = copy / self.seats[parent]["config"]["harness"]
                    shutil.copytree(self.private(parent), copy)
                    shutil.copytree(self.workspace(parent), temporary / "workspace")
                    if (
                        hashes(self.private(parent)) != home_hashes
                        or hashes(copy) != home_hashes
                        or hashes(self.workspace(parent)) != work_hashes
                        or hashes(temporary / "workspace") != work_hashes
                    ):
                        raise RuntimeError("parent changed during checkpoint")
                    write(temporary / "hashes.json", home_hashes)
                    write(temporary / "workspace-hashes.json", work_hashes)
                    temporary.rename(snap)
                except BaseException:
                    blind.discard_state_root(temporary / "home")
                    shutil.rmtree(temporary)
                    raise
                blind.hold(snap / "home", self.checkpoint_held(parent))
            self.check_parent(seat)
            target = self.workspace(seat).parent
            if not target.exists():
                temporary = Path(tempfile.mkdtemp(prefix=".branch-", dir=target.parent))
                try:
                    # The child's home lives where every home does, linked
                    # from inside the clone, so the rename that publishes the
                    # clone publishes the link with it.
                    home = blind.make_state_root(temporary / "seat")
                    home = home / self.seats[seat]["config"]["harness"]
                    shutil.copytree(self.snapshot_home(parent), home)
                    shutil.copytree(snap / "workspace", temporary / "seat")
                    if hashes(home) != read(snap / "hashes.json") or hashes(
                        temporary / "seat"
                    ) != read(snap / "workspace-hashes.json"):
                        raise RuntimeError("incomplete child clone")
                    temporary.rename(target)
                except BaseException:
                    blind.discard_state_root(temporary / "seat")
                    shutil.rmtree(temporary)
                    raise
            elif not self.state(seat).get("session_id") and not self.state(seat).get(
                "pending"
            ):
                if hashes(self.private(seat)) != read(snap / "hashes.json") or hashes(
                    self.workspace(seat)
                ) != read(snap / "workspace-hashes.json"):
                    raise RuntimeError("unsubmitted child differs from its checkpoint")

    def prefix_length(self, seat):
        parent = self.seats[seat].get("parent")
        if not parent:
            return 0
        name = parent["seat"]
        return self.prefix_length(name) + len(self.seats[name]["turns"])

    def stage(self, seat, index):
        spec = self.seats[seat]
        turn = spec["turns"][index]
        work = self.workspace(seat)
        (work / "drafts").mkdir(parents=True, exist_ok=True)
        if spec["policy"].get("system_prompt") is not None:
            (work / "system-prompt.md").write_text(spec["policy"]["system_prompt"])
        directory = work / "materials" / f"turn-{self.prefix_length(seat) + index:03d}"
        # Previous delivered files remain available, but future material and
        # the operator plan never enter this filesystem. Inputs are read-only.
        content, images, files = artifacts.stage(
            turn["materials"],
            self.root / turn["directory"],
            directory,
            harness.VIRTUAL / "materials" / directory.name,
        )
        library_note = ""
        if spec["library"]:
            _, _, library_files = artifacts.stage(
                [dict(x, kind="file") for x in spec["library"]],
                self.root / "inputs" / seat / "library",
                work / "library" / f"set-{self.prefix_length(seat):03d}",
                harness.VIRTUAL / "library" / f"set-{self.prefix_length(seat):03d}",
            )
            library_note = (
                "\nOptional reference library (deliberately available now):\n"
                + "\n".join(str(v) for _, v in library_files)
            )
        prompt = turn["prompt"] + content + library_note
        if turn["outputs"]:
            prompt += "\nSave the requested contribution at:\n" + "\n".join(
                str(harness.VIRTUAL / relative(p)) for p in turn["outputs"]
            )
        return prompt, images, files

    def expected_images(self, seat, through):
        spec = self.seats[seat]
        values = []
        if spec.get("parent"):
            parent = spec["parent"]["seat"]
            values += self.expected_images(parent, len(self.seats[parent]["turns"]) - 1)
        for turn in spec["turns"][: through + 1]:
            values += [x["sha256"] for x in turn["materials"] if x["kind"] == "image"]
        return values

    def collect_turn(self, seat, index):
        spec = self.seats[seat]
        turn = spec["turns"][index]
        state = self.state(seat)
        pending = state["pending"]
        record = self.record(seat, turn["id"])
        if not pending or pending["turn"] != turn["id"]:
            raise RuntimeError("no matching pending turn to collect")
        if pending.get("pid") and process_identity(pending["pid"]) == pending.get(
            "process_identity"
        ):
            raise RuntimeError("submitted process is still alive; do not resubmit")
        # The pin travels in the pending record, so a turn collected later,
        # by another process, is checked against the version it was sent to.
        config = pinned_config(spec["config"], pending.get("model_pinned"))
        receipt = harness.receipt(
            config,
            spec["policy"],
            record,
            self.private(seat),
            state.get("served_model"),
        )
        if config.get("model_pinned"):
            receipt["model_pinned"] = config["model_pinned"]
        # A harness stopped after it answered exits nonzero by our hand, not
        # its own; the receipt says so, and the exit status is no fault.
        stopped = exits.recorded(record)
        if stopped:
            receipt["stopped_after_answer"] = stopped
            receipt.setdefault("notices", []).append(exits.notice(stopped))
        sid = receipt["session_id"]
        if pending["mode"] == "resume" and sid != state["session_id"]:
            raise RuntimeError("resume created a different session")
        if pending["mode"] == "fork":
            if sid == pending["parent_id"] or sid in {
                self.state(s).get("session_id") for s in self.seats
            }:
                raise RuntimeError("fork did not create a distinct session")
            if (
                spec["config"]["harness"] == "codex"
                and receipt["forked_from"] != pending["parent_id"]
            ):
                raise RuntimeError("fork names the wrong parent")
        answer = receipt.pop("answer")
        # A turn told to save its contribution in files is measured there: its
        # reply may be one line saying where. Without declared outputs, the
        # reply is the contribution.
        minimum = turn.get("min_bytes", 1)
        if not turn["outputs"] and len(answer.encode()) < minimum:
            raise RuntimeError("answer is shorter than the declared minimum")
        headings = {
            line.strip().replace("\u2019", "'")
            for line in answer.splitlines()
            if line.startswith("## ")
        }
        if any(h.replace("\u2019", "'") not in headings for h in turn["headings"]):
            raise RuntimeError("required answer headings absent")
        parent = spec.get("parent")
        if parent and any(
            done["receipt"].get("compaction_observed")
            for done in self.state(parent["seat"])["done"].values()
        ):
            receipt["compaction_observed"] = True
            receipt["compaction_inherited_from"] = parent["seat"]
            receipt["compaction_policy_satisfied"] = spec["policy"]["allow_compaction"]
            receipt.setdefault("red_flags", []).append(
                "Compaction in inherited history: supervisor must review the run design."
            )
        if receipt.get("compaction_observed"):
            receipt["history_images"] = None
            receipt["additional_tool_images"] = None
            receipt["image_history_assurance"] = "not certified after compaction"
        elif (
            spec["config"]["harness"] in ("codex", "claude", "agy")
            and not spec["policy"]["allow_compaction"]
        ):
            expected = Counter(self.expected_images(seat, index))
            observed = Counter(
                harness.history_image_hashes(receipt["rollout"], self.private(seat))
            )
            if expected - observed or (
                spec["policy"]["tools"] == "none" and expected != observed
            ):
                raise RuntimeError(
                    "native image history differs from declared delivery"
                )
            receipt["history_images"] = sum(expected.values())
            receipt["additional_tool_images"] = sum((observed - expected).values())
            # A seat that fetches its own material is handed no images, so
            # `history_images` is 0 by design and the only trace of a figure it
            # opened is `additional_tool_images`, which reads like a stray tool
            # call. A turn that declares how many its material references turns
            # that number into a check: a seat rating plates from their captions
            # alone would otherwise be indistinguishable from one that opened
            # every file.
            #
            # Views are not figures: a seat that opened one figure twice and
            # another never would match a count of views. So a declared list
            # is matched by bytes, each figure counting once it was seen, and
            # a bare count is compared with the distinct images opened.
            declared = turn.get("referenced_images")
            if declared:
                shown = observed - expected
                missing = []
                if isinstance(declared, list):
                    missing = [Path(p).name for p in declared if digest(p) not in shown]
                    total = len(declared)
                    opened = total - len(missing)
                else:
                    total, opened = declared, len(shown)
                receipt["referenced_images_opened"] = f"{opened}/{total}"
                if opened < total:
                    named = (
                        f" (never opened: {', '.join(missing[:5])}"
                        + (", ..." if len(missing) > 5 else "")
                        + ")"
                        if missing
                        else ""
                    )
                    receipt.setdefault("red_flags", []).append(
                        "Opened fewer images than the material references: "
                        f"{opened} of {total}{named}; visual verdicts "
                        "in this answer are not evidence."
                    )
        if spec["policy"]["read_root_digests"]:
            held = all(
                tree_digest(root) == expected
                for root, expected in spec["policy"]["read_root_digests"].items()
            )
            receipt["read_root_digests_held"] = held
            if not held:
                receipt.setdefault("red_flags", []).append(
                    "Mounted material changed during the turn; the answer may "
                    "describe bytes other than the ones the plan pinned."
                )
        if turn.get("referenced_files"):
            recorded = reads.events(receipt["rollout"])
            fully, in_order, unproven = reads.coverage(
                [(p, r) for kind, p, r in recorded if kind == "read"],
                turn["referenced_files"],
            )
            total = len(turn["referenced_files"])
            receipt["referenced_files_read"] = f"{fully}/{total}"
            receipt["referenced_files_in_order"] = in_order
            # Reading in order is not reading blind: a seat can open every
            # chapter in sequence and still open three before reacting to the
            # first. Where each file has a reaction of its own, the record
            # also says whether each was written before the next file opened.
            pairs = reads.reaction_pairs(
                turn["referenced_files"], turn.get("outputs", [])
            )
            if pairs:
                before = reads.reactions_before_next(
                    recorded, turn["referenced_files"], pairs
                )
                receipt["reactions_before_next"] = before
                if before is False:
                    receipt.setdefault("red_flags", []).append(
                        "A referenced file was opened before the reaction to the "
                        "file before it was written; reactions from that point "
                        "on may draw on later material."
                    )
                elif before is None:
                    receipt.setdefault("red_flags", []).append(
                        "Not proven that each reaction was written before the "
                        "next referenced file was opened. A write the record "
                        "cannot parse also lands here, so check the native "
                        "session before calling it read ahead."
                    )
                # The order check dates each reaction by its first write, so a
                # reaction rewritten after the seat read on passes it.
                unrevised = reads.reactions_unrevised(
                    recorded, turn["referenced_files"], pairs
                )
                receipt["reactions_unrevised"] = unrevised
                if unrevised is False:
                    receipt.setdefault("red_flags", []).append(
                        "A reaction was written again after the next referenced "
                        "file was opened, so it may record what the seat thought "
                        "after reading on rather than at that point."
                    )
            if unproven:
                receipt.setdefault("red_flags", []).append(
                    f"Not proven read in full: {fully} of {total} referenced "
                    f"files ({', '.join(unproven[:5])}"
                    + (", ..." if len(unproven) > 5 else "")
                    + "). A read the record cannot parse also lands here, so "
                    "check the native session before calling it skipped."
                )
            if in_order is False:
                receipt.setdefault("red_flags", []).append(
                    "Referenced files were not first opened in the declared order."
                )
            elif in_order is None:
                receipt.setdefault("red_flags", []).append(
                    "Not proven that referenced files were opened in the declared "
                    "order, because some were never seen opening. A read the "
                    "record cannot parse also lands here, so check the native "
                    "session before calling it out of order."
                )
        answers = record / "answers"
        answers.mkdir(exist_ok=True)
        target = answers / "answer.md"
        body = answer + "\n"
        if target.exists() and target.read_text() != body:
            raise RuntimeError("refusing to replace an earlier answer")
        target.write_text(body)
        output_transitions = {}
        for output in turn["outputs"]:
            src = self.workspace(seat) / relative(output)
            if src.is_symlink() or not src.is_file() or not src.stat().st_size:
                raise RuntimeError(f"declared output is missing/empty: {output}")
            if src.stat().st_size < minimum:
                raise RuntimeError(
                    f"declared output {output} is shorter than the declared minimum"
                )
            if digest(src) == pending.get("outputs_before", {}).get(
                output
            ) and not turn.get("allow_unchanged_outputs", False):
                raise RuntimeError(
                    f"declared output was not changed by this turn: {output}"
                )
            previous = pending.get("outputs_before", {}).get(output)
            output_transitions[output] = (
                "unchanged-permitted"
                if previous == digest(src)
                else "created"
                if previous is None
                else "modified"
            )
            dest = answers / relative(output)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and digest(dest) != digest(src):
                raise RuntimeError("refusing to replace collected output")
            shutil.copyfile(src, dest)
        # Native transcripts can grow on later turns; freeze each turn's copy.
        shutil.copyfile(receipt["rollout"], record / "native.jsonl")
        receipt["native_sha256"] = digest(record / "native.jsonl")
        receipt["outputs"] = output_transitions
        if (state.get("continuation") or {}).get("turn") == turn["id"]:
            # Banked, so the turn is no longer mid-continuation. The receipt
            # keeps the fact that it took one: an answer assembled across two
            # submissions is still a valid answer, but it is not the same
            # instrument as one delivered in a single unbroken turn, and the
            # write-up has to be able to say so.
            continued = state["continuation"]
            if continued["scope"] == OPERATOR_CONTINUATION:
                receipt["operator_continuation"] = continued["reason"]
                flag = (
                    "Turn continued by the operator after a stop the runner "
                    f"could not classify ({continued['reason']}); the answer "
                    "spans two submissions in one session."
                )
            elif continued["scope"] == AUTH_CONTINUATION:
                receipt["auth_continuation"] = (
                    state.get("auth_continuations") or {}
                ).get(turn["id"], 1)
                flag = (
                    "Turn continued after the host's Claude login was renewed, "
                    "which revoked the token the seat had been given; the answer "
                    "spans more than one submission in one session."
                )
            else:
                receipt["quota_continuation"] = continued["scope"]
                flag = (
                    "Turn continued after a provider quota limit; the answer "
                    "spans two submissions in one session."
                )
            receipt.setdefault("red_flags", []).append(flag)
            state.pop("continuation")
        write(record / "receipt.json", receipt)
        if turn["id"] in state["done"]:
            raise RuntimeError("duplicate banked turn")
        state["done"][turn["id"]] = dict(
            receipt=receipt,
            answer_hashes=hashes(answers),
            record_hashes=hashes(record),
            attempt=state.get("attempts", {}).get(turn["id"], 0),
        )
        state.update(session_id=sid, served_model=receipt["model"], pending=None)
        self.save(seat, state)
        self.check_parent(seat)
        if pending.get("exit_code") not in (None, 0) and not stopped:
            raise RuntimeError(
                "answer banked but process exited nonzero; inspect before continuing"
            )
        return receipt

    def recover_quota(self, seat, turn_id, detail):
        """Undo a refused turn so the session can simply be resumed."""
        state = self.state(seat)
        pending = state.get("pending") or {}
        if pending.get("turn") != turn_id:
            raise RuntimeError("quota recovery needs the pending turn")
        rewind_sessions(pending.get("resume_point"))
        retire_attempt(
            self,
            seat,
            turn_id,
            "refused for quota before the model ran; session rewound and "
            f"the turn is resubmittable ({detail.get('scope') or 'quota'})",
        )

    def continue_after_quota(self, seat, turn_id, detail):
        """Set an interrupted turn up to be continued in its own session.

        The opposite recovery to the one above, and the difference is what the
        provider did. Nothing was rewound, because there is real work in that
        session: the model read, reasoned, called tools and may have written
        files before the wall arrived. Replaying the turn would throw that
        away and pay for it twice, so the next submission carries the
        continuation notice instead of the turn's material and lands in the
        same session.

        The session id comes out of the event stream rather than a receipt,
        because a stopped turn never produced one. Without it there is nothing
        to resume and the operator is told so rather than handed a silent
        restart.
        """
        if (self.state(seat).get("pending") or {}).get("turn") != turn_id:
            raise RuntimeError("quota recovery needs the pending turn")
        if not detail.get("session_id"):
            raise RuntimeError(
                "a quota limit interrupted this turn after the model ran, but "
                "its event stream names no single session to continue; "
                "inspect the record rather than restarting the turn"
            )
        scope = detail.get("scope") or "quota"
        self.continue_in_session(
            seat,
            turn_id,
            detail["session_id"],
            dict(scope=scope),
            "interrupted by a provider quota limit after the model ran; the "
            f"session holds the partial work and is continued, not restarted "
            f"({scope})",
        )

    def recover_auth(self, seat, turn_id, detail):
        """Carry a turn past a Claude login renewed under it, or say why not.

        A seat borrows a copy of the host's access token, and the host renews
        its login near the token's expiry, revoking that copy (``claude_login``).
        The turn is recoverable exactly when the host now holds a different,
        usable token: nothing ran, and the identical turn goes again; or the
        model ran, and the turn is continued in its own session. When the host
        still holds the same token, the login is gone rather than renewed, and
        no retry can help until someone signs in. A small cap keeps a login
        that keeps failing from being retried in a loop. Every refusal leaves
        the turn pending with its session intact.
        """
        state = self.state(seat)
        pending = state.get("pending") or {}
        if pending.get("turn") != turn_id:
            raise RuntimeError("login recovery needs the pending turn")
        later = (
            f"then `quirework sop runs continue {self.root} {seat} {turn_id} "
            "--reason TEXT` and start again; the stopped turn keeps its session"
        )
        done = (state.get("auth_continuations") or {}).get(turn_id, 0)
        if done >= AUTH_CONTINUATION_CAP:
            raise RuntimeError(
                f"the seat's Claude login failed again after {done} automatic "
                "recoveries in this turn; check the host's login with `claude`, "
                + later
            )
        try:
            _, current = claude_login.host_login()
        except claude_login.LoginError as exc:
            raise RuntimeError(
                f"the seat stopped on a revoked or expired Claude login, and {exc}; "
                + later
            ) from None
        if current == pending.get("auth_fingerprint"):
            raise RuntimeError(
                "the seat stopped on a revoked or expired Claude login, and the "
                "host still holds the same token, so it was not renewed: sign in "
                "again with `claude` on the host, " + later
            )
        if detail.get("phase") == "refused":
            rewind_sessions(pending.get("resume_point"))
            retire_attempt(
                self,
                seat,
                turn_id,
                "refused by a revoked or expired Claude login before the model "
                "ran; session rewound and the turn resubmitted with the host's "
                "renewed login",
            )
        else:
            if not detail.get("session_id"):
                raise RuntimeError(
                    "a revoked Claude login interrupted this turn after the model "
                    "ran, but its event stream names no single session to "
                    "continue; inspect the record rather than restarting the turn"
                )
            self.continue_in_session(
                seat,
                turn_id,
                detail["session_id"],
                dict(scope=AUTH_CONTINUATION),
                "interrupted when the host's Claude login was renewed, revoking "
                "the token this attempt held; continued in its own session with "
                "the renewed login",
            )
        state = self.state(seat)
        state.setdefault("auth_continuations", {})[turn_id] = done + 1
        self.save(seat, state)

    def continue_in_session(self, seat, turn_id, session, cause, reason):
        """Retire the stopped attempt and make the next submission resume it.

        Shared by the two ways a turn comes to be continued: a quota limit the
        classifier recognised, and an operator who read a stop nothing
        classified and decided the session was worth keeping. `cause` is
        merged into the continuation record, which the receipt later reads to
        say how the answer came to span two submissions.
        """
        state = self.state(seat)
        pending = state.get("pending") or {}
        if state.get("session_id") and state["session_id"] != session:
            raise RuntimeError("interrupted turn names a different session")
        # A fork interrupted on its first turn has already branched, so the
        # recovered session is the branch and the continuation resumes it.
        # The distinctness check `collect_turn` would have run on that turn
        # therefore has to run here instead, or forking silently stops being
        # checked for exactly the turns that needed recovering.
        parent = self.seats[seat].get("parent")
        if parent and not state.get("session_id"):
            if session == self.state(parent["seat"])["session_id"]:
                raise RuntimeError("fork did not create a distinct session")
        state["session_id"] = session
        # Carried so the resumed submission measures its declared outputs
        # against where the TURN started, not against the half-written file
        # the interrupted attempt left behind.
        state["continuation"] = dict(
            turn=turn_id,
            session_id=session,
            **cause,
            outputs_before=pending.get("outputs_before", {}),
        )
        self.save(seat, state)
        retire_attempt(self, seat, turn_id, reason)

    def turn(self, seat, index, timeout, wait_quota=None):
        """Perform one turn, absorbing a quota refusal by waiting it out."""
        deadline = None if wait_quota is None else time.time() + wait_quota
        while True:
            try:
                return self.attempt_turn(seat, index, timeout)
            except AuthStopped as stopped:
                # No wait: either the host already holds a renewed login and
                # the turn goes on at once, or recovery refuses and says why.
                turn_id = self.seats[seat]["turns"][index]["id"]
                self.recover_auth(seat, turn_id, stopped.detail)
            except QuotaPaused as paused:
                turn_id = self.seats[seat]["turns"][index]["id"]
                if paused.detail.get("phase") == "interrupted":
                    self.continue_after_quota(seat, turn_id, paused.detail)
                else:
                    self.recover_quota(seat, turn_id, paused.detail)
                delay = quota_delay(paused.detail)
                # Recovery already left the seat resumable, so giving up here
                # costs nothing but time: the next `run` continues the session.
                if deadline is None or time.time() + delay > deadline:
                    raise
                time.sleep(delay)

    def attempt_turn(self, seat, index, timeout):
        self.verify()
        spec = self.seats[seat]
        turn = spec["turns"][index]
        state = self.state(seat)
        if turn["id"] in state["done"]:
            return
        if state["pending"]:
            if state["pending"]["turn"] != turn["id"]:
                raise RuntimeError("an unresolved turn blocks further reveals")
            record = self.record(seat, turn["id"])
            stop = harness.quota_stop(spec["config"]["harness"], record)
            if stop:
                # A record stalled by an earlier limit: recover it and re-offer
                # the same turn instead of refusing every later reveal forever.
                # Which recovery depends on whether the model ran, and `turn`
                # reads the phase to decide.
                raise QuotaPaused(stop)
            stop = harness.auth_stop(spec["config"]["harness"], record)
            if stop:
                # Stalled by a login that was renewed under it, perhaps while
                # nobody was running the seat; `turn` checks the host now.
                raise AuthStopped(stop)
            if state["pending"].get("phase") == "not-submitted" or (
                state["pending"].get("phase") == "launch-intent"
                and not (record / "launch.json").exists()
                and not (record / "events.jsonl").exists()
            ):
                retire_attempt(
                    self, seat, turn["id"], "proved unsubmitted: launch record absent"
                )
                state = self.state(seat)
        if state["pending"]:
            if state["pending"]["turn"] != turn["id"]:
                raise RuntimeError("an unresolved turn blocks further reveals")
            return self.collect_turn(seat, index)
        # A resume, and a fork's first turn, continue a session in the seat's
        # private home. Checked before staging writes the turn's drafts, so
        # a home that is gone leaves the seat exactly as it was.
        if state["session_id"] or spec.get("parent"):
            blind.require_session(
                self.workspace(seat),
                spec["config"]["harness"],
                state["session_id"] or self.state(spec["parent"]["seat"])["session_id"],
            )
        # A turn the quota cut in half is continued, never re-delivered. The
        # material is already in the session and re-attaching it would both
        # show the reader the same pages twice and break the image-history
        # receipt, which counts what the session was actually shown.
        continuation = state.get("continuation") or {}
        resuming = continuation.get("turn") == turn["id"]
        if resuming:
            scope = continuation.get("scope")
            prompt = quota.continuation(
                cause="error"
                if scope == OPERATOR_CONTINUATION
                else "auth"
                if scope == AUTH_CONTINUATION
                else "quota"
            )
            images, files = [], []
        else:
            prompt, images, files = self.stage(seat, index)
        mode = (
            "resume"
            if state["session_id"]
            else "fork"
            if spec.get("parent")
            else "start"
        )
        sid = state["session_id"] or (
            self.state(spec["parent"]["seat"])["session_id"]
            if mode == "fork"
            else str(uuid.uuid4())
        )
        # Mounted material is the one input the record did not freeze, so
        # check it is still the material the plan pinned before anything
        # is submitted. A changed mount is refused rather than read: the
        # seat would be reading a book nobody declared.
        changed = [
            root
            for root, expected in spec["policy"]["read_root_digests"].items()
            if tree_digest(root) != expected
        ]
        if changed:
            raise RuntimeError(
                "mounted material changed since the plan pinned it: "
                + ", ".join(changed)
            )
        # Proven after the free check above, which can refuse the turn for
        # nothing, and before anything about the turn is saved, so a seat
        # that cannot be shown blind leaves no launch intent behind.
        proven = harness.canary(spec["config"], spec["policy"], f"run seat {seat}")
        # A runner such as agy reaches its keyring over a filtered session
        # bus. It is opened before the jail is built, because the argv names
        # its socket, and closed once the process has exited.
        with blind.session_bus([spec["config"]["harness"]]) as bus:
            config = pinned_config(
                spec["config"],
                state.get("served_model")
                if mode == "resume"
                else self.state(spec["parent"]["seat"]).get("served_model")
                if mode == "fork"
                else None,
            )
            args, payload, rendered = harness.command(
                config, spec["policy"], mode, sid, prompt, images, files
            )
            jail, env, _ = harness.launch_context(
                spec["config"], spec["policy"], self.workspace(seat), bus, proven
            )
            blind.hold(
                self.workspace(seat),
                f"run {self.root.name}: seat {seat} continues this session "
                "until it completes",
            )
            record = self.record(seat, turn["id"])
            record.mkdir(parents=True, exist_ok=True)
            if (record / "events.jsonl").exists():
                raise RuntimeError(
                    "unbanked event file exists; reconcile it before submission"
                )
            (record / "prompt.txt").write_text(rendered)
            request_sha = hashlib.sha256(
                payload + json.dumps(args).encode()
            ).hexdigest()
            state["pending"] = dict(
                turn=turn["id"],
                mode=mode,
                parent_id=sid,
                model_pinned=config.get("model_pinned"),
                phase="launch-intent",
                request_sha256=request_sha,
                created_at=time.time(),
                # Where the native session stood before this submission. A refused
                # turn still appends its user message (and the refusal itself) to
                # the session, so resuming without rewinding would show the reader
                # a duplicate reveal and a quota notice in its own voice. Captured
                # for every turn because the refusal is only visible afterwards.
                resume_point=session_marks(
                    harness.session_paths(
                        spec["config"]["harness"], self.private(seat), sid
                    )
                ),
                quota_continuation=continuation.get("scope") if resuming else None,
                # Which borrowed login this submission carried, as a fingerprint
                # and never the token: after a 401 it tells a renewed host login
                # from one that is gone (``recover_auth``).
                auth_fingerprint=harness.token_fingerprint(env),
                outputs_before=continuation["outputs_before"]
                if resuming
                else {
                    output: digest(self.workspace(seat) / relative(output))
                    for output in turn["outputs"]
                    if (self.workspace(seat) / relative(output)).is_file()
                },
            )
            self.save(seat, state)
            write(
                record / "launch.json",
                dict(
                    argv=args,
                    delivery=turn["materials"],
                    filesystem=harness.CAPABILITIES[spec["config"]["harness"]][
                        "filesystem"
                    ],
                    read_roots=spec["policy"]["read_roots"],
                    read_root_digests=spec["policy"]["read_root_digests"],
                    billing=harness.billing(spec["config"]["harness"]),
                    session_bus=bus.receipt() if bus else None,
                    canary=proven,
                ),
            )
            usage_records.seat("run", spec["config"])
            with (
                (record / "events.jsonl").open("wb") as out,
                (record / "stderr.log").open("wb") as err,
            ):
                try:
                    proc = blind.popen(
                        jail + args,
                        bus,
                        stdin=subprocess.PIPE,
                        stdout=out,
                        stderr=err,
                        env=env,
                        cwd=self.root,
                        start_new_session=True,
                    )
                except OSError:
                    state["pending"]["phase"] = "not-submitted"
                    self.save(seat, state)
                    raise
                state["pending"].update(
                    phase="process-started",
                    pid=proc.pid,
                    process_identity=process_identity(proc.pid),
                )
                self.save(seat, state)
                name = spec["config"]["harness"]
                why = exits.wait(
                    proc,
                    payload,
                    harness=name,
                    events=record / "events.jsonl",
                    timeout=timeout,
                )
                if why:
                    exits.stop(proc)
                if why == exits.ANSWERED:
                    # Written before the receipt is read, so the record that
                    # banks the answer says the exit was forced and why.
                    exits.record(
                        record,
                        harness=name,
                        grace=exits.EXIT_GRACE,
                        exit_code=proc.returncode,
                    )
                state["pending"].update(
                    phase="receipt-pending", exit_code=proc.returncode
                )
                self.save(seat, state)
        stop = harness.quota_stop(spec["config"]["harness"], record)
        if stop:
            raise QuotaPaused(stop)
        stop = harness.auth_stop(spec["config"]["harness"], record)
        if stop:
            raise AuthStopped(stop)
        # A client can exit before its final receipt flush. Re-read existing
        # evidence, never issue a second first impression.
        for attempt in range(10):
            try:
                return self.collect_turn(seat, index)
            except (RuntimeError, json.JSONDecodeError) as exc:
                current = self.state(seat)
                if current.get("pending"):
                    current["pending"]["last_error"] = str(exc)
                    self.save(seat, current)
                if attempt == 9 or self.state(seat)["pending"] is None:
                    raise
                time.sleep(1)

    def run_seat(self, name, timeout=None, through=None, wait_quota=None):
        with (
            lock(self.root / "locks" / f"{name}.lock"),
            slot(self.seats[name]["config"]["harness"]),
        ):
            spec = self.seats[name]
            if spec["mode"] == "external":
                return "awaiting-external"
            if (
                spec.get("runner_version")
                and blind.runner_version(spec["config"]["harness"])
                != spec["runner_version"]
            ):
                raise RuntimeError("installed harness changed since preparation")
            if spec.get("parent"):
                self.check_parent_ready(name)
                self.require_parent(name)
                self.checkpoint(name)
            for index, turn in enumerate(spec["turns"]):
                self.turn(name, index, timeout, wait_quota=wait_quota)
                if through == turn["id"]:
                    return "paused"
            state = self.state(name)
            state["complete"] = True
            self.save(name, state)
            self.settle_homes(name)
            if self.red_flags(name)[0]:
                return "completed-with-red-flags"
            return "completed"

    def settle_homes(self, name):
        """Release what a seat that just completed no longer needs held.

        Its own home, unless forked children are still checked against it;
        then it stays held for them and says so. And its parent's, once the
        last of the parent's children has completed.
        """
        parent = (self.seats[name].get("parent") or {}).get("seat")
        for seat in (name, parent):
            if not seat or not self.state(seat).get("complete"):
                continue
            waiting = [
                child
                for child, spec in self.seats.items()
                if (spec.get("parent") or {}).get("seat") == seat
                and not self.state(child).get("complete")
            ]
            checkpoint = self.root / "checkpoints" / seat / "home"
            if waiting:
                blind.hold(
                    self.workspace(seat),
                    f"run {self.root.name}: forked seats {', '.join(waiting)} "
                    f"are checked against seat {seat} until they complete",
                )
                blind.hold(checkpoint, self.checkpoint_held(seat))
            else:
                blind.release(self.workspace(seat))
                blind.release(checkpoint)

    def checkpoint_held(self, parent):
        return (
            f"run {self.root.name}: forked seats are cloned from and checked "
            f"against seat {parent}'s checkpoint until they complete"
        )

    def check_parent_ready(self, name):
        parent = self.seats[name]["parent"]["seat"]
        state = self.state(parent)
        if not state.get("complete") or state["pending"]:
            raise RuntimeError("parent has not completed; child was not submitted")

    def run(self, selected=None, jobs=1, timeout=None, through=None, wait_quota=None):
        if jobs < 1 or (timeout is not None and timeout <= 0):
            raise ValueError("jobs and timeout must be positive")
        names = selected or self.plan["order"]
        if set(names) - set(self.seats):
            raise ValueError("unknown selected seat")
        if through and (
            len(names) != 1
            or through not in {t["id"] for t in self.seats[names[0]]["turns"]}
        ):
            raise ValueError("--through must name a turn of one selected seat")
        results = {}
        # A seat is what an operator holds, never the whole record. Each
        # seat's own lock keeps a second operator off it, so a later `start`
        # can launch further seats while an earlier one still runs: the
        # trial seat first, then the rest once it proves the assignment,
        # without splitting one run across two records.
        remaining = list(names)
        while remaining:
            ready = [
                n
                for n in remaining
                if not self.seats[n].get("parent")
                or self.state(self.seats[n]["parent"]["seat"]).get("complete")
            ]
            if not ready:
                for n in remaining:
                    results[n] = "blocked by incomplete parent"
                break

            def execute(n):
                try:
                    return self.run_seat(n, timeout, through, wait_quota)
                except Held:
                    return HELD
                except (
                    ValueError,
                    RuntimeError,
                    OSError,
                    subprocess.SubprocessError,
                ) as exc:
                    # A pending turn is kept, never discarded: say so, and
                    # name the two decisions left to the operator.
                    kept = (self.state(n).get("pending") or {}).get("turn")
                    if not kept:
                        return "stopped: " + str(exc)
                    return (
                        f"stopped: {exc}; turn {kept!r} is kept with its "
                        "session. Read its record, then `continue` it or "
                        "prepare a distinct new run"
                    )

            # `submit` carries the caller's project context into each
            # worker. A bare pool.map would let a worker re-resolve the
            # project from disk, and under the frozen executor that walk
            # starts from the record rather than the project: the seat
            # would then read another catalog and another account lease.
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                futures = [submit(pool, execute, n) for n in ready]
                for name, future in zip(ready, futures):
                    results[name] = future.result()
            remaining = [n for n in remaining if n not in ready]
        return results

    def status(self, name):
        """Where a seat stands, in the words `inspect` and an export use."""
        spec, state = self.seats[name], self.state(name)
        banked = f"{len(state['done'])} of {len(spec['turns'])} turns banked"
        pending = state.get("pending")
        if state.get("complete"):
            return "complete"
        if pending:
            alive = pending.get("pid") and process_identity(
                pending["pid"]
            ) == pending.get("process_identity")
            if alive:
                return f"running turn {pending['turn']}"
            return f"turn {pending['turn']} pending with no process; {banked}"
        if spec["mode"] == "external":
            return f"awaiting external answers; {banked}"
        if state["done"] or state.get("attempts") or state.get("session_id"):
            return banked
        return "not started"

    def not_started(self):
        """The seats a bare `start` would launch for the first time.

        Named wherever the record is read, because a record that holds
        finished seats beside seats that never ran looks finished, and a
        later bare `start` would pay for every one of them.
        """
        return [
            name
            for name in self.plan["order"]
            if self.seats[name]["mode"] != "external"
            and self.status(name) == "not started"
        ]

    def inspect(self, *, prompts=False):
        return dict(
            id=self.plan["id"],
            title=self.plan["title"],
            not_started=self.not_started(),
            seats={
                name: dict(
                    status=self.status(name),
                    mode=s["mode"],
                    model=s["config"]["model"],
                    requested_model=s["config"].get("requested_model"),
                    persona=personas.identity(s.get("persona")),
                    instrument={k: s["instrument"].get(k) for k in ("id", "revision")}
                    if s.get("instrument")
                    else None,
                    scope=s.get("scope"),
                    exposure=s.get("exposure"),
                    **(
                        {
                            "prompts": [
                                {"id": t["id"], "prompt": t["prompt"]}
                                for t in s["turns"]
                            ]
                        }
                        if prompts
                        else {}
                    ),
                    policy=s["policy"],
                    completed_turns=len(self.state(name)["done"]),
                    declared_turns=len(s["turns"]),
                    complete=self.state(name).get("complete", False),
                    pending=self.state(name)["pending"],
                    red_flags=self.red_flags(name)[0],
                    accepted_red_flags=self.red_flags(name)[1],
                )
                for name, s in self.seats.items()
            },
        )

    def export(self, destination, partial=False, flat=False):
        """Copy answers and a curated receipt out of the record.

        The default keeps the record's shape, one directory per seat and
        turn. ``flat`` writes one seat's files straight into
        ``destination``, the shape an evidence directory usually wants: each
        declared output under its own name, each other turn's answer as
        ``NN-<turn>.md`` in turn order, and a receipt for that seat alone. A
        turn's closing chat message is not one of its outputs, so a flat
        export of a turn that declares outputs leaves it in the full export.
        """
        self.verify()
        destination = Path(destination)
        if destination.exists():
            raise ValueError("export destination exists")
        if flat and len(self.seats) != 1:
            raise ValueError(
                "a flat export takes a one-seat record: export the whole record "
                "or give each seat its own"
            )
        for name, spec in self.seats.items():
            state = self.state(name)
            # Seats are independent by design, so one seat stalled on a quota
            # limit must not withhold the evidence of every seat that
            # finished. `partial` says so explicitly and the receipt records
            # each seat's completeness, so nothing is passed off as a whole
            # read that is not one.
            if not state.get("complete") and not partial:
                raise RuntimeError(
                    "cannot export an incomplete run as completed evidence; "
                    "pass partial to release the seats that did finish"
                )
        # Resolved before anything is written, so a collision leaves no
        # half-made export behind.
        flat_files = (
            self._flat_files(next(iter(self.seats)), self.state(next(iter(self.seats))))
            if flat
            else {}
        )
        destination.mkdir(parents=True)
        public = dict(
            id=self.plan["id"],
            title=self.plan["title"],
            source=self.plan["source"],
            metadata=self.plan.get("metadata", {}),
            # A partial export says which declared seats never ran, so the
            # finished ones are not read as the whole panel.
            not_started=self.not_started(),
            seats={},
        )
        for name in self.seats:
            spec = self.seats[name]
            public["seats"][name] = {
                "_assignment": {
                    k: spec.get(k)
                    for k in ("persona", "instrument", "scope", "exposure", "metadata")
                }
            }
            public["seats"][name]["_assignment"]["turns"] = [
                {
                    "id": t["id"],
                    "prompt": t["prompt"],
                    "prompt_sha256": hashlib.sha256(t["prompt"].encode()).hexdigest(),
                    "prompt_source": t.get("prompt_source"),
                    "metadata": t.get("metadata", {}),
                }
                for t in spec["turns"]
            ]
            state = self.state(name)
            # State an exported seat's completeness in the receipt itself, so
            # a partial read can never be read later as a whole one.
            public["seats"][name]["_completeness"] = dict(
                status=self.status(name),
                declared_turns=len(self.seats[name]["turns"]),
                answered_turns=len(state["done"]),
                complete=bool(state.get("complete")),
                pending=(state.get("pending") or {}).get("turn"),
            )
            for target, src in flat_files.items():
                shutil.copyfile(src, destination / target)
            for label, done in state["done"].items():
                if not flat:
                    src = self.record(name, label) / "answers"
                    dest = destination / name / label
                    shutil.copytree(src, dest)
                receipt = done["receipt"]
                public["seats"][name][label] = {
                    k: v for k, v in receipt.items() if k not in ("rollout", "notices")
                }
        if flat:
            (name,) = public["seats"]
            public = {k: v for k, v in public.items() if k != "seats"} | {
                "seat": name,
                "turns": public["seats"][name],
            }
        write(destination / "receipt.json", public)
        return str(destination)

    def _flat_files(self, name, state):
        """{file name in the flat export: its source}, refusing a collision
        before anything is copied, so a partial flat export cannot happen."""
        files = {}
        for index, turn in enumerate(self.seats[name]["turns"], start=1):
            if turn["id"] not in state["done"]:
                continue
            answers = self.record(name, turn["id"]) / "answers"
            if turn["outputs"]:
                pairs = [(Path(o).name, answers / relative(o)) for o in turn["outputs"]]
            else:
                pairs = [(f"{index:02d}-{turn['id']}.md", answers / "answer.md")]
            for target, src in pairs:
                if target in files or target == "receipt.json":
                    raise ValueError(
                        f"a flat export would write {target} twice; export the "
                        "record's full shape instead"
                    )
                files[target] = src
        return files


def pinned_config(config, served):
    """``config`` with its model pinned to ``served`` when that adds anything.

    A Claude seat stores its family alias, because Claude Code has no local
    catalog to resolve it against, and a resume that named the alias would
    run whatever is newest now. A resumed or forked session therefore names
    the version its history was served on. Codex and Antigravity seats run
    a frozen exact id already, so a pin there would claim nothing new.
    """
    if not served or served == config["model"]:
        return config
    return {**config, "model_pinned": served}


def inside_executor(root):
    """Whether this process is the record's own frozen executor."""
    executor = Path(root).expanduser().resolve() / "executor"
    return Path(__file__).resolve().is_relative_to(executor)


def frozen_dispatch(root, args):
    # The bootstrap must not interpret mutable run-state semantics. That job
    # belongs to the preserved executor, including when its schema predates us.
    root = Path(root).expanduser().resolve()
    plan = read(root / "plan.json")
    if digest(root / "plan.json") != read(root / "plan-digest.json")["sha256"]:
        raise RuntimeError("frozen plan changed")
    executor = root / "executor"
    if hashes(executor) != plan["executor_hashes"]:
        raise RuntimeError("frozen executor changed")
    if inside_executor(root):
        return None
    env = os.environ.copy()
    env["PYTHONPATH"] = str(executor / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if not (executor / "src/quirework/runs/__main__.py").is_file():
        raise RuntimeError("frozen executor has no supported entry point")
    env.pop("QUIREWORK_PROJECT", None)
    return subprocess.run(
        [sys.executable, "-m", "quirework.runs", *args], cwd=executor, env=env
    ).returncode


def retire_attempt(run, seat, turn, reason):
    state = run.state(seat)
    record = run.record(seat, turn)
    record.mkdir(parents=True, exist_ok=True)
    write(record / "retirement.json", dict(reason=reason, retired_at=time.time()))
    attempt = state.get("attempts", {}).get(turn, 0)
    state.setdefault("retired_attempts", {}).setdefault(turn, {})[str(attempt)] = dict(
        reason=reason, record_hashes=hashes(record)
    )
    state.setdefault("attempts", {})[turn] = attempt + 1
    state["pending"] = None
    run.save(seat, state)


class QuotaPaused(RuntimeError):
    """A provider account limit stopped a turn, in one of its two shapes.

    Distinct from every other failure on purpose. A transport fault leaves it
    unknown whether the request was served, so it may never be resubmitted. A
    quota stop says which happened, and `detail["phase"]` carries it: a
    *refused* turn produced nothing and can be rewound and sent again, while
    an *interrupted* one banked real work in its session and is continued
    there. Either way a long read survives a limit without losing the session,
    which is the point.
    """

    def __init__(self, detail):
        super().__init__(detail.get("detail") or "refused for quota")
        self.detail = detail


class AuthStopped(RuntimeError):
    """A Claude seat's borrowed login was revoked or expired mid-turn.

    Neither a quota stop, which is waited out, nor an unknown one, which is
    left to the operator: the host renews its login on its own, so the turn
    is recoverable as soon as a newer token exists, and `Run.recover_auth`
    decides whether one does. ``detail["phase"]`` reads as for a quota stop.
    """

    def __init__(self, detail):
        super().__init__(detail.get("detail") or "Claude login revoked or expired")
        self.detail = detail


def quota_delay(detail, now=None):
    """Seconds to wait before resubmitting, from the provider's own reset."""
    now = time.time() if now is None else now
    resets = detail.get("resets_at")
    if isinstance(resets, (int, float)) and resets > now:
        # Two minutes past the stated reset: the provider's clock need not
        # agree with this one, and a resubmission that lands early is only
        # refused again.
        return min(resets - now + 120, 6 * 3600)
    return 300


def retry(run, seat, turn):
    """Retry only a terminal rejection that demonstrably produced no response."""
    state = run.state(seat)
    pending = state["pending"]
    if not pending or pending["turn"] != turn:
        raise ValueError(
            "retry needs the pending turn; completed answers are immutable"
        )
    if pending.get("pid") and process_identity(pending["pid"]) == pending.get(
        "process_identity"
    ):
        raise RuntimeError("submitted process is alive")
    record = run.record(seat, turn)
    if not harness.rejected_without_answer(
        run.seats[seat]["config"]["harness"], record
    ):
        stop = harness.quota_stop(run.seats[seat]["config"]["harness"], record)
        if stop and stop["phase"] == "interrupted":
            raise RuntimeError(
                "a quota limit interrupted this turn AFTER the model ran; it is "
                "continued in its own session by `run`, not retried -- retrying "
                "would discard the reading and reasoning the session still holds"
            )
        raise RuntimeError(
            "submission/answer is unresolved or invalid; investigate it, then "
            "`continue` it in its own session or prepare a distinct new run"
        )
    retire_attempt(run, seat, turn, "terminal rejection without an answer")
    return dict(
        seat=seat,
        turn=turn,
        next_attempt=run.state(seat)["attempts"][turn],
        submitted=False,
    )


def continue_turn(run, seat, turn, reason):
    """Continue a stopped turn in its own session, on the operator's word.

    A turn can stop for many reasons a classifier will never enumerate: a
    dropped connection, an API error, a limit worded in a way nobody has seen
    yet. Every one of them leaves the turn pending with its session intact,
    and nearly all are recovered by telling the agent there was an error and
    to resume. Whether to do that is the operator's call, made after reading
    the record, so the runner never decides it and never forecloses it: this
    is the act that records the decision, and the next `start` carries it out.

    The session is read from the stopped attempt's own event stream, so it
    must name exactly one. A stop the quota classifier already recognises is
    left to `start`, which recovers it the provider's way.
    """
    if not reason or not reason.strip():
        raise ValueError("say why the turn is continued: --reason TEXT")
    state = run.state(seat)
    pending = state["pending"]
    if not pending or pending["turn"] != turn:
        raise ValueError(
            "continue needs the pending turn; completed answers are immutable"
        )
    if pending.get("pid") and process_identity(pending["pid"]) == pending.get(
        "process_identity"
    ):
        raise RuntimeError("submitted process is alive")
    name = run.seats[seat]["config"]["harness"]
    record = run.record(seat, turn)
    if harness.quota_stop(name, record):
        raise RuntimeError(
            "this stop is a provider quota limit the runner recognises; "
            "`start` recovers it, rewinding or continuing as the provider's "
            "own record requires"
        )
    session = quota.session_id(name, quota.rows(record / "events.jsonl"))
    if not session:
        raise RuntimeError(
            "the stopped attempt's event stream names no single session to "
            "continue; a distinct new run is the remaining recovery"
        )
    reason = reason.strip()
    run.continue_in_session(
        seat,
        turn,
        session,
        dict(scope=OPERATOR_CONTINUATION, reason=reason),
        f"stopped without a classified cause; the operator continued it in "
        f"its own session: {reason}",
    )
    return dict(
        seat=seat,
        turn=turn,
        session_id=session,
        next_attempt=run.state(seat)["attempts"][turn],
        continued=True,
    )


def import_answer(run, seat, turn, answer_path, provenance):
    spec = run.seats[seat]
    if spec["mode"] != "external":
        raise ValueError("manual imports require mode=external at preparation")
    if set(provenance) != {"session_id", "model", "source"} or not all(
        isinstance(v, str) and v.strip() for v in provenance.values()
    ):
        raise ValueError("provenance requires session_id, model and source text")
    state = run.state(seat)
    next_turn = next((t for t in spec["turns"] if t["id"] not in state["done"]), None)
    if not next_turn or next_turn["id"] != turn:
        raise ValueError(
            "import must be the next undeclared answer; never replace an earlier one"
        )
    if state.get("session_id") and state["session_id"] != provenance["session_id"]:
        raise ValueError("manual continuation changed session identity")
    record = run.record(seat, turn)
    answers = record / "answers"
    answers.mkdir(parents=True)
    source = Path(answer_path)
    if source.is_symlink() or not source.is_file() or not source.stat().st_size:
        raise ValueError("external answer must be a nonempty regular file")
    shutil.copyfile(source, answers / "answer.md")
    receipt = dict(
        **provenance,
        transport="external import",
        verification="human attestation; native transport unverified",
    )
    write(record / "receipt.json", receipt)
    state["done"][turn] = dict(
        receipt=receipt,
        answer_hashes=hashes(answers),
        record_hashes=hashes(record),
        attempt=0,
    )
    state.update(
        session_id=provenance["session_id"],
        complete=len(state["done"]) == len(spec["turns"]),
    )
    run.save(seat, state)
    if state["complete"]:
        run.settle_homes(seat)
    return receipt
