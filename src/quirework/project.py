"""Explicit project configuration and operation-local service context.

A Project is immutable and independent of the installed package location.
CLI and Python callers select it once per operation. Context variables carry
that selection through internal helpers without mutable module-level paths;
thread submissions capture the selection explicitly.
"""

from __future__ import annotations

import contextvars
import copy
import hashlib
import importlib
import json
import os
import tempfile
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from threading import RLock
from typing import Any, Iterator, Mapping

from quirework import settings as _settings


class ConfigurationError(ValueError):
    """A project configuration cannot be used as declared."""


# The configuration schema this release reads. Schema 1 kept everything about
# the written work in one [book] section; schema 2 splits it by job, so a
# project that never renders through Quirework still declares its spec.
SCHEMA = 2

# The top-level keys a project file may hold. Anything else is refused, so a
# misspelled or retired section fails loudly instead of being read as absent.
SECTIONS = frozenset(
    {
        "schema",
        "project",
        "manuscript",
        "publish",
        "text",
        "provenance",
        "sops",
        "runs",
        "providers",
        "checks",
        "extensions",
        "usage",
    }
)


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _plain(value):
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    return value


def _merge(base, overlay, origins, source, prefix=""):
    for key, value in overlay.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            if key in base and not isinstance(base[key], dict):
                raise ConfigurationError(
                    f"{source}: {name} changes a value into a table"
                )
            _merge(base.setdefault(key, {}), value, origins, source, name)
        else:
            base[key] = value
            origins[name] = str(source)


# Every key [provenance] reads. The section names paths into a private corpus,
# so a key nothing reads is refused rather than ignored: ignored, a misspelled
# or retired path would leave a command reading nothing and reporting an empty
# corpus as if it were the real one.
PROVENANCE_KEYS = frozenset(
    {
        "enabled",
        "root",
        "store",
        "store_dir",
        "coldread",
        "coldread_policy",
        "metrics_policy",
        "human_input",
        "redpen",
        "rulings",
        "redaction_policy",
        "redaction_markers",
        "rendered",
        "reports",
    }
)


def _check_provenance(values, origins):
    section = values.get("provenance") or {}
    unknown = sorted(set(section) - PROVENANCE_KEYS)
    if unknown:
        where = origins.get(f"provenance.{unknown[0]}", "configuration")
        raise ConfigurationError(
            f"{where}: unknown provenance keys: "
            + ", ".join(f"provenance.{key}" for key in unknown)
            + ". The configuration guide's 'Provenance' section lists the keys; "
            "captured sessions live in the store that provenance.store names"
        )


# Settings a release withdrew, each with what replaced it. Refused rather than
# ignored: a project still setting one believes something the tool no longer
# does, and an ignored key would let it go on believing it.
RETIRED = {
    "providers.validation_record": (
        "a launch now proves its runner blind at the exact installed version "
        "and keeps that record per machine, under the project's cache. Delete "
        "the setting, and the file it named"
    ),
}


def _check_retired(values, origins):
    for key, why in RETIRED.items():
        node = values
        for part in key.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if node is not None:
            where = origins.get(key, "configuration")
            raise ConfigurationError(f"{where}: {key} is retired: {why}")


