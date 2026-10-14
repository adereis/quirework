"""Harness operations and receipts. Policy and artifact shape stay caller-owned."""

import base64
import json
import mimetypes
import os
import re
import time
from pathlib import Path

from quirework.providers import blind
from quirework.providers import quota
from quirework.providers import replies
from quirework.providers import models as workshop_drivers
from quirework.runs.storage import read
from quirework.runs.storage import write

# Re-exported on purpose: the run engine and the chapter comparison both reach
# the image audit through this module, which is the one they already hold.
# Spelled with the redundant alias so a lint pass reads it as an export rather
# than as an import nobody uses, which is how it was deleted once.
from quirework.providers.images import history_image_hashes as history_image_hashes

CAPABILITIES = {
    "codex": dict(
        images=True, files=True, resume=True, fork=True, filesystem="enforced"
    ),
    "claude": dict(
        images=True, files=True, resume=True, fork=True, filesystem="enforced"
    ),
    "agy": dict(
        images=False, files=True, resume=True, fork=False, filesystem="audited"
    ),
}
# Where a seat finds itself. It sits in the jail's private /tmp, so the path
# a seat reads back from `pwd` names neither this tool nor the operator's
# home: a seat with a shell looks around before it reads, and a directory
# called after the tool that runs it is a thread worth pulling.
VIRTUAL = Path("/tmp/work")
FALLBACK = "Falling back from WebSockets to HTTPS transport."


def validate(config, policy, turns, parent=None):
    harness = config["harness"]
    if harness not in CAPABILITIES:
        raise ValueError(f"unsupported harness: {harness}")
    caps = CAPABILITIES[harness]
    if policy.get("system_prompt") is not None and harness == "agy":
        raise ValueError("agy does not support replacing the system prompt")
    if parent and not caps["fork"]:
        raise ValueError(
            f"{harness} cannot provide a native fork; no replay substitute"
        )
    if policy["filesystem"] == "enforced" and caps["filesystem"] != "enforced":
        raise ValueError(
            f"{harness} supplies audited filesystem access, not enforced isolation"
        )
    for turn in turns:
        kinds = {item["kind"] for item in turn["materials"]}
        if policy["tools"] == "none" and (
            "file" in kinds or ("image" in kinds and not caps["images"])
        ):
            raise ValueError(
                f"{harness}: this delivery needs file tools; select them explicitly"
            )


def rows(path):
    if not path.exists():
        return []
    # Truncated rows are unresolved receipts, never silently discarded.
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def environment(harness):
    names = (
        "PATH",
        "LANG",
        "LC_ALL",
        "TERM",
        "TZ",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    )
    env = {k: os.environ[k] for k in names if k in os.environ}
    env.update(
        HOME=str(Path.home()),
        USER=os.environ.get("USER", ""),
        DISABLE_AUTOUPDATER="1",
        DISABLE_COMPACT="1",
    )
    if harness == "claude":
        oauth = read(Path.home() / ".claude/.credentials.json").get("claudeAiOauth", {})
        if (
            not oauth.get("accessToken")
            or oauth.get("expiresAt", 0) / 1000 < time.time() + 300
        ):
            raise RuntimeError(
                "Claude access token unavailable/expiring; renew outside the run"
            )
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth["accessToken"]
    return env


