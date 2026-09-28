"""Build a Hugo site into the project's build tree.

The site's own configuration decides everything about the render: theme,
output formats, base URL, what a draft is. This backend only chooses where
the output goes, so a build Quirework runs never overwrites the directory the
site deploys from (Hugo's default is `public/` inside the site). Hugo keeps
its resource cache and build lock inside the site as it always does; a site
already ignores those.

Deploying is the site's business too. A site that pushes its build somewhere
keeps that script, and a procedure can run `publish build` first as the check
that the site still renders.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

from quirework.project import ConfigurationError, current_project

# The files Hugo reads its configuration from, in its own lookup order, and the
# directory form. One of them present is what makes a directory a Hugo site.
CONFIG_NAMES = (
    "hugo.toml",
    "hugo.yaml",
    "hugo.yml",
    "hugo.json",
    "config.toml",
    "config.yaml",
    "config.yml",
    "config.json",
)


def site_root():
    """The Hugo site: `publish.site`, or the project root when unset."""
    project = current_project()
    if project.get("publish.site") is None:
        return project.root
    return project.path("publish.site")


def check_site(site) -> None:
    if not site.is_dir():
        raise ConfigurationError(f"configured Hugo site is missing: {site}")
    if (
        not any((site / name).is_file() for name in CONFIG_NAMES)
        and not (site / "config" / "_default").is_dir()
    ):
        raise ConfigurationError(
            f"{site} has no Hugo configuration ({', '.join(CONFIG_NAMES[:2])}, ...)"
        )


def main(argv: list[str] | None = None, target: str = "site") -> int:
    ap = argparse.ArgumentParser(
        prog="quirework publish build",
        description="Build the Hugo site into the project's build tree. "
        "Arguments this command does not know are passed to `hugo build`.",
        epilog="examples:\n"
        "  quirework publish build                 the site as readers get it\n"
        "  quirework publish build --drafts        drafts included, for review\n"
        "  quirework publish build --environment staging\n"
        "                                          any other flag goes to hugo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--drafts",
        action="store_true",
        help="include pages marked as drafts",
    )
    ap.add_argument(
        "--out",
        metavar="DIR",
        help="where the site is written (default: <publish.build>/<target>)",
    )
    args, passthru = ap.parse_known_args(argv)

    hugo = shutil.which("hugo")
    if not hugo:
        print(
            "publish build: hugo not found on PATH. Install Hugo (the extended "
            "edition if the site's theme needs it), then re-run.",
            file=sys.stderr,
        )
        return 127
    project = current_project()
    site = site_root()
    check_site(site)
    out = (
        project.root / args.out
        if args.out
        else project.path("publish.build", "build") / target
    ).resolve()
    if out == site.resolve() or site.resolve().is_relative_to(out):
        raise ConfigurationError(f"refusing to build into the site itself: {out}")
    command = [
        hugo,
        "build",
        "--source",
        str(site),
        "--destination",
        str(out),
        "--cleanDestinationDir",
    ]
    if args.drafts:
        command.append("--buildDrafts")
    command += passthru
    done = subprocess.run(command)
    if done.returncode:
        print(f"publish build: hugo exited {done.returncode}", file=sys.stderr)
        return done.returncode
    print(f"publish build: {out}")
    return 0
