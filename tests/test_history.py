"""A spec read as it stood at an earlier revision, through that revision's own
configuration, then the map of moved paths, then an error.

The project here is fiction: a lighthouse site whose two claims start in one
notes file, move into spec/, and split into a file each.
"""

import subprocess

import pytest

from quirework.manuscript import history, specdiff, trail
from quirework.project import Project, project_scope

SITE = {
    "format": "pages",
    "source": "content",
    "pages": "posts/*/index.md",
    "marks": "specs/{slug}/marks.md",
    "unit_name": "post",
}

LAMP = "## The lamp flashes {#flash}\n\nIt shows one white flash every ten seconds.\n"
HORN = "## The horn sounds {#horn}\n\nIt sounds two blasts in fog.\n"


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


def _toml(spec, extra=""):
    specs = spec if isinstance(spec, str) else None
    value = f'"{specs}"' if specs else "[" + ", ".join(f'"{s}"' for s in spec) + "]"
    lines = [f'{k} = "{v}"' for k, v in SITE.items()]
    return (
        "schema = 2\n\n[manuscript]\n" + "\n".join(lines) + f"\nspec = {value}\n{extra}"
    )


def _commit(root, message, files, toml=None, gone=()):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    for name in gone:
        (root / name).unlink()
    if toml is not None:
        (root / "quirework.toml").write_text(toml)
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)
    out = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


@pytest.fixture
def site(tmp_path):
    """Five commits: written, sharpened, moved, split, sharpened again."""
    root = tmp_path / "site"
    root.mkdir()
    git(root, "init", "-q")
    post = "+++\ntitle = 'lamp'\ndate = 2031-04-02\n+++\n\nIt turns.\n"
    revs = {}
    revs["written"] = _commit(
        root,
        "Write the claims",
        {
            "content/posts/lamp/index.md": post,
            "notes/claims.md": "# Claims\n\n" + LAMP + "\n" + HORN,
        },
        _toml("notes/claims.md"),
    )
    sharp = LAMP.replace("ten seconds", "ten seconds, all night")
    revs["sharpened"] = _commit(
        root,
        "Sharpen the lamp",
        {"notes/claims.md": "# Claims\n\n" + sharp + "\n" + HORN},
    )
    revs["moved"] = _commit(
        root,
        "Move the claims into spec",
        {"spec/claims.md": "# Claims\n\n" + sharp + "\n" + HORN},
        _toml("spec/claims.md"),
        gone=["notes/claims.md"],
    )
    revs["split"] = _commit(
        root,
        "Give each claim a file",
        {"spec/flash.md": "# Claims\n\n" + sharp, "spec/horn.md": "\n" + HORN},
        _toml(["spec/flash.md", "spec/horn.md"]),
        gone=["spec/claims.md"],
    )
    revs["horn"] = _commit(
        root,
        "Sharpen the horn",
        {"spec/horn.md": "\n" + HORN.replace("in fog", "in fog and snow")},
    )
    settings = {"manuscript": {**SITE, "spec": ["spec/flash.md", "spec/horn.md"]}}
    with project_scope(Project.from_settings(root, settings)):
        yield root, revs


def test_a_move_and_a_split_change_no_section(site, capsys):
    _, revs = site
    assert specdiff.diff(f"{revs['sharpened']}..{revs['split']}") == []
    assert specdiff.main([f"{revs['written']}..HEAD"]) == 0
    out = capsys.readouterr().out
    assert "changed   #flash" in out and "changed   #horn" in out
    assert "new" not in out and "gone" not in out


def test_by_commit_names_only_the_commits_that_changed_a_section(site):
    _, revs = site
    found = specdiff.by_commit(f"{revs['written']}..HEAD")
    assert [(c.subject, [x.slug for x in c.changes]) for c in found] == [
        ("Sharpen the lamp", ["flash"]),
        ("Sharpen the horn", ["horn"]),
    ]


def test_each_revision_is_read_through_its_own_configuration(site):
    _, revs = site
    assert history.spec_at(revs["written"]).files == (("notes/claims.md", None),)
    assert history.spec_at(revs["split"]).how == "configuration"
    where = history.locate("horn", revs["moved"])
    assert (where.path, where.first) == ("spec/claims.md", 7)


