"""Edit counts come from recorded project trees, never from live worktrees.

A session records absolute paths from the checkout it ran in, and whether an
edit was document work depends on measuring that path from the project. When
the measure came from `git worktree list`, removing a worktree moved its
sessions' document edits into the metadata column. The committed catalog then
failed `--check` on commits that changed no session, and regenerating it would
have committed the wrong counts. These tests pin the rule that replaced it:
the trees are recorded while they exist and read back from the corpus alone.
"""

import json
import shutil
import subprocess

import pytest

from quirework.project import Project, project_scope
from quirework.provenance import catalog, coldread, trees


def _repo(tmp_path, name="main"):
    """A main checkout: a directory with a real `.git` *directory*."""
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    return root


def _worktree(main, tmp_path, name):
    """A linked checkout, wired both ways exactly as `git worktree add` does."""
    checkout = tmp_path / name
    checkout.mkdir(parents=True)
    admin = main / ".git" / "worktrees" / name
    admin.mkdir(parents=True)
    (admin / "gitdir").write_text(f"{checkout / '.git'}\n")
    (checkout / ".git").write_text(f"gitdir: {admin}\n")
    return checkout


def _remove_worktree(main, checkout):
    """What `git worktree remove` leaves behind: neither directory."""
    shutil.rmtree(checkout)
    shutil.rmtree(main / ".git" / "worktrees" / checkout.name)


def _book(root):
    return Project.from_settings(root, {"book": {"source": "manuscript"}})


def _session(path, cwd, *edited):
    """A Claude Code capture started in `cwd` that edits each of `edited`."""
    rows = [{"type": "user", "cwd": str(cwd), "message": {"content": "go"}}]
    rows += [
        {
            "type": "assistant",
            "cwd": str(cwd),
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Edit", "input": {"file_path": str(f)}}
                ]
            },
        }
        for f in edited
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_a_removed_worktrees_manuscript_edits_stay_document_work(tmp_path):
    main = _repo(tmp_path)
    linked = _worktree(main, tmp_path, "main-canon")
    with project_scope(_book(main)) as project:
        assert trees.record(project) == [str(main), str(linked)]
        session = _session(
            tmp_path / "s.jsonl",
            linked,
            linked / "manuscript" / "one.qmd",
            linked / "tools" / "sweep.py",
        )
        before = coldread._edit_targets(session)

        _remove_worktree(main, linked)

        assert coldread._edit_targets(session) == before
        assert before[:2] == (1, 1)
        # A later sweep sees only the main checkout and forgets nothing.
        assert trees.record(project) == []
        assert trees.recorded(project) == (str(main), str(linked))


def test_a_live_worktree_nobody_recorded_changes_no_count(tmp_path):
    """The machine's worktrees are not an input. Only the recorded list is."""
    main = _repo(tmp_path)
    linked = _worktree(main, tmp_path, "main-scratch")
    with project_scope(_book(main)):
        session = _session(
            tmp_path / "s.jsonl", linked, linked / "manuscript" / "one.qmd"
        )
        assert coldread._edit_targets(session)[:2] == (0, 1)


def test_a_book_nested_in_a_repository_is_measured_from_its_own_directory(
    tmp_path,
):
    """`manuscript/` is the book's, not a directory at the repository root.

    Measuring from the checkout left `examples/book/manuscript/...`, which no
    document prefix matches, so a nested book counted none of its manuscript
    edits as document work.
    """
    checkout = _repo(tmp_path)
    book = checkout / "examples" / "book"
    book.mkdir(parents=True)
    with project_scope(_book(book)) as project:
        assert trees.record(project) == [str(book)]
        session = _session(
            tmp_path / "s.jsonl",
            book,
            book / "manuscript" / "one.qmd",
            book / "manuscript" / "two.qmd",
            checkout / "manuscript" / "tool-notes.md",
        )
        assert coldread._edit_targets(session)[:2] == (2, 1)


def test_a_worktree_inside_its_checkout_claims_its_own_files(tmp_path):
    main = _repo(tmp_path)
    nested = main / ".claude" / "worktrees" / "fix"
    with project_scope(_book(main)) as project:
        trees.location(project).parent.mkdir(parents=True)
        trees.location(project).write_text(f"{main}\n{nested}\n")
        session = _session(
            tmp_path / "s.jsonl", nested, nested / "manuscript" / "a.qmd"
        )
        assert coldread._edit_targets(session)[:2] == (1, 0)


def test_recording_appends_and_keeps_what_the_author_wrote(tmp_path):
    main = _repo(tmp_path)
    with project_scope(_book(main)) as project:
        listing = trees.location(project)
        listing.parent.mkdir(parents=True)
        listing.write_text(
            "# removed before recording existed\n/elsewhere/book-draft  # 2026\n"
        )
        assert trees.record(project) == [str(main)]
        assert listing.read_text() == (
            "# removed before recording existed\n"
            "/elsewhere/book-draft  # 2026\n"
            f"{main}\n"
        )
        assert trees.recorded(project) == ("/elsewhere/book-draft", str(main))


def test_containment_is_by_component_and_never_resolved(tmp_path):
    listed = ("/work/book",)
    assert trees.within(listed, "/work/book")
    assert trees.within(listed, "/work/book/manuscript")
    assert not trees.within(listed, "/work/book-draft")
    assert not trees.within(listed, None)
    # A symlink on this machine must not decide what another machine reads.
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    assert not trees.within((str(tmp_path / "real"),), str(tmp_path / "link"))


def _git(root, *args):
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-C",
            str(root),
            *args,
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def tracked_corpus(tmp_path):
    """A committed book whose one session ran in a checkout since removed."""
    root = tmp_path / "book"
    gone = tmp_path / "book-canon"
    root.mkdir()
    _git(root, "init", "-q")
    sessions = root / ".quirework/provenance/cc-sessions"
    _session(sessions / "a1.jsonl", gone / "docs", gone / "manuscript" / "one.qmd")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "fixture")
    with project_scope(_book(root)) as project:
        yield project, gone


def test_the_catalog_refuses_while_a_session_started_under_no_recorded_tree(
    tracked_corpus, capsys
):
    project, gone = tracked_corpus
    trees.record(project)

    assert catalog.main(["--check"]) == 1
    assert catalog.main([]) == 1
    err = capsys.readouterr().err
    assert "cannot count edits" in err
    assert f"{gone.name}/docs  (1, e.g. " in err
    assert ".quirework/provenance/project-trees.txt" in err
    assert not (project.root / ".quirework/provenance/index.json").exists()

    # The author lists the removed checkout's project directory by hand.
    with trees.location(project).open("a") as fh:
        fh.write(f"{gone}\n")
    assert catalog.main([]) == 0
    index = json.loads((project.root / ".quirework/provenance/index.json").read_text())
    row = index["sources"][".quirework/provenance/cc-sessions/a1.jsonl"]
    assert (row["doc_edits"], row["meta_edits"]) == (1, 0)
    assert catalog.main(["--check"]) == 0
