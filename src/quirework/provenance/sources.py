"""Configured source locations and curation boundaries."""

from pathlib import Path
import subprocess
from quirework.project import current_project


def kind_of_path(path, project=None):
    project = current_project() if project is None else project
    path = Path(path).expanduser()
    path = (path if path.is_absolute() else project.root / path).resolve()
    matches = []
    for key, kind, default in (
        ("codex_sessions", "codex", "codex-sessions"),
        ("agy_sessions", "agy", "agy-sessions"),
        ("claude_sessions", "cc", "cc-sessions"),
    ):
        root = project.path("provenance." + key, ".quirework/provenance/" + default)
        if path.is_relative_to(root):
            matches.append((len(root.parts), kind))
    return max(matches)[1] if matches else ("web" if path.suffix == ".md" else "cc")


def accepted_files(directory, suffix, project=None):
    project = current_project() if project is None else project
    directory = Path(directory).resolve()
    mode = project.get("provenance.curation", "tracked")
    if mode == "presence":
        return sorted(p for p in directory.glob("*" + suffix) if p.is_file())
    if mode != "tracked":
        raise ValueError("provenance.curation must be tracked or presence")
    if not directory.exists():
        return []
    root = project.path("provenance.tracking_root", str(project.root))
    if not directory.is_relative_to(root):
        raise ValueError(
            "external corpora require provenance.tracking_root or curation = 'presence'"
        )
    output = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "-z",
            "--",
            str(directory.relative_to(root)),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(
        p
        for rel in output.split("\0")
        if rel
        for p in [root / rel]
        if p.parent == directory and p.suffix == suffix and p.is_file()
    )


def source_reference(path, project=None):
    project = current_project() if project is None else project
    path = Path(path).resolve()
    return path.relative_to(project.root) if path.is_relative_to(project.root) else path
