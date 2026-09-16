"""Dependency-aware discovery over existing SOPs; never executes a workflow.

Catalog metadata describes required reading separately from conditional
follow-ups. The SOP files remain the procedure and description owners.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from quirework.project import current_project

_NAME = re.compile(r"[a-z][a-z0-9-]*")
KINDS = {"procedure", "protocol", "gate"}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate catalog key: {key}")
        result[key] = value
    return result


def _keys(value, expected, where):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"{where}: expected fields {', '.join(sorted(expected))}")


def _text(value, where, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"{where}: expected {'possibly empty ' if empty else ''}text")


def _strings(value, where):
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise ValueError(f"{where}: expected a list of strings")
    if any(not x.strip() for x in value) or len(set(value)) != len(value):
        raise ValueError(f"{where}: empty or duplicate item")


def reading_order(entries, name):
    """Dependency-first closure; follow-up edges are deliberately not traversed."""
    if name not in entries:
        raise ValueError(f"unknown SOP: {name}")
    result, done, active = [], set(), []

    def visit(current):
        if current in active:
            raise ValueError(
                "reading dependency cycle: " + " -> ".join(active + [current])
            )
        if current in done:
            return
        active.append(current)
        for dependency in entries[current]["requires"]:
            if dependency not in entries:
                raise ValueError(f"{current}: unknown reading dependency {dependency}")
            visit(dependency)
        active.pop()
        done.add(current)
        result.append(current)

    visit(name)
    return result


def _sops_root(root):
    """The project's own procedure directory; its name is a project choice."""
    return current_project().path_at(root, "sops.root", "sops")


def load(root=None):
    """Validate metadata, complete SOP coverage and the required-reading DAG."""
    if root is None:
        root = current_project().root
    root = Path(root)
    catalog = current_project().path_at(root, "sops.catalog", "sops/catalog.json")
    if not catalog.is_file():
        # The first thing a new project asks the SOP layer, and an errno tells
        # it nothing. Name the file and where its shape is written down.
        raise ValueError(
            f"no procedure catalog at {catalog}. A project declares its "
            "procedures in one; docs/authoring.md has the shape."
        )
    data = json.loads(catalog.read_text(), object_pairs_hook=_unique_object)
    _keys(data, {"version", "families", "entries"}, "catalog")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("unsupported SOP catalog version")
    if not isinstance(data["families"], list) or not data["families"]:
        raise ValueError("families must be a nonempty list")
    families = set()
    for item in data["families"]:
        _keys(item, {"id", "label", "question"}, "family")
        for key in item:
            _text(item[key], f"family {key}")
        if not _NAME.fullmatch(item["id"]) or item["id"] in families:
            raise ValueError(f"invalid or duplicate family: {item['id']}")
        families.add(item["id"])
    entries = data["entries"]
    if not isinstance(entries, dict) or not entries:
        raise ValueError("entries must be a nonempty object")
    files = [
        p
        for directory in (
            _sops_root(root),
            _sops_root(root) / "workflows",
            _sops_root(root) / "protocols",
        )
        for p in directory.glob("*.md")
        if p.name != "README.md"
    ]
    if len({p.stem for p in files}) != len(files):
        raise ValueError("duplicate SOP identifier across layers")
    paths = {p.stem: p for p in files}
    expected = set(paths)
    if set(entries) != expected:
        raise ValueError(
            f"SOP coverage mismatch: uncataloged={sorted(expected - set(entries))}; "
            f"missing files={sorted(set(entries) - expected)}"
        )
    from quirework.sops.commands import is_fragment

    for name, entry in entries.items():
        if not _NAME.fullmatch(name) or name in {
            "list",
            "explain",
            "catalog",
            "skills",
        }:
            raise ValueError(f"invalid SOP identifier: {name}")
        _keys(
            entry,
            {"family", "kind", "requires", "mechanics", "followups", "note"},
            name,
        )
        _text(entry["family"], f"{name} family")
        _text(entry["kind"], f"{name} kind")
        if entry["family"] not in families or entry["kind"] not in KINDS:
            raise ValueError(f"{name}: unknown family or kind")
        text = paths[name].read_text()
        if is_fragment(text) != (entry["kind"] == "protocol"):
            raise ValueError(
                f"{name}: protocol kind disagrees with SOP fragment heading"
            )
        _text(entry["note"], f"{name} note", empty=True)
        _strings(entry["requires"], f"{name} requires")
        _strings(entry["mechanics"], f"{name} mechanics")
        if not isinstance(entry["followups"], list):
            raise ValueError(f"{name}: followups must be a list")
        targets = set()
        for edge in entry["followups"]:
            _keys(edge, {"sop", "when"}, f"{name} followup")
            _text(edge["sop"], f"{name} followup SOP")
            _text(edge["when"], f"{name} followup condition")
            if edge["sop"] not in entries or edge["sop"] in targets:
                raise ValueError(f"{name}: unknown or duplicate followup {edge['sop']}")
            targets.add(edge["sop"])
    for name in entries:
        reading_order(entries, name)
    return data


def path_for(root, name):
    matches = [
        _sops_root(root) / folder / f"{name}.md"
        for folder in ("", "workflows", "protocols")
    ]
    found = [p for p in matches if p.is_file()]
    if len(found) != 1:
        raise ValueError(f"unknown or ambiguous SOP: {name}")
    return found[0]


