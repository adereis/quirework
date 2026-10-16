"""Verify image identities in native session ancestry."""

import base64
import hashlib
import json
from pathlib import Path

# Claude Code's records of the requests it sent, not turns of the dialogue.
# A blob stores a request's messages by hash, so when the client retries a
# stream that was cut off, the retried turn's images appear in a blob as
# well as in the user row that delivered them. Counting both reports a
# figure the reader was shown once as shown twice.
REQUEST_RECORDS = frozenset({"api-request", "api-request-shape", "api-request-blob"})


def _agy_media_hashes(transcript, rows):
    """Hash the images an agy session was shown, from the copies agy keeps.

    agy records an image it showed the model as a step's `media`, naming a
    file it copied into the session's own directory, beside the transcript's
    `.system_generated/logs/`. The name is the path inside the jail, so only
    the part from `brain/` on is kept and resolved against this session's
    directory on the host. A copy that is missing is not counted: the check
    it feeds then reports a shortfall rather than proof.
    """
    if "step_index" not in (rows[0] if rows else {}):
        return []
    brain = transcript.parents[3]
    hashes = []
    for row in rows:
        if row.get("status") != "DONE":
            continue
        for item in row.get("media") or []:
            uri = str(item.get("uri", "")) if isinstance(item, dict) else ""
            parts = Path(uri).parts
            if "brain" not in parts:
                continue
            copy = brain.joinpath(*parts[parts.index("brain") + 1 :]).resolve()
            if copy.is_file() and copy.is_relative_to(brain.resolve()):
                hashes.append(hashlib.sha256(copy.read_bytes()).hexdigest())
    return hashes


def history_image_hashes(rollout, home, seen=None):
    """Audit persisted image bytes, following Codex's native parent references.

    Recent Codex forks store new turns plus a parent reference, whereas Claude
    copies its earlier messages into the child transcript. Neither shape may
    be mistaken for evidence that the reader lost its earlier pages. agy
    stores no image in its transcript at all, only a copy beside it.
    """
    path = Path(rollout)
    seen = set() if seen is None else seen
    if path in seen:
        raise RuntimeError("cycle in native fork history")
    seen.add(path)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    hashes = []

    def visit(value):
        if isinstance(value, dict):
            if value.get("type") == "input_image":
                url = value.get("image_url", "")
                if not url.startswith("data:image/") or ";base64," not in url:
                    raise RuntimeError("persisted image is not self-contained")
                hashes.append(
                    hashlib.sha256(base64.b64decode(url.split(",", 1)[1])).hexdigest()
                )
            elif value.get("type") == "image" and isinstance(value.get("source"), dict):
                source = value["source"]
                if source.get("type") != "base64":
                    raise RuntimeError("persisted Claude image is not self-contained")
                hashes.append(
                    hashlib.sha256(base64.b64decode(source["data"])).hexdigest()
                )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit([row for row in rows if row.get("type") not in REQUEST_RECORDS])
    hashes.extend(_agy_media_hashes(path, rows))
    for row in rows:
        if row.get("type") == "session_meta":
            parent = row["payload"].get("forked_from_id")
            if parent:
                parents = list((Path(home) / "sessions").glob(f"**/*{parent}.jsonl"))
                if len(parents) != 1:
                    raise RuntimeError("fork history has a missing or ambiguous parent")
                hashes.extend(history_image_hashes(parents[0], home, seen))
    return hashes
