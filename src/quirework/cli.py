"""Task-oriented command discovery with lazy, side-effect-free help."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import dataclass

from . import __version__
from .project import ConfigurationError, Project, project_scope


@dataclass(frozen=True)
class Command:
    module: str
    summary: str
    component: str | None = None
    prefix: tuple[str, ...] = ()


GROUPS = {
    "book": "Build and inspect book artifacts",
    "text": "Format and inspect writing",
    "provenance": "Capture, organize, and search source history",
    "sop": "Find procedures and operate supervised workflows",
    "dev": "Repository checks and tooling maintenance",
    "config": "Inspect and validate project configuration",
}
COMMANDS = {
    ("book", "build"): Command("book.render", "Render the configured book", "book"),
    ("book", "figures"): Command(
        "book.figures", "Build and inspect figure galleries", "book"
    ),
    ("book", "chapters"): Command(
        "book.chapters", "Inspect chapter order and references", "book"
    ),
    ("book", "notes"): Command(
        "book.notes", "Inspect annotations and bindings", "book"
    ),
    ("book", "quotes"): Command(
        "book.quotes", "Check quotations in annotations", "book"
    ),
    ("book", "redpen"): Command(
        "book.redpen", "Create and read review documents", "book"
    ),
    ("book", "packet"): Command(
        "book.panelkit", "Prepare a rendered review packet", "book"
    ),
    ("book", "fonts"): Command("book.fonts", "Inspect required typefaces", "book"),
    ("book", "epubcheck"): Command("book.epubcheck", "Validate an EPUB artifact"),
    ("book", "variant"): Command(
        "book.variant", "Prepare a configured derived edition", "book"
    ),
    ("book", "plan"): Command("book.plans", "Compile book artifacts into run plans"),
    ("sop", "panel"): Command(
        "sops.composition", "Compile declared panels into run plans"
    ),
    ("book", "compare"): Command(
        "book.experiment", "Prepare and operate chapter comparisons", "book"
    ),
    ("text", "wrap"): Command("text.mdwrap", "Wrap Markdown or check its formatting"),
    ("text", "check"): Command("text.jargon", "Inspect configured terminology"),
    ("provenance", "search"): Command(
        "provenance.search", "Search human turns", "provenance"
    ),
    ("provenance", "show"): Command(
        "provenance.show", "Read a clean session transcript", "provenance"
    ),
    ("provenance", "catalog"): Command(
        "provenance.catalog", "Build or validate the source catalog", "provenance"
    ),
    ("provenance", "coldread"): Command(
        "provenance.coldread", "Inspect and operate authorship analysis", "provenance"
    ),
    ("provenance", "metrics"): Command(
        "provenance.metrics", "Measure source history and contributions", "provenance"
    ),
    ("provenance", "input"): Command(
        "provenance.humaninput", "Compile verbatim human contributions", "provenance"
    ),
    ("provenance", "transform"): Command(
        "provenance.capture.transform",
        "Render transcripts or recover captured artifacts",
        "provenance",
    ),
    ("provenance", "capture"): Command(
        "provenance.capture.commands",
        "Capture supported web conversations",
        "provenance",
    ),
    ("provenance", "ingest"): Command(
        "provenance.ingest", "Capture local sessions for later curation", "provenance"
    ),
    ("sop", "list"): Command(
        "sops.commands", "List available procedures", prefix=("list",)
    ),
    ("sop", "show"): Command("sops.commands", "Read a procedure"),
    ("sop", "explain"): Command(
        "sops.commands", "Explain procedure prerequisites", prefix=("explain",)
    ),
    ("sop", "catalog"): Command(
        "sops.commands", "Generate or check procedure discovery", prefix=("catalog",)
    ),
    ("sop", "skills"): Command(
        "sops.commands", "Generate agent skill entry points", prefix=("skills",)
    ),
    ("sop", "personas"): Command("sops.personas", "Inspect and validate perspectives"),
    ("sop", "models"): Command(
        "providers.catalog", "Inspect model availability and capabilities"
    ),
    ("sop", "runs"): Command(
        "runs.commands", "Prepare, start, recover, and inspect runs"
    ),
    ("sop", "workshop"): Command(
        "sops.workshop", "Prepare and operate writing experiments"
    ),
    ("sop", "review"): Command("sops.review", "Review a configured source change"),
    ("sop", "blind"): Command("providers.blind", "Inspect and enforce run isolation"),
    ("dev", "check"): Command("core.checks", "Run the configured repository checks"),
}


def _parser(group=None):
    if group is None:
        lines = ["Work with:"]
        lines += [
            f"  {name:12} {GROUPS[name]}"
            for name in ("book", "text", "provenance", "sop")
        ]
        lines += ["", "Maintain:"]
        lines += [f"  {name:12} {GROUPS[name]}" for name in ("dev", "config")]
        p = argparse.ArgumentParser(
            prog="quirework",
            description="Tools for writing, research, and supervised reviews.",
            epilog="\n".join(lines),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        p.add_argument(
            "--project",
            "--config",
            metavar="PATH",
            help="project directory or configuration file",
        )
        p.add_argument(
            "--version", action="version", version=f"quirework {__version__}"
        )
        p.add_argument("group", nargs="?", choices=GROUPS, metavar="GROUP")
    else:
        rows = [
            (name, item.summary)
            for (parent, name), item in COMMANDS.items()
            if parent == group
        ]
        if group == "config":
            rows = [
                ("show", "Show effective settings and their origins"),
                ("check", "Validate enabled project components"),
                ("template", "Print an annotated configuration template"),
            ]
        p = argparse.ArgumentParser(
            prog=f"quirework {group}",
            description=GROUPS[group],
            epilog="Commands:\n"
            + "\n".join(f"  {name:12} {summary}" for name, summary in rows),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        p.add_argument(
            "command", nargs="?", choices=[name for name, _ in rows], metavar="COMMAND"
        )
    p.add_argument("arguments", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return p


def invoke(command, argv=(), *, project):
    key = tuple(command.split()) if isinstance(command, str) else tuple(command)
    if key not in COMMANDS:
        raise ConfigurationError(f"unknown command: {' '.join(key)}")
    item = COMMANDS[key]
    with project_scope(project):
        # Help never validates a corpus, initializes a provider, or launches work.
        help_only = any(a in ("--help", "-h") for a in argv)
        if item.component and not help_only:
            project.require(item.component)
        module = importlib.import_module("quirework." + item.module)
        return module.main([*item.prefix, *argv]) or 0


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not args.group:
        parser.print_help()
        return 0
    sub = _parser(args.group)
    selected = sub.parse_args(args.arguments)
    if not selected.command:
        sub.print_help()
        return 0
    try:
        if args.group == "config":
            p = argparse.ArgumentParser(prog=f"quirework config {selected.command}")
            if selected.command == "template":
                from importlib.resources import files
                from pathlib import Path

                # Not every template is TOML: the cache-cleanup rule is a
                # systemd-tmpfiles drop-in and has to keep that extension.
                names = {
                    "project": "project.toml",
                    "run": "run.toml",
                    "terminology": "terminology.toml",
                    "tmpfiles": "tmpfiles.conf",
                }
                p.add_argument(
                    "kind",
                    choices=tuple(names),
                    nargs="?",
                    default="project",
                )
                p.add_argument(
                    "--out", type=Path, help="create a new file instead of printing"
                )
                options = p.parse_args(selected.arguments)
                text = (
                    files("quirework")
                    .joinpath("templates", names[options.kind])
                    .read_text(encoding="utf-8")
                )
                if options.out is not None:
                    options.out.parent.mkdir(parents=True, exist_ok=True)
                    with options.out.open("x", encoding="utf-8") as output:
                        output.write(text)
                    print(f"Created {options.out}")
                else:
                    print(text, end="")
                return 0
            p.add_argument(
                "--json",
                action="store_true",
                help="emit machine-readable configuration",
            )
            p.parse_args(selected.arguments)
            project = Project.load(args.project)
            if selected.command == "check":
                if project.get("provenance.enabled", False):
                    project.require("provenance")
                if project.get("book"):
                    project.require("book")
                print("Configuration is valid.")
            else:
                print(json.dumps(project.describe(), indent=2))
            return 0
        return invoke(
            (args.group, selected.command),
            selected.arguments,
            project=Project.load(args.project),
        )
    except (ConfigurationError, OSError, RuntimeError, ValueError) as exc:
        print(f"quirework: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
