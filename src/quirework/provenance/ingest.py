"""Capture local sessions; curation remains a separate supervised procedure."""

import argparse
import importlib
from quirework.project import current_project


def main(argv=None):
    p = argparse.ArgumentParser(prog="quirework provenance ingest", description=__doc__)
    p.add_argument("provider", choices=("claude", "codex", "agy", "all"))
    p.add_argument("arguments", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if not any(value in ("--help", "-h") for value in args.arguments):
        current_project().require("provenance")
    providers = (
        ("claude", "codex", "agy") if args.provider == "all" else (args.provider,)
    )
    if args.provider == "all" and args.arguments:
        p.error("provider-specific options require selecting one provider")
    for provider in providers:
        module = importlib.import_module(f"quirework.provenance.ingestion.{provider}")
        result = module.main(args.arguments)
        if result:
            return result
    return 0
