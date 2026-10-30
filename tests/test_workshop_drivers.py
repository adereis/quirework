"""Workshop harness contracts; synthetic fixtures, no live inference."""

import json

import pytest

from quirework.providers import models as d


def test_commands_preserve_requested_models_and_manual_file_transport(tmp_path):
    for harness, model, effort in [
        ("codex", "gpt-5.5", "xhigh"),
        ("claude", "claude-opus-4-6", "high"),
        ("agy", "gemini-3.1-pro-high", "high"),
    ]:
        for manual in (False, True):
            args, payload = d.command(
                # `resolve` always carries the catalog's ceiling for codex, so
                # a config without it is one production never builds.
                {
                    "harness": harness,
                    "model": model,
                    "effort": effort,
                    "max_context_window": 400_000,
                },
                tmp_path,
                "Read the supplied files.",
                manual=manual,
            )
            assert model in args
            assert "--fallback-model" not in args
            assert not any("ephemeral" in a for a in args)
            assert (payload is None) == manual


def test_a_research_seat_gets_the_web_only_where_the_harness_can_grant_it(tmp_path):
    base = {"model": "m", "effort": "high", "max_context_window": 400_000}
    args, _ = d.command(
        {**base, "harness": "claude", "tools": "research"}, tmp_path, "x"
    )
    assert "Read,Write,Edit,Glob,Grep,WebSearch,WebFetch" in args
    args, _ = d.command({**base, "harness": "claude"}, tmp_path, "x")
    assert not any("WebSearch" in a for a in args)
    with pytest.raises(ValueError, match="agy cannot confine"):
        d.command({**base, "harness": "agy", "tools": "research"}, tmp_path, "x")


def test_compaction_and_model_substitution_fail():
    with pytest.raises(RuntimeError, match="compacted"):
        d._no_compaction(
            [{"type": "event_msg", "payload": {"type": "context_compacted"}}]
        )
    assert d._model_matches("claude-sonnet-5", "claude-sonnet-5-20260901")
    assert not d._model_matches("claude-sonnet-5", "claude-opus-4-6")


def test_truncated_harness_event_is_not_completion(tmp_path):
    p = tmp_path / "events.jsonl"
    p.write_text('{"type":"turn.completed"}\n{"type":')
    with pytest.raises(json.JSONDecodeError):
        d.json_rows(p)


def test_claude_private_runtime_skips_onboarding_without_copying_user_state(
    tmp_path, monkeypatch
):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    creds = fake_home / ".claude/.credentials.json"
    creds.parent.mkdir()
    creds.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "fictitious-access",
                    "refreshToken": "fictitious-refresh",
                    "expiresAt": 9999999999999,
                }
            }
        )
    )
    from pathlib import Path

    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr(d.blind, "backend_error", lambda: None)
    monkeypatch.setattr(d.blind, "runner_binds", lambda _: ([], []))
    work = tmp_path / "writer"
    work.mkdir()
    private = d.blind.state_root(work) / "claude"

    def jail(*args, **kwargs):
        private.mkdir(parents=True)
        assert kwargs["bind_credentials"] is False
        return ["bwrap"]

    monkeypatch.setattr(d.blind, "jail_argv", jail)
    argv, env, actual = d.launch_context({"harness": "claude"}, work)
    assert actual == private
    runtime = json.loads((private / "runtime-config.json").read_text())
    assert runtime == {"hasCompletedOnboarding": True, "theme": "dark"}
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "fictitious-access"
    assert "fictitious-refresh" not in json.dumps(env)
    assert not (private / ".credentials.json").exists()
    assert "fictitious-access" not in json.dumps(argv)


