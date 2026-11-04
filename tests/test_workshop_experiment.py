"""A prose experiment from packet to report; fictitious writers, no model."""

import fcntl
import json
import os
import shlex
import subprocess

import pytest

from quirework.project import Project, project_scope
from quirework.sops import workshop

PERSONA = {
    "schema_version": 1,
    "id": "plain",
    "revision": 1,
    "label": "A test writer",
    "description": "Writes whatever the assignment asks for.",
    "tags": [],
    "prompt": "Write plainly.",
}

KETTLE = "The kettle boils at noon, and the tide is out.\n"


def commit(root, files):
    """A repository holding ``files``, committed once so a revision exists."""
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    for args in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-qm", "x"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True)
    return root


@pytest.fixture
def project(tmp_path):
    """A one-file project, current for the whole test."""
    root = commit(tmp_path / "project", {"kettle.md": KETTLE})
    with project_scope(Project.from_settings(root, {})):
        yield root


def plan_at(tmp_path, project, **changes):
    """Two writers on two harnesses, one declared output, and a persona."""
    plan = {
        "source_root": str(project),
        "source_revision": "HEAD",
        "assignment": "Rewrite the paragraph.",
        "brief": "A fictitious notebook about a kettle.",
        "files": [{"source": "kettle.md", "path": "notebook/kettle.md"}],
        "required_reads": ["notebook/kettle.md"],
        "outputs": [{"path": "drafts/kettle.md", "min_bytes": 5}],
        "persona": {"inline": PERSONA},
        "writers": [
            {"id": "plover", "harness": "codex", "model": "fixture-model"},
            {"id": "curlew", "harness": "claude", "model": "fixture-model"},
        ],
        **changes,
    }
    for writer in plan["writers"]:
        writer.setdefault("effort", "high")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    return path


@pytest.fixture
def experiment(project, tmp_path):
    workshop.prepare(plan_at(tmp_path, project), tmp_path / "run", resolve_models=False)
    return tmp_path / "run"


def state_of(root, writer):
    return workshop.read_json(root / "records" / writer / "state.json")


def set_state(root, writer, **fields):
    state = state_of(root, writer)
    state.update(fields)
    workshop.write_json(root / "records" / writer / "state.json", state)
    return state


def draft(root, writer, text, name="kettle.md"):
    path = root / "writers" / writer / "drafts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# --------------------------------------------------------------------------- #
# the packet
# --------------------------------------------------------------------------- #
def test_a_source_revision_freezes_the_committed_bytes_not_the_working_file(
    project, tmp_path
):
    """An uncommitted edit in the author's checkout never reaches a packet
    that names a revision."""
    (project / "kettle.md").write_text("An edit nobody committed.\n")
    frozen = workshop.prepare(
        plan_at(tmp_path, project), tmp_path / "run", resolve_models=False
    )
    staged = tmp_path / "run" / "source" / "notebook" / "kettle.md"
    assert staged.read_text() == KETTLE
    head = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD"])
    assert frozen["source_revision"] == head.decode().strip()
    assert state_of(tmp_path / "run", "plover")["status"] == "prepared"


def test_without_a_revision_the_working_file_is_frozen_by_hash(project, tmp_path):
    (project / "kettle.md").write_text("An edit nobody committed.\n")
    plan = plan_at(tmp_path, project, source_revision=None)
    frozen = workshop.prepare(plan, tmp_path / "run", resolve_models=False)
    staged = tmp_path / "run" / "source" / "notebook" / "kettle.md"
    assert staged.read_text() == "An edit nobody committed.\n"
    assert frozen["source_revision"] is None
    assert "notebook/kettle.md" in frozen["input_hashes"]


def test_each_writer_has_its_own_files_and_its_own_runtime(experiment):
    plover, curlew = (experiment / "writers" / x for x in ("plover", "curlew"))
    staged = "input/notebook/kettle.md"
    assert (plover / staged).stat().st_ino != (curlew / staged).stat().st_ino
    assert workshop.blind.state_root(plover) != workshop.blind.state_root(curlew)
    draft(experiment, "plover", "A new candidate.")
    assert not (curlew / "drafts" / "kettle.md").exists()


