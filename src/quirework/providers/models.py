"""Harness adapters for the prose workshop; procedure: docs/workflows.md.

Adapters transport a file-based assignment, not a fixed writing instrument.
Credentials are used at launch and never serialized into experiment records.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from quirework.project import current_project
from quirework.providers import blind, quota

HARNESSES = ("codex", "claude", "agy")


def read_json(path):
    return json.loads(Path(path).read_text())


def json_rows(path):
    """Parse complete records. A truncated last record is a visible failure."""
    return [
        json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()
    ]


# A plan names a model family (`opus`, `terra`, `gemini-pro`) and preparation
# picks the newest member the harness's own catalog lists, so a release needs
# no change here and a plan written today picks up next month's model. The
# resolved id is frozen with the family recorded beside it; a name with a
# version number in it pins instead. Keep no list of model versions in this
# module: where a harness has no catalog, as Claude Code has none, its own
# alias passes through and the receipt verifies what was served.
CLAUDE_ALIASES = ("fable", "opus", "sonnet", "haiku")
AGY_EFFORTS = ("low", "medium", "high")


def _tokens(name):
    return [t for t in re.split(r"[-_\s]+", str(name).strip().lower()) if t]


def _words(name):
    """The family words of a model name: every token that is not a version."""
    return [t for t in _tokens(name) if not t[0].isdigit()]


def _version(name):
    return tuple(
        int(n) for t in _tokens(name) if t[0].isdigit() for n in re.findall(r"\d+", t)
    )


def is_family(name):
    """Whether a model name names a family rather than a version: no digit."""
    return bool(_tokens(name)) and not re.search(r"\d", str(name))


def families(slugs):
    """The family names a catalog offers, for a refusal to list."""
    return sorted({"-".join(_words(s)) for s in slugs if _words(s)})


def newest_in_family(family, slugs):
    """The newest catalog slug in ``family``, or None when nothing matches.

    A slug is in the family when every family word is one of its words.
    Among those, the members with the fewest words win, so ``terra`` means
    ``gpt-5.6-terra`` and not a later ``gpt-6-terra-mini``. Two different
    word sets left tied are ambiguous, as ``gemini`` is between Flash and
    Pro, and the refusal names both. Inside the family the highest version
    wins, compared as numbers, so 5.10 is newer than 5.4.
    """
    wanted = set(_words(family))
    members = [s for s in slugs if wanted <= set(_words(s))]
    if not members:
        return None
    fewest = min(len(_words(s)) for s in members)
    members = [s for s in members if len(_words(s)) == fewest]
    shapes = families(members)
    if len(shapes) > 1:
        raise ValueError(
            f"model {family!r} names more than one family ({', '.join(shapes)}); "
            "name one of them"
        )
    return max(members, key=lambda s: (_version(s), s))


def model_text(config):
    """A seat's model as status prints it: the id, and the family it came from."""
    asked = config.get("requested_model")
    model = config.get("model")
    if asked and model and str(asked).strip().lower() != model:
        return f"{model} (from {asked})"
    return model


def resolve_claude(model):
    """What a person calls a Claude model, as Claude Code accepts it.

    ``opus-5.5`` is what a person says and ``claude-opus-5-5`` is what the
    CLI takes: Claude ids spell versions with dashes, never dots. A family
    alias passes through, because the CLI resolves it to its latest model
    and the receipt records which one answered. Anything that is neither
    is refused here rather than at launch, where it would come back as
    ``unrecognized_model`` after the jail was already built.
    """
    text = str(model).strip().lower()
    if not text:
        raise ValueError("claude model must not be empty")
    bare = text.removeprefix("claude-").replace(".", "-")
    if bare in CLAUDE_ALIASES:
        return bare
    if not is_family(bare) and (
        text.startswith("claude-") or bare.split("-")[0] in CLAUDE_ALIASES
    ):
        return f"claude-{bare}"
    raise ValueError(
        f"claude model {model!r} is not a name Claude Code accepts. Use a family "
        f"alias ({', '.join(CLAUDE_ALIASES)}), which the CLI resolves to its "
        "latest model, or a version such as opus-5.5 or claude-opus-5-5."
    )


def harness_for(model):
    """The harness a model name belongs to when the operator names none.

    Only Claude names can be recognized without a catalog: its family
    aliases and its ``claude-`` ids. Everything else goes to Codex, whose
    catalog then resolves the family or refuses it and lists the families
    it offers. Antigravity is never inferred, because its effort is part of
    the model id and has to be chosen rather than defaulted.
    """
    text = str(model).strip().lower()
    bare = text.removeprefix("claude-").replace(".", "-")
    if text.startswith("claude-") or bare.split("-")[0] in CLAUDE_ALIASES:
        return "claude"
    return "codex"


CODEX_CACHE = ".codex/models_cache.json"