def test_every_launcher_takes_the_whole_window_and_refuses_compaction():
    """The ceiling, not the session default, and no quiet compaction.

    `review.command` and `workshop_drivers.command` each build their own argv
    and both omitted these, so a review ran at gpt-6-astra's 272k session
    default with compaction free to fire; it did, on a resume, after the spend.
    `runs/harness.py` set both all along, which is what made the omission
    invisible."""
    from quirework.sops import review

    cfg = {
        "harness": "codex",
        "model": "gpt-6-astra",
        "effort": "xhigh",
        "context_window": 272_000,
        "max_context_window": 872_000,
    }
    # The ceiling wins over the session default, for both settings.
    assert d.window_flags(cfg) == [
        "-c",
        "model_context_window=872000",
        "-c",
        "model_auto_compact_token_limit=872000",
        # Without the scope the limit is clamped to 90% and compaction fires
        # anyway, so the pair above is inert on its own.
        "-c",
        'model_auto_compact_token_limit_scope="body_after_prefix"',
    ]
    for args in (
        d.command(cfg, "/tmp/w", "p")[0],
        review.command(cfg, "run", None, "/tmp/r")[0],
    ):
        assert "model_context_window=872000" in args
        assert "model_auto_compact_token_limit=872000" in args
        assert 'model_auto_compact_token_limit_scope="body_after_prefix"' in args
        assert "model_context_window=272000" not in args

    # A missing ceiling is a refusal, never a silent fall back to the default.
    # The session window is not a substitute: a config carrying only
    # `context_window` once emitted 272000 for both settings, which is the
    # number this helper exists to escape.
    for absent in (
        {"harness": "codex", "model": "m"},
        {"harness": "codex", "model": "m", "context_window": 272_000},
    ):
        try:
            d.window_flags(absent)
        except ValueError as e:
            assert "ceiling" in str(e)
        else:
            raise AssertionError(f"a codex run without a ceiling must fail: {absent}")

    # Claude is checked through BOTH launchers' argv, not just the helper: the
    # first fix wired only review's codex branch, so its Claude reviewer kept
    # the omitted setting while the helper's return value looked correct.
    claude = {"harness": "claude", "model": "claude-opus-5", "effort": "xhigh"}
    assert d.window_flags(claude) == ["--autocompact", "1000000"]
    for args in (
        d.command(claude, "/tmp/w", "p")[0],
        review.command(claude, "run", None, "/tmp/r")[0],
    ):
        assert "--autocompact" in args

    assert d.window_flags({"harness": "agy"}) == []


def test_codex_resume_drops_flags_its_subcommand_rejects(tmp_path):
    """`codex exec resume` accepts neither `-s` nor `-C` and refuses the turn.

    The refusal is an argument error on stderr and an empty event stream, so
    it surfaces much later as "no unique completed turn" and looks like a
    model failure rather than a malformed command. The sandbox still travels
    as config; the working directory needs no flag because the jail has
    already put the process inside the workspace.
    """
    config = {
        "harness": "codex",
        "model": "gpt-5.5",
        "effort": "high",
        "max_context_window": 400000,
        "effective_context_window_percent": 100,
    }
    fresh, _ = d.command(config, tmp_path, "write something")
    resumed, _ = d.command(config, tmp_path, "carry on", resume="a-session-id")
    assert "-s" in fresh and "-C" in fresh
    assert "-s" not in resumed and "-C" not in resumed
    assert 'sandbox_mode="workspace-write"' in resumed
    assert resumed[:4] == ["codex", "exec", "resume", "a-session-id"]


def test_family_resolution_rules():
    slugs = [
        "gpt-5.5",
        "gpt-5.4-terra",
        "gpt-5.10-terra",
        "gpt-6-terra-mini",
        "gemini-3.8-flash",
        "gemini-3.1-pro",
        "claude-haiku-4-5-20251001",
        "claude-haiku-5-5",
    ]
    pick = d.newest_in_family
    # Versions compare as numbers, so 5.10 is newer than 5.4, and the
    # plainest member wins: the newer gpt-6-terra-mini is not `terra`.
    assert pick("terra", slugs) == "gpt-5.10-terra"
    assert pick("terra-mini", slugs) == "gpt-6-terra-mini"
    assert pick("Gemini-Pro", slugs) == "gemini-3.1-pro"
    # A date suffix is not a newer version: Haiku 5.5 outranks 4.5's snapshot.
    assert pick("haiku", slugs) == "claude-haiku-5-5"
    assert pick("nova", slugs) is None
    with pytest.raises(
        ValueError, match=r"more than one family \(gemini-flash, gemini-pro\)"
    ):
        pick("gemini", slugs)
    assert d.is_family("gemini-pro") and not d.is_family("gpt-5.6-terra")
    # A served id satisfies a family when it carries every family word.
    # `fable` failed here once, because only three aliases were known.
    assert d._model_matches("fable", "claude-fable-5-1")
    assert d._model_matches("opus", "claude-opus-5-5")
    assert not d._model_matches("opus", "claude-sonnet-5")
    assert d._model_matches("claude-opus-5-5", "claude-opus-5-5-20260901")
    assert not d._model_matches("claude-opus-5", "claude-opus-5-5")
    assert not d._model_matches("opus", None)


def _installed(monkeypatch):
    monkeypatch.setattr(d.shutil, "which", lambda name: f"/usr/bin/{name}")


