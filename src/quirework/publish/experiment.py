"""Forked reader experiments driven by a project-owned procedure.

The operator owns assignment, artifacts and interpretation. A reader sees the
same page transport and frozen read-through prompt on every arm. Runtime homes
are copied only from an idle parent and never shared between arms.
"""

from __future__ import annotations

import argparse
import base64
from collections import Counter
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from quirework.core import usage as usage_records
from quirework.providers import blind

# Above every published ceiling on purpose; Codex clamps it down.
OVER_REQUEST = 1_050_000
from quirework.project import current_project

PROMPT_REVISION = "2026-09-04-reader-understanding"
PROTOCOL_REVISION = "chapter-experiment-v2"
VIRTUAL = Path.home() / "tmp" / "reader-workspace"

ARMS = ("a", "b", "c")
CODEX_TRANSPORT_FALLBACK = "Falling back from WebSockets to HTTPS transport."
SUPPORTED_MODELS = {"codex": "gpt-5.6-sol", "claude": "opus"}


def _settings():
    """Resolve paths from the current operation, without import-time state."""
    from types import SimpleNamespace

    SOURCE = current_project().path("sops.reader_driver")
    return SimpleNamespace(SOURCE=SOURCE)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
        tmp = f.name
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_json(path):
    return json.loads(Path(path).read_text())


def validate_runner_model(runner, model):
    expected = SUPPORTED_MODELS[runner]
    if model != expected:
        raise RuntimeError(
            f"{runner} chapter experiments require model {expected!r}, not {model!r}"
        )


