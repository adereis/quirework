"""Every setting Quirework reads, and every key of the policy files it reads.

A setting a project leaves undeclared takes its default and says nothing,
which is how an optional setting should behave and why a new one is easy to
miss: a project that does not know the key exists never learns of it. So
the keys are listed here, and `quirework config keys` sets them against
what a project declares: each known key declared or not, and each declared
key Quirework does not know, which is usually a misspelling.

A key is written dotted, as `quirework.toml` nests it. A `*` stands for one
name the project chooses, such as an edition's kind or a reader preset's
name. A setting that names a policy file has the keys that file may hold in
`POLICIES`, written the same way from the top of the file.

This module names keys; `docs/configuration.md` says what each one means.
Quirework's own suite records every key the code reads and fails on one
that is not listed here (`recording`), so the list cannot fall behind.
"""

from __future__ import annotations

# The keys of [publish] a publish target's own table may also declare: a
# target is read as its [publish], each key it names replacing the shared one.
_PUBLISH = (
    "backend",
    "build",
    "figure_checks",
    "figure_fonts",
    "figure_type_floor_pt",
    "figures",
    "grayscale_transform",
    "layout_policy",
    "render_policy",
    "site",
    "typst_template",
    "variant_policy",
    "variants",
)

SETTINGS: tuple[str, ...] = (
    "schema",
    "checks.command",
    *(
        f"manuscript.{key}"
        for key in (
            "chapter_policy",
            "chapters",
            "collections.*.order",
            "collections.*.pages",
            "companion_anchors",
            "companion_dirs",
            "format",
            "ideas",
            "marks",
            "noncanonical_roots",
            "pages",
            "quarto",
            "references",
            "review_notes",
            "revision_field",
            "source",
            "source_copies",
            "sources",
            "spec",
            "spec_trail",
            "spec_trail_leads",
            "unit_name",
            "unit_spec",
        )
    ),
    "project.cache",
    "project.name",
    # Documents the project's private space; nothing reads it.
    "project.private",
    "project.scratchpad",
    *(
        f"provenance.{key}"
        for key in (
            "coldread",
            "coldread_policy",
            "enabled",
            "human_input",
            "metrics_policy",
            "redaction_markers",
            "redaction_policy",
            "redpen",
            "rendered",
            "reports",
            "root",
            "store",
            "store_dir",
        )
    ),
    # Per runner: claude, codex or agy.
    "providers.*.account",
    "providers.*.state",
    "providers.agy.sessions",
    "providers.canary_models.*",
    "providers.claude.models",
    "providers.claude.sessions",
    "providers.codex.catalog",
    "providers.isolation_policy",
    *(f"publish.{key}" for key in _PUBLISH),
    "publish.targets.*.copy",
    "publish.targets.*.needs",
    *(f"publish.targets.*.{key}" for key in _PUBLISH),
    "runs.evidence_roots",
    "runs.reviews",
    "runs.store",
    "sops.catalog",
    "sops.default_reader",
    "sops.instruments",
    "sops.operator_guide",
    "sops.panels",
    "sops.persona_policy",
    "sops.personas",
    "sops.reader_driver",
    *(
        f"sops.reader_presets.*.{key}"
        for key in ("ask", "delivery", "include_matter", "instrument", "wrap_ask")
    ),
    "sops.review_policy",
    "sops.root",
    "sops.roster",
    "sops.skill_prefix",
    "text.classifier",
    "text.modes",
    "text.terminology",
    "text.tells_policy",
    "usage.enabled",
    "usage.store",
)

POLICIES: dict[str, tuple[str, ...]] = {
    "manuscript.chapter_policy": ("metadata_layers",),
    "provenance.coldread_policy": ("cli_model", "doc_dirs", "doc_prefixes", "model"),
    "provenance.metrics_policy": (
        "canonical_dir",
        "essay_paths",
        "output_counts.*",
        "source_of_truth",
    ),
    "provenance.redaction_policy": ("markers",),
    "providers.isolation_policy": ("mount_markers", "project_markers"),
    "publish.layout_policy": ("typst_epilogues.*", "typst_prologues.*"),
    "publish.render_policy": ("epub_style_headers", "print_drops"),
    "publish.variant_policy": (
        "blind_tells",
        "default_effort",
        "default_harness",
        "default_model",
        "document_intro",
        "prompt_intro",
        *(f"kinds.*.{key}" for key in ("effort", "harness", "model", "omit", "shape")),
    ),
    "sops.persona_policy": ("default",),
    "sops.review_policy": (
        "frame",
        "hidden",
        "own_output",
        "procedure_frame",
        "reviewable",
    ),
    "text.terminology": ("allow", "exclude_names", "retired", "scope_glob", "terms.*"),
    "text.tells_policy": ("contrast", "exclude_names", "phrases.*", "scope_glob"),
}


def _parts(key: str) -> tuple[str, ...]:
    return tuple(key.split(".")) if key else ()


def _matches(pattern: tuple[str, ...], path: tuple[str, ...]) -> bool:
    return len(pattern) == len(path) and all(
        p == "*" or p == q for p, q in zip(pattern, path)
    )


def _leads_to(pattern: tuple[str, ...], path: tuple[str, ...]) -> bool:
    """Whether ``path`` is ``pattern`` or a table on the way to it."""
    return len(path) <= len(pattern) and _matches(pattern[: len(path)], path)


