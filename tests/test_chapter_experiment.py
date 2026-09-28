"""Failure cases for the fork boundary; no inference and no private corpus."""

import hashlib
import json
from pathlib import Path

import pytest

from quirework.publish import experiment as exp


def test_snapshot_detects_a_writer_during_copy(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "session").write_text("before")
    original_copy = exp.shutil.copytree

    def changing_copy(src, dst):
        original_copy(src, dst)
        (src / "session").write_text("after")

    monkeypatch.setattr(exp.shutil, "copytree", changing_copy)
    with pytest.raises(RuntimeError, match="changed during snapshot"):
        exp.snapshot(source, tmp_path / "snapshot")


def test_snapshot_is_independent_and_rejects_symlinks(tmp_path):
    source, child = tmp_path / "source", tmp_path / "child"
    source.mkdir()
    (source / "session").write_text("shared past")
    exp.snapshot(source, child)
    (child / "session").write_text("child future")
    assert (source / "session").read_text() == "shared past"
    (source / "leak").symlink_to(child)
    with pytest.raises(RuntimeError, match="symlink"):
        exp.snapshot(source, tmp_path / "bad")


def claude_receipt(tmp_path, auxiliary=True):
    home = tmp_path / "home"
    (home / "projects" / "neutral").mkdir(parents=True)
    (home / "projects" / "neutral" / "fictional-session.jsonl").write_text("{}\n")
    usage = {"claude-opus-fictional": {"outputTokens": 20}}
    if auxiliary:
        usage["claude-haiku-fictional"] = {"outputTokens": 3}
    rows = [
        {
            "type": "system",
            "subtype": "init",
            "model": "claude-opus-fictional",
            "tools": ["Read"],
            "mcp_servers": [],
            "output_style": "default",
        },
        {
            "type": "assistant",
            "message": {
                "model": "claude-opus-fictional",
                "content": [{"type": "text", "text": "answer"}],
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "session_id": "fictional-session",
            "result": "A fictional reader response.",
            "modelUsage": usage,
        },
    ]
    return rows, home


def test_preflight_is_not_attributed_as_a_second_reader(tmp_path):
    rows, home = claude_receipt(tmp_path)
    _, receipt = exp.parse_claude(rows, home, "opus", "high", None)
    assert receipt["model"] == "claude-opus-fictional"
    assert set(receipt["auxiliary_usage"]) == {"claude-haiku-fictional"}


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("fallback", "mixed assistant-model"),
        ("tool", "used a tool"),
        ("mcp", "MCP configuration"),
        ("style", "output style"),
        ("no_usage", "usage receipt"),
        ("refusal", "result is"),
    ],
)
def test_claude_rejects_invalid_reader_receipts(tmp_path, mutation, match):
    rows, home = claude_receipt(tmp_path)
    if mutation == "fallback":
        rows.insert(
            1, {"type": "assistant", "message": {"model": "claude-haiku-fictional"}}
        )
    elif mutation == "tool":
        rows[1]["message"]["content"].append({"type": "tool_use", "name": "Read"})
    elif mutation == "mcp":
        rows[0]["mcp_servers"] = [{"name": "fictional-connector"}]
    elif mutation == "style":
        rows[0]["output_style"] = "Explanatory"
    elif mutation == "no_usage":
        rows[-1]["modelUsage"] = {}
    else:
        rows[-1]["is_error"] = True
    with pytest.raises(RuntimeError, match=match):
        exp.parse_claude(rows, home, "opus", "high", None)


@pytest.mark.parametrize(
    "row",
    [
        {"type": "compacted"},
        {"type": "system", "subtype": "compact_boundary"},
        {"type": "event_msg", "payload": {"type": "context_compacted"}},
    ],
)
def test_compaction_is_a_failure_not_a_warning(row):
    with pytest.raises(RuntimeError, match="compaction"):
        exp.reject_compaction([row])


def make_experiment(tmp_path, monkeypatch):
    record = tmp_path / "record"
    record.mkdir()
    plan = {
        "scratch": str(tmp_path / "runtime"),
        "runner": "codex",
        "model": "fictional-model",
        "effort": "high",
        "runner_version": "test",
        "driver_sha": exp.digest(exp.__file__),
        "blind_sha": exp.digest(exp.blind.__file__),
        "hashes": {},
        "arms": {arm: {"segments": []} for arm in exp.ARMS},
    }
    exp.write_json(record / "plan.json", plan)
    (record / "plan.sha256").write_text(exp.digest(record / "plan.json"))
    monkeypatch.setattr(exp.blind, "runner_version", lambda _: "test")
    return exp.Experiment(record)


