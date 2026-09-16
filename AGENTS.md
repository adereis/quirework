# Quirework contributor guide

Quirework is a reusable book-writing and research companion. Books are
consumers of this package and supply their own content, configuration,
personas, procedures, and editorial rules.

Quirework is the tool, never a book. Do not import a consumer's
vocabulary, claims, or private material into this repository, and do not
carry a consumer's guidance into a change here. A session is usually
driven from a book checkout, so this is the boundary an assistant
arriving with that project's instructions is most likely to cross.

## Architecture

Keep one distribution and one Python namespace. Domain modules expose
services independently of command parsing. Provider adapters and the run
engine must not import book-specific workflows. Resolve project settings
at the operation boundary, never from the installed package location.
Preserve isolation across projects, worktrees, threads, and processes.

Configuration describes project choices. Tested code implements provider
protocols, recovery, capability validation, and evidence integrity.
Require explicit configuration for private corpora and custom
extensions.

## Development

Python environments must use
`python3 -m venv --system-site-packages .venv`. A contributor's own
scratch files go under `~/tmp`, never `/tmp`; what the tool itself
writes at run time follows the configuration guide's placement rules.
Keep dependency-heavy imports out of help paths. Run relevant tests and
update documentation with implementation changes. Use only fictitious
test inputs. Never copy book content, private transcripts, credentials,
or runtime homes into this repository.

Keep the public API and command interface understandable to an author
using a different book project, and verify independent installation and
project isolation. Preserve execution evidence through refactors. An
uncertain provider request must be reconciled before any resubmission. A
completed answer with a failed run condition remains available with its
limitations.

## Git

Stage only reviewed files. Keep each commit on one side of the
tool-and-consumer boundary: a change to Quirework under `src/`, `docs/`
or `tests/` never shares a commit with a change to an example book under
`examples/`, so the tool's own history can be reviewed without reading
around a book's content. Keep commit subjects within 50 columns and
prose body lines within 72 columns. Non-trivial commits explain their
motivation and important invariants. End commits with a Co-Authored-By
trailer identifying the model established by the harness. Do not push
unless the user authorizes it. Preserve unrelated concurrent changes.
