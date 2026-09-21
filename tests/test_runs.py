"""Run fidelity: independent delivery, explicit forks, and no duplicate reads."""

from quirework.runs import commands as run_commands
import json
from pathlib import Path

import pytest

from quirework.delivery import artifacts
from quirework.runs import engine
from quirework.providers import harness
from quirework.runs.storage import digest
from quirework.runs.storage import hashes


@pytest.fixture
def setup(tmp_path):
    (tmp_path / "first.md").write_text("Mira left a green umbrella.\n")
    (tmp_path / "later.md").write_text("Later the chair moved.\n")
    plan = dict(
        title="Fictitious scene",
        seats=[
            dict(
                id="reader",
                harness="codex",
                model="fixture-model",
                effort="high",
                turns=[
                    dict(
                        id="first",
                        prompt="Read this scene.",
                        materials=[dict(source="first.md", delivery="text")],
                    ),
                    dict(
                        id="later",
                        prompt="What changed?",
                        materials=[dict(source="later.md", delivery="text")],
                    ),
                ],
            )
        ],
    )
    path = tmp_path / "input.json"
    path.write_text(json.dumps(plan))
    return tmp_path, path, plan


def prepared(setup):
    root, path, _ = setup
    dest = root / "record"
    engine.prepare(path, dest, resolve=False)
    return engine.Run(dest)


def test_only_current_turn_is_exposed(setup):
    run = prepared(setup)
    prompt, images, files = run.stage("reader", 0)
    assert "green umbrella" in prompt and "chair moved" not in prompt
    assert not images and not files
    assert not list(run.workspace("reader").rglob("plan.json"))
    assert all(
        "chair moved" not in p.read_text()
        for p in run.workspace("reader").rglob("*.txt")
    )


def test_inputs_are_frozen_even_when_original_changes(setup):
    run = prepared(setup)
    (setup[0] / "first.md").write_text("An unrelated later revision.")
    assert "green umbrella" in run.stage("reader", 0)[0]
    assert run.verify()


def test_tampering_is_rejected(setup):
    run = prepared(setup)
    next((run.root / "inputs").rglob("material.txt")).write_text("changed")
    with pytest.raises(RuntimeError, match="input changed"):
        run.verify()


def test_executor_snapshot_detects_source_mutation(setup):
    run = prepared(setup)
    f = run.root / "executor/src/quirework/runs/engine.py"
    f.write_text(f.read_text() + "\n# mutation\n")
    with pytest.raises(RuntimeError, match="executor changed"):
        run.verify()


def test_existing_destination_is_preserved(setup):
    run = prepared(setup)
    before = hashes(run.root)
    with pytest.raises(ValueError, match="destination exists"):
        engine.prepare(setup[1], run.root, resolve=False)
    assert hashes(run.root) == before


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.update(
            policy=dict(tools="none"),
            library=[dict(source="later.md", delivery="file")],
        ),
        lambda s: s["turns"][0].update(
            materials=[dict(source="first.md", delivery="file")]
        ),
        lambda s: s["turns"][0].update(outputs=["../secret"]),
        lambda s: s["turns"][0].update(outputs=["drafts/result.md"]),
        lambda s: s.update(policy=dict(filesystem="guess")),
    ],
)
def test_invalid_combinations_fail_before_creating_a_run(setup, change):
    root, path, plan = setup
    change(plan["seats"][0])
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        engine.prepare(path, root / "record", resolve=False)
    assert not (root / "record").exists()


def test_unsupported_agy_native_fork_is_not_replayed(setup):
    root, path, plan = setup
    seat = plan["seats"][0]
    seat.update(harness="agy", policy=dict(filesystem="audited"))
    child = dict(seat, id="branch", parent=dict(seat="reader", after="later"))
    plan["seats"].append(child)
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="native fork"):
        engine.prepare(path, root / "record", resolve=False)


def test_pending_turn_is_collected_not_resubmitted(setup, monkeypatch):
    run = prepared(setup)
    state = run.state("reader")
    state["pending"] = dict(turn="first")
    run.save("reader", state)
    seen = []
    monkeypatch.setattr(
        run, "collect_turn", lambda seat, index: seen.append((seat, index))
    )
    monkeypatch.setattr(run, "stage", lambda *a: pytest.fail("would resubmit"))
    run.turn("reader", 0, 1)
    assert seen == [("reader", 0)]


