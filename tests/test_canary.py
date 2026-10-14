"""The launch canary: what it keys on, what it refuses, and how it reads.

No test here starts a runner or spends a token. ``canary.run`` is replaced
where the gate's bookkeeping is under test, and the judging and extraction
are pure functions over answers written out here.
"""

import json
import subprocess
from pathlib import Path
from contextlib import nullcontext

import pytest

from quirework.project import ConfigurationError, Project, project_scope
from quirework.providers import blind, canary


@pytest.fixture
def project(tmp_path, monkeypatch):
    settings = {
        "providers": {"isolation_policy": "isolation.toml"},
    }
    root = tmp_path / "project"
    root.mkdir()
    (root / "isolation.toml").write_text('project_markers = ["kettle"]\n')
    monkeypatch.setattr(blind, "runner_version", lambda name: "1.2.3")
    monkeypatch.setattr(blind, "cheapest_model", lambda name: "cheap")
    with project_scope(Project.from_settings(root, settings)):
        yield root


@pytest.fixture
def runs(monkeypatch):
    """Replace the live seat with a passing answer, and count the seats."""
    calls = []

    def run(runner, build, model, contract, *, timeout=600):
        calls.append(build("PROMPT", model, canary._PLACEHOLDER["workdir"], "SID"))
        return {"ok": True, "problems": [], "inconclusive": False, "kept": None}

    monkeypatch.setattr(canary, "run", run)
    return calls


def seat(tools="Read"):
    """A builder the way a launch site writes one: its own flags, and the
    canary's prompt, model and session wherever the seat has its own."""

    def build(prompt, model, workdir, session):
        return canary.Twin(
            [
                "claude",
                "-p",
                "--model",
                model,
                "--tools",
                tools,
                "--session-id",
                session,
            ],
            prompt.encode(),
        )

    return build


def test_a_passing_canary_is_recorded_once_and_reused(project, runs):
    first = canary.require("run seat a", "claude", seat())
    again = canary.require("run seat b", "claude", seat())
    assert len(runs) == 1, "the second launch relies on the first one's canary"
    assert again == first
    assert first["runner"] == "claude" and first["version"] == "1.2.3"
    assert first["model"] == "cheap"
    assert canary.proven("claude", "1.2.3") == [first]


def test_any_new_release_is_canaried_again(project, runs, monkeypatch):
    """Patch, minor or major: no vendor's numbering says which ones matter."""
    canary.require("seat", "claude", seat())
    monkeypatch.setattr(blind, "runner_version", lambda name: "1.2.4")
    canary.require("seat", "claude", seat())
    assert len(runs) == 2


def test_the_seats_own_flags_are_part_of_the_key(project, runs):
    canary.require("reader", "claude", seat(tools="Read"))
    canary.require("writer", "claude", seat(tools="Read,Write"))
    canary.require("another reader", "claude", seat(tools="Read"))
    assert len(runs) == 2


def test_the_contract_is_part_of_the_key(project, runs):
    canary.require("blind", "claude", seat())
    canary.require("research", "claude", seat(), canary.Contract(web=True))
    assert len(runs) == 2


def test_fresh_runs_the_canary_even_with_a_record(project, runs):
    canary.require("seat", "claude", seat())
    canary.require("seat", "claude", seat(), fresh=True)
    assert len(runs) == 2


@pytest.mark.parametrize(
    "result,said",
    [
        (
            {"ok": False, "problems": ["forbidden tool(s): webfetch"]},
            "NOT blind",
        ),
        (
            {"ok": False, "inconclusive": True, "problems": ["seat did not run"]},
            "proved nothing",
        ),
    ],
)
def test_a_seat_not_proven_blind_is_refused_and_nothing_recorded(
    project, monkeypatch, result, said
):
    result = {"inconclusive": False, "kept": "/kept", **result}
    monkeypatch.setattr(canary, "run", lambda *a, **k: result)
    with pytest.raises(canary.CanaryError, match=said):
        canary.require("seat", "claude", seat())
    assert canary.records() == {}


