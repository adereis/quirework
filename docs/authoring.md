# Writing a book with Quirework

This is the path from an empty directory to a book an AI companion can
work on without keeping the whole project in one conversation. The
finished project separates its spec, canon prose, variants, companion
documents, scratchpad, and private material. Every command below was run against a throwaway project.
Nothing here is specific to a particular book.

You can follow this guide at a terminal. The expected use is to open the
book project in Claude Code or Codex and let that companion operate the
commands as its procedures require. The
[configuration guide](configuration.md) is the reference for each
setting. The [workflow guide](workflows.md) covers the companion loop
and controlled model runs.

Quirework holds no opinion about what your book argues. It holds the
shape: chapters in reading order, a register of what the book commits
to, marks that tie the two together, figures, and procedures an AI
companion follows.

## A normal writing session

Tell the companion what outcome you want. A request can be as simple as
"revise the opening under the write-a-chapter procedure." The companion
should then read the project guide and use
`quirework sop explain write-a-chapter` to resolve the procedure's
required reading.

The next command is `quirework book notes --primer`. It puts the spec
before the chapter guidance and bindings. That order matters. The
companion learns what the book holds before it changes how one chapter
expresses it.

After the edit, the companion updates any relationship that genuinely
changed. It then runs the procedure's checks and reports what still
needs human judgment. A blind read or workshop may inform that report.
Neither one changes the spec by itself.

## The layers

A project can adopt these one at a time, in this order. Each is useful
without the ones below it.

| Layer | What it is | The commands |
|---|---|---|
| Canon prose | Quarto chapters in a declared reading order | `book build`, `book chapters` |
| Spec | one file of numbered commitments, each with a slug | `book notes --primer` |
| Marks | per-chapter sidecars of `@`-annotations, including which spec section a chapter expresses | `book notes`, `book notes bind` |
| Companion documents | reader-facing material that cites the spec or canon | `book notes --check` |
| Variants | derived editions whose master remains the canon | `book variant` |
| Figures | the book's illustration set, materialized into the build | `book figures` |
| Scratchpad | disposable working material with no authority | project policy |
| Private material | ignored sources and records that must not publish | project policy |
| Procedures | the playbooks an AI companion follows, with personas and questions | `sop list`, `sop explain`, `sop skills` |
| Provenance | the sessions the book was written in | `provenance search`, `provenance metrics` |

## 1. The manuscript

Make a directory and put a `quirework.toml` at its root. That file is
what makes the directory a project; Quirework finds it from any
subdirectory, and the installed package's own location never selects a
book.

```toml
schema = 1

[project]
name = "A Small Notebook"
scratchpad = "scratchpad"
private = "private"

[book]
source = "manuscript"
quarto = "manuscript/_quarto.yml"
chapters = "manuscript/sections"
build = "build"
variants = "variants"
companion_dirs = ["companion"]
noncanonical_roots = ["scratchpad"]
```

Quarto owns reading order and publication settings. `book.quarto` names the
file that lists the chapters:

```yaml
lang: en-US
project:
  type: book
book:
  title: "A Small Notebook"
  author: "A. Fictional Author"
  chapters:
    - index.qmd
    - sections/opening.qmd
    - sections/closing.qmd
format:
  html:
    theme: cosmo
```

Declare `lang`, at the top level and not inside `book`. Without it the
build inherits the machine's locale, which produces an EPUB whose stated
language is whatever the machine happened to be set to, and no error.
The key is easy to get wrong in a way nothing reports: a `language:`
under `book:` is accepted by the parser, ignored by the renderer, and
leaves the locale in the EPUB's `dc:language` exactly as if the line
were absent.

Each chapter is a `.qmd` opening on a level-1 heading with an anchor:

```markdown
# The opening {#sec-opening}

A kettle boils.
The whistle is a signal, not a thought.
```

Then `quirework book build --to html` renders into `build/`, never back
into the source. `quirework book chapters` prints the reading order, and
`--check` verifies that the files on disk and the files `_quarto.yml`
names are the same set.

A chapter's file name is its identifier. Retitling a chapter does not
rename it, and inserting one does not renumber anything, because numbers
are derived at render time and nothing else refers to them.

## 2. The spec

The spec is one Markdown file stating what the book commits to. Each
commitment is a level-2 section carrying a slug:

```markdown
# What this notebook holds

## A kettle signals without thinking {#kettle-signal}

A whistle reports a state. Reporting a state is not having a thought
about it.

## Cooling is not the reverse of boiling {#cooling-asymmetry}

A kettle reaches the boil by one route and cools by another.
```

Point at it and it becomes the binding namespace:

```toml
[book]
spec = "manuscript/sections/_spec.md"
```

The file name is the project's own. Commands that use bindings require this
setting instead of guessing a name or location.

The slugs are the whole mechanism. A chapter does not restate a
commitment; it names the section it expresses, so changing a commitment
tells you which chapters have to change with it. The spec is not
rendered into the book.

`quirework book notes --primer` prints the spec's sections first, then
the marks in reading order. That is the one command to run at the start
of a writing session.

The spec states; it does not argue, and it does not record how it got
here. `notes --check` reports three register slips as advisories rather
than errors, so you find out by running it: an imperative in capitals
(`NEVER`, `MUST NOT`), a run of three or more capitalized words, and
decision journaling, which means day-precision dates, review-round
citations, commit hashes, and any phrasing about what a section used to
say. The reason is not tidiness. A dated ruling in a spec reads as
authority to the next session, which then works around the section
instead of proposing a change to it.

## 3. Marks

Marks are HTML comments, and they live in a sidecar beside the prose
rather than in it, so the rendered book never carries them and a
reflowed paragraph never disturbs them.

A chapter `sections/opening.qmd` has its sidecar at
`sections/_marks/opening.md`. The sidecar's headings mirror the
chapter's, which is how each mark is attributed to a section. Copy each
heading line from the chapter verbatim, attribute block included:
attribution matches on the `{#anchor}` when a heading has one and on the
heading text only when it does not, so a sidecar that says `# Preface`
against a chapter that says `# Preface {#sec-preface}` is a drifted
mirror and `notes --check` says so.

```markdown
<!--
  Metadata sidecar for opening.qmd. Not rendered. Its headings mirror
  the chapter's, so every mark lands in the right section.
-->

# The opening {#sec-opening}

<!-- @guidance  open on the object, not the claim -->
<!-- @bind(kettle-signal) -->
<!-- @todo  say what a signal costs -->
```

Any tag name is accepted, so a project can grow its own vocabulary. The
conventional ones:

- `@bind(slug)`: this chapter expresses that spec section. It carries no
  body: it is a relationship, not a note. Several chapters may bind one
  section, and `book notes bind --slug S` is the reverse lookup that
  answers "what else does this commitment touch?".
- `@guidance`: how the chapter should read, its length, what it is for.
- `@genesis`: where the material came from.
- `@todo`: open work.
- `@figure(name)`: a note about a plate.
- `@see(slug)`: a pointer into book-wide review notes.

`quirework book notes --check` is the gate. It refuses a mark left in
the prose, a sidecar with no chapter, a sidecar heading that no longer
matches the chapter, a `@bind` naming a section the spec does not
declare, and a spec section missing its anchor. Run it before every
commit; it is what keeps the metadata from drifting away from the text
it describes.

## 4. Figures

Put the illustration sources in one directory and point at it:

```toml
[book]
figures = "manuscript/sections/illustrations"
```

A figure counts as part of the book when a chapter references it:

```markdown
![A kettle, and the steam that reports it.](illustrations/kettle.svg){#fig-kettle fig-alt="A rounded kettle in outline with a curl of steam leaving the spout."}
```

`quirework book figures list` shows what is embedded, `build`
materializes the set into the build tree, and `--check` validates it.
The sources stay tracked; the rasters and the derived galleries are
build output.

Two hooks make the figure pipeline a project's own without touching
Quirework. `book.grayscale_transform` names a function taking SVG text
and an asset name and returning SVG text, for a book that wants its own
color-to-gray meaning in the EPUB rather than a generic desaturation.
`book.figure_checks` is a list of functions returning diagnostic
strings, for conventions only this book has. Both are installed Python
in the project's own package, selected explicitly.

`quirework book fonts` reports the families the build needs, read out of
the Typst template and the figure sources rather than declared anywhere.
A missing family is a hard stop, because Typst and rsvg both substitute
silently and the book would render whole in the wrong face. `--any-font`
overrides it.

## 5. Procedures

A procedure is a playbook an AI companion reads and follows. Quirework
supplies tools for its mechanical steps, but it does not execute the
procedure as a whole. Procedures live in a directory of the project's
choosing:

```toml
[sops]
root = "procedures"
catalog = "procedures/catalog.json"
skill_prefix = "nb-"
```