def known(path, patterns) -> bool:
    """Whether ``patterns`` name this key, a table on the way to one, or a
    name below a pattern that ends in ``*``, whose contents are the project's."""
    path = _parts(path) if isinstance(path, str) else tuple(path)
    for pattern in map(_parts, patterns):
        if _leads_to(pattern, path):
            return True
        if pattern[-1:] == ("*",) and _matches(pattern, path[: len(pattern)]):
            return True
    return False


# --------------------------------------------------------------------------- #
# Recording what the code reads, for Quirework's own suite
# --------------------------------------------------------------------------- #

_seen: set[tuple[str, str]] | None = None


def recording() -> None:
    """Start noting every key read through a project, for `unregistered`."""
    global _seen
    _seen = set()


def unregistered() -> list[str]:
    """The keys read since `recording` that this module does not list."""
    found = []
    for where, key in sorted(_seen or ()):
        patterns = SETTINGS if where == "" else POLICIES.get(where, ())
        if not known(key, patterns):
            found.append(f"{where}: {key}" if where else key)
    return found


def note(where: str, key: str) -> None:
    if _seen is not None:
        _seen.add((where, key))


def watched(value, where: str, prefix: str):
    """``value``, made to note the keys read out of it, while recording."""
    if _seen is None or not isinstance(value, dict):
        return value
    return Watched(value, where, prefix)


class Watched(dict):
    """A table that notes each key the code names when reading it.

    Only a named read counts: iterating a table reads names the project
    chose, not keys Quirework knows. A table reached inside it, by name or
    by iterating, is watched too, under the longer prefix, and replaces the
    plain one in place, so writing through it is seen by the next read.
    """

    def __init__(self, value, where, prefix):
        super().__init__(value)
        self._where, self._prefix = where, prefix

    def _named(self, key):
        note(self._where, f"{self._prefix}.{key}" if self._prefix else str(key))

    def _inner(self, key):
        value = super().__getitem__(key)
        if isinstance(value, dict) and not isinstance(value, Watched):
            prefix = f"{self._prefix}.{key}" if self._prefix else str(key)
            value = Watched(value, self._where, prefix)
            super().__setitem__(key, value)
        return value

    def __getitem__(self, key):
        self._named(key)
        return self._inner(key)

    def get(self, key, default=None):
        self._named(key)
        if super().__contains__(key):
            return self._inner(key)
        # A table the project left out still has its keys read, from the
        # caller's default, and those reads count as much as any.
        prefix = f"{self._prefix}.{key}" if self._prefix else str(key)
        return watched(default, self._where, prefix)

    def __contains__(self, key):
        self._named(key)
        return super().__contains__(key)

    def setdefault(self, key, default=None):
        self._named(key)
        if not super().__contains__(key):
            super().__setitem__(key, default)
        return self._inner(key)

    def pop(self, key, *default):
        self._named(key)
        return super().pop(key, *default)

    def values(self):
        return [self._inner(key) for key in self]

    def items(self):
        return [(key, self._inner(key)) for key in self]


# --------------------------------------------------------------------------- #
# What a project declares, against what Quirework knows
# --------------------------------------------------------------------------- #


def _declared(table, patterns, prefix=()):
    """Every declared key in ``table``, as a tuple of names.

    A table is walked into until a key a pattern names exactly, since that
    setting's value may itself be a table, or a name below a pattern ending
    in ``*``, whose value is the project's own."""
    for name, value in table.items():
        path = (*prefix, name)
        whole = any(_matches(_parts(p), path) for p in patterns)
        if isinstance(value, dict) and value and not whole:
            yield from _declared(value, patterns, path)
        else:
            yield path


def _compare(table, patterns):
    keys = list(_declared(table, patterns))
    declared = sorted(
        p
        for p in patterns
        if any(_matches(_parts(p), k[: len(_parts(p))]) for k in keys)
    )
    return {
        "declared": declared,
        "undeclared": sorted(set(patterns) - set(declared)),
        "unknown": sorted(".".join(k) for k in keys if not known(k, patterns)),
    }


def report(project) -> dict:
    """Each known setting and policy key, declared by ``project`` or not, and
    each it declares that Quirework does not know."""
    found = {"settings": _compare(project.describe()["settings"], SETTINGS)}
    policies = {}
    for setting, patterns in sorted(POLICIES.items()):
        if project.get(setting) is None:
            policies[setting] = {
                "file": None,
                "declared": [],
                "undeclared": sorted(patterns),
                "unknown": [],
            }
            continue
        data = project.data(setting, {})
        policies[setting] = {
            "file": str(project.path(setting)),
            **_compare(data if isinstance(data, dict) else {}, patterns),
        }
    found["policies"] = policies
    return found


def show(project, *, undeclared_only=False) -> str:
    """`report` for a person: the settings first, then each policy file."""
    found = report(project)
    lines = []

    def section(rows, indent):
        kinds = (
            ("undeclared", "unknown")
            if undeclared_only
            else (
                "declared",
                "undeclared",
                "unknown",
            )
        )
        for kind in kinds:
            for key in rows[kind]:
                label = "unknown" if kind == "unknown" else kind
                lines.append(f"{indent}{label:11} {key}")

    lines.append("Settings (quirework.toml):")
    section(found["settings"], "  ")
    for setting, rows in found["policies"].items():
        where = rows["file"] or "not declared, so every key is undeclared"
        lines.append(f"{setting} ({where}):")
        section(rows, "  ")
    lines.append(
        "An unknown key is one Quirework never reads, usually a misspelling. "
        "docs/configuration.md explains every known one."
    )
    return "\n".join(lines)
