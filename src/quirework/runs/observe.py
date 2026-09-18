"""Read-only native progress diagnostics, independent of executor replay.

Observations neither mutate run state nor certify a completed result. This
version observes the native JSONL locations used by the shared adapters.
"""

import json
from pathlib import Path

from quirework.runs.storage import digest
from quirework.runs.storage import identifier
from quirework.runs.storage import read


# Where each harness keeps its own transcript, relative to the seat's private
# runner home. Shared so a reader of live progress and a reader of finished
# evidence cannot drift apart about where a session actually is.
NATIVE_TRANSCRIPTS = {
    "codex": "sessions/**/*.jsonl",
    "claude": "projects/**/*.jsonl",
    "agy": "antigravity-cli/brain/*/.system_generated/logs/transcript_full.jsonl",
}


def native_rows(path):
    """A running writer may have flushed only part of its final JSONL row."""
    with Path(path).open("rb") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line.decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError(f"{path}:{number}: native row must be an object")
                yield value
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                if not line.endswith(b"\n"):
                    return
                raise ValueError(f"{path}:{number}: malformed native row") from exc


def compaction_markers(values):
    markers = []
    for row in values:
        payload = row.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        item = payload.get("item")
        item = item if isinstance(item, dict) else {}
        tags = [
            row.get("type"),
            row.get("subtype"),
            row.get("event"),
            payload.get("type"),
            item.get("type"),
        ]
        matched = [
            tag for tag in tags if isinstance(tag, str) and "compact" in tag.lower()
        ]
        if matched:
            markers.append({"timestamp": row.get("timestamp"), "tags": matched})
    return markers


def codex(path):
    models, efforts, windows = set(), set(), set()
    compactions = []
    latest = None
    peak = 0
    count = 0
    for row in native_rows(path):
        count += 1
        payload = row.get("payload") or {}
        compactions.extend(compaction_markers([row]))
        if row.get("type") == "turn_context":
            if payload.get("model"):
                models.add(payload["model"])
            if payload.get("effort"):
                efforts.add(payload["effort"])
        if row.get("type") == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info") or {}
            if info.get("model_context_window"):
                windows.add(info["model_context_window"])
            usage = info.get("last_token_usage") or {}
            peak = max(peak, usage.get("input_tokens", 0))
            if info.get("total_token_usage"):
                latest = info
    return {
        "native_rows": count,
        "models": sorted(models),
        "efforts": sorted(efforts),
        "effective_context_windows": sorted(windows),
        "compaction_markers": compactions,
        "peak_input_tokens": peak or None,
        "last_input_tokens": (latest or {})
        .get("last_token_usage", {})
        .get("input_tokens"),
        "usage": (latest or {}).get("total_token_usage"),
        "usage_scope": "this native session, cumulative; not a bill or a run-wide sum",
    }


def observe(root):
    root = Path(root).expanduser().resolve()
    if digest(root / "plan.json") != read(root / "plan-digest.json")["sha256"]:
        raise ValueError("frozen plan changed")
    plan = read(root / "plan.json")
    if plan.get("schema") != 1:
        raise ValueError("unsupported record layout for native observation")
    seats = {}
    for seat in plan["seats"]:
        name = identifier(seat["id"])
        harness = seat["config"]["harness"]
        private = root / "work" / name / "seat.blind-state" / harness
        patterns = NATIVE_TRANSCRIPTS
        if harness not in patterns:
            seats[name] = {
                "supported": False,
                "reason": "unknown native transcript location",
            }
            continue
        observations = []
        violations = set()
        red_flags = set()
        for path in sorted(private.glob(patterns[harness])):
            if harness == "codex":
                observed = codex(path)
            else:
                values = list(native_rows(path))
                observed = {
                    "native_rows": len(values),
                    "compaction_markers": compaction_markers(values),
                    "models": [],
                    "efforts": [],
                    "usage": None,
                    "note": "compaction signals only; use final adapter receipts for identity and usage",
                }
            observed["source"] = str(path.relative_to(root))
            observations.append(observed)
            if observed["compaction_markers"]:
                red_flags.add("compaction: supervisor must review the run design")
            if (
                observed["compaction_markers"]
                and not seat["policy"]["allow_compaction"]
            ):
                violations.add("compaction despite allow_compaction=false")
            if observed["models"] and observed["models"] != [seat["config"]["model"]]:
                violations.add("native model differs from plan")
            if observed["efforts"] and observed["efforts"] != [
                seat["config"]["effort"]
            ]:
                violations.add("native effort differs from plan")
        seats[name] = {
            "supported": True,
            "available": bool(observations),
            "sessions": observations,
            "policy_violations": sorted(violations),
            "red_flags": sorted(red_flags),
        }
    return {"record": str(root), "diagnostic_only": True, "seats": seats}