def test_assignment_cannot_change_on_resume(tmp_path, monkeypatch):
    experiment = make_experiment(tmp_path, monkeypatch)
    plan = exp.read_json(experiment.record / "plan.json")
    plan["model"] = "different-model"
    exp.write_json(experiment.record / "plan.json", plan)
    with pytest.raises(RuntimeError, match="assignment changed"):
        experiment.verify()


@pytest.mark.parametrize(
    "runner,model",
    [
        ("codex", "gpt-6-astra"),
        ("codex", "gpt-5-6-sol"),
        ("claude", "sonnet"),
    ],
)
def test_chapter_experiment_rejects_non_protocol_models(runner, model):
    with pytest.raises(RuntimeError, match="require model"):
        exp.validate_runner_model(runner, model)


def test_chapter_experiment_models_are_pinned():
    exp.validate_runner_model("codex", "gpt-5.6-sol")
    exp.validate_runner_model("claude", "opus")


def test_progress_is_quiet_by_default(tmp_path, monkeypatch, capsys):
    experiment = make_experiment(tmp_path, monkeypatch)
    experiment.emit("banked")
    assert capsys.readouterr().out == ""
    experiment.emit("complete", terminal=True)
    assert capsys.readouterr().out == "complete\n"
    experiment.progress = True
    experiment.emit("banked")
    assert capsys.readouterr().out == "banked\n"


@pytest.mark.parametrize("action", ["change", "delete"])
def test_bank_preserves_earlier_answers(tmp_path, monkeypatch, action):
    experiment = make_experiment(tmp_path, monkeypatch)
    answer = experiment.record / "previous.md"
    answer.write_text("A prior first impression")
    experiment.state["done"]["previous"] = {
        "answer": answer.name,
        "answer_sha": exp.digest(answer),
    }
    if action == "change":
        answer.write_text("A convenient new first impression")
    else:
        answer.unlink()
    with pytest.raises((RuntimeError, FileNotFoundError)):
        experiment.verify()


def test_missing_receipt_never_resubmits_a_pending_request(tmp_path, monkeypatch):
    experiment = make_experiment(tmp_path, monkeypatch)
    prompt = "A fictional input"
    request_hash = hashlib.sha256(prompt.encode() + b"\0").hexdigest()
    experiment.state["sessions"]["parent"] = "fictional-parent"
    experiment.state["pending"] = {
        "key": "arm-a",
        "request_sha": request_hash,
        "parent_id": "fictional-parent",
    }
    monkeypatch.setattr(exp.time, "sleep", lambda _: None)

    def forbidden(*args, **kwargs):
        pytest.fail("a pending turn was resubmitted")

    monkeypatch.setattr(exp.subprocess, "Popen", forbidden)
    with pytest.raises(RuntimeError, match="thread identity"):
        experiment.turn("a", "arm-a", prompt, [], "fork")


def test_unresolved_turn_blocks_later_reveal(tmp_path, monkeypatch):
    experiment = make_experiment(tmp_path, monkeypatch)
    experiment.state["pending"] = {"key": "earlier"}
    with pytest.raises(RuntimeError, match="cannot skip"):
        experiment.turn("a", "later", "input", [], "fork")


def test_codex_model_drift_and_incomplete_answer_are_rejected(tmp_path):
    rows = [{"type": "thread.started", "thread_id": "fictional-thread"}]
    with pytest.raises(RuntimeError, match="completed answer"):
        exp.parse_codex(rows, tmp_path, "fictional-model", "high", None)
    with pytest.raises(RuntimeError, match="served model"):
        exp.assert_model("codex", "fictional-model", "other-model")
    with pytest.raises(RuntimeError, match="served model"):
        exp.assert_model("claude", "opus", "claude-opus-new", "claude-opus-old")


def test_codex_accepts_only_the_known_completed_transport_fallback():
    rows = [
        {
            "type": "item.completed",
            "item": {
                "type": "error",
                "message": exp.CODEX_TRANSPORT_FALLBACK
                + " stream disconnected before completion",
            },
        }
    ]
    assert exp.codex_item_diagnostics(rows) == [rows[0]["item"]["message"]]
    rows[0]["item"]["message"] = "A different transport or provider error"
    with pytest.raises(RuntimeError, match="unexpected output item"):
        exp.codex_item_diagnostics(rows)