def test_a_changed_input_entry_or_plan_is_refused(experiment):
    (experiment / "writers/plover/input/assignment.md").write_text("Another task")
    with pytest.raises(ValueError, match="writer input changed: plover"):
        workshop.load(experiment, "plover")
    start = experiment / "writers/curlew/START.md"
    start.write_text(start.read_text() + "\nAlso write a poem.\n")
    with pytest.raises(ValueError, match="entry instructions changed: curlew"):
        workshop.load(experiment, "curlew")
    plan = experiment / "plan.json"
    plan.write_text(plan.read_text() + " ")
    with pytest.raises(ValueError, match="frozen plan changed"):
        workshop.load(experiment)


def test_missing_required_reading_leaves_no_experiment_behind(project, tmp_path):
    plan = plan_at(tmp_path, project, required_reads=["notebook/absent.md"])
    with pytest.raises(ValueError, match="required reading is absent"):
        workshop.prepare(plan, tmp_path / "run", resolve_models=False)
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize(
    "path", ["../outside", "/absolute", "drafts/../../escape", "", "."]
)
def test_a_packet_path_cannot_leave_its_directory(path):
    with pytest.raises(ValueError, match="relative path without traversal"):
        workshop.safe_rel(path)


def test_writers_are_named_after_their_models_and_repeats_are_numbered(
    project, tmp_path
):
    seats = [
        {"harness": "codex", "model": "tide-model"},
        {"harness": "codex", "model": "tide-model"},
        {"harness": "claude", "model": "claude-tide"},
    ]
    plan = plan_at(tmp_path, project, writers=seats)
    frozen = workshop.prepare(plan, tmp_path / "run", resolve_models=False)
    names = [item["id"] for item in frozen["writers"]]
    # The harness is named once, even where the model already carries it.
    assert names == ["codex-tide-model", "codex-tide-model-02", "claude-tide"]
    for name in names:
        assert state_of(tmp_path / "run", name)["status"] == "prepared"
        assert (tmp_path / "run" / "records" / name / "manual.sh").is_file()


def test_an_explicit_writer_name_is_reserved_before_any_is_generated(project, tmp_path):
    """The generated name steps aside, wherever the explicit one is listed."""
    seats = [
        {"harness": "codex", "model": "tide-model"},
        {"id": "codex-tide-model", "harness": "codex", "model": "tide-model"},
    ]
    plan = plan_at(tmp_path, project, writers=seats)
    frozen = workshop.prepare(plan, tmp_path / "run", resolve_models=False)
    assert [item["id"] for item in frozen["writers"]] == [
        "codex-tide-model-02",
        "codex-tide-model",
    ]


def test_either_model_name_alone_is_enough(project, tmp_path):
    seats = [
        {"harness": "codex", "requested_model": "tide-model"},
        {"harness": "claude", "model": "claude-tide"},
    ]
    plan = plan_at(tmp_path, project, writers=seats)
    frozen = workshop.prepare(plan, tmp_path / "run", resolve_models=False)
    first, second = frozen["writers"]
    assert first["model"] == first["requested_model"] == "tide-model"
    assert second["model"] == second["requested_model"] == "claude-tide"


def test_a_writer_naming_no_model_is_refused(project, tmp_path):
    plan = plan_at(tmp_path, project, writers=[{"harness": "codex"}])
    with pytest.raises(ValueError, match="requested_model or model"):
        workshop.prepare(plan, tmp_path / "run", resolve_models=False)
    assert not (tmp_path / "run").exists()


def test_an_unknown_tier_is_refused_at_preparation(project, tmp_path):
    seats = [{"harness": "codex", "model": "tide-model", "tier": "luxurious"}]
    plan = plan_at(tmp_path, project, writers=seats)
    with pytest.raises(ValueError, match="tier must be one of"):
        workshop.prepare(plan, tmp_path / "run", resolve_models=False)
    assert not (tmp_path / "run").exists()


