"""Proofs: the copies of a book made before it is published.

A book that renders differently until it is published names, in its render
policy, the top-level metadata key that says so (`proof_switch`). Until that
key is the boolean false, every render is a proof. A book prints a proof's
identity where it chooses, from the revision metadata every stage records
(`revision_metadata`); this module puts it where a person meets the file
before opening it, and then proves it reached the pages:

- the file name says the copy is a proof and names its commit (`proof_name`);
- the library title of an EPUB or a DOCX says so too (`proof_title`), and the
  stage sets the same title as `proof-title` for a PDF template to use;
- a cover drawn from a Typst source (`cover_source`) is compiled in every
  stage, with the proof's identity as inputs, so a proof's cover can say what
  it is and the published cover needs no hand step;
- and the build ends by reading the artifacts back (`verify`).

The last is what makes the rest trustworthy. The book's templates and filters
print the stamp, and a template's conditional fails open: a variable that is
missing prints nothing, and the render succeeds with a copy that does not say
what it is. Only the finished files can show whether the stamp is in them, so
a proof whose commit is missing from a page stops the build.

`quirework publish copy --for NAME` makes a proof prepared for one person. It
refuses sources with uncommitted changes, since two different sets of changes
on one commit would carry one stamp. It gives the copy the next number in a
log kept in the per-user configuration (`copies.log`), and it sets the number
and the person's name as stage metadata for the book to print. The name goes
where a reader looks and never into a file name: the file names the copy by
number, so a forwarded file does not announce whose it was, and the log
connects the number to the person. The log names people, so it is refused
inside the project, where a commit could carry it.
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime
import fcntl
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path

from quirework.project import current_project, user_config_path, user_settings
from quirework.publish import bookstage as _bookstage

# A copy being made for one person, set by `publish copy` around its render:
# {"number": int, "recipient": str}. Every stage of that render reads it.
_COPY: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "proof_copy", default=None
)

DEFAULT_NAME = "{slug}_proof_{date}_{short}"
DEFAULT_TITLE = "{title} (uncorrected proof {short}{copy_note})"
COVER_PPI = 300
# Marks a proof's compiled cover image, so the check can tell it from the
# published cover without reading pixels.
COVER_MARK = "quirework-proof"

# Quarto format name -> the extension of the single file it writes.
EXTENSIONS = {"typst": ".pdf", "pdf": ".pdf", "epub": ".epub", "docx": ".docx"}


def _policy() -> dict:
    return current_project().data("publish.render_policy", {})


@dataclass(frozen=True)
class Proof:
    """What a prepared stage says about the proof it renders."""

    name: str  # the stem of every file the stage writes
    title: str  # the library title
    commit: str
    short: str
    date: str
    modified: bool
    copy: int | None
    recipient: str | None


# --------------------------------------------------------------------------- #
# whether a stage renders a proof
# --------------------------------------------------------------------------- #
def switch() -> str | None:
    """The metadata key the render policy names as the proof switch, if any.

    Raises ValueError for a value that is not a plain metadata name."""
    key = _policy().get("proof_switch")
    if key is None:
        return None
    if not isinstance(key, str) or not _bookstage._METADATA_KEY.fullmatch(key):
        raise ValueError(f"proof_switch must name a metadata key, not {key!r}")
    return key


def is_proof(stage: Path) -> bool | None:
    """Whether the `_quarto.yml` under `stage` renders a proof: None when the
    policy names no switch. Only the boolean false publishes, so a switch that
    is missing, quoted or misspelled leaves the copy a proof."""
    key = switch()
    if key is None:
        return None
    return _bookstage.top_metadata(stage, key) is not False


def _slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-") or "book"


def _revision(stage: Path) -> dict | None:
    read = {
        key: _bookstage.top_metadata(stage, f"source-{key}")
        for key in ("commit", "commit-short", "date", "modified")
    }
    if not all(isinstance(read[k], str) for k in ("commit", "commit-short", "date")):
        return None
    return {
        "commit": read["commit"],
        "short": read["commit-short"],
        "date": read["date"],
        # Only a boolean false says the copy is that commit.
        "modified": read["modified"] is not False,
    }


def _format(pattern: object, setting: str, fields: dict) -> str:
    if not isinstance(pattern, str):
        raise ValueError(f"{setting} must be a string, not {pattern!r}")
    try:
        return pattern.format(**fields)
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(
            f"{setting} {pattern!r} names a field it cannot fill ({exc}); "
            f"it can use {', '.join('{' + f + '}' for f in fields)}"
        ) from None


# --------------------------------------------------------------------------- #
# preparing a stage
# --------------------------------------------------------------------------- #
def prepare(stage: Path) -> int:
    """Name a proof's files, set its title and copy, and compile the cover.

    Runs after a stage has recorded its revision and taken any metadata the
    stage sets, so it reads the stage's own `_quarto.yml` for both. Returns 1,
    with the reason on stderr, for a policy or a stage that cannot make the
    copy it asks for."""
    try:
        proof = _prepare(stage)
    except ValueError as exc:
        print(f"render: {exc}", file=sys.stderr)
        return 1
    return compile_cover(stage, proof)


def _prepare(stage: Path) -> Proof | None:
    proof = is_proof(stage)
    copy = _COPY.get()
    if not proof:
        if copy is not None:
            raise ValueError(
                "a copy for one person is a proof, and this stage renders the "
                "book as published"
            )
        return None
    revision = _revision(stage)
    if revision is None:
        raise ValueError(
            "a proof names the commit it was made from, and this stage has none: "
            "set revision_metadata = true in the render policy, and render from "
            "a git checkout with a commit"
        )
    title = _bookstage.book_option(stage, "title")
    title = title if isinstance(title, str) else ""
    stem = _bookstage.book_option(stage, "output-file")
    slug = _slug(stem if isinstance(stem, str) and stem else title)
    fields = {"slug": slug, "title": title, **revision}
    fields.pop("modified")
    name = _format(_policy().get("proof_name", DEFAULT_NAME), "proof_name", fields)
    if not name or "/" in name or name.startswith("."):
        raise ValueError(f"proof_name makes {name!r}, which is not a file name")
    if revision["modified"]:
        name += "-modified"
    note = ""
    if copy is not None:
        name += f"_copy-{copy['number']:02d}"
        note = f", copy {copy['number']}"
    library = _format(
        _policy().get("proof_title", DEFAULT_TITLE),
        "proof_title",
        {**fields, "copy_note": note},
    )
    _bookstage.set_book_option(stage, "output-file", name)
    values: dict = {"proof-title": library}
    if copy is not None:
        values |= {"copy-number": copy["number"], "copy-recipient": copy["recipient"]}
    _bookstage.set_top_metadata(stage, values)
    return Proof(
        name=name,
        title=library,
        commit=revision["commit"],
        short=revision["short"],
        date=revision["date"],
        modified=revision["modified"],
        copy=copy["number"] if copy else None,
        recipient=copy["recipient"] if copy else None,
    )


def proof_of(stage: Path) -> Proof | None:
    """The proof a prepared stage renders, read back from its `_quarto.yml`,
    or None when it renders the book as published or was never staged."""
    if not (stage / _bookstage.BOOK_CONFIG).is_file() or not is_proof(stage):
        return None
    revision = _revision(stage)
    name = _bookstage.book_option(stage, "output-file")
    title = _bookstage.top_metadata(stage, "proof-title")
    if revision is None or not isinstance(name, str) or not isinstance(title, str):
        return None
    number = _bookstage.top_metadata(stage, "copy-number")
    recipient = _bookstage.top_metadata(stage, "copy-recipient")
    return Proof(
        name=name,
        title=title,
        commit=revision["commit"],
        short=revision["short"],
        date=revision["date"],
        modified=revision["modified"],
        copy=int(number) if isinstance(number, str) and number.isdigit() else None,
        recipient=recipient if isinstance(recipient, str) else None,
    )


# --------------------------------------------------------------------------- #
# the cover
# --------------------------------------------------------------------------- #
def _typeset(command: list[str]) -> subprocess.CompletedProcess:
    """Run the cover's typesetter; its own name, so a test stands in for it
    and for nothing else."""
    return subprocess.run(command, capture_output=True, text=True, check=False)


def compile_cover(stage: Path, proof: Proof | None) -> int:
    """Draw the book's cover from its Typst source into the stage.

    The render policy's `cover_source` names the source, relative to the
    manuscript; it is compiled into the file `book.cover-image` names, as JPEG
    or PNG by that file's extension. The source reads its inputs with
    `sys.inputs`: `proof` is "true" or "false" when the policy names a proof
    switch, and a proof adds `commit`, `short`, `date`, `modified` ("true" or
    "false"), and for a copy for one person `copy` and `recipient`. A proof's
    image also carries its commit in the file's own comment, which is how the
    check tells it from the published cover. Returns 0 with nothing to do."""
    source = _policy().get("cover_source")
    if source is None:
        return 0
    if not isinstance(source, str) or not source:
        print(f"render: cover_source must be a path, not {source!r}", file=sys.stderr)
        return 1
    typ = stage / source
    target = _bookstage.book_option(stage, "cover-image")
    if not typ.is_file():
        print(f"render: cover_source {source} is not in the sources", file=sys.stderr)
        return 1
    if not isinstance(target, str) or Path(target).suffix.lower() not in (
        ".jpg",
        ".jpeg",
        ".png",
    ):
        print(
            "render: cover_source needs book.cover-image to name the .jpg or "
            f".png it compiles into, not {target!r}",
            file=sys.stderr,
        )
        return 1
    from quirework.publish.render import find_quarto

    quarto = find_quarto()
    if not quarto:
        print(
            "render: the cover needs quarto's typst, and quarto is not found",
            file=sys.stderr,
        )
        return 127
    inputs: dict[str, str] = {}
    if switch() is not None:
        inputs["proof"] = "true" if proof else "false"
    if proof:
        inputs |= {
            "commit": proof.commit,
            "short": proof.short,
            "date": proof.date,
            "modified": "true" if proof.modified else "false",
        }
        if proof.copy is not None:
            inputs |= {"copy": str(proof.copy), "recipient": proof.recipient or ""}
    drawn = stage / ".quirework-cover.png"
    command = [quarto, "typst", "compile", str(typ), str(drawn)]
    command += ["--root", str(stage), "--ppi", str(COVER_PPI)]
    for key, value in inputs.items():
        command += ["--input", f"{key}={value}"]
    done = _typeset(command)
    if done.returncode:
        print(f"render: the cover did not compile:\n{done.stderr}", file=sys.stderr)
        drawn.unlink(missing_ok=True)
        return 1
    try:
        from PIL import Image, PngImagePlugin
    except ImportError:
        print("render: the cover needs Pillow to write its image", file=sys.stderr)
        drawn.unlink(missing_ok=True)
        return 1
    out = stage / target
    mark = f"{COVER_MARK} {proof.commit}" if proof else ""
    with Image.open(drawn) as image:
        if out.suffix.lower() == ".png":
            info = PngImagePlugin.PngInfo()
            if mark:
                info.add_text("Comment", mark)
            image.save(out, pnginfo=info)
        else:
            image.convert("RGB").save(out, quality=95, comment=mark.encode())
    drawn.unlink()
    return 0


# --------------------------------------------------------------------------- #
# the library title of an EPUB or a DOCX
# --------------------------------------------------------------------------- #
_DC_TITLE = re.compile(r"(<dc:title\b[^>]*>)(.*?)(</dc:title>)", re.S)


def retitle(site: Path, stage: Path) -> list[Path]:
    """Give a proof's EPUB and DOCX their library title.

    The title a library lists comes from the package's own metadata, and Pandoc
    writes the book's title there and on the visible title page from one value,
    so a book cannot change one without the other. The first `dc:title` of the
    EPUB's package document, and the DOCX's core title, take the stage's
    `proof-title` after the render instead; the pages keep the book's title.
    Returns the files retitled."""
    proof = proof_of(stage)
    if proof is None:
        return []
    done = []
    escaped = html.escape(proof.title, quote=False)
    for path in (site / f"{proof.name}.epub", site / f"{proof.name}.docx"):
        if not path.is_file():
            continue
        with zipfile.ZipFile(path) as z:
            if path.suffix == ".epub":
                root = re.search(
                    rb'full-path="([^"]+)"', z.read("META-INF/container.xml")
                )
                entry = root.group(1).decode() if root else ""
            else:
                entry = "docProps/core.xml"
            text = z.read(entry).decode("utf-8")
        text, count = _DC_TITLE.subn(
            lambda m: m.group(1) + escaped + m.group(3), text, count=1
        )
        if count:
            _bookstage.repack_zip(path, {entry: text.encode("utf-8")})
            done.append(path)
    return done


# --------------------------------------------------------------------------- #
# reading the artifacts back
# --------------------------------------------------------------------------- #
def _bare(text: str) -> str:
    """Text as compared: compatibility-composed, straight apostrophes, no soft
    hyphens or whitespace. That is how a token survives a PDF's extraction of
    set type, which splits tracked capitals with spaces, can give a hyphen as
    a soft one, and gives a ligature as one character: "Sofia" set with an fi
    ligature extracts with U+FB01 in it, which only the compatibility form
    reads as the name it is."""
    text = unicodedata.normalize("NFKC", text).replace("’", "'")
    return re.sub(r"\s+", "", text.replace("\xad", ""))


_BODY = re.compile(rb"<body\b[^>]*>(.*)</body>", re.S | re.I)
_NOT_TEXT = re.compile(rb"<(script|style)\b.*?</\1>", re.S | re.I)


def _xml_text(blob: bytes) -> str:
    """The words of a page's body, or of the whole file when it has none (a
    DOCX part): a stylesheet in a page's head is not something a reader sees."""
    body = _BODY.search(blob)
    blob = _NOT_TEXT.sub(b" ", body.group(1) if body else blob)
    return html.unescape(re.sub(rb"<[^>]+>", b" ", blob).decode("utf-8"))


