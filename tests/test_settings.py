"""What a project can learn of the settings Quirework reads."""

import json

from quirework import settings
from quirework.cli import main
from quirework.project import Project

CONFIG = """schema = 2

[manuscript]
source = "manuscript"
sorce = "manuscript"

[publish]
variant_policy = "config/variant-policy.toml"
"""

POLICY = """default_model = "sonnet"

[kinds.postcard]
shape = "document"
shpae = "chapters"
"""


def test_a_project_learns_every_key_it_leaves_undeclared(tmp_path, capsys):
    """A key a project leaves out takes its default and says nothing, so a
    new one inside a policy file reached a consumer only through a commit
    body. Each known key is named, declared or not, inside the policy files
    too, and a declared key nothing reads is named as unknown."""
    (tmp_path / "quirework.toml").write_text(CONFIG)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "variant-policy.toml").write_text(POLICY)
    assert main(["--project", str(tmp_path), "config", "keys", "--json"]) == 0
    found = json.loads(capsys.readouterr().out)

    assert "manuscript.source" in found["settings"]["declared"]
    assert "manuscript.spec_trail" in found["settings"]["undeclared"]
    assert found["settings"]["unknown"] == ["manuscript.sorce"]

    variant = found["policies"]["publish.variant_policy"]
    assert variant["file"].endswith("config/variant-policy.toml")
    assert {"default_model", "kinds.*.shape"} <= set(variant["declared"])
    assert {"default_harness", "kinds.*.omit"} <= set(variant["undeclared"])
    assert variant["unknown"] == ["kinds.postcard.shpae"]

    tells = found["policies"]["text.tells_policy"]
    assert tells["file"] is None and "phrases.*" in tells["undeclared"]

    assert main(["--project", str(tmp_path), "config", "keys", "--undeclared"]) == 0
    shown = capsys.readouterr().out
    assert "undeclared  kinds.*.omit" in shown
    assert "unknown     manuscript.sorce" in shown
    assert "declared    manuscript.source" not in shown


def test_config_check_refuses_a_key_nothing_reads(tmp_path, capsys):
    """A misspelled key is read as absent and takes its default silently, so
    the command that says whether a configuration is right names it and
    fails, in quirework.toml, in the local overlay and in a policy file."""
    (tmp_path / "quirework.toml").write_text(
        'schema = 2\n\n[project]\nname = "notebook"\nnmae = "notebook"\n\n'
        '[text]\nterminology = "terms.toml"\n'
    )
    (tmp_path / "quirework.local.toml").write_text("[sops]\nskil_prefix = 'nb-'\n")
    (tmp_path / "terms.toml").write_text('retierd = ["kettle-shaped"]\n')
    assert main(["--project", str(tmp_path), "config", "check"]) == 1
    captured = capsys.readouterr()
    assert "Configuration is valid." not in captured.out
    assert "quirework.toml: unknown setting project.nmae" in captured.err
    assert "quirework.local.toml: unknown setting sops.skil_prefix" in captured.err
    assert "terms.toml: unknown key retierd in text.terminology" in captured.err

    (tmp_path / "quirework.toml").write_text(
        'schema = 2\n\n[project]\nname = "notebook"\n\n'
        '[text]\nterminology = "terms.toml"\n'
    )
    (tmp_path / "quirework.local.toml").write_text("[sops]\nskill_prefix = 'nb-'\n")
    (tmp_path / "terms.toml").write_text('retired = ["kettle-shaped"]\n')
    assert main(["--project", str(tmp_path), "config", "check"]) == 0
    assert "Configuration is valid." in capsys.readouterr().out


OWN = """schema = 2

[project]
name = "notebook"
cover_palette = "teal"

[text]
terminology = "terms.toml"

[extensions]
settings = ["project.cover_palette"]
stray = 1

[extensions.policy_keys]
"text.terminology" = ["house_terms.*"]
"""


def test_a_key_the_projects_own_code_reads_is_its_own(tmp_path, capsys):
    """An extension may read a setting of its own, or a key of its own in a
    policy file it shares with Quirework. Listed under [extensions], each is
    the project's own and passes the check; a misspelling beside it, or a
    key under [extensions] other than its two lists, still fails."""
    (tmp_path / "quirework.toml").write_text(OWN)
    (tmp_path / "terms.toml").write_text(
        'retired = ["kettle-shaped"]\nretierd = []\n\n[house_terms]\nlid = "the lid"\n'
    )
    assert main(["--project", str(tmp_path), "config", "check"]) == 1
    err = capsys.readouterr().err
    assert "terms.toml: unknown key retierd in text.terminology" in err
    assert "quirework.toml: unknown setting extensions.stray" in err
    assert "cover_palette" not in err and "house_terms" not in err

    assert main(["--project", str(tmp_path), "config", "keys", "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["settings"]["own"] == ["project.cover_palette"]
    assert found["settings"]["unknown"] == ["extensions.stray"]
    terms = found["policies"]["text.terminology"]
    assert terms["own"] == ["house_terms.*"] and terms["unknown"] == ["retierd"]

    (tmp_path / "quirework.toml").write_text(OWN.replace("stray = 1\n", ""))
    (tmp_path / "terms.toml").write_text(
        'retired = ["kettle-shaped"]\n\n[house_terms]\nlid = "the lid"\n'
    )
    assert main(["--project", str(tmp_path), "config", "check"]) == 0
    assert "Configuration is valid." in capsys.readouterr().out


def test_a_list_for_a_file_quirework_never_reads_is_refused(tmp_path, capsys):
    (tmp_path / "quirework.toml").write_text(
        'schema = 2\n\n[extensions.policy_keys]\n"publish.palette_policy" = ["x"]\n'
    )
    assert main(["--project", str(tmp_path), "config", "check"]) == 2
    assert "not a policy file Quirework reads" in capsys.readouterr().err


def test_a_key_the_code_reads_must_be_listed(tmp_path, monkeypatch):
    """Quirework's suite records every key the code reads and fails on one
    the list lacks, so a new setting cannot ship without `config keys`
    knowing it."""
    monkeypatch.setattr(settings, "_seen", set())  # not the session's record
    (tmp_path / "policy.toml").write_text('default_model = "sonnet"\n')
    project = Project.from_settings(
        tmp_path, {"publish": {"variant_policy": "policy.toml"}}
    )
    project.get("manuscript.source")
    project.get("manuscript.brand_new")
    policy = project.data("publish.variant_policy")
    policy.get("default_model")
    policy.get("kinds", {}).get("postcard", {}).get("brand_new")
    assert settings.unregistered() == [
        "manuscript.brand_new",
        "publish.variant_policy: kinds.postcard.brand_new",
    ]
