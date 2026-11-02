"""A reviewer's tool policy: recorded at start, honoured by every harness."""

import pytest

from quirework.providers import canary as canaries
from quirework.sops import review

CODEX = {
    "harness": "codex",
    "model": "demo-model",
    "effort": "high",
    "max_context_window": 400_000,
}
CLAUDE = {"harness": "claude", "model": "demo-model", "effort": "high"}


def _claude_tools(args):
    return args[args.index("--tools") + 1], args[args.index("--allowedTools") + 1]


def test_a_review_that_withholds_the_web_gets_none(tmp_path):
    # A record from before the policy existed ran with no web either.
    for config in (CODEX, {**CODEX, "tools": "review"}):
        args, _ = review.command(config, "run", None, tmp_path)
        assert 'web_search="disabled"' in args
        assert 'web_search="live"' not in args
    for config in (CLAUDE, {**CLAUDE, "tools": "review"}):
        listed, allowed = _claude_tools(
            review.command(config, "run", None, tmp_path)[0]
        )
        assert listed == allowed == "Bash,Read,Glob,Grep,Write"


def test_a_research_review_gets_each_harness_its_web_tools(tmp_path):
    for mode, session in (("run", None), ("resume", "s-1"), ("fork", "s-1")):
        args, _ = review.command(
            {**CODEX, "tools": "research"}, mode, session, tmp_path
        )
        assert 'web_search="live"' in args
        assert 'web_search="disabled"' not in args
        listed, allowed = _claude_tools(
            review.command({**CLAUDE, "tools": "research"}, mode, session, tmp_path)[0]
        )
        assert listed == allowed == "Bash,Read,Glob,Grep,Write,WebSearch,WebFetch"


def test_the_recorded_policy_is_true_of_the_runner():
    # A new run gets the web unless it asks for review.
    assert review.review_tools("codex") == "research"
    assert review.review_tools("claude") == "research"
    assert review.review_tools("codex", "review") == "review"
    assert review.review_tools("claude", "research") == "research"
    # agy keeps its own tools under any flags, so its record says so.
    assert review.review_tools("agy") == "unconfined"
    assert review.review_tools("agy", "research") == "unconfined"
    with pytest.raises(ValueError, match="agy cannot confine"):
        review.review_tools("agy", "review")
    with pytest.raises(ValueError, match="--tools must be one of"):
        review.review_tools("codex", "browse")


def test_the_canary_proves_the_policy_the_run_records(tmp_path, monkeypatch):
    seen = {}

    def require(label, runner, build, contract=canaries.BLIND, **_):
        seen[runner] = (contract, build("prompt", "canary-model", tmp_path, "s").argv)

    monkeypatch.setattr(canaries, "require", require)
    review.canary({**CODEX, "tools": "research"}, "review x")
    contract, argv = seen["codex"]
    assert contract.web and contract.tree and contract.context
    assert 'web_search="live"' in argv
    review.canary(CODEX, "review y")
    contract, argv = seen["codex"]
    assert not contract.web
    assert 'web_search="disabled"' in argv


def test_the_launch_line_names_the_tools():
    assert "tools=research" in review.launch_summary(
        {**CODEX, "tools": "research"}, True
    )
    # A record from before the policy existed ran with no web tools.
    assert "tools=review" in review.launch_summary(CODEX, True)


def test_an_old_record_reads_as_the_policy_it_ran_with():
    # The new-run default is never read into a record that predates it.
    assert review.recorded_tools(CODEX) == "review"
    assert review.recorded_tools(CLAUDE) == "review"
    assert review.recorded_tools({"harness": "agy", "model": "m"}) == "unconfined"
    assert review.recorded_tools({**CODEX, "tools": "research"}) == "research"
