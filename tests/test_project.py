"""Different books share an installation without sharing configuration."""

import json
import tempfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from quirework.project import (
    ConfigurationError,
    Project,
    current_project,
    project_scope,
    submit,
)
from quirework.manuscript import notes, units
from quirework.publish import fonts, graybuild
from quirework.sops import catalog, commands, personas


def book(root, slug):
    source = root / "manuscript/sections"
    source.mkdir(parents=True)
    (source / f"{slug}.qmd").write_text(
        f"# {slug.title()} {{#sec-{slug}}}\n\nA small fictional scene.\n"
    )
    (source.parent / "_quarto.yml").write_text(
        f"book:\n  chapters:\n    - sections/{slug}.qmd\nformat:\n  html: default\n"
    )
    (root / "quirework.toml").write_text(
        'schema = 2\n[manuscript]\nsource = "manuscript"\n'
        'quarto = "manuscript/_quarto.yml"\n'
        'chapters = "manuscript/sections"\n'
    )
    return Project.load(root)


def test_different_layout_and_no_private_corpus(tmp_path):
    project = book(tmp_path / "novel", "arrival")
    with project_scope(project):
        assert [c.slug for c in units.registry()] == ["arrival"]
        assert not (project.root / "private").exists()


def test_local_override_paths_are_relative_to_their_file(tmp_path, monkeypatch):
    project = book(tmp_path / "novel", "arrival")
    (project.root / "quirework.local.toml").write_text('[runs]\nstore = "local-runs"\n')
    monkeypatch.chdir(tmp_path)
    configured = Project.load(project.root)
    assert configured.state_root == project.root / "local-runs"
    assert configured.origins["runs.store"].endswith("quirework.local.toml")


def test_project_selection_is_restored_after_errors(tmp_path):
    a, b = book(tmp_path / "a", "arrival"), book(tmp_path / "b", "departure")
    with project_scope(a):
        with pytest.raises(RuntimeError):
            with project_scope(b):
                assert current_project() is b
                raise RuntimeError("fictitious failure")
        assert current_project() is a


def test_parallel_projects_have_separate_paths_and_policy(tmp_path):
    a, b = book(tmp_path / "a", "arrival"), book(tmp_path / "b", "departure")

    def read():
        return current_project().root, [c.slug for c in units.registry()]

    with ThreadPoolExecutor(max_workers=2) as pool:
        with project_scope(a):
            one = submit(pool, read)
        with project_scope(b):
            two = submit(pool, read)
        assert one.result() == (a.root, ["arrival"])
        assert two.result() == (b.root, ["departure"])
    assert a.cache_root != b.cache_root
    assert a.state_root != b.state_root
    assert a.account_state("agy") == b.account_state("agy")


def test_unconfigured_corpus_is_distinct_from_missing_corpus(tmp_path):
    empty = Project.from_settings(tmp_path, {})
    with pytest.raises(ConfigurationError, match="not configured"):
        empty.require("provenance")
    configured = Project.from_settings(
        tmp_path, {"provenance": {"enabled": True, "root": "missing"}}
    )
    with pytest.raises(ConfigurationError, match="missing"):
        configured.require("provenance")


def test_unknown_configuration_section_is_rejected(tmp_path):
    (tmp_path / "quirework.toml").write_text(
        "schema = 2\n[provnance]\nenabled = true\n"
    )
    with pytest.raises(ConfigurationError, match="unknown sections"):
        Project.load(tmp_path)


def test_a_schema_1_book_section_says_where_its_keys_went(tmp_path):
    """Schema 1 kept everything in [book]; the error names the two sections."""
    (tmp_path / "quirework.toml").write_text(
        'schema = 1\n[book]\nsource = "manuscript"\n'
    )
    with pytest.raises(ConfigurationError, match=r"\[manuscript\].*\[publish\]"):
        Project.load(tmp_path)


def test_the_schema_must_be_the_current_one(tmp_path):
    (tmp_path / "quirework.toml").write_text(
        'schema = 1\n[manuscript]\nsource = "manuscript"\n'
    )
    with pytest.raises(ConfigurationError, match="schema must be 2"):
        Project.load(tmp_path)


