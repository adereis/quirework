"""Every setting the code reads is named in the configuration guide.

A setting Quirework adds defaults to describing nothing, so a project that
never declares it sees no files, no error and a passing check. The guide is
then the only place a project can learn the setting exists, and a setting it
leaves out cannot be discovered at all.
"""

import ast
from pathlib import Path
import re

import quirework
from quirework.project import SECTIONS

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "configuration.md"

# The Project accessors that take a dotted key, with the argument holding it.
ACCESSORS = {"get": 0, "path": 0, "data": 0, "path_at": 1}

DOTTED = re.compile(r"[a-z_]+(\.[a-z_]+)+")


def settings_read():
    """``{key: [where]}`` for each key passed to an accessor, read statically.

    A key counts when the call spells it as a string literal or names a
    string constant assigned at the top of the same module. A key built at
    run time, such as an f-string over a provider's name, or a constant
    imported from another module, is invisible here; the guide still owes it
    a sentence, and nothing but review asks for one.
    """
    root = Path(quirework.__file__).parent
    found = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = {
            node.targets[0].id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ACCESSORS
                and len(node.args) > ACCESSORS[node.func.attr]
            ):
                continue
            argument = node.args[ACCESSORS[node.func.attr]]
            if isinstance(argument, ast.Constant):
                key = argument.value
            elif isinstance(argument, ast.Name):
                key = constants.get(argument.id)
            else:
                continue
            if (
                isinstance(key, str)
                and DOTTED.fullmatch(key)
                and key.split(".")[0] in SECTIONS
            ):
                where = f"{path.relative_to(root)}:{node.lineno}"
                found.setdefault(key, []).append(where)
    return found


def named(key, text):
    """Whether ``text`` names ``key`` whole, or as ``leaf =`` under its table."""
    if re.search(rf"(?<![\w.]){re.escape(key)}(?!\w)", text):
        return True
    table, leaf = key.rsplit(".", 1)
    blocks = re.findall(
        rf"^\[{re.escape(table)}\]\n(.*?)(?=^\[|^```)", text, re.M | re.S
    )
    return any(re.search(rf"^#?\s*{re.escape(leaf)}\s*=", b, re.M) for b in blocks)


def test_the_scan_finds_the_settings_it_exists_to_find():
    """A scan that matched nothing would pass every guide, so pin that it
    sees a literal, a module constant and a key in the second argument."""
    found = settings_read()
    assert "manuscript.source" in found
    assert "text.tells_policy" in found  # only through a module constant
    assert "sops.instruments" in found  # only as path_at's second argument


def test_every_setting_the_code_reads_is_in_the_guide():
    text = GUIDE.read_text(encoding="utf-8")
    missing = {
        key: where for key, where in settings_read().items() if not named(key, text)
    }
    assert not missing, (
        "settings the code reads but docs/configuration.md never names; say "
        "what each does, its default and when a project needs it:\n"
        + "\n".join(
            f"  {key}  ({', '.join(sorted(set(where)))})"
            for key, where in missing.items()
        )
    )