def test_unresolved_turn_blocks_next_reveal(setup):
    run = prepared(setup)
    state = run.state("reader")
    state["pending"] = dict(turn="first")
    run.save("reader", state)
    with pytest.raises(RuntimeError, match="blocks further reveals"):
        run.turn("reader", 1, 1)
    assert not run.workspace("reader").exists()


def test_bank_is_idempotent_and_preserves_receipts(setup, monkeypatch):
    run = prepared(setup)
    state = run.state("reader")
    state["pending"] = dict(turn="first", mode="start", parent_id="sid", exit_code=0)
    run.save("reader", state)
    record = run.record("reader", "first")
    record.mkdir(parents=True)
    native = record / "source.jsonl"
    native.write_text("{}\n")
    monkeypatch.setattr(
        harness,
        "receipt",
        lambda *a: dict(
            session_id="sid",
            model="fixture-model",
            rollout=str(native),
            answer="Green umbrella.",
            forked_from=None,
        ),
    )
    monkeypatch.setattr(harness, "history_image_hashes", lambda *a: [])
    run.collect_turn("reader", 0)
    assert (record / "answers/answer.md").read_text() == "Green umbrella.\n"
    run.turn("reader", 0, 1)  # skips, does not ask for a second answer
    assert len(run.state("reader")["done"]) == 1
    (record / "answers/answer.md").write_text("Revised first impression.")
    with pytest.raises(RuntimeError, match="banked"):
        run.verify()


def test_live_process_prevents_collection(setup, monkeypatch):
    run = prepared(setup)
    state = run.state("reader")
    state["pending"] = dict(turn="first", pid=42, process_identity="token")
    run.save("reader", state)
    monkeypatch.setattr(engine, "process_identity", lambda pid: "token")
    with pytest.raises(RuntimeError, match="still alive"):
        run.collect_turn("reader", 0)


def test_wrong_resume_session_is_not_banked(setup, monkeypatch):
    run = prepared(setup)
    state = run.state("reader")
    state.update(
        session_id="old", pending=dict(turn="first", mode="resume", parent_id="old")
    )
    run.save("reader", state)
    monkeypatch.setattr(harness, "receipt", lambda *a: dict(session_id="different"))
    with pytest.raises(RuntimeError, match="different session"):
        run.collect_turn("reader", 0)
    assert not run.state("reader")["done"]


def test_page_and_text_transforms_are_independent(tmp_path):
    from pypdf import PdfWriter

    pdf = tmp_path / "pages.pdf"
    w = PdfWriter()
    w.add_blank_page(width=120, height=180)
    w.add_blank_page(width=120, height=180)
    with pdf.open("wb") as f:
        w.write(f)
    images = artifacts.prepare(
        [dict(source="pages.pdf", delivery="pages")], tmp_path, tmp_path / "frozen"
    )
    assert len(images) == 2 and all(x["kind"] == "image" for x in images)
    native = artifacts.prepare(
        [dict(source="pages.pdf", delivery="file")], tmp_path, tmp_path / "native"
    )
    assert len(native) == 1 and native[0]["kind"] == "file"


def test_codex_prompt_is_stdin_and_images_precede_json():
    config = dict(harness="codex", model="fixture-model", effort="high")
    policy = engine.policy({})
    prompt = "A large prompt " + ("x" * 140000)
    argv, payload, rendered = harness.command(
        config,
        policy,
        "start",
        "sid",
        prompt,
        [(Path("x.png"), Path("/neutral/x.png"))],
        [],
    )
    assert argv[-1] == "-" and prompt not in argv
    assert argv.index("-i") < argv.index("--json")
    assert payload.decode() == rendered and prompt in rendered
    assert not any("1000000" in s for s in argv)


def test_claude_model_only_on_start_codex_every_turn():
    for runner in ("claude", "codex"):
        config = dict(harness=runner, model="fixture-model", effort="high")
        for mode in ("start", "resume", "fork"):
            argv, _, _ = harness.command(
                config, engine.policy({}), mode, "sid", "Hello", [], []
            )
            if runner == "codex":
                assert "-m" in argv
            else:
                assert ("--model" in argv) == (mode == "start")