@dataclass(frozen=True)
class Project:
    root: Path
    settings: Mapping[str, Any]
    origins: Mapping[str, str]
    config_file: Path | None = None
    _policy_cache: dict = field(default_factory=dict, compare=False, repr=False)
    _scratch: dict = field(default_factory=dict, compare=False, repr=False)
    _policy_lock: Any = field(default_factory=RLock, compare=False, repr=False)

    @classmethod
    def load(cls, source: str | Path | None = None, *, overrides=None):
        if source is None:
            explicit = os.environ.get("QUIREWORK_PROJECT")
            if explicit:
                return cls.load(explicit, overrides=overrides)
            cwd = Path.cwd().resolve()
            config = next(
                (
                    p / "quirework.toml"
                    for p in (cwd, *cwd.parents)
                    if (p / "quirework.toml").is_file()
                ),
                None,
            )
            root = config.parent if config else cwd
        else:
            candidate = Path(source).expanduser().resolve()
            config = candidate / "quirework.toml" if candidate.is_dir() else candidate
            if not config.is_file():
                raise ConfigurationError(f"project configuration not found: {config}")
            root = config.parent
        values, origins = {}, {}
        if config:
            for path in (config, root / "quirework.local.toml"):
                if path == config or path.exists():
                    try:
                        data = tomllib.loads(path.read_text(encoding="utf-8"))
                    except (OSError, tomllib.TOMLDecodeError) as exc:
                        raise ConfigurationError(f"cannot read {path}: {exc}") from exc
                    unknown = set(data) - SECTIONS
                    if "book" in unknown:
                        raise ConfigurationError(
                            f"{path}: the [book] section is retired. Schema 2 "
                            "splits it into [manuscript] (what is written) and "
                            "[publish] (how it is rendered); the configuration "
                            "guide's 'From schema 1' table maps every key"
                        )
                    if unknown:
                        raise ConfigurationError(
                            f"{path}: unknown sections: {', '.join(sorted(unknown))}"
                        )
                    _merge(values, data, origins, path)
            if type(values.get("schema")) is not int or values["schema"] != SCHEMA:
                raise ConfigurationError(f"{config}: schema must be {SCHEMA}")
        if overrides:
            _merge(values, overrides, origins, root / "<invocation>")
        _check_provenance(values, origins)
        _check_retired(values, origins)
        return cls(root, _freeze(values), _freeze(origins), config)

    @classmethod
    def from_settings(cls, root, settings):
        """Construct an isolated project for a library caller or test."""
        root = Path(root).expanduser().resolve()
        origins = {}
        values = {}
        _merge(values, copy.deepcopy(settings), origins, root / "quirework.toml")
        _check_provenance(values, origins)
        _check_retired(values, origins)
        return cls(root, _freeze(values), _freeze(origins))

    def get(self, key, default=None):
        _settings.note("", key)
        value = self.settings
        for part in key.split("."):
            if not isinstance(value, Mapping) or part not in value:
                return _settings.watched(default, "", key)
            value = value[part]
        return _settings.watched(_plain(value), "", key)

    def path(self, key, default=None):
        value = self.get(key, default)
        if (
            key == "provenance.coldread"
            and self.get(key) is None
            and self.get("provenance.root") is not None
        ):
            return self.path("provenance.root") / "coldread"
        if value is None:
            raise ConfigurationError(f"configure {key} in quirework.toml")
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise ConfigurationError(f"{key} must be a nonempty path")
        path = Path(value).expanduser()
        base = Path(self.origins[key]).parent if key in self.origins else self.root
        return (path if path.is_absolute() else base / path).resolve()

    def path_at(self, root, key, default=None):
        """Apply a configured layout to an explicitly supplied project root."""
        value = self.get(key, default)
        if value is None:
            raise ConfigurationError(f"configure {key} in quirework.toml")
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else (Path(root) / path).resolve()

    @property
    def chapter_subdir(self):
        source = self.path("manuscript.source")
        chapters = self.path("manuscript.chapters")
        try:
            return chapters.relative_to(source)
        except ValueError as exc:
            raise ConfigurationError(
                "manuscript.chapters must be inside manuscript.source"
            ) from exc

    @property
    def state_root(self):
        default = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        identity = hashlib.sha256(os.fsencode(self.root)).hexdigest()[:20]
        return self.path("runs.store", str(default / "quirework/projects" / identity))

    @property
    def cache_root(self):
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        identity = hashlib.sha256(os.fsencode(self.root)).hexdigest()[:20]
        return self.path("project.cache", str(base / "quirework" / identity))

    def scratch_dir(self, name):
        """A private directory for an artifact that must NOT outlive the run.

        Not the cache: nothing collects `~/.cache` on its own, so a derived
        dump left there is permanent by default, and this tool's dumps include
        verbatim human prompts. `$XDG_RUNTIME_DIR` is per-user, already 0700,
        on tmpfs and emptied at logout, which is what this artifact wants.
        Where there is none (macOS, a container, a cron context) fall back to
        `tempfile`, which honors `$TMPDIR` and creates the directory with an
        unpredictable name. One directory per project instance, so everything
        inside one `project_scope` shares it and two runs never collide.
        """
        cached = self._scratch.get(name)
        if cached is not None:
            return cached
        made = Path(
            tempfile.mkdtemp(prefix=f"quirework-{name}-", dir=private_tmp_parent())
        )
        self._scratch[name] = made
        return made

    def account_state(self, provider):
        account = self.get(f"providers.{provider}.account", "default")
        if not isinstance(account, str) or not account:
            raise ConfigurationError(
                f"providers.{provider}.account must be a nonempty string"
            )
        identity = hashlib.sha256(account.encode()).hexdigest()[:20]
        default = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        return self.path(
            f"providers.{provider}.state",
            str(default / "quirework/accounts" / provider / identity),
        )

    def data(self, key, default=None):
        """Read project-owned TOML or JSON data; absence is optional only if undeclared."""
        if self.get(key) is None:
            return copy.deepcopy(default)
        path = self.path(key)
        with self._policy_lock:
            if path not in self._policy_cache:
                try:
                    text = path.read_text(encoding="utf-8")
                    value = (
                        tomllib.loads(text)
                        if path.suffix == ".toml"
                        else json.loads(text)
                    )
                except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
                    raise ConfigurationError(
                        f"cannot read {key} from {path}: {exc}"
                    ) from exc
                self._policy_cache[path] = _freeze(value)
            return _settings.watched(_plain(self._policy_cache[path]), key, "")

    def require(self, component):
        if component == "provenance":
            if not self.get("provenance.enabled", False):
                raise ConfigurationError(
                    "provenance is not configured; enable it and declare "
                    "provenance.root and provenance.store"
                )
            root = self.path("provenance.root")
            if not root.is_dir():
                raise ConfigurationError(
                    f"configured provenance directory is missing: {root}"
                )
            # An enabled corpus whose store is missing or belongs to another
            # project is refused here, before a command reads it as empty.
            from quirework.provenance import store

            store.open_store(self)
        elif component == "manuscript":
            source = self.path("manuscript.source")
            if not source.is_dir():
                raise ConfigurationError(
                    f"configured manuscript source directory is missing: {source}"
                )
            # The layout is the format's to validate, so a format added as an
            # extension is held to its own rules rather than to a list here.
            from quirework.manuscript import formats

            with project_scope(self):
                formats.current().validate(self)
            for key, kind in (
                ("manuscript.spec", "spec file"),
                ("manuscript.review_notes", "review-notes file"),
            ):
                if self.get(key) is not None and not self.path(key).is_file():
                    raise ConfigurationError(
                        f"configured {kind} is missing: {self.path(key)}"
                    )
            sources = self.get("manuscript.sources")
            if sources is not None and not any(c in str(sources) for c in "*?["):
                if not self.path("manuscript.sources").is_file():
                    raise ConfigurationError(
                        f"configured sources register is missing: "
                        f"{self.path('manuscript.sources')}"
                    )
            copies = self.get("manuscript.source_copies")
            if copies is not None and "{slug}" not in str(copies):
                raise ConfigurationError(
                    "manuscript.source_copies must contain {slug}, e.g. "
                    "'research/copies/{slug}.txt'"
                )
            unit_spec = self.get("manuscript.unit_spec")
            if unit_spec is not None and "{slug}" not in str(unit_spec):
                raise ConfigurationError(
                    "manuscript.unit_spec must contain {slug}, e.g. "
                    "'specs/{slug}/spec.md'"
                )
            if self.get("manuscript.spec_trail") is not None:
                # The trail mirrors the spec's sections, so it means nothing
                # without one. The file itself may not exist yet: the first
                # breadcrumb creates it.
                if self.get("manuscript.spec") is None and unit_spec is None:
                    raise ConfigurationError(
                        "manuscript.spec_trail follows the spec's sections; configure "
                        "manuscript.spec or manuscript.unit_spec too, or remove "
                        "manuscript.spec_trail"
                    )
                trail = self.path("manuscript.spec_trail")
                if trail.is_dir():
                    raise ConfigurationError(
                        f"manuscript.spec_trail names a directory, not a file: {trail}"
                    )
            if self.get("manuscript.spec_trail_leads") is not None:
                # The backfill ledger holds leads for the trail's sections, so it
                # means nothing without a trail. Its directory is made on first use.
                if self.get("manuscript.spec_trail") is None:
                    raise ConfigurationError(
                        "manuscript.spec_trail_leads keeps leads for a spec trail; "
                        "configure manuscript.spec_trail too, or remove it"
                    )
                leads = self.path("manuscript.spec_trail_leads")
                if leads.exists() and not leads.is_dir():
                    raise ConfigurationError(
                        f"manuscript.spec_trail_leads names a file, not a directory: {leads}"
                    )
        elif component == "publish":
            # A publisher renders the manuscript, so it needs everything the
            # manuscript does before anything of its own.
            self.require("manuscript")
            from quirework.publish import targets

            targets.validate(self)

    def overlay(self, prefix, table_key, drop=()):
        """This project with the table at ``table_key`` laid over ``prefix``.

        A publish target's table is read as its `[publish]`: each key it
        declares replaces the shared one, and keeps the origin of the file that
        declared it, so a relative path still resolves against that file. The
        keys in ``drop`` leave ``prefix`` first. The result is a project like
        any other; nothing is mutated.
        """
        values = copy.deepcopy(_plain(self.settings))
        origins = dict(self.origins)
        table = self.get(table_key) or {}
        section = values.setdefault(prefix, {})
        for key in drop:
            section.pop(key, None)
        for key, value in table.items():
            section[key] = copy.deepcopy(value)
            declared = f"{table_key}.{key}"
            for origin_key in self.origins:
                if origin_key == declared or origin_key.startswith(declared + "."):
                    rest = origin_key[len(table_key) + 1 :]
                    origins[f"{prefix}.{rest}"] = self.origins[origin_key]
        return Project(self.root, _freeze(values), _freeze(origins), self.config_file)

    def replaced(self, values):
        """This project with each dotted key in ``values`` set to its value.

        A view of the same project with a few settings moved, such as a
        derived edition rendered through the book's own pipeline as if it
        were the book. A replaced path resolves against the project root
        unless it is absolute. The result is a project like any other;
        nothing is mutated.
        """
        settings = copy.deepcopy(_plain(self.settings))
        origins = dict(self.origins)
        for key, value in values.items():
            *parents, leaf = key.split(".")
            table = settings
            for part in parents:
                table = table.setdefault(part, {})
            table[leaf] = copy.deepcopy(value)
            for origin_key in list(origins):
                if origin_key == key or origin_key.startswith(key + "."):
                    del origins[origin_key]
        return Project(self.root, _freeze(settings), _freeze(origins), self.config_file)

    def describe(self):
        return {
            "root": str(self.root),
            "configuration": str(self.config_file) if self.config_file else None,
            "settings": _plain(self.settings),
            "origins": dict(self.origins),
        }

    def invoke(self, command, argv=()):
        """Run a declared command against this project without changing cwd."""
        from .cli import invoke

        return invoke(command, list(argv), project=self)