def command(config, policy, mode, sid, prompt, images, files):
    name, model, effort = (config[k] for k in ("harness", "model", "effort"))
    # See engine.pinned_config: a resumed session keeps its served version.
    model = config.get("model_pinned") or model
    if files:
        prompt += "\nUse only the supplied paths. For a PDF or image, inspect its pages/images; do not extract its text.\n"
    if images and name == "agy":
        prompt += "\nInspect these images with your file-viewing tool:\n" + "\n".join(
            str(v) for _, v in images
        )
    elif images:
        prompt += "\nThe attached images are the supplied material, in order. Inspect them directly.\n"
    if policy["tools"] != "research":
        prompt += (
            "\nUse no web or external services. Work only with the supplied material.\n"
        )
    if policy["tools"] == "none":
        prompt += "\nUse no tools. Return your answer directly.\n"
    if name == "codex":
        args = ["codex", "exec"]
        if mode != "start":
            args += [mode, sid]
        else:
            args += [
                "-s",
                "workspace-write" if policy["tools"] == "write" else "read-only",
            ]
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
            "project_doc_max_bytes=0",
            "-c",
            "features.multi_agent=false",
            "-c",
            'sandbox_mode="workspace-write"'
            if policy["tools"] == "write"
            else 'sandbox_mode="read-only"',
            "-c",
            'web_search="live"'
            if policy["tools"] == "research"
            else 'web_search="disabled"',
            *blind.CODEX_SEAT,
        ]
        if policy.get("system_prompt") is not None:
            args += [
                "-c",
                "model_instructions_file="
                + json.dumps(str(VIRTUAL / "system-prompt.md")),
            ]
        if policy["tools"] == "none":
            # Every tool feature the validated Codex offers is named off, not
            # left to its default. 0.156 moved the image viewer from
            # `tools.view_image` to a feature flag and ignored the old key, so
            # a seat declared tool-free kept the viewer on; the launch refused
            # only because Codex warned about the key. An unknown key here
            # stops the run, which is how the next rename will be caught.
            # `apps` and `image_generation` are off for every seat, above,
            # not only this one.
            for feature in (
                "shell_tool",
                "unified_exec",
                "view_image",
                "browser_use",
                "browser_use_external",
                "in_app_browser",
                "computer_use",
            ):
                args += ["-c", f"features.{feature}=false"]
        if policy.get("context_window"):
            args += ["-c", f"model_context_window={policy['context_window']}"]
            if not policy["allow_compaction"]:
                args += [
                    "-c",
                    f"model_auto_compact_token_limit={policy['context_window']}",
                    # Without the scope the limit above is clamped to 90% of
                    # the window and compaction fires anyway; see
                    # workshop_drivers.window_flags for the whole reason.
                    "-c",
                    'model_auto_compact_token_limit_scope="body_after_prefix"',
                ]
        for _, virtual in images:
            args += ["-i", str(virtual)]
        return args + ["--json", "-"], prompt.encode(), prompt
    if name == "claude":
        # Codex is told not to compact through model_auto_compact_token_limit;
        # this is Claude's equivalent, and without it compaction here was only
        # ever detected after the fact, never prevented. Pushing the threshold
        # to the model's whole window makes a run that outgrows its context
        # fail loudly instead of quietly losing the early chapters a read
        # depends on. Built before the arg list: a nested list literal inside
        # it hides the blindness constants from the launch guard's scanner.
        autocompact = (
            ["--autocompact", str(policy["context_window"])]
            if policy.get("context_window") and not policy["allow_compaction"]
            else []
        )
        toolset = {
            "none": "Read",
            "read": "Read",
            "write": "Read,Write,Edit,Glob,Grep",
            "research": "Read,WebSearch,WebFetch",
        }[policy["tools"]]
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
            toolset,
            "--allowedTools",
            toolset,
            "--disable-slash-commands",
            *autocompact,
            *blind.BLIND_SETTINGS,
            *blind.NO_SETTING_SOURCES,
            *blind.NO_MCP,
        ]
        if policy.get("system_prompt") is not None:
            args += [
                "--system-prompt",
                policy["system_prompt"],
                "--exclude-dynamic-system-prompt-sections",
            ]
        # Every launch names the model: a resume without it runs the CLI's
        # default, and a resume with the family alone runs the newest one.
        if mode == "start":
            args += ["--model", model, "--session-id", sid]
        else:
            args += ["--resume", sid, "--model", model]
            if mode == "fork":
                args += ["--fork-session"]
        content = [dict(type="text", text=prompt)]
        for physical, _ in images:
            content.append(
                dict(
                    type="image",
                    source=dict(
                        type="base64",
                        media_type=mimetypes.guess_type(physical.name)[0],
                        data=base64.b64encode(physical.read_bytes()).decode(),
                    ),
                )
            )
        payload = (
            json.dumps(dict(type="user", message=dict(role="user", content=content)))
            + "\n"
        )
        return args, payload.encode(), prompt
    args = [
        "stdbuf",
        "-oL",
        "-eL",
        "agy",
        "--print=",
        "--input-format",
        "stream-json",
        "--model",
        model,
        "--effort",
        effort,
        "--disable-slash-commands",
        "--output-format",
        "stream-json",
        "--print-timeout",
        "60m",
        "--dangerously-skip-permissions",
    ]
    if mode != "start":
        args += ["--conversation", sid]
    payload = (
        json.dumps(
            dict(
                event="user",
                message=dict(role="user", content=[dict(type="text", text=prompt)]),
            )
        )
        + "\n"
    )
    return args, payload.encode(), prompt