Files directly under that root are procedures, and so are the ones in
`workflows/` and `protocols/`: all three are discovered, and every
command that resolves a procedure by name looks in all three. The
directories are a filing convention for a reader. What makes a file a
shared fragment rather than a procedure is its opening `# SOP fragment`
heading, which is checked against the `kind` its catalog entry declares,
so a fragment filed anywhere is still a fragment and a procedure filed
under `protocols/` is still a procedure. A procedure declares its own
one-line purpose in a marker:

```markdown
# Tidy the notebook

<!-- @skill: Tidy a small notebook before filing it. -->

Read the two sections, fix the wrapping, and stop.
```

The catalog is the discovery graph. Every procedure file must appear in
it and every entry must have a file, so neither can quietly rot:

```json
{
  "version": 1,
  "families": [
    {"id": "tidy", "label": "Tidy", "question": "What needs straightening?"}
  ],
  "entries": {
    "tidy": {"family": "tidy", "kind": "procedure", "skill": true,
             "requires": ["care"], "mechanics": ["quirework text wrap"],
             "followups": [], "note": ""},
    "care": {"family": "tidy", "kind": "protocol", "skill": false,
             "requires": [], "mechanics": [], "followups": [], "note": ""}
  }
}
```

`skill` decides whether the procedure gets its own generated route, and
it is required rather than inferred. Adding a file is not a decision, so
a procedure with no `skill` line fails validation until someone records
one; that is what stops a skill list growing to a size nobody chose.
`false` is a normal answer: the procedure stays discoverable through
`sop list` and `sop NAME` without occupying a slot in every companion
session's skill list. A `kind: protocol` fragment can never take a
route, and asking for one is an error.

`requires` is reading a procedure depends on and is traversed into a
deduplicated reading order; `followups` is work that a result might
justify and is deliberately never traversed. `quirework sop list` groups
by family, `sop explain NAME` prints the reading order with its word
count, and `sop skills --target both` generates thin Claude and portable
routes so a companion can be pointed at a procedure by name. The
generated routes name the project's own guide, which
`sops.operator_guide` sets.

Personas are perspectives and instruments are questions, kept separate
so either can vary alone. A persona is one JSON file per profile under
`sops.personas`:

```json
{
  "schema_version": 1,
  "id": "reader",
  "revision": 1,
  "label": "An ordinary reader",
  "description": "A fictional test perspective",
  "tags": ["reader"],
  "prompt": "You are reading for pleasure. Say what is clear."
}
```

The [persona name catalogue](persona-name-catalog.md) offers fictional,
task-linked labels for profiles. The profile prompt remains the source
of the seat's actual behavior.

An instrument under `sops.instruments` is one JSON file of questions:

```json
{
  "id": "first-reader",
  "revision": "1",
  "default_persona": "reader",
  "default_scope": "book",
  "prompts": {
    "first": {"text": "{{persona}} Read the opening. What do you expect next?"},
    "next": {"text": "Keep reading. Where did you stop believing it?"},
    "wrap": {"text": "Say in three sentences what the book was for."}
  },
  "sequence": {"first": "first", "next": "next", "wrap": "wrap"}
}
```

Every ask is an object with `text`, not a bare string, so an ask can
also carry `scopes` and `headings` later without changing shape.
`sequence` names which ask opens, which repeats, and which closes; it is
what makes a set of questions an order rather than a collection.
`{{persona}}` is where the selected profile's prompt lands. `revision`
is a string and is bumped whenever the questions change, because a
prepared run freezes the rendered prompt and an old record has to keep
the questions it actually asked.

## 6. Checks of your own

`checks.command` names a function in the project's package, and
`quirework dev check` runs it. That is where a book's own rules live:
prose guards, vocabulary rules, anything Quirework has no business
knowing. `text.classifier` similarly selects a function deciding each
source path's formatting mode, so a project can wrap its guides at 72
columns and keep its chapters one sentence per line.

## Where to go next

- [examples/demo-book](../examples/demo-book): a finished book that
  followed this guide, with every layer above adopted and nothing else.
  Read its `quirework.toml` beside this page.
- [configuration.md](configuration.md): every setting, the layering
  rules, and the annotated templates
  (`quirework config template project`).
- [workflows.md](workflows.md): procedures, personas, panels, model
  runs, recovery and evidence.
- [development.md](development.md): the architecture and the tests, for
  changing Quirework itself.
