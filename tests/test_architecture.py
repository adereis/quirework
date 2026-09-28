"""Keep reusable execution independent of writing workflows and consumers."""

import ast
from pathlib import Path
import quirework


def test_execution_and_providers_do_not_import_writing_workflows():
    root = Path(quirework.__file__).parent
    forbidden = (
        "quirework.manuscript",
        "quirework.publish",
        "quirework.sops.workshop",
        "quirework.sops.review",
        "book_project",
    )
    for directory in ("runs", "providers"):
        for path in (root / directory).glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else []
                )
                assert not any(name.startswith(forbidden) for name in names), path


def test_package_has_no_static_dependency_on_a_consumer():
    root = Path(quirework.__file__).parent
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("book_project"), path


def test_every_subcommand_says_what_it_does():
    """argparse lists a subcommand's purpose only when it was given `help`.

    Without it the verb still works and still appears in the terse
    `{prepare,start,...}` line, which is why the omission survives: it is
    invisible to the author and total to a reader who only has `--help`.
    """
    root = Path(quirework.__file__).parent
    bare = [
        f"{path.relative_to(root)}:{node.lineno}"
        for path in sorted(root.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", "") == "add_parser"
        and not any(k.arg == "help" for k in node.keywords)
    ]
    assert bare == []