def canary(config, policy, label):
    """Prove this seat's flags blind on the installed runner (``canary``).

    The twin is this module's own command with the canary's prompt, model
    and session, under the seat's tool policy. A seat with a system prompt
    keeps the flags that carry one, with a neutral prompt in its place; the
    Codex one is a file under ``VIRTUAL``, so the canary's work directory is
    bound there as a seat's workspace is.
    """
    from quirework.providers import canary as canaries

    name = config["harness"]
    twin_policy = {"tools": policy["tools"]}
    if policy.get("system_prompt") is not None:
        twin_policy["system_prompt"] = canaries.SYSTEM_PROMPT

    def build(prompt, model, workdir, session):
        twin_config = {"harness": name, "model": model, "effort": canaries.EFFORT}
        args, payload, _ = command(
            twin_config, twin_policy, "start", session, prompt, [], []
        )
        if name == "codex" and "system_prompt" in twin_policy:
            return canaries.Twin(
                args,
                payload,
                binds=((workdir, VIRTUAL),),
                files={"system-prompt.md": canaries.SYSTEM_PROMPT},
            )
        return canaries.Twin(args, payload)

    # agy keeps its web tools under any flags; `receipt` reads its calls
    # afterwards and refuses a run that used them outside a research policy.
    contract = canaries.Contract(
        web=policy["tools"] == "research", web_audited=name == "agy"
    )
    return canaries.require(label, name, build, contract)


def billing(harness):
    """How a seat on this harness is paid for, from the credential's kind.

    Only the kind is read, never the secret. It matters because the two
    kinds cost differently and only one is priced here: a Claude receipt's
    cost is a list-price equivalent under a subscription, and a Codex seat
    on an API key is metered spend that no receipt prices.
    """
    if harness == "claude":
        return "subscription (OAuth); cost_usd is a list-price equivalent"
    if harness == "codex":
        try:
            mode = read(Path.home() / ".codex/auth.json").get("auth_mode")
        except (OSError, ValueError):
            mode = None
        if mode == "apikey":
            return "API key: metered spend, not priced by this receipt"
        if mode == "chatgpt":
            return "ChatGPT plan quota"
    return "unknown"


def launch_context(config, policy, workspace, bus=None, canary=None):
    name = config["harness"]
    if error := blind.backend_error():
        raise RuntimeError(error)
    _, missing = blind.runner_binds([name])
    if missing:
        raise RuntimeError("missing harness prerequisites: " + "; ".join(missing))
    env = environment(name)
    jail = blind.jail_argv(
        workspace,
        runners=[name],
        bind_credentials=name != "claude",
        bus=bus,
        canary=canary,
    )
    jail[jail.index("--chdir") + 1] = str(VIRTUAL)
    jail += ["--bind", str(workspace), str(VIRTUAL), "--setenv", "PWD", str(VIRTUAL)]
    for name_ in ("materials", "library"):
        p = workspace / name_
        p.mkdir(exist_ok=True)
        jail += ["--ro-bind", str(p), str(p), "--ro-bind", str(p), str(VIRTUAL / name_)]
    for path in policy.get("read_roots", []):
        jail += ["--ro-bind", path, path]
    private = blind.state_root(workspace) / name
    if name == "claude":
        cfg = private / "runtime-config.json"
        if not cfg.exists():
            write(cfg, {"hasCompletedOnboarding": True, "theme": "dark"})
        jail += ["--bind", str(cfg), str(Path.home() / ".claude.json")]
    return jail, env, private


