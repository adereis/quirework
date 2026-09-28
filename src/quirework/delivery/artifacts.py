"""Explicit delivery transforms, independent of exposure and session topology.

Text is not silently cleaned or summarized. Source shaping is a preparation
choice; arbitrary files remain opaque unless their declared delivery needs a
conversion. A registry allows further transports without editing the engine.
"""

import shutil
import subprocess
from pathlib import Path

from quirework.runs.storage import digest
from quirework.runs.storage import relative

TRANSFORMS = {}


def register(name):
    def install(function):
        if name in TRANSFORMS:
            raise ValueError(f"duplicate delivery transform: {name}")
        TRANSFORMS[name] = function
        return function

    return install


def copy(source, target):
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"material must be a regular file: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target


@register("text")
def text(source, directory):
    source.read_text(encoding="utf-8")  # Reject a binary pretending to be text.
    return [("text", copy(source, directory / "material.txt"))]


@register("image")
def image(source, directory):
    # SVG is not a native image attachment in these adapters. Make conversion
    # explicit with delivery=svg; never claim that staging an SVG showed it.
    if source.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        raise ValueError(
            "image delivery requires PNG, JPEG or WebP; use svg for conversion"
        )
    return [("image", copy(source, directory / ("image" + source.suffix.lower())))]


@register("svg")
def svg(source, directory):
    frozen = copy(source, directory / "source.svg")
    dest = directory / "image.png"
    subprocess.run(
        ["rsvg-convert", "-w", "1600", "-o", str(dest), str(frozen)],
        check=True,
        capture_output=True,
    )
    return [("image", dest)]


@register("pages")
def pages(source, directory):
    from pypdf import PdfReader

    frozen = copy(source, directory / "source.pdf")
    subprocess.run(
        ["pdftoppm", "-png", "-scale-to", "1600", str(frozen), str(directory / "page")],
        check=True,
        capture_output=True,
    )
    files = sorted(
        directory.glob("page-*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[1])
    )
    if not files or len(files) != len(PdfReader(frozen).pages):
        raise RuntimeError("incomplete PDF page rasterization")
    return [("image", p) for p in files]


@register("file")
def file(source, directory):
    return [("file", copy(source, directory / ("material" + source.suffix)))]


def prepare(materials, origin, destination, cache=None):
    """Freeze the exact source plus derived delivery bytes, with neutral names."""
    if not isinstance(materials, list):
        raise ValueError("materials must be a list")
    result = []
    for index, item in enumerate(materials):
        if not isinstance(item, dict) or set(item) - {"source", "delivery", "label"}:
            raise ValueError("material fields: source, delivery, optional label")
        if item.get("delivery") not in TRANSFORMS:
            raise ValueError(f"unknown delivery: {item.get('delivery')}")
        if "label" in item and (
            not isinstance(item["label"], str) or not item["label"].strip()
        ):
            raise ValueError("material label must contain text")
        source = Path(item["source"]).expanduser()
        source = source if source.is_absolute() else origin / source
        directory = destination / f"asset-{index:03d}"
        directory.mkdir(parents=True)
        before = digest(source)
        key = (item["delivery"], before, source.suffix.lower())
        cached = cache.get(key) if cache is not None else None
        # Composite prose/figure transforms have multiple source dependencies;
        # don't cache them under the chapter hash alone.
        cacheable = item["delivery"] not in ("prose", "prose-images")
        if cached and cacheable:
            previous, output_items = cached
            shutil.copytree(previous, directory, dirs_exist_ok=True)
            values = [(kind, directory / rel) for kind, rel in output_items]
        else:
            values = TRANSFORMS[item["delivery"]](source, directory)
            if cache is not None and cacheable:
                cache[key] = (
                    directory,
                    [(kind, str(p.relative_to(directory))) for kind, p in values],
                )
        if digest(source) != before:
            raise RuntimeError(f"source changed while freezing: {source}")
        for kind, path in values:
            result.append(
                dict(
                    kind=kind,
                    path=str(path.relative_to(destination)),
                    sha256=digest(path),
                    source_sha256=before,
                    delivery=item["delivery"],
                    label=item.get("label"),
                )
            )
    return result


def stage(items, frozen, destination, virtual):
    """Only the declared current turn is copied; return prompt and attachments."""
    destination.mkdir(parents=True, exist_ok=True)
    chunks, images, files = [], [], []
    for index, item in enumerate(items):
        source = frozen / relative(item["path"])
        path = destination / f"material-{index:03d}{source.suffix}"
        copy(source, path)
        shown = virtual / path.name
        if item["kind"] == "text":
            label = f": {item['label']}" if item.get("label") else ""
            chunks.append(
                f"\n=== MATERIAL {index + 1}{label} ===\n{path.read_text()}\n=== END MATERIAL ==="
            )
        elif item["kind"] == "image":
            images.append((path, shown))
            if item.get("label"):
                chunks.append(f"\nAttached image {len(images)}: {item['label']}\n")
        else:
            files.append((path, shown))
            if item.get("label"):
                chunks.append(f"\nFile {shown}: {item['label']}\n")
    if files:
        chunks.append(
            "\nRead the supplied files at these exact paths; do not search for them:\n"
            + "\n".join(str(v) for _, v in files)
        )
    return "\n".join(chunks), images, files


@register("prose")
def prose(source, directory):
    from quirework.delivery.text import reader_markdown

    frozen = copy(source, directory / ("source" + source.suffix))
    text, figures = reader_markdown(frozen.read_text())
    for figure in figures:
        text = text.replace(
            f"![{figure['caption']}](../{figure['file']})",
            f"[Figure caption: {figure['caption']}]",
        )
    dest = directory / "material.md"
    dest.write_text(text)
    return [("text", dest)]


@register("prose-images")
def prose_images(source, directory):
    from quirework.delivery.text import reader_markdown

    # The figures travel as images, so their alt text would only restate
    # them in words, and a seat could rate a figure from that without ever
    # looking at it. Text-only `prose` keeps it: there the description is
    # the only account of the figure the seat gets.
    frozen = copy(source, directory / ("source" + source.suffix))
    text, figures = reader_markdown(frozen.read_text(), alt=False)
    dest = directory / "material.md"
    dest.write_text(text)
    result = [("text", dest)]
    for index, figure in enumerate(figures):
        # A figure travels from beside the page that embeds it. A site-root
        # path or a URL names something only the published site can find.
        if "://" in figure["source"] or Path(figure["source"]).is_absolute():
            raise ValueError(
                f"{source.name}: figure {figure['source']} is not a file beside "
                "the page, so it cannot be delivered as an image; deliver the "
                "page as text, or its figures as their own materials"
            )
        original = source.parent / figure["source"]
        transform = svg if original.suffix.lower() == ".svg" else image
        result += transform(original, directory / f"figure-{index:03d}")
    return result
