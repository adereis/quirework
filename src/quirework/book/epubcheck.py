"""Validate the built EPUB against the spec, using the reference checker.

The EPUB is the one edition whose defects are INVISIBLE from the inside. A
figure number that disagrees across editions is caught by `fignumbers`, and a
missing typeface is caught by `fonts`, because both are properties this repo
can compute. Spec conformance is not: an `.epub` is a zip of XHTML validated
against schemas nothing here carries, and the failure mode is that every
desktop reader opens the book perfectly while a retailer's ingestion pipeline
rejects it. Foliate, Okular, and every browser-based reader are forgiving in
exactly the places EPUB is strict, so "it looks fine here" is not evidence.

So this shells out to **EPUBCheck**, the W3C reference implementation and the
same validator the retail pipelines run. It is pinned by version AND by
SHA-256, and fetched into a cache rather than vendored: it is 32 MB of jars,
which has no business in a prose repository, and an unpinned download is a
supply-chain hole in a tool that runs on every render.

Java is the one dependency, and its absence is reported rather than guessed
around. Unlike a missing typeface -- which fails SILENTLY, and is why `render`
refuses outright -- a missing validator is loud by construction: nothing claims
the book was checked. So `render` prints how to install it and carries on,
while `quirework book epubcheck` run directly exits non-zero, since asking for a
check and getting a shrug is a different thing from rendering without one.

Severity follows EPUBCheck's own exit semantics: FATAL and ERROR fail, WARNING
and INFO are counted and shown. Messages are deduplicated by (severity, code,
message) because one systematic defect reports once per document -- the font
`src` bug below produced 152 identical errors across 38 files -- and a raw dump
buries the count that actually tells you what kind of problem it is.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

# Pinned. Bump the three together: a version without its checksum is an
# unverified download, and a checksum without its version is a lie.
VERSION = "5.3.0"
URL = (
    f"https://github.com/w3c/epubcheck/releases/download/v{VERSION}"
    f"/epubcheck-{VERSION}.zip"
)
SHA256 = "6c07e68584b2e2ce2f89fe06e1246dfead3eb36b46b340e7d93524f29dcff6c5"

# `ERROR(RSC-007): /path/to.epub/EPUB/nav.xhtml(331,5): message`
_LINE = re.compile(r"^(FATAL|ERROR|WARNING|INFO|USAGE)\(([A-Z0-9-]+)\):\s*(.*)$")

# The location prefix on a message, stripped so identical defects in different
# files collapse to one row. Keeps the trailing message only.
_WHERE = re.compile(r"^\S+?\.epub/(\S+?)(?:\(\d+,\d+\))?:\s*")

FAIL = ("FATAL", "ERROR")

# EPUBCheck quotes the entire permitted-attribute list into a single RSC-005
# message -- roughly 2 KB of it -- so an untruncated report is unreadable.
_ELIDE = 140


def cache_dir() -> Path:
    """Where the pinned checker lives. XDG, so it is shared across clones."""
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "quirework"


def jar_path() -> Path:
    return cache_dir() / f"epubcheck-{VERSION}" / "epubcheck.jar"


def find_jar() -> Path | None:
    """The jar, by explicit override, then by cache. Never by PATH guess.

    `EPUBCHECK_JAR` is honored first so a distro package or a CI image can
    supply its own copy; everything else uses the pinned cache.
    """
    override = os.environ.get("EPUBCHECK_JAR")
    if override:
        p = Path(override).expanduser()
        return p if p.exists() else None
    p = jar_path()
    return p if p.exists() else None


def have_java() -> bool:
    return shutil.which("java") is not None


def install(force: bool = False) -> int:
    """Fetch, verify and unpack the pinned EPUBCheck into the cache."""
    target = jar_path()
    if target.exists() and not force:
        print(f"epubcheck {VERSION} already installed: {target}")
        return 0

    dest = cache_dir()
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / f"epubcheck-{VERSION}.zip"

    if not archive.exists() or force:
        print(f"fetching EPUBCheck {VERSION} ...")
        try:
            with urllib.request.urlopen(URL, timeout=60) as r:  # noqa: S310
                archive.write_bytes(r.read())
        except Exception as exc:  # network, TLS, 404
            archive.unlink(missing_ok=True)
            print(f"epubcheck: download failed: {exc}", file=sys.stderr)
            return 1

    # Verified BEFORE it is unpacked, so a corrupted or substituted archive
    # never reaches the filesystem as executable jars.
    got = hashlib.sha256(archive.read_bytes()).hexdigest()
    if got != SHA256:
        archive.unlink(missing_ok=True)
        print(
            f"epubcheck: checksum mismatch for {URL}\n"
            f"  expected {SHA256}\n  got      {got}\n"
            f"  the download was discarded.",
            file=sys.stderr,
        )
        return 1

    with zipfile.ZipFile(archive) as z:
        z.extractall(dest)
    if not target.exists():
        print(f"epubcheck: {target} missing after unpack.", file=sys.stderr)
        return 1
    print(f"epubcheck {VERSION} installed: {target}")
    return 0


def parse(output: str) -> list[tuple[str, str, str, str]]:
    """(severity, code, where, message) per reported line.

    `where` is the path inside the container; it is kept for the sample line
    and dropped from the dedupe key, since one systematic defect reports once
    per document and the count is the interesting part.
    """
    rows: list[tuple[str, str, str, str]] = []
    for line in output.splitlines():
        m = _LINE.match(line.strip())
        if not m:
            continue
        severity, code, rest = m.groups()
        where = ""
        w = _WHERE.match(rest)
        if w:
            where = w.group(1)
            rest = rest[w.end() :]
        rows.append((severity, code, where, rest.strip()))
    return rows


def report(rows: list[tuple[str, str, str, str]]) -> None:
    """Group by (severity, code, message), worst first, with one example."""
    groups: dict[tuple[str, str, str], list[str]] = {}
    for severity, code, where, message in rows:
        groups.setdefault((severity, code, message), []).append(where)

    order = {"FATAL": 0, "ERROR": 1, "WARNING": 2, "INFO": 3, "USAGE": 4}
    for (severity, code, message), wheres in sorted(
        groups.items(), key=lambda kv: (order.get(kv[0][0], 9), -len(kv[1]))
    ):
        text = message if len(message) <= _ELIDE else message[:_ELIDE] + " ..."
        print(f"  {severity}({code}) x{len(wheres)}: {text}")
        if wheres[0]:
            print(f"    e.g. {wheres[0]}")


def check(epub: Path) -> tuple[int, list[tuple[str, str, str, str]]] | None:
    """Run the checker. None when it cannot run (no java, no jar)."""
    jar = find_jar()
    if jar is None or not have_java():
        return None
    proc = subprocess.run(
        ["java", "-jar", str(jar), str(epub)], capture_output=True, text=True
    )
    # EPUBCheck writes its findings to stderr and its summary to stdout.
    return proc.returncode, parse(proc.stdout + "\n" + proc.stderr)


def missing_hint() -> str:
    if not have_java():
        return (
            "epubcheck: java not found (EPUBCheck needs a JRE).\n"
            "  Fedora: sudo dnf install java-latest-openjdk-headless"
        )
    return (
        "epubcheck: the validator is not installed.\n"
        "  Install the pinned copy with: quirework book epubcheck --install"
    )


def verify(site: Path) -> int:
    """The render-time gate over every EPUB in `site`.

    Absence is a loud skip rather than a render failure: EPUBCheck is a 32 MB
    download and a JRE, which is a heavy thing to demand of a clone that only
    wants to read the book. It never skips SILENTLY -- the point of the fonts
    rule is that a silent substitution leaves no trace, and this one prints.
    """
    epubs = sorted(site.glob("*.epub"))
    if not epubs:
        return 0
    if find_jar() is None or not have_java():
        print(missing_hint(), file=sys.stderr)
        print("  The EPUB was NOT validated.", file=sys.stderr)
        return 0

    rc = 0
    for epub in epubs:
        result = check(epub)
        if result is None:  # raced with a removal; treated as unchecked
            print(missing_hint(), file=sys.stderr)
            continue
        _, rows = result
        bad = [r for r in rows if r[0] in FAIL]
        if not bad:
            others = len(rows)
            tail = f" ({others} warnings/notices)" if others else ""
            print(f"epubcheck: {epub.name} is valid EPUB{tail}.")
            continue
        print(
            f"epubcheck: {epub.name} FAILED with {len(bad)} error(s):", file=sys.stderr
        )
        report(rows)
        rc = 1
    return rc


HELP = """\
usage: quirework book epubcheck [--install] [--force] [EPUB]

