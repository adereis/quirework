"""The argv each harness's reviewer gets: what it keeps, and its own session.

Only the argv is built; no runner is started and no model is called.
"""

import json

import pytest

from quirework.providers import blind
from quirework.sops import review

CODEX = {
    "harness": "codex",
    "model": "demo-model",
    "effort": "high",
    "max_context_window": 400_000,
}
CLAUDE = {"harness": "claude", "model": "demo-model", "effort": "high"}
AGY = {"harness": "agy", "model": "demo-model", "effort": "high"}
CONFIGS = {"codex": CODEX, "claude": CLAUDE, "agy": AGY}
MODES = (("run", None), ("resume", "s-1"), ("fork", "s-1"))


def _carries(argv, tokens):
    """Whether ``tokens`` appear in ``argv`` together and in this order."""
    n = len(tokens)
    return any(argv[i : i + n] == tokens for i in range(len(argv) - n + 1))


def test_a_claude_reviewer_keeps_the_projects_own_guidance(tmp_path):
    """`--setting-sources ""` is right for a blind seat and wrong here.

    It takes the project's CLAUDE.md with it, and a reviewer that cannot
    read the guidance cannot check a claim against it. The operator's own
    memory is kept out by the jail's private home instead.
    """
    for mode, session in MODES:
        argv, _ = review.command(CLAUDE, mode, session, tmp_path)
        assert blind.NO_SETTING_SOURCES[0] not in argv


def test_a_claude_reviewer_runs_no_hooks_and_leaves_the_shared_settings(tmp_path):
    """A project hook would try to write into the read-only checkout.

    The blind seats' settings are the starting point, and the reviewer's
    copy is its own: turning hooks off for it must not turn them off for
    every blind seat built after it.
    """
    shared = list(blind.BLIND_SETTINGS)
    for mode, session in MODES:
        argv, _ = review.command(CLAUDE, mode, session, tmp_path)
        settings = json.loads(argv[argv.index("--settings") + 1])
        assert settings == {**json.loads(shared[1]), "disableAllHooks": True}
        assert settings["outputStyle"] == "default"
    assert blind.BLIND_SETTINGS == shared


def test_no_codex_reviewer_is_read_only_to_its_harness(tmp_path):
    """Codex's read-only sandbox refuses the report file too.

    The reviewer writes its report as it goes, so a turn killed part-way
    keeps what it found; under `read-only` it would keep nothing. The
    checkout's protection is the jail's read-only bind, never the harness's.
    """
    for jailed in (True, False):
        sandbox = review.codex_sandbox(jailed)
        for mode, session in MODES:
            argv, _ = review.command(CODEX, mode, session, tmp_path, jailed=jailed)
            assert "read-only" not in " ".join(argv)
            assert f'sandbox_mode="{sandbox}"' in argv
            if mode == "run":
                assert argv[argv.index("-s") + 1] == sandbox


@pytest.mark.parametrize(
    "harness, tokens",
    [
        ("codex", ["codex", "exec", "resume", "s-1"]),
        ("claude", ["--resume", "s-1"]),
        ("agy", ["--conversation", "s-1"]),
    ],
)
def test_every_harness_resumes_its_own_session(harness, tokens, tmp_path):
    """A quota stop must cost the turn, never the session, on any harness.

    Each harness names the session its own way, and codex by a subcommand
    rather than a flag.
    """
    argv, _ = review.command(CONFIGS[harness], "resume", "s-1", tmp_path)
    assert _carries(argv, tokens)
    assert "--fork-session" not in argv


def test_a_fork_branches_off_the_session_it_names(tmp_path):
    argv, _ = review.command(CODEX, "fork", "s-1", tmp_path)
    assert argv[:4] == ["codex", "exec", "fork", "s-1"]
    argv, _ = review.command(CLAUDE, "fork", "s-1", tmp_path)
    # Claude forks by resuming the session into a new one.
    assert _carries(argv, ["--resume", "s-1"]) and "--fork-session" in argv


def test_agy_cannot_fork_and_says_so(tmp_path):
    """agy has no native fork, and pretending otherwise would branch nothing."""
    with pytest.raises(ValueError, match="no native fork"):
        review.command(AGY, "fork", "s-1", tmp_path)


@pytest.mark.parametrize(
    "harness, mode",
    [
        ("codex", "resume"),
        ("claude", "resume"),
        ("agy", "resume"),
        ("codex", "fork"),
        ("claude", "fork"),
    ],
)
def test_a_resume_or_fork_without_a_session_is_refused(harness, mode, tmp_path):
    with pytest.raises(ValueError, match=f"a {mode} needs a session id"):
        review.command(CONFIGS[harness], mode, None, tmp_path)
