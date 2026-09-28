"""Which publisher renders the manuscript, and which commands each one offers.

`publish.backend` names it. `quarto`, the default, stages a book and renders
its four editions, and every other `publish` command (figures, fonts, red-pen
copies, reader packets, derived editions, comparisons) is built on that
staging. `hugo` builds a site with the project's own Hugo configuration and
offers `publish build` alone: a site's figures, templates and deploy belong to
the site, and Quirework does not second-guess them.

A project can select a backend of its own as an installed extension,
`module:function`, where the function returns a `Backend`. It offers
`publish build` and nothing a built-in backend alone provides.

A command a backend does not offer is refused by name rather than attempted,
because running Quarto staging against a Hugo site would fail somewhere deep
and say nothing useful about why.
"""

from __future__ import annotations

from quirework.project import ConfigurationError, Project, current_project, extension


class Backend:
    """One publisher. Subclasses validate their configuration and build.

    Every method runs with the target's view of the project current: the
    target's own table laid over `[publish]` (see `publish.targets`).
    """

    name: str = ""
    # The name the project's one target takes when it declares no targets.
    target: str = ""

    def validate(self, project: Project) -> None:
        """Refuse a configuration this backend cannot build. Called by
        `Project.require("publish")` inside the target's scope."""

    def build(self, argv: list[str] | None, target: str) -> int:
        """Build ``target`` with the arguments `publish build` passed on."""
        raise NotImplementedError

    def output(self, project: Project, target: str):
        """The directory a finished build of ``target`` leaves its output in,
        which is what another target copies from."""
        return project.path("publish.build", "build") / target


class Quarto(Backend):
    name = "quarto"
    target = "book"

    def validate(self, project: Project) -> None:
        if (
            project.get("publish.figures") is not None
            and not project.path("publish.figures").is_dir()
        ):
            raise ConfigurationError(
                f"configured figure directory is missing: "
                f"{project.path('publish.figures')}"
            )

    def build(self, argv: list[str] | None, target: str) -> int:
        from quirework.publish import render

        return render.main(argv)

    def output(self, project: Project, target: str):
        # The renderer consolidates every edition here, whatever the target
        # is called, which is one reason a project has one Quarto target.
        return project.path("publish.build", "build") / "book" / "_site"


class Hugo(Backend):
    name = "hugo"
    target = "site"

    def validate(self, project: Project) -> None:
        from quirework.publish import hugo

        hugo.check_site(hugo.site_root())

    def build(self, argv: list[str] | None, target: str) -> int:
        from quirework.publish import hugo

        return hugo.main(argv, target)


BUILTIN = {"quarto": Quarto(), "hugo": Hugo()}


def get(name: str) -> Backend:
    """The backend ``name`` names: a built-in, or an extension's."""
    if name in BUILTIN:
        return BUILTIN[name]
    if ":" in name:
        made = extension(name)()
        if not isinstance(made, Backend):
            raise ConfigurationError(
                f"publish.backend {name} must return a quirework Backend"
            )
        return made
    raise ConfigurationError(
        f"publish.backend must be one of {', '.join(BUILTIN)} or an installed "
        f"module:function, not {name!r}"
    )


def selected(project=None) -> Backend:
    """The backend of a project, or of one target's view of it."""
    project = project or current_project()
    return get(project.get("publish.backend", "quarto"))
