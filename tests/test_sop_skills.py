"""Generated skill routes: what they say, when they drift, who owns them.

A route is a thin front door the generator writes from a procedure. The
generator owns the routes it marked and one switch in each portable route's
metadata; a route somebody wrote by hand, and every other line of metadata,
stays theirs.
"""

import json

import pytest

from quirework.project import Project, project_scope
from quirework.sops import commands

SETTINGS = {"sops": {"root": "procedures", "catalog": "procedures/index.json"}}

# name: (kind, skill, text). One procedure declines a route, and a protocol
# can never have one, so two of the four get none.
PROCEDURES = {
    "brew": ("procedure", True, "# Brew a pot\n\n<!-- @skill: Brew one pot. -->\n"),
    "taste": ("procedure", True, "# Taste\n\n<!-- @skill: Taste a cup. -->\n"),
    "descale": ("procedure", False, "# Descale\n\n<!-- @skill: Remove scale. -->\n"),
    "kettle-rules": ("protocol", False, "# SOP fragment — kettle\n\nNever dry.\n"),
}

IMPLICIT = "<!-- @skill-invocation: implicit -->\n"
EXPLICIT_ONLY = "only when the human explicitly requests"


def write_catalog(root, decisions):
    entries = {
        name: {
            "family": "tea",
            "kind": kind,
            "skill": skill,
            "requires": [],
            "mechanics": [],
            "followups": [],
            "note": "",
        }
        for name, (kind, skill) in decisions.items()
    }
    (root / "procedures" / "index.json").write_text(
        json.dumps(
            {
                "version": 1,
                "families": [{"id": "tea", "label": "Tea", "question": "What?"}],
                "entries": entries,
            }
        )
    )


@pytest.fixture
def kitchen(tmp_path):
    (tmp_path / "procedures").mkdir()
    for name, (_, _, text) in PROCEDURES.items():
        (tmp_path / "procedures" / f"{name}.md").write_text(text)
    write_catalog(tmp_path, {n: (k, s) for n, (k, s, _) in PROCEDURES.items()})
    with project_scope(Project.from_settings(tmp_path, SETTINGS)):
        yield tmp_path


def skills(*argv):
    return commands.main(["skills", *argv])


def words(path):
    """A route is wrapped, so where its lines break says nothing."""
    return " ".join(path.read_text().split())


