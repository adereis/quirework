"""Isolated synthetic projects for Quirework unit tests."""

import os
import shutil
import stat
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from quirework.project import Project, project_scope

# The same isolation a consumer's suite opts into, so the two cannot drift:
# HOME and the XDG trees point into the session's own sandbox, usage and live
# canaries are off, and a write into a real per-user location fails its test.
pytest_plugins = ["quirework.testing"]

RUN_PREFIX = "quirework-tests-"
# A session's run directory is taken for abandoned at this age, the same
# three days after which pytest takes a session's lock for dead. No suite
# run lasts that long, so a sweep never pulls files from under a live one.
ABANDONED_AFTER = 3 * 24 * 60 * 60
RUN = pytest.StashKey[Path]()


def project_at(root):
    return Project.from_settings(root, {})


def corpus_settings(root="private/corpus", store="private/store", **extra):
    """A `[provenance]` section whose store sits in the test's own directory."""
    return {"enabled": True, "root": root, "store_dir": store, **extra}


def keep_session(project, reference, content, *, pending=False, mtime=None):
    """Put one captured file in the project's store; return where it landed.

    ``reference`` names it as the store does, ``cc-sessions/<id>.jsonl``. The
    file is accepted unless ``pending``, which leaves it waiting the way a
    capture nobody has read yet waits. Writing a session that is already
    accepted accepts the new text as its next version.
    """
    from quirework.project import ConfigurationError
    from quirework.provenance import store

    with project_scope(project):
        try:
            corpus = store.open_store()
        except ConfigurationError:
            corpus, _ = store.init()
        family, rel = reference.split("/", 1)
        target = corpus.pending_dir(family) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        if mtime is not None:
            os.utime(target, (mtime, mtime))
        if pending:
            return target
        store.accept([store.unit_of(family, rel)], live=True)
        return store.path_of(family, rel)


def patch_setting(monkeypatch, module, accessor, key, value):
    original = getattr(module, accessor)

    def configured():
        values = vars(original()).copy()
        values[key] = value
        return SimpleNamespace(**values)

    monkeypatch.setattr(module, accessor, configured)


def patch_project_path(monkeypatch, module, key, value):
    """Give `module` the current project with one path setting replaced."""
    original = module.current_project()
    settings = original.describe()["settings"]
    section, field = key.split(".", 1)
    settings.setdefault(section, {})[field] = str(value)
    selected = Project.from_settings(original.root, settings)
    monkeypatch.setattr(module, "current_project", lambda: selected)


# The helper a consumer's fake harness uses too, so a resume finds a test's
# session by the runner's own layout and no table here repeats it.
from quirework.testing import native_session  # noqa: E402,F401


@pytest.fixture(autouse=True)
def isolated_project(tmp_path):
    with project_scope(Project.from_settings(tmp_path / "project", {})):
        yield


@pytest.fixture
def corpus_project(tmp_path):
    """The current project, given an empty provenance store of its own."""
    from quirework.provenance import store

    project = Project.from_settings(
        tmp_path / "project", {"provenance": corpus_settings()}
    )
    with project_scope(project):
        store.init()
        yield project


@pytest.fixture
def three_hours_behind_utc(monkeypatch):
    """Put the machine's local zone at UTC-3, which keeps no daylight saving."""
    monkeypatch.setenv("TZ", "Etc/GMT+3")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def remove_run(run):
    """Remove a session's run directory, sharing the work with any other.

    Two sessions that start together can both sweep the same abandoned run,
    and each then finds entries the other has already removed. That is
    progress rather than failure, so the removal goes on until nothing is
    left.
    """
    while os.path.lexists(run):
        try:
            shutil.rmtree(run)
        except FileNotFoundError:
            continue


def sweep_abandoned_runs(temporary):
    """Remove the run directories that sessions left behind long ago."""
    cutoff = time.time() - ABANDONED_AFTER
    for run in temporary.glob(f"{RUN_PREFIX}*"):
        try:
            status = run.lstat()
        except FileNotFoundError:
            continue  # another session swept it between listing and now
        # `lstat` keeps a link that borrows the name from leading the sweep
        # out of the temporary tree.
        if stat.S_ISDIR(status.st_mode) and status.st_mtime < cutoff:
            remove_run(run)


def pytest_configure(config):
    """Keep test scratch files in the contributor's private temporary tree."""
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

    # Each session gets a run directory of its own, because pytest empties a
    # given basetemp at the start of a session and several sessions can run
    # at once from different worktrees. A given basetemp also turns off
    # pytest's own rotation of old sessions, so the suite cleans up after
    # itself: here it sweeps runs long abandoned, and at the end of the
    # session it removes its own run if every test passed.
    if config.option.basetemp is None:
        temporary = Path.home() / "tmp"
        temporary.mkdir(mode=0o700, exist_ok=True)
        sweep_abandoned_runs(temporary)
        run = Path(tempfile.mkdtemp(prefix=RUN_PREFIX, dir=temporary))
        config.stash[RUN] = run
        config.option.basetemp = str(run / "work")
    # Everything per-user is `quirework.testing`'s, which runs after this and
    # puts its sandbox beside basetemp, inside the run this removes. The tests
    # *about* usage records and canaries drop its switches in their own
    # fixtures, and the tests about the provenance home set their own.
    # Every key the code reads must be one `quirework.settings` lists, or a
    # project could not learn of it from `config keys`; the session's end
    # fails on any that is not.
    from quirework import settings

    settings.recording()


@pytest.fixture
def canaries_pass(monkeypatch):
    """Every launch in this test finds a passing canary without running one.

    A launch now proves its seat blind first, which needs a real runner's
    version and a model. A test about something else a launch does stubs the
    gate here and can read, from the returned list, which launches asked;
    the gate itself is tested in test_canary.py.
    """
    from quirework.providers import canary

    asked = []

    def require(label, runner, build, contract=canary.BLIND, **_):
        asked.append(dict(label=label, runner=runner, contract=contract))
        return {"runner": runner, "version": "0.0.0-test", "label": label}

    monkeypatch.setattr(canary, "require", require)
    return asked


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """Fail a session that read an unlisted key; remove a passing session's
    run, and thin what a failing one keeps."""
    from quirework import settings

    missing = settings.unregistered()
    if missing:
        print(
            "\nRead but not listed in quirework/settings.py, so `config keys` "
            "cannot tell a project of them:\n  " + "\n  ".join(missing)
        )
        session.exitstatus = exitstatus = pytest.ExitCode.TESTS_FAILED
    run = session.config.stash.get(RUN, None)
    if run is None:
        return  # the caller chose basetemp, so the caller keeps it
    if exitstatus == pytest.ExitCode.OK:
        remove_run(run)
        return
    # pytest removes a passing test's directory but not the `<name>current`
    # link it made to it, so a failing session would keep one dangling link
    # per passing test around the few directories worth reading.
    for link in Path(session.config.option.basetemp).glob("*current"):
        if link.is_symlink() and not link.exists():
            link.unlink()