def test_custom_persona_directory_and_no_implicit_book_identity(tmp_path):
    directory = tmp_path / "profiles"
    directory.mkdir()
    profile = dict(
        schema_version=1,
        id="reader",
        revision=1,
        label="Reader",
        description="A fictional test perspective",
        tags=["reader"],
        prompt="Explain what is clear.",
    )
    (directory / "reader.json").write_text(json.dumps(profile))
    project = Project.from_settings(tmp_path, {"sops": {"personas": "profiles"}})
    with project_scope(project):
        resolved = personas.resolve("reader")
        assert resolved["profile"] == profile
        assert personas._options().DEFAULT is None


def test_procedure_catalog_uses_configured_directory(tmp_path):
    directory = tmp_path / "procedures"
    directory.mkdir()
    (directory / "read.md").write_text(
        "# Read a note\n\n<!-- @skill: Read a supplied note and report what is clear. -->\n\nRead only the supplied note.\n"
    )
    data = {
        "version": 1,
        "families": [{"id": "read", "label": "Read", "question": "What is clear?"}],
        "entries": {
            "read": {
                "family": "read",
                "kind": "procedure",
                "skill": True,
                "requires": [],
                "mechanics": [],
                "followups": [],
                "note": "",
            }
        },
    }
    (directory / "index.json").write_text(json.dumps(data))
    project = Project.from_settings(
        tmp_path, {"sops": {"root": "procedures", "catalog": "procedures/index.json"}}
    )
    with project_scope(project):
        assert catalog.load()["entries"] == data["entries"]
        assert catalog.reading_order(data["entries"], "read") == ["read"]
        # Resolution, not just discovery: a renamed directory has to survive
        # the read path too, or explaining a procedure cannot find its file.
        explained = catalog.explain(data, "read")
        assert explained["reading_order"][0]["path"] == "procedures/read.md"
        assert explained["description"].startswith("Read a supplied note")


def test_a_skill_is_a_catalog_decision(tmp_path):
    """Adding a procedure file must not quietly add a skill route.

    Letting the file tree decide is how a skill list grows to a size nobody
    chose, with entries that route to "now pick a sub-procedure". The `skill`
    field is required, so the person adding a procedure answers yes or no, and
    `kind: procedure` with `skill: false` keeps a real procedure discoverable
    through the catalog without its own front door.
    """
    directory = tmp_path / "procedures"
    directory.mkdir()
    (directory / "read.md").write_text("# Read\n\n<!-- @skill: Read it. -->\n")
    (directory / "shared.md").write_text("# SOP fragment — shared\n\nRules.\n")

    def write(**overrides):
        entries = {
            "read": {
                "family": "read",
                "kind": "procedure",
                "skill": True,
                "requires": [],
                "mechanics": [],
                "followups": [],
                "note": "",
            },
            "shared": {
                "family": "read",
                "kind": "protocol",
                "skill": False,
                "requires": [],
                "mechanics": [],
                "followups": [],
                "note": "",
            },
        }
        for name, patch in overrides.items():
            entries[name] = {**entries[name], **patch}
            if patch.get("_drop"):
                entries[name] = {
                    k: v
                    for k, v in entries[name].items()
                    if k not in ("skill", "_drop")
                }
        (directory / "index.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "families": [{"id": "read", "label": "Read", "question": "Clear?"}],
                    "entries": entries,
                }
            )
        )

    project = Project.from_settings(
        tmp_path, {"sops": {"root": "procedures", "catalog": "procedures/index.json"}}
    )
    with project_scope(project):
        write()
        assert [s for s, _ in commands.invokable()] == ["read"]

        write(read={"_drop": True})
        with pytest.raises(ValueError, match="expected fields.*skill"):
            catalog.load()

        write(read={"skill": "yes"})
        with pytest.raises(ValueError, match="skill must be true or false"):
            catalog.load()

        write(shared={"skill": True})
        with pytest.raises(ValueError, match="shared reading, not a skill"):
            catalog.load()

        # The state the file tree could not express: real procedure, no route.
        write(read={"skill": False})
        assert commands.invokable() == []
        assert catalog.load()["entries"]["read"]["kind"] == "procedure"


