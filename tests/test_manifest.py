"""A spec kept as a directory of section files, read through a manifest.

The spec here is fiction: a lighthouse site's claims, in two parts.
"""

import subprocess

import pytest

from quirework.manuscript import footprint, manifest, notes, specdiff
from quirework.project import ConfigurationError, Project, project_scope

OPENING = "<!-- the claims -->\n\n# Claims\n\nWhat the light holds.\n"
FLASH = "## The lamp flashes {#flash}\n\nOne white flash every ten seconds.\n"
HORN = "## The horn sounds {#horn}\n\nTwo blasts in fog, which `#flash` does not.\n"
KEEPER = "## Someone keeps it {#keeper}\n\nA keeper winds it nightly.\n"

WHOLE = (
    OPENING
    + "\n# What the light is for\n\n"
    + FLASH
    + "\n"
    + HORN
    + "\n# Who keeps it\n\n"
    + KEEPER
)

MANIFEST = """opening = "opening.md"

[[parts]]
title = "What the light is for"
sections = ["flash", "horn"]

[[parts]]
title = "Who keeps it"
sections = ["keeper"]
"""

SITE = {
    "format": "pages",
    "source": "content",
    "pages": "posts/*/index.md",
    "marks": "specs/{slug}/marks.md",
    "unit_name": "post",
    "spec": "spec/manifest.toml",
}


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _split(root):
    for name, text in (
        ("manifest.toml", MANIFEST),
        ("opening.md", OPENING),
        ("flash.md", FLASH),
        ("horn.md", HORN),
        ("keeper.md", KEEPER),
    ):
        _write(root / "spec" / name, text)


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    _split(root)
    _write(
        root / "content" / "posts" / "lamp" / "index.md",
        "+++\ntitle = 'lamp'\ndate = 2031-04-02\n+++\n\n## The light\n\nIt turns.\n",
    )
    _write(
        root / "specs" / "lamp" / "marks.md",
        "## The light\n\n<!-- @bind(flash) -->\n",
    )
    with project_scope(Project.from_settings(root, {"manuscript": SITE})):
        yield root


def test_the_files_joined_in_order_are_the_spec_as_one_file(site):
    found = manifest.read(site / "spec" / "manifest.toml")
    assert manifest.join(found) == WHOLE


def test_the_sections_are_read_in_the_manifest_s_order(site, capsys):
    assert [s.slug for s in notes.spec_sections()] == ["flash", "horn", "keeper"]
    assert notes.check() == 0, capsys.readouterr().err


def test_the_primer_groups_the_sections_by_part(site, capsys):
    notes._print_spec_toc()
    out = capsys.readouterr().out
    assert "spec — spec/ · What the light is for (shared; 2 section(s)" in out
    assert "spec — spec/ · Who keeps it (shared; 1 section(s)" in out


def test_every_section_file_is_part_of_the_work(site):
    fp = footprint.footprint(Project.from_settings(site, {"manuscript": SITE}))
    assert fp.covers("spec/manifest.toml") and fp.covers("spec/horn.md")


@pytest.mark.parametrize(
    "change, expected",
    [
        (("horn.md", None), "lists 'horn', and horn.md does not exist"),
        (("horn.md", KEEPER), "holds #keeper; a section file holds one section"),
        (("fog.md", "## Fog {#fog}\n\nx\n"), "that it does not list"),
    ],
)
def test_a_manifest_and_its_files_must_agree(site, capsys, change, expected):
    name, text = change
    path = site / "spec" / name
    if text is None:
        path.unlink()
    else:
        path.write_text(text)
    assert notes.check() == 1
    assert expected in capsys.readouterr().err


@pytest.mark.parametrize(
    "text, message",
    [
        ("sections = ['a']\n[[parts]]\ntitle = 'x'\nsections = []\n", "not both"),
        ("[[parts]]\nsections = ['a']\n", "part 1 has no title"),
        ("sections = ['a', 'a']\n", "listed twice"),
        ("sections = ['a']\npages = 2\n", "unknown manifest keys: pages"),
    ],
)
def test_a_manifest_that_cannot_hold_is_refused(tmp_path, text, message):
    with pytest.raises(ConfigurationError, match=message):
        manifest.parse(tmp_path / "manifest.toml", text)


def test_a_spec_without_parts_joins_without_headings(tmp_path):
    _write(tmp_path / "flash.md", FLASH)
    _write(tmp_path / "horn.md", HORN)
    found = manifest.parse(tmp_path / "m.toml", "sections = ['flash', 'horn']\n")
    assert manifest.join(found) == FLASH + "\n" + HORN


def git(root, *args):
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=A. Keeper",
            "-c",
            "user.email=keeper@example.invalid",
            *args,
        ],
        check=True,
        capture_output=True,
    )


def test_a_split_into_section_files_changes_no_section(tmp_path):
    root = tmp_path / "site"
    single = {**SITE, "spec": "spec/claims.md"}
    toml = "schema = 2\n\n[manuscript]\n" + "".join(
        f'{k} = "{v}"\n' for k, v in single.items()
    )
    _write(root / "content" / "posts" / "lamp" / "index.md", "+++\n+++\n\nIt.\n")
    _write(root / "spec" / "claims.md", WHOLE)
    _write(root / "quirework.toml", toml)
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Write the claims")
    git(root, "tag", "whole")
    (root / "spec" / "claims.md").unlink()
    _split(root)
    _write(
        root / "quirework.toml", toml.replace("spec/claims.md", "spec/manifest.toml")
    )
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Give each claim a file")
    with project_scope(Project.from_settings(root, {"manuscript": SITE})):
        assert specdiff.diff("whole..HEAD") == []


def test_a_rename_moves_the_section_s_file_and_its_manifest_entry(site, capsys):
    from quirework.manuscript import rename

    assert rename.main(["horn", "fog-signal"]) == 0
    assert not (site / "spec" / "horn.md").exists()
    assert "{#fog-signal}" in (site / "spec" / "fog-signal.md").read_text()
    assert '"fog-signal"' in (site / "spec" / "manifest.toml").read_text()
    assert notes.check() == 0, capsys.readouterr().err
