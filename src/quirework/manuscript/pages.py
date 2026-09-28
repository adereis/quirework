"""The `pages` manuscript format: a site's Markdown pages.

`manuscript.pages` is a glob under `manuscript.source`, the shape a static
site generator such as Hugo reads. A page named `index.md` takes its slug from
its directory (a page bundle), any other page from its stem. Order is the
frontmatter `date`, and a page's `draft` flag is carried along. Pages carry no
number.

A page's marks live at `manuscript.marks`, a path pattern with `{slug}` in it,
which should sit outside the rendered tree: anything inside it is the site
generator's to read, and marks are metadata a reader must never be served.
"""

from __future__ import annotations

from pathlib import Path

from quirework.core import frontmatter
from quirework.manuscript import formats
from quirework.manuscript.formats import SKIP_NAMES, SLUG_OK, Format, Unit
from quirework.project import ConfigurationError, Project, current_project


def _glob() -> str:
    pattern = current_project().get("manuscript.pages")
    if not pattern:
        raise ConfigurationError(
            "manuscript.format = 'pages' needs manuscript.pages, a glob under "
            "manuscript.source such as 'posts/*/index.md'"
        )
    return pattern


def _marks() -> str | None:
    """`manuscript.marks`, or None when the project keeps no marks."""
    return formats.slug_pattern("manuscript.marks")


def _page(path: Path, base: Path) -> Unit:
    text = path.read_text(encoding="utf-8")
    try:
        meta = frontmatter.fields(text)
    except ValueError as exc:
        raise ValueError(f"{formats.rel(path)}: {exc}") from None
    title = frontmatter.as_text(meta.get("title")) or formats.h1(text)[0]
    slug = FORMAT.slug_of(path)
    return Unit(
        slug,
        str(path.relative_to(base)),
        title or slug,
        None,
        frontmatter.as_text(meta.get("date")),
        bool(meta.get("draft", False)),
    )


class Pages(Format):
    name = "pages"
    noun = "page"

    def validate(self, project: Project) -> None:
        # A glob, not a path: it names the pages under the source, and matching
        # nothing is legitimate for a site with no posts yet.
        _glob()
        _marks()

    def registry(self, docs: Path) -> list[Unit]:
        """Every page, oldest first; an undated page after the dated ones."""
        base = formats.base(docs)
        pages = [_page(p, base) for p in self.prose_files(docs)]
        return sorted(pages, key=lambda u: (u.date is None, u.date or "", u.slug))

    def order_problems(self, docs: Path) -> list[str]:
        """Two pages sharing a slug, or a slug no reference could name."""
        from quirework.manuscript import units

        problems: list[str] = []
        seen: dict[str, str] = {}
        for u in self.registry(docs):
            if u.slug in seen:
                problems.append(
                    f"{u.rel}: slug '{u.slug}' is also {seen[u.slug]}'s; a slug "
                    f"is an identity and names one {units.noun()}"
                )
            seen.setdefault(u.slug, u.rel)
            if not SLUG_OK.match(u.slug):
                problems.append(
                    f"{u.rel}: slug '{u.slug}' is not lowercase words joined by "
                    f"hyphens, so no @ch() reference can name it"
                )
        return problems

    def prose_files(self, docs: Path) -> list[Path]:
        """The pages under ``docs``. A Hugo branch bundle's `_index.md` is a
        list page, and other `_`-prefixed files are partials, so neither is a
        unit."""
        return sorted(
            p
            for p in docs.glob(_glob())
            if p.is_file() and not p.name.startswith("_") and p.name not in SKIP_NAMES
        )

    def slug_of(self, path: Path) -> str:
        """A page bundle's `index.md` is named by its directory; a page file by
        its stem."""
        path = Path(path)
        return path.parent.name if path.stem == "index" else path.stem

    def marks_path(self, slug: str, docs: Path) -> Path | None:
        pattern = _marks()
        if pattern is None:
            return None
        return current_project().root / pattern.replace("{slug}", slug)

    def sidecars(self, docs: Path) -> list[Path]:
        pattern = _marks()
        if pattern is None:
            return []
        root = current_project().root
        return sorted(
            p for p in root.glob(pattern.replace("{slug}", "*")) if p.is_file()
        )

    def sidecar_slug(self, path: Path) -> str | None:
        pattern = _marks()
        if pattern is None:
            return None
        root = current_project().root
        try:
            relative = Path(path).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return None
        m = formats.pattern_regex(pattern).match(relative)
        return m.group("slug") if m else None

    def prose_for_sidecar(self, path: Path, docs: Path) -> Path | None:
        slug = self.sidecar_slug(path)
        if slug is None:
            return None
        for p in self.prose_files(docs):
            if self.slug_of(p) == slug:
                return p
        return None

    def harvest(self, docs: Path) -> list[Path]:
        # File by file: the sidecars live outside the source, and a site's
        # source holds much besides the manuscript.
        return self.prose_files(docs) + self.sidecars(docs)

    def default_layers(self, project: Project) -> tuple[str, ...]:
        pattern = _marks()
        return (pattern.replace("{slug}", "*"),) if pattern else ()

    def footprint(self, project: Project) -> tuple[str, ...]:
        # The pages, each bundle whole (its figures are the post's too), and
        # the marks. Not the whole source: a site's source holds list pages,
        # taxonomies and whatever else the generator reads.
        source = formats.relative(project, "manuscript.source")
        pattern = project.get("manuscript.pages")
        out: list[str] = []
        if source is not None and pattern:
            prefix = "" if source == "." else f"{source}/"
            out.append(prefix + pattern)
            head, _, name = pattern.rpartition("/")
            if name == "index.md" and head:
                out.append(f"{prefix}{head}/**")
        marks = project.get("manuscript.marks")
        if marks:
            out.append(str(marks).replace("{slug}", "*"))
        return tuple(out)

    def listing(self, units: list[Unit]) -> list[str]:
        return [
            f"  {u.date or '-':<25} {'draft' if u.draft else '     '}  "
            f"{u.slug:34} {u.title}"
            for u in units
        ]


FORMAT = Pages()