def test_without_a_cheap_model_nothing_runs(project, runs, monkeypatch):
    """The account default was a Pro tier the first time a model went unnamed."""
    monkeypatch.setattr(blind, "cheapest_model", lambda name: None)
    with pytest.raises(canary.CanaryError, match="cheapest"):
        canary.require("seat", "claude", seat())
    assert runs == []


def test_a_configured_canary_model_resolves_like_a_seats(tmp_path, monkeypatch):
    """A family named once keeps meaning its newest member, so a new release
    of the cheap model is canaried as itself without editing the setting."""
    from quirework.providers import models

    asked = []

    def resolve(harness, requested, effort):
        asked.append((harness, requested, effort))
        return {"model": "kestrel-2"}

    monkeypatch.setattr(models, "resolve", resolve)
    settings = {"providers": {"canary_models": {"codex": "kestrel"}}}
    with project_scope(Project.from_settings(tmp_path, settings)):
        assert blind.cheapest_model("codex") == "kestrel-2"
    assert asked == [("codex", "kestrel", canary.EFFORT)]


def test_a_canary_model_that_does_not_resolve_is_refused(project, runs, monkeypatch):
    """Named but unresolvable is not "no cheap model": the refusal says which
    setting to fix, and nothing runs."""

    def unresolvable(name):
        raise ValueError("the catalog offers no family named kestrel")

    monkeypatch.setattr(blind, "cheapest_model", unresolvable)
    with pytest.raises(canary.CanaryError, match="canary_models.*kestrel"):
        canary.require("seat", "claude", seat())
    assert runs == []


def test_preparing_asks_each_runner_for_its_canary_model_once(monkeypatch):
    """A procedure asks while it prepares, so a plan that could never launch
    is refused before anything is staged. The first seat on a runner names
    the refusal."""
    asked = []

    def cheapest(name):
        asked.append(name)
        return None if name == "agy" else "cheap"

    monkeypatch.setattr(blind, "cheapest_model", cheapest)
    canary.require_models([("seat a", "codex"), ("seat b", "codex")])
    assert asked == ["codex"]
    with pytest.raises(canary.CanaryError, match="^seat c: .*cheapest agy model"):
        canary.require_models([("seat c", "agy"), ("seat d", "agy")])


def test_an_unknown_runner_is_refused(project, runs):
    with pytest.raises(canary.CanaryError, match="no canary knows"):
        canary.require("seat", "kettle-cli", seat())


# --------------------------------------------------------------------------- #
# the jail will not hold a runner the gate has not seen
# --------------------------------------------------------------------------- #
def test_a_runner_jail_needs_a_canary_for_that_runner(tmp_path):
    with pytest.raises(blind.BlindnessError, match="no launch canary for codex"):
        blind._require_canary(["codex"], None)
    with pytest.raises(blind.BlindnessError, match="no launch canary for codex"):
        blind._require_canary(["codex"], {"runner": "claude"})
    blind._require_canary(["codex"], {"runner": "codex"})
    blind._require_canary(
        ["codex", "claude"], [{"runner": "codex"}, {"runner": "claude"}]
    )
    blind._require_canary(["codex"], blind.PROBE)
    blind._require_canary([], None)


# --------------------------------------------------------------------------- #
# reading the answer
# --------------------------------------------------------------------------- #
ANSWER = "READ=FAIL\nTOOLS=Read\nPERSONA=NONE\nCONTEXT=NONE"
TEMPLATE = "READ=<OK if you can read, FAIL if you cannot>\nTOOLS=<names>"


def test_a_stream_answer_is_never_read_from_the_echoed_prompt():
    """The prompt's template says FAIL; taken as the answer, it would pass."""
    stream = "\n".join(
        json.dumps(e)
        for e in (
            {"type": "system", "subtype": "init", "tools": ["Read"]},
            {"type": "user", "message": {"content": [{"text": TEMPLATE}]}},
            {"type": "result", "result": ANSWER},
        )
    )
    said, init = canary.extract(stream)
    assert said == ANSWER and init["tools"] == ["Read"]
    echo_only = json.dumps(
        {"type": "user", "message": {"content": [{"text": TEMPLATE}]}}
    )
    assert canary.extract(echo_only) == ("", None)