def test_image_transport_preserves_order_and_bytes(tmp_path):
    images = [tmp_path / "first.png", tmp_path / "second.png"]
    for i, path in enumerate(images):
        path.write_bytes(f"fictional image {i}".encode())
    message = json.loads(exp.image_message("same reader prompt", images))
    content = message["message"]["content"]
    assert content[0] == {"type": "text", "text": "same reader prompt"}
    assert [exp.base64.b64decode(i["source"]["data"]) for i in content[1:]] == [
        p.read_bytes() for p in images
    ]


def test_jail_hides_repo_peer_and_other_runtime_home(tmp_path):
    if exp.blind.backend_error():
        pytest.skip(exp.blind.backend_error())
    work = tmp_path / "seat"
    peer = tmp_path / "other-seat"
    work.mkdir()
    peer.mkdir()
    (peer / "reaction.md").write_text("withheld")
    # The project's own guide exists, so a probe that finds nothing proves the
    # jail hid it rather than that it was never there.
    guide = exp.current_project().root / "AGENTS.md"
    guide.parent.mkdir(parents=True, exist_ok=True)
    guide.write_text("a project guide")
    probes = [guide, peer / "reaction.md"]
    for target in probes:
        args = exp.blind.jail_argv(work) + ["test", "-e", str(target)]
        result = exp.subprocess.run(args, capture_output=True)
        assert result.returncode == 1, result.stderr.decode()


def test_image_audit_follows_native_codex_parent_references(tmp_path):
    home = tmp_path / "home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)

    def image_row(data):
        return {
            "type": "response_item",
            "payload": {
                "content": [
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64,"
                        + exp.base64.b64encode(data).decode(),
                    }
                ]
            },
        }

    parent = sessions / "rollout-fictional-parent.jsonl"
    parent.write_text(json.dumps(image_row(b"earlier rendered page")) + "\n")
    child = sessions / "rollout-fictional-child.jsonl"
    child.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {
                    "type": "session_meta",
                    "payload": {"forked_from_id": "fictional-parent"},
                },
                image_row(b"new rendered page"),
            ]
        )
        + "\n"
    )
    got = exp.history_image_hashes(child, home)
    assert set(got) == {
        hashlib.sha256(data).hexdigest()
        for data in (b"earlier rendered page", b"new rendered page")
    }
    parent.unlink()
    with pytest.raises(RuntimeError, match="missing or ambiguous parent"):
        exp.history_image_hashes(child, home)


def test_image_audit_rejects_a_reference_to_an_external_image(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": "https://example.invalid/page.png",
                        }
                    ]
                },
            }
        )
        + "\n"
    )
    with pytest.raises(RuntimeError, match="not self-contained"):
        exp.history_image_hashes(rollout, tmp_path)


def test_image_audit_supports_claude_copied_history(tmp_path):
    rollout = tmp_path / "session.jsonl"
    data = b"fictional rendered page"
    rollout.write_text(
        json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": exp.base64.b64encode(data).decode(),
                            },
                        }
                    ]
                },
            }
        )
        + "\n"
    )
    assert exp.history_image_hashes(rollout, tmp_path) == [
        hashlib.sha256(data).hexdigest()
    ]


@pytest.mark.parametrize("mode,returned", [("fork", "parent"), ("resume", "different")])
def test_wrong_session_identity_cannot_be_banked(tmp_path, monkeypatch, mode, returned):
    experiment = make_experiment(tmp_path, monkeypatch)
    prompt = "A fictional input"
    experiment.state["sessions"] = {"parent": "parent", "a": "parent"}
    experiment.state["pending"] = {
        "key": "arm-a",
        "request_sha": hashlib.sha256(prompt.encode() + b"\0").hexdigest(),
        "parent_id": "parent",
        "exit_code": 0,
    }
    monkeypatch.setattr(
        exp,
        "parse_codex",
        lambda *a: (
            "A sufficiently long fictional reader reaction.",
            {"session_id": returned},
        ),
    )
    with pytest.raises(RuntimeError, match="distinct session|silently created"):
        experiment.turn("a", "arm-a", prompt, [], mode)
    assert "arm-a" not in experiment.state["done"]
    assert not (experiment.record / "arm-a.md").exists()


def test_second_operator_cannot_enter_a_running_record(tmp_path):
    lock_path = tmp_path / ".lock"
    with lock_path.open("w") as owner:
        exp.fcntl.flock(owner, exp.fcntl.LOCK_EX | exp.fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="another operator"):
            exp.main(["run", str(tmp_path)])