def _check_pdf(path: Path, proof: Proof) -> list[str]:
    import pypdf

    pages = pypdf.PdfReader(str(path)).pages
    texts = [_bare(page.extract_text() or "") for page in pages]
    token = _bare(proof.short)
    missing = [i + 1 for i, text in enumerate(texts) if token not in text]
    problems = []
    if missing:
        shown = ", ".join(map(str, missing[:12])) + (
            " ..." if len(missing) > 12 else ""
        )
        problems.append(
            f"{len(missing)} of {len(texts)} pages do not name {proof.short} "
            f"(pages {shown})"
        )
    if proof.recipient and texts and _bare(proof.recipient) not in texts[0]:
        problems.append(f"the first page does not name {proof.recipient}")
    return problems


def _check_epub(path: Path, proof: Proof) -> list[str]:
    problems = []
    token = _bare(proof.short)
    with zipfile.ZipFile(path) as z:
        root = re.search(rb'full-path="([^"]+)"', z.read("META-INF/container.xml"))
        opf_name = root.group(1).decode() if root else ""
        opf = z.read(opf_name).decode("utf-8")
        base = opf_name.rpartition("/")[0]

        def entry(href: str) -> str:
            return f"{base}/{href}" if base else href

        title = _DC_TITLE.search(opf)
        if not title or token not in _bare(html.unescape(title.group(2))):
            problems.append(f"the library title does not name {proof.short}")
        items = {
            m.group("id"): m.group(0)
            for m in re.finditer(r"<item\b(?=[^>]*\bid=\"(?P<id>[^\"]+)\")[^>]*>", opf)
        }

        def href(item: str) -> str:
            m = re.search(r'\bhref="([^"]+)"', item)
            return m.group(1) if m else ""

        unstamped, named = [], False
        for idref in re.findall(r'<itemref\b[^>]*\bidref="([^"]+)"', opf):
            text = _bare(_xml_text(z.read(entry(href(items.get(idref, ""))))))
            if not text:
                continue  # a page that is only a picture, such as the cover
            if token not in text:
                unstamped.append(href(items[idref]).rpartition("/")[2])
            if proof.recipient and _bare(proof.recipient) in text:
                named = True
        if unstamped:
            problems.append(
                f"{len(unstamped)} reading-order documents do not name "
                f"{proof.short} ({', '.join(unstamped[:8])}"
                + (" ..." if len(unstamped) > 8 else "")
                + ")"
            )
        if proof.recipient and not named:
            problems.append(f"no page names {proof.recipient}")
        cover = next((i for i in items.values() if "cover-image" in i), None)
        if cover and not _cover_marked(z.read(entry(href(cover))), proof):
            problems.append("the cover is not the proof's own cover")
    return problems


