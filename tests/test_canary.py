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
# the committed record this replaces
# --------------------------------------------------------------------------- #
def test_the_retired_validation_record_is_refused(tmp_path):
    settings = {"providers": {"validation_record": "validated.json"}}
    with pytest.raises(ConfigurationError, match="validation_record is retired"):
        Project.from_settings(tmp_path, settings)
