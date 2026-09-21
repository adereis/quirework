"""Cross-model editorial reviews; procedure: docs/workflows.md.

A review is a reviewer, a delta and a brief. The reviewer runs *inside* the
repository with the whole instruction stack visible, because a reviewer that
cannot read the project guide, the spec, and the marks cannot check a claim
against what the book holds. That is the opposite of a confined workshop,
whose confinement to a frozen packet is its experiment; here the same
confinement would be a defect, so only `blind.jail_argv`'s **home-blanking** is
reused and none of its packet.

The home-blanking is not incidental. Claude Code's auto-memory is keyed on the
exact working-directory string, under `~/.claude/projects/<slug>/memory/`, and
it holds the operator's own conclusions about this project: closed rulings,
standing "do not re-propose" notes, prior verdicts. A reviewer that loads it
starts from the priors the review exists to be independent of. Blanking `$HOME`
and binding a private `~/.claude` back removes that store and the global
`CLAUDE.md` with it, while the project's own guidance still arrives from the
repository files. `--setting-sources` is a different surface and does not do
this.

Running unjailed stays available and is the SOP's disclosed fallback rather
than a silent one: `jailed: false` is recorded in the run record and printed,
because the write-up has to say what the reviewer had.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from quirework.providers import blind
from quirework.sops import personas
from quirework.providers import quota
from quirework.providers import models as drivers
from quirework.project import current_project

# Records split the way a panel seat's do: the reviewer's own words are
# evidence and are tracked; the runner's log wrapped around them is not.


# Where the reviewer's growing report is bound inside the jail. `/tmp` is
# already a private tmpfs there (blind.BLANKED), so a subdirectory of it is
# namespace-local: nothing is created on the real filesystem and no other
# process shares the path. That is why the repository's "never /tmp" rule does
# not reach in here.
JAILED_REPORT = "/tmp/quirework-review"

RUN_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-review-[a-z0-9][a-z0-9-]*$")
SCOPE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _options():
    """Read policy from the selected project for this operation."""
    from types import SimpleNamespace

    OWN_OUTPUT = (
        current_project()
        .data("sops.review_policy", {})
        .get("own_output", ".quirework/reviews/*")
    )
    FRAME = (
        current_project()
        .data("sops.review_policy", {})
        .get(
            "frame",
            "Review the supplied material. Report findings with evidence.\n\n{brief}\n\n{delta}\n\nWrite the report to {report}.",
        )
    )
    PROCEDURE_FRAME = (
        current_project()
        .data("sops.review_policy", {})
        .get(
            "procedure_frame",
            "Review the supplied changes and their procedures. Report concrete findings.\n\n{brief}\n\n{delta}\n\nWrite the report to {report}.",
        )
    )
    project = current_project()
    declared = [
        project.get("book.source"),
        *(project.get("book.companion_dirs", ()) or ()),
    ]
    default_reviewable = []
    for value in declared:
        if not value:
            continue
        path = Path(value)
        try:
            relative = path.relative_to(project.root) if path.is_absolute() else path
        except ValueError:
            continue
        default_reviewable.append(str(relative).strip("/") + "/")
    REVIEWABLE = tuple(
        project.data("sops.review_policy", {}).get("reviewable", default_reviewable)
    )
    return SimpleNamespace(
        FRAME=FRAME,
        OWN_OUTPUT=OWN_OUTPUT,
        PROCEDURE_FRAME=PROCEDURE_FRAME,
        REVIEWABLE=REVIEWABLE,
    )


def _settings():
    """Resolve paths from the current operation, without import-time state."""
    from types import SimpleNamespace

    ROUNDS = current_project().path("runs.reviews")
    DEFAULT_STORE = current_project().state_root
    return SimpleNamespace(DEFAULT_STORE=DEFAULT_STORE, ROUNDS=ROUNDS)


def _fail(message):
    raise ValueError(message)


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def git(*args, check=True):
    p = subprocess.run(
        ["git", "-C", str(current_project().root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and p.returncode:
        _fail(f"git {' '.join(args)} failed: {p.stderr.strip()[:300]}")
    return p.stdout


# What the driver itself writes into the repository, and therefore the only
# thing the find-only checksum must not count. It is a glob and not this run's
# own directory: two reviews launched together accused each other, because the
# second saw the first's run directory appear and reported a violation neither
# reviewer had committed. The reviewer cannot write here in any case -- the
# jail binds the repository read-only and gives it one writable mount -- so
# excluding the family costs nothing a jailed run was relying on.


def worktree_state():
    """HEAD plus the porcelain status: the find-only checksum, both halves.

    Status alone would miss a reviewer that committed, and HEAD alone would
    miss one that edited. A review changes neither.

    The later verdict separates changes in reviewed files from unrelated
    concurrent activity. This snapshot alone assigns no authorship.
    """
    return {
        "head": git("rev-parse", "HEAD").strip(),
        # No `glob` magic: with it, `*` stops at a path separator and the
        # exclusion drops the directory entry while still reporting every
        # file inside it.
        "status": git(
            "status", "--porcelain", "--", ".", f":(exclude){_options().OWN_OUTPUT}"
        ).rstrip("\n"),
    }


def delta_paths(delta):
    """The files the reviewed delta actually touches.

    A change to one of these DURING the run matters whoever made it: the
    report's `file:line` citations and quotations may then describe a state
    that no longer exists.
    """
    if delta and delta.get("paths"):
        return delta["paths"]
    if not delta or delta.get("kind") != "range":
        return [f for f in git("diff", "--name-only", "HEAD").split() if f]
    return [f for f in git("diff", "--name-only", delta["spec"]).split() if f]


def reviewed_hashes(delta):
    return {
        p: hashlib.sha256((current_project().root / p).read_bytes()).hexdigest()
        if (current_project().root / p).is_file()
        else None
        for p in delta_paths(delta)
    }


def _moved_paths(before, after):
    """Paths whose porcelain status differs between the two snapshots."""

    def index(state):
        out = {}
        for line in state["status"].splitlines():
            if len(line) > 3:
                out[line[3:].strip().strip('"')] = line[:2]
        return out

    b, a = index(before), index(after)
    moved = {p for p in set(b) | set(a) if b.get(p) != a.get(p)}
    if before["head"] != after["head"]:
        moved.add("HEAD")
    return sorted(moved)


def _find_only_verdict(moved, in_scope, jailed):
    """Separate "the reviewer wrote" from "the workspace moved".

    The two were conflated and the checksum cried wolf twice: once at its own
    run directory, and then at a concurrent agent's tooling commits. Neither
    was the reviewer, and a jailed reviewer CANNOT be: the jail binds the
    repository read-only and gives it one writable mount outside it. So the
    verdict follows from what the jail guarantees rather than from the mere
    fact of movement.

    What still deserves a loud line, jailed or not, is movement in the files
    under review, because then the report may cite a state that is gone.
    """
    if not moved:
        return {"find_only": True, "workspace": "unchanged", "note": ""}
    if in_scope:
        return {
            # The jail is what settles authorship; staleness is a
            # separate worry and gets the loud line either way.
            "find_only": jailed,
            "workspace": "reviewed-files-moved",
            "note": (
                "the FILES UNDER REVIEW changed during the run, so this "
                "report may cite a state that no longer exists. Re-check "
                "every finding against the current files before acting:"
            ),
        }
    if jailed:
        return {
            "find_only": True,
            "workspace": "concurrent-activity",
            "note": (
                "the workspace moved during the run, outside the reviewed "
                "files. The reviewer was jailed with the repository "
                "read-only, so this was somebody else; no finding is "
                "affected. Moved:"
            ),
        }
    return {
        "find_only": False,
        "workspace": "unjailed-and-moved",
        "note": (
            "FIND-ONLY UNVERIFIABLE -- the workspace moved and this "
            "reviewer was NOT jailed, so it is a candidate author. "
            "Inspect before using this review:"
        ),
    }


def resolve_range(spec):
    """Validate the delta and return it with the commits it actually covers."""
    if spec in ("", None):
        _fail(
            "a review needs a delta: pass --range A..B, or --range HEAD for the "
            "working tree"
        )
    if spec == "HEAD":
        if not git("status", "--porcelain").strip():
            _fail("--range HEAD means the uncommitted working tree, and it is clean")
        return {"spec": "HEAD", "kind": "worktree", "commits": []}
    if ".." not in spec:
        _fail(f"not a git range: {spec!r} (expected A..B)")
    p = subprocess.run(
        ["git", "-C", str(current_project().root), "log", "--format=%H %s", spec],
        capture_output=True,
        text=True,
        check=False,
    )
    if p.returncode:
        _fail(f"git cannot resolve {spec!r}: {p.stderr.strip()[:200]}")
    commits = [line for line in p.stdout.splitlines() if line.strip()]
    if not commits:
        _fail(f"{spec!r} covers no commits; there is nothing to review")
    return {"spec": spec, "kind": "range", "commits": commits}


# The frame is what every review carries; the brief names the range and any
# necessary working-tree context. Commit bodies carry the reasoning.
# The frame does not restate the SOP's rules, because
# the reviewer can read the SOP: it is in the repository the reviewer is
# standing in, and one owner for a rule beats a copy that drifts.


DELTA_RANGE = """\
The commits in `git log {spec}` and their net diff, `git diff {spec}`:

{commits}
"""

DELTA_WORKTREE = """\
The uncommitted working tree: `git status` and `git diff`, plus any staged
change in `git diff --cached`. Nothing is committed yet.
"""


def compose(brief_text, delta, report_path, *, persona=None, kind="consistency"):
    if delta["kind"] == "range":
        listed = "\n".join("  " + c for c in delta["commits"])
        block = DELTA_RANGE.format(spec=delta["spec"], commits=listed)
    else:
        block = DELTA_WORKTREE
    if delta.get("paths"):
        block += (
            "\n\nReview only these target paths and their necessary dependencies.\n"
        )
        block += "New untracked files are part of the review; read them in full.\n"
        block += "\n".join("  " + p for p in delta["paths"])
    if kind not in ("consistency", "procedure"):
        _fail("review kind must be consistency or procedure")
    text = (
        _options().PROCEDURE_FRAME if kind == "procedure" else _options().FRAME
    ).format(
        root=current_project().root,
        delta=block.rstrip(),
        report=report_path,
        brief=brief_text.strip(),
    )
    return personas.compose(text, persona) if persona else text


def run_dir_for(name):
    return _settings().ROUNDS / name


def store_dir_for(name, store=None):
    return Path(store or _settings().DEFAULT_STORE) / name


def resolve_run(name, store=None):
    """Accept a full run directory name or a unique scope token."""
    if RUN_NAME.match(name):
        run = run_dir_for(name)
        if not run.is_dir():
            _fail(f"no such review run: {run}")
        return name
    if not SCOPE.match(name):
        _fail(f"not a run name or scope token: {name!r}")
    hits = sorted(
        p.name for p in _settings().ROUNDS.glob(f"*-review-{name}") if p.is_dir()
    )
    if not hits:
        _fail(f"no review run with scope {name!r} under {_settings().ROUNDS}")
    if len(hits) > 1:
        _fail(
            f"scope {name!r} matches {len(hits)} runs: {', '.join(hits)}; "
            "name the run directory"
        )
    return hits[0]


def command(config, mode, session_id, report_dir):
    """Return argv and stdin bytes for a review turn.

    The prompt never passes through a shell, and never through argv either:
    every harness takes it on stdin, so a brief carrying quotes, backticks or
    a diff cannot be reinterpreted on the way in.
    """
    harness, model, effort = (config[k] for k in ("harness", "model", "effort"))
    if mode == "fork" and harness == "agy":
        _fail("agy exposes no native fork; resume the session or start a new run")
    if mode in ("resume", "fork") and not session_id:
        _fail(f"a {mode} needs a session id")
    if harness == "codex":
        head = {
            "run": ["exec"],
            "resume": ["exec", "resume", session_id],
            "fork": ["exec", "fork", session_id],
        }[mode]
        args = [
            "codex",
            *head,
            "--ignore-user-config",
            "--ignore-rules",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{effort}"',
            "-c",
            'personality="none"',
            "-c",
            'model_verbosity="high"',
            "-c",
            'web_search="disabled"',
            "-c",
            "features.multi_agent=false",
            # The whole window, and no compaction. Shared with the workshop so
            # the two launchers cannot drift from providers/harness.py again: this
            # module built its own argv and omitted both, so every review ran
            # at the model's session default and could compact. The resume
            # inherits the prior transcript, so it compacts first.
            *drivers.window_flags(config),
            # NOT project_doc_max_bytes=0. The workshop zeroes it to keep the
            # instruction stack out of a blind writer; a reviewer needs it.
            # NOT read-only: codex's own read-only sandbox refuses every
            # write including the report file, which costs the reviewer the
            # write-as-you-go protection and leaves a killed turn with
            # nothing. The repository is enforced read-only by the jail's
            # ro-bind, which is the layer that should be doing it; the harness
            # policy only has to leave the report directory writable.
            "-c",
            'sandbox_mode="workspace-write"',
            "--skip-git-repo-check",
            "--json",
            "-",
        ]
        if mode == "run":
            args.insert(args.index("--json"), "-s")
            args.insert(args.index("--json"), "workspace-write")
        return args, None
    if harness == "claude":
        settings = json.loads(blind.BLIND_SETTINGS[1])
        # Keep project guidance, but leave lifecycle side effects to the
        # driver. The repository's SessionEnd capture hook otherwise tries
        # to write into the read-only checkout after a successful review.
        settings["disableAllHooks"] = True
        args = [
            "claude",
            "--model",
            model,
            "--effort",
            effort,
            # Bash is load-bearing: the delta is a git range and the reviewer
            # reads it with git. Write is scoped by the jail to the report dir.
            "--tools",
            "Bash,Read,Glob,Grep,Write",
            "--allowedTools",
            "Bash,Read,Glob,Grep,Write",
            "--disable-slash-commands",
            "--dangerously-skip-permissions",
            *drivers.window_flags(config),
            "--settings",
            json.dumps(settings),
            *blind.NO_MCP,
            # A resume lands in the session the private home already holds, so
            # the reviewer keeps its reading, its reasoning and the report it
            # was part-way through. `chapter_experiment` has driven this flag
            # against a private Claude home since the forked-chapter runs, and
            # `_collect` below checks the returned session id against the one
            # asked for, which is what turns "the flag exists" into a receipt.
            *(
                quota.resume_argv("claude", session_id)
                if mode in ("resume", "fork")
                else []
            ),
            *(["--fork-session"] if mode == "fork" else []),
            # Deliberately NO --setting-sources: the project's own CLAUDE.md and
            # AGENTS.md are exactly what this reviewer is supposed to have. The
            # operator's memory is kept out by the private home, not by this.
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        return args, None
    if harness == "agy":
        return [
            "agy",
            "--model",
            model,
            "--effort",
            effort,
            "--disable-slash-commands",
            "--dangerously-skip-permissions",
            *(quota.resume_argv("agy", session_id) if mode == "resume" else []),
            "--output-format",
            "stream-json",
            "--print-timeout",
            "60m",
        ], None
    _fail(f"unknown harness: {harness}")


def launch_context(config, record, report_dir, *, jailed=True, probe=False):
    """Environment and jail for a reviewer standing in the repository.

    Returns ``(argv_prefix, env, private, report_path)``. ``report_path`` is
    where the reviewer is told to write, which differs inside the jail because
    the real path is bound to a fixed mount point there.

    ``probe=True`` builds the same jail for inspection without reading
    credentials. It still creates the private runner home, because that is
    where ``jail_argv`` puts it and a later real run wants the same one.
    """
    harness = config["harness"]
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
    if not jailed:
        if harness == "claude":
            # Say it plainly rather than letting the record imply otherwise.
            print(
                "review: unjailed Claude reviewer carries the operator's "
                "MEMORY.md and the global CLAUDE.md; recorded as jailed=false",
                file=sys.stderr,
            )
        return [], env, None, report_dir

    if error := blind.backend_error():
        raise RuntimeError(error)
    _, missing = blind.runner_binds([harness])
    if missing:
        raise RuntimeError("missing harness prerequisites: " + "; ".join(missing))
    if harness == "claude" and not probe:
        oauth = read_json(Path.home() / ".claude/.credentials.json").get(
            "claudeAiOauth", {}
        )
        if (
            not oauth.get("accessToken")
            or oauth.get("expiresAt", 0) / 1000 < time.time() + 300
        ):
            raise RuntimeError(
                "Claude access token missing or expiring; renew it before launching"
            )
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth["accessToken"]

    # The private homes hang off the RUN's record directory, never a turn's:
    # `codex exec resume` looks for the session under ~/.codex/sessions, so a
    # per-turn state root would hand turn two an empty home and lose the very
    # session it was asked to resume.
    private = blind.state_root(record) / harness
    jail = blind.jail_argv(
        record,
        runners=[harness],
        bind_credentials=harness != "claude",
        expose_run_dir=False,
    )
    if harness == "claude":
        runtime = private / "runtime-config.json"
        if not runtime.exists():
            runtime.write_text(
                json.dumps({"hasCompletedOnboarding": True, "theme": "dark"}) + "\n"
            )
        jail += ["--bind", str(runtime), str(Path.home() / ".claude.json")]
    # The repository, read-only, at its real path: the reviewer's own quoted
    # `file:line` citations have to be the operator's paths. Then the report
    # directory, writable, at the one place the reviewer may write. Both go
    # after jail_argv's own binds, and the chdir last, because bwrap applies
    # operations in sequence and the working directory is the repository, not
    # the record.
    jail += ["--ro-bind", str(current_project().root), str(current_project().root)]
    jail += ["--bind", str(report_dir), JAILED_REPORT]
    jail += [
        "--chdir",
        str(current_project().root),
        "--setenv",
        "PWD",
        str(current_project().root),
    ]
    return jail, env, private, Path(JAILED_REPORT)


def _answer(harness, config, rows, private):
    """The reviewer's final message plus a model/effort receipt from the harness.

    Deliberately not `drivers.receipt`: that one demands a single native
    transcript for the session, which is right for a one-shot writer and wrong
    here, because a resumed or forked review legitimately has more than one.
    The compaction refusal and the served-model check are the same, and are
    reused rather than restated.
    """
    drivers._no_compaction(rows)
    out = {
        "session_id": None,
        "model": None,
        "effort": config["effort"],
        "answer": "",
        "usage": None,
        "native_transcripts": [],
    }
    if harness == "codex":
        ids = {r["thread_id"] for r in rows if r.get("type") == "thread.started"}
        if len(ids) != 1 or not any(r.get("type") == "turn.completed" for r in rows):
            _fail("codex has no unique completed turn; inspect events.jsonl and stderr")
        out["session_id"] = ids.pop()
        messages = [
            r["item"]["text"]
            for r in rows
            if r.get("type") == "item.completed"
            and r.get("item", {}).get("type") == "agent_message"
        ]
        out["answer"] = messages[-1] if messages else ""
        out["usage"] = next(
            (
                r.get("usage")
                for r in reversed(rows)
                if r.get("type") == "turn.completed"
            ),
            None,
        )
    elif harness == "claude":
        results = [r for r in rows if r.get("type") == "result"]
        if (
            len(results) != 1
            or results[0].get("is_error")
            or results[0].get("subtype") != "success"
        ):
            said = (results[0].get("result") or "").strip() if results else ""
            _fail(
                "claude has no successful completed result"
                + (f": {said[:300]}" if said else "; inspect events and stderr")
            )
        result = results[0]
        out.update(
            session_id=result["session_id"],
            answer=result.get("result", ""),
            usage=result.get("modelUsage"),
        )
        served = {
            r.get("message", {}).get("model")
            for r in rows
            if r.get("type") == "assistant"
            and r.get("message", {}).get("model") not in (None, "<synthetic>")
        }
        if len(served) != 1 or not drivers._model_matches(
            config["model"], next(iter(served))
        ):
            _fail(f"claude served model mismatch: {served}")
        out["model"] = served.pop()
    else:
        results = [r["result"] for r in rows if r.get("event") == "result"]
        if len(results) != 1:
            _fail("agy has no unique final result; inspect events and stderr")
        result = results[0]
        if result.get("status") not in ("SUCCESS", "COMPLETED", "DONE"):
            _fail(f"agy result status: {result.get('status')}")
        ids = {r["conversation_id"] for r in rows if r.get("conversation_id")}
        if len(ids) != 1:
            _fail("agy has no unique conversation ID")
        out.update(
            session_id=ids.pop(),
            answer=result.get("response", ""),
            usage=result.get("usage"),
            model=result.get("model"),
        )
    if harness == "codex" and private is not None:
        contexts = []
        for path in (private / "sessions").rglob(f"*{out['session_id']}*.jsonl"):
            out["native_transcripts"].append(str(path))
            native = drivers.json_rows(path)
            drivers._no_compaction(native)
            contexts += [
                r["payload"] for r in native if r.get("type") == "turn_context"
            ]
        models = {c.get("model") for c in contexts}
        efforts = {c.get("effort") for c in contexts}
        if models and (models != {config["model"]} or efforts != {config["effort"]}):
            _fail(f"codex model/effort mismatch: {models}, {efforts}")
        out["model"] = config["model"] if models else None
    return out


def _parent_turn(state, from_turn=None):
    """Which turn a resume or fork continues, and its session.

    A fork is a side branch: it exists so a disagreement can be pressed while
    the first answer stays intact and quotable. So the default parent is the
    latest turn that is NOT a fork, and continuing a fork is something the
    operator asks for by turn. Defaulting to "the last turn" would silently
    move the trunk onto the branch the moment anyone argued, which is the one
    property forking is for.
    """
    turns = [t for t in state.get("turns", []) if t.get("session_id")]
    if from_turn:
        hit = [t for t in turns if t["turn"] == from_turn]
        if not hit:
            _fail(f"no completed turn {from_turn!r} with a session id")
        return hit[-1]["turn"], hit[-1]["session_id"]
    trunk = [t for t in turns if t.get("mode") != "fork"]
    chosen = (trunk or turns)[-1] if turns else None
    return (chosen["turn"], chosen["session_id"]) if chosen else (None, None)


def _next_turn(record):
    turns = record / "turns"
    existing = (
        sorted(p.name for p in turns.glob("[0-9][0-9]")) if turns.is_dir() else []
    )
    return f"{int(existing[-1]) + 1:02d}" if existing else "01"


def start(
    scope,
    *,
    range_spec,
    brief,
    harness="codex",
    model,
    effort="xhigh",
    store=None,
    jailed=True,
    timeout=None,
    dry_run=False,
    persona="default",
    kind="consistency",
    paths=None,
):
    """Open a review run and take its first turn."""
    if not SCOPE.match(scope):
        _fail(f"scope must be a lowercase slug: {scope!r}")
    # The kind is supplied here, so a scope that repeats it names the run
    # twice: `--scope review-foo` produced `2026-09-10-review-review-foo`.
    # Refused rather than absorbed, because silently rewriting what the
    # operator typed makes the directory hard to find again.
    if scope.startswith("review-"):
        _fail(
            f"scope must not repeat the kind: use {scope[len('review-') :]!r}, "
            f"not {scope!r}; the run is named <date>-review-<scope>"
        )
    name = f"{datetime.date.today().isoformat()}-review-{scope}"
    run = run_dir_for(name)
    if run.exists() and not dry_run:
        _fail(f"{run} already exists; resume it or pick another scope")
    config = drivers.resolve(harness, model, effort)
    profile = personas.resolve(persona)
    if kind not in ("consistency", "procedure"):
        _fail("review kind must be consistency or procedure")
    delta = resolve_range(range_spec)
    if paths is not None:
        if delta["kind"] != "worktree":
            _fail("--paths-file is for a working-tree review (--range HEAD)")
        selected = []
        for path in paths:
            path = path.strip()
            if not path:
                continue
            p = Path(path)
            if p.is_absolute() or ".." in p.parts or not p.parts:
                _fail(f"review path must be relative to the checkout: {path}")
            if p.as_posix() in selected:
                _fail(f"duplicate review path: {path}")
            target = current_project().root / p
            if (
                target.is_symlink()
                or target.is_dir()
                or not target.resolve().is_relative_to(current_project().root.resolve())
            ):
                _fail(f"review target must be a file: {path}")
            if not target.exists() and not git("ls-files", "--", p.as_posix()).strip():
                _fail(f"review target is absent and was not tracked: {path}")
            selected.append(p.as_posix())
        if not selected:
            _fail("review path list is empty")
        delta["paths"] = selected
    record = store_dir_for(name, store)
    if not dry_run:
        run.mkdir(parents=True)
        record.mkdir(parents=True, exist_ok=True)
        write_json(
            record / "run.json",
            {
                "run": name,
                "scope": scope,
                "created_at": time.time(),
                "config": config,
                "delta": delta,
                "jailed": jailed,
                "brief_source": str(Path(brief).resolve()),
                "harness_version": blind.runner_version(harness),
                "persona": profile,
                "kind": kind,
                "turns": [],
            },
        )
    return _turn(
        name,
        "run",
        Path(brief).read_text(),
        store=store,
        jailed=jailed,
        timeout=timeout,
        dry_run=dry_run,
        config=config,
        delta=delta,
        persona=profile,
        kind=kind,
    )


# What a continued reviewer is told on top of the standard quota notice. It
# names the one file that carries its work, because the report -- not the
# transcript -- is what the operator reads, and a reviewer that starts a fresh
# report renumbers findings the round has already banked.
CONTINUE_NOTE = """\
Your report is at {report} and is unchanged. Keep writing to that same file.
Re-read it first: its PROGRESS line is where you stopped. Do not redo work it
already records, do not disturb or renumber existing findings, and number new
ones from where its numbering left off. Set STATUS: complete when the declared
review is finished."""


def _turn(
    name,
    mode,
    brief_text,
    *,
    store=None,
    jailed=True,
    timeout=None,
    dry_run=False,
    config=None,
    delta=None,
    from_turn=None,
    persona=None,
    kind=None,
):
    record = store_dir_for(name, store)
    state = read_json(record / "run.json") if (record / "run.json").exists() else None
    if state is None:
        if config is None:
            _fail(f"no run record at {record / 'run.json'}")
        state = {
            "run": name,
            "config": config,
            "delta": delta,
            "turns": [],
            "persona": persona,
            "kind": kind or "consistency",
        }
    config = config or state["config"]
    delta = delta or state["delta"]
    if (
        delta.get("kind") == "worktree"
        and "paths" not in delta
        and state["delta"].get("paths")
    ):
        delta = {**delta, "paths": state["delta"]["paths"]}
    persona = state.get("persona", persona)
    if persona:
        personas.verify(persona)
    kind = state.get("kind", kind or "consistency")
    harness = config["harness"]

    parent, session_id = _parent_turn(state, from_turn)
    if mode in ("resume", "fork", "continue") and not session_id:
        _fail(
            f"{name} has no recorded session id to {mode}"
            + (f" at turn {from_turn}" if from_turn else "")
        )

    turn = _next_turn(record)
    turn_dir = record / "turns" / turn
    # A continuation writes into the INTERRUPTED turn's report directory. The
    # turn record is new, because it is a separate submission with its own
    # events, argv and find-only checksum; the report is not, because it is
    # one document the reviewer was part-way through and the whole point of
    # continuing is to keep it.
    report_dir = (
        record / "turns" / parent / "report"
        if mode == "continue"
        else turn_dir / "report"
    )
    # A dry run launches nothing, so it creates nothing. Creating the
    # directory here anyway made `review prompt` allocate a turn: _next_turn
    # counts directories, so the next real turn skipped past an empty 01 and
    # the run's own evidence then showed a first turn that produced no report.
    if not dry_run:
        turn_dir.mkdir(parents=True, exist_ok=True)
        report_dir.mkdir(parents=True, exist_ok=True)

    prefix, env, private, report_path = launch_context(
        config, record, report_dir, jailed=jailed, probe=dry_run
    )
    if mode == "continue":
        # No brief, no delta, no persona frame: all three are already in the
        # session. Re-sending them is how a continuation turns back into a
        # restart, which is the thing this path exists to avoid.
        prompt = quota.continuation(
            CONTINUE_NOTE.format(report=report_path / "report.md")
        )
    else:
        prompt = compose(
            brief_text, delta, report_path / "report.md", persona=persona, kind=kind
        )
    args, _ = command(
        config, "resume" if mode == "continue" else mode, session_id, report_dir
    )
    if dry_run:
        return {"run": name, "mode": mode, "argv": prefix + args, "prompt": prompt}

    (turn_dir / "prompt.md").write_text(prompt)
    write_json(
        turn_dir / "launch.json",
        {
            "argv": args,
            "jail": prefix,
            "mode": mode,
            "jailed": jailed,
            "resumed_from": session_id
            if mode in ("resume", "fork", "continue")
            else None,
            "parent_turn": parent if mode in ("resume", "fork", "continue") else None,
            "private_runtime": str(private) if private else None,
            "report_mount": str(report_path),
            "persona_sha256": persona["sha256"] if persona else None,
        },
    )
    before = worktree_state()
    before["reviewed_hashes"] = reviewed_hashes(delta)
    started = time.time()
    with (
        (turn_dir / "events.jsonl").open("xb") as out,
        (turn_dir / "stderr.log").open("xb") as err,
    ):
        proc = subprocess.Popen(
            prefix + args,
            stdin=subprocess.PIPE,
            stdout=out,
            stderr=err,
            cwd=str(current_project().root),
            env=env,
            start_new_session=True,
        )
        try:
            proc.communicate(prompt.encode(), timeout=timeout)
        except subprocess.TimeoutExpired:
            # Killed, not abandoned: whatever the reviewer had already written
            # to its report is the point of the write-as-you-go rule, and it
            # is collected below exactly as a completed turn's would be.
            drivers._terminate(proc)
            proc.communicate()
    after = worktree_state()
    after["reviewed_hashes"] = reviewed_hashes(delta)
    return _collect(
        name,
        turn,
        before,
        after,
        started,
        store=store,
        jailed=jailed,
        mode=mode,
        parent=parent,
        delta=delta,
        report_dir=report_dir,
        expect_session=session_id,
    )


def _collect(
    name,
    turn,
    before,
    after,
    started,
    *,
    store=None,
    jailed=True,
    mode="run",
    parent=None,
    delta=None,
    report_dir=None,
    expect_session=None,
):
    """Bank the turn: the reviewer's words to the run, everything else here."""
    record = store_dir_for(name, store)
    state = read_json(record / "run.json")
    config = state["config"]
    harness = config["harness"]
    turn_dir = record / "turns" / turn
    private = (blind.state_root(record) / harness) if jailed else None

    events = turn_dir / "events.jsonl"
    rows = drivers.json_rows(events) if events.exists() else []
    stop = quota.classify(harness, turn_dir) if rows else None
    try:
        receipt = _answer(harness, config, rows, private)
        status = "completed"
        error = None
    except (ValueError, RuntimeError) as exc:
        # A dead turn still banks its report. That is the whole reason the
        # reviewer is told to write as it goes, and a partial report that
        # names three real defects is worth more than a clean failure.
        receipt = {
            "session_id": None,
            "model": None,
            "effort": config["effort"],
            "answer": "",
            "usage": None,
            "native_transcripts": [],
        }
        status, error = "failed", str(exc)
        # The session id is the one thing a continuation needs, and reading it
        # only out of a SUCCESSFUL receipt is what made a quota-stopped review
        # unresumable: the id existed all along, inside the event stream.
        recovered = quota.session_id(harness, rows)
        if recovered:
            receipt["session_id"] = recovered
        if stop:
            status = "quota-refused" if stop["phase"] == "refused" else "quota-stopped"
            error = f"{stop['scope']}: {stop['detail']}".strip(": ")
    # A resume that silently opened a new conversation would look identical to
    # one that continued the old, and every later turn would then branch off
    # evidence nobody read. A FORK is the one mode where a different session is
    # the point, so it is exempt; `_answer` already checks that a fork is
    # distinct. Banked rather than raised, because by this point a foreign
    # session may have written into the report and the operator needs to see
    # the record, not an exception where the turn should have been.
    if (
        mode in ("resume", "continue")
        and expect_session
        and receipt["session_id"]
        and receipt["session_id"] != expect_session
    ):
        status = "wrong-session"
        error = (
            f"{mode} landed in session {receipt['session_id']}, not "
            f"{expect_session}; this is not a continuation of the same "
            "reviewer, and anything it wrote to the report is suspect"
        )
        print(f"review: turn {turn}: {error}", file=sys.stderr)

    written = (report_dir or (turn_dir / "report")) / "report.md"
    body = written.read_text() if written.exists() else ""
    if not body.strip():
        body = receipt["answer"]
        source = "final message"
    else:
        source = "report file"
    if not body.strip():
        body = ""

    finding_ids = re.findall(r"(?m)^#{2,3}\s+F(\d+)\b", body)
    findings = (
        len(set(finding_ids)) if finding_ids else len(re.findall(r"(?m)^\d+\.\s", body))
    )
    progress = re.findall(r"(?m)^\s*PROGRESS:.*$", body)
    moved = _moved_paths(before, after)
    delta = delta or state.get("delta")
    reviewed = set(delta_paths(delta))
    old_hashes, new_hashes = (
        before.get("reviewed_hashes", {}),
        after.get("reviewed_hashes", {}),
    )
    moved = sorted(
        set(moved)
        | {
            p
            for p in old_hashes.keys() | new_hashes.keys()
            if old_hashes.get(p) != new_hashes.get(p)
        }
    )
    in_scope = sorted(p for p in moved if p in reviewed)
    verdict = _find_only_verdict(moved, in_scope, jailed)

    out_name = f"{turn}-{harness}-{config['model']}.md"
    if body.strip():
        (run_dir_for(name) / out_name).write_text(
            body if body.endswith("\n") else body + "\n"
        )

    entry = {
        "turn": turn,
        "mode": mode,
        "parent_turn": parent,
        "delta": delta,
        "quota_stop": stop["phase"] if stop else None,
        "quota_scope": stop["scope"] if stop else None,
        "quota_resets_at": stop["resets_at"] if stop else None,
        "report_dir": str(report_dir) if report_dir else None,
        "prompt_sha256": hashlib.sha256(
            (turn_dir / "prompt.md").read_bytes()
        ).hexdigest()
        if (turn_dir / "prompt.md").exists()
        else None,
        "status": status,
        "error": error,
        "session_id": receipt["session_id"],
        "model": receipt["model"],
        "selected_model": config["model"],
        "model_evidence": (
            "harness turn context"
            if receipt["model"]
            else "explicit CLI selection; no served receipt"
        ),
        "effort": config["effort"],
        "answer_source": source,
        "report": out_name if body.strip() else None,
        "findings": findings,
        "progress": progress[-1:] and progress[-1] or None,
        "usage": receipt["usage"],
        "native_transcripts": receipt["native_transcripts"],
        "seconds": round(time.time() - started, 1),
        "find_only": verdict["find_only"],
        "workspace": verdict["workspace"],
        "workspace_note": verdict["note"],
        "moved_paths": moved,
        "moved_in_scope": in_scope,
        "worktree_before": before,
        "worktree_after": after,
    }
    state.setdefault("turns", []).append(entry)
    state["delta"] = delta
    state["jailed"] = jailed
    write_json(record / "run.json", state)
    if state.get("persona"):
        write_json(
            run_dir_for(name) / "_assignment.json",
            {
                "persona": state["persona"],
                "kind": state.get("kind", "consistency"),
                "turns": [
                    {
                        k: t.get(k)
                        for k in (
                            "turn",
                            "delta",
                            "model",
                            "selected_model",
                            "model_evidence",
                            "effort",
                            "status",
                            "report",
                            "find_only",
                            "workspace",
                            "prompt_sha256",
                        )
                    }
                    for t in state["turns"]
                ],
            },
        )
    if verdict["workspace"] != "unchanged":
        # Never quietly, and never louder than the evidence warrants.
        print(f"review: turn {turn}: {verdict['note']}", file=sys.stderr)
        for path in moved[:8]:
            print(
                f"  {path}" + ("   <-- UNDER REVIEW" if path in in_scope else ""),
                file=sys.stderr,
            )
        if len(moved) > 8:
            print(f"  … and {len(moved) - 8} more", file=sys.stderr)
    return {
        k: entry[k]
        for k in (
            "turn",
            "status",
            "error",
            "session_id",
            "model",
            "report",
            "findings",
            "progress",
            "seconds",
            "find_only",
            "workspace",
            "moved_in_scope",
        )
    } | {"run": name}


