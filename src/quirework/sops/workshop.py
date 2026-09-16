"""Persistent prose experiments. Procedure: docs/workflows.md."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path

from quirework.providers import blind
from quirework.sops import personas
from quirework.providers import quota
from quirework.providers import models as drivers
from quirework.project import current_project, submit

SCHEMA = 1
# `quota` owns the tier vocabulary and the order it implies, because the
# reason for the order is the provider's wall and that module owns the wall.
TIERS = quota.TIERS

# The author's default seats, owned as data so a plan can omit `writers` and
# the SOP does not carry a second copy of the list to rot.


def _settings():
    """Resolve paths from the current operation, without import-time state."""
    from types import SimpleNamespace

    DEFAULT_ROOT = current_project().state_root
    ROSTER = current_project().path("sops.roster", "sops/prose-workshop/roster.json")
    return SimpleNamespace(DEFAULT_ROOT=DEFAULT_ROOT, ROSTER=ROSTER)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
        temporary = f.name
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def safe_rel(value):
    p = Path(value)
    if p.is_absolute() or not value or ".." in p.parts or p == Path("."):
        raise ValueError(f"expected a relative path without traversal: {value!r}")
    return p


def identifier(value):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", value):
        raise ValueError(f"invalid experiment/writer identifier: {value!r}")
    return value


def model_directory_name(harness, model):
    """Readable model identity, with the harness named once."""
    model = model.lower()
    stem = model if model.startswith(harness + "-") else f"{harness}-{model}"
    stem = re.sub(r"[^a-z0-9._-]+", "-", stem).strip("-._")
    return identifier(stem)


def tree_hashes(root):
    root = Path(root)
    if root.is_symlink():
        raise ValueError(f"symlink at tree root: {root}")
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            raise ValueError(f"symlink in frozen material: {p}")
        if p.is_file():
            out[str(p.relative_to(root))] = digest(p)
        elif not p.is_dir():
            raise ValueError(f"non-regular file: {p}")
    return out


def process_identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except FileNotFoundError:
        return None


@contextmanager
def lock(path):
    with Path(path).open("a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"another operator holds {path}") from exc
        yield


@contextmanager
def harness_slot(harness):
    from quirework.runs.storage import slot

    with slot(harness):
        yield


def _git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def prepare(plan_path, destination, *, resolve_models=True):
    """Plan: assignment, brief, files, required_reads, outputs, writers.

    File entries have path (inside input/) and source (inside source_root).
    A source_revision freezes Git blobs; omitted uses the actual working file.
    local_source instead names an explicit file relative to the plan directory.
    Outputs are paths under drafts/, with optional min_bytes. Media are opaque
    bytes: the assignment and chosen harness determine how to use them.
    """
    plan_path, destination = Path(plan_path).resolve(), Path(destination).absolute()
    plan = read_json(plan_path)
    identifier(destination.name)
    if destination.exists():
        raise ValueError(f"experiment already exists: {destination}")
    if not plan.get("assignment", "").strip() or not plan.get("brief", "").strip():
        raise ValueError("assignment and brief must both contain text")
    repo = Path(plan.get("source_root", current_project().root)).resolve()
    revision = plan.get("source_revision")
    if revision:
        revision = (
            _git(repo, "rev-parse", "--verify", revision + "^{commit}").decode().strip()
        )
    outputs = []
    for entry in plan.get("outputs", []):
        item = {"path": entry} if isinstance(entry, str) else dict(entry)
        rel = safe_rel(item["path"])
        if rel.parts[0] != "drafts" or len(rel.parts) < 2:
            raise ValueError("outputs must be files below drafts/")
        item.setdefault("min_bytes", 100)
        if not isinstance(item["min_bytes"], int) or item["min_bytes"] < 1:
            raise ValueError("output min_bytes must be a positive integer")
        outputs.append(item)
    if not outputs or len({i["path"] for i in outputs}) != len(outputs):
        raise ValueError("declare unique output files")
    roster_default = not plan.get("writers")
    roster_data = read_json(_settings().ROSTER)
    supplied_writers = roster_data["writers"] if roster_default else plan["writers"]
    writers = []
    for supplied in supplied_writers:
        item = dict(supplied)
        item["persona"] = personas.resolve(
            item.get(
                "persona",
                plan.get("persona", roster_data.get("persona", "workshop-writer")),
            )
        )
        if "id" in item:
            identifier(item["id"])
        if item.get("mode", "auto") not in ("auto", "manual"):
            raise ValueError("writer mode must be auto or manual")
        if "requested_model" not in item and "model" not in item:
            raise ValueError("writer needs requested_model or model")
        # Either name alone is enough. Requiring both made the common plan --
        # copied from the roster minus its resolved spelling -- die on a bare
        # KeyError that named a field the help text never called required.
        item.setdefault("requested_model", item.get("model"))
        item.setdefault("model", item["requested_model"])
        if resolve_models:
            resolved = drivers.resolve(
                item["harness"],
                item["requested_model"],
                item["effort"],
                item["model"],
            )
            resolved["requested_effort"] = item.get("requested_effort", item["effort"])
            item.update(resolved)
        item.setdefault("mode", "auto")
        item.setdefault("tier", "standard")
        if item["tier"] not in TIERS:
            raise ValueError(f"writer tier must be one of {TIERS}: {item['tier']!r}")
        item["capabilities"] = list(
            item.get("capabilities", plan.get("capabilities", []))
        )
        unknown = set(item["capabilities"]) - {"image-generation"}
        if unknown:
            raise ValueError(f"unsupported capabilities: {sorted(unknown)}")
        if "image-generation" in item["capabilities"] and item["harness"] != "agy":
            raise ValueError(
                "native image generation currently requires the agy adapter"
            )
        writers.append(item)
    explicit = [item["id"] for item in writers if "id" in item]
    if not writers or len(set(explicit)) != len(explicit):
        raise ValueError("declare unique writers")
    reserved = set(explicit)
    for item in writers:
        if "id" in item:
            continue
        stem = model_directory_name(item["harness"], item["model"])
        name, attempt = stem, 2
        while name in reserved:
            name = f"{stem}-{attempt:02d}"
            attempt += 1
        item["id"] = name
        reserved.add(name)
    # Validate before creating the destination. Failed setup never masquerades
    # as a launchable partial experiment.
    files = {
        "assignment.md": plan["assignment"].encode(),
        "orientation.md": plan["brief"].encode(),
    }
    for entry in plan.get("files", []):
        rel = safe_rel(entry["path"])
        if str(rel) in files or any(
            part
            in ("AGENTS.md", "CLAUDE.md", "CODEX.md", "GEMINI.md", ".codex", ".claude")
            for part in rel.parts
        ):
            raise ValueError(f"duplicate or injected instruction path: {rel}")
        if "local_source" in entry:
            p = plan_path.parent / entry["local_source"]
            if p.is_symlink():
                raise ValueError(f"source is a symlink: {p}")
            content = p.read_bytes()
        else:
            src = safe_rel(entry["source"])
            if revision:
                mode = (
                    _git(repo, "ls-tree", revision, "--", str(src)).decode().split()[0]
                )
                if mode != "100644" and mode != "100755":
                    raise ValueError(f"source is not a regular Git blob: {src}")
                content = _git(repo, "show", f"{revision}:{src}")
            else:
                p = repo / src
                if p.is_symlink():
                    raise ValueError(f"source is a symlink: {p}")
                content = p.read_bytes()
        files[str(rel)] = content
    order_path = plan.get("reading_order", "book/READING_ORDER.md")
    if order_path and str(safe_rel(order_path)) not in files:
        # A library's order is visible in no file it contains, so it is the
        # kind of convention that cannot transmit: a writer given thirty
        # chapters cannot tell which follows which. Generated rather than
        # asked for, because the one packet built by hand forgot it.
        listing = _reading_order(repo, files)
        if listing:
            files[str(safe_rel(order_path))] = listing.encode()
    for rel in plan.get("required_reads", []):
        if str(safe_rel(rel)) not in files:
            raise ValueError(f"required reading is absent: {rel}")
    destination.mkdir(parents=True, mode=0o700)
    source = destination / "source"
    source.mkdir()
    for rel, content in files.items():
        p = source / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    frozen = dict(
        plan,
        schema=SCHEMA,
        writers_from_roster=roster_default,
        source_root=str(repo),
        source_revision=revision,
        writers=writers,
        outputs=outputs,
        input_hashes=tree_hashes(source),
        source_head=_git(repo, "rev-parse", "HEAD").decode().strip(),
        prepared_at=time.time(),
        supplied_bytes=sum(len(v) for v in files.values()),
    )
    write_json(destination / "plan.json", frozen)
    (destination / "evaluation").mkdir()
    (destination / "records").mkdir()
    (destination / "experiment.md").write_text(
        f"# {plan.get('title', destination.name)}\n\n"
        f"Source revision: {revision or 'working files, frozen by hash'}.\n\n"
        "Private experiment. plan.json freezes the assignment and inputs;\n"
        "records/<writer>/state.json carries progress. Read the assignment\n"
        "before deciding whether to run, continue manually, or evaluate.\n"
    )
    for item in writers:
        _prepare_writer(destination, frozen, item)
    write_json(
        destination / "plan-digest.json", {"sha256": digest(destination / "plan.json")}
    )
    return frozen


def _reading_order(repo, files):
    """Chapter order for the staged book files, from the book's own config.

    Silent when the source tree has no `docs/`, or when nothing staged is a
    chapter: an experiment supplying one passage needs no order.
    """
    try:
        from quirework.book import chapters as _chapters

        registry = _chapters.registry(
            current_project().path_at(repo, "book.source", "docs")
        )
    except Exception:
        return ""
    staged = {Path(rel).name for rel in files if rel.endswith(".qmd")}
    rows = [c for c in registry if Path(c.rel).name in staged]
    if len(rows) < 3:
        return ""
    lines = ["# Reading order", ""]
    for c in rows:
        number = getattr(c, "number", None)
        lines.append(f"{number:>3}. {c.title}" if number else f"     {c.title}")
    lines += ["", "Front and back matter carry no number.", ""]
    return "\n".join(lines)


def anonymize(root, output=None, *, seed=None, include_partial=False):
    """Shuffle collected drafts into evaluation/anon/ under sealed letters.

    The key is written and never returned or printed. An editor who reads the
    mapping before the drafts has spent the step and kept none of it, and a
    reshuffle cannot undo that, because draft sizes are near-unique per writer.
    Neither can a reshuffle undo per-writer byte counts seen any other way, so
    seal before inspecting sizes; `report` withholds them until this has run.

    `output` defaults to the plan's first declared output, because a default
    naming one experiment's file silently fails in every later one. Only
    writers whose turn actually completed are shuffled in: a failed partial
    looks chapter-complete and is not a candidate. Pass include_partial to
    read them anyway, deliberately.

    Candidates come from the records on disk rather than from `plan["writers"]`.
    A retry is a new writer that `retry` cannot add to the plan, because `load`
    hash-verifies the plan and a changed one is refused. Reading the frozen list
    here therefore dropped every retried seat from the seal, and dropped it
    SILENTLY: a seven-seat run whose premium seat failed on quota and was
    retried sealed six drafts and reported six, with nothing saying which seat
    was gone or why. The failed original is still excluded by the completed
    check, so a retried seat contributes its retry and not its partial.
    """
    root = Path(root)
    plan = read_json(root / "plan.json")
    if output is None:
        output = Path(plan["outputs"][0]["path"]).name

    def _completed(writer):
        state = root / "records" / writer / "state.json"
        return state.is_file() and read_json(state).get("status") == "completed"

    # Sorted for a deterministic candidate order before the shuffle; the
    # shuffle is what seals, not the enumeration order.
    candidates = sorted(p.name for p in (root / "writers").iterdir() if p.is_dir())
    present = [
        writer
        for writer in candidates
        if (root / "writers" / writer / "drafts" / output).is_file()
        and (root / "writers" / writer / "drafts" / output).stat().st_size
        and (include_partial or _completed(writer))
    ]
    if not present:
        raise ValueError(
            f"no completed writer produced drafts/{output}"
            if not include_partial
            else f"no writer produced drafts/{output}"
        )
    letters = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[: len(present)]]
    shuffled = list(present)
    random.Random(seed).shuffle(shuffled)
    anon = root / "evaluation" / "anon"
    if anon.exists():
        shutil.rmtree(anon)
    anon.mkdir(parents=True)
    key = {}
    for letter, writer in zip(letters, shuffled):
        shutil.copyfile(
            root / "writers" / writer / "drafts" / output,
            anon / f"{letter}{Path(output).suffix}",
        )
        key[letter] = writer
    write_json(root / "evaluation" / "identity-key.json", key)
    return {
        "drafts": len(key),
        "letters": letters,
        "anon": str(anon),
        "key": str(root / "evaluation" / "identity-key.json"),
        "warning": "read the drafts before opening the key; it is not printed",
    }


def _prepare_writer(root, plan, item):
    work = root / "writers" / item["id"]
    work.mkdir(parents=True)
    shutil.copytree(root / "source", work / "input")
    (work / "drafts").mkdir()
    required = ["orientation.md", "assignment.md", *plan.get("required_reads", [])]
    text = "# Your assignment\n\n" + "Read these files in full before writing:\n\n"
    text += "".join(f"- {work / 'input' / rel}\n" for rel in dict.fromkeys(required))
    text += "\nOther supplied book files are available as reference.\n\n"
    text += "The assignment determines what to deliver. Save these files:\n\n"
    text += "".join(f"- {work / entry['path']}\n" for entry in plan["outputs"])
    text += (
        "\nWork with the supplied material. Stay inside this workspace; use no\n"
        "web, external services or other agents. Inputs are read-only.\n"
        "Write your contribution in drafts/. Keep any further alternatives\n"
        "as separate files. Finish the assignment without asking for approval.\n"
    )
    if "image-generation" in item.get("capabilities", []):
        text += "\nThis assignment additionally permits the harness image-generation tool.\n"
    if item.get("persona"):
        text = personas.compose(text, item["persona"])
    (work / "START.md").write_text(text)
    record = root / "records" / item["id"]
    record.mkdir()
    write_json(
        record / "state.json",
        {"status": "prepared", "config": item, "start_sha": digest(work / "START.md")},
    )
    # A human-operated session uses the same controller and adapters. Quote
    # every path with shell quoting, not JSON or shell interpolation.
    import shlex

    command = [
        sys.executable,
        "-m",
        "quirework.sops.workshop",
        "manual",
        str(root),
        item["id"],
    ]
    launcher = (
        "#!/bin/sh\nset -eu\ncd "
        + shlex.quote(str(current_project().root))
        + "\n"
        + "export PYTHONPATH="
        + shlex.quote(str(Path(__file__).resolve().parents[2]))
        + "\nexec "
        + shlex.join(command)
        + "\n"
    )
    (record / "manual.sh").write_text(launcher)
    (record / "manual.sh").chmod(0o700)


def load(root, writer=None):
    root = Path(root).resolve()
    plan = read_json(root / "plan.json")
    if plan.get("schema") != SCHEMA:
        raise ValueError("unsupported experiment schema")
    if digest(root / "plan.json") != read_json(root / "plan-digest.json")["sha256"]:
        raise ValueError("frozen plan changed")
    if writer is None:
        return root, plan
    identifier(writer)
    record, work = root / "records" / writer, root / "writers" / writer
    state = read_json(record / "state.json")
    if tree_hashes(work / "input") != plan["input_hashes"]:
        raise ValueError(f"writer input changed: {writer}")
    if digest(work / "START.md") != state["start_sha"]:
        raise ValueError(f"writer entry instructions changed: {writer}")
    return root, plan, record, work, state


def _snapshot_driver(record):
    directory = record / "driver"
    directory.mkdir(exist_ok=True)
    for p in (
        Path(__file__),
        Path(drivers.__file__),
        Path(blind.__file__),
        Path(__file__).resolve().parents[1] / "project.py",
    ):
        shutil.copyfile(p, directory / p.name)
    write_json(
        directory / "versions.json",
        {
            "python": sys.version,
            "implementation_version": __import__("quirework").__version__,
            "files": tree_hashes(directory),
        },
    )


def _archive_drafts(work, record):
    """Copy every draft, complete or not, before anything can fail."""
    outputs = record / "artifacts"
    outputs.mkdir(exist_ok=True)
    for rel in tree_hashes(work / "drafts"):
        src, dst = work / "drafts" / rel, outputs / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and digest(src) != digest(dst):
            raise RuntimeError(f"archived output changed: {rel}; create a new attempt")
        if not dst.exists():
            shutil.copyfile(src, dst)
    return tree_hashes(outputs)


def _missing_outputs(plan, work):
    return [
        item["path"]
        for item in plan["outputs"]
        if not (work / item["path"]).is_file()
        or (work / item["path"]).stat().st_size < item["min_bytes"]
    ]


def _collect(root, writer, *, external=None):
    root, plan, record, work, state = load(root, writer)
    if state.get("pid") and process_identity(state["pid"]) == state.get(
        "process_identity"
    ):
        raise RuntimeError("writer process is still alive; wait, do not resubmit")
    if (
        state["status"] == "running"
        and not state.get("pid")
        and not state.get("ended_at")
    ):
        raise RuntimeError(
            "pending launch has no process identity; inspect the host before retiring or collecting it"
        )
    if state["status"] == "completed":
        if tree_hashes(record / "transcripts") != state["record_hashes"]:
            raise RuntimeError("archived transcript integrity failure")
        if tree_hashes(record / "artifacts") != state["output_hashes"]:
            raise RuntimeError("archived output integrity failure")
        return state
    try:
        outputs = _archive_drafts(work, record)
        if external:
            result = dict(
                external,
                transport="external import; harness completion not independently verified",
            )
            if (
                not result.get("session_id")
                or not result.get("model")
                or not result.get("provenance")
            ):
                raise ValueError(
                    "external collection needs session_id, model and provenance"
                )
        else:
            private = blind.state_root(work) / state["config"]["harness"]
            result = drivers.receipt(
                state["config"],
                record,
                private,
                manual=state.get("manual", False),
                resumed=state.get("resumed_from"),
            )
            result["transport"] = "confined harness session"
        if missing := _missing_outputs(plan, work):
            raise RuntimeError(f"missing or undersized declared outputs: {missing}")
        transcripts = record / "transcripts"
        transcripts.mkdir(exist_ok=True)
        for p in result.get("native_transcripts", []):
            src = Path(p)
            target = transcripts / src.name
            if target.exists() and digest(target) != digest(src):
                raise RuntimeError("native transcript changed after collection")
            if not target.exists():
                shutil.copyfile(src, target)
        if external and external.get("transcript"):
            src = Path(external["transcript"])
            shutil.copyfile(src, transcripts / src.name)
        answer = result.pop("answer", "")
        if answer:
            (record / "response.md").write_text(answer)
        write_json(record / "receipt.json", result)
        state.update(
            status="completed",
            completed_at=time.time(),
            output_hashes=outputs,
            record_hashes=tree_hashes(transcripts),
            receipt=result,
        )
        state.pop("error", None)
    except (RuntimeError, ValueError, OSError, StopIteration) as exc:
        state.update(status="failed", error=str(exc), collected_at=time.time())
        write_json(record / "state.json", state)
        raise
    write_json(record / "state.json", state)
    return state


def collect(root, writer, *, external=None):
    root, _, record, _, _ = load(root, writer)
    with lock(record / "operator.lock"):
        return _collect(root, writer, external=external)


def _pty_run(args, env, record):
    """Forward an interactive terminal while recording the actual child PID."""
    import errno
    import pty
    import select
    import termios
    import tty

    if not sys.stdin.isatty():
        raise RuntimeError(
            "manual mode requires a terminal; use the prepared manual.sh"
        )
    master, slave = pty.openpty()
    size = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, size)
    settings = termios.tcgetattr(sys.stdin)
    proc = subprocess.Popen(
        args, stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True
    )
    os.close(slave)
    state = read_json(record / "state.json")
    state.update(pid=proc.pid, process_identity=process_identity(proc.pid))
    write_json(record / "state.json", state)
    try:
        tty.setraw(sys.stdin.fileno())
        with (record / "terminal.log").open("ab") as log:
            while True:
                ready, _, _ = select.select([master, sys.stdin.fileno()], [], [], 1)
                if master in ready:
                    try:
                        data = os.read(master, 65536)
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                        break
                    if not data:
                        break
                    log.write(data)
                    log.flush()
                    os.write(sys.stdout.fileno(), data)
                if sys.stdin.fileno() in ready:
                    data = os.read(sys.stdin.fileno(), 65536)
                    os.write(master, data or b"\x04")
                if proc.poll() is not None and not ready:
                    break
        return proc.wait()
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
        os.close(master)


def _terminate(proc):
    """Stop a writer and everything it started, then let it be reaped.

    The child is its own session leader (``start_new_session``), so the
    harness's own subprocesses die with it instead of surviving as orphans
    holding the writer's workspace open.
    """
    for signal_number in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), signal_number)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=20)
            return
        except subprocess.TimeoutExpired:
            continue


def run_writer(root, writer, *, manual=False, timeout=None, resume=None):
    root, _plan, record, work, state = load(root, writer)
    with lock(record / "operator.lock"), harness_slot(state["config"]["harness"]):
        if state["status"] == "completed":
            return _collect(root, writer)
        if state["status"] != "prepared":
            raise RuntimeError(
                f"{writer} is {state['status']}; collect existing evidence or retry as a new writer"
            )
        if manual and not sys.stdin.isatty():
            raise RuntimeError("manual mode requires a terminal")
        if resume:
            # The session already holds the assignment, the reading and the
            # draft so far. Re-sending START.md would invite a cold restart,
            # which is the expensive thing this path exists to avoid.
            prompt = quota.continuation(
                f"Your draft is where you left it, under {work / 'drafts'}. "
                "Continue writing there."
            )
        else:
            prompt = (
                f"Read {work / 'START.md'} and complete the assignment it specifies."
            )
        args, payload = drivers.command(
            state["config"], work, prompt, manual=manual, resume=resume
        )
        jail, env, private = drivers.launch_context(state["config"], work)
        _snapshot_driver(record)
        (record / "prompt.md").write_text(prompt + "\n")
        write_json(
            record / "launch.json",
            {
                "argv": args,
                "jail": jail,
                "harness_version": blind.runner_version(state["config"]["harness"]),
                "private_runtime": str(private),
                "manual": manual,
            },
        )
        state.update(status="running", started_at=time.time(), manual=manual)
        write_json(record / "state.json", state)
        try:
            if manual:
                state["exit_code"] = _pty_run(jail + args, env, record)
            else:
                with (
                    (record / "events.jsonl").open("xb") as out,
                    (record / "stderr.log").open("xb") as err,
                ):
                    proc = subprocess.Popen(
                        jail + args,
                        stdin=subprocess.PIPE,
                        stdout=out,
                        stderr=err,
                        cwd=current_project().root,
                        env=env,
                        start_new_session=True,
                    )
                    state.update(
                        pid=proc.pid, process_identity=process_identity(proc.pid)
                    )
                    write_json(record / "state.json", state)
                    try:
                        proc.communicate(payload, timeout=timeout)
                    except subprocess.TimeoutExpired:
                        # Killed, not abandoned: collection below archives
                        # whatever prose exists and then reports the run as
                        # failed, so a stalled writer costs one slot, not the
                        # whole experiment.
                        _terminate(proc)
                        proc.communicate()
                        state["timed_out_after"] = timeout
                    state["exit_code"] = proc.returncode
            state.update(ended_at=time.time())
            state.pop("pid", None)
            state.pop("process_identity", None)
            write_json(record / "state.json", state)
            return _collect(root, writer)
        except BaseException as exc:
            # Do not mark a still-running external process dead. Recovery
            # inspects its identity and receipts instead of blindly retrying.
            state = read_json(record / "state.json")
            state["error"] = str(exc)
            if state.get("ended_at") or (
                state.get("pid")
                and process_identity(state["pid"]) != state.get("process_identity")
            ):
                state["status"] = "failed"
            write_json(record / "state.json", state)
            raise


def launch_order(writers):
    """The declared seats in spend order; `quota.spend_order` owns the rule."""
    return quota.spend_order(writers)


def run(root, writers=None, jobs=1, timeout=None):
    """Run the selected writers, cheapest first and one at a time by default.

    `jobs` stays available because the author may want a batch, but its
    default is one: seats sharing an account exhaust a five-hour window
    several times faster in parallel, and the wall then lands mid-turn on
    every one of them at once instead of on one.
    """
    root, plan = load(root)
    if jobs < 1:
        raise ValueError("jobs must be positive")
    if writers:
        # An explicit list is the operator's own order and is left alone.
        selected = list(writers)
    else:
        selected = [
            w["id"] for w in launch_order(plan["writers"]) if w.get("mode") == "auto"
        ]

    def task(writer):
        try:
            return writer, run_writer(root, writer, timeout=timeout)["status"]
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
            return writer, f"error: {exc}"

    results = {}
    if jobs == 1:
        # Sequential means sequential: a one-worker pool would still hide the
        # order, and the order is the point.
        for writer in selected:
            writer, status = task(writer)
            results[writer] = status
        return results
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for future in as_completed([submit(pool, task, w) for w in selected]):
            writer, status = future.result()
            results[writer] = status
    return results


def _archive_attempt(record, state, reason):
    """Move the stopped attempt's whole record aside before the next one runs.

    Everything the attempt produced is kept: its events, its stderr, the
    prompt it was given, the argv it was launched with, the partial draft
    `_collect` already archived, and the driver snapshot that ran it. Nothing is
    deleted and nothing is overwritten, so the run can still be read as two
    attempts rather than one that changed its mind. This is also what unblocks the collector, whose refusal to let an
    archived output change is correct for one attempt and wrong across two.
    """
    number = len(state.get("attempts", [])) + 1
    folder = record / "attempts" / f"{number:02d}"
    folder.mkdir(parents=True)
    for name in (
        "events.jsonl",
        "stderr.log",
        "prompt.md",
        "launch.json",
        "artifacts",
        "response.md",
        "receipt.json",
        "driver",
    ):
        source = record / name
        if source.exists():
            shutil.move(str(source), str(folder / name))
    state.setdefault("attempts", []).append(
        {
            "attempt": number,
            "reason": reason,
            "error": state.get("error"),
            "exit_code": state.get("exit_code"),
            "record": str(folder),
            "hashes": tree_hashes(folder),
        }
    )
    return folder


def resume(root, writer, *, timeout=None):
    """Continue a writer a provider quota limit stopped, in its own session.

    NOT `retry`. A retry is a new writer with a new session that re-derives
    everything from a cold start; this hands the same session a short notice
    and lets it finish. The difference is measurable: one seat's 31 requests
    before a wall spent 58 percent of their cost on output and reasoning and
    4.5 percent on cached context reads, so a restart pays the expensive half
    again to recover what the session still holds.

    Refuses on anything that is not a quota stop. A timeout, a crash or a
    model that simply gave up are all ambiguous about what actually ran, and
    resuming one of those would ask a writer to continue from a state nobody
    has established.
    """
    root, _plan, record, work, state = load(root, writer)
    with lock(record / "operator.lock"):
        if state.get("pid") and process_identity(state["pid"]) == state.get(
            "process_identity"
        ):
            raise RuntimeError("writer process is still alive; wait, do not resume")
        if state["status"] not in ("failed", "running"):
            raise RuntimeError(
                f"{writer} is {state['status']}; only a stopped writer is resumed"
            )
        harness = state["config"]["harness"]
        stop = quota.classify(harness, record)
        if not stop:
            raise RuntimeError(
                f"{writer} does not carry a provider quota stop in its event "
                "stream; inspect the record, and use `retry` if the work has "
                "to start over"
            )
        session = stop["session_id"]
        if stop["phase"] == "interrupted" and not session:
            raise RuntimeError(
                "the limit interrupted this writer after the model ran, but its "
                "event stream names no single session to continue; preserve the "
                "record and retry as a new writer"
            )
        # A refusal means the provider never ran the model, so there is no
        # reasoning to keep and nothing to continue FROM. The same assignment
        # simply goes again, in a new session, which is a relaunch and not a
        # restart of work that happened.
        resumed = session if stop["phase"] == "interrupted" else None
        _archive_attempt(record, state, f"quota {stop['phase']}: {stop['scope']}")
        state.update(
            status="prepared",
            resumed_from=resumed,
            quota_stop=stop["phase"],
            quota_scope=stop["scope"],
        )
        for key in (
            "error",
            "exit_code",
            "started_at",
            "ended_at",
            "collected_at",
            "timed_out_after",
            "receipt",
        ):
            state.pop(key, None)
        write_json(record / "state.json", state)
    return run_writer(root, writer, timeout=timeout, resume=resumed)


def retry(root, writer, new_id):
    root, plan, record, _, state = load(root, writer)
    identifier(new_id)
    with lock(record / "operator.lock"):
        if state.get("pid") and process_identity(state["pid"]) == state.get(
            "process_identity"
        ):
            raise RuntimeError("old writer is still running")
        if state["status"] == "running":
            raise RuntimeError(
                "reconcile the submitted attempt before creating a retry"
            )
        if (root / "writers" / new_id).exists():
            raise ValueError("new writer already exists")
        item = dict(state["config"], id=new_id, retry_of=writer)
        _prepare_writer(root, plan, item)
    return item


# The keys an editor reads to decide what to do next. The rest of a writer's
# config is the resolution evidence: a whole Codex catalog entry per writer,
# which buries ten writers' status in several hundred lines. It stays in
# plan.json and state.json; --full prints it here.
BRIEF_CONFIG = (
    "harness",
    "model",
    "effort",
    "mode",
    "tier",
    "requested_model",
    "requested_effort",
    "capabilities",
    "retry_of",
)


def roster(*, check=False, plan=None):
    """The default seats, optionally resolved against the installed catalogs.

    Resolution is what makes a stale entry visible: providers rename models
    often, and a seat that has quietly disappeared should fail here rather
    than halfway through an experiment.

    `plan` resolves that plan's own writers instead of the default roster.
    Departing from the roster is normal, and a run that adds a seat had no
    supported way to check it before launch: the answer was to call
    drivers.resolve by hand, which no reader of the SOP would find.
    """
    if plan is not None:
        data = {"source": str(plan), "writers": read_json(plan).get("writers", [])}
    else:
        data = read_json(_settings().ROSTER)
    if not check:
        return data
    for item in data["writers"]:
        # Either name alone is enough here too. `prepare` normalizes the pair
        # before resolving; this path did not, so the one command the SOP says
        # to run against a hand-written plan died on a bare KeyError naming a
        # field the plan template calls optional.
        item.setdefault("requested_model", item.get("model"))
        item.setdefault("model", item.get("requested_model"))
        # `effort` is the third resolver input and was the one still indexed
        # bare, so the fix above left the identical crash reachable under a
        # different key. The plan template marks the field optional and
        # `prepare` fills it; this path has to as well.
        item.setdefault("effort", None)
        try:
            item["resolved"] = drivers.resolve(
                item["harness"],
                item["requested_model"],
                item["effort"],
                item["model"],
            )["model"]
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            item["resolved"] = f"UNAVAILABLE: {exc}"
    data["unavailable"] = [
        i["model"]
        for i in data["writers"]
        if str(i["resolved"]).startswith("UNAVAILABLE")
    ]
    return data


def inspect(root, *, full=False):
    root, plan = load(root)
    writers = {}
    for p in sorted((root / "records").glob("*/state.json")):
        s = read_json(p)
        item = {
            k: s[k]
            for k in (
                "status",
                "config",
                "error",
                "started_at",
                "ended_at",
                "quota_stop",
                "quota_scope",
                "resumed_from",
            )
            if k in s
        }
        # Named outright, because the recovery differs from every other
        # failure: this writer is resumed, not retried.
        if s.get("status") == "failed" and not s.get("attempts"):
            stop = quota.classify(
                s["config"]["harness"], root / "records" / p.parent.name
            )
            if stop:
                item["quota_stop"] = stop["phase"]
                # Not `bool(session_id)`: a refusal records a session too, so
                # that test called every refused writer resumable and put it in
                # the bucket the SOP uses to choose between continue and
                # relaunch. `quota` owns which stops can be continued.
                item["resumable"] = bool(quota.resumable_session(stop))
        if s.get("attempts"):
            item["attempts"] = [
                {k: a.get(k) for k in ("attempt", "reason", "record")}
                for a in s["attempts"]
            ]
        item["persona"] = personas.identity(s.get("config", {}).get("persona"))
        if not full and "config" in item:
            item["config"] = {
                k: v
                for k, v in item["config"].items()
                if k in BRIEF_CONFIG and v not in (None, [], "")
            }
        if s.get("pid"):
            item["process_alive"] = process_identity(s["pid"]) == s.get(
                "process_identity"
            )
        # Presence and packet integrity, never byte counts. A completion check
        # needs both, and reporting sizes here would hand over the identity key
        # before `anonymize` has sealed it, which is what `report` now refuses
        # to do. See docs/workflows.md, "Seal first, inspect afterwards".
        work = root / "writers" / p.parent.name
        item["declared_outputs"] = {
            o["path"]: (
                "ok"
                if (work / o["path"]).is_file()
                and (work / o["path"]).stat().st_size >= o["min_bytes"]
                else "missing or undersized"
            )
            for o in plan["outputs"]
        }
        item["packet_unchanged"] = tree_hashes(work / "input") == plan["input_hashes"]
        writers[p.parent.name] = item
    return {
        "experiment": str(root),
        "supplied_bytes": plan["supplied_bytes"],
        "writers": writers,
    }


def _normalize_usage(harness, usage):
    """One token shape across three harnesses, plus what each cannot report.

    These are provider counters summed over every request in the session, so
    they are neither context occupancy nor comparable billing across
    harnesses. Only Claude reports a price; a subscription harness reports
    none, and inventing one from public rates would put a number the run did
    not measure next to numbers it did.
    """
    out = {
        "input": None,
        "cached_input": None,
        "output": None,
        "reasoning": None,
        "cost_usd": None,
        "cost_evidence": "not reported by this harness",
    }
    if not usage:
        return out
    if harness == "claude":
        # A dict keyed by served model: the writing model plus whatever small
        # preflight the harness ran. Summed here, itemized in by_model so the
        # preflight stays visible instead of inflating the writer's figure.
        out["by_model"] = {
            name: {
                "input": row.get("inputTokens"),
                "output": row.get("outputTokens"),
                "reasoning": row.get("thinkingTokens"),
                "cost_usd": row.get("costUSD"),
            }
            for name, row in usage.items()
        }

        def total(k):
            return sum(r.get(k) or 0 for r in usage.values())

        out.update(
            input=total("inputTokens"),
            cached_input=total("cacheReadInputTokens")
            + total("cacheCreationInputTokens"),
            output=total("outputTokens"),
            reasoning=total("thinkingTokens"),
            cost_usd=round(total("costUSD"), 4),
            cost_evidence="harness modelUsage costUSD, list basis",
        )
    elif harness == "codex":
        out.update(
            input=usage.get("input_tokens"),
            cached_input=usage.get("cached_input_tokens"),
            output=usage.get("output_tokens"),
            reasoning=usage.get("reasoning_output_tokens"),
        )
    else:
        out.update(
            input=usage.get("input_tokens"),
            cached_input=usage.get("cache_read_tokens"),
            output=usage.get("output_tokens"),
            reasoning=usage.get("thinking_tokens"),
        )
    return out


def report(root, *, sizes=None):
    """Cost of each writer beside what it returned, for the editor's ROI note.

    Everything here is transport and spend. Yield is the editorial reading;
    output_bytes counts what came back, never whether it was worth reading.

    Byte counts are near-unique per writer, so printing them before the drafts
    are sealed hands the editor the identity key by arithmetic. They are
    therefore withheld until `anonymize` has run. Pass sizes=True to override
    deliberately, sizes=False to keep them out of a sealed run's write-up.
    """
    root, plan = load(root)
    if sizes is None:
        sizes = (Path(root) / "evaluation" / "identity-key.json").is_file()
    declared = [item["path"] for item in plan["outputs"]]
    rows = []
    for p in sorted((root / "records").glob("*/state.json")):
        s, writer = read_json(p), p.parent.name
        config, receipt = s.get("config", {}), s.get("receipt", {})
        artifacts = root / "records" / writer / "artifacts"
        produced = {
            str(q.relative_to(artifacts)): q.stat().st_size
            for q in sorted(artifacts.rglob("*"))
            if q.is_file()
        }
        started, ended = s.get("started_at"), s.get("ended_at")
        rows.append(
            {
                "writer": writer,
                "harness": config.get("harness"),
                "model": receipt.get("model") or config.get("model"),
                "selected_model": config.get("model"),
                "effort": config.get("effort"),
                "requested_effort": config.get("requested_effort"),
                "status": s.get("status"),
                "seconds": round(ended - started, 1) if started and ended else None,
                "usage": _normalize_usage(config.get("harness"), receipt.get("usage")),
                "context": drivers.context_peak(
                    config.get("harness"), root / "records" / writer
                ),
                "output_bytes": {
                    "declared": sum(
                        produced.get(d.split("/", 1)[1], 0) for d in declared
                    ),
                    "all_files": sum(produced.values()),
                    "extra_files": sorted(
                        set(produced) - {d.split("/", 1)[1] for d in declared}
                    ),
                }
                if sizes
                else {"withheld": "run anonymize first, or pass --sizes"},
                "error": s.get("error"),
            }
        )
    return {"experiment": str(root), "sizes_shown": sizes, "writers": rows}


def report_table(data):
    """A markdown table for the editor's write-up; JSON stays the record."""
    head = (
        "| Writer | Harness | Model | Effort | Status | Seconds | "
        "Peak request | Window | In | Cached in | Out | Reasoning | USD | "
        "Draft bytes |"
    )
    lines = [head, "|" + "---|" * 14]

    def cell(value):
        if value in (None, ""):
            return "-"
        return f"{value:,}" if isinstance(value, int) else str(value)

    for r in data["writers"]:
        u = r["usage"]
        effort = r["effort"]
        if r["requested_effort"] and r["requested_effort"] != r["effort"]:
            effort += f" (asked {r['requested_effort']})"
        lines.append(
            "| "
            + " | ".join(
                [
                    r["writer"],
                    r["harness"] or "-",
                    r["model"] or "-",
                    effort or "-",
                    r["status"] or "-",
                    cell(r["seconds"]),
                    cell(r["context"]["peak_request"]),
                    cell(r["context"]["window"]),
                    cell(u["input"]),
                    cell(u["cached_input"]),
                    cell(u["output"]),
                    cell(u["reasoning"]),
                    cell(u["cost_usd"]),
                    cell(r["output_bytes"].get("declared")),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("prepare", description=prepare.__doc__)
    p.add_argument("plan")
    p.add_argument("destination")
    p = sub.add_parser("run")
    p.add_argument("experiment")
    p.add_argument("writers", nargs="*")
    p.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="writers at once; one by default, and naming writers keeps your "
        "order instead of the roster's cheap-then-premium one",
    )
    p.add_argument(
        "--timeout",
        type=float,
        help="seconds before a writer is killed; its partial prose is kept",
    )
    p = sub.add_parser("roster", description=roster.__doc__)
    p.add_argument(
        "--check", action="store_true", help="resolve each seat against the catalogs"
    )
    p.add_argument(
        "--plan",
        help="resolve this plan's writers instead of the default roster; "
        "departing from the roster is normal and its seats need checking too",
    )
    p = sub.add_parser("inspect")
    p.add_argument("experiment")
    p.add_argument(
        "--full", action="store_true", help="include full model-resolution evidence"
    )
    p = sub.add_parser("report", description=report.__doc__)
    p.add_argument("experiment")
    p.add_argument("--table", action="store_true", help="markdown instead of JSON")
    p.add_argument(
        "--sizes",
        dest="sizes",
        action="store_true",
        default=None,
        help="show per-writer byte counts before the drafts are sealed",
    )
    p.add_argument(
        "--no-sizes", dest="sizes", action="store_false", help="always withhold them"
    )
    p = sub.add_parser("anonymize", description=anonymize.__doc__)
    p.add_argument("experiment")
    p.add_argument(
        "--output", default=None, help="default: the plan's first declared output"
    )
    p.add_argument(
        "--include-partial",
        action="store_true",
        help="shuffle in writers whose turn did not complete",
    )
    p.add_argument("--seed", type=int, default=None)

    p = sub.add_parser("collect")
    p.add_argument("experiment")
    p.add_argument("writer")
    p.add_argument(
        "--external",
        help="JSON file with session_id, model, provenance, optional transcript",
    )
    p = sub.add_parser("manual")
    p.add_argument("experiment")
    p.add_argument("writer")
    p = sub.add_parser(
        "resume",
        description="Continue a writer a provider quota limit stopped, in its "
        "own session. Not a retry: the session keeps the reading, the "
        "reasoning and the draft, and the writer is told only that it was "
        "interrupted.",
    )
    p.add_argument("experiment")
    p.add_argument("writer")
    p.add_argument("--timeout", type=float, default=None)
    p = sub.add_parser("retry")
    p.add_argument("experiment")
    p.add_argument("writer")
    p.add_argument("new_id")
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            p = prepare(args.plan, args.destination)
            result = {
                "experiment": args.destination,
                "writers": len(p["writers"]),
                "supplied_bytes": p["supplied_bytes"],
            }
        elif args.action == "run":
            result = run(args.experiment, args.writers, args.jobs, args.timeout)
            print(json.dumps(result, indent=2))
            return int(any(v != "completed" for v in result.values()))
        elif args.action == "roster":
            result = roster(check=args.check, plan=args.plan)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return int(bool(result.get("unavailable")))
        elif args.action == "inspect":
            result = inspect(args.experiment, full=args.full)
        elif args.action == "report":
            data = report(args.experiment, sizes=args.sizes)
            print(report_table(data) if args.table else json.dumps(data, indent=2))
            return 0
        elif args.action == "anonymize":
            result = anonymize(
                args.experiment,
                args.output,
                seed=args.seed,
                include_partial=args.include_partial,
            )
        elif args.action == "collect":
            result = collect(
                args.experiment,
                args.writer,
                external=read_json(args.external) if args.external else None,
            )
        elif args.action == "manual":
            result = run_writer(args.experiment, args.writer, manual=True)
        elif args.action == "resume":
            result = resume(args.experiment, args.writer, timeout=args.timeout)
        else:
            result = retry(args.experiment, args.writer, args.new_id)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"workshop: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