def receipt(config, policy, record, private, established=None):
    name = config["harness"]
    stream = rows(record / "events.jsonl")
    notices = []
    calls = []
    efforts = set()
    # Only Claude's record says why each response stopped and marks what
    # the harness itself wrote, so elsewhere neither can be evidenced.
    reply = dict(output_limit=None, harness_text=[])
    if name == "codex":
        ids = {r["thread_id"] for r in stream if r.get("type") == "thread.started"}
        complete = [r for r in stream if r.get("type") == "turn.completed"]
        answers = []
        for row in stream:
            if row.get("type") != "item.completed":
                continue
            item = row["item"]
            kind = item["type"]
            if kind == "agent_message":
                answers.append(item["text"])
            elif kind == "error" and item.get("message", "").startswith(FALLBACK):
                notices.append(item["message"])
            elif kind != "reasoning":
                if kind == "error":
                    raise RuntimeError(
                        "unrecognized Codex error: " + item.get("message", "")[:200]
                    )
                calls.append(item)
        if len(ids) != 1 or not complete or not answers:
            raise RuntimeError("Codex receipt is incomplete or ambiguous")
        sid = ids.pop()
        answer = answers[-1]
        usage = complete[-1].get("usage")
        paths = list((private / "sessions").rglob(f"*{sid}.jsonl"))
        if len(paths) != 1:
            raise RuntimeError("missing or ambiguous native Codex session")
        native = rows(paths[0])
        contexts = [r["payload"] for r in native if r.get("type") == "turn_context"]
        if not contexts or any(
            c.get("model") != config["model"] or c.get("effort") != config["effort"]
            for c in contexts
        ):
            raise RuntimeError("Codex model/effort drift")
        expected_sandbox = (
            "workspace-write" if policy["tools"] == "write" else "read-only"
        )
        if any(
            c.get("sandbox_policy", {}).get("type") != expected_sandbox
            for c in contexts
        ):
            raise RuntimeError(
                "Codex served sandbox policy differs from the declared task"
            )
        model = contexts[-1]["model"]
        windows = [
            r["payload"].get("model_context_window", 0)
            for r in native
            if r.get("type") == "event_msg"
            and r.get("payload", {}).get("type") == "task_started"
        ]
        window = windows[-1] if windows else None
        parent = next(
            (
                r["payload"].get("forked_from_id")
                for r in native
                if r.get("type") == "session_meta"
            ),
            None,
        )
        peak = max(
            (
                r["payload"]
                .get("info", {})
                .get("last_token_usage", {})
                .get("input_tokens", 0)
                for r in native
                if r.get("type") == "event_msg"
                and r.get("payload", {}).get("type") == "token_count"
                and r["payload"].get("info")
            ),
            default=0,
        )
    elif name == "claude":
        result = [r for r in stream if r.get("type") == "result"]
        if (
            len(result) != 1
            or result[0].get("is_error")
            or result[0].get("subtype") != "success"
        ):
            said = result[0].get("result", "") if result else "missing result"
            raise RuntimeError("Claude did not complete: " + str(said)[:250])
        result = result[0]
        sid = result["session_id"]
        answer = result.get("result", "")
        models = {
            r["message"]["model"]
            for r in stream
            if r.get("type") == "assistant"
            and r.get("message", {}).get("model") not in (None, "<synthetic>")
        }
        if len(models) != 1:
            raise RuntimeError("missing or mixed Claude model identity")
        model = models.pop()
        expected = established or config.get("model_pinned") or config["model"]
        if not workshop_drivers._model_matches(expected, model):
            raise RuntimeError(f"Claude model drift: {model}, expected {expected}")
        usage = result.get("modelUsage", {})
        # The messages carry the served id, which may be dated
        # (`...-4-5-20251001`), while the usage table may key the same model
        # by its undated version. Match them the way a request is matched to
        # what served it, or an answered turn is refused for want of usage.
        served = usage.get(model) or next(
            (v for k, v in usage.items() if workshop_drivers._model_matches(k, model)),
            {},
        )
        if not served.get("outputTokens"):
            raise RuntimeError("Claude lacks served-model usage evidence")
        for row in stream:
            if row.get("type") == "assistant":
                calls += [
                    b
                    for b in row.get("message", {}).get("content", [])
                    if b.get("type") in ("tool_use", "server_tool_use")
                ]
        paths = list((private / "projects").rglob(f"{sid}.jsonl"))
        if len(paths) != 1:
            raise RuntimeError("missing or ambiguous native Claude session")
        native = rows(paths[0])
        # The result row holds only the last response. A reply that reached
        # the output ceiling was resumed in a further response, so it is
        # rebuilt from the stream, and the native session says where.
        answer, reply = replies.claude(stream, native)
        if reply["output_limit"] is not None:
            reply["output_limit"]["ceiling"] = served.get("maxOutputTokens")
        window = None
        parent = None
        # The session states each answer's effort and token use, so both
        # are read from it rather than taken on trust from the launch
        # arguments. An older CLI that records no effort falls back to the
        # arguments and the receipt says so.
        efforts = {
            r["effort"]
            for r in native
            if r.get("type") == "assistant" and r.get("effort")
        }
        if efforts and efforts != {config["effort"]}:
            raise RuntimeError(
                f"Claude effort drift: {sorted(efforts)}, expected {config['effort']}"
            )
        peak = max(
            (
                sum(
                    (r["message"].get("usage") or {}).get(k, 0) or 0
                    for k in (
                        "input_tokens",
                        "cache_read_input_tokens",
                        "cache_creation_input_tokens",
                    )
                )
                for r in native
                if r.get("type") == "assistant" and isinstance(r.get("message"), dict)
            ),
            default=None,
        )
        init = [
            r
            for r in stream
            if r.get("type") == "system" and r.get("subtype") == "init"
        ]
        if (
            len(init) != 1
            or init[0].get("mcp_servers")
            or init[0].get("output_style") != "default"
        ):
            raise RuntimeError("Claude MCP/style configuration drift")
    else:
        results = [r["result"] for r in stream if r.get("event") == "result"]
        ids = {r["conversation_id"] for r in stream if r.get("conversation_id")}
        if (
            len(results) != 1
            or len(ids) != 1
            or results[0].get("status") not in ("SUCCESS", "COMPLETED", "DONE")
        ):
            raise RuntimeError("agy has no unique completed result")
        result = results[0]
        sid = ids.pop()
        model = result.get("model") or next(
            (
                r.get("init", {}).get("model")
                for r in stream
                if r.get("event") == "init"
            ),
            None,
        )
        if model != config["model"]:
            raise RuntimeError("agy model receipt missing or changed")
        answer, usage = result.get("response", ""), result.get("usage")
        paths = list(
            private.glob(
                f"antigravity-cli/brain/{sid}/.system_generated/logs/transcript_full.jsonl"
            )
        )
        if len(paths) != 1:
            raise RuntimeError("agy native transcript missing or ambiguous")
        native = rows(paths[0])
        for row in stream:
            su = row.get("step_update") or {}
            if su.get("step_type") == "tool" and su.get("state") == "ACTIVE":
                calls.append(
                    dict(
                        name=su.get("tool_name"),
                        parameters=su.get("tool_info", {}).get("parameters", {}),
                    )
                )
        window = None
        parent = None
        peak = None
    if not answer.strip():
        raise RuntimeError("empty completed answer")
    if established and model != established:
        raise RuntimeError("reader model changed during a session")
    from quirework.runs.observe import compaction_markers

    compactions = compaction_markers(stream) + compaction_markers(native)
    red_flags = (
        [
            "Compaction occurred: preserve the result and review model, "
            "effective context, supplied material and task scope."
        ]
        if compactions
        else []
    )
    red_flags += replies.flags(reply)
    red_flags += bus_flags(record)
    minimum = policy.get("minimum_context_window", 0)
    if minimum and (window is None or window < minimum):
        raise RuntimeError("required effective context window is not evidenced")
    if policy["tools"] == "none" and calls:
        raise RuntimeError("tool use violates the declared no-tools policy")
    if name == "codex":
        native_calls = [
            r["payload"]
            for r in native
            if r.get("type") == "response_item"
            and r.get("payload", {}).get("type", "").endswith("_call")
        ]
        if policy["tools"] == "none" and native_calls:
            raise RuntimeError("native history records forbidden tool calls")
        if not usage or not usage.get("output_tokens"):
            raise RuntimeError("Codex has no output-token receipt")
    for call in calls:
        tool = call.get("name") or call.get("type", "")
        if re.search(
            r"mcp|subagent|send_message|send_email|manage_inbox|schedule", tool, re.I
        ):
            raise RuntimeError(
                "connected-service, delegation or messaging tool is outside the run policy"
            )
        params = call.get("parameters", call.get("input", {}))
        command_text = str(call.get("command", ""))
        if tool == "run_command":
            command_text += json.dumps(params)
        if policy["tools"] != "research":
            if re.search(
                r"web_search|web__run|WebSearch|WebFetch|search_web|read_url|browser",
                tool,
            ) or re.search(r"https?://|\b(curl|wget)\b", command_text):
                raise RuntimeError("network use violates the declared policy")
        if name == "agy":
            for key, value in params.items() if isinstance(params, dict) else []:
                if (
                    isinstance(value, str)
                    and ("path" in key.lower() or "file" in key.lower())
                    and value.startswith("/")
                ):
                    path = Path(value)
                    allowed = [VIRTUAL, *map(Path, policy["read_roots"])]
                    if not any(path.is_relative_to(p) for p in allowed):
                        raise RuntimeError(
                            "agy accessed a path outside the declared material"
                        )
    try:
        launched = read(record / "launch.json")
    except (OSError, ValueError):
        launched = {}
    cost = (
        round(sum(u.get("costUSD", 0) or 0 for u in (usage or {}).values()), 4)
        if name == "claude"
        else None
    )
    return dict(
        session_id=sid,
        model=model,
        effort=config["effort"],
        effort_evidence="native turn context"
        if name == "codex"
        else "native session"
        if name == "claude" and efforts
        else "CLI selection",
        billing=launched.get("billing", "unknown"),
        cost_usd=cost,
        filesystem=CAPABILITIES[name]["filesystem"],
        tool_calls=len(calls),
        allow_compaction=policy["allow_compaction"],
        compaction_markers=compactions,
        compaction_observed=bool(compactions),
        compaction_policy_satisfied=policy["allow_compaction"] or not compactions,
        red_flags=red_flags,
        output_limit=reply["output_limit"],
        harness_text=reply["harness_text"],
        effective_window=window,
        peak_input=peak,
        usage=usage,
        forked_from=parent,
        notices=notices,
        rollout=str(paths[0]),
        answer=answer,
    )


