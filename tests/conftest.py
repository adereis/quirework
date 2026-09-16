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
    if config.option.basetemp is None:
        import tempfile
        from pathlib import Path

        temporary = Path.home() / "tmp"
        temporary.mkdir(mode=0o700, exist_ok=True)
        config.option.basetemp = str(
            Path(tempfile.mkdtemp(prefix="quirework-tests-", dir=temporary)) / "work"
        )
