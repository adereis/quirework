"""The provenance store: sessions outside the checkout, decisions inside it.

These pin what the store has to guarantee for a corpus to leave git without
losing what git gave it. A session counts only once it is accepted, and the
ledger's list of hashes is what says so. An accepted version never changes, so
every branch's list keeps resolving to the bytes it accepted. The store is
found by a committed name and refuses a ledger that is not its own. Damage is
reported, never read around.

Every session here is fiction written for the purpose.
"""

import json
import os
import time
from pathlib import Path

import pytest

from conftest import corpus_settings, keep_session
from quirework.project import ConfigurationError, Project, project_scope
from quirework.provenance import store

KETTLE = "0badc0de-0000-4000-8000-000000000001"
STORM = "0badc0de-0000-4000-8000-000000000002"


def _book(tmp_path, **provenance):
    settings = corpus_settings(**provenance)
    return Project.from_settings(tmp_path / "book", {"provenance": settings})


def _transcript(*said):
    return "".join(
        json.dumps({"type": "user", "message": {"content": text}}) + "\n"
        for text in said
    )


@pytest.fixture
def book(tmp_path):
    project = _book(tmp_path)
    with project_scope(project):
        store.init()
        yield project


# --------------------------------------------------------------------------- #
# where the store is
# --------------------------------------------------------------------------- #
def test_a_named_store_lives_under_the_provenance_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QUIREWORK_PROVENANCE_HOME", str(tmp_path / "stores"))
    project = Project.from_settings(
        tmp_path / "book", {"provenance": {"enabled": True, "store": "kettle-book"}}
    )
    assert store.location(project) == tmp_path / "stores" / "kettle-book"


def test_without_a_home_the_store_is_user_data(tmp_path, monkeypatch):
    monkeypatch.delenv("QUIREWORK_PROVENANCE_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    project = Project.from_settings(
        tmp_path / "book", {"provenance": {"enabled": True, "store": "kettle-book"}}
    )
    expected = tmp_path / "data" / "quirework" / "provenance" / "kettle-book"
    assert store.location(project) == expected


def test_a_relative_data_home_is_ignored_as_the_standard_says(tmp_path, monkeypatch):
    monkeypatch.delenv("QUIREWORK_PROVENANCE_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", "relative/data")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = Project.from_settings(
        tmp_path / "book", {"provenance": {"enabled": True, "store": "kettle-book"}}
    )
    assert store.location(project).is_relative_to(tmp_path / "home" / ".local/share")


def test_a_relative_provenance_home_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("QUIREWORK_PROVENANCE_HOME", "stores")
    project = Project.from_settings(
        tmp_path / "book", {"provenance": {"enabled": True, "store": "kettle-book"}}
    )
    with pytest.raises(ConfigurationError, match="absolute path"):
        store.location(project)


