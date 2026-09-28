"""Which publisher renders the manuscript, and which commands each one offers.

`publish.backend` names it. `quarto`, the default, stages a book and renders
its four editions, and every other `publish` command (figures, fonts, red-pen
copies, reader packets, derived editions, comparisons) is built on that
staging. `hugo` builds a site with the project's own Hugo configuration and
offers `publish build` alone: a site's figures, templates and deploy belong to
the site, and Quirework does not second-guess them.

A command a backend does not offer is refused by name rather than attempted,
because running Quarto staging against a Hugo site would fail somewhere deep
and say nothing useful about why.
"""

from __future__ import annotations

from quirework.project import ConfigurationError, current_project

BACKENDS = ("quarto", "hugo")


def selected(project=None) -> str:
    project = project or current_project()
    backend = project.get("publish.backend", "quarto")
    if backend not in BACKENDS:
        raise ConfigurationError(
            f"publish.backend must be one of {', '.join(BACKENDS)}, not {backend!r}"
        )
    return backend


def require(backend: str, command: str, project=None) -> None:
    """Refuse ``command`` unless the project publishes with ``backend``."""
    have = selected(project)
    if have != backend:
        raise ConfigurationError(
            f"`quirework {command}` works on a {backend} build, and this "
            f"project publishes with {have} (publish.backend)"
        )