def test_a_worker_thread_runs_under_the_callers_project(setup, monkeypatch):
    """A seat runs in a pool thread. It must see the project the operator
    selected, not one re-resolved from wherever the worker happens to stand,
    or a frozen executor's seats read the wrong catalog and account lease."""
    from quirework.project import Project, current_project, project_scope

    run = prepared(setup)
    seen = []
    monkeypatch.setattr(
        engine.Run,
        "run_seat",
        lambda self, name, *a: seen.append(current_project().root) or "done",
    )
    chosen = Project.from_settings(setup[0] / "chosen", {})
    with project_scope(chosen):
        assert run.run(jobs=2) == {"reader": "done"}
    assert seen == [chosen.root]


def test_bootstrap_uses_frozen_executor_not_live_state_interpreter(setup, monkeypatch):
    run = prepared(setup)
    root = run.root
    monkeypatch.setattr(
        engine, "Run", lambda *a: pytest.fail("new code interpreted old state")
    )
    assert engine.frozen_dispatch(root, ["verify", str(root)]) == 0


def test_rejected_attempt_retry_preserves_old_events(setup):
    run = prepared(setup)
    state = run.state("reader")
    state["pending"] = dict(turn="first", phase="receipt-pending")
    run.save("reader", state)
    record = run.record("reader", "first")
    record.mkdir(parents=True)
    (record / "events.jsonl").write_text(
        json.dumps(dict(type="turn.failed", error=dict(code="rate_limit_exceeded")))
        + "\n"
    )
    before = (record / "events.jsonl").read_bytes()
    engine.retry(run, "reader", "first")
    assert (record / "events.jsonl").read_bytes() == before
    assert (record / "retirement.json").exists()
    assert run.state("reader")["pending"] is None
    assert run.record("reader", "first").name == "attempt-001"


def test_ambiguous_request_cannot_be_retried(setup):
    run = prepared(setup)
    state = run.state("reader")
    state["pending"] = dict(turn="first")
    run.save("reader", state)
    record = run.record("reader", "first")
    record.mkdir(parents=True)
    (record / "events.jsonl").write_text(
        '{"type":"thread.started","thread_id":"sid"}\n'
    )
    with pytest.raises(RuntimeError, match="unresolved"):
        engine.retry(run, "reader", "first")


def test_external_answers_are_ordered_and_labeled_unverified(setup):
    root, path, plan = setup
    plan["seats"][0]["mode"] = "external"
    path.write_text(json.dumps(plan))
    run = prepared(setup)
    answer = root / "response.md"
    answer.write_text("A green umbrella.")
    proof = dict(
        session_id="external-session",
        model="fixture-model",
        source="operator-selected UI",
    )
    with pytest.raises(ValueError, match="next"):
        engine.import_answer(run, "reader", "later", answer, proof)
    result = engine.import_answer(run, "reader", "first", answer, proof)
    assert "unverified" in result["verification"]
    with pytest.raises(ValueError, match="next"):
        engine.import_answer(run, "reader", "first", answer, proof)


def test_identical_delivery_converts_once_per_preparation(setup, monkeypatch):
    root, path, plan = setup
    second = dict(plan["seats"][0], id="another-reader")
    plan["seats"].append(second)
    path.write_text(json.dumps(plan))
    original = artifacts.TRANSFORMS["text"]
    calls = []

    def counted(source, directory):
        calls.append(source)
        return original(source, directory)

    monkeypatch.setitem(artifacts.TRANSFORMS, "text", counted)
    prepared(setup)
    assert len(calls) == 2  # two different texts, each shared by both seats


def test_prose_projection_removes_internal_comments_but_keeps_figure_description(
    tmp_path,
):
    source = tmp_path / "chapter.qmd"
    source.write_text(
        '# A scene {#sec-scene}\n\n<!-- internal direction -->\nThe scene.\n\n![A circle](illustrations/circle.svg){#fig-circle fig-alt="A blue circle."}\n'
    )
    result = artifacts.prepare(
        [dict(source="chapter.qmd", delivery="prose")], tmp_path, tmp_path / "frozen"
    )
    text = (tmp_path / "frozen" / result[0]["path"]).read_text()
    assert "internal direction" not in text and "{#" not in text
    assert "The scene." in text and "A blue circle." in text


def test_retired_attempt_cannot_disappear(setup):
    run = prepared(setup)
    state = run.state("reader")
    state["pending"] = dict(turn="first")
    run.save("reader", state)
    record = run.record("reader", "first")
    record.mkdir(parents=True)
    event = record / "events.jsonl"
    event.write_text('{"type":"turn.failed","error":{"code":"rate_limit_exceeded"}}\n')
    engine.retry(run, "reader", "first")
    event.unlink()
    with pytest.raises(RuntimeError, match="retired attempt"):
        run.verify()