def _user_config(tmp_path, monkeypatch, text):
    """Write the per-user configuration a test reads, and nothing else."""
    monkeypatch.delenv("QUIREWORK_PROVENANCE_HOME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    path = tmp_path / "config" / "quirework" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(text)
    return path


def _named(tmp_path):
    return Project.from_settings(
        tmp_path / "book", {"provenance": {"enabled": True, "store": "kettle-book"}}
    )


def test_the_user_configuration_names_the_home(tmp_path, monkeypatch):
    stores = tmp_path / "stores"
    _user_config(tmp_path, monkeypatch, f'[provenance]\nhome = "{stores}"\n')
    assert store.location(_named(tmp_path)) == stores / "kettle-book"
    assert store.find_home().chosen_by == "user"
    assert "from provenance.home in" in store.describe_home(_named(tmp_path))


def test_the_variable_overrides_the_user_configuration(tmp_path, monkeypatch):
    _user_config(tmp_path, monkeypatch, '[provenance]\nhome = "/elsewhere"\n')
    monkeypatch.setenv("QUIREWORK_PROVENANCE_HOME", str(tmp_path / "stores"))
    assert store.location(_named(tmp_path)) == tmp_path / "stores" / "kettle-book"
    assert store.describe_home(_named(tmp_path)).endswith(
        "from $QUIREWORK_PROVENANCE_HOME"
    )


def test_a_configured_home_may_start_at_the_home_directory(tmp_path, monkeypatch):
    _user_config(tmp_path, monkeypatch, '[provenance]\nhome = "~/stores"\n')
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    expected = tmp_path / "home" / "stores" / "kettle-book"
    assert store.location(_named(tmp_path)) == expected


def test_a_relative_configured_home_is_refused(tmp_path, monkeypatch):
    path = _user_config(tmp_path, monkeypatch, '[provenance]\nhome = "stores"\n')
    with pytest.raises(ConfigurationError, match="absolute path") as caught:
        store.location(_named(tmp_path))
    assert path.name in str(caught.value)


@pytest.mark.parametrize(
    "text, message",
    [
        ('[provenance]\nhoem = "/stores"\n', "provenance.hoem"),
        ('home = "/stores"\n', "unknown setting home"),
        ('[runs]\nstore = "/runs"\n', "unknown setting runs"),
        ("[provenance\n", "cannot read"),
    ],
)
def test_a_user_configuration_it_cannot_follow_is_refused(
    tmp_path, monkeypatch, text, message
):
    path = _user_config(tmp_path, monkeypatch, text)
    with pytest.raises(ConfigurationError, match=message) as caught:
        store.location(_named(tmp_path))
    assert str(path) in str(caught.value)


def test_an_edited_user_configuration_is_read_again(tmp_path, monkeypatch):
    path = _user_config(tmp_path, monkeypatch, '[provenance]\nhome = "/one"\n')
    assert store.home() == Path("/one")
    path.write_text('[provenance]\nhome = "/a-longer-two"\n')
    assert store.home() == Path("/a-longer-two")


def test_without_either_setting_the_default_says_so(tmp_path, monkeypatch):
    monkeypatch.delenv("QUIREWORK_PROVENANCE_HOME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    described = store.describe_home(_named(tmp_path))
    assert "the default" in described and "sets no provenance.home" in described


def test_a_store_named_by_directory_has_no_home_to_describe(tmp_path):
    assert store.describe_home(_book(tmp_path)) is None


@pytest.mark.parametrize(
    "provenance, message",
    [
        ({}, "exactly one"),
        ({"store": "kettle-book", "store_dir": "store"}, "exactly one"),
        ({"store": "../kettle-book"}, "plain name"),
        ({"store": "stores/kettle-book"}, "plain name"),
    ],
)
def test_the_store_is_declared_once_and_plainly(tmp_path, provenance, message):
    project = Project.from_settings(
        tmp_path / "book", {"provenance": {"enabled": True, **provenance}}
    )
    with pytest.raises(ConfigurationError, match=message):
        store.location(project)


def test_a_store_directory_resolves_from_the_configuration(tmp_path):
    project = _book(tmp_path, store="private/store")
    assert store.location(project) == tmp_path / "book" / "private" / "store"


def test_a_key_provenance_does_not_read_is_refused(tmp_path):
    with pytest.raises(ConfigurationError, match="provenance.claude_sessions"):
        Project.from_settings(
            tmp_path, {"provenance": {"enabled": True, "claude_sessions": "x"}}
        )


def test_a_retired_key_in_a_configuration_file_names_the_file(tmp_path):
    (tmp_path / "quirework.toml").write_text(
        'schema = 2\n[provenance]\nenabled = true\ncuration = "presence"\n'
    )
    with pytest.raises(ConfigurationError) as caught:
        Project.load(tmp_path)
    assert "quirework.toml" in str(caught.value)
    assert "provenance.curation" in str(caught.value)


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #
def test_init_binds_the_ledger_to_a_new_store(tmp_path):
    project = _book(tmp_path)
    with project_scope(project):
        created, fresh = store.init()
        assert fresh
        identity, entries = store.read_manifest(store.ledger() / store.MANIFEST)
        assert identity == created.identity and entries == []
        assert (store.location() / store.IDENTITY).is_file()
        again, fresh = store.init()
        assert not fresh and again.identity == created.identity


def test_a_store_is_private_to_its_owner(tmp_path):
    project = _book(tmp_path)
    with project_scope(project):
        store.init()
        assert (store.location().stat().st_mode & 0o777) == 0o700


def test_an_enabled_corpus_without_its_store_is_refused(tmp_path):
    project = _book(tmp_path)
    (tmp_path / "book" / "private" / "corpus").mkdir(parents=True)
    with pytest.raises(ConfigurationError, match="provenance store init"):
        project.require("provenance")


def test_two_projects_that_chose_one_name_never_share_a_store(tmp_path, monkeypatch):
    monkeypatch.setenv("QUIREWORK_PROVENANCE_HOME", str(tmp_path / "stores"))
    settings = {"enabled": True, "root": "ledger", "store": "notes"}
    first = Project.from_settings(tmp_path / "first", {"provenance": settings})
    second = Project.from_settings(tmp_path / "second", {"provenance": settings})
    with project_scope(first):
        store.init()
    with project_scope(second):
        with pytest.raises(ConfigurationError, match="another project"):
            store.init()
        with pytest.raises(ConfigurationError, match="store init"):
            store.open_store()


def test_a_ledger_naming_another_store_is_refused(book):
    path = store.ledger() / store.MANIFEST
    path.write_text("store 00000000-0000-4000-8000-0000000000ff\n")
    with pytest.raises(ConfigurationError, match="not this project's"):
        store.open_store()


def test_a_fresh_clone_creates_its_store_under_the_ledgers_identity(tmp_path):
    original = _book(tmp_path)
    keep_session(original, f"cc-sessions/{KETTLE}.jsonl", _transcript("Boil."))
    with project_scope(original):
        identity = store.open_store().identity
    # The clone's ledger is the same file; its store is somewhere new and empty.
    clone = Project.from_settings(
        tmp_path / "book",
        {"provenance": corpus_settings(store=str(tmp_path / "clone-store"))},
    )
    with project_scope(clone):
        created, fresh = store.init()
        assert fresh and created.identity == identity
        assert store.verify() == [f"missing   {store.open_store().entries()[0].stored}"]


def test_a_first_store_is_missing_until_init(tmp_path, monkeypatch):
    monkeypatch.delenv("QUIREWORK_PROVENANCE_HOME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    with pytest.raises(ConfigurationError) as caught:
        store.open_store(_named(tmp_path))
    said = str(caught.value)
    assert "its home is" in said and "the default" in said
    assert "Run `quirework provenance store init`" in said
    assert "elsewhere" not in said


def test_a_home_that_lost_its_store_is_not_sent_to_init(tmp_path, monkeypatch):
    # The store was made under one home, and a process that never saw the
    # setting naming it looks in another: the shape of a hook whose harness
    # did not inherit the variable a shell profile exports.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("QUIREWORK_PROVENANCE_HOME", str(tmp_path / "stores"))
    project = _named(tmp_path)
    keep_session(project, f"cc-sessions/{KETTLE}.jsonl", _transcript("Boil."))
    monkeypatch.delenv("QUIREWORK_PROVENANCE_HOME")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    with pytest.raises(ConfigurationError) as caught:
        store.open_store(project)
    said = str(caught.value)
    assert "the default" in said and "lists 1 accepted file(s)" in said
    assert "point $QUIREWORK_PROVENANCE_HOME or provenance.home" in said
    assert "creates an empty store here" in said
    assert not (tmp_path / "data").exists()


def test_a_missing_store_directory_names_the_setting_to_correct(tmp_path):
    original = _book(tmp_path)
    keep_session(original, f"cc-sessions/{KETTLE}.jsonl", _transcript("Boil."))
    moved = Project.from_settings(
        tmp_path / "book",
        {"provenance": corpus_settings(store=str(tmp_path / "nowhere"))},
    )
    with pytest.raises(ConfigurationError) as caught:
        store.open_store(moved)
    said = str(caught.value)
    assert "the directory provenance.store_dir names" in said
    assert "correct provenance.store_dir" in said


def test_init_of_a_store_the_ledger_already_filled_warns_of_a_wrong_home(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("QUIREWORK_PROVENANCE_HOME", str(tmp_path / "stores"))
    project = _named(tmp_path)
    keep_session(project, f"cc-sessions/{KETTLE}.jsonl", _transcript("Boil."))
    monkeypatch.delenv("QUIREWORK_PROVENANCE_HOME")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    with project_scope(project):
        capsys.readouterr()
        assert store.cmd_init(None) == 0
    said = capsys.readouterr().out
    assert "its home is" in said and "the default" in said
    assert "this one is in the wrong place" in said


def test_a_directory_that_is_not_a_store_is_never_taken_over(tmp_path):
    project = _book(tmp_path)
    occupied = tmp_path / "book" / "private" / "store"
    occupied.mkdir(parents=True)
    (occupied / "notes.txt").write_text("someone else's\n")
    with project_scope(project):
        with pytest.raises(ConfigurationError, match="not empty"):
            store.init()


# --------------------------------------------------------------------------- #
# accepting
# --------------------------------------------------------------------------- #
def test_a_capture_is_evidence_only_once_accepted(book):
    pending = keep_session(
        book, f"cc-sessions/{KETTLE}.jsonl", _transcript("Boil."), pending=True
    )
    assert store.files(store.CC) == []
    assert store.pending(store.CC) == [pending]

    done, skipped = store.accept([KETTLE[:8]], live=True)
    assert [(f, u) for f, u, _, _ in done] == [(store.CC, KETTLE)] and not skipped
    (accepted,) = store.files(store.CC)
    assert accepted.read_text() == _transcript("Boil.")
    assert store.pending(store.CC) == []
    (entry,) = store.open_store().entries()
    assert entry.sha == store.sha256_file(accepted)
    assert entry.reference == f"cc-sessions/{KETTLE}.jsonl"
    # Nothing but what the store holds: no lock file a repository would see.
    assert sorted(p.name for p in store.location().iterdir()) == [
        "accepted",
        "pending",
        "store.toml",
    ]


def test_an_accepted_version_cannot_be_written_through(book):
    path = keep_session(book, f"cc-sessions/{KETTLE}.jsonl", _transcript("Boil."))
    assert not os.access(path, os.W_OK)


def test_a_session_still_growing_is_refused_unless_asked(book):
    keep_session(
        book, f"cc-sessions/{KETTLE}.jsonl", _transcript("Boil."), pending=True
    )
    with pytest.raises(ValueError, match="may still be running"):
        store.accept([KETTLE])
    done, _ = store.accept([KETTLE], live=True)
    assert len(done) == 1


def test_accepting_everything_skips_the_sessions_still_running(book):
    old = time.time() - store.SETTLE_SECONDS - 60
    keep_session(
        book,
        f"cc-sessions/{KETTLE}.jsonl",
        _transcript("Boil."),
        pending=True,
        mtime=old,
    )
    keep_session(book, f"cc-sessions/{STORM}.jsonl", _transcript("Rain."), pending=True)
    done, skipped = store.accept(everything=True)
    assert [u for _, u, _, _ in done] == [KETTLE]
    assert skipped == [(store.CC, STORM)]
    assert [p.name for p in store.pending(store.CC)] == [f"{STORM}.jsonl"]


def test_an_ambiguous_prefix_is_refused_and_an_exact_id_is_not(book):
    keep_session(book, "cc-sessions/ab12.jsonl", _transcript("a"), pending=True)
    keep_session(book, "cc-sessions/ab12-two.jsonl", _transcript("b"), pending=True)
    with pytest.raises(ValueError, match="ambiguous"):
        store.accept(["ab1"], live=True)
    done, _ = store.accept(["ab12"], live=True)
    assert [u for _, u, _, _ in done] == ["ab12"]


def test_a_removed_session_is_not_accepted_back(book):
    keep_session(
        book, f"cc-sessions/{KETTLE}.jsonl", _transcript("Boil."), pending=True
    )
    store.add_to_list(store.REMOVED, "cc", KETTLE, "tooling only")
    with pytest.raises(ValueError, match="removed-sessions.txt"):
        store.accept([KETTLE], live=True)


# --------------------------------------------------------------------------- #
# versions
# --------------------------------------------------------------------------- #
def test_a_grown_session_becomes_a_new_version_beside_the_old(book):
    first = keep_session(book, f"cc-sessions/{KETTLE}.jsonl", _transcript("Boil."))
    before = (store.ledger() / store.MANIFEST).read_text()
    second = keep_session(
        book, f"cc-sessions/{KETTLE}.jsonl", _transcript("Boil.", "And pour.")
    )
    assert first != second and first.is_file() and second.is_file()
    assert first.read_text() == _transcript("Boil.")
    assert store.files(store.CC) == [second]

    # A branch whose ledger still lists the first version reads exactly it.
    (store.ledger() / store.MANIFEST).write_text(before)
    assert store.files(store.CC) == [first]
    assert store.verify() == []


def test_the_same_bytes_accepted_twice_make_one_version(book):
    first = keep_session(book, f"cc-sessions/{KETTLE}.jsonl", _transcript("Boil."))
    again = keep_session(book, f"cc-sessions/{KETTLE}.jsonl", _transcript("Boil."))
    assert first == again


def test_a_web_conversation_is_accepted_whole(book):
    raw = "conversations/c0ffee00-0000-4000-8000-000000000001.raw.json"
    keep_session(book, raw, '{"chat_messages": []}', pending=True)
    keep_session(
        book, raw.replace(".raw.json", ".md"), "# A rendered talk\n", pending=True
    )
    keep_session(
        book,
        "conversations/files/c0ffee00-0000-4000-8000-000000000001/notes.txt",
        "kept\n",
        pending=True,
    )
    done, _ = store.accept(["c0ffee00"])
    assert len(done) == 1
    (export,) = store.files(store.WEB, "*.raw.json")
    assert (export.parent / "c0ffee00-0000-4000-8000-000000000001.md").is_file()
    assert {p.name for p in store.files(store.WEB)} == {
        "c0ffee00-0000-4000-8000-000000000001.raw.json",
        "c0ffee00-0000-4000-8000-000000000001.md",
    }

    # A transcript rendered again later joins the export in a new version.
    keep_session(book, raw.replace(".raw.json", ".md"), "# Rendered anew\n")
    (export,) = store.files(store.WEB, "*.raw.json")
    sibling = export.parent / "c0ffee00-0000-4000-8000-000000000001.md"
    assert sibling.read_text() == "# Rendered anew\n"
    assert (export.parent / "files").is_dir()


# --------------------------------------------------------------------------- #
# rejecting
# --------------------------------------------------------------------------- #
def test_rejecting_a_pending_capture_deletes_it_and_lists_why(book):
    keep_session(book, f"cc-sessions/{KETTLE}.jsonl", _transcript("x"), pending=True)
    assert store.reject([KETTLE[:8]], "a stub") == [(store.CC, KETTLE)]
    assert store.pending(store.CC) == []
    assert store.listed(store.REMOVED) == [store.Listed("cc", KETTLE, "a stub")]


def test_rejecting_an_accepted_session_keeps_its_bytes_for_other_branches(book):
    path = keep_session(book, f"cc-sessions/{KETTLE}.jsonl", _transcript("x"))
    store.reject([KETTLE], "about the tooling")
    assert store.files(store.CC) == [] and path.is_file()
    assert store.elsewhere(store.open_store()) == {(store.CC, KETTLE)}


def test_a_rejection_needs_a_reason(book):
    keep_session(book, f"cc-sessions/{KETTLE}.jsonl", _transcript("x"), pending=True)
    with pytest.raises(ValueError, match="--reason"):
        store.reject([KETTLE], " ")


# --------------------------------------------------------------------------- #
# damage
# --------------------------------------------------------------------------- #
def test_an_altered_or_missing_file_is_reported(book):
    kettle = keep_session(book, f"cc-sessions/{KETTLE}.jsonl", _transcript("x"))
    storm = keep_session(book, f"cc-sessions/{STORM}.jsonl", _transcript("y"))
    kettle.chmod(0o600)
    kettle.write_text(_transcript("changed"))
    storm.unlink()
    problems = store.verify()
    assert len(problems) == 2
    assert problems[0].startswith("altered") and problems[1].startswith("missing")
    with pytest.raises(ConfigurationError, match="lacks 1 accepted file"):
        store.files(store.CC)


def test_a_damaged_list_is_refused_rather_than_read_around(book):
    path = store.ledger() / store.MANIFEST
    path.write_text(path.read_text() + "not a line the store writes\n")
    with pytest.raises(ConfigurationError, match="not '<sha256>"):
        store.files(store.CC)


# --------------------------------------------------------------------------- #
# names
# --------------------------------------------------------------------------- #
def test_a_stored_file_is_named_by_family_and_name_alone(book):
    pending = keep_session(
        book, f"cc-sessions/{KETTLE}.jsonl", _transcript("x"), pending=True
    )
    assert store.reference(pending) == f"cc-sessions/{KETTLE}.jsonl"
    assert store.area(pending) == "pending"
    store.accept([KETTLE], live=True)
    (accepted,) = store.files(store.CC)
    assert store.reference(accepted) == f"cc-sessions/{KETTLE}.jsonl"
    assert store.family_of(accepted) == store.CC
    assert store.area(accepted) == "accepted"
    inside = book.root / "notes" / "a.md"
    assert store.reference(inside) == "notes/a.md"


def test_a_project_without_a_store_holds_no_path(tmp_path):
    with project_scope(Project.from_settings(tmp_path, {})):
        assert store.family_of(tmp_path / "cc-sessions" / "x.jsonl") is None


def test_a_list_line_names_its_kind_or_is_reported(book, capsys):
    (store.ledger() / store.MAKING).write_text(
        f"cc {KETTLE}  # the ingest sweep\n{STORM}.jsonl  # the old form\n"
    )
    assert store.listed(store.MAKING) == [
        store.Listed("cc", KETTLE, "the ingest sweep")
    ]
    assert "not '<kind> <session id>'" in capsys.readouterr().err
    assert store.add_to_list(store.MAKING, "cc", KETTLE, "again") is False


# --------------------------------------------------------------------------- #
# capture
# --------------------------------------------------------------------------- #
def _claude_home(tmp_path, project, sid, *said):
    """A Claude Code transcript of this project, where Claude Code keeps it."""
    encoded = str(project.root.resolve()).replace("/", "-").replace(".", "-")
    folder = tmp_path / "claude" / encoded
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    path.write_text(
        "".join(
            json.dumps(
                {
                    "type": "user",
                    "cwd": str(project.root),
                    "timestamp": "2031-04-02T10:00:00Z",
                    "message": {"content": text},
                }
            )
            + "\n"
            for text in said
        )
    )
    old = time.time() - store.SETTLE_SECONDS - 60
    os.utime(path, (old, old))
    return path


def test_capture_waits_in_the_store_and_returns_only_when_a_session_grows(tmp_path):
    from quirework.provenance.ingestion import claude

    settings = corpus_settings()
    project = Project.from_settings(
        tmp_path / "book",
        {
            "provenance": settings,
            "providers": {"claude": {"sessions": str(tmp_path / "claude")}},
        },
    )
    (tmp_path / "book").mkdir()
    source = _claude_home(tmp_path, project, KETTLE, "Boil the water.")
    with project_scope(project):
        store.init()
        claude.main([])
        assert [p.name for p in store.pending(store.CC)] == [f"{KETTLE}.jsonl"]
        store.accept(everything=True)
        claude.main([])
        assert store.pending(store.CC) == []

        _claude_home(tmp_path, project, KETTLE, "Boil the water.", "Pour it.")
        assert source.is_file()
        claude.main([])
        assert [p.name for p in store.pending(store.CC)] == [f"{KETTLE}.jsonl"]
        assert len(store.files(store.CC)) == 1


# --------------------------------------------------------------------------- #
# what status sees that capture missed
# --------------------------------------------------------------------------- #
def _claude_book(tmp_path):
    """A book whose Claude Code transcripts are kept in the test's directory."""
    project = Project.from_settings(
        tmp_path / "book",
        {
            "provenance": corpus_settings(),
            "providers": {"claude": {"sessions": str(tmp_path / "claude")}},
        },
    )
    (tmp_path / "book").mkdir()
    return project


def _status(capsys):
    capsys.readouterr()
    assert store.cmd_status(None) == 0
    return capsys.readouterr().out


def test_status_names_a_session_no_capture_holds(tmp_path, capsys):
    from quirework.provenance.ingestion import claude

    project = _claude_book(tmp_path)
    _claude_home(tmp_path, project, KETTLE, "Boil the water.")
    with project_scope(project):
        store.init()
        out = _status(capsys)
        assert "uncaptured 1 Claude Code session(s)" in out
        assert f"{KETTLE}  never captured" in out
        assert "missed by its capture hook" in out

        claude.main([])
        out = _status(capsys)
        assert "uncaptured 0 Claude Code session(s)" in out
        assert "missed" not in out


def test_status_only_reads(tmp_path, capsys):
    project = _claude_book(tmp_path)
    _claude_home(tmp_path, project, KETTLE, "Boil the water.")
    with project_scope(project):
        store.init()
        ledger = sorted(p.name for p in store.ledger().iterdir())
        _status(capsys)
        assert store.pending(store.CC) == []
        assert sorted(p.name for p in store.ledger().iterdir()) == ledger


def test_a_session_still_being_written_is_not_called_missed(tmp_path, capsys):
    project = _claude_book(tmp_path)
    source = _claude_home(tmp_path, project, KETTLE, "Boil the water.")
    os.utime(source)
    with project_scope(project):
        store.init()
        out = _status(capsys)
        line = next(x for x in out.splitlines() if KETTLE in x)
        assert line.endswith("may still be running")
        assert "missed" not in out


def test_a_session_that_grew_after_acceptance_is_named_grown(tmp_path, capsys):
    from quirework.provenance.ingestion import claude

    project = _claude_book(tmp_path)
    _claude_home(tmp_path, project, KETTLE, "Boil the water.")
    with project_scope(project):
        store.init()
        claude.main([])
        store.accept(everything=True)
        _claude_home(tmp_path, project, KETTLE, "Boil the water.", "Pour it.")
        assert f"{KETTLE}  grown since its capture" in _status(capsys)


def test_a_removed_session_is_not_reported_uncaptured(tmp_path, capsys):
    project = _claude_book(tmp_path)
    _claude_home(tmp_path, project, KETTLE, "Boil the water.")
    with project_scope(project):
        store.init()
        store.add_to_list(store.REMOVED, "cc", KETTLE, "a stub")
        assert "uncaptured 0 Claude Code session(s)" in _status(capsys)


def test_status_is_silent_about_claude_code_where_it_never_ran(tmp_path, capsys):
    project = _claude_book(tmp_path)
    with project_scope(project):
        store.init()
        assert "uncaptured" not in _status(capsys)