def tree(directory):
    return {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def test_a_fragment_heading_marks_shared_reading():
    assert commands.is_fragment("# SOP fragment — kettle rules\n")
    assert not commands.is_fragment("# SOP — brew a pot\n")
    assert not commands.is_fragment("Brew one pot of tea.\n")


def test_the_skill_marker_is_the_description_even_beside_a_goal_line():
    text = "# Brew\n\n<!-- @skill: Brew one pot of tea. -->\nGoal: something else.\n"
    assert commands._description(text, "brew") == "Brew one pot of tea."


def test_without_a_marker_the_goal_line_is_the_description():
    text = "# Brew\n\nGoal: brew one pot of tea for the table.\nWarm the pot.\n"
    assert commands._description(text, "brew") == "brew one pot of tea for the table."


def test_a_hand_edit_to_a_generated_route_is_drift(kitchen):
    routes = kitchen / ".claude" / "skills"
    assert skills() == 0
    assert sorted(p.name for p in routes.iterdir()) == ["qw-brew", "qw-taste"]
    assert skills("--check") == 0
    route = routes / "qw-brew" / "SKILL.md"
    route.write_text(route.read_text() + "\nA stray line.\n")
    assert skills("--check") == 1
    assert skills() == 0
    assert skills("--check") == 0


def test_a_route_whose_procedure_is_gone_is_drift_and_is_removed(kitchen, capsys):
    routes = kitchen / ".claude" / "skills"
    assert skills() == 0
    (kitchen / "procedures" / "taste.md").unlink()
    write_catalog(
        kitchen, {n: (k, s) for n, (k, s, _) in PROCEDURES.items() if n != "taste"}
    )
    capsys.readouterr()
    assert skills("--check") == 1
    assert "orphan   qw-taste" in capsys.readouterr().err
    assert (routes / "qw-taste").exists()
    assert skills() == 0
    assert not (routes / "qw-taste").exists()


def test_a_handwritten_route_with_the_prefix_is_never_removed(kitchen):
    """Only a route carrying the generator's mark is the generator's."""
    assert skills() == 0
    hand = kitchen / ".claude" / "skills" / "qw-handwritten" / "SKILL.md"
    hand.parent.mkdir()
    body = "---\nname: qw-handwritten\ndescription: mine\n---\nPour it slowly.\n"
    hand.write_text(body)
    assert skills("--check") == 0
    assert skills() == 0
    assert hand.read_text() == body


def test_a_portable_policy_edited_by_hand_is_drift_and_the_check_writes_nothing(
    kitchen,
):
    agents = kitchen / ".agents" / "skills"
    assert skills("--target", "agents") == 0
    route = agents / "qw-brew" / "SKILL.md"
    policy = agents / "qw-brew" / "agents" / "openai.yaml"
    # Codex reads the switch from the metadata, not the route.
    assert "disable-model-invocation" not in route.read_text()
    assert policy.read_text() == "policy:\n  allow_implicit_invocation: false\n"
    before = route.read_bytes()
    policy.write_text("policy:\n  allow_implicit_invocation: true\n")
    assert skills("--target", "agents", "--check") == 1
    assert policy.read_text() == "policy:\n  allow_implicit_invocation: true\n"
    assert route.read_bytes() == before
    assert skills("--target", "agents") == 0
    assert skills("--target", "agents", "--check") == 0
    policy.unlink()
    assert skills("--target", "agents", "--check") == 1
    assert not policy.exists()


def test_a_handwritten_portable_route_keeps_its_body_and_other_metadata(kitchen):
    """The generator holds a handwritten route's switch at explicit-only, and
    every other line of its metadata, comments included, stays the author's."""
    route = kitchen / ".agents" / "skills" / "qw-handwritten" / "SKILL.md"
    route.parent.mkdir(parents=True)
    body = "---\nname: qw-handwritten\ndescription: mine\n---\nPour it slowly.\n"
    route.write_text(body)
    policy = route.parent / "agents" / "openai.yaml"
    policy.parent.mkdir()
    metadata = (
        'interface:\n  display_name: "Kettle"\n'
        "policy: # set by the author\n"
        "  allow_implicit_invocation: true # a costly route\n"
        '  pour_speed: "slow"\n'
        'dependencies:\n  tools:\n    - type: "mcp"\n      value: "kettle"\n'
    )
    policy.write_text(metadata)
    assert skills("--target", "agents") == 0
    assert route.read_text() == body
    assert policy.read_text() == metadata.replace("true # a costly", "false # a costly")
    assert skills("--target", "agents", "--check") == 0


@pytest.mark.parametrize(
    "metadata",
    [
        "",
        'interface:\n  display_name: "Kettle"',
        "policy:",
        'policy: # kettle settings\n    pour_speed: "slow"\n',
        'interface:\n  display_name: "Kettle"\npolicy:\n# a closing comment\n',
    ],
    ids=[
        "empty",
        "no-policy-no-newline",
        "empty-policy",
        "policy-with-a-wider-indent",
        "policy-holding-a-comment",
    ],
)
@pytest.mark.parametrize("allow_implicit", [False, True])
def test_setting_the_invocation_switch_keeps_the_rest_of_the_metadata(
    metadata, allow_implicit
):
    result = commands._invocation_policy(metadata, allow_implicit=allow_implicit)
    value = "true" if allow_implicit else "false"
    assert f"allow_implicit_invocation: {value}" in result
    for line in metadata.splitlines():
        assert line in result
    assert commands._invocation_policy(result, allow_implicit=allow_implicit) == result


@pytest.mark.parametrize(
    "metadata",
    [
        "policy: {allow_implicit_invocation: true}\n",
        "policy: &defaults\n  allow_implicit_invocation: true\n",
        "policy:\n  allow_implicit_invocation: false\npolicy:\n",
        (
            "policy:\n  allow_implicit_invocation: false\n"
            "  allow_implicit_invocation: true\n"
        ),
        'policy:\n  allow_implicit_invocation: "false"\n',
        '"policy":\n  allow_implicit_invocation: true\n',
        "policy:\n  <<: *defaults\n",
    ],
    ids=[
        "flow-mapping",
        "anchor",
        "two-policies",
        "two-switches",
        "quoted-value",
        "quoted-key",
        "merge-key",
    ],
)
def test_an_ambiguous_policy_stops_generation_before_any_write(kitchen, metadata):
    """Rewriting a shape the generator cannot read for certain could replace
    the author's metadata, so it names the file and writes nothing at all."""
    agents = kitchen / ".agents" / "skills"
    policy = agents / "qw-brew" / "agents" / "openai.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(metadata)
    assert skills("--target", "agents") == 1
    assert policy.read_text() == metadata
    assert not list(agents.glob("*/SKILL.md"))


def test_an_implicit_marker_opens_its_route_in_both_hosts(kitchen):
    """Claude reads the switch from a route's front matter and Codex from its
    metadata, so one marker in the procedure reaches both, for its own route
    and no other."""
    source = kitchen / "procedures" / "taste.md"
    source.write_text(source.read_text() + IMPLICIT)
    assert commands._implicit_skills() == {"qw-taste"}
    assert skills("--target", "both") == 0
    for name, implicit in (("qw-taste", True), ("qw-brew", False)):
        claude = words(kitchen / ".claude" / "skills" / name / "SKILL.md")
        policy = kitchen / ".agents" / "skills" / name / "agents" / "openai.yaml"
        value = "true" if implicit else "false"
        assert f"disable-model-invocation: {'false' if implicit else 'true'}" in claude
        assert (EXPLICIT_ONLY in claude) is not implicit
        assert policy.read_text() == f"policy:\n  allow_implicit_invocation: {value}\n"


@pytest.mark.parametrize("target", ["claude", "agents"])
def test_dropping_an_implicit_marker_is_drift_until_regenerated(kitchen, target):
    routes = kitchen / (".claude" if target == "claude" else ".agents") / "skills"
    source = kitchen / "procedures" / "taste.md"
    plain = source.read_text()
    source.write_text(plain + IMPLICIT)
    assert skills("--target", target) == 0
    route = routes / "qw-taste" / "SKILL.md"
    policy = routes / "qw-taste" / "agents" / "openai.yaml"
    metadata = (
        'interface:\n  display_name: "Taste"\n'
        "policy: # invocation\n"
        "  allow_implicit_invocation: true # keep this comment\n"
        '  pour_speed: "slow"\n'
    )
    if target == "agents":
        policy.write_text(metadata)
    source.write_text(plain)
    before = tree(routes)
    assert skills("--target", target, "--check") == 1
    assert tree(routes) == before
    assert skills("--target", target) == 0
    assert EXPLICIT_ONLY in words(route)
    if target == "agents":
        assert policy.read_text() == metadata.replace("true #", "false #")
    else:
        assert "disable-model-invocation: true" in words(route)
    source.write_text(plain + IMPLICIT)
    assert skills("--target", target) == 0
    assert skills("--target", target, "--check") == 0
    if target == "agents":
        assert policy.read_text() == metadata
    else:
        assert "disable-model-invocation: false" in words(route)


@pytest.mark.parametrize(
    "kind, heading, markers, error",
    [
        (
            "procedure",
            "# Pour",
            "<!-- @skill-invocation: automatic -->",
            "expected one @skill-invocation",
        ),
        ("procedure", "# Pour", "<!-- @skill-invocation -->", "expected one"),
        (
            "procedure",
            "# Pour",
            IMPLICIT + "<!-- @skill-invocation: explicit -->",
            "expected one",
        ),
        ("protocol", "# SOP fragment — pour", IMPLICIT, "fragments cannot declare"),
    ],
    ids=["unknown-value", "no-value", "two-markers", "on-a-fragment"],
)
def test_a_malformed_invocation_marker_stops_generation_before_any_write(
    kitchen, capsys, kind, heading, markers, error
):
    (kitchen / "procedures" / "pour.md").write_text(f"{heading}\n\n{markers}\n")
    decisions = {n: (k, s) for n, (k, s, _) in PROCEDURES.items()}
    write_catalog(kitchen, {**decisions, "pour": (kind, kind == "procedure")})
    capsys.readouterr()
    assert skills("--target", "both") == 1
    assert error in capsys.readouterr().err
    assert not (kitchen / ".claude").exists()
    assert not (kitchen / ".agents").exists()


def test_an_invocation_marker_on_a_procedure_without_a_route_is_refused(
    kitchen, capsys
):
    """A marker on a procedure the catalog gives no route reads like a live
    route and opens nothing, so generation stops and says which."""
    source = kitchen / "procedures" / "descale.md"
    source.write_text(source.read_text() + IMPLICIT)
    capsys.readouterr()
    assert skills() == 1
    assert "descale: declares skill invocation but the catalog gives it no skill" in (
        capsys.readouterr().err
    )
    assert not (kitchen / ".claude").exists()


def test_a_handwritten_route_named_like_a_generated_one_is_refused(kitchen, capsys):
    """The generator never writes over a route it did not mark, even one that
    has the name it would give its own."""
    hand = kitchen / ".claude" / "skills" / "qw-brew" / "SKILL.md"
    hand.parent.mkdir(parents=True)
    body = "---\nname: qw-brew\ndescription: mine\n---\nBrew it my way.\n"
    hand.write_text(body)
    capsys.readouterr()
    assert skills() == 1
    assert "unmanaged skill conflicts with generated route" in capsys.readouterr().err
    assert hand.read_text() == body
