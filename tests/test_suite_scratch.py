"""The suite's own scratch runs: the abandoned are swept, the live spared.

Each session writes under a run directory of its own in `~/tmp`, which
pytest never rotates, so the suite sweeps what sessions left behind. A
sweep deletes trees in the contributor's home, which is why its limits are
pinned here. Every run below is a stand-in under the test's own directory.
"""

import os
import shutil
import time

from conftest import remove_run, sweep_abandoned_runs

DAY = 24 * 60 * 60


def left(root, name, days_ago):
    """A run directory with one test's files, last touched `days_ago`."""
    run = root / name
    (run / "work" / "test_a_kettle_whistles0").mkdir(parents=True)
    (run / "work" / "test_a_kettle_whistles0" / "note.md").write_text("Steam.\n")
    then = time.time() - days_ago * DAY
    os.utime(run, (then, then))
    return run


def test_a_run_three_days_abandoned_is_swept_and_a_younger_one_kept(tmp_path):
    abandoned = left(tmp_path, "quirework-tests-abandon1", 4)
    live = left(tmp_path, "quirework-tests-liverun1", 2)
    stranger = left(tmp_path, "kettle-notes", 4)

    sweep_abandoned_runs(tmp_path)

    assert not abandoned.exists()
    assert (live / "work" / "test_a_kettle_whistles0" / "note.md").exists()
    assert stranger.exists()


def test_a_link_that_borrows_the_name_is_not_followed(tmp_path):
    elsewhere = left(tmp_path, "kettle-notes", 4)
    link = tmp_path / "quirework-tests-borrowed"
    link.symlink_to(elsewhere)
    then = time.time() - 4 * DAY
    os.utime(link, (then, then), follow_symlinks=False)

    sweep_abandoned_runs(tmp_path)

    assert link.is_symlink()
    assert (elsewhere / "work" / "test_a_kettle_whistles0" / "note.md").exists()


def test_a_run_another_session_is_removing_too_is_still_removed(tmp_path, monkeypatch):
    run = left(tmp_path, "quirework-tests-shared01", 4)
    rmtree = shutil.rmtree

    def outpaced(path, *args, **kwargs):
        # The other session removes part of the tree first, so this one
        # trips over an entry that is already gone.
        monkeypatch.setattr(shutil, "rmtree", rmtree)
        rmtree(path / "work")
        raise FileNotFoundError(path / "work")

    monkeypatch.setattr(shutil, "rmtree", outpaced)

    remove_run(run)

    assert not os.path.lexists(run)