def bus_flags(record):
    """The red flag for a session bus proxied into this turn's jail, if any."""
    launch = record / "launch.json"
    bus = read(launch).get("session_bus") if launch.exists() else None
    return [blind.Bus.flag(bus)] if bus else []


def session_paths(name, private, sid):
    """The native session file(s) a resume would continue from.

    The lookup is the runner table's, the one a resume's guard uses, so a
    refused turn is rewound off the same files a resume would continue.
    Returns [] when the session was never created.
    """
    return blind.session_paths(Path(private), name, sid)


def quota_stop(name, record):
    """A provider quota limit in this record, of either shape, else None.

    Two shapes, and they take different recoveries, which is why the phase
    travels with the detail. ``refused`` means the provider declined before
    running the model, so nothing was generated and the identical turn can be
    rewound and resubmitted. ``interrupted`` means the model ran and the wall
    arrived partway through, so the turn's work is banked in the session and
    the only recovery that keeps it is to resume that session.

    A transport fault is ambiguous and is neither. ``quota`` owns telling
    them apart; this wrapper exists so callers inside the runner keep reading
    one module for harness facts.
    """
    return quota.classify(name, record)


def quota_rejection(name, record):
    """Structured detail when a turn was refused *before the model ran*.

    Narrower than ``quota_stop`` on purpose: this is the only condition under
    which a turn may be resubmitted verbatim, so the rewind-and-resubmit path
    asks for exactly it and nothing broader. It also insists on the older
    receipt that no answer exists, so a stream that carries a limit notice
    and real output is never mistaken for one that carries neither.
    """
    if not rejected_without_answer(name, record):
        return None
    detail = quota.classify(name, record)
    if not detail or detail["phase"] != "refused":
        return None
    return detail


