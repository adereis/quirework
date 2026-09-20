"""Isolated synthetic projects for Quirework unit tests."""

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


def pytest_configure(config):
    """Keep test scratch files in the contributor's private temporary tree."""
    import os
    import tempfile
    from pathlib import Path

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
