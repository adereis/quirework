# Quirework

Quirework is a set of tools for writing and researching a book with an
AI companion such as Claude Code or Codex. The companion works in your
book project. Quirework gives it a durable spec, named procedures,
consistency checks, publication tools, and controlled ways to ask other
models for an independent view.

You can run every command yourself. That is not the workflow Quirework
was made for. The normal workflow is to tell your companion what you
want to accomplish, let it follow the project's procedure, and judge the
evidence it brings back.

Quirework does not write your book and it does not settle editorial
questions. It gives the collaboration a memory and a method. It builds
the artifacts, catches drift across chapters, and preserves enough
evidence for you to make the call.

Any Quarto book can use it. One installation serves several, because a
project is selected by a `quirework.toml` found from the working
directory and never by where the package happens to live.

## The default workflow

The author sets the aim and keeps editorial authority. The AI companion
coordinates the work. Quirework makes that work inspectable and
repeatable.

A typical revision starts with a request in Claude Code or Codex. The
companion then follows this loop:

1. Read the project's guide and resolve the relevant SOP with
   `quirework sop explain NAME`.
2. Load the book's commitments, chapter guidance, and bindings with
   `quirework book notes --primer`.
3. Inspect the prose and any prior human decisions needed for this task.
4. Draft or revise inside the limits of the SOP.
5. Use a writers room, a prose workshop, or blind readers when the task
   benefits from independent perspectives.
6. Reconcile those reports against the spec instead of treating a model
   answer as authority.
7. Run the structural, editorial, and publication checks named by the
   SOP.
8. Return the change, the evidence, and any remaining author decision.

The spec is the center of that loop. It states what the book currently
holds. Chapter sidecars bind passages to those commitments. When one
commitment changes, the companion can find every chapter that expresses
it before a local edit becomes a book-wide contradiction.

SOPs make the rest of the loop project-specific. A book can define how
its companion writes a chapter, checks a claim, researches a source,
runs a workshop, or prepares a blind read. Quirework catalogs those
procedures and generates thin skill routes for Claude Code and portable
agents. The SOP remains the source of truth.

The foreground companion is not tied to the models it supervises. A
Codex session can send a blind reading to Claude. A Claude Code session
can convene a room with Codex, Claude, and Antigravity seats. Each seat
declares its harness, model, effort, materials, and access policy. The
receipt records what the provider actually served.

The [workflow guide](docs/workflows.md) develops this loop through four
especially useful patterns: writers rooms, independent prose workshops,
blind-reader panels, and contextual consistency reviews.

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
never disturbs them. `book notes --check` gates eleven invariants: a
mark left in the prose, a sidecar that no longer mirrors its chapter, a
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

`book trail` connects that record to the spec without putting any of it
in the spec. A trail sidecar, kept beside the private corpus, points
each spec section at the conversations where the author seeded,
sharpened or reversed it: a session and the time a turn was logged, not
a copy of the words. A companion about to change a commitment reads the
author's own turns, oldest first, and opens as much of each conversation
as it needs.

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

### Turns procedures into something your companion can follow

Procedures are files the project writes, and the catalog makes them
navigable: `sop explain NAME` prints required reading in dependency
order with its word count, while follow-up work is recorded and
deliberately never traversed for you. Personas say who is reading,
instruments say what they are asked, and both are versioned data
composed at preparation and frozen with the run. `sop skills` generates
thin entry points so Claude Code or Codex can be pointed at a procedure
by name.

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
Enforced filesystem isolation uses bubblewrap on Linux, and an
Antigravity seat also needs `xdg-dbus-proxy`. PDF and SVG
operations use Poppler and librsvg when the selected operation needs
those formats. Missing required dependencies produce an error.

## Let the companion choose the tools

You normally work by describing the job to your AI companion. The
command groups below are the vocabulary it uses to carry out that job:

```text
quirework book        Build and inspect book artifacts
quirework text        Format and inspect writing
quirework provenance  Capture, organize, and search source history
quirework sop         Find procedures and operate supervised workflows
quirework dev         Run project checks
quirework config      Inspect project configuration
quirework usage       Report which commands, procedures and seats get used
```

You can also run the commands directly. Add `--help` at any level to
inspect the actual options. Help does not require a book, a private
corpus, or a provider account.

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
chapters, figures and its own procedures, and
[examples/demo-book](examples/demo-book) is a small finished book that
walked it: a spec, marks, a figure, procedures, a derived edition and
the session it was written in, in about forty files.

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