# Where an edit is reviewable: the book, the spec and the companions that
# reason from it. Tooling and records are outside, not because they matter
# less, but because the reviewer this procedure describes checks claims against
# what the book holds.


def _trailer(rev, key):
    return git("log", "-1", f"--format=%(trailers:key={key},valueonly)", rev).strip()


def last_reviewed():
    """The most recent commit that carried a `Reviewed-by:` trailer."""
    for rev in git("log", "--format=%H", "-500").split():
        if _trailer(rev, "Reviewed-by"):
            return rev
    return None


def audit(since=None):
    """Reviewable commits since a ref that carry no `Reviewed-by:` trailer.

    An advisory, and it stays one. Whether a given edit needed a foreign
    reviewer is a judgment about the edit, and a script that answered it would
    be certifying the thing the procedure exists to make someone look at.
    """
    if since is None:
        since = last_reviewed()
        if since is None:
            _fail(
                "no commit in the last 500 carries a Reviewed-by: trailer; "
                "pass --since REF to say where to start"
            )
        since = f"{since}..HEAD"
    elif ".." not in since:
        since = f"{since}..HEAD"
    revs = git("log", "--format=%H", since).split()
    unreviewed = []
    for rev in revs:
        touched = [
            f
            for f in git("show", "--name-only", "--format=", rev).split()
            if f.startswith(_options().REVIEWABLE)
        ]
        if touched and not _trailer(rev, "Reviewed-by"):
            unreviewed.append(
                {
                    "commit": rev[:8],
                    "subject": git("log", "-1", "--format=%s", rev).strip(),
                    "files": len(touched),
                }
            )
    return {"range": since, "commits": len(revs), "unreviewed": unreviewed}


