"""The procedure catalog: its reading graph, its metadata and its index.

Required reading is a graph the catalog closes over; a follow-up is a
condition the operator weighs, never something read or run along the way.
"""

import json

import pytest

from quirework.project import Project, project_scope
from quirework.sops import catalog


def entry(*requires, kind="procedure", follow=()):
    return {
        "family": "tea",
        "kind": kind,
        "skill": kind != "protocol",
        "requires": list(requires),
        "mechanics": ["a timer"],
        "followups": [{"sop": name, "when": "If the pot is cold."} for name in follow],
        "note": "",
    }


def write_catalog(root, data):
    (root / "procedures" / "index.json").write_text(json.dumps(data))


@pytest.fixture
def pantry(tmp_path):
    """Two procedures share one protocol; a third needs both, and it and a
    fourth name each other only as follow-ups."""
    directory = tmp_path / "procedures"
    directory.mkdir()
    data = {
        "version": 1,
        "families": [{"id": "tea", "label": "Tea", "question": "What does it need?"}],
        "entries": {
            "kettle-rules": entry(kind="protocol"),
            "fill": entry("kettle-rules"),
            "boil": entry("kettle-rules"),
            "brew": entry("fill", "boil", follow=["steep"]),
            "steep": entry(follow=["brew"]),
        },
    }
    for name, item in data["entries"].items():
        heading = "# SOP fragment — " if item["kind"] == "protocol" else "# "
        (directory / f"{name}.md").write_text(
            f"{heading}{name.title()}\n\n<!-- @skill: Do the {name}. -->\n"
        )
    # A directory card is navigation, not a procedure the catalog must list.
    (directory / "README.md").write_text("# What lives here\n")
    write_catalog(tmp_path, data)
    project = Project.from_settings(
        tmp_path, {"sops": {"root": "procedures", "catalog": "procedures/index.json"}}
    )
    with project_scope(project):
        yield tmp_path, data


def test_a_shared_reading_is_read_once_and_follow_ups_are_not_read(pantry):
    report = catalog.explain(catalog.load(), "brew")
    assert [r["sop"] for r in report["reading_order"]] == [
        "kettle-rules",
        "fill",
        "boil",
        "brew",
    ]
    assert [edge["sop"] for edge in report["followups"]] == ["steep"]
    assert report["reading_words"] == sum(r["words"] for r in report["reading_order"])


def test_a_follow_up_cycle_is_allowed_but_a_reading_cycle_is_refused(pantry):
    """Brewing may call for steeping and steeping for brewing again: that is
    a judgement made each time. A procedure that must be read before itself
    can never be read at all."""
    root, data = pantry
    catalog.load()
    data["entries"]["kettle-rules"]["requires"] = ["brew"]
    write_catalog(root, data)
    with pytest.raises(ValueError, match="reading dependency cycle"):
        catalog.load()


@pytest.mark.parametrize("relation", ["requires", "followups"])
def test_an_unknown_reading_or_follow_up_target_is_refused(pantry, relation):
    root, data = pantry
    data["entries"]["brew"][relation] = (
        ["descale"]
        if relation == "requires"
        else [{"sop": "descale", "when": "If there is scale."}]
    )
    write_catalog(root, data)
    with pytest.raises(ValueError, match="unknown.*descale"):
        catalog.load()


def test_a_procedure_file_without_a_catalog_entry_is_refused(pantry):
    root, _ = pantry
    (root / "procedures" / "descale.md").write_text("# Descale\n")
    with pytest.raises(ValueError, match="uncataloged=.*descale"):
        catalog.load()


def test_a_catalog_entry_whose_file_is_gone_is_refused(pantry):
    root, _ = pantry
    (root / "procedures" / "fill.md").unlink()
    with pytest.raises(ValueError, match="missing files=.*fill"):
        catalog.load()


@pytest.mark.parametrize(
    "mutation, error",
    [
        # JSON `true` compares equal to 1 in Python, and is still no version.
        (lambda d: d.update(version=True), "version"),
        (lambda d: d["entries"]["brew"].update(requires="fill"), "list of strings"),
        (lambda d: d["entries"]["brew"].update(family="coffee"), "unknown family"),
        (
            lambda d: d["entries"]["kettle-rules"].update(kind="procedure"),
            "fragment heading",
        ),
        (lambda d: d["entries"]["brew"].update(colour="amber"), "expected fields"),
        (
            lambda d: d["entries"]["brew"].update(
                followups=[{"sop": "steep", "when": ""}]
            ),
            "condition: expected text",
        ),
    ],
    ids=[
        "boolean-version",
        "requires-not-a-list",
        "unknown-family",
        "protocol-kind-on-a-procedure-heading",
        "unexpected-field",
        "follow-up-without-a-condition",
    ],
)
def test_malformed_metadata_is_refused(pantry, mutation, error):
    root, data = pantry
    mutation(data)
    write_catalog(root, data)
    with pytest.raises(ValueError, match=error):
        catalog.load()


def test_a_repeated_key_in_the_catalog_is_refused(pantry):
    """A JSON reader keeps the last of two equal keys, so the first would
    vanish without a word."""
    root, _ = pantry
    (root / "procedures" / "index.json").write_text('{"version": 1, "version": 1}')
    with pytest.raises(ValueError, match="duplicate catalog key: version"):
        catalog.load()


def test_the_index_check_names_a_stale_index_and_writes_nothing(pantry, capsys):
    root, _ = pantry
    assert catalog.main(["catalog", "--write"]) == 0
    index = root / "procedures" / "catalog" / "README.md"
    before = index.read_bytes()
    assert catalog.main(["catalog", "--check"]) == 0
    assert index.read_bytes() == before
    source = root / "procedures" / "brew.md"
    source.write_text(source.read_text().replace("Do the brew.", "Warm the pot."))
    capsys.readouterr()
    assert catalog.main(["catalog", "--check"]) == 2
    assert index.read_bytes() == before
    assert "stale" in capsys.readouterr().err
    assert catalog.main(["catalog", "--write"]) == 0
    assert "Warm the pot." in index.read_text()


@pytest.mark.parametrize("name", ["descale", "../README"])
@pytest.mark.usefixtures("pantry")
def test_explaining_an_unknown_or_escaping_name_is_refused(name):
    with pytest.raises(ValueError, match="unknown SOP"):
        catalog.explain(catalog.load(), name)