def _codex_live():
    """``(models, None)`` from ``codex debug models``, or ``(None, why)``."""
    try:
        p = subprocess.run(
            ["codex", "debug", "models"],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if p.returncode:
        return None, (p.stderr.strip() or f"exit status {p.returncode}")[-300:]
    try:
        return json.loads(p.stdout).get("models", []), None
    except (json.JSONDecodeError, AttributeError) as exc:
        return None, f"unreadable output: {exc}"


def codex_catalog():
    """Codex's model list, and a line saying where it was read.

    A catalog the project configures as ``providers.codex.catalog`` is used
    as given: that is a pinned choice. Otherwise Codex is asked, because the
    file it keeps at ``~/.codex/models_cache.json`` is written only by a
    real Codex session and can trail the provider by days. Resolved against
    that file, an exact slug the provider already served was refused, and
    its family resolved to the previous generation without a word: a model
    substitution at launch. ``codex debug models`` refreshes when its copy
    is stale and prints the result, though it does not write the file back.
    The file is the fallback when Codex cannot be asked, and the source line
    then says how old it is, because an old catalog is how a newer model
    goes unseen.
    """
    project = current_project()
    if project.get("providers.codex.catalog") is not None:
        path = project.path("providers.codex.catalog")
        if not path.exists():
            raise ValueError(f"the configured Codex catalog {path} does not exist")
        return read_json(path).get("models", []), f"{path} (configured)"
    models, failure = _codex_live()
    if models is not None:
        return models, "codex debug models"
    cache = Path.home() / CODEX_CACHE
    if not cache.exists():
        raise ValueError(
            f"codex debug models failed ({failure}) and there is no cached "
            f"catalog at {cache}; check that Codex is logged in with "
            "`codex login status`"
        )
    data = read_json(cache)
    source = (
        f"{cache}, fetched {data.get('fetched_at') or 'at an unrecorded time'}, "
        f"because codex debug models failed: {failure}"
    )
    print(f"models: resolving against {source}", file=sys.stderr)
    return data.get("models", []), source


def resolve(harness, requested, effort, resolved_model=None):
    if harness not in HARNESSES:
        raise ValueError(f"unknown harness: {harness}")
    if not shutil.which(harness):
        raise ValueError(f"harness not installed: {harness}")
    model = str(resolved_model or requested).strip().lower()
    evidence = {"requested_model": requested, "requested_effort": effort}
    if harness == "codex":
        model = re.sub(r"^(gpt-\d+)-(\d+)", r"\1.\2", model)
        models, source = codex_catalog()
        evidence["catalog_source"] = source
        item = next((m for m in models if m.get("slug") == model), None)
        # Hidden entries are Codex's own internals; a family never lands on one.
        listed = [
            m["slug"] for m in models if m.get("slug") and m.get("visibility") != "hide"
        ]
        if item is None and is_family(model):
            slug = newest_in_family(model, listed)
            item = next((m for m in models if m.get("slug") == slug), None)
        if item is None:
            raise ValueError(
                f"Codex catalog does not contain {model!r}; it offers the "
                f"families {', '.join(families(listed))}, which resolve to "
                "their newest version, or an exact slug"
            )
        model = item["slug"]
        levels = [r["effort"] for r in item.get("supported_reasoning_levels", [])]
        if effort not in levels:
            raise ValueError(f"{model} supports {levels}, not {effort!r}")
        evidence["context_window"] = item.get("context_window")
        # `context_window` is only the default a session opens with. The
        # ceiling a run may raise it to is `max_context_window`, of which the
        # provider hands back `effective_context_window_percent`. Carry all
        # three so a plan asks for the largest window this model actually has
        # instead of a number someone wrote down once.
        evidence["max_context_window"] = item.get("max_context_window")
        evidence["effective_context_window_percent"] = item.get(
            "effective_context_window_percent"
        )
        evidence["catalog_entry"] = {
            k: item.get(k)
            for k in (
                "slug",
                "supported_reasoning_levels",
                "context_window",
                "max_context_window",
                "effective_context_window_percent",
            )
        }
    elif harness == "agy":
        if effort not in AGY_EFFORTS:
            raise ValueError(
                "agy supports low, medium, high; explicitly map the requested effort"
            )
        p = subprocess.run(
            ["agy", "models"], capture_output=True, text=True, timeout=45, check=False
        )
        if p.returncode:
            raise ValueError(f"agy model lookup failed: {p.stderr[-500:]}")
        available = [
            line.split()[0]
            for line in p.stdout.splitlines()
            if line.startswith(("gemini-", "claude-", "gpt-"))
        ]
        # A slug carries its effort as a suffix (`gemini-3.1-pro-high`); a
        # family is resolved on the model with the suffix taken off, and the
        # seat's effort then completes the slug.
        suffix = re.compile(r"-(" + "|".join(AGY_EFFORTS) + r")$")
        bases = sorted({suffix.sub("", s) for s in available})
        if model not in available and is_family(model):
            model = newest_in_family(model, bases) or model
        if model not in available and f"{model}-{effort}" in available:
            model = f"{model}-{effort}"
        if model not in available:
            offered = sorted(
                m.group(1)
                for s in available
                if (m := suffix.search(s)) and suffix.sub("", s) == model
            )
            if offered:
                raise ValueError(
                    f"agy offers {model} at efforts {', '.join(offered)}, "
                    f"not {effort!r}"
                )
            raise ValueError(
                f"agy model unavailable: {model!r}; it offers the families "
                f"{', '.join(families(bases))}, which resolve to their newest "
                "version, or an exact slug from `agy models`"
            )
        evidence["catalog_entry"] = model
    else:
        model = resolve_claude(model)
        if effort not in ("low", "medium", "high", "xhigh", "max"):
            raise ValueError(f"invalid Claude effort: {effort}")
        evidence["catalog_entry"] = (
            "family alias; the CLI picks its latest, and the receipt records "
            "the version served"
            if model in CLAUDE_ALIASES
            else "explicit model; verify served identity after launch"
        )
    evidence.update(harness=harness, model=model, effort=effort)
    return evidence


# Claude publishes no catalog, so its ceiling is a constant. Kept equal to
# runs/engine.py's CLAUDE_MAX_WINDOW rather than imported, because that module
# pulls the whole run engine in behind it and this one is reached from the
# jail.
CLAUDE_MAX_WINDOW = 1_000_000


def window_flags(config):
    """Harness flags pinning a run to its model's whole window, no compaction.

    Two settings doing one job. `model_context_window` lifts the session off
    the default the catalog calls `context_window` -- 272,000 for gpt-6-astra
    -- to the ceiling it calls `max_context_window`, which is 872,000. Setting
    the auto-compact limit to the same number means a run that outgrows its
    context fails loudly at the ceiling instead of quietly compacting below
    it, which is what `providers/harness.py` has always done and what the blind
    drivers do with their own constant.

    Neither setting was here or in `review.py`, so every workshop and review
    run took the session default and could compact freely; `_no_compaction`
    then caught it after the fact, when the spend was already made. A review
    resume compacted for exactly this reason on 2026-09-10. The resume is the
    turn that needs the ceiling most, because it inherits the whole prior
    transcript before the new delta is added, so the run most likely to
    compact was the one configured lowest.

    `docs/workflows.md` makes compaction a red flag for every
    harness-invoking SOP, and a red flag that can only be raised afterwards is
    a worse instrument than one that prevents the event.
    """
    harness = config["harness"]
    if harness == "claude":
        return ["--autocompact", str(CLAUDE_MAX_WINDOW)]
    if harness != "codex":
        return []
    # The ceiling only. `context_window` is the session default this helper
    # exists to escape, so accepting it as a substitute emits the very number
    # the guard refuses: a catalog entry carrying only `context_window`
    # silently produced 272,000 for both settings.
    window = config.get("max_context_window")
    if not window:
        raise ValueError(
            f"no context ceiling resolved for {config.get('model')!r}; refresh "
            "the Codex catalog rather than letting the run take the session "
            "default (docs/workflows.md, 'Compaction is a major design "
            "warning')"
        )
    return [
        "-c",
        f"model_context_window={window}",
        "-c",
        f"model_auto_compact_token_limit={window}",
        # THE SETTING THAT MAKES THE LIMIT ABOVE MEAN ANYTHING. Without a
        # scope, Codex applies the total-mode 90 percent threshold clamp, so
        # an auto-compact limit set to the window still fires at nine tenths
        # of it and compaction is not disabled at all: for an 872,000 ceiling
        # at 95 percent usable, the trigger sits at 784,800 rather than
        # 828,400. `body_after_prefix` reads the explicit limit directly. The
        # operator's own ~/.codex/config.toml carries this, researched and
        # cited against the Codex source, and every launcher here passes
        # --ignore-user-config, so all of that was being discarded and none
        # of it re-supplied. Version-specific behaviour, not an off switch:
        # recheck on upgrades (established for 0.153.4; 0.154.0 installed).
        "-c",
        'model_auto_compact_token_limit_scope="body_after_prefix"',
    ]


# Harnesses whose compaction behaviour a run can actually pin. Antigravity is
# absent because its CLI exposes no context or compaction setting at all: not
# a ceiling, not a trigger, not an off switch. A seat there takes the session
# default, and the only guard available is the one that reads the stream
# afterwards and reports that it happened. Named here rather than left as a
# silent `return []`, because that same empty list otherwise means both "this
# harness needs no flags" and "this harness cannot be steered", and an
# operator reading a mixed room's evidence has to tell those apart.
PINNED_WINDOW = ("claude", "codex")


# What a seat may do beyond reading its packet. `write` is the floor,
# `research` adds the web, and `workbench` adds a shell to that.
TOOL_POLICIES = ("write", "research", "workbench")


def window_assurance(harness):
    """Whether a run can prevent compaction here, or only detect it after."""
    return "enforced" if harness in PINNED_WINDOW else "detected"


def command(config, workspace, prompt, *, manual=False, resume=None):
    """Return argv and stdin bytes; prompts never pass through a shell.

    ``resume`` names a session to continue instead of opening a new one. It
    exists for the quota case: a writer stopped by a provider limit has its
    reading, its reasoning and its half-written draft inside that session, and
    a fresh writer would pay for all of it again. The caller checks the
    returned session id against ``resume``, because a harness that silently
    started a new conversation produces evidence that looks continuous and
    is not.
    """
    harness, model, effort = (config[k] for k in ("harness", "model", "effort"))
    # A resumed session names the version it began on, never the family: an
    # alias on resume follows a release, so the seat would change model
    # between turns while its receipt still read as the same seat.
    model = config.get("model_pinned") or model
    tools = config.get("tools", "write")
    if tools not in TOOL_POLICIES:
        raise ValueError(f"tools must be one of {TOOL_POLICIES}, not {tools!r}")
    # `workbench` is `research` plus a shell: the roundtable's seats speak
    # through a command in their workspace, and a seat that cannot run it
    # cannot post. It is also the one policy Antigravity can honour, because
    # it confines nothing that agy could otherwise have used.
    workbench = tools == "workbench"
    research = tools == "research" or workbench
    if resume and manual:
        raise ValueError("resume the writer automatically or relaunch it by hand")
    if tools == "research" and harness == "agy":
        raise ValueError("agy cannot confine a seat to a tool set")
    if harness == "codex":
        args = ["codex"] + (
            []
            if manual
            else [
                "exec",
                *(quota.resume_argv("codex", resume) if resume else []),
                "--ignore-user-config",
                "--ignore-rules",
            ]
        )
        args += window_flags(config)
        args += [
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{effort}"',
            "-c",
            'personality="none"',
            "-c",
            'model_verbosity="high"',
            "-c",
            'web_search="live"' if research else 'web_search="disabled"',
            # Codex's own sandbox cuts a shell command off the network even
            # where the jail around it does not. A workbench seat was
            # promised the network, not only the built-in search tool.
            *(
                ["-c", "sandbox_workspace_write.network_access=true"]
                if workbench
                else []
            ),
            "-c",
            "project_doc_max_bytes=0",
            "-c",
            "features.multi_agent=false",
        ]
        # `codex exec resume` accepts neither `-s` nor `-C`: it refuses the
        # argument outright and the turn produces no events at all, which
        # surfaces later as "no unique completed turn" rather than as the
        # argument error it is. The sandbox still travels as config, and the
        # working directory needs no flag because the jail has already chdir'd
        # the process into the workspace.
        if resume and not manual:
            args += ["-c", 'sandbox_mode="workspace-write"']
        else:
            args += ["-s", "workspace-write", "-C", str(workspace)]
        if manual:
            return args + [prompt], None
        return args + ["--skip-git-repo-check", "--json", "-"], prompt.encode()
    if harness == "claude":
        tools = (
            "Read,Write,Edit,Glob,Grep"
            + (",Bash" if workbench else "")
            + (",WebSearch,WebFetch" if research else "")
        )
        args = [
            "claude",
            *(quota.resume_argv("claude", resume) if resume else []),
            "--model",
            model,
            "--effort",
            effort,
            "--tools",
            tools,
            "--allowedTools",
            tools,
            "--disable-slash-commands",
            *window_flags(config),
            *blind.BLIND_SETTINGS,
            *blind.NO_SETTING_SOURCES,
            *blind.NO_MCP,
        ]
        if manual:
            return args + [prompt], None
        return args + [
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
        ], prompt.encode()
    if harness == "agy":
        args = [
            "agy",
            *(quota.resume_argv("agy", resume) if resume else []),
            "--model",
            model,
            "--effort",
            effort,
            "--disable-slash-commands",
            "--dangerously-skip-permissions",
        ]
        if manual:
            return args + ["--prompt-interactive", prompt], None
        return args + [
            "--print",
            prompt,
            "--output-format",
            "stream-json",
            "--print-timeout",
            "60m",
        ], b""
    raise ValueError(f"unknown harness: {harness}")


def launch_context(config, workspace, bus=None):
    """Use private runtime state and a read-only packet in the shared jail.

    ``bus`` is the filtered session bus a runner such as agy needs, opened by
    the caller with ``blind.session_bus`` so it outlives this call and is
    handed to the process through ``blind.popen``.
    """
    harness = config["harness"]
    if error := blind.backend_error():
        raise RuntimeError(error)
    _, missing = blind.runner_binds([harness])
    if missing:
        raise RuntimeError("missing harness prerequisites: " + "; ".join(missing))
    allowed = (
        "PATH",
        "LANG",
        "LC_ALL",
        "TERM",
        "COLORTERM",
        "TZ",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    )
    env = {k: os.environ[k] for k in allowed if k in os.environ}
    env.update(
        HOME=str(Path.home()),
        USER=os.environ.get("USER", ""),
        DISABLE_AUTOUPDATER="1",
        DISABLE_COMPACT="1",
    )
    if harness == "claude":
        oauth = read_json(Path.home() / ".claude/.credentials.json").get(
            "claudeAiOauth", {}
        )
        if (
            not oauth.get("accessToken")
            or oauth.get("expiresAt", 0) / 1000 < time.time() + 300
        ):
            raise RuntimeError(
                "Claude access token missing or expiring; renew outside the writer"
            )
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth["accessToken"]
    jail = blind.jail_argv(
        workspace, runners=[harness], bind_credentials=harness != "claude", bus=bus
    )
    private = blind.state_root(workspace) / harness
    if harness == "claude":
        runtime = private / "runtime-config.json"
        if not runtime.exists():
            runtime.write_text(
                json.dumps({"hasCompletedOnboarding": True, "theme": "dark"}) + "\n"
            )
        jail += ["--bind", str(runtime), str(Path.home() / ".claude.json")]
    # Put the read-only mounts after the workspace mount, which is writable.
    for name in ("input", "START.md"):
        path = workspace / name
        jail += ["--ro-bind", str(path), str(path)]
    return jail, env, private


def _model_matches(expected, actual):
    """Whether the served model id satisfies the requested one.

    A family is satisfied by any served id carrying every family word, so
    ``fable`` accepts ``claude-fable-5-1``. That once failed every fable seat
    after it had spent its quota, because only opus, sonnet and haiku were
    known here. A version must match exactly, or with a date suffix.
    """
    if not actual:
        return False
    if is_family(expected):
        return set(_words(expected)) <= set(_words(actual))
    return actual == expected or bool(
        re.fullmatch(re.escape(expected) + r"-\d{8}", actual)
    )


def compaction_events(rows):
    """The event types in a stream that report a compaction, in order.

    Read from the fields that name an event, never from what it says: a
    reviewer that reads a procedure about compaction writes the word into its
    stream a dozen times without anything having compacted.
    """
    found = []
    for row in rows:
        payload = row.get("payload") or {}
        fields = (
            row.get("type"),
            row.get("subtype"),
            row.get("event"),
            payload.get("type"),
            row.get("step_update", {}).get("step_type"),
        )
        label = next(
            (str(v) for v in fields if v and "compact" in str(v).lower()), None
        )
        if label:
            found.append(label)
    return found


def _no_compaction(rows):
    if compaction_events(rows):
        raise RuntimeError(
            "session compacted; preserve it as an invalid experimental attempt"
        )


def _rollouts(private, harness, sid, *, resumed=False):
    """Every native transcript belonging to one session.

    A writer that ran once has exactly one, and anything else means the record
    is not what it claims to be -- so the single-file requirement stays for
    that case. A writer CONTINUED after a quota limit is the exception the
    rule cannot cover: the second submission may land beside the first rather
    than inside it, and both files are then the same session's evidence.
    """
    base = private / ("sessions" if harness == "codex" else "projects")
    paths = sorted(base.rglob(f"*{sid}*.jsonl"))
    if not paths or (len(paths) != 1 and not resumed):
        raise RuntimeError(
            f"expected one native {harness} transcript for {sid}, found {len(paths)}"
        )
    return paths


def request_size(harness, row):
    """Tokens one request carried, from a row of the harness's own accounting.

    None for a row that reports no request. Shared by the peak measured after
    a run and the figure `review status` shows while one is running, so the
    two cannot disagree about what a request is.
    """
    if harness == "codex":
        payload = row.get("payload") or {}
        if row.get("type") != "event_msg" or payload.get("type") != "token_count":
            return None
        last = (payload.get("info") or {}).get("last_token_usage") or {}
        if not last:
            return None
        return (last.get("input_tokens") or 0) + (last.get("cached_input_tokens") or 0)
    if harness == "claude":
        usage = (row.get("message") or {}).get("usage") or {}
        if not usage:
            return None
        # The three parts of one prompt: cached prefix, newly cached prefix,
        # and the uncached remainder.
        return sum(
            usage.get(k) or 0
            for k in (
                "input_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
        )
    return None


def context_peak(harness, record):
    """Largest single request, which is the only figure a window bounds.

    A receipt's token counters are summed over every request in a session and
    say nothing about occupancy: the highest cumulative input in one run
    belonged to the writer that read the least. What a 200k model has to fit
    is one prompt -- the conversation, its tool output and the prose written
    so far -- so that is what is measured here, from the harness's own
    per-request accounting and never from the packet's size.
    """
    out = {"peak_request": None, "window": None, "evidence": "not reported"}
    if harness == "codex":
        peak = window = 0
        for path in sorted((record / "transcripts").glob("*.jsonl")):
            for row in json_rows(path):
                payload = row.get("payload") or {}
                if (
                    row.get("type") != "event_msg"
                    or payload.get("type") != "token_count"
                ):
                    continue
                info = payload.get("info") or {}
                window = info.get("model_context_window") or window
                peak = max(peak, request_size(harness, row) or 0)
        if peak:
            out.update(
                peak_request=peak,
                window=window or None,
                evidence="rollout token_count.last_token_usage",
            )
    elif harness == "claude":
        peak = window = 0
        events = record / "events.jsonl"
        for row in json_rows(events) if events.exists() else []:
            peak = max(peak, request_size(harness, row) or 0)
            for served in (row.get("modelUsage") or {}).values():
                window = max(window, served.get("contextWindow") or 0)
        if peak:
            out.update(
                peak_request=peak,
                window=window or None,
                evidence="assistant message usage",
            )
    else:
        # agy records session totals only; its transcript carries no
        # per-request accounting, so occupancy here is unmeasured, not small.
        out["evidence"] = "agy reports no per-request usage"
    return out


# Context windows no harness states before a turn has run. Codex lists its
# own in the catalog, but Claude Code reports a window only in a finished
# turn's result and Antigravity never does, so a procedure that must state a
# budget before the first turn reads the window here. Claude's figures are
# Anthropic's model table (cached 2026-06), and Claude Code reported the same
# for Haiku and Sonnet in the September 2026 pilots. Gemini's is Google's
# documented 1M-token input window for Gemini 3 models; Antigravity may cap
# a session lower than that and has no way to say so.
KNOWN_WINDOWS = {
    "haiku": 200_000,
    "sonnet": 1_000_000,
    "opus": 1_000_000,
    "fable": 1_000_000,
    "mythos": 1_000_000,
    "gemini": 1_048_576,
}


def stated_window(config):
    """A model's context window before it has run, and where the figure is from.

    ``(None, None)`` when nothing here knows it: a caller that has to state a
    budget then asks for the window instead of guessing one.
    """
    if config["harness"] == "codex":
        window = config.get("max_context_window")
        if not window:
            return None, None
        # The share of the ceiling Codex actually hands a session: 95 percent
        # of 872,000 is the 828,400 its own token counts report.
        share = config.get("effective_context_window_percent") or 100
        return int(window * share / 100), "Codex catalog"
    words = set(_words(config["model"]))
    for family, window in KNOWN_WINDOWS.items():
        if family in words:
            return window, "documented window"
    return None, None


# Characters per token when a transcript's size stands in for a count. It
# leans low, so the estimate leans high: retiring a seat a little early
# costs less than letting it run into a wall nobody measured.
CHARS_PER_TOKEN = 3.5


def agy_estimate(usage, transcripts, before=None):
    """How full an Antigravity session is, estimated, since agy never says.

    agy bills each turn as totals over all its requests, so the billed prompt
    tokens divided by the requests the turn made is the turn's average
    context. Growth within a turn is roughly even, so the end sits about half
    the turn's growth above that average, and the growth is the transcript's
    new bytes. ``before`` is the transcript's size and request count after
    the previous turn, which the caller keeps; the returned ``marker`` is the
    same figure after this one. A turn with no billed requests adds its
    growth to nothing measured and is reported as unknown.
    """
    size = responses = 0
    for path in transcripts:
        path = Path(path)
        if not path.exists():
            continue
        size += path.stat().st_size
        responses += sum(
            1 for row in json_rows(path) if row.get("type") == "PLANNER_RESPONSE"
        )
    marker = {"bytes": size, "responses": responses}
    before = before or {"bytes": 0, "responses": 0}
    requests = responses - before["responses"]
    billed = sum(
        (usage or {}).get(k) or 0 for k in ("input_tokens", "cache_read_tokens")
    )
    if requests <= 0 or not billed:
        return None, marker
    growth = max(0, size - before["bytes"]) / CHARS_PER_TOKEN
    return int(billed / requests + growth / 2), marker


def context_now(
    harness, record, transcripts=(), *, usage=None, before=None, estimate=False
):
    """How full a session's window is after its latest request.

    Not the peak. With compaction off, the latest request carries the whole
    conversation so far, so its size is what the next turn starts from, and
    a seat told that figure can decide how much of the rest it can afford.
    ``transcripts`` are the native rollouts a receipt names, which is where
    Codex keeps its per-request accounting; Claude keeps it in the stream.
    Antigravity reports session totals only, so it is estimated when
    ``estimate`` asks for it and unknown otherwise, never reported as small.
    """
    out = {"tokens": None, "window": None, "evidence": "not reported"}
    if harness == "codex":
        for path in sorted(Path(p) for p in transcripts):
            for row in json_rows(path):
                size = request_size(harness, row)
                if size is None:
                    continue
                info = (row.get("payload") or {}).get("info") or {}
                out.update(
                    tokens=size,
                    window=info.get("model_context_window") or out["window"],
                    evidence="rollout token_count.last_token_usage",
                )
    elif harness == "claude":
        events = record / "events.jsonl"
        for row in json_rows(events) if events.exists() else []:
            if row.get("type") == "assistant":
                size = request_size(harness, row)
                if size:
                    out.update(tokens=size, evidence="assistant message usage")
            for served in (row.get("modelUsage") or {}).values():
                out["window"] = (
                    max(out["window"] or 0, served.get("contextWindow") or 0) or None
                )
    elif estimate:
        tokens, marker = agy_estimate(usage, transcripts, before)
        out.update(
            tokens=tokens,
            evidence="estimated from billed tokens and transcript growth",
            estimated=True,
            marker=marker,
        )
    else:
        out["evidence"] = "agy reports no per-request usage"
    return out


def _check_resumed(sid, resumed):
    """A resume must land in the session it named, and this is checked FIRST.

    Before the transcript lookups, because a resume that quietly opened a new
    conversation fails those with a confusing message about a missing file --
    and because the draft it returns is a cold restart wearing a
    continuation's receipt, which nothing else would notice.
    """
    if resumed and sid != resumed:
        raise RuntimeError(
            f"resume landed in session {sid}, not {resumed}; the writer did "
            "not continue its own session"
        )


def receipt(config, record, private, *, manual=False, resumed=None):
    """Use harness evidence, not the writer's claim about its own identity.

    ``resumed`` names the session this attempt was supposed to continue. It is
    checked against the session the harness actually reports: a resume that
    quietly opened a new conversation is otherwise indistinguishable from one
    that continued, and the draft it returns would be a cold restart wearing a
    continuation's receipt.
    """
    harness = config["harness"]
    event_file = record / "events.jsonl"
    rows = json_rows(event_file) if event_file.exists() else []
    _no_compaction(rows)
    native = []
    answer = ""
    if harness == "codex":
        if manual:
            paths = list((private / "sessions").rglob("*.jsonl"))
            if len(paths) != 1:
                raise RuntimeError(
                    "manual Codex session has no unique native transcript"
                )
            native_rows = json_rows(paths[0])
            meta = next(
                r["payload"] for r in native_rows if r.get("type") == "session_meta"
            )
            sid = meta["id"]
        else:
            ids = {r["thread_id"] for r in rows if r.get("type") == "thread.started"}
            if len(ids) != 1 or not any(
                r.get("type") == "turn.completed" for r in rows
            ):
                raise RuntimeError(
                    "Codex has no unique completed turn; inspect events and stderr"
                )
            sid = ids.pop()
            _check_resumed(sid, resumed)
            paths = _rollouts(private, harness, sid, resumed=bool(resumed))
            native_rows = [r for p in paths for r in json_rows(p)]
            answers = [
                r["item"]["text"]
                for r in rows
                if r.get("type") == "item.completed"
                and r.get("item", {}).get("type") == "agent_message"
            ]
            answer = answers[-1] if answers else ""
        native = paths
        _no_compaction(native_rows)
        contexts = [
            r["payload"] for r in native_rows if r.get("type") == "turn_context"
        ]
        models = {c.get("model") for c in contexts}
        efforts = {c.get("effort") for c in contexts}
        if models != {config["model"]} or efforts != {config["effort"]}:
            raise RuntimeError(f"Codex model/effort mismatch: {models}, {efforts}")
        if manual and not any(
            r.get("payload", {}).get("type") == "task_complete" for r in native_rows
        ):
            raise RuntimeError("manual Codex transcript has no completed task")
        actual = config["model"]
        usage = next(
            (
                r.get("usage")
                for r in reversed(rows)
                if r.get("type") == "turn.completed"
            ),
            None,
        )
    elif harness == "claude":
        if manual:
            paths = list((private / "projects").rglob("*.jsonl"))
            paths = [p for p in paths if "subagents" not in p.parts]
            if len(paths) != 1:
                raise RuntimeError(
                    "manual Claude session has no unique native transcript"
                )
            sid = paths[0].stem
            rows = json_rows(paths[0])
            if not any(
                r.get("message", {}).get("stop_reason") == "end_turn" for r in rows
            ):
                raise RuntimeError("manual Claude transcript has no completed turn")
            usage = None
        else:
            results = [r for r in rows if r.get("type") == "result"]
            if (
                len(results) != 1
                or results[0].get("is_error")
                or results[0].get("subtype") != "success"
            ):
                # A quota stop and a crash both land here, and the harness
                # states which in the result text it still returns. Quote it:
                # an operator told only that a file is absent looks for the
                # wrong cause. Note that `subtype` can read "success" while
                # `is_error` is set.
                said = (results[0].get("result") or "").strip() if results else ""
                raise RuntimeError(
                    "Claude has no successful completed result"
                    + (f": {said[:300]}" if said else "; inspect events and stderr")
                )
            result = results[0]
            sid, answer, usage = (
                result["session_id"],
                result.get("result", ""),
                result.get("modelUsage"),
            )
            _check_resumed(sid, resumed)
            paths = _rollouts(private, harness, sid, resumed=bool(resumed))
        native = paths
        for path in paths:
            _no_compaction(json_rows(path))
        models = {
            r.get("message", {}).get("model")
            for r in rows
            if r.get("type") == "assistant"
            and r.get("message", {}).get("model") not in (None, "<synthetic>")
        }
        expected = config.get("model_pinned") or config["model"]
        if len(models) != 1 or not _model_matches(expected, next(iter(models))):
            raise RuntimeError(f"Claude served model mismatch: {models}")
        actual = models.pop()
        # Effort is selected by the CLI. Do not claim a served-effort receipt
        # when the provider does not expose one in its stream.
    else:
        paths = list(
            private.glob(
                "antigravity-cli/brain/*/.system_generated/logs/transcript_full.jsonl"
            )
        )
        if manual:
            if len(paths) != 1:
                raise RuntimeError("manual agy session has no unique native transcript")
            sid = paths[0].parents[2].name
            native_rows = json_rows(paths[0])
            _no_compaction(native_rows)
            last = native_rows[-1] if native_rows else {}
            if (
                last.get("type") != "PLANNER_RESPONSE"
                or last.get("status") != "DONE"
                or not last.get("content")
            ):
                raise RuntimeError(
                    "manual agy transcript has no completed final response"
                )
            answer, usage, actual = last["content"], None, None
        else:
            results = [r["result"] for r in rows if r.get("event") == "result"]
            if len(results) != 1:
                raise RuntimeError(
                    "agy has no unique final result; inspect events and stderr"
                )
            result = results[0]
            if result.get("status") not in ("SUCCESS", "COMPLETED", "DONE"):
                raise RuntimeError(f"agy result status: {result.get('status')}")
            ids = {r["conversation_id"] for r in rows if r.get("conversation_id")}
            if len(ids) != 1:
                raise RuntimeError("agy has no unique conversation ID")
            sid = ids.pop()
            _check_resumed(sid, resumed)
            actual = result.get("model") or next(
                (
                    r.get("init", {}).get("model")
                    for r in rows
                    if r.get("event") == "init"
                ),
                None,
            )
            if actual != config["model"]:
                raise RuntimeError(
                    f"agy model mismatch or missing init receipt: {actual}"
                )
            answer, usage = result.get("response", ""), result.get("usage")
            paths = [p for p in paths if p.parents[2].name == sid]
        if len(paths) != 1:
            raise RuntimeError("agy native transcript is absent or ambiguous")
        native = paths
        native_rows = json_rows(paths[0])
        _no_compaction(native_rows)
        forbidden = []
        # agy cannot be told which tools a seat may use, so its transcript is
        # read afterwards instead. The web is outside a writing assignment and
        # inside a workbench one; the browser, MCP servers, subagents and
        # messaging stay outside both, because each would put something other
        # than this one model into the seat.
        outside = r"browser|mcp|invoke_subagent|send_message"
        if config.get("tools") != "workbench":
            outside += r"|search_web|url"
        for row in native_rows:
            for call in row.get("tool_calls", []):
                name = call.get("name", "")
                if re.search(outside, name):
                    forbidden.append(name)
                if name == "generate_image" and "image-generation" not in config.get(
                    "capabilities", []
                ):
                    forbidden.append(name)
        if forbidden:
            raise RuntimeError(
                f"agy used tools outside the text assignment: {forbidden}"
            )
    return {
        "session_id": sid,
        "model": actual,
        "resumed_from": resumed,
        "selected_model": config["model"],
        **(
            {"model_pinned": config["model_pinned"]}
            if config.get("model_pinned")
            else {}
        ),
        "model_evidence": (
            "harness init" if harness == "agy" else "assistant/turn context"
        )
        if actual
        else "explicit CLI selection; no served model receipt",
        "effort": config["effort"],
        "effort_evidence": "turn_context"
        if harness == "codex"
        else "explicit CLI selection",
        "usage": usage,
        "native_transcripts": [str(p) for p in native],
        "answer": answer,
    }


# What each harness calls the same quantity, and the one place that knows it.
# Three adapters report usage in three native shapes, so a caller adding up a
# mixed room either restates this mapping or silently reads only the seats it
# happens to understand. A first attempt at totalling a four-seat room summed
# two of them and printed a figure that looked complete.
#
# `input` and `cache_read` are reported as the harness counts them, and the
# harnesses do not agree: Codex counts cached tokens inside `input_tokens`,
# Claude counts them beside it. Reconciling that here would mean inventing a
# number no receipt contains, so the difference is surfaced instead.
USAGE_FIELDS = {
    "codex": {
        "input": "input_tokens",
        "output": "output_tokens",
        "reasoning": "reasoning_output_tokens",
        "cache_read": "cached_input_tokens",
    },
    "agy": {
        "input": "input_tokens",
        "output": "output_tokens",
        "reasoning": "thinking_tokens",
        "cache_read": "cache_read_tokens",
    },
    "claude": {
        "input": "inputTokens",
        "output": "outputTokens",
        "reasoning": "thinkingTokens",
        "cache_read": "cacheReadInputTokens",
        "cost_usd": "costUSD",
    },
}
CACHE_INSIDE_INPUT = ("codex",)


def usage_totals(harness, usage):
    """One receipt's usage in common terms, or nothing if it reported none.

    Claude nests usage under the model that served the turn and is the only
    harness reporting a price, so `cost_usd` is None elsewhere rather than
    zero: a room whose cost is partly unknown must not read as a cheaper room.
    """
    fields = USAGE_FIELDS.get(harness)
    if not fields or not isinstance(usage, dict) or not usage:
        return None
    rows = (
        [v for v in usage.values() if isinstance(v, dict)]
        if harness == "claude"
        else [usage]
    )
    if not rows:
        return None
    totals = {name: 0 for name in ("input", "output", "reasoning", "cache_read")}
    cost = 0.0 if "cost_usd" in fields else None
    for row in rows:
        for name in totals:
            totals[name] += row.get(fields[name]) or 0
        if cost is not None:
            cost += row.get(fields["cost_usd"]) or 0.0
    return {
        **totals,
        "cost_usd": cost,
        "cache_inside_input": harness in CACHE_INSIDE_INPUT,
    }
