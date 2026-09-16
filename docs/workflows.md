# Procedures and model runs

An SOP remains a procedure the operator follows. Its catalog describes
required reading, mechanical tools, and conditional follow-ups. A link
to another procedure never launches it automatically.

## Discovery and assignments

`quirework sop list` and `quirework sop explain NAME` read the project's
catalog. `quirework sop skills --target both` generates thin entry
points for Claude and portable agents. The SOP remains the content
owner. Project configuration selects the skill-name prefix.

Personas specify perspectives. Instruments specify questions. Panels
specify an allocation of perspectives and models. None grants filesystem
access. Preparation resolves the selected data and freezes the composed
assignment alongside its source identities.

Research is a supervised workflow. A research assignment can request
external source access through its tool policy. The operator still
checks sources and evaluates the answer. Provenance retrieval and model
output are evidence to inspect.

## Run plans

A minimal plan contains explicit seats and ordered turns:

```json
{
  "title": "Read a small note",
  "seats": [{
    "id": "reader",
    "harness": "codex",
    "model": "gpt-5.6-luna",
    "effort": "low",
    "policy": {"tools": "none", "filesystem": "enforced"},
    "turns": [{
      "id": "opening",
      "prompt": "What is clear in this note? Answer briefly.",
      "materials": [{"source": "note.md", "delivery": "text"}]
    }]
  }]
}
```

The model is an explicit choice. `quirework sop models list PROVIDER`
and `resolve PROVIDER MODEL --effort LEVEL` inspect available
information without starting a model session. Unsupported capabilities
fail during preparation. A native model alias remains explicit; the
receipt records the model actually served.

`quirework book plan` compiles rendered book packets, comparisons, and
bundles. `quirework sop panel` compiles a declared panel. These helpers
live above the generic execution engine.

Supported delivery includes text, clean prose, prose with figures,
images, rendered PDF pages, and native files. Delivery and exposure are
separate choices. A native file receipt does not establish that its
pages were inspected visually.

## Execution and recovery

Use `sop runs prepare`, `start`, `inspect`, `observe`, `collect`,
`verify`, and `export`. Preparation freezes inputs, settings, questions,
and the generic executor. Each run has separate seats, ordered turns,
attempts, and checkpoints.

Launch intent is recorded before process creation. A pending request
must be reconciled before another submission. `collect` recovers
existing evidence without buying a new answer. An explicit retry is
allowed only after a provable terminal rejection without an answer.
Earlier answers and rejected attempts remain immutable.

Native forks preserve their parent boundary and image ancestry. A
provider without native forks cannot substitute a summary or replay.
Tool restrictions and filesystem enforcement are checked separately.
Antigravity currently provides audited rather than enforced filesystem
assurance; it cannot satisfy a requirement for enforced isolation.

Compaction is always reported as a red flag. Completed answers remain
available, but a failed run condition does not become a clean result.
Export copies answers and curated receipts. It does not publish them or
copy runtime homes and raw event streams.

Existing records dispatch through their verified frozen executor.
Historical commands, prompts, answers, and hashes remain unchanged. The
current command surface translates a start request when dispatching a
supported pre-extraction executor. A provider version mismatch still
requires attention rather than silent reinterpretation.

Workshop and contextual-review commands retain their specialized state
and manual-session features. They share provider adapters and account
leases with the generic engine.

Capture schema identifiers remain unchanged for compatibility with
stored transcripts. Project redaction policies are separate data.
Changing a marker list changes the analysis recipe; a verified legacy
marker fingerprint preserves existing cache identities for the unchanged
book policy. Primary transcripts are never rewritten by redaction.