def rejected_without_answer(name, record):
    stream = rows(record / "events.jsonl")
    if name == "claude":
        results = [r for r in stream if r.get("type") == "result"]
        # An account limit is rendered as an assistant text row carrying the
        # notice, so "no assistant row" is not the test for "no answer" -- it
        # trapped a six-seat run whose other four seats had finished. The
        # served-model receipt is: a rejection reports no output tokens at all,
        # and `modelUsage` is empty because the model never ran. A real answer
        # cannot satisfy that and an error result together.
        return (
            len(results) == 1
            and bool(results[0].get("is_error"))
            and not any(
                v.get("outputTokens", 0)
                for v in results[0].get("modelUsage", {}).values()
            )
        )
    if name == "agy":
        results = [r["result"] for r in stream if r.get("event") == "result"]
        return (
            len(results) == 1
            and results[0].get("status") == "ERROR"
            and not results[0].get("response")
            and results[0].get("usage", {}).get("output_tokens") == 0
            and not any(
                r.get("step_update", {}).get("step_type") == "tool" for r in stream
            )
        )
    failed = [r for r in stream if r.get("type") == "turn.failed"]
    return (
        len(failed) == 1
        and failed[0].get("error", {}).get("code")
        in ("rate_limit_exceeded", "insufficient_quota")
        and not any(r.get("type") == "item.completed" for r in stream)
    )