def test_unsubmitted_intent_recovers_without_losing_prior_attempt(setup, monkeypatch):
    run = prepared(setup)
    state = run.state("reader")
    state["pending"] = dict(turn="first", phase="launch-intent")
    run.save("reader", state)
    monkeypatch.setattr(harness, "command", lambda *a: (["fixture"], b"", "prompt"))
    monkeypatch.setattr(
        harness, "launch_context", lambda *a: ([], {}, run.private("reader"))
    )

    def fail_spawn(*a, **kw):
        raise OSError("synthetic pre-spawn failure")

    monkeypatch.setattr(engine.subprocess, "Popen", fail_spawn)
    with pytest.raises(OSError, match="pre-spawn"):
        run.turn("reader", 0, 1)
    state = run.state("reader")
    assert state["attempts"]["first"] == 1
    assert state["pending"]["phase"] == "not-submitted"
    assert "0" in state["retired_attempts"]["first"]


def test_a_seat_is_counted_when_it_is_launched_not_when_it_answers(setup, monkeypatch):
    """The usage record is made before the process starts, so a seat the
    provider then refuses is still a launch that happened."""
    from quirework.core import usage

    run = prepared(setup)
    monkeypatch.setattr(harness, "command", lambda *a: (["fixture"], b"", "prompt"))
    monkeypatch.setattr(
        harness, "launch_context", lambda *a: ([], {}, run.private("reader"))
    )
    launched = []
    monkeypatch.setattr(
        usage,
        "seat",
        lambda workflow, config, **kw: launched.append((workflow, config)),
    )

    def fail_spawn(*a, **kw):
        raise OSError("synthetic pre-spawn failure")

    monkeypatch.setattr(engine.subprocess, "Popen", fail_spawn)
    with pytest.raises(OSError, match="pre-spawn"):
        run.turn("reader", 0, 1)
    assert launched == [("run", run.seats["reader"]["config"])]


def test_the_frozen_executor_carries_the_usage_module(setup):
    """The engine records seat launches, so a record replayed by its own
    frozen executor must be able to import the module that writes them."""
    run = prepared(setup)
    assert (run.root / "executor/src/quirework/core/usage.py").is_file()
    assert (run.root / "executor/src/quirework/core/__init__.py").is_file()


def test_relative_record_and_export_paths_survive_dispatch(setup, monkeypatch):
    prepared(setup)
    monkeypatch.chdir(setup[0])
    assert run_commands.main(["inspect", "record"]) == 0
    # An incomplete run must fail as incomplete, not resolve DEST or RECORD
    # inside executor/. This exercises actual subprocess dispatch.
    assert run_commands.main(["export", "record", "exported"]) == 2
    assert not (setup[0] / "exported").exists()


def branched(setup):
    root, path, plan = setup
    plan["seats"].append(
        dict(plan["seats"][0], id="child", parent=dict(seat="reader", after="later"))
    )
    path.write_text(json.dumps(plan))
    run = prepared(setup)
    run.workspace("reader").mkdir(parents=True)
    (run.workspace("reader") / "material.txt").write_text("frozen parent")
    run.private("reader").mkdir(parents=True)
    (run.private("reader") / "session.json").write_text("parent history")
    run.save("reader", dict(done={}, session_id="parent", pending=None, complete=True))
    return run


def test_checkpoint_failure_does_not_publish_partial_state(setup, monkeypatch):
    run = branched(setup)
    real = engine.shutil.copytree

    def interrupted(source, target, *a, **kw):
        Path(target).mkdir(parents=True)
        (Path(target) / "partial").write_text("interrupted")
        raise OSError("synthetic interruption")

    monkeypatch.setattr(engine.shutil, "copytree", interrupted)
    with pytest.raises(OSError, match="interruption"):
        run.checkpoint("child")
    assert not (run.root / "checkpoints/reader").exists()
    assert not run.workspace("child").parent.exists()
    monkeypatch.setattr(engine.shutil, "copytree", real)
    run.checkpoint("child")
    assert hashes(run.private("child")) == hashes(run.private("reader"))
    assert hashes(run.workspace("child")) == hashes(run.workspace("reader"))