# --------------------------------------------------------------------------- #
# the reading order a packet carries
# --------------------------------------------------------------------------- #
QUARTO = (
    "project:\n  type: book\n"
    "book:\n  title: A Harbour Notebook\n  chapters:\n"
    "    - index.qmd\n"
    "    - sections/kettle.qmd\n"
    "    - sections/tide.qmd\n"
    "    - sections/lamp.qmd\n"
)


@pytest.fixture
def book(tmp_path):
    """A four-file Quarto book, current for the whole test."""
    root = commit(
        tmp_path / "book",
        {
            "manuscript/_quarto.yml": QUARTO,
            "manuscript/index.qmd": "# A Harbour Notebook\n",
            "manuscript/sections/kettle.qmd": "# The kettle {#sec-kettle}\n",
            "manuscript/sections/tide.qmd": "# The tide {#sec-tide}\n",
            "manuscript/sections/lamp.qmd": "# The lamp {#sec-lamp}\n",
        },
    )
    settings = {
        "manuscript": {
            "source": "manuscript",
            "quarto": "manuscript/_quarto.yml",
            "chapters": "manuscript/sections",
        }
    }
    with project_scope(Project.from_settings(root, settings)):
        yield root


def staged(*sources):
    return [
        {"source": f"manuscript/{rel}", "path": f"packet/{i}.qmd"}
        for i, rel in enumerate(sources)
    ]


def test_a_packet_of_several_units_carries_the_works_reading_order(book, tmp_path):
    """No file in a packet says which unit follows which, so the packet is
    given the order the work's own registry holds."""
    files = staged("sections/lamp.qmd", "sections/kettle.qmd", "index.qmd")
    plan = plan_at(tmp_path, book, files=files, required_reads=[])
    workshop.prepare(plan, tmp_path / "run", resolve_models=False)
    order = (tmp_path / "run" / "source" / "READING_ORDER.md").read_text()
    # The registry's order and printed numbers, not the plan's order; the
    # unit that was not staged is not listed, and its number is not reused.
    assert order == (
        "# Reading order\n"
        "\n"
        "     A Harbour Notebook\n"
        "  1. The kettle\n"
        "  3. The lamp\n"
        "\n"
        "Front and back matter carry no number.\n"
    )
    writer = tmp_path / "run" / "writers" / "plover" / "input" / "READING_ORDER.md"
    assert writer.read_text() == order


def test_a_packet_of_one_or_two_units_carries_no_reading_order(book, tmp_path):
    files = staged("sections/kettle.qmd", "sections/lamp.qmd")
    plan = plan_at(tmp_path, book, files=files, required_reads=[])
    workshop.prepare(plan, tmp_path / "run", resolve_models=False)
    assert not (tmp_path / "run" / "source" / "READING_ORDER.md").exists()


def test_a_source_without_the_manuscript_gives_no_reading_order(book, tmp_path):
    sources = [
        "manuscript/sections/kettle.qmd",
        "manuscript/sections/tide.qmd",
        "manuscript/sections/lamp.qmd",
    ]
    assert workshop._reading_order(book, sources)
    assert workshop._reading_order(tmp_path / "elsewhere", sources) == ""
    with project_scope(Project.from_settings(book, {})):
        assert workshop._reading_order(book, sources) == ""


# --------------------------------------------------------------------------- #
# collecting a writer
# --------------------------------------------------------------------------- #
EXTERNAL = {
    "session_id": "fictitious-01",
    "model": "fixture-model",
    "provenance": "supplied by hand",
}


def test_a_drafts_directory_that_is_a_symlink_is_refused(experiment, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "private.md").write_text("Nothing a writer wrote.")
    drafts = experiment / "writers/plover/drafts"
    drafts.rmdir()
    drafts.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        workshop.collect(experiment, "plover", external=EXTERNAL)
    assert not list((experiment / "records/plover/artifacts").rglob("*.md"))
    assert state_of(experiment, "plover")["status"] == "failed"