def _cover_marked(blob: bytes, proof: Proof) -> bool:
    """Whether a cover image carries this proof's mark, without Pillow: the
    mark is a JPEG comment or a PNG text chunk, both plain bytes in the file."""
    return f"{COVER_MARK} {proof.commit}".encode() in blob


def _check_docx(path: Path, proof: Proof) -> list[str]:
    problems = []
    token = _bare(proof.short)
    with zipfile.ZipFile(path) as z:
        body = _bare(_xml_text(z.read("word/document.xml")))
        core = z.read("docProps/core.xml").decode("utf-8")
    title = _DC_TITLE.search(core)
    if not title or token not in _bare(html.unescape(title.group(2))):
        problems.append(f"the document title does not name {proof.short}")
    if token not in body:
        problems.append(f"the text does not name {proof.short}")
    if proof.recipient and _bare(proof.recipient) not in body:
        problems.append(f"the text does not name {proof.recipient}")
    return problems


def _check_html(site: Path, proof: Proof) -> tuple[list[str], int]:
    token = _bare(proof.short)
    pages = [p for p in sorted(site.rglob("*.html")) if "site_libs" not in p.parts]
    missing = [
        str(p.relative_to(site))
        for p in pages
        if token not in _bare(_xml_text(p.read_bytes()))
    ]
    if missing:
        return [
            f"{len(missing)} of {len(pages)} pages do not name {proof.short} "
            f"({', '.join(missing[:8])}" + (" ..." if len(missing) > 8 else "") + ")"
        ], len(pages)
    return [], len(pages)


