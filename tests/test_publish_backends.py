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
    assert backends.selected(Project.from_settings(tmp_path, {})).name == "quarto"


def test_an_unknown_backend_is_refused(tmp_path):
    (tmp_path / "content").mkdir()
    settings = {
        "manuscript": {**SITE["manuscript"]},
        "publish": {"backend": "jekyll"},
    }
    with pytest.raises(ConfigurationError, match="one of quarto, hugo"):
        Project.from_settings(tmp_path, settings).require("publish")


PRESS = """
from quirework.project import current_project
from quirework.publish.backends import Backend


class Press(Backend):
    \"\"\"A publisher that prints a small book: a page and two editions.\"\"\"

    name = "press"

    def build(self, argv, target):
        out = self.output(current_project(), target)
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text("<p>The kettle.</p>")
        (out / "kettle.pdf").write_text("%PDF")
        (out / "kettle.epub").write_text("EPUB")
        print("pressed", target, *(argv or []))
        return 0


def press():
    return Press()
"""


def test_a_backend_can_be_an_installed_extension(tmp_path, monkeypatch, capsys):
    """A publisher Quirework does not ship builds through `publish build`, and
    the commands built on Quarto staging are refused by name, as on Hugo."""
    (tmp_path / "ext").mkdir()
    (tmp_path / "ext" / "kettle_press.py").write_text(PRESS)
    monkeypatch.syspath_prepend(str(tmp_path / "ext"))
    root = _site(tmp_path / "site")
    settings = {**SITE, "publish": {"backend": "kettle_press:press"}}
    project = Project.from_settings(root, settings)
    assert project.invoke("publish build", ["--proof"]) == 0
    assert capsys.readouterr().out == "pressed press --proof\n"
    with pytest.raises(
        ConfigurationError, match="no target of this project is one: press"
    ):
        project.invoke("publish figures", ["--check"])


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


# --------------------------------------------------------------------------- #
# a project that publishes more than one thing
# --------------------------------------------------------------------------- #
TWO = {
    **SITE,
    "publish": {
        "targets": {
            "book": {"backend": "kettle_press:press"},
            "site": {
                "backend": "hugo",
                "site": "website",
                "needs": ["book"],
                "copy": [
                    ["book:.", "book/"],
                    ["book:*.pdf", "downloads/the-kettle.pdf"],
                    ["book:*.epub", "downloads/"],
                ],
            },
        }
    },
}


@pytest.fixture
def two(tmp_path, monkeypatch, fake_hugo):
    (tmp_path / "ext").mkdir()
    (tmp_path / "ext" / "kettle_press.py").write_text(PRESS)
    monkeypatch.syspath_prepend(str(tmp_path / "ext"))
    root = _site(tmp_path / "project")
    (root / "website").mkdir()
    (root / "website" / "hugo.toml").write_text("title = 'The Kettle'\n")
    return Project.from_settings(root, TWO)


def test_a_target_is_built_after_what_it_needs_and_takes_its_output(two, fake_hugo):
    assert two.invoke("publish build") == 0
    args = fake_hugo()
    # The site's own directory, named in its table, relative to the project.
    assert args[args.index("--source") + 1] == str(two.root / "website")
    site = two.root / "build" / "site"
    assert args[args.index("--destination") + 1] == str(site)
    assert (site / "book" / "index.html").read_text() == "<p>The kettle.</p>"
    assert (site / "downloads" / "the-kettle.pdf").read_text() == "%PDF"
    assert (site / "downloads" / "kettle.epub").read_text() == "EPUB"
    # The site's source is never written into.
    assert not (two.root / "website" / "book").exists()


def test_arguments_go_to_the_target_named(two, fake_hugo, capsys):
    assert two.invoke("publish build", ["site", "--drafts"]) == 0
    assert "--buildDrafts" in fake_hugo()
    # The book it needs was built first, without the site's arguments.
    assert "pressed book\n" in capsys.readouterr().out


def test_with_several_targets_arguments_need_a_name(two):
    with pytest.raises(ConfigurationError, match="name the target"):
        two.invoke("publish build", ["--drafts"])


def test_a_target_alone_uses_its_needs_last_build(two):
    with pytest.raises(ConfigurationError, match="has no output"):
        two.invoke("publish build", ["site", "--no-needs"])
    assert two.invoke("publish build", ["book"]) == 0
    assert two.invoke("publish build", ["site", "--no-needs"]) == 0


def test_a_file_destination_takes_exactly_one_file(two):
    table = TWO["publish"]["targets"]
    rows = [["book:*", "downloads/the-kettle.pdf"]]
    settings = {
        **TWO,
        "publish": {"targets": {**table, "site": {**table["site"], "copy": rows}}},
    }
    project = Project.from_settings(two.root, settings)
    with pytest.raises(ConfigurationError, match="matched 3"):
        project.invoke("publish build")


@pytest.mark.parametrize(
    "change, message",
    [
        ({"site": {"needs": ["cover"]}}, "which is no target"),
        ({"book": {"needs": ["site"]}}, "need each other: book -> site -> book"),
        ({"site": {"needs": []}}, "does not list in needs"),
        ({"site": {"copy": [["book:x", "../out"]]}}, "leaves the target's output"),
        ({"site": {"copy": [["book", "out/"]]}}, "TARGET:GLOB"),
        ({"cover": {"site": "."}}, "needs a backend"),
    ],
)
def test_targets_are_refused_when_they_cannot_all_be_built(two, change, message):
    table = {k: dict(v) for k, v in TWO["publish"]["targets"].items()}
    for name, keys in change.items():
        table.setdefault(name, {}).update(keys)
    settings = {**TWO, "publish": {"targets": table}}
    with pytest.raises(ConfigurationError, match=message):
        Project.from_settings(two.root, settings).require("publish")


def test_one_backend_is_named_once(two):
    settings = {**TWO, "publish": {**TWO["publish"], "backend": "hugo"}}
    with pytest.raises(ConfigurationError, match="name the backend in each target"):
        Project.from_settings(two.root, settings).require("publish")


def test_a_project_has_one_quarto_target(tmp_path):
    (tmp_path / "content").mkdir()
    settings = {
        **SITE,
        "publish": {
            "targets": {"print": {"backend": "quarto"}, "web": {"backend": "quarto"}}
        },
    }
    with pytest.raises(ConfigurationError, match="one Quarto target"):
        Project.from_settings(tmp_path, settings).require("publish")


def test_a_quarto_command_runs_as_the_quarto_target_sees_the_project(tmp_path):
    """A Quarto-only command reads `publish.*` as the Quarto target declares
    it, laid over the shared keys."""
    from quirework.publish import targets

    settings = {
        "publish": {
            "build": "out",
            "targets": {
                "book": {"backend": "quarto", "figures": "art"},
                "site": {"backend": "hugo", "needs": ["book"]},
            },
        }
    }
    project = Project.from_settings(tmp_path, settings)
    scoped = targets.for_backend("quarto", "publish figures", project)
    assert scoped.path("publish.figures") == tmp_path / "art"
    assert scoped.path("publish.build") == tmp_path / "out"
    assert scoped.get("publish.targets") is None
    hugo_only = Project.from_settings(tmp_path, SITE)
    with pytest.raises(ConfigurationError, match=r"is one: site \(hugo\)"):
        targets.for_backend("quarto", "publish figures", hugo_only)
