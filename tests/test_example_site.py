"""The bundled example site, run through the gates a real site runs.

`examples/demo-site` is a small Hugo blog written the way a site is written:
posts in page bundles, a spec per post beside a shared one, marks outside the
tree Hugo renders, a register of sources, and a voice policy. The book example proves the pieces
compose for a book; this one proves they compose for a site.

Hugo itself is replaced by a stand-in that records its arguments, so the suite
does not need it installed and nothing is written into the example.
"""

import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from quirework.project import Project, project_scope

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "demo-site"


@pytest.fixture
def site():
    if not (EXAMPLE / "quirework.toml").is_file():
        pytest.skip("the example site is not in this checkout")
    project = Project.load(EXAMPLE)
    with project_scope(project):
        yield project


def run(project, command, *argv):
    """A command as the author would type it, returning its exit status."""
    return project.invoke(command, list(argv))


def test_the_posts_are_listed_by_date_with_the_draft_marked(site, capsys):
    assert run(site, "spec units") == 0
    out = capsys.readouterr().out.splitlines()
    assert "published" in out[0] and "why-the-lamp-turns" in out[0]
    assert "draft" in out[1] and "what-the-fog-signal-is-for" in out[1]
    # A post specified before it is drafted is listed as planned, and a brief
    # no post has taken up as an idea.
    assert "planned (a spec, no post yet):" in out[2]
    assert "a-keepers-first-winter" in out[3]
    assert "ideas (no post has taken them up):" in out[4]
    assert "ideas/the-tide-clock/brief.md" in out[5]


def test_a_published_post_s_open_to_do_is_reported(site, capsys):
    assert run(site, "spec units", "--check") == 0
    err = capsys.readouterr().err
    assert "1 open @todo on published posts" in err
    assert "clockwork" in err


def test_every_citation_names_a_source_and_every_quotation_holds(site, capsys):
    assert run(site, "spec sources", "--check") == 0
    err = capsys.readouterr().err
    assert "not in the captured copy" not in err
    assert "research/sources.md: station-log" in err  # cited by no post
    assert run(site, "spec sources", "--slug", "coast-pilot") == 0
    assert "cited by: why-the-lamp-turns" in capsys.readouterr().out


def test_the_marks_mirror_their_posts_and_bind_the_register(site, capsys):
    assert run(site, "spec notes", "--check") == 0
    err = capsys.readouterr().err
    assert "specs/a-keepers-first-winter/spec.md" in err  # a planned post


def test_quoted_material_still_appears_in_the_posts(site):
    assert run(site, "spec quotes") == 0


def test_a_post_s_primer_is_its_own_spec_and_what_it_binds(site, capsys):
    assert run(site, "spec notes", "--primer", "-c", "why-the-lamp-turns") == 0
    out = capsys.readouterr().out
    assert "#why-the-lamp-turns/thesis" in out
    assert "#recognizable-signal" in out
    assert "#what-the-fog-signal-is-for/thesis" not in out
    assert "#no-real-rescues" not in out


def test_a_shared_claim_lists_every_post_that_leans_on_it(site, capsys):
    assert run(site, "spec notes", "bind", "--slug", "recognizable-signal") == 0
    out = capsys.readouterr().out
    assert "why-the-lamp-turns" in out
    assert "what-the-fog-signal-is-for" in out


def test_the_prose_is_one_sentence_per_line_and_the_docs_are_wrapped(site):
    files = sorted(
        str(p)
        for pattern in (
            "content/posts/*/index.md",
            "specs/**/*.md",
            "research/*.md",
            "ideas/*/*.md",
            "*.md",
        )
        for p in EXAMPLE.glob(pattern)
    )
    assert files
    assert run(site, "text wrap", "--check", *files) == 0


def test_the_voice_policy_holds(site):
    assert run(site, "text check") == 0


def test_the_procedure_catalog_and_skills_are_current(site):
    assert run(site, "sop catalog", "--check") == 0
    assert run(site, "sop skills", "--check") == 0


def test_an_em_dash_back_in_a_post_fails_the_check(tmp_path):
    copy = tmp_path / "demo-site"
    shutil.copytree(EXAMPLE, copy, ignore=shutil.ignore_patterns("build"))
    post = copy / "content/posts/why-the-lamp-turns/index.md"
    post.write_text(post.read_text().replace("Nobody pushes it now.", "Nobody—now."))
    project = Project.load(copy)
    with project_scope(project):
        assert run(project, "text check") == 1


def test_the_site_builds_through_hugo_into_the_build_tree(site, tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "hugo"
    fake.write_text(
        "#!/usr/bin/env python3\nimport json, os, sys\n"
        "json.dump(sys.argv[1:], open(os.environ['FAKE_HUGO_LOG'], 'w'))\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "args.json"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_HUGO_LOG", str(log))
    assert run(site, "publish build", "--drafts") == 0
    args = json.loads(log.read_text())
    assert args[args.index("--source") + 1] == str(EXAMPLE)
    assert args[args.index("--destination") + 1] == str(EXAMPLE / "build" / "site")
    assert "--buildDrafts" in args
