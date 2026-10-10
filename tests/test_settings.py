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
