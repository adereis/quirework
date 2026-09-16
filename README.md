# Quirework

Quirework is a book-writing and research companion. It brings
publishing, text inspection, provenance, and supervised model workflows
into one modular Python tool. Each book supplies its content and
configuration.

## Install

Python 3.11 or later is required. From this checkout:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -e '.[test]'
```

Install `.[book]` for Quarto rendering, `.[capture]` for browser
capture, or `.[semantic]` for local semantic search. `.[dev]` includes
all extras. Copy `.envrc.example` to `.envrc` to use direnv.

The core and command discovery use the Python standard library. Model
runs use separately installed Codex, Claude Code, or Antigravity CLIs.
Enforced filesystem isolation uses bubblewrap on Linux. PDF and SVG
operations use Poppler and librsvg when the selected operation needs
those formats. Missing required dependencies produce an error.

## Choose an activity

```text
quirework book        Build and inspect book artifacts
quirework text        Format and inspect writing
quirework provenance  Capture, organize, and search source history
quirework sop         Find procedures and operate supervised workflows
quirework dev         Run project checks
quirework config      Inspect project configuration
```

Add `--help` at any level to inspect the actual options. Help does not
require a book, a private corpus, or a provider account.

Common operations include:

```bash
quirework book build --to html
quirework book redpen build --chapter opening
quirework text wrap manuscript/opening.qmd
quirework provenance search 'an earlier idea'
quirework sop list
quirework sop explain review
quirework sop runs prepare plan.json /path/to/private/run
quirework sop runs start /path/to/private/run --timeout 60 --wait-quota 0
```

Starting a book from nothing? The
[authoring guide](docs/authoring.md) is the path from an empty directory
to a manuscript with a spec, marked chapters, figures and its own
procedures.

The [configuration guide](docs/configuration.md) explains how to connect
a book. The [workflow guide](docs/workflows.md) covers procedures,
research, model runs, and saved evidence. The
[development guide](docs/development.md) covers architecture and tests.

## A book owns its choices

Place `quirework.toml` at the book project root. Quirework discovers it
from the working directory, including subdirectories. Use
`quirework --project PATH ...` to select a different project explicitly.
Installing Quirework never selects a book by the package's location.

Quarto remains the source of publication settings and reading order.
Personas, questions, custom procedures, editorial policies, and private
corpora belong to the book project. A project without provenance can use
the publishing and run tools independently.

Quirework is one distribution with one Python namespace. Its domain
modules are designed for reuse without the CLI or a particular book. The
software is licensed under the MIT license.
