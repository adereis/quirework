"""The plugin a suite opts into to keep out of its author's per-user state.

Each test runs a whole pytest session of a fictional consumer in a
subprocess, whose own "real" home is a directory of this test's, so nothing
here can reach the person running the suite.
"""

import pytest

pytest_plugins = ["pytester"]

CONFTEST = 'pytest_plugins = ["quirework.testing"]\n'


@pytest.fixture
def consumer(pytester, tmp_path, monkeypatch):
    """A consumer's suite, run as if by someone whose home is ``real``."""
    real = tmp_path / "real-home"
    real.mkdir()
    monkeypatch.setenv("HOME", str(real))
    for variable in ("XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    # This suite is isolated too; the consumer's session is a fresh one.
    monkeypatch.delenv("QUIREWORK_TEST_SANDBOX", raising=False)
    monkeypatch.delenv("QUIREWORK_TEST_GUARDED", raising=False)
    monkeypatch.setenv("QUIREWORK_PROVENANCE_HOME", str(real / "provenance"))
    monkeypatch.setenv("CODEX_HOME", str(real / "codex"))
    monkeypatch.setenv("QUIREWORK_USAGE", "1")
    monkeypatch.delenv("QUIREWORK_CANARY", raising=False)
    monkeypatch.setenv("REAL_HOME", str(real))
    pytester.makeconftest(CONFTEST)
    return pytester, real


def test_one_line_moves_every_per_user_location(consumer):
    """A suite that redirected the state tree but not the cache left nine
    seat homes in its author's real cache. Opted in, nothing a test can
    reach by Quirework's own rules is the person's."""
    pytester, real = consumer
    pytester.makepyfile(
        """
        import os
        from pathlib import Path

        from quirework.project import Project, project_scope, user_config_path
        from quirework.providers import blind
        from quirework.provenance import store

        REAL = Path(os.environ["REAL_HOME"])


        def test_quirework_finds_nothing_of_the_person(tmp_path):
            assert not Path.home().is_relative_to(REAL)
            for variable in (
                "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"
            ):
                assert not Path(os.environ[variable]).is_relative_to(REAL)
            assert "QUIREWORK_PROVENANCE_HOME" not in os.environ
            assert "CODEX_HOME" not in os.environ
            assert os.environ["QUIREWORK_USAGE"] == "0"
            assert os.environ["QUIREWORK_CANARY"] == "0"
            with project_scope(Project.from_settings(tmp_path, {})):
                seat_home = blind.make_state_root(tmp_path / "seat")
                assert not seat_home.is_relative_to(REAL)
                assert not store.find_home().path.is_relative_to(REAL)
                assert not user_config_path().is_relative_to(REAL)
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)
    assert sorted(p.name for p in real.iterdir()) == []


def test_a_write_into_a_real_location_fails_its_test(consumer):
    """Silent was the problem: the leak was found only by listing the cache
    afterwards. A write into a real location fails the test that made it,
    even when the code under test swallowed the error."""
    pytester, real = consumer
    pytester.makepyfile(
        """
        import os
        from pathlib import Path

        TARGET = Path(os.environ["REAL_HOME"]) / ".cache" / "quirework" / "homes"


        def test_writes_into_the_real_cache():
            TARGET.mkdir(parents=True)


        def test_swallows_the_refusal():
            try:
                (Path(os.environ["REAL_HOME"]) / "provenance").mkdir()
            except Exception:
                pass


        def test_stays_in_its_own_directory(tmp_path):
            (tmp_path / "notes.md").write_text("A kettle boils.\\n")
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1, failed=2)
    result.stdout.fnmatch_lines(["*IsolationLeak*/.cache*real per-user location*"])
    assert sorted(p.name for p in real.iterdir()) == []
