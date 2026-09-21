"""Reusable perspectives. Profiles never grant access or select a model."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from quirework.project import current_project


NAME = re.compile(r"[a-z][a-z0-9-]*")
FIELDS = {"schema_version", "id", "revision", "label", "description", "tags", "prompt"}


def _options():
    """Read policy from the selected project for this operation."""
    from types import SimpleNamespace

    DEFAULT = current_project().data("sops.persona_policy", {}).get("default", None)
    return SimpleNamespace(DEFAULT=DEFAULT)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def parse(text):
    return json.loads(text, object_pairs_hook=_pairs)


def validate(data):
    if not isinstance(data, dict) or set(data) != FIELDS:
        raise ValueError(f"persona fields must be: {', '.join(sorted(FIELDS))}")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("unsupported persona schema")
    if not isinstance(data["id"], str) or not NAME.fullmatch(data["id"]):
        raise ValueError("persona id must be a lowercase slug")
    if data["id"] == "default":
        raise ValueError("default is an alias; use a concrete profile id")
    if type(data["revision"]) is not int or data["revision"] < 1:
        raise ValueError("persona revision must be a positive integer")
    for key in ("label", "description", "prompt"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise ValueError(f"persona {key} must contain text")
    tags = data["tags"]
    if (
        not isinstance(tags, list)
        or any(not isinstance(t, str) or not NAME.fullmatch(t) for t in tags)
        or len(set(tags)) != len(tags)
    ):
        raise ValueError("persona tags must be unique slugs")
    return data


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True
    ).stdout.decode("utf-8")


def git_path(root, path):
    """A project-relative path as Git names it, from anywhere in the repository.

    ``git ls-tree`` prints repository-relative paths and ``git show REV:PATH``
    reads one, while a project names its own files relative to the project
    root. Those are the same string only when the project is the whole
    checkout. A book kept in a subdirectory would otherwise look for its
    ``profiles/reader.json`` among paths that all begin
    ``examples/demo-book/``, match none of them, and read that as "every
    profile is new" -- a revision check that silently checks nothing.
    """
    prefix = _git(root, "rev-parse", "--show-prefix").strip()
    return prefix + str(path)


def _project_relative(root, path):
    """A configured directory as the project names it, for Git and for reports."""
    path = Path(path)
    return (
        str(path.relative_to(Path(root)))
        if path.is_relative_to(Path(root))
        else str(path)
    )


def resolve(reference=None, *, root=None):
    """Resolve a catalog reference or explicit inline profile to frozen bytes.

    Strings accept id@revision. Objects accept id, revision and optional at
    (a Git commit), or exactly {inline: PROFILE}. No latest fallback on a pin.
    """
    if reference is None:
        reference = _options().DEFAULT
    if root is None:
        root = current_project().root
    if isinstance(reference, dict) and set(reference) == {"frozen"}:
        return copy.deepcopy(verify(reference["frozen"]))
    if isinstance(reference, dict) and set(reference) == {"inline"}:
        data = copy.deepcopy(validate(reference["inline"]))
        raw = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        source = {"kind": "inline"}
    else:
        if isinstance(reference, str):
            name, sep, rev = reference.partition("@")
            if sep and not rev.isdecimal():
                raise ValueError("persona revision must be an integer")
            ref = {"id": name, **({"revision": int(rev)} if sep else {})}
        elif isinstance(reference, dict):
            ref = dict(reference)
        else:
            raise ValueError("persona must name a profile or supply an inline profile")
        if set(ref) - {"id", "revision", "at"} or "id" not in ref:
            raise ValueError("persona reference fields: id, revision, at")
        name = _options().DEFAULT if ref["id"] == "default" else ref["id"]
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("invalid persona id")
        directory = current_project().path_at(root, "sops.personas")
        rel = str(Path(_project_relative(root, directory)) / f"{name}.json")
        if "at" in ref:
            revision = _git(
                root,
                "rev-parse",
                "--verify",
                "--end-of-options",
                str(ref["at"]) + "^{commit}",
            ).strip()
            raw = _git(root, "show", f"{revision}:{git_path(root, rel)}")
            source = {"kind": "git", "path": rel, "commit": revision}
        else:
            path = Path(root) / rel
            if path.is_symlink():
                raise ValueError("persona source cannot be a symlink")
            if not path.exists():
                raise ValueError(
                    f"unknown persona {name!r}; use quirework sop personas list"
                )
            raw = path.read_bytes().decode("utf-8")
            source = {"kind": "catalog", "path": rel}
        data = validate(parse(raw))
        if data["id"] != name:
            raise ValueError("persona filename and id disagree")
        if "revision" in ref and (
            type(ref["revision"]) is not int or ref["revision"] != data["revision"]
        ):
            raise ValueError(f"persona revision unavailable: {name}@{ref['revision']}")
    return {
        "profile": data,
        "source": source,
        "text": raw,
        "sha256": hashlib.sha256(raw.encode()).hexdigest(),
    }


def verify(snapshot):
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "profile",
        "source",
        "text",
        "sha256",
    }:
        raise ValueError("invalid frozen persona")
    if not isinstance(snapshot["text"], str) or not isinstance(snapshot["sha256"], str):
        raise ValueError("frozen persona bytes and hash must be text")
    source = snapshot["source"]
    if not isinstance(source, dict) or source.get("kind") not in (
        "inline",
        "catalog",
        "git",
    ):
        raise ValueError("invalid frozen persona source")
    if hashlib.sha256(snapshot["text"].encode()).hexdigest() != snapshot["sha256"]:
        raise ValueError("frozen persona hash changed")
    if validate(parse(snapshot["text"])) != snapshot["profile"]:
        raise ValueError("frozen persona disagrees with its bytes")
    return snapshot


def compose(text, snapshot):
    """Keep measured role positions when a template has an explicit slot."""
    profile = verify(snapshot)["profile"]["prompt"]
    if "{{persona}}" in text:
        return text.replace("{{persona}}", profile)
    return "=== PERSPECTIVE ===\n" + profile + "\n\n" + text


def identity(snapshot):
    if snapshot is None:
        return None
    p = verify(snapshot)["profile"]
    return {"id": p["id"], "revision": p["revision"], "sha256": snapshot["sha256"]}


def same_perspective(one, two):
    """Catalog and inline references may carry identical profile semantics."""
    if one is None or two is None:
        return one is two
    return verify(one)["profile"] == verify(two)["profile"]


def check(*, root=None, base=None):
    if root is None:
        root = current_project().root
    if (current_project().path_at(root, "sops.personas") / "default.json").exists():
        raise ValueError(
            "default.json is reserved by the default alias; configure the default profile"
        )
    profiles = [
        resolve(p.stem, root=root)
        for p in sorted(current_project().path_at(root, "sops.personas").glob("*.json"))
    ]
    if not profiles:
        raise ValueError("persona catalog is empty")
    if base:
        sha = _git(
            root, "rev-parse", "--verify", "--end-of-options", base + "^{commit}"
        ).strip()
        directory = _project_relative(
            root, current_project().path_at(root, "sops.personas")
        )
        paths = set(
            _git(
                root,
                "ls-tree",
                "-r",
                # Repository-relative on both sides: without this the
                # pathspec and the printed names are relative to the
                # working directory, and a project below the checkout
                # root compares two different spellings of one file.
                "--full-tree",
                "--name-only",
                sha,
                "--",
                git_path(root, directory),
            ).splitlines()
        )
        for snapshot in profiles:
            rel = git_path(root, snapshot["source"]["path"])
            if rel not in paths:
                continue
            old = validate(parse(_git(root, "show", f"{sha}:{rel}")))
            new = snapshot["profile"]
            if new["revision"] < old["revision"] or (
                any(new[k] != old[k] for k in ("prompt", "tags"))
                and new["revision"] <= old["revision"]
            ):
                raise ValueError(f"changed persona needs a new revision: {new['id']}")
    return profiles


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action")
    sub.add_parser("list")
    p = sub.add_parser("show")
    p.add_argument("persona")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("check")
    p.add_argument("--base", default="HEAD")
    args = parser.parse_args(argv)
    try:
        if args.action == "show":
            snapshot = resolve(args.persona)
            print(
                json.dumps(snapshot, indent=2)
                if args.json
                else snapshot["profile"]["prompt"]
            )
        else:
            profiles = check(base=args.base if args.action == "check" else None)
            if args.action == "check":
                print(f"{len(profiles)} personas checked")
            else:
                for s in profiles:
                    p = s["profile"]
                    print(
                        f"{p['id']}@{p['revision']}  {p['label']} — {p['description']}"
                    )
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"personas: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
