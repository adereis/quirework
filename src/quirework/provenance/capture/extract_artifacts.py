#!/usr/bin/env python3
"""Extract Claude `artifacts`-tool documents embedded in a captured
conversation into standalone files, replaying create/rewrite/update ops to
reconstruct each artifact's final state.

  usage: extract-artifacts.py <conversation-uuid>
  reads  private/provenance/conversations/<uuid>.raw.json
  writes private/provenance/conversations/artifacts/<uuid>/<artifact-id>.<ext>
         private/provenance/conversations/artifacts/<uuid>/manifest.json

The manifest records every operation (command, version_uuid, byte length,
source message index) so the full version trajectory is documented even though
only the final content of each artifact is written as a file.
"""

import json, sys, os
from pathlib import Path

# Outputs use the selected project configuration, independently of where
# this package is installed.
from quirework.project import current_project

EXT = {
    "text/html": "html",
    "text/markdown": "md",
    "text/plain": "txt",
    "application/vnd.ant.code": "txt",
    "application/vnd.ant.react": "jsx",
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        raise SystemExit("usage: extract-artifacts.py <conversation-uuid>")
    uuid = argv[0]
    raw = str(current_project().path("provenance.conversations") / f"{uuid}.raw.json")
    outdir = str(
        current_project().path("provenance.conversations") / "artifacts" / uuid
    )
    conv = json.load(open(raw))
    msgs = sorted(conv["chat_messages"], key=lambda m: m.get("index", 0))

    state = {}  # id -> {content, title, type}
    ops = []
    for m in msgs:
        for b in m["content"]:
            if b.get("type") != "tool_use" or b.get("name") != "artifacts":
                continue
            inp = b.get("input", {})
            aid = inp.get("id") or "untitled"
            cmd = inp.get("command", "?")
            st = state.setdefault(aid, {"content": "", "title": None, "type": None})
            if cmd in ("create", "rewrite"):
                st["content"] = inp.get("content", "")
            elif cmd == "update":
                old, new = inp.get("old_str", ""), inp.get("new_str", "")
                if old:
                    st["content"] = st["content"].replace(old, new, 1)
            if inp.get("title"):
                st["title"] = inp["title"]
            if inp.get("type"):
                st["type"] = inp["type"]
            ops.append(
                {
                    "id": aid,
                    "command": cmd,
                    "version_uuid": inp.get("version_uuid"),
                    "title": inp.get("title"),
                    "type": inp.get("type"),
                    "msg_index": m.get("index"),
                    "result_len": len(st["content"]),
                }
            )

    if not state:
        print(f"no artifacts in {uuid}")
        return
    os.makedirs(outdir, exist_ok=True)
    files = []
    for aid, st in state.items():
        ext = EXT.get(st["type"] or "", "txt")
        fn = f"{outdir}/{aid}.{ext}"
        with open(fn, "w") as f:
            f.write(st["content"])
        files.append(
            {
                "id": aid,
                "title": st["title"],
                "type": st["type"],
                "file": os.path.basename(fn),
                "bytes": len(st["content"]),
            }
        )
    with open(f"{outdir}/manifest.json", "w") as f:
        json.dump(
            {"conversation_uuid": uuid, "artifacts": files, "operations": ops},
            f,
            indent=2,
            ensure_ascii=False,
        )
    for x in files:
        print(f"  {x['file']:40} {x['bytes']:>7} B  {x['title']}")
    print(f"wrote {len(files)} artifacts + manifest.json to {outdir}")


if __name__ == "__main__":
    main()