def test_each_runner_answers_in_its_own_event():
    codex = json.dumps(
        {"type": "item.completed", "item": {"type": "agent_message", "text": ANSWER}}
    )
    agy = json.dumps({"event": "result", "result": {"response": ANSWER}})
    assert canary.extract(codex)[0] == ANSWER
    assert canary.extract(agy)[0] == ANSWER
    assert canary.extract(ANSWER + "\n")[0] == ANSWER + "\n"


def fields(**over):
    base = dict(READ="FAIL", TOOLS="Read", PERSONA="NONE", CONTEXT="NONE")
    return {**base, **over}


def test_a_blind_seat_may_hold_nothing_of_the_project(project):
    argv = ["claude", "--tools", "Read"]
    assert canary.judge("claude", argv, fields(), canary.BLIND) == []
    assert canary.judge("claude", argv, fields(READ="OK"), canary.BLIND)
    leak = fields(CONTEXT="The kettle book's own guide says")
    assert canary.judge("claude", argv, leak, canary.BLIND)


def test_a_review_may_read_the_tree_and_its_guidance(project):
    review = canary.Contract(tree=True, context=True)
    said = fields(READ="OK", CONTEXT="The kettle book's own guide says")
    assert canary.judge("claude", ["claude"], said, review) == []


def test_web_tools_need_a_research_contract_and_mcp_is_never_allowed(project):
    web = fields(TOOLS="Read, WebFetch")
    assert canary.judge("claude", ["claude"], web, canary.BLIND)
    assert canary.judge("claude", ["claude"], web, canary.Contract(web=True)) == []
    mcp = fields(TOOLS="Read, mcp__drive__search")
    assert canary.judge("claude", ["claude"], mcp, canary.Contract(web=True))


def test_agy_web_tools_are_seen_and_pass_only_an_audited_launch(project):
    """agy's are `search_web` and `read_url_content`, which no other runner's
    names match, so its seats once passed as holding no web."""
    said = fields(TOOLS="run_command, view_file, search_web, read_url_content")
    problems = canary.judge("agy", ["agy"], said, canary.BLIND)
    assert any("search_web" in p and "read_url" in p for p in problems)
    assert any("audits its transcript" in p for p in problems)
    audited = canary.Contract(web_audited=True)
    assert canary.judge("agy", ["agy"], said, audited) == []
    mcp = fields(TOOLS="search_web, mcp__drive__search")
    assert canary.judge("agy", ["agy"], mcp, audited)


def test_an_audited_contract_never_excuses_a_runner_that_can_be_confined(project):
    audited = canary.Contract(web_audited=True)
    for runner, tool in (("claude", "WebFetch"), ("codex", "web_search")):
        said = fields(TOOLS=f"Read, {tool}")
        assert canary.judge(runner, [runner], said, audited)


def test_only_the_launchers_that_audit_agy_accept_its_web_tools(project, monkeypatch):
    from quirework.providers import harness, models

    seen = []
    monkeypatch.setattr(
        canary, "require", lambda label, runner, build, contract: seen.append(contract)
    )
    for name in ("agy", "codex"):
        harness.canary({"harness": name}, {"tools": "write"}, "seat")
        models.canary({"harness": name, "tools": "write"}, "writer")
    agy_seat, agy_writer, codex_seat, codex_writer = seen
    assert agy_seat.web_audited and agy_writer.web_audited
    assert not codex_seat.web_audited and not codex_writer.web_audited
    assert not any(c.web for c in seen)


def test_claude_holding_more_than_its_tools_list_is_a_breach(project):
    """Its own init event, which the model cannot misreport. `--tools ""` once
    fell back to the default set while every seat believed it held nothing."""
    argv = ["claude", "--tools", "Read"]
    init = {"tools": ["Read", "Bash"], "mcp_servers": []}
    problems = canary.judge("claude", argv, fields(), canary.BLIND, init)
    assert any("Bash" in p for p in problems)
    servers = {"tools": ["Read"], "mcp_servers": [{"name": "drive"}]}
    assert canary.judge("claude", argv, fields(), canary.BLIND, servers)


