"""Read-only native progress diagnostics, independent of executor replay.

Observations neither mutate run state nor certify a completed result. This
version observes the native JSONL locations used by the shared adapters.
Because it runs from the installed package rather than a record's frozen
executor, it also checks records banked by older code for conditions their
own receipts could not see.
"""

import json
import time
from collections import Counter
from pathlib import Path

from quirework.providers import blind
from quirework.providers import models as drivers
from quirework.providers import replies
from quirework.runs.storage import digest
from quirework.runs.storage import identifier
from quirework.runs.storage import process_identity
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


# What a Claude transcript cannot show yet, said instead of empty fields.
CLAUDE_PENDING = (
    "no response has completed yet. Claude Code writes a response to its "
    "transcript only once the response completes, so a turn still thinking "
    "shows here only when it ends; the seat's progress follows it meanwhile"
)
CLAUDE_USAGE = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)


def claude(path):
    """What a Claude session's transcript says so far.

    Each completed response carries its model, effort, usage and the reason
    it stopped. The rows of one response repeat its usage, so it is counted
    once per response.
    """
    rows = list(native_rows(path))
    models, efforts, usage, stops = set(), set(), {}, Counter()
    for row in rows:
        message = row.get("message")
        if row.get("type") != "assistant" or not isinstance(message, dict):
            continue
        if message.get("model") not in (None, "<synthetic>"):
            models.add(message["model"])
        if row.get("effort"):
            efforts.add(row["effort"])
        ident = message.get("id")
        if ident and isinstance(message.get("usage"), dict):
            if ident not in usage:
                stops[message.get("stop_reason")] += 1
            usage[ident] = message["usage"]
    observed = {
        "native_rows": len(rows),
        "compaction_markers": compaction_markers(rows),
    }
    if not usage:
        return observed | {"unavailable": CLAUDE_PENDING}
    return observed | {
        "models": sorted(models),
        "efforts": sorted(efforts),
        "responses": len(usage),
        "responses_at_output_limit": stops[replies.LIMIT],
        "usage": {
            key: sum((u.get(key) or 0) for u in usage.values()) for key in CLAUDE_USAGE
        },
        "usage_scope": "this native session's completed responses, cumulative; "
        "not a bill or a run-wide sum",
    }


def progress(root, name, harness, declared):
    """What a seat is doing now, read from the run's own record of it.

    A native transcript says what a session finished, and Claude Code
    writes nothing there until a response completes, which at high effort
    can take many minutes. The turn's event stream is written as the
    harness works: Claude Code adds an estimate of its thinking every few
    seconds, and Codex each item it completes. So whether a seat is thinking
    or has stalled is read there, from how recently it was written and what
    it says.
    """
    path = root / "state" / f"{name}.json"
    state = read(path) if path.exists() else {}
    banked = f"{len(state.get('done', {}))} of {declared} turns banked"
    pending = state.get("pending")
    if state.get("complete"):
        return dict(state="complete", turns=banked)
    if not pending:
        if state.get("done") or state.get("attempts") or state.get("session_id"):
            return dict(state="between turns", turns=banked)
        return dict(state="not started", turns=banked)
    attempt = state.get("attempts", {}).get(pending["turn"], 0)
    alive = bool(pending.get("pid")) and process_identity(
        pending["pid"]
    ) == pending.get("process_identity")
    if alive:
        doing = "running"
    elif pending.get("pid"):
        doing = "exited, not yet banked"
    else:
        doing = "launching"
    now = time.time()
    found = dict(state=doing, turn=pending["turn"], attempt=attempt, turns=banked)
    if pending.get("created_at"):
        found["elapsed_seconds"] = round(now - pending["created_at"])
    events = (
        root / "records" / name / pending["turn"] / f"attempt-{attempt:03d}"
    ) / "events.jsonl"
    if not events.is_file() or not events.stat().st_size:
        found["events"] = "none yet: the harness has written nothing for this turn"
        return found
    rows = list(native_rows(events))
    found |= dict(
        events=len(rows),
        seconds_since_last_event=round(now - events.stat().st_mtime),
    )
    if harness == "claude":
        found |= claude_progress(rows)
    elif harness == "codex":
        found["items_completed"] = dict(
            Counter(
                (r.get("item") or {}).get("type")
                for r in rows
                if r.get("type") == "item.completed"
            )
        )
    return found


