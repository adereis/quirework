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


# Where each harness writes a session in its home, by the names the real ones
# use, so a test's session is found the way a resume would find it.
NATIVE_SESSION = {
    "codex": "sessions/2026/01/02/rollout-2026-01-02T03-04-05-{id}.jsonl",
    "claude": "projects/-seat/{id}.jsonl",
    "agy": "antigravity-cli/brain/{id}/.system_generated/logs/transcript_full.jsonl",
}


def native_session(run_dir, harness, session_id):
    """Leave a session in a seat's private home, as its harness would.

    A resume is refused unless the home holds the session it continues, so
    a fake launcher that hands a seat a session id leaves one behind too.
    """
    from quirework.providers import blind

    path = blind.state_root(Path(run_dir)) / harness
    path = path / NATIVE_SESSION[harness].format(id=session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


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
    # The provenance home is read from the environment and then from the
    # per-user configuration, and a contributor who keeps real sessions sets
    # one of the two. Either would send a test's named store into that real
    # home, so the variable is dropped and the configuration directory points
    # at one no test writes; the tests about the home set their own.
    os.environ.pop("QUIREWORK_PROVENANCE_HOME", None)
    os.environ["XDG_CONFIG_HOME"] = str(state.parent / "quirework-config")
    # Relocating the store is not the same as not recording, and a test run is
    # not use of a command: a suite that dispatches `sop show` forty times to
    # check its exit status should leave no usage records anywhere. The tests
    # that are *about* recording drop this variable in their own fixture, so
    # switching it off here costs them nothing.
    os.environ["QUIREWORK_USAGE"] = "0"
    # A launch now proves its seat blind with a live canary first. A test that
    # drives a launch without stubbing that gate (the `canaries_pass` fixture)
    # would reach a real runner and spend real tokens, so it is refused
    # instead. The tests of the canary itself drop this in their own fixture.
    os.environ["QUIREWORK_CANARY"] = "0"


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
    """Remove a passing session's run, and thin what a failing one keeps."""
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