def test_a_codex_family_resolves_to_its_newest_listed_version(tmp_path, monkeypatch):
    """A plan says `terra`; a Terra released tomorrow needs no code change."""
    from pathlib import Path

    _installed(monkeypatch)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Codex cannot be asked here, so the file it keeps is the catalog.
    monkeypatch.setattr(d, "_codex_live", lambda: (None, "offline"))
    levels = [{"effort": e} for e in ("low", "medium", "high")]
    catalog = tmp_path / ".codex/models_cache.json"
    catalog.parent.mkdir()
    catalog.write_text(
        json.dumps(
            {
                "models": [
                    {"slug": "gpt-5.5", "supported_reasoning_levels": levels},
                    {
                        "slug": "gpt-5.6-terra",
                        "max_context_window": 872000,
                        "supported_reasoning_levels": levels,
                    },
                    {"slug": "gpt-5.4-terra", "supported_reasoning_levels": levels},
                    {"slug": "gpt-6-astra", "supported_reasoning_levels": levels},
                    # Codex's own internals are hidden, never a family's answer.
                    {
                        "slug": "gpt-7-terra",
                        "visibility": "hide",
                        "supported_reasoning_levels": levels,
                    },
                ]
            }
        )
    )
    got = d.resolve("codex", "Terra", "high")
    assert got["model"] == "gpt-5.6-terra"
    assert got["requested_model"] == "Terra"
    assert got["max_context_window"] == 872000
    assert d.model_text(got) == "gpt-5.6-terra (from Terra)"
    assert d.resolve("codex", "astra", "low")["model"] == "gpt-6-astra"
    # An exact slug pins, and prints as itself.
    pinned = d.resolve("codex", "gpt-5.4-terra", "low")
    assert pinned["model"] == "gpt-5.4-terra"
    assert d.model_text(pinned) == "gpt-5.4-terra"
    with pytest.raises(ValueError, match="families gpt, gpt-astra, gpt-terra"):
        d.resolve("codex", "nova", "low")
    assert "because codex debug models failed: offline" in got["catalog_source"]
    catalog.unlink()
    with pytest.raises(ValueError, match="codex login status"):
        d.resolve("codex", "terra", "low")


def _levels():
    return [{"effort": e} for e in ("low", "medium", "high", "xhigh")]


def test_codex_is_asked_before_the_file_it_may_not_have_refreshed(
    tmp_path, monkeypatch, capsys
):
    """The file lags the provider; resolving against it substituted a model."""
    from pathlib import Path

    _installed(monkeypatch)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    stale = tmp_path / ".codex/models_cache.json"
    stale.parent.mkdir()
    stale.write_text(
        json.dumps(
            {
                "fetched_at": "2026-01-01T00:00:00Z",
                "models": [
                    {"slug": "gpt-5.6-sol", "supported_reasoning_levels": _levels()}
                ],
            }
        )
    )
    live = [
        {"slug": "gpt-6-sol", "supported_reasoning_levels": _levels()},
        {"slug": "gpt-5.6-sol", "supported_reasoning_levels": _levels()},
    ]
    monkeypatch.setattr(d, "_codex_live", lambda: (live, None))
    got = d.resolve("codex", "sol", "xhigh")
    assert got["model"] == "gpt-6-sol"
    assert got["catalog_source"] == "codex debug models"
    assert d.resolve("codex", "gpt-6-sol", "xhigh")["model"] == "gpt-6-sol"
    # Only when Codex cannot be asked does the file answer, and it says how old
    # it is, because an old catalog is how the newer model went unseen.
    monkeypatch.setattr(d, "_codex_live", lambda: (None, "exit status 1"))
    fallback = d.resolve("codex", "sol", "xhigh")
    assert fallback["model"] == "gpt-5.6-sol"
    assert "fetched 2026-01-01T00:00:00Z" in fallback["catalog_source"]
    assert "resolving against" in capsys.readouterr().err


def test_a_configured_codex_catalog_is_used_without_asking_codex(tmp_path, monkeypatch):
    from quirework.project import Project, project_scope

    _installed(monkeypatch)
    pinned = tmp_path / "catalog.json"
    pinned.write_text(
        json.dumps(
            {"models": [{"slug": "gpt-5.5", "supported_reasoning_levels": _levels()}]}
        )
    )

    def never():
        raise AssertionError("a configured catalog is a pinned choice")

    monkeypatch.setattr(d, "_codex_live", never)
    settings = {"providers": {"codex": {"catalog": str(pinned)}}}
    with project_scope(Project.from_settings(tmp_path, settings)):
        got = d.resolve("codex", "gpt-5.5", "high")
    assert got["model"] == "gpt-5.5"
    assert got["catalog_source"] == f"{pinned} (configured)"


