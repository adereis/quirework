"""`quirework publish build`: build a target, after the targets it needs.

    quirework publish build                 every target, each after its needs
    quirework publish build site            one target, after what it needs
    quirework publish build site --no-needs one target, from its needs' last builds
    quirework publish build book --to html  arguments after the target go to it

With one target the name may be left out, and every argument goes to its
backend. With several, arguments are for one target, so it must be named.
"""

from __future__ import annotations

import sys

from quirework.project import ConfigurationError, current_project, project_scope
from quirework.publish import targets as _targets


def _build(target, known, argv) -> int:
    with project_scope(target.project):
        status = target.backend.build(argv, target.name)
        if status:
            return status
        for line in _targets.copy_into(target, known):
            print(f"publish build: {target.name}: {line}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    known = _targets.targets()
    skip_needs = "--no-needs" in argv
    argv = [a for a in argv if a != "--no-needs"]
    if argv and argv[0] in known:
        wanted, argv = [argv[0]], argv[1:]
    elif len(known) == 1:
        wanted = list(known)
    elif argv:
        raise ConfigurationError(
            f"name the target these arguments are for: {', '.join(known)}"
        )
    else:
        wanted = list(known)
    plan = wanted if skip_needs else _targets.order(wanted, known)
    for name in plan:
        # A needed target is built as it would be with no arguments; the ones
        # given are for the target named.
        status = _build(known[name], known, argv if name in wanted else [])
        if status:
            if name not in wanted:
                print(
                    f"publish build: {name}, which {', '.join(wanted)} needs, "
                    f"failed; nothing after it was built",
                    file=sys.stderr,
                )
            return status
    if len(known) > 1:
        print(f"publish build: built {', '.join(plan)} for {current_project().root}")
    return 0