def verify(site: Path, rendered: list[tuple[Path, list[str]]]) -> int:
    """Read each proof the render made, and stop the build if one does not say
    what it is.

    `rendered` pairs each stage the render ran with the formats it rendered
    there, and each artifact is checked against its own stage's revision. A
    proof's file is named for it; its short commit is on every page of a PDF,
    in every reading-order document of an EPUB but its picture pages, in the
    text of a DOCX and on every HTML page; its library title names it; and an
    EPUB's cover is the proof's own. A copy for one person also names them: on
    a PDF's first page, somewhere in an EPUB, in a DOCX's text. A format this
    cannot read is named and not failed. Returns 0 when nothing is a proof."""
    failed = False
    for stage, formats in rendered:
        proof = proof_of(stage)
        if proof is None:
            if is_proof(stage):
                print(f"proof: {stage.name} was never prepared", file=sys.stderr)
                failed = True
            continue
        for fmt in formats:
            if fmt == "html":
                problems, count = _check_html(site, proof)
                shown = f"{count} HTML pages"
            elif fmt in EXTENSIONS:
                path = site / f"{proof.name}{EXTENSIONS[fmt]}"
                shown = path.name
                if not path.is_file():
                    problems = ["it was not written under its proof name"]
                else:
                    check = {".pdf": _check_pdf, ".epub": _check_epub}.get(
                        path.suffix, _check_docx
                    )
                    problems = check(path, proof)
            else:
                print(f"proof: {fmt} is not read back; check it by eye")
                continue
            if problems:
                failed = True
                for problem in problems:
                    print(f"proof: {shown}: {problem}", file=sys.stderr)
            else:
                print(f"proof: {shown} names {proof.short} throughout")
    if failed:
        print(
            "proof: a proof that does not say what it is can pass for the "
            "published book. Fix whatever stopped printing the stamp.",
            file=sys.stderr,
        )
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
# `quirework publish copy`: a proof prepared for one person
# --------------------------------------------------------------------------- #
HELP = """\
usage: quirework publish copy --for NAME [--to FMT[,FMT...]] [--any-font]

Render a proof prepared for one person. The copy takes the next number in
the log of copies, prints the person's name where the book prints it, and
lands in build/copies/ under a file name that carries the number, never the
name. Refused while the book is published, while its sources have changes
not yet committed, and until the per-user configuration names the log.

options:
  -h, --help        show this help message and exit
  --for NAME        whom the copy is for, as the book should print it
  --to FMT[,FMT..]  the formats to make (default: every declared format
                    but HTML, which is a site and not a copy)
  --any-font        render even if a required typeface is missing

The log is `copies.log` in ~/.config/quirework/config.toml: a file of one
JSON record per line, outside the project. It names people, so it never
belongs where a commit could carry it.
"""