def test_a_tool_its_flags_turn_off_must_be_off(project):
    argv = ["codex", "exec", "-c", "features.view_image=false"]
    said = fields(TOOLS="shell, view_image")
    problems = canary.judge("codex", argv, said, canary.BLIND)
    assert any("view_image" in p for p in problems)
    assert canary.judge("codex", argv, fields(TOOLS="shell"), canary.BLIND) == []


def test_the_codex_personality_is_never_allowed(project):
    said = fields(PERSONA="As Codex, you are an excellent communicator")
    review = canary.Contract(tree=True, context=True, web=True)
    assert canary.judge("codex", ["codex"], said, review)


# --------------------------------------------------------------------------- #
# one canary seat, end to end, with the runner replaced
# --------------------------------------------------------------------------- #
@pytest.fixture
def live(monkeypatch):
    """Lift the suite's ban on live canaries; the runner is replaced instead."""
    monkeypatch.delenv("QUIREWORK_CANARY", raising=False)


def test_a_process_can_forbid_live_canaries(project, monkeypatch):
    """The suite sets it, so a test that forgets to stub the gate spends
    nothing: its launch is refused rather than its runner reached."""
    monkeypatch.setenv("QUIREWORK_CANARY", "0")
    reached = []
    monkeypatch.setattr(canary.subprocess, "run", lambda *a, **k: reached.append(a))
    with pytest.raises(canary.CanaryError, match="QUIREWORK_CANARY=0"):
        canary.require("seat", "claude", seat())
    assert reached == [] and canary.records() == {}


def test_a_canary_seat_runs_its_twin_in_a_fresh_jail(
    project, tmp_path, monkeypatch, live
):
    monkeypatch.setattr(blind, "backend_error", lambda: None)
    monkeypatch.setattr(blind, "ensure_neutral_cwd", lambda: tmp_path)
    monkeypatch.setattr(blind, "session_bus", lambda runners: nullcontext())
    jails = []

    def jail(run_dir, runners=(), bus=None, canary=None, **kw):
        jails.append(dict(run_dir=run_dir, runners=runners, canary=canary))
        return ["jail"]

    monkeypatch.setattr(blind, "jail_argv", jail)
    seen = {}

    def fake(argv, input=None, cwd=None, **kw):
        seen.update(argv=argv, input=input, cwd=cwd)
        seen["file"] = (tmp_path / cwd / "system-prompt.md").read_text()
        out = json.dumps({"type": "result", "result": ANSWER}).encode()
        return subprocess.CompletedProcess(argv, 0, out, b"")

    monkeypatch.setattr(canary.subprocess, "run", fake)

    def build(prompt, model, workdir, session):
        return canary.Twin(
            ["claude", "--model", model, "--tools", "Read"],
            prompt.encode(),
            binds=((workdir, "/tmp/work"),),
            files={"system-prompt.md": canary.SYSTEM_PROMPT},
        )

    result = canary.run("claude", build, "cheap", canary.BLIND)
    assert result["ok"], result["problems"]
    workdir = jails[0]["run_dir"]
    assert jails[0]["runners"] == ["claude"] and jails[0]["canary"] is blind.PROBE
    assert seen["argv"][:4] == ["jail", "--bind", str(workdir), "/tmp/work"]
    assert b"AGENTS.md" in seen["input"], "the prompt names a file to try to read"
    assert seen["file"] == canary.SYSTEM_PROMPT
    assert not workdir.exists(), "a passing canary leaves nothing behind"