def test_incomplete_outputs_fail_the_writer_but_its_partial_prose_is_kept(
    experiment, monkeypatch
):
    partial = draft(experiment, "plover", "Promising but unfinished.", "partial.md")
    draft(experiment, "plover", "Hm.")  # the declared output, undersized
    monkeypatch.setattr(
        workshop.drivers, "receipt", lambda *a, **k: {"session_id": "s", "model": "m"}
    )
    with pytest.raises(RuntimeError, match="missing or undersized"):
        workshop.collect(experiment, "plover")
    artifacts = experiment / "records/plover/artifacts"
    assert (artifacts / "partial.md").read_text() == partial.read_text()
    assert (artifacts / "kettle.md").read_text() == "Hm."
    assert workshop.inspect(experiment)["writers"]["plover"]["status"] == "failed"


def test_a_stopped_session_is_reported_by_its_harness_not_by_the_absent_file(
    experiment,
):
    """The missing output is a symptom; the transport verdict names the cause,
    so it is the one an operator is told."""
    draft(experiment, "plover", "Half a paragraph.", "partial.md")
    with pytest.raises(RuntimeError, match="no unique completed turn"):
        workshop.collect(experiment, "plover")
    state = state_of(experiment, "plover")
    assert "missing or undersized" not in state["error"]
    assert (experiment / "records/plover/artifacts/partial.md").is_file()


def test_an_external_import_is_labelled_and_a_collected_writer_stays_collected(
    experiment,
):
    draft(experiment, "plover", "The kettle waits.")
    state = workshop.collect(experiment, "plover", external=EXTERNAL)
    assert "not independently verified" in state["receipt"]["transport"]
    # Collecting again returns the banked state rather than collecting anew.
    assert workshop.collect(experiment, "plover") == state
    (experiment / "records/plover/artifacts/kettle.md").write_text("Tampered")
    with pytest.raises(RuntimeError, match="archived output integrity failure"):
        workshop.collect(experiment, "plover")


def test_a_live_writer_is_never_launched_collected_retried_or_resumed(experiment):
    """Its request may still be answered; anything else would submit it twice."""
    me = os.getpid()
    set_state(
        experiment,
        "plover",
        status="running",
        pid=me,
        process_identity=workshop.process_identity(me),
    )
    before = (experiment / "records/plover/state.json").read_bytes()
    with pytest.raises(RuntimeError, match="plover is running"):
        workshop.run_writer(experiment, "plover")
    with pytest.raises(RuntimeError, match="still alive; wait, do not resubmit"):
        workshop.collect(experiment, "plover")
    with pytest.raises(RuntimeError, match="old writer is still running"):
        workshop.retry(experiment, "plover", "plover-02")
    with pytest.raises(RuntimeError, match="still alive; wait, do not resume"):
        workshop.resume(experiment, "plover")
    assert (experiment / "records/plover/state.json").read_bytes() == before
    assert not (experiment / "writers/plover-02").exists()


def test_a_launch_with_no_process_identity_must_be_reconciled_first(experiment):
    """A launch that recorded no process cannot be told apart from one still
    running, so neither collecting it nor retrying it is safe."""
    set_state(experiment, "plover", status="running")
    with pytest.raises(RuntimeError, match="no process identity"):
        workshop.collect(experiment, "plover")
    with pytest.raises(RuntimeError, match="reconcile"):
        workshop.retry(experiment, "plover", "plover-02")
    assert state_of(experiment, "plover")["status"] == "running"


def test_a_retry_is_a_new_writer_and_the_old_record_is_untouched(experiment):
    old = (experiment / "records/curlew/state.json").read_bytes()
    workshop.retry(experiment, "curlew", "curlew-02")
    assert (experiment / "records/curlew/state.json").read_bytes() == old
    retried = state_of(experiment, "curlew-02")
    assert retried["status"] == "prepared"
    assert retried["config"]["retry_of"] == "curlew"
    assert "curlew-02" in workshop.inspect(experiment)["writers"]
    with pytest.raises(ValueError, match="new writer already exists"):
        workshop.retry(experiment, "curlew", "curlew-02")


def test_a_second_operator_is_shut_out_of_a_writer(experiment):
    lock = experiment / "records/plover/operator.lock"
    with workshop.lock(lock):
        with pytest.raises(RuntimeError, match="another operator holds"):
            with workshop.lock(lock):
                pass
        with pytest.raises(RuntimeError, match="another operator holds"):
            workshop.collect(experiment, "plover", external=EXTERNAL)
        with pytest.raises(RuntimeError, match="another operator holds"):
            workshop.run_writer(experiment, "plover")
    assert state_of(experiment, "plover")["status"] == "prepared"


