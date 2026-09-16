"""Keep reusable execution independent of book workflows and consumers."""

import ast
from pathlib import Path
import quirework


def test_execution_and_providers_do_not_import_book_workflows():
    root = Path(quirework.__file__).parent
    forbidden = (
        "quirework.book",
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
