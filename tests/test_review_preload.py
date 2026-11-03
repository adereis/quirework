"""What a harness loads before a reviewer's prompt, and the proof of it.

The repositories and session records here are synthetic, written for the test.
"""

import json
import subprocess

import pytest

from quirework.project import Project, project_scope
from quirework.providers import preload
from quirework.sops import review

GUIDE = "# Kettle house guide\n\nA kettle boils.\n" + "Ada Kettle fills it.\n" * 2000


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "book"
    (root / "chapters").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "AGENTS.md").write_text(GUIDE)
    (root / "CLAUDE.md").write_text("Read the kettle guide.\n")
    return root


def test_codex_reads_the_chain_from_the_root_and_an_override_wins(repo):
    sub = repo / "chapters"
    (sub / "AGENTS.md").write_text("A chapter guide.\n")
    assert preload.codex_chain(sub) == [repo / "AGENTS.md", sub / "AGENTS.md"]
    (sub / "AGENTS.override.md").write_text("An override.\n")
    assert preload.codex_chain(sub) == [repo / "AGENTS.md", sub / "AGENTS.override.md"]
    # An empty file is no guide.
    (sub / "AGENTS.override.md").write_text("")
    assert preload.codex_chain(sub)[-1] == sub / "AGENTS.md"


def test_the_codex_limit_holds_the_whole_chain_and_never_shrinks(repo, tmp_path):
    size = (repo / "AGENTS.md").stat().st_size
    assert size > preload.CODEX_DOC_DEFAULT
    limit = preload.codex_doc_bytes(repo)
    assert limit >= size and limit % 1024 == 0
    small = tmp_path / "small"
    small.mkdir()
    subprocess.run(["git", "init", "-q", str(small)], check=True)
    (small / "AGENTS.md").write_text("A kettle boils.\n")
    assert preload.codex_doc_bytes(small) == preload.CODEX_DOC_DEFAULT


def test_a_codex_reviewer_is_given_the_limit_its_run_recorded(tmp_path):
    config = {
        "harness": "codex",
        "model": "demo-model",
        "effort": "high",
        "max_context_window": 400_000,
    }
    for mode, session in (("run", None), ("resume", "s-1")):
        args, _ = review.command(
            {**config, "codex_doc_bytes": 45056}, mode, session, tmp_path
        )
        assert "project_doc_max_bytes=45056" in args
        # A record from before the limit was recorded keeps Codex's own.
        args, _ = review.command(config, mode, session, tmp_path)
        assert not any("project_doc_max_bytes" in a for a in args)


def test_each_harness_is_told_what_it_already_holds(repo):
    assert preload.sentence("codex", repo) == (
        "Your harness loaded AGENTS.md into your context, whole, before this "
        "message. Do not read it again."
    )
    # Claude Code loads AGENTS.md only when something asks it to, so a seat
    # on it is told to read the guide its harness left out.
    assert preload.sentence("claude", repo) == (
        "Your harness loaded CLAUDE.md into your context, whole, before this "
        "message. Do not read it again. It did not load AGENTS.md: read it "
        "once, in full."
    )
    # Nothing is claimed where the driver cannot see.
    assert preload.sentence("agy", repo) == (
        "This driver cannot see what your harness loaded into your context "
        "before this message. Read AGENTS.md once, in full, unless your "
        "context already holds it whole."
    )


def test_claude_loads_what_its_guide_imports_and_no_more(repo):
    (repo / "CLAUDE.md").write_text(
        "Start with the kettle guide.\n\n@AGENTS.md\n\n"
        "Never `@chapters/one.md`, and not this either:\n\n```\n@chapters/two.md\n```\n"
    )
    for name in ("one.md", "two.md"):
        (repo / "chapters" / name).write_text("A chapter.\n")
    assert preload.claude_chain(repo) == [repo / "CLAUDE.md", repo / "AGENTS.md"]
    assert preload.sentence("claude", repo) == (
        "Your harness loaded CLAUDE.md and AGENTS.md into your context, whole, "
        "before this message. Do not read them again."
    )


def test_a_prompt_that_asks_for_a_loaded_file_again_is_a_lead(repo):
    said = preload.sentence("codex", repo)
    prompt = (
        f"{said}\n\nRead these first:\n\n  AGENTS.md\n  the guide, sections 2 and 3\n"
        "Read and follow AGENTS.md and the chapter cards.\n"
        "the directory card (AGENTS.md) of any directory you open\n"
    )
    leads = preload.frame_leads(prompt, "codex", repo, skip=said)
    assert [(lead["line"], lead["file"]) for lead in leads] == [
        (5, "AGENTS.md"),
        (7, "AGENTS.md"),
    ]
    assert preload.frame_leads(prompt, "agy", repo) == []


def test_a_frame_names_preloaded_and_the_driver_fills_it(repo):
    (repo / "review.toml").write_text(
        'frame = "{preloaded}\\n\\nReview {delta}. Brief: {brief}. Report: {report}."\n'
    )
    settings = {"sops": {"review_policy": "review.toml"}}
    with project_scope(Project.from_settings(repo, settings)):
        delta = {"kind": "worktree", "spec": "HEAD", "commits": []}
        text = review.compose("Kettles.", delta, repo / "r.md", harness="claude")
    assert text.startswith("Your harness loaded CLAUDE.md into your context")
    assert "It did not load AGENTS.md: read it once, in full." in text


