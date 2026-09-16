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
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from threading import RLock
from typing import Any, Iterator, Mapping


class ConfigurationError(ValueError):
    """A project configuration cannot be used as declared."""


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


@dataclass(frozen=True)
class Project:
    root: Path
    settings: Mapping[str, Any]
    origins: Mapping[str, str]
    config_file: Path | None = None
    _policy_cache: dict = field(default_factory=dict, compare=False, repr=False)
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
                    unknown = set(data) - {
                        "schema",
                        "project",
                        "book",
                        "text",
                        "provenance",
                        "sops",
                        "runs",
                        "providers",
                        "checks",
                        "extensions",
                    }
                    if unknown:
                        raise ConfigurationError(
                            f"{path}: unknown sections: {', '.join(sorted(unknown))}"
                        )
                    _merge(values, data, origins, path)
            if type(values.get("schema")) is not int or values["schema"] != 1:
                raise ConfigurationError(f"{config}: schema must be 1")
        if overrides:
            _merge(values, overrides, origins, root / "<invocation>")
        return cls(root, _freeze(values), _freeze(origins), config)

    @classmethod
    def from_settings(cls, root, settings):
        """Construct an isolated project for a library caller or test."""
        root = Path(root).expanduser().resolve()
        origins = {}
        values = {}
        _merge(values, copy.deepcopy(settings), origins, root / "quirework.toml")
        return cls(root, _freeze(values), _freeze(origins))

    def get(self, key, default=None):
        value = self.settings
        for part in key.split("."):
            if not isinstance(value, Mapping) or part not in value:
                return default
            value = value[part]
        return _plain(value)

    def path(self, key, default=None):
        value = self.get(key, default)
        book_defaults = {
            "book.quarto": ("book.source", "docs", "_quarto.yml"),
            "book.chapters": ("book.source", "docs", "canon"),
            "book.figures": ("book.chapters", "docs/canon", "illustrations"),
            "book.spec": ("book.chapters", "docs/canon", "_framework.md"),
            "book.references": ("book.chapters", "docs/canon", "_references.md"),
            "book.review_notes": ("book.chapters", "docs/canon", "_review-notes.md"),
            "book.typst_template": ("book.source", "docs", "typst-show.typ"),
        }
        if (
            key in book_defaults
            and self.get(key) is None
            and (
                self.get("book.source") is not None
                or self.get("book.chapters") is not None
            )
        ):
            parent, fallback, name = book_defaults[key]
            return self.path(parent, fallback) / name
        children = {
            "conversations": "conversations",
            "claude_sessions": "cc-sessions",
            "codex_sessions": "codex-sessions",
            "agy_sessions": "agy-sessions",
            "coldread": "coldread",
        }
        if (
            key.startswith("provenance.")
            and key.split(".", 1)[1] in children
            and self.get(key) is None
            and self.get("provenance.root") is not None
        ):
            return self.path("provenance.root") / children[key.split(".", 1)[1]]
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
        source = self.path("book.source", "docs")
        chapters = self.path("book.chapters", "docs/canon")
        try:
            return chapters.relative_to(source)
        except ValueError as exc:
            raise ConfigurationError(
                "book.chapters must be inside book.source"
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
            return _plain(self._policy_cache[path])

    def require(self, component):
        if component == "provenance":
            if not self.get("provenance.enabled", False):
                raise ConfigurationError(
                    "provenance is not configured; enable it and declare provenance.root"
                )
            root = self.path("provenance.root")
            if not root.is_dir():
                raise ConfigurationError(
                    f"configured provenance directory is missing: {root}"
                )
        elif component == "book":
            path = self.path("book.source", "docs")
            if not path.is_dir():
                raise ConfigurationError(
                    f"configured book source directory is missing: {path}"
                )

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