def tree_hashes(root):
    root = Path(root)
    if any(p.is_symlink() for p in root.rglob("*")):
        raise RuntimeError(f"symlink in experiment state: {root}")
    return {
        str(p.relative_to(root)): digest(p)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def events(path):
    """An incomplete final line is a pending writer, never a completed turn."""
    if not Path(path).exists():
        return []
    lines = Path(path).read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def prompts(seat):
    spec = importlib.util.spec_from_file_location(
        "reader_instrument", _settings().SOURCE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prefix = seat.upper()
    return {
        "first": getattr(module, f"{prefix}_TURN_ONE"),
        "next": getattr(module, f"{prefix}_TURN_NEXT"),
        "headings": getattr(module, f"{prefix}_CHAPTER_HEADINGS"),
    }


def missing_headings(text, headings):
    """Match heading lines while preserving typographic punctuation in evidence."""
    actual = {
        line.strip().replace("\u2019", "'")
        for line in text.splitlines()
        if line.startswith("## ")
    }
    return [heading for heading in headings if heading not in actual]


def render_pages(pdf, directory):
    directory.mkdir(parents=True)
    subprocess.run(
        ["pdftoppm", "-png", "-scale-to", "1600", str(pdf), str(directory / "page")],
        check=True,
        capture_output=True,
    )
    pages = sorted(
        directory.glob("page-*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[1])
    )
    from pypdf import PdfReader

    if len(pages) != len(PdfReader(pdf).pages) or not pages:
        raise RuntimeError("incomplete page rasterization")
    return pages


def snapshot(source, target):
    """Copy an idle home, detecting concurrent writes rather than copying a WAL mid-write."""
    before = tree_hashes(source)
    shutil.copytree(source, target)
    if tree_hashes(source) != before or tree_hashes(target) != before:
        raise RuntimeError("parent state changed during snapshot; do not fork it")
    return before


def assert_model(runner, requested, actual, established=None):
    if established is not None:
        valid = actual == established
    elif runner == "claude" and requested in ("opus", "opus[1m]"):
        valid = actual.startswith("claude-opus-")
    else:
        valid = actual == requested
    if not valid:
        raise RuntimeError(
            f"served model {actual!r}, expected {established or requested!r}"
        )


def reject_compaction(rows):
    for row in rows:
        payload = row.get("payload") or {}
        item = payload.get("item") or {}
        fields = [
            row.get("type"),
            row.get("subtype"),
            payload.get("type"),
            item.get("type"),
        ]
        if any("compact" in str(value).lower() for value in fields if value):
            raise RuntimeError("compaction invalidates this experiment")


def codex_item_diagnostics(rows):
    """Return recognized transport notices and reject every other item kind."""
    notices = []
    for row in rows:
        if row.get("type") != "item.completed":
            continue
        item = row.get("item", {})
        if item.get("type") in ("agent_message", "reasoning"):
            continue
        if item.get("type") == "error" and item.get("message", "").startswith(
            CODEX_TRANSPORT_FALLBACK
        ):
            notices.append(item["message"])
            continue
        raise RuntimeError("Codex used a tool or an unexpected output item")
    return notices


def parse_codex(rows, home, model, effort, established):
    started = [r["thread_id"] for r in rows if r.get("type") == "thread.started"]
    if len(started) != 1:
        raise RuntimeError("missing or ambiguous Codex thread identity")
    sid = started[0]
    transport_notices = codex_item_diagnostics(rows)
    answers = [
        r["item"]["text"]
        for r in rows
        if r.get("type") == "item.completed"
        and r.get("item", {}).get("type") == "agent_message"
    ]
    completed = [r for r in rows if r.get("type") == "turn.completed"]
    if not answers or not completed:
        raise RuntimeError("Codex turn has no completed answer")
    matches = list((home / "sessions").glob(f"**/*{sid}.jsonl"))
    if len(matches) != 1:
        raise RuntimeError("missing or ambiguous Codex rollout for this thread")
    roll = events(matches[0])
    reject_compaction(roll)
    contexts = [r["payload"] for r in roll if r.get("type") == "turn_context"]
    if not contexts:
        raise RuntimeError("missing Codex model receipt")
    ctx = contexts[-1]
    actual = ctx.get("model")
    assert_model("codex", model, actual, established)
    if ctx.get("effort") != effort:
        raise RuntimeError("Codex effort changed")
    windows = [
        r["payload"].get("model_context_window", 0)
        for r in roll
        if r.get("type") == "event_msg"
        and r.get("payload", {}).get("type") == "task_started"
    ]
    # STILL A FIXED FLOOR, and deliberately so. Deriving it from the catalog
    # was tried and reverted: this is the receipt verifier, and making it
    # resolve a model would put a live catalog read on the verification path,
    # which the tests rightly refuse by running the whole module on a
    # fictional model. The number is the one hazard the launch side no longer
    # has (a model ceilinged below it would be refused for running at its own
    # maximum), so the fix belongs at prepare time: freeze the resolved
    # ceiling and floor into the plan, and read them here. Every model this
    # experiment has run publishes 872,000 at 95 percent, so nothing is
    # refused today.
    if not windows or windows[-1] < 800_000:
        raise RuntimeError("Codex effective context below the reader requirement")
    meta = next((r["payload"] for r in roll if r.get("type") == "session_meta"), {})
    return answers[-1], {
        "session_id": sid,
        "model": actual,
        "usage": completed[-1]["usage"],
        "effective_context": windows[-1],
        "forked_from": meta.get("forked_from_id"),
        "transport_notices": transport_notices,
        "rollout": str(matches[0]),
    }


def parse_claude(rows, home, model, effort, established):
    reject_compaction(rows)
    results = [r for r in rows if r.get("type") == "result"]
    if len(results) != 1:
        raise RuntimeError("Claude turn has no unique result")
    result = results[0]
    if result.get("is_error") or result.get("subtype") != "success":
        raise RuntimeError(
            f"Claude result is {result.get('subtype')}: "
            f"{str(result.get('result', ''))[:180]}"
        )
    for row in rows:
        if row.get("type") == "assistant":
            content = row.get("message", {}).get("content", [])
            if any(b.get("type") in ("tool_use", "server_tool_use") for b in content):
                raise RuntimeError("Claude used a tool in an attached-pages experiment")
    usage = result.get("modelUsage") or {}
    served = {
        r.get("message", {}).get("model") for r in rows if r.get("type") == "assistant"
    }
    if len(served) != 1:
        raise RuntimeError("Claude missing or mixed assistant-model receipt")
    actual = served.pop()
    if actual not in usage or not usage[actual].get("outputTokens"):
        raise RuntimeError("Claude missing served-model usage receipt")
    # Claude's documented first-turn Haiku preflight is billing overhead,
    # not a fallback reader. Verify the actual assistant messages separately.
    auxiliary = {k: v for k, v in usage.items() if k != actual}
    if any(not k.startswith("claude-haiku-") for k in auxiliary):
        raise RuntimeError("unrecognized auxiliary model usage")
    init = [r for r in rows if r.get("type") == "system" and r.get("subtype") == "init"]
    if len(init) != 1 or init[0].get("tools") != ["Read"] or init[0].get("mcp_servers"):
        raise RuntimeError("Claude tool or MCP configuration drifted")
    if init[0].get("output_style") != "default" or init[0].get("model") != actual:
        raise RuntimeError("Claude initial model or output style drifted")
    assert_model("claude", model, actual, established)
    sid = result["session_id"]
    matches = list((home / "projects").glob(f"**/{sid}.jsonl"))
    if len(matches) != 1:
        raise RuntimeError("Claude session was not durably persisted")
    reject_compaction(events(matches[0]))
    return result.get("result", ""), {
        "session_id": sid,
        "model": actual,
        "usage": usage[actual],
        "auxiliary_usage": auxiliary,
        "cost_usd": result.get("total_cost_usd"),
        "rollout": str(matches[0]),
    }


from quirework.providers.images import history_image_hashes


def command(runner, model, effort, mode, sid, images, prompt):
    if runner == "codex":
        args = ["codex", "exec"]
        if mode == "start":
            args += ["-s", "read-only"]
        else:
            args += [mode, sid]
        args += [
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{effort}"',
            "-c",
            'model_verbosity="high"',
            "-c",
            'personality="none"',
            "-c",
            'web_search="disabled"',
            # Over-request and let Codex clamp to the catalog maximum,
            # which is what the operator's own config.toml does and says:
            # "Codex clamps this to the model catalog maximum and reserves
            # the model's configured headroom." Resolving the exact ceiling
            # was tried and reverted: it puts a live catalog read on a path
            # the tests exercise with a fictional model. The scope is the
            # setting that matters either way, without which the limit is
            # clamped to 90% and compaction is not disabled at all.
            "-c",
            f"model_context_window={OVER_REQUEST}",
            "-c",
            f"model_auto_compact_token_limit={OVER_REQUEST}",
            "-c",
            'model_auto_compact_token_limit_scope="body_after_prefix"',
        ]
        for path in images:
            args += ["-i", str(path)]
        return args + ["--json", prompt], b""
    args = [
        "claude",
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--effort",
        effort,
        "--tools",
        "Read",
        "--allowedTools",
        "Read",
        *blind.BLIND_SETTINGS,
        *blind.NO_SETTING_SOURCES,
        *blind.NO_MCP,
    ]
    if mode == "start":
        args += ["--model", model, "--session-id", sid]
    else:
        args += ["--resume", sid]
        if mode == "fork":
            args += ["--fork-session"]
    # The host supplies actual image bytes below; no file-read tool is needed.
    return args, None


def claude_environment():
    """Supply only an access token; a blind child must not rotate shared OAuth.

    No token is put in an argument, receipt, plan or snapshot. The documented
    environment-token path takes precedence over persisted Claude credentials
    and has no refresh token. Renew authentication outside the reader.
    """
    env = os.environ.copy()
    if any(
        env.get(k)
        for k in (
            "CLAUDE_BACKEND",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
        )
    ):
        raise RuntimeError(
            "this experiment adapter requires direct claude.ai authentication"
        )
    if not env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        path = Path.home() / ".claude" / ".credentials.json"
        oauth = read_json(path).get("claudeAiOauth", {})
        if (
            not oauth.get("accessToken")
            or oauth.get("expiresAt", 0) / 1000 < time.time() + 300
        ):
            raise RuntimeError(
                "Claude login is missing or expiring; refresh it outside the blind reader"
            )
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth["accessToken"]
    return env


def image_message(prompt, images):
    content = [{"type": "text", "text": prompt}]
    content += [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(Path(p).read_bytes()).decode(),
            },
        }
        for p in images
    ]
    return (
        json.dumps({"type": "user", "message": {"role": "user", "content": content}})
        + "\n"
    ).encode()


class Experiment:
    def __init__(self, record, progress=False):
        self.record = Path(record).resolve()
        self.plan = read_json(self.record / "plan.json")
        self.root = Path(self.plan["scratch"])
        self.runner = self.plan["runner"]
        self.progress = progress
        self.state_file = self.record / "state.json"
        self.state = (
            read_json(self.state_file)
            if self.state_file.exists()
            else {"sessions": {}, "done": {}, "pending": None}
        )

    def save(self):
        write_json(self.state_file, self.state)

    def emit(self, message, terminal=False):
        if self.progress or terminal:
            print(message, flush=True)

    def workspace(self, seat):
        return self.root / seat / "seat"

    def home(self, seat):
        return blind.state_root(self.workspace(seat)) / self.runner

    def banked_history_pages(self, seat):
        pages = [
            p
            for index, item in enumerate(self.plan.get("prefix", []))
            if f"prefix-{index:02d}-{item['stem']}" in self.state["done"]
            for p in item["pages"]
        ]
        if seat in ARMS:
            pages += [
                p
                for index, item in enumerate(self.plan["arms"][seat]["segments"])
                if f"arm-{seat}-{index:02d}-{item['stem']}" in self.state["done"]
                for p in item["pages"]
            ]
        return pages

    def verify(self):
        if (
            digest(self.record / "plan.json")
            != (self.record / "plan.sha256").read_text().strip()
        ):
            raise RuntimeError("frozen plan or assignment changed")
        if self.plan["driver_sha"] != digest(__file__):
            raise RuntimeError(
                "driver changed since preparation; freeze a new experiment"
            )
        if self.plan["blind_sha"] != digest(blind.__file__):
            raise RuntimeError("shared confinement code changed since preparation")
        if blind.runner_version(self.runner) != self.plan["runner_version"]:
            raise RuntimeError("runner version changed since preparation")
        for rel, expected in self.plan["hashes"].items():
            if digest(self.record / rel) != expected:
                raise RuntimeError(f"frozen experiment input changed: {rel}")
        for key, done in self.state["done"].items():
            if digest(self.record / done["answer"]) != done["answer_sha"]:
                raise RuntimeError(f"earlier reaction changed or disappeared: {key}")

    def turn(self, seat, key, prompt, pages, mode, headings=()):
        self.verify()
        if key in self.state["done"]:
            return self.state["done"][key]
        pending = self.state["pending"]
        if pending and pending["key"] != key:
            raise RuntimeError(
                f"unresolved submitted turn {pending['key']}; cannot skip it"
            )
        work = self.workspace(seat)
        work.mkdir(parents=True, exist_ok=True)
        staged = work / "pages"
        staged.mkdir(exist_ok=True)
        originals = [self.record / p for p in pages]
        wanted = {f"page-{index:03d}.png" for index in range(len(originals))}
        for old in staged.iterdir():
            if old.is_symlink() or not old.is_file():
                raise RuntimeError("unexpected entry in isolated page staging")
            if old.name not in wanted:
                old.unlink()
        for index, source in enumerate(originals):
            shutil.copyfile(source, staged / f"page-{index:03d}.png")
        physical_images = sorted(staged.glob("*.png"))
        virtual_images = [VIRTUAL / "pages" / p.name for p in physical_images]
        sid = self.state["sessions"].get(seat)
        if mode == "fork":
            sid = self.state["sessions"]["parent"]
        elif mode == "start":
            sid = str(uuid.uuid4())
        raw = self.record / "raw" / f"events-{key}.jsonl"
        err = self.record / "raw" / f"stderr-{key}.log"
        raw.parent.mkdir(exist_ok=True)
        args, payload = command(
            self.runner,
            self.plan["model"],
            self.plan["effort"],
            mode,
            sid,
            virtual_images,
            prompt,
        )
        if payload is None:
            payload = image_message(prompt, physical_images)
        request_hash = hashlib.sha256(
            prompt.encode()
            + b"\0"
            + "".join(digest(p) for p in physical_images).encode()
        ).hexdigest()
        if pending:
            if pending["request_sha"] != request_hash:
                raise RuntimeError("pending request differs from frozen request")
            if pending.get("pid"):
                try:
                    os.kill(pending["pid"], 0)
                except ProcessLookupError:
                    pass
                else:
                    raise RuntimeError(
                        "submitted process still exists; wait, do not resubmit"
                    )
        else:
            if raw.exists():
                raise RuntimeError(
                    "unbanked event file exists; reconcile before submitting"
                )
            runtime_env = claude_environment() if self.runner == "claude" else None
            self.state["pending"] = {
                "key": key,
                "request_sha": request_hash,
                "mode": mode,
                "parent_id": sid,
            }
            self.save()
            if blind.backend_error():
                raise RuntimeError(blind.backend_error())
            if self.runner == "claude":
                # Claude also keeps runtime configuration beside ~/.claude.
                # Persist a fresh per-seat file; never mount the user's file.
                config = self.home(seat) / "runtime-config.json"
                config.parent.mkdir(parents=True, exist_ok=True)
                if not config.exists():
                    write_json(config, {})
            jail = blind.jail_argv(
                work,
                runners=[self.runner],
                bind_credentials=self.runner != "claude",
                env={"DISABLE_COMPACT": "1", "DISABLE_AUTOUPDATER": "1"},
            )
            # Give every arm the same logical paths without sharing a mount or
            # runtime home. The host's real seat path stays outside the prompt.
            # Replace the existing chdir argument rather than emitting a
            # second one (bwrap warns, and stderr must remain useful).
            jail[jail.index("--chdir") + 1] = str(VIRTUAL)
            jail += ["--bind", str(work), str(VIRTUAL), "--setenv", "PWD", str(VIRTUAL)]
            if self.runner == "claude":
                jail += ["--bind", str(config), str(Path.home() / ".claude.json")]
            binds, missing = blind.runner_binds([self.runner])
            if missing:
                raise RuntimeError(
                    "missing blind launcher paths: " + "; ".join(missing)
                )
            usage_records.seat(
                "experiment",
                {
                    "harness": self.runner,
                    "model": self.plan["model"],
                    "effort": self.plan["effort"],
                },
            )
            blind.write_receipt(
                self.record / "raw" / f"blind-{key}.json",
                confined=True,
                cmd=args,
                run_dir=work,
                runners=[self.runner],
                binds=binds,
                reason="",
            )
            with raw.open("wb") as out, err.open("wb") as errors:
                proc = subprocess.Popen(
                    jail + args,
                    stdin=subprocess.PIPE,
                    stdout=out,
                    stderr=errors,
                    cwd=current_project().root,
                    start_new_session=True,
                    env=runtime_env,
                )
                self.state["pending"]["pid"] = proc.pid
                self.save()
                proc.communicate(payload)
                self.state["pending"]["exit_code"] = proc.returncode
                self.state["pending"].pop("pid", None)
                self.save()
        # Completion can be flushed just after the CLI exits. Reconcile the
        # existing event/rollout files; never generate a second first reaction.
        failure = None
        for attempt in range(30):
            try:
                parser = parse_codex if self.runner == "codex" else parse_claude
                answer, receipt = parser(
                    events(raw),
                    self.home(seat),
                    self.plan["model"],
                    self.plan["effort"],
                    self.state.get("served_model"),
                )
                break
            except (RuntimeError, json.JSONDecodeError) as exc:
                failure = exc
                if attempt == 29:
                    raise RuntimeError(f"{key}: {failure}; inspect {err}") from exc
                time.sleep(1)
        if len(answer.strip()) < 30:
            raise RuntimeError("implausibly short answer")
        missing = missing_headings(answer, headings)
        if missing:
            raise RuntimeError(f"reaction missing headings: {missing}")
        actual_sid = receipt["session_id"]
        previous = self.state["pending"]["parent_id"]
        if mode == "resume" and actual_sid != previous:
            raise RuntimeError("resume silently created a new session")
        if mode == "fork" and (
            actual_sid == previous or actual_sid in self.state["sessions"].values()
        ):
            raise RuntimeError("fork did not create a distinct session")
        if (
            mode == "fork"
            and self.runner == "codex"
            and receipt["forked_from"] != previous
        ):
            raise RuntimeError("Codex fork names the wrong parent")
        previous_pages = self.banked_history_pages(seat)
        expected_images = Counter(
            digest(self.record / p) for p in previous_pages + list(pages)
        )
        observed_images = Counter(
            history_image_hashes(receipt["rollout"], self.home(seat))
        )
        if expected_images - observed_images:
            raise RuntimeError("persisted reading history lost or changed page images")
        receipt["verified_history_images"] = sum(expected_images.values())
        receipt["exit_code"] = self.state["pending"].get("exit_code")
        self.state["served_model"] = receipt["model"]
        self.state["sessions"][seat] = actual_sid
        dest = self.record / f"{key}.md"
        if dest.exists() and dest.read_text() != answer + "\n":
            raise RuntimeError("refusing to overwrite an existing reaction")
        dest.write_text(answer + "\n")
        receipt.update(
            answer=dest.name, answer_sha=digest(dest), request_sha=request_hash
        )
        self.state["done"][key] = receipt
        self.state["pending"] = None
        self.save()
        for image in physical_images:
            image.unlink()
        self.emit(f"{self.runner}: banked {key} ({receipt['model']})")
        if receipt["exit_code"] not in (None, 0):
            raise RuntimeError(
                f"{key}: answer banked, but process exited {receipt['exit_code']}; halt"
            )
        return receipt

    def checkpoint(self):
        if self.state["pending"]:
            raise RuntimeError("cannot checkpoint a pending turn")
        checkpoint = self.root / "checkpoint"
        if "checkpoint" not in self.state:
            # Provider writers must be quiescent, including delayed writes.
            previous = tree_hashes(self.home("parent"))
            for _ in range(30):
                time.sleep(1)
                now = tree_hashes(self.home("parent"))
                if now == previous:
                    break
                previous = now
            else:
                raise RuntimeError("parent runtime home is still changing")
            hashes = snapshot(self.home("parent"), checkpoint)
            self.state["checkpoint"] = hashes
            self.save()
        self.check_parent()

    def check_parent(self):
        expected = self.state["checkpoint"]
        if tree_hashes(self.root / "checkpoint") != expected:
            raise RuntimeError("frozen checkpoint changed")
        if tree_hashes(self.home("parent")) != expected:
            raise RuntimeError("parent changed after checkpoint")

    def fork_home(self, seat):
        self.check_parent()
        home = self.home(seat)
        if not home.exists():
            home.parent.mkdir(parents=True)
            snapshot(self.root / "checkpoint", home)
        elif seat not in self.state["sessions"] and not self.state["pending"]:
            if tree_hashes(home) != self.state["checkpoint"]:
                raise RuntimeError("unsubmitted branch home differs from checkpoint")

    def run(self):
        self.verify()
        p = self.plan["prompts"]
        for index, item in enumerate(self.plan["prefix"]):
            self.turn(
                "parent",
                f"prefix-{index:02d}-{item['stem']}",
                p["first"] if index == 0 else p["next"],
                item["pages"],
                "start" if index == 0 else "resume",
                p["headings"],
            )
        self.checkpoint()
        for arm in self.plan["order"]:
            self.fork_home(arm)
            for index, item in enumerate(self.plan["arms"][arm]["segments"]):
                self.turn(
                    arm,
                    f"arm-{arm}-{index:02d}-{item['stem']}",
                    p["next"],
                    item["pages"],
                    "fork" if index == 0 else "resume",
                    p["headings"],
                )
                self.check_parent()
        if self.plan.get("canary_markers"):
            for arm in ARMS:
                item = self.plan["arms"][arm]["segments"][0]
                answer = self.record / f"arm-{arm}-00-{item['stem']}.md"
                text = answer.read_text().lower()
                if any(
                    marker.lower() not in text for marker in self.plan["canary_markers"]
                ):
                    raise RuntimeError(
                        "fork did not retain the synthetic reading history"
                    )
            self.state["canary_passed"] = True
        self.state["complete"] = True
        self.save()
        self.emit(f"Complete: {self.record}", terminal=True)


def prepare(args):
    record = args.record_dir.expanduser().resolve()
    validate_runner_model(args.runner, args.model)
    record.mkdir(parents=True, exist_ok=False)
    artifact = record / "raw" / "artifact"
    artifact.mkdir(parents=True)
    scratch = Path(
        tempfile.mkdtemp(prefix="quirework-chapter-", dir=Path.home() / "tmp")
    )
    plan = {
        "protocol": PROTOCOL_REVISION,
        "prompt_revision": PROMPT_REVISION,
        "runner": args.runner,
        "model": args.model,
        "effort": args.effort,
        "runner_version": blind.runner_version(args.runner),
        "driver_sha": digest(__file__),
        "blind_sha": digest(blind.__file__),
        "prompt_source_sha": digest(_settings().SOURCE),
        "scratch": str(scratch),
        "seat": args.seat,
        "prompts": prompts(args.seat),
        "prefix": [],
        "arms": {},
        "order": list(ARMS),
    }
    import random

    entropy = random.SystemRandom()
    entropy.shuffle(plan["order"])
    if args.canary:
        color = entropy.choice(("ultramarine", "vermilion", "turquoise"))
        drink = entropy.choice(("cocoa", "lemonade", "chamomile"))
        plan["source_sha"] = "synthetic"
        plan["target"] = "synthetic"
        plan["canary_markers"] = [color, drink]
        plan["prompts"] = {
            "first": f"Imagine a harmless fictional scene. Its lighthouse is "
            f"painted {color}, and its keeper drinks {drink}. "
            "Acknowledge both details in one sentence. Do not use tools.",
            "next": "Continue the harmless fictional scene from our previous turn. "
            "In one sentence, name the lighthouse's color and the keeper's "
            "drink, then say that the story is continuing. Do not use tools.",
            "headings": [],
        }
        plan["prefix"] = [{"stem": "canary", "pages": []}]
        plan["arms"] = {
            a: {
                "version": "synthetic",
                "segments": [{"stem": "canary", "version": "synthetic", "pages": []}],
            }
            for a in ARMS
        }
    else:
        manifest = read_json(args.original_kit / "manifest.json")
        if manifest["dirty"]:
            raise RuntimeError("baseline kit must be rendered from clean canon sources")
        segments = [i for i in manifest["chapters"] if i["kind"] == "chapter"]
        start_matches = [i for i, s in enumerate(segments) if s["stem"] == args.chapter]
        end_slug = args.through_chapter or args.chapter
        end_matches = [i for i, s in enumerate(segments) if s["stem"] == end_slug]
        if (
            len(start_matches) != 1
            or len(end_matches) != 1
            or start_matches[0] == 0
            or end_matches[0] < start_matches[0]
        ):
            raise RuntimeError(
                "target span must resolve in order and have a preceding segment"
            )
        start_index, end_index = start_matches[0], end_matches[0]
        targets = segments[start_index : end_index + 1]
        source_stems = [source.stem for source in args.candidate_source]
        target_stems = [item["stem"] for item in targets]
        if len(targets) > 1 and source_stems != target_stems:
            raise RuntimeError(
                "candidate sources must name every target chapter in reading order"
            )
        if len(targets) == 1 and len(source_stems) != 1:
            raise RuntimeError("a single target requires one candidate source")
        plan["source_sha"] = manifest["source_sha"]
        plan["target"] = args.chapter
        plan["targets"] = target_stems
        plan["hypothesis"] = args.hypothesis.read_text()
        plan["baseline_manifest"] = manifest
        originals = []
        for index, item in enumerate(segments[: end_index + 1]):
            name = item["stem"]
            shutil.copyfile(args.original_kit / item["file"], artifact / f"{name}.pdf")
            pages = render_pages(artifact / f"{name}.pdf", artifact / name)
            entry = {
                "stem": name,
                "title": item["title"],
                "pages": [str(p.relative_to(record)) for p in pages],
            }
            if start_index <= index <= end_index:
                originals.append({**entry, "version": "original"})
            else:
                plan["prefix"].append(entry)
        candidates = []
        if args.candidate_kit:
            candidate_manifest = read_json(args.candidate_kit / "manifest.json")
            if candidate_manifest["source_sha"] != manifest["source_sha"]:
                raise RuntimeError("candidate kit is not based on the baseline commit")
            candidate_segments = {
                item["stem"]: item
                for item in candidate_manifest["chapters"]
                if item["kind"] == "chapter"
            }
            candidate_order = [
                item["stem"]
                for item in candidate_manifest["chapters"]
                if item["kind"] == "chapter"
            ]
            if candidate_order != [item["stem"] for item in segments]:
                raise RuntimeError("candidate kit changed the reading order")
            if [item["stem"] for item in targets] != [
                candidate_segments.get(item["stem"], {}).get("stem") for item in targets
            ]:
                raise RuntimeError("candidate kit does not contain the target span")
            plan["candidate_manifest"] = candidate_manifest
            for original in originals:
                name = original["stem"]
                candidate_item = candidate_segments[name]
                target_pdf = artifact / f"candidate-{name}.pdf"
                shutil.copyfile(args.candidate_kit / candidate_item["file"], target_pdf)
                pages = render_pages(target_pdf, artifact / f"candidate-{name}")
                candidates.append(
                    {
                        "stem": name,
                        "title": candidate_item["title"],
                        "version": "candidate",
                        "pages": [str(p.relative_to(record)) for p in pages],
                    }
                )
        else:
            if len(originals) != 1:
                raise RuntimeError("a multi-chapter span requires --candidate-kit")
            shutil.copyfile(args.candidate_pdf, artifact / "candidate.pdf")
            pages = render_pages(artifact / "candidate.pdf", artifact / "candidate")
            candidates = [
                {
                    "stem": originals[0]["stem"],
                    "title": originals[0]["title"],
                    "version": "candidate",
                    "pages": [str(p.relative_to(record)) for p in pages],
                }
            ]
        plan["arms"] = {
            "a": {"version": "original", "segments": originals},
            "b": {"version": "original", "segments": originals},
            "c": {"version": "candidate", "segments": candidates},
        }
        source_dir = artifact / "candidate-sources"
        source_dir.mkdir()
        for source in args.candidate_source:
            shutil.copyfile(source, source_dir / source.name)
        shutil.copyfile(args.candidate_patch, record / "_candidate.patch")
    shutil.copyfile(__file__, artifact / "driver.py")
    shutil.copyfile(_settings().SOURCE, artifact / "reader_instrument.py")
    shutil.copyfile(blind.__file__, artifact / "shared_blind.py")
    if (
        digest(artifact / "driver.py") != plan["driver_sha"]
        or digest(artifact / "reader_instrument.py") != plan["prompt_source_sha"]
        or digest(artifact / "shared_blind.py") != plan["blind_sha"]
    ):
        raise RuntimeError("implementation changed during preparation")
    plan["hashes"] = tree_hashes(artifact)
    plan["hashes"] = {f"raw/artifact/{k}": v for k, v in plan["hashes"].items()}
    if (record / "_candidate.patch").exists():
        plan["hashes"]["_candidate.patch"] = digest(record / "_candidate.patch")
    write_json(record / "plan.json", plan)
    (record / "plan.sha256").write_text(digest(record / "plan.json") + "\n")
    print(f"Prepared: {record}", flush=True)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="quirework publish compare", description=__doc__
    )
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare", help="stage a forked reader comparison")
    prep.add_argument("--record-dir", type=Path, required=True)
    prep.add_argument("--runner", choices=("codex", "claude"), required=True)
    prep.add_argument("--model", required=True)
    prep.add_argument("--effort", default="xhigh")
    prep.add_argument("--seat", choices=("trade",), default="trade")
    prep.add_argument("--canary", action="store_true")
    prep.add_argument("--original-kit", type=Path)
    prep.add_argument("--chapter")
    prep.add_argument("--through-chapter")
    prep.add_argument("--candidate-kit", type=Path)
    prep.add_argument("--candidate-pdf", type=Path)
    prep.add_argument("--candidate-source", type=Path, action="append")
    prep.add_argument("--candidate-patch", type=Path)
    prep.add_argument("--hypothesis", type=Path)
    for name, text in (
        ("run", "run the prepared arms"),
        ("inspect", "each arm's progress and receipts"),
    ):
        action = sub.add_parser(name, help=text)
        action.add_argument("record_dir", type=Path)
        if name == "run":
            action.add_argument(
                "--progress",
                action="store_true",
                help="print each banked turn; default output is terminal status only",
            )
    args = parser.parse_args(argv)
    if args.action == "prepare":
        if not args.canary and not all(
            [
                args.original_kit,
                args.chapter,
                args.candidate_source,
                args.candidate_patch,
                args.hypothesis,
                args.candidate_kit or args.candidate_pdf,
            ]
        ):
            parser.error(
                "a book experiment needs both artifacts, source, patch and hypothesis"
            )
        prepare(args)
    elif args.action == "run":
        with (args.record_dir / ".lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    "another operator is running this experiment"
                ) from exc
            experiment = Experiment(args.record_dir, progress=args.progress)
            experiment.run()
            if experiment.state.get("canary_passed"):
                print(
                    "Canary: all three distinct forks retained parent history; parent unchanged"
                )
    else:
        experiment = Experiment(args.record_dir)
        experiment.verify()
        if experiment.state.get("checkpoint"):
            experiment.check_parent()
        print(json.dumps(experiment.state, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        sys.exit(2)