def _log_path() -> Path:
    configured = user_settings().get("copies", {}).get("log")
    if not isinstance(configured, str) or not configured:
        raise ValueError(
            f"set copies.log in {user_config_path()} to the file that keeps the "
            "log of copies, outside the project"
        )
    path = Path(configured).expanduser()
    if not path.is_absolute():
        raise ValueError(f"copies.log must be an absolute path, not {configured!r}")
    root = current_project().root.resolve()
    if path.resolve().is_relative_to(root):
        raise ValueError(
            "copies.log is inside the project, where a commit could carry the "
            "names it keeps; move it outside"
        )
    return path


def _records(handle) -> list[dict]:
    handle.seek(0)
    records = []
    for number, line in enumerate(handle.read().splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            record = None
        if not isinstance(record, dict) or not isinstance(record.get("copy"), int):
            raise ValueError(
                f"line {number} of the log of copies is not a record; fix it "
                "before numbering another copy"
            )
        records.append(record)
    return records


@contextlib.contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield handle
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _append(path: Path, record: dict) -> int | None:
    """Append `record` under the log's lock; a record without a number takes
    the next one, which is returned."""
    with _locked(path) as handle:
        if record.get("copy") is None:
            known = [r["copy"] for r in _records(handle)]
            record = {"copy": max(known, default=0) + 1, **record}
        handle.seek(0, 2)
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
    return record["copy"]


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _parse(argv: list[str]) -> tuple[str | None, list[str] | None, bool]:
    recipient, formats, any_font = None, None, False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--for", "--to") and i + 1 < len(argv):
            value, i = argv[i + 1], i + 2
        elif a.startswith(("--for=", "--to=")):
            a, _, value = a.partition("=")
            i += 1
        elif a == "--any-font":
            any_font, i = True, i + 1
            continue
        else:
            raise ValueError(f"unknown argument {a!r} (see --help)")
        if a == "--for":
            recipient = value
        else:
            formats = [f.strip() for f in value.split(",") if f.strip()]
    return recipient, formats, any_font


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if any(a in ("-h", "--help") for a in argv):
        print(HELP, end="")
        return 0
    from quirework.publish import render as _render

    try:
        recipient, formats, any_font = _parse(argv)
        if not recipient or not recipient.strip() or "\n" in recipient:
            raise ValueError("name the person the copy is for: --for NAME")
        recipient = " ".join(recipient.split())
        log = _log_path()
        source = current_project().path("manuscript.source")
        if is_proof(source) is None:
            raise ValueError(
                "a copy for one person is a proof, and the render policy names "
                "no proof_switch"
            )
        if not is_proof(source):
            raise ValueError(
                "the book is published; its copies need no number and no name"
            )
        declared = _render.book_formats()
        formats = formats or [f for f in declared if f != "html"]
        if "html" in formats:
            raise ValueError("HTML is a site, not a copy for a person")
        unknown = [f for f in formats if f not in declared]
        if unknown or not formats:
            raise ValueError(
                f"no declared format {', '.join(unknown) or 'to make'} "
                f"(declared: {', '.join(declared)})"
            )
        revision = _render.source_revision()
        if revision is None:
            raise ValueError("a copy names its commit, and this is not a git checkout")
        if revision["modified"]:
            raise ValueError(
                "the sources have changes not yet committed; a copy for a person "
                "is exactly one commit, so commit or set them aside first"
            )
        quarto = _render.find_quarto()
        if not quarto:
            raise ValueError("quarto is not found")
    except ValueError as exc:
        print(f"copy: {exc}", file=sys.stderr)
        return 2

    try:
        number = _append(
            log,
            {
                "event": "started",
                "recipient": recipient,
                "commit": revision["commit"],
                "formats": formats,
                "project": str(current_project().root),
                "at": _now(),
            },
        )
    except (OSError, ValueError) as exc:
        print(f"copy: cannot number the copy in {log}: {exc}", file=sys.stderr)
        return 1
    print(f"copy: copy {number}, prepared for {recipient}", flush=True)

    token = _COPY.set({"number": number, "recipient": recipient})
    try:
        rc = _render.render(quarto, ["--to", ",".join(formats)], any_font)
        stages = [
            _render._settings().BUILD_BOOK_PRINT,
            _render._settings().BUILD_BOOK_NOPARTS,
        ]
        proofs = [p for p in map(proof_of, stages) if p and p.copy == number]
    finally:
        _COPY.reset(token)
    if not rc and any(p.modified for p in proofs):
        print(
            "copy: the sources changed while the copy was made, so it is not "
            "the commit it names",
            file=sys.stderr,
        )
        rc = 1
    site = _render._options().BUILD_BOOK / _render.SITE
    made = (
        sorted(
            {site / f"{p.name}{EXTENSIONS[f]}" for p in proofs for f in formats}
            & {path for path in site.iterdir() if path.is_file()}
        )
        if site.is_dir()
        else []
    )
    if rc or len(made) != len(formats):
        _append(log, {"copy": number, "event": "failed", "at": _now()})
        print(f"copy: copy {number} was not made", file=sys.stderr)
        return rc or 1

    out = current_project().path("publish.build", "build") / "copies"
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for path in made:
        dest = out / path.name
        shutil.move(path, dest)
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        files.append(
            {"name": dest.name, "sha256": digest, "bytes": dest.stat().st_size}
        )
    _append(log, {"copy": number, "event": "issued", "files": files, "at": _now()})
    try:
        shown = out.relative_to(current_project().root)
    except ValueError:
        shown = out
    for f in files:
        print(f"copy: {shown}/{f['name']}")
    print(f"copy: copy {number} is in the log at {log}")
    return 0