def test_generated_skills_name_the_project_own_guide(tmp_path):
    """A generated skill must not send an agent to a file this book lacks."""
    directory = tmp_path / "procedures"
    directory.mkdir()
    (directory / "tidy.md").write_text("# Tidy\n\n<!-- @skill: Tidy it. -->\n")
    settings = {"sops": {"root": "procedures", "catalog": "procedures/index.json"}}
    with project_scope(Project.from_settings(tmp_path, settings)):
        body = commands._skill_md("tidy", "Tidy it.")
        assert 'per AGENTS.md\n("How work happens")' in body
        # The route goes through the command, not the file, so the read is
        # counted; the file is still named as the source of truth.
        assert "Run `quirework sop show tidy`" in body
        assert "Read `procedures/tidy.md`" not in body
        assert "`procedures/tidy.md` is the single source of truth" in body
    named = dict(settings)
    named["sops"] = {**settings["sops"], "operator_guide": "CONTRIBUTING.md"}
    with project_scope(Project.from_settings(tmp_path, named)):
        body = commands._skill_md("tidy", "Tidy it.")
        assert "per CONTRIBUTING.md: drive" in body
        assert "AGENTS.md" not in body
    silent = dict(settings)
    silent["sops"] = {**settings["sops"], "operator_guide": ""}
    with project_scope(Project.from_settings(tmp_path, silent)):
        body = commands._skill_md("tidy", "Tidy it.")
        assert "as the operator: drive" in body


def test_a_generic_font_family_is_not_a_missing_typeface(tmp_path):
    """`sans-serif` resolves to whatever is installed; it cannot be missing."""
    figures = tmp_path / "plates"
    figures.mkdir()
    (figures / "kettle.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text font-family="sans-serif">'
        "steam</text></svg>"
    )
    settings = {"publish": {"figures": "plates"}}
    with project_scope(Project.from_settings(tmp_path, settings)):
        assert fonts._stacks_in_figures() == []
    (figures / "kettle.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text font-family="Spectral,serif">'
        "steam</text></svg>"
    )
    with project_scope(Project.from_settings(tmp_path, settings)):
        assert fonts._stacks_in_figures() == [("Spectral",)]


def test_a_fallback_chain_is_read_as_a_chain(tmp_path):
    """A stack asks for its first family and says what to do without it.

    Reading only the first name turns a deliberate fallback into a hard
    dependency, and refuses to render on a machine that has every alternative
    the author named.
    """
    figures = tmp_path / "plates"
    figures.mkdir()
    (figures / "kettle.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text '
        'font-family="Nothing Installed Here, Liberation Serif, serif">'
        "steam</text></svg>"
    )
    (figures / "whistle.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text font-family="Second Plate">'
        "steam</text></svg>"
    )
    settings = {"publish": {"figures": "plates"}}
    with project_scope(Project.from_settings(tmp_path, settings)):
        stacks = fonts._stacks_in_figures()
        assert stacks == [
            ("Nothing Installed Here", "Liberation Serif"),
            ("Second Plate",),
        ]
        assert fonts.available(stacks[0], "rsvg")
        assert not fonts.available(stacks[1], "rsvg")


def test_a_format_the_reader_letters_needs_nothing_installed(tmp_path):
    """HTML and EPUB ship the SVG; the browser resolves its fonts, not this machine."""
    figures = tmp_path / "plates"
    figures.mkdir()
    (figures / "kettle.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text font-family="Second Plate">'
        "steam</text></svg>"
    )
    settings = {"publish": {"figures": "plates"}}
    with project_scope(Project.from_settings(tmp_path, settings)):
        assert fonts.missing(["html"]) == []
        assert fonts.missing(["epub", "html"]) == []
        # The print interior is declared as `typst`, not as `pdf`.
        assert fonts.missing(["typst"]) == [(("Second Plate",), "rsvg")]
        assert fonts.missing(["docx"]) == [(("Second Plate",), "rsvg")]
        assert fonts.missing() == [(("Second Plate",), "rsvg")]


def test_the_spec_is_named_by_configuration_not_by_convention(tmp_path):
    """A project's register has its own name; diagnostics must say that one."""
    source = tmp_path / "manuscript"
    source.mkdir()
    (source / "_register.md").write_text(
        "# Held\n\n## A kettle signals {#kettle-signal}\n\nA whistle reports.\n"
    )
    settings = {
        "manuscript": {"source": "manuscript", "spec": "manuscript/_register.md"}
    }
    with project_scope(Project.from_settings(tmp_path, settings)):
        assert notes._spec_name() == "_register.md"
        assert notes.spec_sections()[0][0] == "kettle-signal"


