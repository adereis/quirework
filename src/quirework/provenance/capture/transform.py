"""Transform an already captured conversation without contacting a provider."""

import argparse
import importlib
from quirework.project import current_project


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="quirework provenance transform", description=__doc__
    )
    parser.add_argument("kind", choices=("transcript", "files", "artifacts"))
    parser.add_argument("conversation")
    args = parser.parse_args(argv)
    current_project().require("provenance")
    name = {
        "transcript": "render_transcript",
        "files": "extract_files",
        "artifacts": "extract_artifacts",
    }[args.kind]
    module = importlib.import_module("quirework.provenance.capture." + name)
    return module.main([args.conversation]) or 0
