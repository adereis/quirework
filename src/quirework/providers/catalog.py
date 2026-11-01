"""Inspect provider catalogs without starting a model session."""

import argparse
import json
import subprocess
from quirework.project import current_project
from .models import codex_catalog, resolve


def agy_catalog():
    """What `agy models` prints, or a ValueError the command line reports.

    agy fails when it is not signed in, and a failure here used to surface as
    a traceback where the Codex listing names the failure and how to check the
    login; the command line prints a ValueError as one clean line.
    """
    try:
        value = subprocess.run(
            ["agy", "models"], capture_output=True, text=True, timeout=45, check=True
        )
    except subprocess.CalledProcessError as exc:
        said = (exc.stderr or exc.stdout or "").strip().splitlines()
        reason = said[-1] if said else f"exit status {exc.returncode}"
        raise ValueError(
            f"agy models failed ({reason}); check that agy is signed in"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            "agy models did not answer within 45 seconds; check that agy is "
            "signed in and can reach its service"
        ) from exc
    return value.stdout


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
        result = resolve(
            args.provider,
            args.model,
            args.effort,
            declared=current_project().get("providers.claude.models", []),
        )
    elif args.provider == "codex":
        models, source = codex_catalog()
        result = {"source": source, "models": models}
    elif args.provider == "agy":
        result = {"source": "agy models", "catalog": agy_catalog()}
    else:
        result = {
            "source": "project configuration",
            "models": current_project().get("providers.claude.models", []),
            "note": "Claude model selection is explicit; served identity is verified from the run receipt.",
        }
    print(json.dumps(result, indent=2))
    return 0