def test_existing_partial_child_is_rejected(setup):
    run = branched(setup)
    run.workspace("child").mkdir(parents=True)
    (run.workspace("child") / "partial").write_text("not a checkpoint")
    with pytest.raises(RuntimeError, match="unsubmitted child"):
        run.checkpoint("child")


def test_stale_declared_writer_output_is_not_a_new_contribution(setup, monkeypatch):
    root, path, plan = setup
    plan["seats"][0]["policy"] = dict(tools="write")
    plan["seats"][0]["turns"][0]["outputs"] = ["drafts/chapter.md"]
    path.write_text(json.dumps(plan))
    run = prepared(setup)
    output = run.workspace("reader") / "drafts/chapter.md"
    output.parent.mkdir(parents=True)
    output.write_text("An inherited draft.")
    state = run.state("reader")
    state["pending"] = dict(
        turn="first",
        mode="start",
        parent_id="sid",
        outputs_before={"drafts/chapter.md": digest(output)},
    )
    run.save("reader", state)
    record = run.record("reader", "first")
    record.mkdir(parents=True)
    native = record / "source.jsonl"
    native.write_text("{}\n")
    monkeypatch.setattr(
        harness,
        "receipt",
        lambda *a: dict(
            session_id="sid", model="fixture-model", rollout=str(native), answer="Done."
        ),
    )
    monkeypatch.setattr(harness, "history_image_hashes", lambda *a: [])
    with pytest.raises(RuntimeError, match="not changed"):
        run.collect_turn("reader", 0)
    assert not run.state("reader")["done"]


def test_codex_forks_pin_write_policy_in_config():
    config = dict(harness="codex", model="fixture-model", effort="high")
    for mode in ("start", "resume", "fork"):
        argv, _, _ = harness.command(
            config, engine.policy(dict(tools="write")), mode, "sid", "Write.", [], []
        )
        assert 'sandbox_mode="workspace-write"' in argv


def test_agy_large_text_uses_json_stdin():
    config = dict(harness="agy", model="fixture-model", effort="high")
    text = "Synthetic transport text. " * 8000
    argv, payload, rendered = harness.command(
        config, engine.policy(dict(filesystem="audited")), "start", "sid", text, [], []
    )
    assert text not in argv and "--input-format" in argv
    message = json.loads(payload)
    assert message["event"] == "user"
    assert "--print=" in argv
    assert message["message"]["content"][0]["text"] == rendered


def test_bundle_plan_preserves_manifest_order_and_rejects_reordering(tmp_path):
    import hashlib

    from quirework.book.plans import bundle

    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "part.md").write_text("# Part One\n")
    (artifact / "chapter.md").write_text("# The station\nA scene.\n")
    (tmp_path / "ask.md").write_text("Review the supplied text.")
    entries = [
        {"file": name, "sha256": digest(artifact / name), "figures": []}
        for name in ("part.md", "chapter.md")
    ]
    h = hashlib.sha256(
        b"".join((artifact / r["file"]).read_bytes() for r in entries)
    ).hexdigest()
    manifest = {
        "kind": "codex-multidisc-markdown-png",
        "source_sha": "fictitious",
        "artifact_sha256": h,
        "reading_order": entries,
        "figures": [],
    }
    (artifact / "manifest.json").write_text(json.dumps(manifest))
    output = tmp_path / "plan.json"
    bundle(
        artifact,
        output,
        harness="codex",
        model="fixture-model",
        prompt_file=tmp_path / "ask.md",
    )
    result = json.loads(output.read_text())
    assert [
        Path(m["source"]).name for m in result["seats"][0]["turns"][0]["materials"]
    ] == ["part.md", "chapter.md"]
    manifest["reading_order"].reverse()
    (artifact / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="order or digest"):
        bundle(
            artifact,
            tmp_path / "changed.json",
            harness="codex",
            model="fixture-model",
            prompt_file=tmp_path / "ask.md",
        )


