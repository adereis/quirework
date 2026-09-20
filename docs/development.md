# Architecture and verification

Quirework has one distribution and one namespace. Command discovery is
standard-library argparse and loads domain implementations lazily.
Parsing and presentation stay outside the reusable execution engine.

| Namespace | Responsibility |
|---|---|
| `book` | Publication structure, rendering, annotations, review artifacts |
| `text` | Formatting and editorial inspection |
| `provenance` | Runtime readers, capture, catalog, retrieval, measurements |
| `sops` | Procedure discovery, personas, instruments, workflow preparation |
| `runs` | Execution state, recovery, checkpoints, evidence |
| `providers` | Native invocation, model resolution, receipts, isolation |
| `delivery` | Frozen material transforms and turn delivery |
| `core` | Artifact classification, project check dispatch, pinned tools, usage records |

Some operations need a program too large to vendor: an EPUB validator,
an e-reader renderer. `core.pinned` fetches one by version, URL and
SHA-256 into the shared cache, hashing the archive before anything is
unpacked so a substituted download never lands as an executable. A
consumer keeps its own pin and calls `pinned.ensure`; a tool Quirework
does not ship a command for is not Quirework's version to choose.

A book depends on Quirework. Quirework never imports a book's extension
unless that project explicitly selects it. The execution and provider
layers do not import book workflows. Executor snapshots include their
runtime dependencies without copying publishing code or book content.

## Configuration lifetime

A `Project` is immutable. Public entry points select it explicitly.
Internal helpers use an operation-local context variable rather than
mutable module-level paths. The context is restored on exceptions.
Policy data is read once per project instance and returned as isolated
copies. Load a new instance to observe edits made after that snapshot.
Thread submissions capture the operation context explicitly. Subprocess
and frozen-run boundaries serialize the selected settings. Package
installation paths are used only to locate implementation resources,
never the book.

## Tests

```bash
.venv/bin/python -m pytest -m 'not live'
```

Unit tests use fictional text and diagrams. They check project
isolation, custom catalogs, formatting, artifact transforms, independent
seats, interrupted receipts, forks, quota recovery, and immutable
snapshots. The consuming book keeps its own integration tests, including
exact parity with its historical questions and project-specific visual
rules.

Run a minimal unrelated book through rendering as an installation check.
Run the consumer's gates and relevant publication formats before
releasing a migration. These checks do not call a model.

Live tests require explicit authorization. Use inexpensive models, tiny
synthetic assignments, no automatic quota retries, and a hard wall-clock
limit. Development smoke tests must not receive book content or large
writing assignments. Count the provider's own prompt overhead as well as
the supplied material.

Codex and Claude support explicit replacement system instructions. Codex
uses `model_instructions_file`; an unknown configuration key can be
silently ignored, so verify the native receipt and input usage. No-tools
Codex runs disable the shell, unified execution, apps, and local
image-view tool. Attached image delivery remains a separate capability.
Claude uses `--system-prompt` with dynamic system sections excluded.
Antigravity does not expose the same replacement control.

The supported Codex settings are documented in the
[official configuration reference](https://developers.openai.com/codex/config-reference/).
A requested setting is not evidence that the native client honored it.

Keep raw test sessions and runtime homes outside Git. Retain only
synthetic fixtures and the tests themselves. Record relevant validation
results with each release or integration change.