def _uncollected_turns(record, state):
    """Expose saved progress without claiming that an unfinished process is live."""
    collected = {t["turn"] for t in state.get("turns", [])}
    pending = []
    for folder in sorted((record / "turns").glob("[0-9]*")):
        if not folder.is_dir() or not folder.name.isdigit() or folder.name in collected:
            continue
        report = folder / "report" / "report.md"
        body = report.read_text() if report.exists() else ""
        statuses = re.findall(r"(?m)^STATUS:[ \t]*(.*)$", body)
        progress = re.findall(r"(?m)^PROGRESS:.*$", body)
        pending.append(
            {
                "turn": folder.name,
                "report_status": statuses[-1] if statuses else None,
                "progress": progress[-1] if progress else None,
                "report_path": str(report),
                "events_path": str(folder / "events.jsonl"),
                "stderr_path": str(folder / "stderr.log"),
                "process_state": "unknown; uncollected does not mean stopped",
            }
        )
    return pending


def status(name=None, store=None):
    if name is None:
        runs = sorted(
            p.name for p in _settings().ROUNDS.glob("*-review-*") if p.is_dir()
        )
        return {"runs": runs}
    name = resolve_run(name, store)
    record = store_dir_for(name, store)
    if not (record / "run.json").exists():
        return {"run": name, "record": str(record), "note": "no run record here"}
    state = read_json(record / "run.json")
    return {
        "run": name,
        "record": str(record),
        "jailed": state.get("jailed"),
        "config": state["config"],
        "delta": state["delta"],
        "persona": personas.identity(state.get("persona")),
        "kind": state.get("kind", "consistency"),
        "uncollected_turns": _uncollected_turns(record, state),
        "turns": [
            {
                k: t.get(k)
                for k in (
                    "turn",
                    "mode",
                    "parent_turn",
                    "status",
                    "session_id",
                    "quota_stop",
                    "quota_scope",
                    "quota_resets_at",
                    "findings",
                    "progress",
                    "find_only",
                    "report",
                )
            }
            for t in state.get("turns", [])
        ],
        # Named here rather than left for the operator to infer, because the
        # recovery differs: an interrupted turn is continued in its session,
        # a refused one never ran and is relaunched.
        "continuable": [
            t["turn"]
            for t in state.get("turns", [])
            if t.get("quota_stop") == "interrupted" and t.get("session_id")
        ],
    }


