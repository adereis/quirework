"""Publishing through a backend: Quarto for a book, Hugo for a site.

The Hugo tests run a stand-in `hugo` that records its arguments, so they check
what Quirework asks Hugo to do without needing Hugo installed. Every fixture
here is fiction.
"""

import json
import os
import stat

import pytest

from quirework.project import ConfigurationError, Project, project_scope
from quirework.publish import backends, hugo

FAKE_HUGO = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_HUGO_LOG"], "w") as log:
    json.dump(sys.argv[1:], log)
sys.exit(int(os.environ.get("FAKE_HUGO_EXIT", "0")))
"""


def _site(root):
    (root / "content" / "posts" / "a-kettle").mkdir(parents=True)
    (root / "content" / "posts" / "a-kettle" / "index.md").write_text(
        "+++\ntitle = 'A Kettle'\n+++\n\nIt whistles.\n"
    )
    (root / "hugo.toml").write_text("title = 'A Kettle Blog'\n")
    return root


SITE = {
    "manuscript": {
        "format": "pages",
        "source": "content",
        "pages": "posts/*/index.md",
    },
    "publish": {"backend": "hugo"},
}


@pytest.fixture
def fake_hugo(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "hugo"
    script.write_text(FAKE_HUGO)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "hugo-args.json"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_HUGO_LOG", str(log))
    return lambda: json.loads(log.read_text())


@pytest.fixture
def site(tmp_path):
    root = _site(tmp_path / "site")
    project = Project.from_settings(root, SITE)
    with project_scope(project):
        yield project


def test_a_project_without_a_backend_publishes_with_quarto(tmp_path):
    assert backends.selected(Project.from_settings(tmp_path, {})) == "quarto"


def test_an_unknown_backend_is_refused(tmp_path):
    (tmp_path / "content").mkdir()
    settings = {
        "manuscript": {**SITE["manuscript"]},
        "publish": {"backend": "jekyll"},
    }
    with pytest.raises(ConfigurationError, match="'quarto' or 'hugo'"):
        Project.from_settings(tmp_path, settings).require("publish")


def test_a_hugo_site_needs_its_own_configuration(tmp_path):
    root = _site(tmp_path / "site")
    (root / "hugo.toml").unlink()
    with pytest.raises(ConfigurationError, match="no Hugo configuration"):
        Project.from_settings(root, SITE).require("publish")


def test_a_hugo_site_needs_no_quarto_file(site):
    site.require("publish")


def test_a_quarto_command_is_refused_by_name_on_a_site(site):
    with pytest.raises(ConfigurationError, match="works on a quarto build"):
        site.invoke("publish figures", ["list"])


def test_build_writes_into_the_build_tree_not_the_deploy_dir(site, fake_hugo):
    assert site.invoke("publish build") == 0
    args = fake_hugo()
    assert args[:3] == ["build", "--source", str(site.root)]
    assert args[args.index("--destination") + 1] == str(site.root / "build" / "site")
    assert "--buildDrafts" not in args


def test_drafts_and_unknown_flags_reach_hugo(site, fake_hugo):
    assert site.invoke("publish build", ["--drafts", "--environment", "staging"]) == 0
    args = fake_hugo()
    assert "--buildDrafts" in args
    assert args[-2:] == ["--environment", "staging"]


def test_a_failed_hugo_run_fails_the_build(site, fake_hugo, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_HUGO_EXIT", "3")
    assert site.invoke("publish build") == 3
    assert "hugo exited 3" in capsys.readouterr().err


def test_a_missing_hugo_is_said_plainly(site, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    assert site.invoke("publish build") == 127
    assert "hugo not found" in capsys.readouterr().err


def test_the_site_itself_is_never_the_destination(site, fake_hugo):
    with pytest.raises(ConfigurationError, match="refusing to build into the site"):
        hugo.main(["--out", ".."])