def claude_progress(rows):
    """A Claude turn in flight, from its stream.

    Claude Code reports an estimate of its thinking while it thinks,
    counting from zero again for each request, with the step since the last
    report beside it; the steps add up to the turn's whole estimate. A
    response's text arrives in one row once the text is complete, so after
    thinking ends the stream is quiet while the reply is written.
    """
    thinking = [r for r in rows if r.get("subtype") == "thinking_tokens"]
    found = dict(
        thinking_tokens_estimated=sum(
            r.get("estimated_tokens_delta") or 0 for r in thinking
        )
    )
    last = rows[-1]
    blocks = (last.get("message") or {}).get("content")
    if last.get("type") == "result":
        found["now"] = "finished"
    elif last.get("subtype") == "thinking_tokens":
        found["now"] = "thinking"
    elif (
        last.get("type") == "assistant"
        and isinstance(blocks, list)
        and blocks
        and blocks[-1].get("type") == "thinking"
    ):
        found["now"] = "writing, after thinking"
    else:
        found["now"] = "between responses"
    if injected := sum(1 for r in rows if r.get("isSynthetic")):
        found["harness_messages"] = injected
    return found


def banked_answers(root, name):
    """Each banked Claude turn's answer, checked against the seat's whole reply.

    A receipt made before replies were rebuilt across the output ceiling
    banked only the last response of a reply that reached it, and said
    nothing. The turn's own stream and its frozen copy of the native session
    still hold every part, so the whole reply can be rebuilt here and the
    banked answer measured against it. ``{turn: finding}`` plus the red
    flags they raise.
    """
    path = root / "state" / f"{name}.json"
    done = read(path).get("done", {}) if path.exists() else {}
    plan = root / "plan.json"
    seat = next(
        (
            s
            for s in (read(plan).get("seats", []) if plan.exists() else ())
            if s.get("id") == name
        ),
        {},
    )
    declared = (seat.get("policy") or {}).get("output_ceiling")
    found, flags = {}, []
    for turn, banked in done.items():
        record = (
            root / "records" / name / turn / f"attempt-{banked.get('attempt', 0):03d}"
        )
        files = [
            record / f for f in ("events.jsonl", "native.jsonl", "answers/answer.md")
        ]
        if not all(f.is_file() for f in files):
            continue  # an imported answer has no native evidence to check
        stream = list(native_rows(files[0]))
        if not any(r.get("type") == "result" for r in stream):
            continue
        whole, evidence = replies.claude(stream, native_rows(files[1]))
        kept = files[2].read_text()
        complete = kept == whole + "\n"
        found[turn] = dict(
            evidence,
            banked_answer="whole reply" if complete else "part of the reply",
            banked_characters=len(kept.removesuffix("\n")),
            reply_characters=len(whole),
        )
        flags += [f"turn {turn}: {flag}" for flag in replies.flags(evidence, declared)]
        if not complete:
            flags.append(
                f"turn {turn}: the banked answer holds "
                f"{found[turn]['banked_characters']:,} of the reply's "
                f"{len(whole):,} characters; the whole reply is in the turn's "
                "events.jsonl and native.jsonl"
            )
    return found, flags


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
        private = blind.state_root(root / "work" / name / "seat") / harness
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
            elif harness == "claude":
                observed = claude(path)
            else:
                values = list(native_rows(path))
                observed = {
                    "native_rows": len(values),
                    "compaction_markers": compaction_markers(values),
                    "unavailable": "this transcript is read for compaction "
                    "only; the turn's receipt gives identity and usage",
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
            # A Claude seat may name a family alias, which any version of
            # the family satisfies; the others name the exact model.
            served = observed.get("models", [])
            if served and (
                any(
                    not drivers._model_matches(seat["config"]["model"], m)
                    for m in served
                )
                if harness == "claude"
                else served != [seat["config"]["model"]]
            ):
                violations.add("native model differs from plan")
            if observed.get("efforts") and observed["efforts"] != [
                seat["config"]["effort"]
            ]:
                violations.add("native effort differs from plan")
        seats[name] = {
            "supported": True,
            "available": bool(observations),
            "progress": progress(root, name, harness, len(seat.get("turns", []))),
            "sessions": observations,
        }
        if harness == "claude":
            answers, flags = banked_answers(root, name)
            seats[name]["answers"] = answers
            red_flags.update(flags)
        seats[name] |= {
            "policy_violations": sorted(violations),
            "red_flags": sorted(red_flags),
        }
    return {"record": str(root), "diagnostic_only": True, "seats": seats}
