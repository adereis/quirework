"""The derived-edition builder, on a synthetic book.

Nothing here launches a model. The parts worth testing are the ones that
decide *what a blind drafter is told*: the bundle it reads, the brief it is
given, and the guard that refuses to hand it the project's own marginalia.
"""

import hashlib
import json

import pytest

from quirework.publish import variant
from quirework.project import Project, project_scope


def _book(tmp_path):
    """A two-chapter book with one sidecar, in the layout the guide teaches."""
    root = tmp_path / "book"
    sections = root / "manuscript" / "sections"
    (sections / "_marks").mkdir(parents=True)
    (root / "manuscript" / "_quarto.yml").write_text(
        "lang: en-US\n"
        "project:\n  type: book\n"
        "book:\n  title: A Small Notebook\n  chapters:\n"
        "    - index.qmd\n"
        "    - sections/opening.qmd\n"
        "    - sections/closing.qmd\n"
    )
    (root / "manuscript" / "index.qmd").write_text("# A Small Notebook {#sec-index}\n")
    (sections / "opening.qmd").write_text(
        "# The opening {#sec-opening}\n\nA kettle boils.\n"
    )
    (sections / "closing.qmd").write_text(
        "# The closing {#sec-closing}\n\nThe kettle cools.\n"
    )
    (sections / "_marks" / "opening.md").write_text(
        "# The opening {#sec-opening}\n\n"
        "<!-- @bind(kettle-signal) -->\n"
        "<!-- @figure(kettle)  A kettle with steam leaving the spout. "
        "Built: illustrations/kettle.svg -->\n"
    )
    procedures = root / "procedures"
    procedures.mkdir()
    (procedures / "variant-common.md").write_text(
        "# Deriving an edition\n\n## Blind-draft brief\n\nShort sentences. No jokes.\n"
    )
    (procedures / "variant-postcard.md").write_text(
        "# The postcard edition\n\n## Blind-draft brief\n\nA reader in a hurry.\n"
    )
    return root


def _project(root, **overrides):
    settings = {
        "manuscript": {
            "source": "manuscript",
            "quarto": "manuscript/_quarto.yml",
            "chapters": "manuscript/sections",
        },
        "publish": {
            "variants": "variants",
        },
        "sops": {"root": "procedures"},
    }
    settings.update(overrides)
    return Project.from_settings(root, settings)


def test_the_bundle_is_the_book_in_reading_order(tmp_path):
    root = _book(tmp_path)
    with project_scope(_project(root)):
        chapters = variant.chapter_order()
        assert [c["slug"] for c in chapters] == ["index", "opening", "closing"]
        assert [c["title"] for c in chapters] == [
            "A Small Notebook",
            "The opening",
            "The closing",
        ]
        bundle = variant.build_bundle(chapters)
    assert "A kettle boils." in bundle
    assert "The kettle cools." in bundle


def test_a_title_is_read_without_its_attribute_block(tmp_path):
    root = _book(tmp_path)
    (root / "manuscript" / "sections" / "opening.qmd").write_text(
        "# Preface {.unnumbered #sec-preface}\n\nA kettle boils.\n"
    )
    with project_scope(_project(root)):
        assert variant.chapter_order()[1]["title"] == "Preface"


