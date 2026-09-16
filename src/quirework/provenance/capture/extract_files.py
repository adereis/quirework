#!/usr/bin/env python3
"""Reconstruct files authored in a conversation's code/file environment by
replaying create_file / str_replace operations to their final state.

  usage: extract-files.py <conversation-uuid>
  reads  private/provenance/conversations/<uuid>.raw.json
  writes private/provenance/conversations/files/<uuid>/<basename>   (final content)
         private/provenance/conversations/files/<uuid>/manifest.json

Only create_file and str_replace are replayed (the operations that deterministic-
ally define file content). bash_tool / view / present_files are ignored. If a
str_replace's old_str is not found in the reconstructed state, it is recorded as
a mismatch in the manifest so fidelity is auditable rather than silently wrong.
"""

import json, sys, os

# Outputs use the selected project configuration, independently of where
# this package is installed.
from quirework.project import current_project


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        raise SystemExit("usage: extract-files.py <conversation-uuid>")
    uuid = argv[0]
    raw = str(current_project().path("provenance.conversations") / f"{uuid}.raw.json")
    outdir = str(current_project().path("provenance.conversations") / "files" / uuid)
    conv = json.load(open(raw))
    msgs = sorted(conv["chat_messages"], key=lambda m: m.get("index", 0))

    state = {}  # path -> content
    ops, mismatches = [], []
    for m in msgs:
        for b in m["content"]:
            if b.get("type") != "tool_use":
                continue
            name, inp = b.get("name"), b.get("input", {})
            path = inp.get("path")
            if name == "create_file" and path is not None:
                state[path] = inp.get("file_text", inp.get("content", ""))
                ops.append(
                    {
                        "op": "create_file",
                        "path": path,
                        "msg_index": m.get("index"),
                        "len": len(state[path]),
                    }
                )
            elif name == "str_replace" and path is not None:
                old, new = inp.get("old_str", ""), inp.get("new_str", "")
                cur = state.get(path, "")
                if old and old in cur:
                    state[path] = cur.replace(old, new, 1)
                    ok = True
                else:
                    mismatches.append({"path": path, "msg_index": m.get("index")})
                    ok = False
                ops.append(
                    {
                        "op": "str_replace",
                        "path": path,
                        "msg_index": m.get("index"),
                        "applied": ok,
                        "len": len(state.get(path, "")),
                    }
                )

    if not state:
        print(f"no code-env files in {uuid}")
        return
    os.makedirs(outdir, exist_ok=True)
    # Disambiguate when distinct source paths share a basename (e.g. a working
    # copy under /home/claude and a presented copy under /mnt/.../outputs).
    from collections import Counter

    base_counts = Counter(os.path.basename(p) or "file" for p in state)

    def outname(path):
        base = os.path.basename(path) or "file"
        if base_counts[base] == 1:
            return base
        return path.lstrip("/").replace(" ", "_").replace("/", "__")

    files = []
    for path, content in state.items():
        name = outname(path)
        with open(f"{outdir}/{name}", "w") as f:
            f.write(content)
        files.append({"src_path": path, "file": name, "bytes": len(content)})
    with open(f"{outdir}/manifest.json", "w") as f:
        json.dump(
            {
                "conversation_uuid": uuid,
                "files": files,
                "operations": ops,
                "mismatches": mismatches,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    for x in files:
        print(f"  {x['file']:32} {x['bytes']:>7} B   (from {x['src_path']})")
    print(
        f"wrote {len(files)} file(s) + manifest to {outdir}; "
        f"{len(mismatches)} str_replace mismatch(es)"
    )


if __name__ == "__main__":
    main()