def test_a_transient_artifact_does_not_land_in_the_cache(tmp_path, monkeypatch):
    """Nothing collects ~/.cache, so a dump of private text must not go there."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    project = Project.from_settings(tmp_path, {})
    first = project.scratch_dir("human-input")
    assert first.parent == runtime
    assert first.stat().st_mode & 0o777 == 0o700
    # One directory per process, so two commands in a run share it.
    assert project.scratch_dir("human-input") == first
    assert project.scratch_dir("other") != first
    # With no runtime directory the fallback still creates a private one, at
    # the temporary directory `tempfile` resolves (it caches that per process,
    # hence the reset; a CLI run resolves it once and is unaffected).
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    monkeypatch.setenv("TMPDIR", str(tmp_path / "elsewhere"))
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", None)
    assert Project.from_settings(tmp_path, {}).scratch_dir("human-input").parent == (
        tmp_path / "elsewhere"
    )


def test_generic_grayscale_has_no_book_palette(tmp_path):
    project = Project.from_settings(tmp_path, {})
    with project_scope(project):
        output = graybuild.to_gray_svg(
            '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="#ff0000"/></svg>'
        )
        assert "#4c4c4c" in output
        assert graybuild.non_gray_hexes(output) == set()


def test_external_corpus_uses_declared_format_and_curation(tmp_path):
    from quirework.provenance.sources import (
        accepted_files,
        kind_of_path,
        source_reference,
    )

    directory = tmp_path / "external" / "coding"
    directory.mkdir(parents=True)
    source = directory / "sample.jsonl"
    source.write_text("{}\n")
    project = Project.from_settings(
        tmp_path / "book",
        {
            "provenance": {
                "root": str(directory.parent),
                "codex_sessions": str(directory),
                "curation": "presence",
            }
        },
    )
    assert accepted_files(directory, ".jsonl", project) == [source]
    assert kind_of_path(source, project) == "codex"
    assert source_reference(source, project) == source


def test_redaction_policy_changes_have_different_cache_identities(tmp_path):
    from quirework.provenance.ccfilter import policy_version, redact

    first = Project.from_settings(
        tmp_path / "a", {"provenance": {"redaction_markers": ["Example Labs"]}}
    )
    second = Project.from_settings(
        tmp_path / "b", {"provenance": {"redaction_markers": ["Fictional Institute"]}}
    )
    with project_scope(first):
        one = policy_version("example-v1")
        assert "Example Labs" not in redact("Work at Example Labs")
    with project_scope(second):
        assert one != policy_version("example-v1")
        assert redact("Work at Example Labs") == "Work at Example Labs"


def test_review_archives_do_not_leak_between_projects(tmp_path):
    from quirework.provenance.ccfilter import redpen_index

    projects = []
    for name in ("a", "b"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "round.md").write_text(f"source-docx: {name}.docx\n")
        projects.append(
            Project.from_settings(
                tmp_path / ("project-" + name),
                {"provenance": {"redpen": str(directory)}},
            )
        )
    with project_scope(projects[0]):
        assert set(redpen_index()) == {"a.docx"}
    with project_scope(projects[1]):
        assert set(redpen_index()) == {"b.docx"}


def test_no_tools_codex_session_uses_documented_prompt_control(tmp_path):
    from quirework.providers.harness import command
    from quirework.runs.engine import policy

    config = {"harness": "codex", "model": "fixture-model", "effort": "low"}
    args, _, _ = command(
        config,
        policy({"system_prompt": "A small test prompt."}),
        "start",
        "fixture",
        "Reply briefly.",
        [],
        [],
    )
    assert any(value.startswith("model_instructions_file=") for value in args)
    assert "features.shell_tool=false" in args
    assert "features.apps=false" in args
    assert not any(value.startswith("base_instructions=") for value in args)
    # Codex 0.156 ignores the old key, which left the image viewer on.
    assert "features.view_image=false" in args
    assert not any(value.startswith("tools.view_image") for value in args)
    for feature in ("image_generation", "browser_use", "computer_use"):
        assert f"features.{feature}=false" in args


def test_bundled_templates_are_commented_and_parseable():
    from importlib.resources import files
    import tomllib

    for name in ("book", "site", "run", "terminology"):
        text = files("quirework").joinpath("templates", name + ".toml").read_text()
        assert text.startswith("# ")
        assert sum(line.startswith("#") for line in text.splitlines()) >= 5
        assert tomllib.loads(text)


def test_the_cache_cleanup_rule_is_a_tmpfiles_line_systemd_accepts():
    """A drop-in systemd rejects is worse than none, so check its shape here."""
    from importlib.resources import files

    text = files("quirework").joinpath("templates", "tmpfiles.conf").read_text()
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    assert len(lines) == 1
    kind, path, mode, user, group, age = lines[0].split()
    # `e` never creates the directory, so the rule is inert until something is
    # cached; `d` would make one on every timer run.
    assert kind == "e"
    # %C is the XDG cache directory under `systemd-tmpfiles --user`.
    assert path == "%C/quirework"
    assert (mode, user, group) == ("-", "-", "-")
    assert age.endswith("d") and int(age[:-1]) > 0
    # Run records are evidence and live under the state directory, whose
    # specifier is %S. No rule in this file may ever reach them.
    assert "%S" not in text


def test_template_command_does_not_overwrite_configuration(tmp_path, capsys):
    from quirework.cli import main

    target = tmp_path / "quirework.toml"
    assert main(["config", "template", "site", "--out", str(target)]) == 0
    original = target.read_bytes()
    assert main(["config", "template", "book", "--out", str(target)]) == 2
    assert target.read_bytes() == original


def test_commented_run_plan_freezes_without_comments(tmp_path):
    from importlib.resources import files
    from quirework.runs.engine import prepare, Run

    plan = tmp_path / "plan.toml"
    plan.write_text(files("quirework").joinpath("templates", "run.toml").read_text())
    record = tmp_path / "record"
    prepare(plan, record, resolve=False)
    run = Run(record)
    assert run.verify()
    turn = run.seats["reader"]["turns"][0]
    assert (
        turn["prompt"]
        == "A fictional note says the lantern is green. Reply only with the color."
    )
    assert "Preparation freezes" not in turn["prompt"]


def test_policy_loader_reads_inline_commented_toml(tmp_path):
    path = tmp_path / "terms.toml"
    path.write_text(
        '# Deliberate advisory rules.\n[terms]\n"utilize" = "use" # A plain alternative.\n'
    )
    project = Project.from_settings(tmp_path, {"text": {"terminology": "terms.toml"}})
    assert project.data("text.terminology") == {"terms": {"utilize": "use"}}


def test_policy_snapshot_is_local_and_callers_cannot_mutate_it(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text('value = "first"\n')
    project = Project.from_settings(tmp_path, {"text": {"terminology": "policy.toml"}})
    value = project.data("text.terminology")
    value["value"] = "caller change"
    path.write_text('value = "second"\n')
    assert project.data("text.terminology") == {"value": "first"}
    next_operation = Project.from_settings(
        tmp_path, {"text": {"terminology": "policy.toml"}}
    )
    assert next_operation.data("text.terminology") == {"value": "second"}


def test_book_can_check_metadata_without_migration_tables(tmp_path):
    directory = tmp_path / "manuscript/sections/_marks"
    directory.mkdir(parents=True)
    (directory / "arrival.md").write_text("# Arrival\n\nA neutral note.\n")
    project = Project.from_settings(
        tmp_path,
        {
            "manuscript": {
                "source": "manuscript",
                "chapters": "manuscript/sections",
            },
        },
    )
    with project_scope(project):
        assert units.scan_layers(known={"arrival"}) == []


@pytest.mark.parametrize("kind", ["book", "site"])
def test_a_project_template_is_a_configuration_quirework_reads(tmp_path, kind):
    """Each project template loads as written, with every directory it names
    made, so a new book or site starts from a file that works."""
    from importlib.resources import files

    root = tmp_path / kind
    root.mkdir()
    text = files("quirework").joinpath("templates", f"{kind}.toml").read_text()
    (root / "quirework.toml").write_text(text)
    project = Project.load(root)
    source = project.path("manuscript.source")
    source.mkdir(parents=True)
    if kind == "book":
        project.path("manuscript.chapters").mkdir(parents=True)
        project.path("manuscript.quarto").write_text(
            "book:\n  chapters:\n    - index.qmd\n"
        )
        project.path("publish.figures").mkdir(parents=True)
    else:
        (root / "hugo.toml").write_text("title = 'Example'\n")
        (root / "specs").mkdir()
        project.path("manuscript.spec").write_text("# Site\n")
    project.require("manuscript")
    project.require("publish")