Validate the built EPUB with the W3C reference checker (EPUBCheck),
pinned by version and SHA-256 and cached outside the repo. Without an
argument, checks every .epub in build/book/_site/.

Desktop readers are forgiving where EPUB is strict, so "it opens fine"
is not evidence; a retailer's ingestion pipeline runs this validator.

options:
  -h, --help   show this help message and exit
  --install    fetch and verify the pinned EPUBCheck into the cache
  --force      re-download even if the cache already has it

Exit status is non-zero when the book has FATAL or ERROR findings, or
when the checker was asked for and is not installed.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("epub", nargs="?")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("-h", "--help", action="store_true", dest="help")
    args = parser.parse_args(list(argv if argv is not None else sys.argv[1:]))

    if args.help:
        print(HELP, end="")
        return 0
    if args.install or args.force:
        return install(force=args.force)

    import quirework.book.render as _options_quirework_book_render
    from quirework.book.render import SITE

    if args.epub:
        targets = [Path(args.epub)]
        if not targets[0].exists():
            print(f"epubcheck: no such file: {args.epub}", file=sys.stderr)
            return 2
    else:
        targets = sorted(
            (_options_quirework_book_render._options().BUILD_BOOK / SITE).glob("*.epub")
        )
        if not targets:
            print(
                f"epubcheck: no .epub in {_options_quirework_book_render._options().BUILD_BOOK / SITE} "
                f"(run `quirework book build --to epub` first).",
                file=sys.stderr,
            )
            return 2

    # Asked for directly, an uninstalled checker is a failure, not a skip.
    if find_jar() is None or not have_java():
        print(missing_hint(), file=sys.stderr)
        return 127

    try:
        return _check_all(targets)
    except KeyboardInterrupt:
        # `subprocess.run` kills the validator for us and re-raises; all that
        # is left is not to hand the caller a traceback for a deliberate stop.
        print("\nepubcheck: interrupted.", file=sys.stderr)
        return 130


def _check_all(targets: list[Path]) -> int:
    rc = 0
    for epub in targets:
        result = check(epub)
        assert result is not None  # guarded by the caller
        _, rows = result
        bad = [r for r in rows if r[0] in FAIL]
        counts = {}
        for severity, _code, _where, _msg in rows:
            counts[severity] = counts.get(severity, 0) + 1
        summary = ", ".join(
            f"{n} {s.lower()}{'s' if n != 1 else ''}" for s, n in sorted(counts.items())
        )
        print(f"{epub.name}: {summary or 'clean'}")
        if rows:
            report(rows)
        if bad:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