HELP = """\
usage: quirework sop review [run | resume | continue | fork | status | audit | prompt] ...

Cross-model editorial review of a delta. Procedure: docs/workflows.md.

  quirework sop review run SCOPE --range A..B --brief FILE --model M
      Open a dated review record under `runs.reviews` and take the first turn.
      --range HEAD reviews the uncommitted working tree instead.
  quirework sop review resume RUN --brief FILE [--range A..B]
      Hand the same reviewer the new commits and let it re-derive whether
      its findings are closed. Do NOT hand it the argument for why it was
      wrong: a reversal after that is not evidence.
  quirework sop review continue RUN [--from TURN]
      Continue a turn a provider quota limit cut in half. It resumes that
      turn's own session with a short notice and no brief, and the reviewer
      keeps writing the SAME report: a quota stop costs the wall-clock wait,
      never the reading and reasoning already banked in the session. Use it
      whenever `status` shows a turn with quota_stop: interrupted; a restart
      pays for that work twice.
  quirework sop review fork RUN --brief FILE
      Branch the session to press a disagreement, leaving the first answer
      intact and quotable. A fork is a side branch: later resumes stay on
      the trunk unless you name the branch with --from TURN.
  quirework sop review status [RUN]
      Show collected turns and saved progress from uncollected turns.
      An uncollected turn may still be running; do not launch a duplicate.
  quirework sop review audit [--since REF]
      Commits touching the configured reviewable paths since the last commit
      carrying a Reviewed-by: trailer that carry none themselves. Advisory:
      whether an edit needed a foreign reviewer is a judgment, not a gate.
  quirework sop review prompt SCOPE --range A..B --brief FILE --model M
      Print the composed prompt and argv, launch nothing.

New runs accept --persona ID (default: the configured project persona), --kind
consistency|procedure, and --paths-file FILE for explicit target paths including
new files. The persona is frozen; resume and fork cannot select another one.

The reviewer must not be the model that made the edit. It runs read-only
with the whole repository visible, and jailed by default so a Claude
reviewer does not carry the operator's MEMORY.md; --no-jail is recorded and
announced, never silent.

All three harnesses resume their own session: codex by subcommand, claude by
--resume, agy by --conversation. Every resumed turn checks the session it
landed in against the one it asked for, so a harness that quietly opened a
new conversation fails loudly instead of branching the round off evidence
nobody read.
"""


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="quirework sop review",
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=HELP,
    )
    parser.add_argument("-h", "--help", action="store_true")
    sub = parser.add_subparsers(dest="action")

    def common(p, *, needs_delta):
        p.add_argument(
            "--brief",
            required=True,
            help="Markdown file: review range and necessary tree context; "
            "reasoning belongs in commit bodies",
        )
        p.add_argument(
            "--range",
            dest="range_spec",
            required=needs_delta,
            default=None,
            help="git range A..B, or HEAD for the working tree",
        )
        p.add_argument("--harness", default="codex", choices=drivers.HARNESSES)
        p.add_argument("--model", default=None)
        if needs_delta:
            p.add_argument("--persona", default="default")
            p.add_argument(
                "--kind", choices=("consistency", "procedure"), default="consistency"
            )
            p.add_argument(
                "--paths-file",
                help="newline-separated target paths, including new files",
            )
        p.add_argument("--effort", default="xhigh")
        p.add_argument("--store", default=None)
        p.add_argument(
            "--no-jail",
            dest="jailed",
            action="store_false",
            help="run outside the bwrap jail; recorded as jailed=false",
        )
        p.add_argument(
            "--timeout",
            type=float,
            default=None,
            help="seconds before the reviewer is killed; its written "
            "report is still banked",
        )

    p = sub.add_parser("run")
    p.add_argument("scope")
    common(p, needs_delta=True)
    p = sub.add_parser("prompt")
    p.add_argument("scope")
    common(p, needs_delta=True)
    for action in ("resume", "fork"):
        p = sub.add_parser(action)
        p.add_argument("run")
        p.add_argument(
            "--from",
            dest="from_turn",
            default=None,
            metavar="TURN",
            help="continue this turn's session instead of the trunk "
            "(the default skips fork turns, so a branch never "
            "silently becomes the trunk)",
        )
        common(p, needs_delta=False)
    # `continue` takes no brief and no range: a quota continuation re-sends
    # nothing, because everything it would re-send is already in the session.
    p = sub.add_parser("continue")
    p.add_argument("run")
    p.add_argument("--from", dest="from_turn", default=None, metavar="TURN")
    p.add_argument("--store", default=None)
    p.add_argument("--no-jail", dest="jailed", action="store_false")
    p.add_argument("--timeout", type=float, default=None)
    p = sub.add_parser("status")
    p.add_argument("run", nargs="?")
    p.add_argument("--store", default=None)
    p = sub.add_parser("audit")
    p.add_argument(
        "--since",
        default=None,
        help="ref or range; default is since the last reviewed commit",
    )

    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(HELP)
        return 0
    args = parser.parse_args(argv)
    if getattr(args, "help", False):
        print(HELP)
        return 0

    try:
        if args.action in ("run", "prompt"):
            if not args.model:
                _fail(
                    "--model is required: the reviewer must not be the model "
                    "that made the edit, so there is no safe default"
                )
            result = start(
                args.scope,
                range_spec=args.range_spec,
                brief=args.brief,
                harness=args.harness,
                model=args.model,
                effort=args.effort,
                store=args.store,
                jailed=args.jailed,
                timeout=args.timeout,
                dry_run=args.action == "prompt",
                persona=args.persona,
                kind=args.kind,
                paths=Path(args.paths_file).read_text().splitlines()
                if args.paths_file
                else None,
            )
            if args.action == "prompt":
                print(result["prompt"])
                print("\n--- argv ---")
                print(json.dumps(result["argv"], indent=2))
                return 0
        elif args.action in ("resume", "fork"):
            name = resolve_run(args.run, args.store)
            delta = resolve_range(args.range_spec) if args.range_spec else None
            result = _turn(
                name,
                args.action,
                Path(args.brief).read_text(),
                store=args.store,
                jailed=args.jailed,
                timeout=args.timeout,
                delta=delta,
                from_turn=args.from_turn,
            )
        elif args.action == "continue":
            name = resolve_run(args.run, args.store)
            result = _turn(
                name,
                "continue",
                "",
                store=args.store,
                jailed=args.jailed,
                timeout=args.timeout,
                from_turn=args.from_turn,
            )
        elif args.action == "status":
            result = status(args.run, args.store)
        elif args.action == "audit":
            result = audit(args.since)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        else:
            print(HELP)
            return 0
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("status", "completed") == "completed" else 1
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"review: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