def test_an_agy_family_resolves_at_the_seat_effort(monkeypatch):
    import subprocess

    _installed(monkeypatch)
    listing = (
        "Fetching available models...\n"
        "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
        "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
        "gemini-3.7-flash-low\tGemini 3.7 Flash (Low)\n"
        "gemini-3.1-pro-high\tGemini 3.1 Pro (High)\n"
        "gemini-3.1-pro-low\tGemini 3.1 Pro (Low)\n"
    )
    monkeypatch.setattr(
        d.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=listing, stderr=""),
    )
    assert d.resolve("agy", "flash", "low")["model"] == "gemini-3.8-flash-low"
    assert d.resolve("agy", "gemini-pro", "high")["model"] == "gemini-3.1-pro-high"
    # Pro has no medium; the refusal names the efforts it has.
    with pytest.raises(
        ValueError, match="gemini-3.1-pro at efforts high, low, not 'medium'"
    ):
        d.resolve("agy", "gemini-pro", "medium")
    with pytest.raises(ValueError, match=r"more than one family"):
        d.resolve("agy", "gemini", "low")
    with pytest.raises(ValueError, match="families gemini-flash, gemini-pro"):
        d.resolve("agy", "gemini-ultra", "low")


def test_claude_shorthand_resolves_without_a_version_list(monkeypatch):
    """`opus-5.5` is what a person says; `claude-opus-5-5` is what the CLI takes."""
    _installed(monkeypatch)
    for said, taken in (
        ("opus-5.5", "claude-opus-5-5"),
        ("Opus-5", "claude-opus-5"),
        ("claude-opus", "opus"),
        ("Fable", "fable"),
        ("claude-unreleased-9", "claude-unreleased-9"),
    ):
        assert d.resolve("claude", said, "high")["model"] == taken, said
    # Neither an alias nor a Claude version: refused here, not at launch.
    for bogus in ("opus5", "gpt-5", "terra"):
        with pytest.raises(ValueError, match="not a name Claude Code accepts"):
            d.resolve("claude", bogus, "high")


def test_a_resumed_reviewer_and_writer_name_the_pinned_version(tmp_path):
    from quirework.sops import review

    config = {
        "harness": "claude",
        "model": "opus",
        "effort": "high",
        "model_pinned": "claude-opus-5-20260601",
    }
    args, _ = review.command(config, "resume", "s-1", tmp_path)
    assert args[args.index("--model") + 1] == "claude-opus-5-20260601"
    args, _ = d.command(config, tmp_path, "carry on", resume="s-1")
    assert args[args.index("--model") + 1] == "claude-opus-5-20260601"


def test_a_declared_claude_model_resolves_verbatim(monkeypatch):
    _installed(monkeypatch)
    got = d.resolve(
        "claude", "RITS/Zai-Org/GLM-5-3", "medium", declared=["rits/zai-org/glm-5-3"]
    )
    assert got["model"] == "rits/zai-org/glm-5-3"
    assert "operator-declared" in got["catalog_entry"]
    # The effort check the claude branch owns still applies to a declared id.
    with pytest.raises(ValueError, match="invalid Claude effort"):
        d.resolve("claude", "glm-5-3", "wild", declared=["glm-5-3"])


def test_an_undeclared_claude_name_is_still_refused(monkeypatch):
    _installed(monkeypatch)
    with pytest.raises(ValueError, match="not a name Claude Code accepts"):
        d.resolve("claude", "glm-5-3", "medium", declared=["another/gateway"])


def test_gateway_env_crosses_and_replaces_the_host_login(monkeypatch):
    from quirework.providers import harness

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fictitious")

    def never():
        raise AssertionError("a gateway seat never borrows the host login")

    monkeypatch.setattr(harness.claude_login, "host_login", never)
    env = harness.environment("claude", ["ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY"])
    assert env["ANTHROPIC_BASE_URL"] == "https://gateway.example"
    assert env["ANTHROPIC_API_KEY"] == "sk-fictitious"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_an_unsourced_gateway_env_refuses_rather_than_falls_back(monkeypatch):
    from quirework.providers import harness

    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="gateway environment not sourced"):
        harness.environment("claude", ["ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY"])