def test_a_writer_run_by_hand_needs_a_terminal_before_anything_launches(
    experiment, monkeypatch, canaries_pass
):
    monkeypatch.setattr(workshop.sys.stdin, "isatty", lambda: False)
    with pytest.raises(RuntimeError, match="requires a terminal"):
        workshop.run_writer(experiment, "plover", manual=True)
    assert state_of(experiment, "plover")["status"] == "prepared"
    assert not (experiment / "records/plover/launch.json").exists()
    assert canaries_pass == []


def test_agy_writers_share_two_slots_across_projects(tmp_path):
    """The slots belong to the account, so a writer launched from another
    project or worktree takes the second slot instead of a pool of its own."""
    first = Project.from_settings(tmp_path / "first", {})
    second = Project.from_settings(tmp_path / "second", {})
    with project_scope(first), workshop.harness_slot("agy"):
        with project_scope(second), workshop.harness_slot("agy"):
            locks = sorted((second.account_state("agy") / ".locks").glob("*.lock"))
            assert len(locks) == 2
            for path in locks:
                with path.open("a+") as handle, pytest.raises(BlockingIOError):
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for path in locks:
        with path.open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_a_timeout_kills_the_writers_whole_group_and_keeps_what_it_wrote(
    experiment, monkeypatch, canaries_pass
):
    """A stalled writer is stopped and failed alone, with its prose banked.
    A shell that outlived its own child would show the group survived."""
    partial = experiment / "writers/plover/drafts/partial.md"
    orphan = experiment / "orphan.pid"
    script = (
        f"printf 'Half a paragraph.' > {shlex.quote(str(partial))}; "
        f"sleep 120 & echo $! > {shlex.quote(str(orphan))}; wait"
    )
    monkeypatch.setattr(
        workshop.drivers, "command", lambda *a, **k: (["sh", "-c", script], b"")
    )
    monkeypatch.setattr(
        workshop.drivers, "launch_context", lambda *a, **k: ([], {}, experiment)
    )
    monkeypatch.setattr(workshop.blind, "runner_version", lambda name: None)
    with pytest.raises(RuntimeError, match="no unique completed turn"):
        workshop.run_writer(experiment, "plover", timeout=1)
    state = state_of(experiment, "plover")
    assert state["status"] == "failed" and state["timed_out_after"] == 1
    kept = experiment / "records/plover/artifacts/partial.md"
    assert kept.read_text() == "Half a paragraph."
    assert workshop.process_identity(int(orphan.read_text())) is None
    assert state_of(experiment, "curlew")["status"] == "prepared"


def test_a_run_launches_one_writer_at_a_time_in_spend_order(
    project, tmp_path, monkeypatch
):
    """Writers sharing an account drain it faster together, and a limit then
    stops all of them at once instead of one."""
    seats = [
        {"id": "plover", "harness": "codex", "model": "fixture-model"},
        {"id": "curlew", "harness": "claude", "model": "fixture-model"},
        {"id": "dunlin", "harness": "codex", "model": "small", "tier": "cheap"},
        {"id": "by-hand", "harness": "claude", "model": "m", "mode": "manual"},
    ]
    plan = plan_at(tmp_path, project, writers=seats)
    workshop.prepare(plan, tmp_path / "run", resolve_models=False)
    order = []
    monkeypatch.setattr(
        workshop,
        "run_writer",
        lambda root, writer, *, timeout=None: (
            order.append(writer) or {"status": "completed"}
        ),
    )
    monkeypatch.setattr(
        workshop,
        "ThreadPoolExecutor",
        lambda *a, **k: pytest.fail("a default run opens no pool"),
    )
    assert workshop.run(tmp_path / "run") == {
        "dunlin": "completed",
        "plover": "completed",
        "curlew": "completed",
    }
    # The cheap writer leads; a writer run by hand is never launched for it.
    assert order == ["dunlin", "plover", "curlew"]


