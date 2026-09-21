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
