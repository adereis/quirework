"""Worktree discovery on synthetic git plumbing, never the real repo.

The failure this module prevents is silent: a sweep run from one checkout
captures nothing from the others, reports success, and the sessions are simply
absent from the corpus. So the tests assert the *set* of checkouts, and the
containment rule that decides whether a recorded cwd is inside one -- including
the sibling-name case (`repo-codex` beside `repo`) that a string prefix gets
wrong in exactly the direction that quietly swallows another project.
"""

from quirework.provenance import worktrees


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


def test_a_repo_without_worktrees_is_just_itself(tmp_path):
    root = _repo(tmp_path)
    assert worktrees.checkouts(root) == [root]


def test_every_checkout_is_found_from_any_checkout(tmp_path):
    main = _repo(tmp_path)
    a = _worktree(main, tmp_path, "wt-a")
    b = _worktree(main, tmp_path, "wt-b")
    expected = sorted([main, a, b])
    assert worktrees.checkouts(main) == expected
    assert worktrees.checkouts(a) == expected  # the case that used to miss
    assert worktrees.checkouts(b) == expected


def test_found_from_a_subdirectory_of_a_checkout(tmp_path):
    main = _repo(tmp_path)
    wt = _worktree(main, tmp_path, "wt-a")
    deep = wt / "docs" / "canon"
    deep.mkdir(parents=True)
    assert worktrees.checkouts(deep) == sorted([main, wt])


def test_a_deleted_but_unpruned_worktree_is_skipped(tmp_path):
    main = _repo(tmp_path)
    wt = _worktree(main, tmp_path, "wt-gone")
    (wt / ".git").unlink()
    wt.rmdir()
    assert worktrees.checkouts(main) == [main]


def test_a_plain_directory_degrades_to_itself(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert worktrees.checkouts(plain) == [plain]


def test_contains_matches_a_checkout_and_anything_under_it(tmp_path):
    main = _repo(tmp_path)
    wt = _worktree(main, tmp_path, "wt-a")
    roots = worktrees.checkouts(main)
    assert worktrees.contains(roots, str(main))
    assert worktrees.contains(roots, str(main / "docs" / "canon"))
    assert worktrees.contains(roots, str(wt / "src"))


def test_the_scratchpad_is_excluded_from_every_checkout(tmp_path):
    """A session opened in the scratchpad is exhaust, not the author working."""
    from quirework.project import Project

    main = _repo(tmp_path)
    wt = _worktree(main, tmp_path, "wt-a")
    roots = worktrees.checkouts(main)
    project = Project.from_settings(main, {"project": {"scratchpad": "scratchpad"}})
    excluding = worktrees.excluded_trees(project, roots)
    assert worktrees.contains(roots, str(main / "docs"), excluding)
    assert not worktrees.contains(roots, str(main / "scratchpad"), excluding)
    assert not worktrees.contains(roots, str(main / "scratchpad" / "w" / "x"), excluding)
    assert not worktrees.contains(roots, str(wt / "scratchpad" / "w"), excluding)
    # a name that merely extends the scratchpad's is not the scratchpad
    assert worktrees.contains(roots, str(main / "scratchpad-notes"), excluding)


def test_a_project_without_a_scratchpad_excludes_nothing(tmp_path):
    from quirework.project import Project

    main = _repo(tmp_path)
    roots = worktrees.checkouts(main)
    project = Project.from_settings(main, {})
    assert worktrees.excluded_trees(project, roots) == []


def test_contains_rejects_a_sibling_whose_name_extends_a_checkout(tmp_path):
    """`example-book-worktree` is not inside `example-book`.

    A `str.startswith` test says it is, and that is how an unrelated project's
    sessions would be swept into a private corpus.
    """
    main = _repo(tmp_path, "repo")
    sibling = tmp_path / "repo-codex"
    sibling.mkdir()
    roots = worktrees.checkouts(main)
    assert not worktrees.contains(roots, str(sibling))
    assert not worktrees.contains(roots, str(sibling / "docs"))


def test_contains_is_false_for_nothing_at_all(tmp_path):
    roots = worktrees.checkouts(_repo(tmp_path))
    assert not worktrees.contains(roots, None)
    assert not worktrees.contains(roots, "")


def test_a_project_at_a_checkout_root_sees_every_checkout(tmp_path):
    main = _repo(tmp_path)
    wt = _worktree(main, tmp_path, "wt-a")
    assert worktrees.project_trees(main) == sorted([main, wt])


def test_a_nested_project_sees_only_its_own_directories(tmp_path):
    """An example book inside its tool's repo must not inherit the tool's sessions.

    Both checkouts hold the book at the same relative position, so both of its
    directories are in scope and neither checkout root is.
    """
    main = _repo(tmp_path)
    wt = _worktree(main, tmp_path, "wt-a")
    for checkout in (main, wt):
        (checkout / "examples" / "demo-book").mkdir(parents=True)
    book = main / "examples" / "demo-book"
    trees = worktrees.project_trees(book)
    assert trees == sorted([book, wt / "examples" / "demo-book"])
    assert not worktrees.contains(trees, str(main / "src"))
    assert worktrees.contains(trees, str(book / "manuscript"))


def test_a_nested_project_absent_from_a_worktree_is_skipped(tmp_path):
    main = _repo(tmp_path)
    _worktree(main, tmp_path, "wt-a")
    book = main / "examples" / "demo-book"
    book.mkdir(parents=True)
    assert worktrees.project_trees(book) == [book]


def test_a_plain_directory_is_its_own_project_tree(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert worktrees.project_trees(plain) == [plain]
