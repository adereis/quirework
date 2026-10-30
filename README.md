# Quirework

Quirework is a set of tools for writing and researching with an AI
companion such as Claude Code or Codex. The work can be a book, a site
of long-lived posts, or anything else that has to stay consistent with
itself while it grows. The companion works in your project. Quirework
gives it a durable spec, named procedures, consistency checks,
publication tools, and controlled ways to ask other models for an
independent view.

You can run every command yourself. That is not the workflow Quirework
was made for. The normal workflow is to tell your companion what you
want to accomplish, let it follow the project's procedure, and judge the
evidence it brings back.

Quirework does not write your work and it does not settle editorial
questions. It gives the collaboration a memory and a method. It builds
the artifacts, catches drift across chapters or posts, and preserves
enough evidence for you to make the call.

A Quarto book can use it, and so can a site of Markdown pages such as a
Hugo blog. One installation serves several, because a project is
selected by a `quirework.toml` found from the working directory and
never by where the package happens to live.

The website, [quirework.com](https://quirework.com), shows the tool at
work: the spec and its trail, the procedures, and a fictional sample
book you can read in every edition it was published in. AI wrote that
book to test the tool, and no person reviewed its prose. Every deploy
renders it again from the same commit of this repository.

## The default workflow

The author sets the aim and keeps editorial authority. The AI companion
coordinates the work. Quirework makes that work inspectable and
repeatable.

A typical revision starts with a request in Claude Code or Codex. The
companion then follows this loop:

1. Read the project's guide and resolve the relevant SOP with
   `quirework sop explain NAME`.
2. Load the work's commitments, the guidance for the chapter or post at
   hand, and its bindings with `quirework spec notes --primer`.
3. Inspect the prose and the prior human decisions the task needs.
   `quirework spec trail SLUG` leads from a commitment to the author's
   own turns that shaped it.
4. Draft or revise inside the limits of the SOP.
5. Convene a writers room, a roundtable, a prose workshop, or blind
   readers when the task benefits from independent perspectives.
6. Reconcile those reports against the spec instead of treating a model
   answer as authority.
7. Run the structural, editorial, and publication checks named by the
   SOP, and an independent consistency review where it asks for one.
8. Return the change, the evidence, and any remaining author decision.

The spec is the center of that loop. It states what the work currently
holds. Sidecars bind passages to those commitments. When one commitment
changes, the companion can find every chapter or post that expresses it
before a local edit becomes a contradiction somewhere else.

SOPs make the rest of the loop project-specific. A project can define
how its companion writes a chapter or a post, checks a claim, researches
a source, runs a workshop, or prepares a blind read. Quirework catalogs
those procedures and generates thin skill routes for Claude Code and
portable agents. The SOP remains the source of truth.

The foreground companion is not tied to the models it supervises. A
Codex session can send a blind reading to Claude. A Claude Code session
can convene a room with Codex, Claude, and Antigravity seats. Each seat
declares its harness, model, effort, materials, and access policy. The
receipt records what the provider actually served.

The
[workflow guide](https://github.com/adereis/quirework/blob/main/docs/workflows.md)
develops this loop through five especially useful patterns: writers
rooms, roundtables of models working together as themselves, independent
prose workshops, blind-reader panels, and contextual consistency
reviews.

## What it does

### Keeps the claims from drifting apart

A work that argues something has commitments, and after a year they live
in one chapter's phrasing and contradict another's. Quirework gives them
a file. Each commitment is a section with a slug, each chapter names the
sections it expresses, and `spec notes bind --slug S` answers the
question that matters when you change one: which other chapters have to
change with it.

A site argues one thing per post, so each post can keep a spec of its
own: its reader, its thesis, its boundaries. A shared spec holds what
several posts lean on, and a post that binds another's claim is listed
when that claim changes.

The shared spec can span several files read as one register, such as
what the work claims in one and how it states it in another. A project
can publish some of those files and keep the rest to itself, and the
gate then refuses a published page that cites a section its reader
cannot open. A slug is a name, and a section can outgrow it.
`spec rename OLD NEW` rewrites a slug everywhere it is written, and
refuses before writing anything if it cannot.

The annotations live in a sidecar rather than in the prose, so the
rendered text never carries them and reflowing a paragraph never
disturbs them. A book keeps them beside each chapter, and a site keeps
them outside the tree its generator renders. A derived edition's
documents bind the same spec, so a commitment can be followed from the
chapter that states it into every edition that retells it.
`spec notes --check` gates the structure: a mark left in the prose, a
sidecar that no longer mirrors its chapter, a binding that names a
commitment the spec does not declare, a reading order that disagrees
with the files on disk. `spec quotes` checks that a phrase quoted in
metadata still exists in the work, and `--grep TERM` lists every
quotation of a term you just retired from the prose.

### Keeps track of what the work stands on

Research gets a register too. Each source has a slug, in one file of
sections or one file per source, and a mark cites it as `@source(slug)`.
`spec sources --slug S` then lists every chapter or post that leans on a
source, and the gate refuses a citation of a source the register does
not hold. A source can keep a plain-text capture of itself, and a
quotation from it is checked against that capture, so a misremembered
line is caught the way a misquoted chapter is. A references register,
which says where the work stands on each source, cites the spec by slug,
and the gate holds each of those citations to a section that exists.

### Remembers how the work was written

`provenance` captures your Claude Code, Codex and Antigravity sessions
from their local stores, and web conversations too. The captures go to a
store outside your repository and wait there until you read and accept
them. The repository keeps only the decisions: which sessions count,
each with a hash that proves its bytes. So the transcripts never bloat
the work's history, and a grep across the repository never wades through
them. `provenance store backup` copies the store to the destinations it
names, through rclone, a mounted directory, git or a script of your own.
It never deletes a session at a destination before the copy that
replaced it has arrived. `provenance search` then reads the human's own
turns, by keyword or by meaning; the semantic index is built and stored
locally, so corpus text never leaves the machine.

`spec trail` connects that record to the spec without putting any of it
in the spec. A trail sidecar, kept beside the private corpus, points
each spec section at the conversations where the author seeded,
sharpened or reversed it: a session and the time a turn was logged, not
a copy of the words. A companion about to change a commitment reads the
author's own turns, oldest first, and opens as much of each conversation
as it needs. For a spec that grew before it had a trail,
`spec trail backfill` has cheap readers flag every session once and
leaves the decision for each section to a strong model, or, as leads
shown beside the trail, to whoever reads that section later. Each read
records the turns it saw, so a session that later gains turns is handed
out again for those turns alone.

On top of that corpus it measures things about the making. `metrics`
estimates the division of labor between the human and the AI, with its
caveats attached rather than laundered away. `input` compiles the
human's verbatim words. `coldread` puts the record in front of blind
analysts and caches their reports by content, so changing the recipe
misses the cache deliberately rather than reusing an answer to a
different question.

### Turns procedures into something your companion can follow

Procedures are files the project writes, and the catalog makes them
navigable: `sop explain NAME` prints required reading in dependency
order with its word count, while follow-up work is recorded and
deliberately never traversed for you. Personas say who is reading,
instruments say what they are asked, and both are versioned data
composed at preparation and frozen with the run. `sop skills` generates
thin entry points so Claude Code or Codex can be pointed at a procedure
by name. A procedure that declares modes has them named in its entry
point, and `sop show NAME --mode MODE` prints only the part a request
needs. An entry point stops once it has presented its plan, before any
run starts, unless the request opens with `go`.

### Runs models under conditions you declared

A run is a plan: seats, ordered turns, an explicit model and effort.
Prepare freezes the prompts and the inputs and hashes them, start
executes through Codex, Claude Code or Antigravity, and the receipt
records the model actually served rather than the one requested. Each
claim in it names its source: the effort is read from the harness's own
session record where the harness keeps one, and the billing names the
kind of credential the seat ran on, never the credential itself. A
Claude seat can run through a gateway the project declares, and then
never borrows the host's own login.

The controls are the point. A seat can be confined by the operating
system rather than asked nicely, and where a provider can only offer an
audited boundary the run says so instead of claiming isolation. Delivery
is a separate choice from exposure: plain text, prose with its figures,
rendered PDF pages, native file attachments. A pending request is never
blindly resubmitted, a run stopped by a quota limit is continued in its
own session rather than restarted, and a compaction event is always
reported even when the answer looks fine. `sop runs watch` follows a run
as it goes, and `export` hands back the answers and a sanitized receipt.

The five patterns build on that engine, and what tells them apart is who
sees what. A writers room (`sop room`) develops one answer through
disagreement on a shared board. A roundtable (`sop roundtable`) has
models work together as themselves, each seeing every turn as it
happens. A prose workshop (`sop workshop`) gives every writer the same
frozen packet and never another writer's draft, and keeps the drafts
anonymous under a sealed key. A blind-reader panel (`sop panel`) shows
each reader only the reader-facing material. A consistency review
(`sop review`) gives an independent reviewer the project guide, the
spec, the marks, a brief and a Git range, with the repository read-only.

### Publishes a book in four formats, or a site through its generator

Publishing is a backend the project names. A site built by Hugo keeps
its own configuration, theme and deploy, and `publish build` builds it
into the project's build tree, where it cannot overwrite the directory
the site deploys from. A project that publishes more than one thing,
such as a book and the site that serves its editions, names each as a
target, and a target can take another's output into its own. A book gets
the rest of this section.

`publish build` renders a Quarto manuscript to every format its
`_quarto.yml` lists: HTML, PDF through Typst, EPUB and DOCX for a book
that lists all four, as the authoring guide's example does. It stages
three trees to do it, because the formats disagree about what they will
carry, and it checks afterwards that a figure has the same number in
every edition rather than assuming so. The source tree is never written
into.

Every derived edition renders the same way: a memo for a manager, a
version for young readers, the book as poems, each composed beside the
book with a `_quarto.yml` of its own.

It also does the things that go wrong silently. It generates a grayscale
figure set from the color one, because e-ink desaturates color into mud.
It validates the EPUB with the W3C reference checker, pinned by version
and checksum, because a file every desktop reader opens can still be
rejected by a retailer. It refuses to render in a substituted typeface,
because Typst and rsvg substitute without complaining and the whole book
comes out in the wrong face.

### Hands copies to readers and brings their marks back

A reader's note is only useful if it finds the text it was written
against. Until a book is published, it can render every copy as a proof
named for its commit, with the commit stamped on its pages, and the
build stops if one page lacks the stamp. `publish copy --for NAME`
renders a proof for one person from a committed tree. The copy takes the
next number in a log kept outside the project, and its file is named by
that number, never by the person.

The marks come back the same way they went out. `publish redpen` builds
a reviewable DOCX and reads the reviewer's marks back onto the source.
`publish kindle` delivers a copy to a Kindle library from a signed-in
browser tab, then finds each highlight and note from the reader's
exported notebook in the manuscript. Marks on a copy made for the author
become the author's own turns in the record, and another reader's are
kept apart as that reader's feedback. `provenance rulings archive` keeps
the rulings the author makes on a review sheet as turns of theirs too,
so a spec trail can point at one.

### Formats and inspects the prose

`text wrap` applies the project's own conventions, and it can apply
different ones per path, declared as a table of globs: one sentence per
line in chapters or posts, hard wrapped at a column in guides.
`text check` reports field jargon in reader-facing prose against a term
list the project writes, so a word must be avoided, glossed on first
use, or confined to where it belongs. A phrase the project has retired
goes on the same list and fails the check if it ever comes back.
`text tells` reports the habits a model leaves in prose it drafted: the
contrast move, which says what a thing is by saying what it is not, and
phrases the project lists. `--since REV` prints only what the working
tree adds over a revision, which is the warning a commit hook or a
review pass wants; like `text check`, it never gates. Quirework ships no
default phrase list, since a stale one would read as clean prose;
`config template tells` prints a dated seed and the recipe for keeping
it current.

## Install

Python 3.11 or later is required. A checkout of this repository brings
the two examples along with the tool:

```bash
git clone https://github.com/adereis/quirework.git
cd quirework
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -e '.[quarto]'
```

Put `.venv/bin` on your `PATH`, or copy `.envrc.example` to `.envrc` and
let direnv do it. To install the released package into an environment of
your own without a checkout, run `pip install 'quirework[quarto]'`.

The extras follow the work. `quarto` installs Quarto, which brings Typst
for the PDF, and what a book's render needs besides. A site needs none.
`semantic` adds local semantic search of the corpus, `capture` captures
web conversations from a browser, and `test` runs the test suite. `dev`
includes all of them.

The core and command discovery use the Python standard library. Other
programs are needed only by the operations that use them:

- Model seats run through the Codex, Claude Code or Antigravity CLI,
  each installed separately.
- Enforced filesystem isolation uses bubblewrap on Linux, and an
  Antigravity seat also needs `xdg-dbus-proxy`.
- A book's PDF and SVG work uses Poppler and librsvg, and the typeface
  check uses Fontconfig.
- EPUB validation needs Java and the W3C checker, which
  `quirework publish epubcheck --install` fetches pinned by version and
  checksum. A render without them says the EPUB was not validated.
- A site renders with Hugo, and a store backup uses rclone when one of
  its targets does.

A missing required program stops the operation with an error.

## Let the companion choose the tools

You normally work by describing the job to your AI companion. The
command groups below are the vocabulary it uses to carry out that job:

```text
quirework spec        Read and check the spec, marks, sources, quotations and trail
quirework publish     Render the manuscript and inspect its editions
quirework text        Format and inspect writing
quirework provenance  Capture, organize, and search source history
quirework sop         Find procedures and operate supervised workflows
quirework dev         Repository checks and tooling maintenance
quirework config      Inspect and validate project configuration
quirework usage       Report which commands, procedures and seats get used
```

You can also run the commands directly. Add `--help` at any level to
inspect the actual options. Help does not require a project, a private
corpus, or a provider account.

Common operations include:

```bash
quirework spec notes --primer -c opening
quirework spec notes bind --slug kettle-signal
quirework spec trail kettle-signal
quirework spec notes --check
quirework publish build --to html
quirework publish copy --for Ada
quirework publish redpen build --chapter opening
quirework text wrap manuscript/opening.qmd
quirework text tells --since main
quirework provenance search 'an earlier idea'
quirework sop list
quirework sop explain review
quirework sop runs prepare plan.json /path/to/private/run
quirework sop runs start /path/to/private/run --timeout 60 --wait-quota 0
quirework sop runs watch /path/to/private/run
```

Starting from nothing? The
[authoring guide](https://github.com/adereis/quirework/blob/main/docs/authoring.md)
is the path from an empty directory to a manuscript with a spec, marked
chapters, figures and its own procedures, and its last part does the
same for a site.
[examples/side-quest](https://github.com/adereis/quirework/tree/main/examples/side-quest)
is a short complete book that walked it: a comic memoir with a spec and
its trail, a book in four formats, three derived editions bound to the
same spec, an extension of its own, the fifteen sessions it was written
in, and the records of the workshop, writers room, blind read,
roundtable and consistency review that made parts of it. Its editions
can be read at
[quirework.com/examples](https://quirework.com/examples/).
[examples/demo-site](https://github.com/adereis/quirework/tree/main/examples/demo-site)
is the site version: posts with a spec each, a shared spec, marks kept
outside the rendered tree, and a voice policy the checks enforce.

Both examples are test fixtures, not showcases. Their authors are
invented, AI wrote them, and no person edited or reviewed their prose.
Every session of the book was driven by an AI playing its author, which
the example's own README says. Read them for the structure Quirework
keeps, not as examples of the writing it helps an author make.

The
[configuration guide](https://github.com/adereis/quirework/blob/main/docs/configuration.md)
explains how to connect a project. The
[workflow guide](https://github.com/adereis/quirework/blob/main/docs/workflows.md)
covers procedures, research, model runs, and saved evidence. The
[development guide](https://github.com/adereis/quirework/blob/main/docs/development.md)
covers architecture and tests.

## A project owns its choices

Place `quirework.toml` at the project root. Quirework discovers it from
the working directory, including subdirectories. Use
`quirework --project PATH ...` to select a different project explicitly.
Installing Quirework never selects a project by the package's location.
`quirework config template book` or `site` prints an annotated file to
start from. Private corpus paths and account identities go in an ignored
`quirework.local.toml` beside it. What belongs to you across every
project, such as where the session store lives and which devices receive
your copies, goes in `~/.config/quirework/config.toml`.

A book's Quarto file remains the source of its publication settings and
reading order; a site's own generator configuration does the same job
for the site. Personas, questions, custom procedures, editorial
policies, and private corpora belong to the project. A project without
provenance can use the publishing and run tools independently.

Quirework is one distribution with one Python namespace. Its domain
modules are designed for reuse without the CLI or a particular project.
The software is licensed under the MIT license.