# --------------------------------------------------------------------------- #
# inspecting and reporting
# --------------------------------------------------------------------------- #
def test_inspect_hides_the_resolution_evidence_until_asked(experiment):
    state = state_of(experiment, "plover")
    state["config"]["catalog_entry"] = {"slug": "fixture-model", "window": 400_000}
    workshop.write_json(experiment / "records/plover/state.json", state)
    brief = workshop.inspect(experiment)["writers"]["plover"]["config"]
    assert "catalog_entry" not in brief and brief["model"] == "fixture-model"
    full = workshop.inspect(experiment, full=True)["writers"]["plover"]["config"]
    assert "catalog_entry" in full


def bank(root, writer, files):
    artifacts = root / "records" / writer / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (artifacts / name).write_text(text)


def test_the_report_counts_spend_and_returned_bytes_per_harness(experiment):
    codex = {"input_tokens": 900, "output_tokens": 20, "cached_input_tokens": 800}
    claude = {
        "preflight-model": {"inputTokens": 9, "outputTokens": 1, "costUSD": 0.1},
        "fixture-model": {"inputTokens": 3, "outputTokens": 7, "costUSD": 1.25},
    }
    for writer, usage in (("plover", codex), ("curlew", claude)):
        set_state(
            experiment,
            writer,
            status="completed",
            started_at=10.0,
            ended_at=25.5,
            receipt={"model": f"served-{writer}", "usage": usage},
        )
        bank(experiment, writer, {"kettle.md": "x" * 40, "alternative.md": "y" * 5})
    data = workshop.report(experiment, sizes=True)
    rows = {r["writer"]: r for r in data["writers"]}
    assert rows["plover"]["seconds"] == 15.5
    # A subscription harness reports no price, and none is invented for it.
    assert rows["plover"]["usage"]["cost_usd"] is None
    assert rows["plover"]["usage"]["cached_input"] == 800
    # The declared output is counted apart from whatever else came back.
    assert rows["plover"]["output_bytes"] == {
        "declared": 40,
        "all_files": 45,
        "extra_files": ["alternative.md"],
    }
    # A preflight model's spend is summed in, and stays itemized beside it.
    assert rows["curlew"]["usage"]["cost_usd"] == 1.35
    assert rows["curlew"]["usage"]["by_model"]["preflight-model"]["cost_usd"] == 0.1
    assert rows["curlew"]["model"] == "served-curlew"
    assert "served-curlew" in workshop.report_table(data)


def test_the_report_withholds_byte_counts_until_the_drafts_are_sealed(experiment):
    """Draft sizes are near-unique per writer, so showing them before the
    seal gives the identity key away."""
    bank(experiment, "plover", {"kettle.md": "x" * 40})
    unsealed = workshop.report(experiment)
    assert unsealed["sizes_shown"] is False
    assert all("declared" not in r["output_bytes"] for r in unsealed["writers"])
    rows = workshop.report(experiment, sizes=True)["writers"]
    assert {r["writer"]: r["output_bytes"]["declared"] for r in rows}["plover"] == 40
    workshop.write_json(experiment / "evaluation" / "identity-key.json", {"A": "x"})
    assert workshop.report(experiment)["sizes_shown"] is True
    assert workshop.report(experiment, sizes=False)["sizes_shown"] is False


def test_the_report_measures_the_largest_single_request(experiment):
    """A window bounds one request. A writer's summed counters can be the
    largest in a run while its fullest request is the smallest."""
    record = experiment / "records"
    (record / "plover/transcripts").mkdir(parents=True)
    rollout = [
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "model_context_window": 400_000,
                    "last_token_usage": {
                        "input_tokens": fresh,
                        "cached_input_tokens": cached,
                    },
                },
            },
        }
        for fresh, cached in ((900, 0), (400, 80_000), (500, 20_000))
    ]
    (record / "plover/transcripts/rollout.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rollout)
    )
    stream = [
        {
            "type": "assistant",
            "message": {
                "usage": {
                    "input_tokens": 12,
                    "cache_read_input_tokens": 40_000,
                    "cache_creation_input_tokens": 1_000,
                }
            },
        },
        {"type": "result", "modelUsage": {"m": {"contextWindow": 200_000}}},
    ]
    (record / "curlew/events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in stream)
    )
    data = workshop.report(experiment, sizes=True)
    rows = {r["writer"]: r["context"] for r in data["writers"]}
    assert rows["plover"] == {
        "peak_request": 80_400,
        "window": 400_000,
        "evidence": "rollout token_count.last_token_usage",
    }
    assert rows["curlew"]["peak_request"] == 41_012
    assert rows["curlew"]["window"] == 200_000
    assert "400,000" in workshop.report_table(data)


