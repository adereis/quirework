# Architecture and verification

Quirework has one distribution and one namespace. Command discovery is
standard-library argparse and loads domain implementations lazily.
Parsing and presentation stay outside the reusable execution engine.

| Namespace | Responsibility |
|---|---|
| `manuscript` | Units, spec, marks, quotations, trail: the written work in any format |
| `publish` | Rendering, figures, editions, rendered review artifacts |
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
layers import neither `manuscript` nor `publish`. Executor snapshots
include their runtime dependencies without copying publishing code or
book content.

## The jail

`providers.blind` confines every seat with bubblewrap, and it is an
allow-list all the way down. The home is blanked and a list of paths is
bound back. The root is the same: a fixed set of system trees bound
read-only at their own paths, and nothing else. Do not reintroduce a
whole-root bind or a deny-list of filesystem types. Every earlier
repair to the root followed from that shape: a stale network share
failed the bind, the mount table changed under the jail, and a
read-write root left writable paths behind the blanked set. A runner
that needs a tree outside the list fails at launch; add the tree to
`OPTIONAL_TREES`, do not bind `/`.

`/run/user/<uid>` is never bound, because the session bus there lets
`systemd --user` start a process outside any sandbox. A runner that
must reach a bus name declares it in `Runner.bus_names`. The caller
then opens `blind.session_bus` around the launch, passes it to the
jail, and starts the process with `blind.popen`, which hands bwrap the
proxy's pipe. The proxy lives exactly as long as the jail.

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

## The commit gate

Enable the hook once per clone. Git does not run a hook out of the
working tree on its own, so a hook nobody enables is the same as no
hook:

```bash
git config core.hooksPath hooks
```

`hooks/pre-commit` runs `quirework dev check --staged`, which parses
every staged Python file and asserts that `ruff format` would leave it
alone. `ruff` must be on `PATH`; it is a contributor tool rather than a
package dependency, so a standard install does not supply it.

The hook refuses the commit when it cannot run those checks, whether
Quirework is not installed or the formatter is missing. A gate that
passes when its tools are absent is the gate this repository already
had. That is stricter than `dev check` itself, which tolerates a missing
formatter because a consuming book need not use one; this tree declares
that it sits at the formatter's fixed point and cannot assert that
without the formatter. While the tree is under heavy development the
friction of a blocked commit is worth more than the drift.

The tree sits at the formatter's fixed point, which is what lets a
contributor run `ruff format` out of tidiness and get an empty diff. The
gate exists because that invariant was not holding on its own:
formatting drift arrived inside ordinary feature commits and was swept
up later by commits that did nothing else, twice in the first
seventy-seven. A sweep is not a fix, since the drift is invisible in
review and the sweep hides the next one.

`--no-verify` skips the hook for a commit that genuinely should not be
gated. `quirework dev check --list` describes the suite without running
it. A consuming book selects its own suite with `checks.command`, and
the hook then runs that instead.

## Tests

```bash
.venv/bin/python -m pytest -m 'not live'
```

The suite refuses to run against any Quirework but this checkout's. A
shell with a consuming book's environment active would otherwise import
the book's pinned copy, test it, and pass.

Unit tests use fictional text and diagrams. They check project
isolation, custom catalogs, formatting, artifact transforms, independent
seats, interrupted receipts, forks, quota recovery, and immutable
snapshots. A consuming book keeps its own integration tests for its questions
and project-specific visual rules.

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