@pytest.mark.parametrize("value", [0, -5, 2.5, False, "64000"])
def test_the_project_output_ceiling_must_be_a_positive_integer(value):
    from quirework.providers import output_ceiling

    with pytest.raises(ValueError, match="providers.claude.output_ceiling"):
        output_ceiling.validate(value)


def test_an_undeclared_ceiling_clears_one_the_operator_left_set():
    from quirework.providers import output_ceiling

    env = {output_ceiling.ENV: "9999"}
    assert output_ceiling.ENV not in output_ceiling.apply(env, None)
    assert output_ceiling.apply({}, 128000)[output_ceiling.ENV] == "128000"


def test_preparing_a_claude_seat_freezes_the_project_ceiling(tmp_path, monkeypatch):
    from quirework.project import Project, project_scope

    _installed(monkeypatch)
    settings = {"providers": {"claude": {"output_ceiling": 128000}}}
    with project_scope(Project.from_settings(tmp_path, settings)):
        assert d.resolve("claude", "opus", "high")["output_ceiling"] == 128000
    # A project that sets none leaves the CLI's default, and says nothing.
    assert "output_ceiling" not in d.resolve("claude", "opus", "high")


def test_a_room_seat_launches_with_the_ceiling_it_was_prepared_with(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(d.blind, "backend_error", lambda: None)
    monkeypatch.setattr(d.blind, "runner_binds", lambda _: ([], []))
    monkeypatch.setattr(d.claude_login, "host_login", lambda: ("tok", None))
    monkeypatch.setenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "9999")
    work = tmp_path / "writer"
    work.mkdir()

    def jail(*args, **kwargs):
        # The real jail creates the private runner home; this one stands in.
        (d.blind.state_root(work) / "claude").mkdir(parents=True, exist_ok=True)
        return ["bwrap"]

    monkeypatch.setattr(d.blind, "jail_argv", jail)
    config = {"harness": "claude", "output_ceiling": 128000}
    _, env, _ = d.launch_context(config, work)
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "128000"
    # A room prepared before seats carried a ceiling runs at the default,
    # and the operator's own setting never reaches it.
    _, env, _ = d.launch_context({"harness": "claude"}, work)
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in env


def test_a_reviewer_launches_with_its_frozen_ceiling(tmp_path):
    from quirework.sops import review

    config = {"harness": "claude", "output_ceiling": 128000}
    _, env, _, _ = review.launch_context(config, tmp_path, tmp_path, jailed=False)
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "128000"


def test_a_run_seat_keeps_the_project_ceiling_in_its_policy(tmp_path, monkeypatch):
    from quirework.project import Project, project_scope
    from quirework.runs import engine

    _installed(monkeypatch)
    monkeypatch.setattr(engine, "canary_model", lambda *a: None)
    settings = {"providers": {"claude": {"output_ceiling": 128000}}}
    seat = dict(
        harness="claude",
        model="opus",
        effort="high",
        turns=[dict(id="t", prompt="Continue the fictitious draft.")],
    )
    plan = dict(
        title="Fictitious read",
        seats=[
            dict(seat, id="inherits"),
            dict(seat, id="declares", policy=dict(output_ceiling=64000)),
        ],
    )
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    with project_scope(Project.from_settings(tmp_path, settings)):
        engine.prepare(path, tmp_path / "build" / "record")
    frozen = json.loads((tmp_path / "build" / "record" / "plan.json").read_text())
    seats = {s["id"]: s for s in frozen["seats"]}
    assert seats["inherits"]["policy"]["output_ceiling"] == 128000
    assert seats["declares"]["policy"]["output_ceiling"] == 64000
    # One home for it in a run record: the policy, never the config too.
    assert all("output_ceiling" not in s["config"] for s in seats.values())


def _agy_turn(*types):
    rows = [dict(step_index=i, type=t, status="DONE") for i, t in enumerate(types)]
    for row in rows:
        if row["type"] == "ERROR_MESSAGE":
            row["error"] = d.AGY_RECOVERABLE
    return rows


def test_an_agy_turn_that_recovered_its_dropped_streams_is_banked():
    result = dict(status="ERROR", error=d.AGY_RECOVERABLE, response="Written.")
    rows = _agy_turn(
        "USER_INPUT", "ERROR_MESSAGE", "ERROR_MESSAGE", "PLANNER_RESPONSE", "GENERIC"
    )
    assert d.agy_recovered(result, rows) == 2
    assert "dropped 2 times" in d.agy_recovered_flag(2)