def test_a_title_page_with_nothing_to_retell_gets_no_drafter(tmp_path, capsys):
    """It stays in the bundle every drafter reads, and costs no prompt of its own."""
    root = _book(tmp_path)
    with project_scope(_project(root)):
        assert variant.cmd_build(["postcard", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "skip         index" in out and "a title, nothing to retell" in out
    assert "would draft  opening" in out and "would draft  index" not in out
    assert "(2 chapter(s)" in out


def test_the_reading_order_comes_from_the_configured_file(tmp_path):
    """A book that keeps its Quarto config under another name is still read."""
    root = _book(tmp_path)
    (root / "manuscript" / "_quarto.yml").rename(root / "manuscript" / "book.yml")
    project = _project(root)
    with project_scope(project):
        with pytest.raises(FileNotFoundError):
            variant.chapter_order()
    settings = {
        "manuscript": {
            "source": "manuscript",
            "chapters": "manuscript/sections",
            "quarto": "manuscript/book.yml",
        },
        "sops": {"root": "procedures"},
    }
    with project_scope(Project.from_settings(root, settings)):
        assert [c["slug"] for c in variant.chapter_order()] == [
            "index",
            "opening",
            "closing",
        ]


def test_marks_never_reach_the_drafter(tmp_path):
    root = _book(tmp_path)
    chapter = root / "manuscript" / "sections" / "opening.qmd"
    chapter.write_text(
        "# The opening {#sec-opening}\n\n"
        "<!-- @todo  say what a signal costs -->\n\n"
        "A kettle boils.\n"
    )
    with project_scope(_project(root)):
        bundle = variant.build_bundle(variant.chapter_order())
        variant.assert_blind(bundle)
    assert "@todo" not in bundle
    assert "A kettle boils." in bundle


def test_a_leaked_comment_stops_the_build(tmp_path):
    with pytest.raises(AssertionError, match="HTML comments"):
        variant.assert_blind("A kettle boils.\n<!-- @bind(kettle-signal) -->\n")


def test_a_project_adds_its_own_blindness_tells(tmp_path):
    """The mark syntax is the tool's; a book's private vocabulary is the book's."""
    variant.assert_blind("The kettle is load-bearing.\n")
    with pytest.raises(AssertionError, match="load-bearing"):
        variant.assert_blind("The kettle is load-bearing.\n", ("load-bearing",))


def test_the_brief_is_read_out_of_the_two_procedures(tmp_path):
    root = _book(tmp_path)
    with project_scope(_project(root)):
        voice, reader = variant.read_brief("postcard")
    assert voice == "Short sentences. No jokes."
    assert reader == "A reader in a hurry."


def test_a_procedure_is_found_in_any_layer_the_project_uses(tmp_path):
    """Shared fragments belong under protocols/, and the catalog files them there.

    Reading only the procedure root meant a project that followed its own
    documented layout got "no SOP" from a command standing next to the file.
    """
    root = _book(tmp_path)
    protocols = root / "procedures" / "protocols"
    protocols.mkdir()
    (root / "procedures" / "variant-common.md").rename(protocols / "variant-common.md")
    workflows = root / "procedures" / "workflows"
    workflows.mkdir()
    (root / "procedures" / "variant-postcard.md").rename(
        workflows / "variant-postcard.md"
    )
    with project_scope(_project(root)):
        assert variant.kinds() == ["postcard"]
        assert variant.procedure("variant-common") == protocols / "variant-common.md"
        voice, reader = variant.read_brief("postcard")
    assert voice == "Short sentences. No jokes."
    assert reader == "A reader in a hurry."


def test_a_kind_without_a_procedure_says_which_file_is_missing(tmp_path):
    root = _book(tmp_path)
    with project_scope(_project(root)):
        with pytest.raises(ValueError, match="variant-telegram.md"):
            variant.read_brief("telegram")


def test_figures_come_from_the_sidecar_without_operator_metadata(tmp_path):
    root = _book(tmp_path)
    with project_scope(_project(root)):
        figures = variant.figures_for(root / "manuscript" / "sections" / "opening.qmd")
    assert figures == [
        {"name": "kettle", "desc": "A kettle with steam leaving the spout"}
    ]


def test_the_prompt_carries_the_book_the_task_the_reader_and_the_voice(tmp_path):
    root = _book(tmp_path)
    with project_scope(_project(root)):
        chapters = variant.chapter_order()
        bundle = variant.build_bundle(chapters)
        voice, reader = variant.read_brief("postcard")
        prompt = variant.assemble_prompt(
            "postcard", bundle, chapters[1], reader, voice, []
        )
    assert "POSTCARD edition" in prompt
    assert "A kettle boils." in prompt
    assert 'Rewrite the chapter titled "The opening"' in prompt
    assert "A reader in a hurry." in prompt
    assert "Short sentences. No jokes." in prompt
    assert "No illustration is assigned" in prompt


# --- the drafters, as a run ------------------------------------------------ #


def _policy(root, text):
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "variant-policy.toml").write_text(text)
    return _project(
        root,
        publish={
            "variants": "variants",
            "variant_policy": "config/variant-policy.toml",
        },
    )


def test_the_drafter_is_the_invocations_then_the_kinds_then_the_defaults(tmp_path):
    root = _book(tmp_path)
    project = _policy(
        root,
        'default_model = "sonnet"\ndefault_effort = "medium"\n'
        '[kinds.postcard]\nharness = "codex"\nmodel = "gpt-5.5"\n',
    )
    with project_scope(project):
        assert variant.drafter("postcard") == {
            "harness": "codex",
            "model": "gpt-5.5",
            "effort": "medium",
        }
        # Another kind falls back to the defaults, and Claude's name implies
        # its harness.
        assert variant.drafter("telegram") == {
            "harness": "claude",
            "model": "sonnet",
            "effort": "medium",
        }
        assert variant.drafter("postcard", model="opus", effort="max") == {
            "harness": "codex",
            "model": "opus",
            "effort": "max",
        }
    with project_scope(_project(root)):
        assert variant.drafter("postcard", model="opus")["effort"] == "high"


def test_each_chapter_is_one_blind_seat_with_no_tools(tmp_path):
    drafts = [
        {"unit": u, "title": u.title(), "source": f"s/{u}.qmd", "prompt": f"P {u}"}
        for u in ("opening", "closing")
    ]
    seat = {"harness": "agy", "model": "gemini-pro", "effort": "high"}
    plan = variant.plan("postcard", drafts, seat)

    assert plan["metadata"] == {"variant": {"kind": "postcard"}}
    first = plan["seats"][0]
    assert first["id"] == "draft-opening"
    assert first["policy"] == {"tools": "none", "filesystem": "audited"}
    assert first["accept_red_flags"] == ["session-bus"]
    assert first["turns"] == [{"id": "draft", "prompt": "P opening", "min_bytes": 20}]
    assert first["metadata"]["variant"]["unit"] == "opening"
    claude = variant.plan("postcard", drafts, {**seat, "harness": "claude"})
    assert claude["seats"][0]["policy"]["filesystem"] == "enforced"
    assert "accept_red_flags" not in claude["seats"][0]


def _drafted(tmp_path, kind, units, answers, *, flags=None):
    """Prepare ``units``' seats as external ones and bank ``answers``.

    External seats take their answers through the engine's own import, so
    the record holds receipts as a run would, without launching a model.
    """
    from quirework.runs import engine
    from quirework.runs.storage import hashes, write

    drafts = [
        {"unit": u, "title": u, "source": f"s/{u}.qmd", "prompt": f"Rewrite {u}."}
        for u in units
    ]
    document = variant.plan(
        kind, drafts, {"harness": "claude", "model": "sonnet", "effort": "medium"}
    )
    for seat in document["seats"]:
        seat["mode"] = "external"
    run_dir = tmp_path / f"run-{len(list(tmp_path.glob('run-*')))}"
    run_dir.mkdir()
    (run_dir / "plan.json").write_text(json.dumps(document))
    record = run_dir / "record"
    engine.prepare(run_dir / "plan.json", record, resolve=False)
    run = engine.Run(record)
    for unit, text in answers.items():
        answer = run_dir / f"{unit}.md"
        answer.write_text(text)
        name = variant.seat_id(unit)
        engine.import_answer(
            run,
            name,
            "draft",
            answer,
            {"session_id": f"s-{unit}", "model": "claude-sonnet-5", "source": "test"},
        )
        if flags and unit in flags:
            state = run.state(name)
            done = state["done"]["draft"]
            done["receipt"]["red_flags"] = [flags[unit]]
            write(run.record(name, "draft") / "receipt.json", done["receipt"])
            if done.get("record_hashes"):
                done["record_hashes"] = hashes(run.record(name, "draft"))
            run.save(name, state)
    return record


def test_finished_drafts_come_in_with_the_model_that_served_them(tmp_path, capsys):
    root = _book(tmp_path)
    with project_scope(_project(root)):
        record = _drafted(
            tmp_path,
            "postcard",
            ["opening", "closing"],
            {"opening": "# The opening\n\nSteam.\n"},
        )
        assert variant.import_drafts("postcard", record) == 1

    raw = root / "variants" / "postcard" / "_raw"
    assert (raw / "opening.md").read_text() == "# The opening\n\nSteam.\n"
    assert not (raw / "closing.md").exists()
    receipts = json.loads((raw / "receipts.json").read_text())["drafts"]
    assert list(receipts) == ["opening"]
    entry = receipts["opening"]
    assert entry["harness"] == "claude" and entry["model"] == "claude-sonnet-5"
    assert entry["requested_model"] == "sonnet" and entry["seat"] == "draft-opening"
    assert entry["sha256"] == hashlib.sha256(b"# The opening\n\nSteam.\n").hexdigest()
    # A receipt that is committed beside the drafts names no private path.
    assert str(tmp_path) not in (raw / "receipts.json").read_text()
    out = capsys.readouterr().out
    assert "WAIT  closing" in out
    assert f"quirework publish variant import postcard {record}" in out


def test_a_redraft_replaces_its_own_receipt_and_keeps_the_others(tmp_path):
    root = _book(tmp_path)
    with project_scope(_project(root)):
        first = _drafted(
            tmp_path,
            "postcard",
            ["opening", "closing"],
            {
                "opening": "# The opening\n\nOne.\n",
                "closing": "# The closing\n\nTwo.\n",
            },
        )
        assert variant.import_drafts("postcard", first) == 0
        again = _drafted(
            tmp_path, "postcard", ["opening"], {"opening": "# The opening\n\nThree.\n"}
        )
        assert variant.import_drafts("postcard", again) == 0

    raw = root / "variants" / "postcard" / "_raw"
    receipts = json.loads((raw / "receipts.json").read_text())["drafts"]
    assert receipts["opening"]["record"] != receipts["closing"]["record"]
    assert (raw / "opening.md").read_text() == "# The opening\n\nThree.\n"
    assert (raw / "closing.md").read_text() == "# The closing\n\nTwo.\n"


def test_a_flagged_draft_is_held_out_of_the_edition(tmp_path, capsys):
    root = _book(tmp_path)
    with project_scope(_project(root)):
        record = _drafted(
            tmp_path,
            "postcard",
            ["opening"],
            {"opening": "# The opening\n\nSteam.\n"},
            flags={"opening": "tool call: Read"},
        )
        assert variant.import_drafts("postcard", record) == 1
    assert not (root / "variants" / "postcard" / "_raw" / "opening.md").exists()
    assert "HELD  opening" in capsys.readouterr().out


def test_a_run_of_another_edition_is_refused(tmp_path):
    root = _book(tmp_path)
    with project_scope(_project(root)):
        record = _drafted(tmp_path, "telegram", ["opening"], {})
        with pytest.raises(ValueError, match="not a run of the postcard"):
            variant.import_drafts("postcard", record)


def test_plan_only_writes_the_plan_outside_the_book(tmp_path, capsys, monkeypatch):
    root = _book(tmp_path)
    (root / "quirework.toml").write_text(_CONFIG)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    from quirework.cli import main

    rc = main(
        ["--project", str(root), "publish", "variant", "build", "postcard"]
        + ["--model", "sonnet", "--plan-only"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    plans = list((tmp_path / "state").rglob("plan.json"))
    assert len(plans) == 1
    document = json.loads(plans[0].read_text())
    assert [s["id"] for s in document["seats"]] == ["draft-opening", "draft-closing"]
    assert "@bind" not in plans[0].read_text()
    assert "quirework sop runs prepare" in out
    assert "quirework publish variant import postcard" in out
    assert not (root / "variants").exists()


# --- the pure helpers the builder is made of ------------------------------- #


def test_strip_comments_drops_all_marks_and_collapses_blanks():
    src = (
        "# Title {#sec-x}\n"
        "<!-- @bind(kettle-signal) -->\n"
        "<!-- @figure(whistle)  the whistle. Built: derived/illustrations/whistle.svg -->\n\n"
        "A kettle has states.\n\n"
        "<!-- @todo rephrase -->\n\n"
        "The whistle is the signal.\n"
    )
    out = variant.strip_comments(src)
    assert "@bind" not in out and "@figure" not in out and "@todo" not in out
    assert "<!--" not in out
    assert "A kettle has states." in out and "The whistle is the signal." in out
    assert "\n\n\n" not in out  # blank runs collapsed


def test_section_body_extracts_block_to_next_heading():
    md = "## Blind-draft brief\n\n- rule one\n- rule two\n\n## Convention\n\nboilerplate\n"
    body = variant.section_body(md, "Blind-draft brief")
    assert body is not None
    assert body.startswith("- rule one")
    assert "rule two" in body
    assert "Convention" not in body and "boilerplate" not in body


def test_section_body_missing_returns_none():
    assert variant.section_body("# just a title\n\nbody\n", "Blind-draft brief") is None


def test_clean_figure_splits_desc_and_drops_metadata():
    body = (
        "the whistle as a gauge, not a switch. "
        "Built: derived/illustrations/the-whistle.svg. Reader panel: all 6."
    )
    desc, built = variant.clean_figure(body)
    assert desc == "the whistle as a gauge, not a switch"
    assert built == "derived/illustrations/the-whistle.svg"
    assert "Reader panel" not in desc


def test_figures_for_without_a_sidecar_is_empty(tmp_path):
    (tmp_path / "ch.qmd").write_text("# Ch\n\nProse.\n")
    assert variant.figures_for(tmp_path / "ch.qmd") == []


# --- the command, on the same book (no model launched) ---------------------- #

_CONFIG = """schema = 2

[manuscript]
source = "manuscript"
quarto = "manuscript/_quarto.yml"
chapters = "manuscript/sections"

[publish]
variants = "variants"

[sops]
root = "procedures"
"""


def _command(tmp_path, *args):
    """Run `quirework publish variant ...` against the synthetic book."""
    from quirework.cli import main

    root = _book(tmp_path)
    (root / "quirework.toml").write_text(_CONFIG)
    return main(["--project", str(root), "publish", "variant", *args])


def test_the_command_lists_the_kinds_the_procedures_define(tmp_path, capsys):
    assert _command(tmp_path) == 0
    out = capsys.readouterr().out
    assert "variant kinds" in out and "postcard" in out


def test_build_requires_a_kind(tmp_path):
    assert _command(tmp_path, "build") == 2


def test_build_names_the_procedure_an_unknown_kind_lacks(tmp_path, capsys):
    assert _command(tmp_path, "build", "nonexistent-kind", "--dry-run") == 2
    captured = capsys.readouterr()
    assert "variant-nonexistent-kind" in captured.out + captured.err


def test_an_unknown_kind_is_refused_by_name(tmp_path, capsys):
    assert _command(tmp_path, "does-not-exist") == 2
    captured = capsys.readouterr()
    assert "variant-does-not-exist" in captured.out + captured.err