def test_the_trail_follows_a_section_across_the_move_and_the_split(site):
    commits, reason = trail._commits("flash")
    assert reason is None
    # The move and the split carried the lamp unchanged, so neither is a lead.
    assert [c[2] for c in commits] == ["Write the claims", "Sharpen the lamp"]
    commits, _ = trail._commits("horn")
    assert [c[2] for c in commits] == ["Write the claims", "Sharpen the horn"]


def test_a_renamed_setting_in_an_old_revision_is_read_under_its_new_name(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    git(root, "init", "-q")
    old = _commit(
        root,
        "Write the claims",
        {"spec/claims.md": "# Claims\n\n" + LAMP, "spec/reading.md": "# Reading\n"},
        _toml("spec/claims.md", 'references = "spec/reading.md"\n'),
    )
    settings = {"manuscript": {**SITE, "spec": "spec/claims.md"}}
    with project_scope(Project.from_settings(root, settings)):
        assert history.spec_at(old).how == "configuration"


def _unconfigured(tmp_path, moves=None):
    """A first commit made before the project had a configuration, then the
    spec moved into spec/ with one."""
    root = tmp_path / "site"
    root.mkdir()
    git(root, "init", "-q")
    first = _commit(root, "Write the claims", {"notes/claims.md": "# C\n\n" + LAMP})
    _commit(
        root,
        "Configure the site",
        {"spec/claims.md": "# C\n\n" + LAMP},
        _toml("spec/claims.md"),
        gone=["notes/claims.md"],
    )
    settings = {**SITE, "spec": "spec/claims.md"}
    if moves is not None:
        (root / "policy.toml").write_text(
            "[moved_paths]\n" + "".join(f'"{k}" = "{v}"\n' for k, v in moves.items())
        )
        settings["chapter_policy"] = "policy.toml"
    return root, first, Project.from_settings(root, {"manuscript": settings})


def test_an_unreadable_configuration_falls_back_to_the_moved_paths(tmp_path, capsys):
    _, first, project = _unconfigured(tmp_path, {"notes/claims.md": "spec/claims.md"})
    with project_scope(project):
        spec = history.spec_at(first)
        assert (spec.files, spec.how) == ((("notes/claims.md", None),), "moved paths")
        assert "holds no quirework.toml" in spec.why
        assert specdiff.main([f"{first}..HEAD"]) == 0
    captured = capsys.readouterr()
    assert "no spec section changed" in captured.out
    assert "was read through the moved paths" in captured.err


def test_a_directory_move_places_every_file_below_it(tmp_path):
    _, first, project = _unconfigured(tmp_path, {"notes/": "spec/"})
    with project_scope(project):
        assert history.spec_at(first).files == (("notes/claims.md", None),)


def test_a_file_nothing_places_stops_the_read_and_is_named(tmp_path, capsys):
    _, first, project = _unconfigured(tmp_path)
    with project_scope(project):
        with pytest.raises(history.Unplaced, match="spec file spec/claims.md"):
            history.spec_at(first)
        # Never today's files, which would report the lamp as new.
        assert specdiff.main([f"{first}..HEAD"]) == 2
    assert "no moved path places it there" in capsys.readouterr().err


def test_one_section_is_followed_past_a_file_created_later(tmp_path):
    """A revision with no configuration cannot place a spec file created
    after it, so its whole spec is unplaced; a section of an older file is
    still found, because only its own file needs placing."""
    _, first, project = _unconfigured(tmp_path, {"notes/claims.md": "spec/claims.md"})
    root = project.root
    _commit(
        root,
        "Add the keeper's file",
        {"spec/keeper.md": "# K\n\n## Someone keeps it {#keeper}\n\nNightly.\n"},
        _toml(["spec/claims.md", "spec/keeper.md"]),
    )
    settings = {**SITE, "spec": ["spec/claims.md", "spec/keeper.md"]}
    settings["chapter_policy"] = "policy.toml"
    with project_scope(Project.from_settings(root, {"manuscript": settings})):
        with pytest.raises(history.Unplaced, match="spec/keeper.md"):
            history.spec_at(first)
        assert history.locate("flash", first).path == "notes/claims.md"
        commits, reason = trail._commits("flash")
        assert reason is None
        assert [c[2] for c in commits] == ["Write the claims"]
