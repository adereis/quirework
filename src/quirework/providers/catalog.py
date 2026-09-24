"""Inspect provider catalogs without starting a model session."""

import argparse
import json
import subprocess
from quirework.project import current_project
from .models import codex_catalog, resolve


def main(argv=None):
    p = argparse.ArgumentParser(prog="quirework sop models", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="print what a provider's catalog offers")
    listing.add_argument("provider", choices=("codex", "claude", "agy"))
    query = sub.add_parser(
        "resolve", help="resolve a model name or family as preparation would"
    )
    query.add_argument("provider", choices=("codex", "claude", "agy"))
    query.add_argument("model")
    query.add_argument("--effort", required=True)
    args = p.parse_args(argv)
    if args.command == "resolve":
        result = resolve(args.provider, args.model, args.effort)
    elif args.provider == "codex":
        models, source = codex_catalog()
        result = {"source": source, "models": models}
    elif args.provider == "agy":
        value = subprocess.run(
            ["agy", "models"], capture_output=True, text=True, timeout=45, check=True
        )
        result = {"source": "agy models", "catalog": value.stdout}
    else:
        result = {
            "source": "project configuration",
            "models": current_project().get("providers.claude.models", []),
            "note": "Claude model selection is explicit; served identity is verified from the run receipt.",
        }
    print(json.dumps(result, indent=2))
    return 0
