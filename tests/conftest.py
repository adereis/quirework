"""Isolated synthetic projects for Quirework unit tests."""

import time
from types import SimpleNamespace
import pytest
from quirework.project import Project, project_scope


def project_at(root):
    return Project.from_settings(root, {})


def patch_setting(monkeypatch, module, accessor, key, value):
    original = getattr(module, accessor)

    def configured():
        values = vars(original()).copy()
        values[key] = value
        return SimpleNamespace(**values)

    monkeypatch.setattr(module, accessor, configured)


@pytest.fixture(autouse=True)
def isolated_project(tmp_path):
    with project_scope(Project.from_settings(tmp_path / "project", {})):
        yield


@pytest.fixture
def three_hours_behind_utc(monkeypatch):
    """Put the machine's local zone at UTC-3, which keeps no daylight saving."""
    monkeypatch.setenv("TZ", "Etc/GMT+3")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def pytest_configure(config):
    """Keep test scratch files in the contributor's private temporary tree."""
    import os
    import tempfile
    from pathlib import Path

    import quirework

    # A suite run with another project's environment active imports that
    # project's copy of Quirework and tests it instead of this checkout, and
    # passes. Nothing about a green run says which code it exercised, so
    # refuse rather than report on the wrong tree.
    checkout = Path(__file__).resolve().parents[1] / "src"
    imported = Path(quirework.__file__).resolve()
    if not imported.is_relative_to(checkout):
        raise pytest.UsageError(
            f"these tests would exercise the Quirework at {imported.parent}, "
            f"not this checkout's {checkout}: run them with this checkout's own "
            ".venv, or put its src first on PYTHONPATH"
        )

    if config.option.basetemp is None:
        temporary = Path.home() / "tmp"
        temporary.mkdir(mode=0o700, exist_ok=True)
        config.option.basetemp = str(
            Path(tempfile.mkdtemp(prefix="quirework-tests-", dir=temporary)) / "work"
        )
    # A project that configures no store falls back to the XDG state tree, so
    # a test that forgets `runs.store` writes run records into the
    # contributor's real home, and every test that dispatches a command now
    # writes a usage record there too. Point the tree at the test run's own
    # directory once, rather than trusting each test to remember. It sits
    # beside basetemp instead of inside it, because pytest empties basetemp
    # at the start of a session.
    state = Path(config.option.basetemp).parent / "quirework-state"
    os.environ["XDG_STATE_HOME"] = str(state)
    # The cache tree has the same hole: a project's cache root sits under it,
    # and a command that takes a lock or caches a digest there wrote into the
    # contributor's real ~/.cache, keyed by a test directory that was already
    # gone. The trail's write lock left forty of them before this line.
    os.environ["XDG_CACHE_HOME"] = str(state.parent / "quirework-cache")
    # Relocating the store is not the same as not recording, and a test run is
    # not use of a command: a suite that dispatches `sop show` forty times to
    # check its exit status should leave no usage records anywhere. The tests
    # that are *about* recording drop this variable in their own fixture, so
    # switching it off here costs them nothing.
    os.environ["QUIREWORK_USAGE"] = "0"