@pytest.mark.parametrize(
    "result, types",
    [
        # Nothing completed after the last drop.
        (
            dict(status="ERROR", error=None, response="Partly."),
            ("USER_INPUT", "PLANNER_RESPONSE", "ERROR_MESSAGE"),
        ),
        # Another error, not a dropped stream.
        (
            dict(status="ERROR", error="Quota exhausted.", response="x"),
            ("USER_INPUT", "ERROR_MESSAGE", "PLANNER_RESPONSE"),
        ),
        # No reply at all.
        (
            dict(status="ERROR", response=""),
            ("USER_INPUT", "ERROR_MESSAGE", "PLANNER_RESPONSE"),
        ),
    ],
)
def test_an_agy_error_that_did_not_recover_stays_a_failure(result, types):
    result = dict(result)
    result.setdefault("error", d.AGY_RECOVERABLE)
    if result["error"] is None:
        result["error"] = d.AGY_RECOVERABLE
    assert d.agy_recovered(result, _agy_turn(*types)) is None


def test_a_message_agy_cut_is_found_in_its_transcript_and_flagged():
    note = "NOTE: The output was truncated because it was too long. Use a more"
    rows = [
        dict(
            type="USER_INPUT",
            content=f"Chapter one.\n\n<truncated 120 bytes>\n\n{note}",
        ),
        dict(type="PLANNER_RESPONSE", content="The chapter ends mid-sentence."),
    ]
    assert d.agy_input_cuts(rows) == [120]
    assert "120 bytes" in d.agy_input_flags(rows)[0]


def test_material_quoting_a_cut_marker_is_not_a_cut():
    rows = [
        dict(type="USER_INPUT", content="A log line read <truncated 5 bytes> once."),
        dict(
            type="PLANNER_RESPONSE",
            content="<truncated 9 bytes> NOTE: The output was truncated because "
            "it was too long",
        ),
    ]
    assert d.agy_input_cuts(rows) == []
    assert d.agy_input_flags(rows) == []


def test_a_declared_output_ceiling_reaches_the_claude_runner(monkeypatch):
    from quirework.providers import harness

    monkeypatch.setattr(harness.claude_login, "host_login", lambda: ("tok", None))
    env = harness.environment("claude", output_ceiling=128000)
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "128000"
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in harness.environment("claude")


def test_a_gateway_seat_keeps_its_declared_output_ceiling(monkeypatch):
    from quirework.providers import harness

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example")
    env = harness.environment("claude", ["ANTHROPIC_BASE_URL"], 128000)
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "128000"


@pytest.mark.parametrize("name", ["codex", "agy"])
def test_only_a_claude_seat_can_declare_an_output_ceiling(name):
    from quirework.providers import harness
    from quirework.runs import engine

    policy = engine.policy(dict(output_ceiling=128000, filesystem="audited"))
    with pytest.raises(ValueError, match="no output ceiling"):
        harness.validate(dict(harness=name), policy, [])


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "128000"])
def test_an_output_ceiling_must_be_a_positive_integer(value):
    from quirework.runs import engine

    with pytest.raises(ValueError, match="output_ceiling"):
        engine.policy(dict(output_ceiling=value))


def test_a_declared_claude_seat_carries_its_gateway_env_into_the_record(tmp_path):
    from quirework.project import Project, project_scope
    from quirework.runs import engine

    settings = {
        "providers": {
            "claude": {
                "models": ["rits/zai-org/glm-5-3"],
                "gateway_env": ["ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY"],
            }
        }
    }
    plan = dict(
        title="Fictitious read",
        seats=[
            dict(
                id="reader",
                harness="claude",
                model="rits/zai-org/glm-5-3",
                effort="medium",
                policy=dict(tools="read"),
                turns=[dict(id="t", prompt="Continue the fictitious draft.")],
            ),
            dict(
                id="writer",
                harness="claude",
                model="opus",
                effort="high",
                policy=dict(tools="read"),
                turns=[dict(id="t", prompt="Continue the fictitious draft.")],
            ),
        ],
    )
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    with project_scope(Project.from_settings(tmp_path, settings)):
        engine.prepare(path, tmp_path / "build" / "record", resolve=False)
    frozen = json.loads((tmp_path / "build" / "record" / "plan.json").read_text())
    policies = {s["id"]: s["policy"]["gateway_env"] for s in frozen["seats"]}
    assert policies["reader"] == ["ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY"]
    # A seat on an ordinary claude name keeps the empty default (a list,
    # because the frozen record is JSON).
    assert policies["writer"] == []
