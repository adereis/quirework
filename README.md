# Quirework

Quirework is a book-writing and research companion. It publishes a book,
keeps a long argument consistent with itself, remembers how the work
happened, and puts the result in front of a model under controlled
conditions. Each book supplies its own content, vocabulary and editorial
rules; the tool supplies the machinery and holds no opinion about what
you are arguing.

It does not write your book and it does not settle editorial questions.
It builds the artifacts, catches the drift a person cannot see across
thirty chapters, and hands you evidence to judge.

Any Quarto book can use it. One installation serves several, because a
project is selected by a `quirework.toml` found from the working
directory and never by where the package happens to live.

## What it does

### Publishes one source to four editions, and proves they agree

`book build` renders a Quarto manuscript to HTML, PDF through Typst,
EPUB and DOCX. It stages three trees to do it, because the formats
disagree about what they will carry, and it checks afterwards that a
figure has the same number in every edition rather than assuming so. The
source tree is never written into.

It also does the things that go wrong silently. It generates a grayscale
figure set from the color one, because e-ink desaturates color into mud.
It validates the EPUB with the W3C reference checker, pinned by version
and checksum, because a file every desktop reader opens can still be
rejected by a retailer. It refuses to render in a substituted typeface,
because Typst and rsvg substitute without complaining and the whole book
comes out in the wrong face.

### Keeps the claims from drifting apart

A book that argues something has commitments, and after a year they live
in one chapter's phrasing and contradict another's. Quirework gives them
a file. Each commitment is a section with a slug, each chapter names the
sections it expresses, and `book notes bind --slug S` answers the
question that matters when you change one: which other chapters have to
change with it.

The annotations live in a sidecar beside each chapter rather than in the
prose, so the rendered book never carries them and reflowing a paragraph
never disturbs them. `book notes --check` gates ten invariants: a mark
left in the prose, a sidecar that no longer mirrors its chapter, a
binding that names a commitment the spec does not declare, a reading
order that disagrees with the files on disk. `book quotes` checks that a
phrase quoted in metadata still exists in the book.

### Formats and inspects the prose

`text wrap` applies the project's own conventions, and it can apply
different ones per path: one sentence per line in chapters, hard wrapped
at a column in guides. `text check` reports field jargon in
reader-facing prose against a term list the project writes, so a word
must be avoided, glossed on first use, or confined to where it belongs.

### Remembers how the book was written

`provenance` captures your Claude Code, Codex and Antigravity sessions
from their local stores, and web conversations too. `provenance search`
then reads the human's own turns, by keyword or by meaning; the semantic
index is built and stored locally, so corpus text never leaves the
machine.

On top of that corpus it measures things about the making. `metrics`
estimates the division of labor between the human and the AI, with its
caveats attached rather than laundered away. `input` compiles the
human's verbatim words. `coldread` puts the record in front of blind
analysts and caches their reports by content, so changing the recipe
misses the cache deliberately rather than reusing an answer to a
different question.

### Runs models under conditions you declared

A run is a plan: seats, ordered turns, an explicit model and effort.
Prepare freezes the prompts and the inputs and hashes them, start
executes through Codex, Claude Code or Antigravity, and the receipt
records the model actually served rather than the one requested.

The controls are the point. A seat can be confined by the operating
system rather than asked nicely, and where a provider can only offer an
audited boundary the run says so instead of claiming isolation. Delivery
is a separate choice from exposure: plain text, prose with its figures,
rendered PDF pages, native file attachments. A pending request is never
blindly resubmitted, a run stopped by a quota limit is continued in its
own session rather than restarted, and a compaction event is always
reported even when the answer looks fine. `export` hands back the
answers and a sanitized receipt.

### Turns procedures into something an agent can follow

Procedures are files the project writes, and the catalog makes them
navigable: `sop explain NAME` prints required reading in dependency
order with its word count, while follow-up work is recorded and
deliberately never traversed for you. Personas say who is reading,
instruments say what they are asked, and both are versioned data
composed at preparation and frozen with the run. `sop skills` generates
thin entry points so an agent can be pointed at a procedure by name.

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

Everything above is reached through six command groups:

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

Starting a book from nothing? The [authoring guide](docs/authoring.md)
is the path from an empty directory to a manuscript with a spec, marked
chapters, figures and its own procedures.

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