def _codex_rollout(path, root, loaded, reads=()):
    rows = [
        {"type": "session_meta", "payload": {"id": "s-1", "cwd": str(root)}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"{preload.CODEX_HEADER}{root}\n\n<INSTRUCTIONS>\n"
                        f"{loaded}\n</INSTRUCTIONS>",
                    }
                ],
            },
        },
    ]
    for command in reads:
        rows.append(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": json.dumps({"cmd": command}),
                },
            }
        )
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_a_codex_record_proves_the_guide_whole_or_cut(repo, tmp_path):
    whole = _codex_rollout(tmp_path / "whole.jsonl", repo, GUIDE)
    assert preload.receipt("codex", [whole], repo) == [
        {"path": "AGENTS.md", "bytes": len(GUIDE), "loaded": "whole"}
    ]
    cut = _codex_rollout(
        tmp_path / "cut.jsonl",
        repo,
        GUIDE[: preload.CODEX_DOC_DEFAULT],
        reads=("cat AGENTS.md", "sed -n '600,900p' AGENTS.md"),
    )
    files = preload.receipt("codex", [cut], repo)
    assert files[0]["loaded"] == f"cut at {preload.CODEX_DOC_DEFAULT} bytes"
    counts = preload.rereads([cut], ["AGENTS.md"], repo)
    assert counts == {"AGENTS.md": 2}
    assert preload.summary("codex", files, counts) == (
        f"preload: codex loaded AGENTS.md CUT AT {preload.CODEX_DOC_DEFAULT} BYTES "
        f"({len(GUIDE)} bytes on disk), read again 2 times"
    )


def test_a_claude_record_proves_what_it_loaded_and_when(repo, tmp_path):
    record = tmp_path / "session.jsonl"
    rows = [
        {
            "type": "attachment",
            "attachment": {
                "type": "instructions",
                "files": [
                    {
                        "path": str(repo / "CLAUDE.md"),
                        "content": "Read the kettle guide.\n",
                    },
                    {"path": str(repo / "AGENTS.md"), "content": GUIDE},
                ],
            },
        },
        {
            "type": "attachment",
            "attachment": {
                "type": "nested_memory",
                "path": str(repo / "chapters" / "CLAUDE.md"),
                "content": {
                    "path": str(repo / "chapters" / "CLAUDE.md"),
                    "content": "A chapter card.\n",
                },
            },
        },
    ]
    (repo / "chapters" / "CLAUDE.md").write_text("A chapter card.\n")
    record.write_text("".join(json.dumps(r) + "\n" for r in rows))
    files = preload.receipt("claude", [record], repo)
    assert [(f["path"], f["loaded"], f["when"]) for f in files] == [
        ("CLAUDE.md", "whole", "start"),
        ("AGENTS.md", "whole", "start"),
        ("chapters/CLAUDE.md", "whole", "on entry"),
    ]
    assert preload.summary("claude", files, {}) == (
        "preload: claude loaded CLAUDE.md whole; AGENTS.md whole; "
        "on entry, chapters/CLAUDE.md"
    )


def test_a_harness_whose_record_says_nothing_is_not_vouched_for(repo):
    assert preload.receipt("agy", [], repo) is None
    assert preload.summary("agy", None, {}) == (
        "preload not shown: no agy session record read here says what it loaded"
    )


def test_a_codex_run_records_the_limit_and_its_prompt_says_what_is_loaded(
    repo, monkeypatch, capsys
):
    """A dry run builds exactly what a launch would, and launches nothing."""
    from quirework.providers import models

    def resolve(harness, requested, effort, *args, **kwargs):
        return {
            "harness": harness,
            "model": requested,
            "effort": effort,
            "max_context_window": 400_000,
        }

    monkeypatch.setattr(models, "resolve", resolve)
    # No persona: the frame is what is under test.
    monkeypatch.setattr(review.personas, "resolve", lambda reference: None)
    (repo / "review.toml").write_text(
        'frame = "{preloaded}\\n\\nRead these first:\\n\\n  AGENTS.md\\n\\n'
        '{delta} {brief} {report}"\n'
    )
    (repo / "brief.md").write_text("Kettles.\n")
    (repo / "chapters" / "one.md").write_text("A kettle boils.\n")
    settings = {
        "sops": {"review_policy": "review.toml"},
        "runs": {"reviews": "records"},
    }
    with project_scope(Project.from_settings(repo, settings)):
        out = review.start(
            "kettles",
            range_spec="HEAD",
            brief=str(repo / "brief.md"),
            harness="codex",
            model="demo-model",
            effort="high",
            store=repo / ".state",
            jailed=False,
            dry_run=True,
        )
    assert f"project_doc_max_bytes={preload.codex_doc_bytes(repo)}" in out["argv"]
    assert out["prompt"].startswith("Your harness loaded AGENTS.md into your context")
    assert [lead["file"] for lead in out["frame_leads"]] == ["AGENTS.md"]
    assert "the prompt asks for AGENTS.md, which codex loads whole" in (
        capsys.readouterr().err
    )
