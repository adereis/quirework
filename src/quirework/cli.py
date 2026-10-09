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
    # The publisher a command is built on, when it is one backend's alone.
    backend: str | None = None


GROUPS = {
    "spec": "Read and check the spec, marks, sources, quotations and trail",
    "publish": "Render the manuscript and inspect its editions",
    "text": "Format and inspect writing",
    "provenance": "Capture, organize, and search source history",
    "sop": "Find procedures and operate supervised workflows",
    "dev": "Repository checks and tooling maintenance",
    "config": "Inspect and validate project configuration",
    "usage": "Report which commands and procedures get used",
}
COMMANDS = {
    ("spec", "notes"): Command(
        "manuscript.notes", "Inspect annotations and bindings", "manuscript"
    ),
    ("spec", "trail"): Command(
        "manuscript.trail",
        "Point spec sections at the sessions that shaped them",
        "manuscript",
    ),
    ("spec", "quotes"): Command(
        "manuscript.quotes", "Check quotations in annotations", "manuscript"
    ),
    ("spec", "sources"): Command(
        "manuscript.sources",
        "List the sources the work stands on and check citations of them",
        "manuscript",
    ),
    ("spec", "units"): Command(
        "manuscript.units",
        "List the units and check references to them",
        "manuscript",
    ),
    ("spec", "rename"): Command(
        "manuscript.rename",
        "Rename a spec section everywhere its slug is written",
        "manuscript",
    ),
    ("publish", "build"): Command(
        "publish.build", "Render the manuscript through its backend", "publish"
    ),
    ("publish", "figures"): Command(
        "publish.figures",
        "Build and inspect figure galleries",
        "publish",
        backend="quarto",
    ),
    ("publish", "fonts"): Command(
        "publish.fonts",
        "Inspect required typefaces",
        "publish",
        backend="quarto",
    ),
    ("publish", "epubcheck"): Command("publish.epubcheck", "Validate an EPUB artifact"),
    ("publish", "redpen"): Command(
        "publish.redpen",
        "Create and read review documents",
        "publish",
        backend="quarto",
    ),
    ("publish", "packet"): Command(
        "publish.panelkit",
        "Prepare a rendered review packet",
        "publish",
        backend="quarto",
    ),
    ("publish", "variant"): Command(
        "publish.variant",
        "Prepare a configured derived edition",
        "publish",
        backend="quarto",
    ),
    ("publish", "plan"): Command(
        "publish.plans",
        "Compile rendered artifacts into run plans",
        backend="quarto",
    ),
    ("publish", "compare"): Command(
        "publish.experiment",
        "Prepare and operate chapter comparisons",
        "publish",
        backend="quarto",
    ),
    ("sop", "panel"): Command(
        "sops.composition", "Compile declared panels into run plans"
    ),
    ("text", "wrap"): Command("text.mdwrap", "Wrap Markdown or check its formatting"),
    ("text", "check"): Command("text.jargon", "Inspect configured terminology"),
    ("text", "tells"): Command("text.tells", "Report machine habits in the prose"),
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
    ("provenance", "store"): Command(
        "provenance.store", "Create, inspect, verify and back up the session store"
    ),
    ("provenance", "accept"): Command(
        "provenance.curate",
        "Accept pending sessions into the corpus",
        "provenance",
        prefix=("accept",),
    ),
    ("provenance", "reject"): Command(
        "provenance.curate",
        "Curate sessions out of the corpus",
        "provenance",
        prefix=("reject",),
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
    ("sop", "room"): Command("sops.room", "Operate a writers room over a shared board"),
    ("sop", "roundtable"): Command(
        "sops.roundtable", "Run AI models, as themselves, in turns towards one goal"
    ),
    ("sop", "blind"): Command("providers.blind", "Inspect and enforce run isolation"),
    ("dev", "check"): Command("core.checks", "Run the configured repository checks"),
    ("usage", "show"): Command(
        "core.usage", "Summarize recorded command usage", prefix=("show",)
    ),
    ("usage", "sops"): Command(
        "core.usage", "Rank procedures by recorded reads", prefix=("sops",)
    ),
    ("usage", "seats"): Command(
        "core.usage", "Rank harnesses and models by seats launched", prefix=("seats",)
    ),
    ("usage", "log"): Command(
        "core.usage", "Read the most recent records", prefix=("log",)
    ),
    ("usage", "prune"): Command(
        "core.usage", "Remove old usage records", prefix=("prune",)
    ),
}


def _parser(group=None):
    if group is None:
        lines = ["Work with:"]
        lines += [
            f"  {name:12} {GROUPS[name]}"
            for name in ("spec", "publish", "text", "provenance", "sop")
        ]
        lines += ["", "Maintain:"]
        lines += [f"  {name:12} {GROUPS[name]}" for name in ("dev", "config", "usage")]
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
        scoped = project
        if item.backend and not help_only:
            # A command built on one backend's staging runs as the target that
            # backend builds sees the project, and is refused by name where no
            # target uses it.
            from .publish import targets

            scoped = targets.for_backend(item.backend, " ".join(key), project)
        module = importlib.import_module("quirework." + item.module)
        if help_only:
            # Asking what a command does is not using it, and help stays free
            # of side effects, so nothing is recorded on this path.
            return module.main([*item.prefix, *argv]) or 0
        from .core.usage import record

        with record(key, argv, project=project) as outcome, project_scope(scoped):
            outcome.status = module.main([*item.prefix, *argv]) or 0
            return outcome.status


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
                    "backup": "backup.toml",
                    "book": "book.toml",
                    "site": "site.toml",
                    "run": "run.toml",
                    "terminology": "terminology.toml",
                    "tells": "tells.toml",
                    "tmpfiles": "tmpfiles.conf",
                }
                p.add_argument("kind", choices=tuple(names))
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
                    from .provenance import backup, store

                    opened = store.open_store(project)
                    print(f"Provenance store: {store.describe_location(opened)}")
                    chosen = store.describe_home(project)
                    if chosen:
                        print(f"Provenance home: {chosen}")
                    # Read and validated here, offline; `store backup --check`
                    # is what reaches each destination.
                    source = backup.Source(opened.root, opened.identity)
                    drivers = backup.targets(opened.root)
                    if drivers is None:
                        print(f"Provenance backup: {backup.unconfigured(source)}")
                    for driver in drivers or ():
                        print(
                            f"Provenance backup: {driver.target}, {driver.describe()}"
                        )
                if project.get("manuscript"):
                    project.require("manuscript")
                if project.get("publish"):
                    project.require("publish")
                if project.get("usage.store") is not None:
                    # Resolve it here, where somebody is asking, instead of
                    # letting a typo surface as a warning on every command.
                    project.path("usage.store")
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
