"""A pinned download is a supply-chain control, so its order of work is tested.

Every case here serves an archive over `file://`. Nothing reaches the network,
and the fixtures are a few bytes of fictional text.
"""

import hashlib
import tarfile
import zipfile

import pytest
from quirework.core import pinned


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _zip_with_root(tmp_path, root="widget-1.0"):
    """A release that wraps its payload in one directory, as EPUBCheck does."""
    archive = tmp_path / "widget-1.0.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(f"{root}/widget.jar", "a fictional payload")
        bundle.writestr(f"{root}/README", "fictional notes")
    return archive


def _tar_without_root(tmp_path):
    """A release that unpacks straight into place, as KOReader does."""
    payload = tmp_path / "staging"
    (payload / "bin").mkdir(parents=True)
    (payload / "bin" / "widget").write_text("#!/bin/sh\necho fictional\n")
    (payload / "bin" / "widget").chmod(0o755)
    archive = tmp_path / "widget-1.0.tar.xz"
    with tarfile.open(archive, "w:xz") as bundle:
        for item in sorted(payload.rglob("*")):
            bundle.add(item, arcname=str(item.relative_to(payload)))
    return archive


@pytest.fixture
def cache(tmp_path, monkeypatch):
    home = tmp_path / "cache"
    home.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(home))
    return home / "quirework"


def test_an_archive_with_its_own_root_directory_is_not_nested_twice(tmp_path, cache):
    archive = _zip_with_root(tmp_path)
    got = pinned.ensure(
        "widget",
        "1.0",
        archive.as_uri(),
        _sha(archive),
        "widget.jar",
        log=lambda _message: None,
    )
    assert got == cache / "widget-1.0" / "widget.jar"
    assert got.read_text() == "a fictional payload"


def test_an_archive_without_a_root_directory_lands_in_the_tool_directory(
    tmp_path, cache
):
    archive = _tar_without_root(tmp_path)
    got = pinned.ensure(
        "widget",
        "1.0",
        archive.as_uri(),
        _sha(archive),
        "bin/widget",
        log=lambda _message: None,
    )
    assert got == cache / "widget-1.0" / "bin" / "widget"
    # The executable bit has to survive, or the tool cannot be launched.
    assert got.stat().st_mode & 0o111


def test_a_wrong_checksum_discards_the_archive_and_unpacks_nothing(tmp_path, cache):
    archive = _zip_with_root(tmp_path)
    with pytest.raises(pinned.PinnedToolError) as caught:
        pinned.ensure(
            "widget",
            "1.0",
            archive.as_uri(),
            "0" * 64,
            "widget.jar",
            log=lambda _message: None,
        )
    assert "checksum mismatch" in str(caught.value)
    # Nothing executable may reach the filesystem from an archive whose bytes
    # were not the pinned ones, and the bad download is not left to be
    # skipped by the exists() test on the next run.
    assert not (cache / "widget-1.0").exists()
    assert list(cache.glob("*.zip")) == []


def test_an_override_is_honored_before_the_cache(tmp_path, cache, monkeypatch):
    supplied = tmp_path / "packaged" / "widget.jar"
    supplied.parent.mkdir()
    supplied.write_text("a distribution's own copy")
    monkeypatch.setenv("WIDGET_JAR", str(supplied))
    assert pinned.find("widget", "1.0", "widget.jar", env_override="WIDGET_JAR") == (
        supplied
    )
    # An override naming something absent reports absence rather than falling
    # back, so a broken package is visible instead of silently replaced.
    monkeypatch.setenv("WIDGET_JAR", str(tmp_path / "gone.jar"))
    assert pinned.find("widget", "1.0", "widget.jar", env_override="WIDGET_JAR") is None


def test_a_cached_tool_is_not_downloaded_again(tmp_path, cache):
    archive = _zip_with_root(tmp_path)
    digest, url = _sha(archive), archive.as_uri()
    first = pinned.ensure(
        "widget", "1.0", url, digest, "widget.jar", log=lambda _m: None
    )
    archive.unlink()  # a second fetch would now fail outright
    assert (
        pinned.ensure("widget", "1.0", url, digest, "widget.jar", log=lambda _m: None)
        == first
    )


def test_the_cache_root_follows_the_xdg_variable(cache):
    assert pinned.cache_root() == cache
    assert pinned.tool_root("widget", "1.0") == cache / "widget-1.0"


def test_a_missing_download_reports_the_tool_and_not_a_traceback(tmp_path, cache):
    missing = (tmp_path / "absent.zip").as_uri()
    with pytest.raises(pinned.PinnedToolError) as caught:
        pinned.ensure(
            "widget", "1.0", missing, "0" * 64, "widget.jar", log=lambda _m: None
        )
    assert str(caught.value).startswith("widget: download failed")


def test_install_reports_an_exit_status_rather_than_raising(tmp_path, cache, capsys):
    archive = _zip_with_root(tmp_path)
    assert (
        pinned.install("widget", "1.0", archive.as_uri(), "0" * 64, "widget.jar") == 1
    )
    assert "checksum mismatch" in capsys.readouterr().err
    assert (
        pinned.install("widget", "1.0", archive.as_uri(), _sha(archive), "widget.jar")
        == 0
    )
    capsys.readouterr()
    assert (
        pinned.install("widget", "1.0", archive.as_uri(), _sha(archive), "widget.jar")
        == 0
    )
    assert "already installed" in capsys.readouterr().out