def test_claude_child_receives_no_refresh_credential(tmp_path, monkeypatch):
    monkeypatch.setattr(exp.os, "environ", {})
    monkeypatch.setattr(exp.Path, "home", lambda: tmp_path)
    credential = tmp_path / ".claude" / ".credentials.json"
    credential.parent.mkdir()
    exp.write_json(
        credential,
        {
            "claudeAiOauth": {
                "accessToken": "fictional-access-token",
                "refreshToken": "fictional-refresh-token",
                "expiresAt": (exp.time.time() + 3600) * 1000,
            }
        },
    )
    env = exp.claude_environment()
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "fictional-access-token"
    assert "fictional-refresh-token" not in str(env)
    oauth = exp.read_json(credential)
    oauth["claudeAiOauth"]["expiresAt"] = 0
    exp.write_json(credential, oauth)
    with pytest.raises(RuntimeError, match="expiring"):
        exp.claude_environment()


def test_access_only_jail_omits_credential_file_mounts(tmp_path):
    args = exp.blind.jail_argv(tmp_path, runners=["claude"], bind_credentials=False)
    for value in args:
        assert not value.endswith("/.credentials.json")
    assert str(exp.blind.state_root(tmp_path) / "claude") in args


def test_failed_canary_cannot_mark_experiment_complete(tmp_path, monkeypatch):
    experiment = make_experiment(tmp_path, monkeypatch)
    experiment.plan.update(
        prompts={"headings": [], "next": "recall"},
        prefix=[],
        order=list(exp.ARMS),
        arms={arm: {"segments": [{"stem": "canary", "pages": []}]} for arm in exp.ARMS},
        canary_markers=["ultramarine", "cocoa"],
    )
    for arm in exp.ARMS:
        (experiment.record / f"arm-{arm}-00-canary.md").write_text(
            "I do not recall the color or drink."
        )
    for method in ("verify", "checkpoint", "check_parent"):
        monkeypatch.setattr(experiment, method, lambda: None)
    monkeypatch.setattr(experiment, "fork_home", lambda *a: None)
    monkeypatch.setattr(experiment, "turn", lambda *a: None)
    with pytest.raises(RuntimeError, match="did not retain"):
        experiment.run()
    assert not experiment.state.get("complete")
    assert not experiment.state.get("canary_passed")


def test_changed_span_forks_once_then_resumes_each_arm(tmp_path, monkeypatch):
    experiment = make_experiment(tmp_path, monkeypatch)
    experiment.plan.update(
        prompts={"first": "first", "next": "next", "headings": []},
        prefix=[{"stem": "shared", "pages": ["shared.png"]}],
        order=list(exp.ARMS),
        arms={
            arm: {
                "segments": [
                    {"stem": "first-change", "pages": [f"{arm}-first.png"]},
                    {"stem": "last-change", "pages": [f"{arm}-last.png"]},
                ]
            }
            for arm in exp.ARMS
        },
    )
    calls = []
    for method in ("verify", "checkpoint", "check_parent", "fork_home"):
        monkeypatch.setattr(experiment, method, lambda *args: None)
    monkeypatch.setattr(
        experiment,
        "turn",
        lambda seat, key, prompt, pages, mode, headings=(): calls.append(
            (seat, key, mode, tuple(pages))
        ),
    )
    experiment.run()
    assert calls[0][:3] == ("parent", "prefix-00-shared", "start")
    for offset, arm in enumerate(exp.ARMS):
        first, second = calls[1 + offset * 2 : 3 + offset * 2]
        assert first[:3] == (arm, f"arm-{arm}-00-first-change", "fork")
        assert second[:3] == (arm, f"arm-{arm}-01-last-change", "resume")


def test_banked_history_includes_earlier_changed_chapters(tmp_path, monkeypatch):
    experiment = make_experiment(tmp_path, monkeypatch)
    experiment.plan.update(
        prefix=[{"stem": "shared", "pages": ["shared-1.png", "shared-2.png"]}],
        arms={
            "a": {
                "segments": [
                    {"stem": "first-change", "pages": ["original-first.png"]},
                    {"stem": "last-change", "pages": ["original-last.png"]},
                ]
            },
            "b": {"segments": []},
            "c": {"segments": []},
        },
    )
    experiment.state["done"] = {
        "prefix-00-shared": {},
        "arm-a-00-first-change": {},
    }
    assert experiment.banked_history_pages("a") == [
        "shared-1.png",
        "shared-2.png",
        "original-first.png",
    ]


def test_experiment_heading_check_accepts_typographic_apostrophes():
    text = "## Lines I’d underline\n\nA line.\n\n## Couldn’t place\n\nNone.\n"
    assert (
        exp.missing_headings(text, ("## Lines I'd underline", "## Couldn't place"))
        == []
    )