_CURRENT: contextvars.ContextVar[Project | None] = contextvars.ContextVar(
    "quirework_project", default=None
)


def private_tmp_parent() -> Path | None:
    """Where a directory that must not outlive its command belongs.

    `$XDG_RUNTIME_DIR` where the session has one: per-user, 0700, on tmpfs and
    emptied at logout. Otherwise None, which sends `tempfile` to `$TMPDIR`
    under a name nobody can predict.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return Path(runtime) if runtime and Path(runtime).is_dir() else None


# Every key the per-user configuration reads. The file holds what belongs to the
# person rather than to one project, and it is a file rather than a variable in
# a shell profile because a program the person's shell did not start (a hook a
# harness runs, an editor opened from a launcher) never sees what the profile
# exported, while every program can read a file. Strict like a project's: a
# misspelled key would otherwise leave its default in force without a word.
USER_KEYS = {
    "copies": frozenset({"log", "author"}),
    "kindle": frozenset({"inbox", "targets", "reader_targets"}),
    "provenance": frozenset({"home"}),
}

# Parsed per-user configurations, keyed by the file's path and checked against
# its inode, size and modification time before use, so an edit is never missed.
_USER_PARSED = {}


def user_config_path() -> Path:
    """Where the per-user configuration lives, whether or not it exists."""
    configured = os.environ.get("XDG_CONFIG_HOME")
    base = Path(configured) if configured and Path(configured).is_absolute() else None
    return (base or Path.home() / ".config") / "quirework" / "config.toml"


def user_settings() -> dict:
    """The per-user configuration as ``{section: {key: value}}``; empty without one."""
    path = user_config_path()
    try:
        st = path.stat()
    except FileNotFoundError:
        return {}
    signature = (st.st_ino, st.st_size, st.st_mtime_ns)
    cached = _USER_PARSED.get(str(path))
    if cached is not None and cached[0] == signature:
        return copy.deepcopy(cached[1])
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot read {path}: {exc}") from exc
    known = ", ".join(f"{s}.{k}" for s, keys in USER_KEYS.items() for k in keys)
    for section, values in data.items():
        if section not in USER_KEYS or not isinstance(values, dict):
            raise ConfigurationError(
                f"{path}: unknown setting {section}; this file reads {known}"
            )
        unknown = sorted(set(values) - USER_KEYS[section])
        if unknown:
            raise ConfigurationError(
                f"{path}: unknown setting {section}.{unknown[0]}; this file "
                f"reads {known}"
            )
    _USER_PARSED[str(path)] = (signature, data)
    return copy.deepcopy(data)


def current_project() -> Project:
    project = _CURRENT.get()
    return project if project is not None else Project.load()


@contextmanager
def project_scope(project: Project) -> Iterator[Project]:
    token = _CURRENT.set(project)
    try:
        yield project
    finally:
        _CURRENT.reset(token)


def submit(pool, function, *args, **kwargs):
    """Submit a worker with its caller's immutable operation configuration."""
    context = contextvars.copy_context()
    return pool.submit(context.run, function, *args, **kwargs)


def extension(reference):
    """Load an explicitly selected, installed Python extension."""
    if not isinstance(reference, str) or reference.count(":") != 1:
        raise ConfigurationError("an extension must name an installed module:function")
    module, name = reference.split(":")
    try:
        imported = importlib.import_module(module)
    except ModuleNotFoundError as exc:
        raise ConfigurationError(
            f"cannot load extension {reference}: {exc}; install the consumer package "
            "and its dependencies in the environment running Quirework"
        ) from exc
    value = getattr(imported, name)
    if not callable(value):
        raise ConfigurationError(f"extension is not callable: {reference}")
    return value