def describe(root, name):
    from quirework.sops.commands import _description

    return _description(path_for(root, name).read_text(), name)


def explain(data, name, root=None):
    if root is None:
        root = current_project().root
    entries = data["entries"]
    order = reading_order(entries, name)
    reads = []
    for item in order:
        path = path_for(root, item)
        text = path.read_text()
        reads.append(
            {
                "sop": item,
                "path": str(path.relative_to(root)),
                "lines": len(text.splitlines()),
                "words": len(text.split()),
            }
        )
    return {
        "sop": name,
        **entries[name],
        "description": describe(root, name),
        "reading_order": reads,
        "reading_words": sum(r["words"] for r in reads),
        "scope": "SOP reading dependencies only; root guidance, directory cards "
        "and task-specific inputs still apply. No follow-up is executed.",
    }


def listing(data, root=None):
    if root is None:
        root = current_project().root
    lines = ["SOPs by task (existing names; no workflow is launched):"]
    for family in data["families"]:
        lines.extend(["", f"{family['label']} — {family['question']}"])
        for name, entry in sorted(data["entries"].items()):
            if entry["family"] == family["id"]:
                suffix = f" [{entry['kind']}]" if entry["kind"] != "procedure" else ""
                lines.append(f"  {name}{suffix}: {describe(root, name)}")
    lines.extend(
        [
            "",
            "Use sop NAME to read a procedure; sop explain NAME to see its dependencies.",
        ]
    )
    return "\n".join(lines) + "\n"


def markdown(data, root=None):
    if root is None:
        root = current_project().root
    lines = [
        "# SOP discovery catalog",
        "",
        "Generated from `sops/catalog.json` and the SOP descriptions. These are",
        "the task families, specialized procedures and shared protocols. Read the",
        "selected workflow and instrument before use.",
        "",
        "Required reading is distinct from running another workflow. Conditional",
        "follow-ups do not authorize or automatically launch work. Root guidance,",
        "directory cards and task-specific inputs still apply.",
        "",
    ]
    for family in data["families"]:
        lines += [
            f"## {family['label']}",
            "",
            family["question"],
            "",
            "| Procedure | Purpose | Required SOP reading |",
            "|---|---|---|",
        ]
        for name, entry in sorted(data["entries"].items()):
            if entry["family"] != family["id"]:
                continue
            deps = (
                ", ".join(
                    f"[{d}](../{path_for(root, d).relative_to(_sops_root(root))})"
                    for d in entry["requires"]
                )
                or "None"
            )
            desc = describe(root, name).replace("|", r"\|")
            suffix = f" ({entry['kind']})" if entry["kind"] != "procedure" else ""
            lines.append(
                f"| [{name}](../{path_for(root, name).relative_to(_sops_root(root))}){suffix} "
                f"| {desc} | {deps} |"
            )
        lines.append("")
    lines += ["## Execution notes", ""]
    for name, entry in sorted(data["entries"].items()):
        if entry["note"]:
            lines += [f"- **{name}:** {entry['note']}"]
    lines += [
        "",
        "The operator can inspect a deduplicated reading order and conditional",
        "follow-ups with `quirework sop explain NAME`. The catalog is discovery",
        "metadata, not a replacement for the SOP or a semantic completeness gate.",
        "",
    ]
    # Match the repo's source-Markdown wrapper; generated content must round-trip.
    from quirework.text.mdwrap import reflow_columns

    return reflow_columns("\n".join(lines))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="quirework sop", description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("list")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("explain")
    p.add_argument("name")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("catalog")
    options = p.add_mutually_exclusive_group()
    options.add_argument("--check", action="store_true")
    options.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    try:
        data = load(current_project().root)
        if args.action == "list":
            print(
                json.dumps(data, indent=2)
                if args.json
                else listing(data, current_project().root),
                end="\n" if args.json else "",
            )
        elif args.action == "explain":
            report = explain(data, args.name, current_project().root)
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                print(f"{args.name}: {report['description']}\n")
                print(
                    "Required SOP reading, dependencies first (not an execution plan):"
                )
                for row in report["reading_order"]:
                    print(f"  {row['path']} ({row['words']:,} words)")
                print(f"Total: {report['reading_words']:,} words\n")
                print("Mechanical tools: " + ", ".join(report["mechanics"]))
                if report["note"]:
                    print("Note: " + report["note"])
                print("\nConditional follow-ups (operator decides whether triggered):")
                for edge in report["followups"]:
                    print(f"  {edge['sop']}: {edge['when']}")
                if not report["followups"]:
                    print("  None cataloged.")
                print("\n" + report["scope"])
        else:
            content = markdown(data, current_project().root)
            path = current_project().path("sops.root", "sops") / "catalog" / "README.md"
            if args.check:
                if not path.exists() or path.read_text() != content:
                    raise ValueError(
                        "generated catalog is stale; run quirework sop catalog --write"
                    )
                print(
                    f"SOP catalog: {len(data['entries'])} entries, reading graph and generated index valid"
                )
            elif args.write:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                print("Wrote sops/catalog/README.md")
            else:
                print(content, end="")
    except (ValueError, OSError) as exc:
        print(f"sop catalog: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