def test_a_failing_canary_keeps_its_output(project, tmp_path, monkeypatch, live):
    monkeypatch.setattr(blind, "backend_error", lambda: None)
    monkeypatch.setattr(blind, "ensure_neutral_cwd", lambda: tmp_path)
    monkeypatch.setattr(blind, "session_bus", lambda runners: nullcontext())
    monkeypatch.setattr(blind, "jail_argv", lambda *a, **k: ["jail"])
    out = "READ=OK\nTOOLS=Read\nPERSONA=NONE\nCONTEXT=NONE\n".encode()
    monkeypatch.setattr(
        canary.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, out, b""),
    )
    result = canary.run("claude", seat(), "cheap", canary.BLIND)
    assert not result["ok"] and "READ the project tree" in result["problems"][0]
    assert "READ=OK" in open(result["kept"]).read()
    # The output is what an operator reads; the runner state is not kept.
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".canary-")] == [
        Path(result["kept"]).name
    ]


# --------------------------------------------------------------------------- #
# codex: the account's apps, and the inventory that proves them gone
# --------------------------------------------------------------------------- #
def test_every_codex_seat_turns_off_what_codex_leaves_on(project, tmp_path):
    """The account's apps, sub-agents and image generation, on every seat.

    Codex's `apps` feature gave every seat with tools the account's connected
    mail, calendar, documents and code hosting, with tools that commit and
    delete; no private home or ignored config removes them, because the
    account delivers them. Multi-agent v2 offers every seat `spawn_agent`
    whatever `features.multi_agent` says. One builder that forgets the set is
    a seat that holds them all again.
    """
    from quirework.providers import harness, models
    from quirework.publish import experiment
    from quirework.runs.engine import policy
    from quirework.sops import review

    config = {
        "harness": "codex",
        "model": "m",
        "effort": "low",
        "max_context_window": 400_000,
    }
    built = {
        "review": review.command(config, "run", None, tmp_path)[0],
        "review, resumed": review.command(config, "resume", "s-1", tmp_path)[0],
        "review, unjailed": review.command(config, "run", None, tmp_path, jailed=False)[
            0
        ],
        "workshop": models.command(config, tmp_path, "p")[0],
        "workshop, resumed": models.command(config, tmp_path, "p", resume="s-1")[0],
        "workshop, manual": models.command(config, tmp_path, "p", manual=True)[0],
        "blind run": blind.CANARY_ARGV["codex"]("p", "m"),
        "experiment": experiment.command("codex", "m", "low", "start", None, [], "p")[
            0
        ],
    }
    for tools in ("none", "read", "write", "research"):
        built[f"engine, {tools}"] = harness.command(
            config, policy({"tools": tools}), "start", "s", "p", [], []
        )[0]
    wanted = blind.CODEX_SEAT[1::2]
    assert "features.apps=false" in wanted
    assert "features.plugins=false" in wanted
    assert "features.remote_plugin=false" in wanted
    assert "features.image_generation=false" in wanted
    assert "features.multi_agent_v2.max_concurrent_threads_per_session=1" in wanted
    for name, argv in built.items():
        settings = canary.codex_settings(argv)
        assert all(w in settings for w in wanted), name


def test_codex_settings_carry_only_what_the_app_server_takes():
    argv = ["codex", "exec", "-m", "m", "-c", 'a="b"', "-s", "read-only"]
    argv += ["--enable", "x", "--disable", "apps", "--json", "-"]
    assert canary.codex_settings(argv) == [
        "-c",
        'a="b"',
        "--enable",
        "x",
        "--disable",
        "apps",
    ]


# A stand-in for `codex app-server`: it answers the two requests the canary
# makes, from pages given on its command line.
APP_SERVER = """
import json, sys
pages = json.loads(sys.argv[1])
for line in sys.stdin:
    asked = json.loads(line)
    if asked.get("method") == "initialize":
        print(json.dumps({"id": asked["id"], "result": {}}), flush=True)
    elif asked.get("method") == "mcpServerStatus/list":
        at = int(asked["params"].get("cursor") or 0)
        more = str(at + 1) if at + 1 < len(pages) else None
        page = {"data": pages[at], "nextCursor": more}
        print(json.dumps({"id": asked["id"], "result": page}), flush=True)
"""


def _app_server(tmp_path, pages):
    import sys

    script = tmp_path / "app_server.py"
    script.write_text(APP_SERVER)
    return [sys.executable, str(script), json.dumps(pages)]


