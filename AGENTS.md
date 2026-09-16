# Quirework contributor guide

Quirework is a reusable book-writing and research companion. Books are
consumers of this package and supply their own content, configuration,
personas, procedures, and editorial rules.

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

Read CODEX.md before substantive work. Python environments must use
`python3 -m venv --system-site-packages .venv`. Use temporary files
under `~/tmp`, never `/tmp`. Keep dependency-heavy imports out of help
paths. Run relevant tests and update documentation with implementation
changes. Use only fictitious test inputs. Never copy book content,
private transcripts, credentials, or runtime homes into this repository.

## Git

Stage only reviewed files. Keep commit subjects within 50 columns and
prose body lines within 72 columns. Non-trivial commits explain their
motivation and important invariants. End commits with a Co-Authored-By
trailer identifying the model established by the harness. Do not push
unless the user authorizes it. Preserve unrelated concurrent changes.
