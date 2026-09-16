"""Capture a web conversation using an explicitly configured corpus."""

import argparse
from quirework.project import current_project


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="quirework provenance capture", description=__doc__
    )
    parser.add_argument("conversation", help="conversation UUID, ID, or Gemini URL")
    parser.add_argument("--provider", choices=("claude", "gemini"), default="claude")
    parser.add_argument(
        "--port", type=int, default=9222, help="local browser debugging port"
    )
    args = parser.parse_args(argv)
    current_project().require("provenance")
    if args.provider == "gemini":
        if args.port != 9222:
            parser.error(
                "Gemini capture currently requires browser debugging port 9222"
            )
        from . import fetch_gemini

        return fetch_gemini.main([args.conversation])
    from . import fetch_conversation

    return fetch_conversation.capture(args.conversation, port=args.port)