@pytest.mark.parametrize("effort,expected", [(None, "xhigh"), ("high", "high")])
def test_reader_plan_effort_default_and_explicit_override(tmp_path, effort, expected):
    from quirework.book.plans import book

    kit = tmp_path / "kit"
    kit.mkdir()
    (kit / "manifest.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {"stem": "opening", "kind": "chapter", "file": "opening.pdf"}
                ],
                "source_sha": "fictional-revision",
                "dirty": False,
            }
        )
    )
    output = tmp_path / "plan.json"
    options = {} if effort is None else {"effort": effort}
    book(
        kit,
        output,
        harness="codex",
        model="fictional-model",
        instrument="example-reader",
        **options,
    )
    assert json.loads(output.read_text())["seats"][0]["effort"] == expected


@pytest.mark.parametrize("allow", [False, True])
def test_compaction_keeps_completed_answer_and_always_flags_it(tmp_path, allow):
    record = tmp_path / "record"
    private = tmp_path / "private"
    record.mkdir()
    sessions = private / "sessions"
    sessions.mkdir(parents=True)
    stream = [
        {"type": "thread.started", "thread_id": "fixture-session"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Useful finding."},
        },
        {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 12}},
    ]
    native = [
        {
            "type": "turn_context",
            "payload": {
                "model": "fixture-model",
                "effort": "xhigh",
                "sandbox_policy": {"type": "read-only"},
            },
        },
        {"type": "compacted", "timestamp": "fixture-time", "payload": {}},
    ]
    (record / "events.jsonl").write_text("".join(json.dumps(x) + "\n" for x in stream))
    (sessions / "fixture-session.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in native)
    )
    result = harness.receipt(
        {"harness": "codex", "model": "fixture-model", "effort": "xhigh"},
        engine.policy({"allow_compaction": allow}),
        record,
        private,
    )
    assert result["answer"] == "Useful finding."
    assert result["compaction_observed"] is True
    assert result["compaction_policy_satisfied"] is allow
    assert result["red_flags"]


def test_compacted_result_is_banked_and_exported_with_warning(setup, monkeypatch):
    root, path, plan = setup
    plan["seats"][0]["turns"] = plan["seats"][0]["turns"][:1]
    path.write_text(json.dumps(plan))
    run = prepared(setup)
    state = run.state("reader")
    state["pending"] = dict(turn="first", mode="start", parent_id="sid", exit_code=0)
    run.save("reader", state)
    record = run.record("reader", "first")
    record.mkdir(parents=True)
    native = record / "source.jsonl"
    native.write_text('{"type":"compacted"}\n')
    monkeypatch.setattr(
        harness,
        "receipt",
        lambda *args: dict(
            session_id="sid",
            model="fixture-model",
            rollout=str(native),
            answer="An exploratory finding.",
            forked_from=None,
            compaction_observed=True,
            compaction_policy_satisfied=False,
            red_flags=["Compaction requires supervisor review."],
        ),
    )
    monkeypatch.setattr(
        harness,
        "history_image_hashes",
        lambda *args: pytest.fail(
            "Cannot certify intact image history after compaction"
        ),
    )
    run.collect_turn("reader", 0)
    assert run.run_seat("reader") == "completed-with-red-flags"
    assert run.inspect()["seats"]["reader"]["red_flags"]
    exported = root / "exported"
    run.export(exported)
    receipt = json.loads((exported / "receipt.json").read_text())["seats"]["reader"][
        "first"
    ]
    assert receipt["compaction_observed"]
    assert receipt["compaction_policy_satisfied"] is False
    assert receipt["history_images"] is None
    assert receipt["red_flags"]
    assert (
        exported / "reader/first/answer.md"
    ).read_text() == "An exploratory finding.\n"


def test_a_turn_declares_how_many_images_its_material_references(setup):
    """A seat that fetches its own figures is handed none, so `history_images` is
    0 by design and proves nothing. The declared count is what turns the ones it
    opened from a stray tool call into evidence."""
    root, path, plan = setup
    plan["seats"][0]["turns"][0]["referenced_images"] = 2
    path.write_text(json.dumps(plan))
    engine.prepare(path, root / "record", resolve=False)
    frozen = json.loads((root / "record" / "plan.json").read_text())
    assert frozen["seats"][0]["turns"][0]["referenced_images"] == 2
    assert frozen["seats"][0]["turns"][1]["referenced_images"] == 0


@pytest.mark.parametrize("value", [-1, "2", 1.0, None])
def test_a_bad_referenced_image_count_is_refused_before_launch(setup, value):
    root, path, plan = setup
    plan["seats"][0]["turns"][0]["referenced_images"] = value
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="referenced_images"):
        engine.prepare(path, root / "record", resolve=False)