def test_the_codex_inventory_is_read_to_its_last_page(tmp_path):
    apps = {"name": "codex_apps", "tools": {"mail.send": {}, "code.commit": {}}}
    pages = [[apps], [{"name": "late", "tools": {}}]]
    servers, why = canary.codex_servers(_app_server(tmp_path, pages), ["codex"])
    assert (servers, why) == ({"codex_apps": 2, "late": 0}, "")
    assert canary.codex_servers(_app_server(tmp_path, [[]]), ["codex"]) == ({}, "")


def test_an_inventory_that_cannot_be_read_proves_nothing(tmp_path):
    import sys

    gone = [sys.executable, "-c", "print('not a protocol')"]
    servers, why = canary.codex_servers(gone, ["codex"])
    assert servers is None and "did not initialize" in why
    silent = [sys.executable, "-c", "import time; time.sleep(30)"]
    servers, why = canary.codex_servers(silent, ["codex"], timeout=1)
    assert servers is None and "within 1s" in why


@pytest.fixture
def codex_seat(project, tmp_path, monkeypatch, live):
    """A codex canary with its jail, its inventory and its exchange replaced."""
    monkeypatch.setattr(blind, "backend_error", lambda: None)
    monkeypatch.setattr(blind, "ensure_neutral_cwd", lambda: tmp_path)
    monkeypatch.setattr(blind, "session_bus", lambda runners: nullcontext())
    monkeypatch.setattr(blind, "jail_argv", lambda *a, **k: ["jail"])
    spent = []

    def exchange(argv, **kw):
        spent.append(argv)
        out = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": ANSWER},
            }
        )
        return subprocess.CompletedProcess(argv, 0, out.encode(), b"")

    monkeypatch.setattr(canary.subprocess, "run", exchange)

    def run(servers, why=""):
        monkeypatch.setattr(canary, "codex_servers", lambda *a, **k: (servers, why))
        build = lambda prompt, model, workdir, session: canary.Twin(  # noqa: E731
            ["codex", "exec", "-c", "features.apps=false"], prompt.encode()
        )
        return canary.run("codex", build, "cheap", canary.BLIND)

    return run, spent


def test_a_codex_seat_holding_a_server_is_refused_unspent(codex_seat):
    run, spent = codex_seat
    result = run({"codex_apps": 12})
    assert not result["ok"] and not result["inconclusive"]
    assert result["problems"] == [
        "the runner loaded MCP server(s): codex_apps (12 tools)"
    ]
    assert spent == [], "a breach found by the inventory costs no model call"
    assert "codex_apps" in open(result["kept"]).read()


def test_a_codex_inventory_that_proves_nothing_is_inconclusive(codex_seat):
    run, spent = codex_seat
    result = run(None, "codex app-server did not initialize")
    assert result["inconclusive"] and "did not initialize" in result["problems"][0]
    assert spent == []


def test_an_empty_codex_inventory_lets_the_exchange_judge(codex_seat):
    run, spent = codex_seat
    result = run({})
    assert result["ok"], result["problems"]
    assert result["servers"] == {} and len(spent) == 1


def test_a_codex_record_carries_its_inventory(project, monkeypatch):
    def run(runner, *args, **kw):
        # Only a codex seat has an inventory read; Claude's is its init event.
        held = {} if runner == "codex" else None
        return {"ok": True, "problems": [], "inconclusive": False, "servers": held}

    monkeypatch.setattr(canary, "run", run)
    build = lambda prompt, model, workdir, session: canary.Twin(["codex"])  # noqa: E731
    assert canary.require("seat", "codex", build)["mcp_servers"] == {}
    claude = canary.require("seat", "claude", seat())
    assert "mcp_servers" not in claude


# --------------------------------------------------------------------------- #
# the committed record this replaces
# --------------------------------------------------------------------------- #
def test_the_retired_validation_record_is_refused(tmp_path):
    settings = {"providers": {"validation_record": "validated.json"}}
    with pytest.raises(ConfigurationError, match="validation_record is retired"):
        Project.from_settings(tmp_path, settings)
