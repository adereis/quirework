"""Back a store up with a script of the person's own, kept in the store.

The target names the script and its arguments, ``command = ["backup.sh",
"--quiet"]``, and Quirework runs it from the store's directory with three
variables set: ``QUIREWORK_STORE``, the store's path; ``QUIREWORK_STORE_ID``,
its identity; ``QUIREWORK_BACKUP_TARGET``, the target's name. Its exit status
is the run's outcome, and what it prints goes to the run's log.

Quirework cannot see what a script copies, so the rules a copying driver
follows are the script's to keep: leave names that start with a dot behind,
never overwrite or delete an accepted file at the destination, and delete a
pending file there only once its accepted version has arrived. The script
runs without the store's lock, since a slow upload would otherwise hold up
``accept`` for as long as it took.

Because Quirework runs it unattended, the script has to be one only its owner
could have put there: a regular file at the store's top level, owned by the
user running the backup, executable, and writable by nobody else.
"""

from __future__ import annotations

import os
import stat

from quirework.project import ConfigurationError
from quirework.provenance.backup.base import (
    PENDING,
    BackupError,
    Driver,
    Outcome,
    run_tool,
)
from quirework.runs.storage import shown


class Command(Driver):
    name = "command"
    keys = required = frozenset({"command"})

    def configure(self, settings):
        argv = settings["command"]
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(a, str) and a for a in argv)
        ):
            raise ConfigurationError(
                f"{self.where}: command must be a list of non-empty strings, the "
                'script first: ["backup.sh"]'
            )
        script = argv[0]
        if "/" in script or script.startswith("."):
            raise ConfigurationError(
                f"{self.where}: the script must be a file at the store's top "
                f"level, named without a path, not {script!r}"
            )
        self.argv = argv

    def describe(self):
        return "command " + " ".join(self.argv)

    def _script(self):
        path = self.root / self.argv[0]
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            raise BackupError(f"{shown(path)} does not exist") from None
        if not stat.S_ISREG(st.st_mode):
            raise BackupError(f"{shown(path)} is not a regular file")
        if st.st_uid != os.getuid():
            raise BackupError(f"{shown(path)} is not owned by the user running it")
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise BackupError(
                f"{shown(path)} is writable by others; `chmod go-w` it before "
                "Quirework will run it"
            )
        if not st.st_mode & stat.S_IXUSR:
            raise BackupError(f"{shown(path)} is not executable; `chmod u+x` it")
        return path

    def check(self, log):
        self._script()

    def run(self, source, log):
        script = self._script()
        versions = source.versions()
        pending = len(source.files(PENDING))
        env = {
            **os.environ,
            "QUIREWORK_STORE": str(source.root),
            "QUIREWORK_STORE_ID": source.identity,
            "QUIREWORK_BACKUP_TARGET": self.target,
        }
        run_tool(
            [script, *self.argv[1:]],
            log,
            what=self.argv[0],
            cwd=source.root,
            env=env,
        )
        return Outcome(
            versions=versions,
            pending=pending,
            detail=f"{self.argv[0]} reported success",
            seen=False,
        )