def test_a_harness_that_reports_no_request_sizes_is_unmeasured_not_small(
    experiment,
):
    state = state_of(experiment, "plover")
    state["config"]["harness"] = "agy"
    workshop.write_json(experiment / "records/plover/state.json", state)
    rows = workshop.report(experiment, sizes=True)["writers"]
    context = {r["writer"]: r["context"] for r in rows}["plover"]
    assert context["peak_request"] is None
    assert "no per-request usage" in context["evidence"]


# --------------------------------------------------------------------------- #
# sealing the drafts
# --------------------------------------------------------------------------- #
def complete(root, writer, text, status="completed"):
    draft(root, writer, text)
    set_state(root, writer, status=status)


def test_anonymize_seals_the_key_and_hands_back_no_mapping(experiment):
    """A label read before the draft ranks the draft; the step exists to stop
    that, and a mapping handed back would spend it."""
    complete(experiment, "plover", "A first draft.\n")
    complete(experiment, "curlew", "A second draft.\n")
    result = workshop.anonymize(experiment, "kettle.md", seed=0)
    assert result["drafts"] == 2 and set(result["letters"]) == {"A", "B"}
    said = json.dumps(result)
    assert "plover" not in said and "curlew" not in said
    key = workshop.read_json(experiment / "evaluation" / "identity-key.json")
    assert sorted(key) == ["A", "B"]
    assert sorted(key.values()) == ["curlew", "plover"]
    sealed = sorted(p.name for p in (experiment / "evaluation" / "anon").iterdir())
    assert sealed == ["A.md", "B.md"]


def test_anonymize_needs_a_completed_draft(experiment):
    draft(experiment, "plover", "Never collected.\n")
    with pytest.raises(ValueError, match="no completed writer produced"):
        workshop.anonymize(experiment, "kettle.md")


def test_anonymize_seals_the_plans_first_output_by_default(experiment):
    complete(experiment, "plover", "A draft.\n")
    assert workshop.anonymize(experiment, seed=0)["drafts"] == 1


def test_anonymize_leaves_out_a_writer_that_did_not_finish(experiment):
    """A failed partial can look complete on disk, and it is not a candidate
    unless asked for."""
    complete(experiment, "plover", "A finished draft.\n")
    complete(experiment, "curlew", "A partial draft.\n", status="failed")
    assert workshop.anonymize(experiment, "kettle.md", seed=0)["drafts"] == 1
    key = workshop.read_json(experiment / "evaluation" / "identity-key.json")
    assert list(key.values()) == ["plover"]
    both = workshop.anonymize(experiment, "kettle.md", seed=0, include_partial=True)
    assert both["drafts"] == 2


def test_anonymize_seals_a_retried_writer_the_frozen_plan_does_not_list(
    experiment,
):
    """A retry cannot enter the hash-checked plan, so the seal reads the
    writers on disk; the failed original still stays out."""
    workshop.retry(experiment, "curlew", "curlew-02")
    complete(experiment, "plover", "A draft.\n")
    complete(experiment, "curlew", "A partial.\n", status="failed")
    complete(experiment, "curlew-02", "The retried draft.\n")
    listed = workshop.read_json(experiment / "plan.json")["writers"]
    assert "curlew-02" not in {item["id"] for item in listed}
    assert workshop.anonymize(experiment, "kettle.md", seed=0)["drafts"] == 2
    key = workshop.read_json(experiment / "evaluation" / "identity-key.json")
    assert sorted(key.values()) == ["curlew-02", "plover"]
