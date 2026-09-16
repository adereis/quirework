# Writing a book with Quirework

This is the path from an empty directory to a book with a spec, marked
chapters, figures and its own procedures. Every command below was run
against a throwaway project; nothing here is specific to a particular
book. The [configuration guide](configuration.md) is the reference for
each setting named, and the [workflow guide](workflows.md) covers model
runs.

Quirework holds no opinion about what your book argues. It holds the
shape: chapters in reading order, a register of what the book commits
to, marks that tie the two together, figures, and procedures an agent
follows.

## The layers

A project can adopt these one at a time, in this order. Each is useful
without the ones below it.

| Layer | What it is | The commands |
|---|---|---|
| Manuscript | Quarto chapters in a declared reading order | `book build`, `book chapters` |
| Spec | one file of numbered commitments, each with a slug | `book notes --primer` |
| Marks | per-chapter sidecars of `@`-annotations, including which spec section a chapter expresses | `book notes`, `book notes bind` |
| Figures | the book's illustration set, materialized into the build | `book figures` |
| Procedures | the playbooks an agent follows, with personas and questions | `sop list`, `sop explain`, `sop skills` |
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

[book]
source = "manuscript"
chapters = "manuscript/sections"
build = "build"
```

Quarto owns reading order and publication settings, so the book's
`_quarto.yml` lives inside `book.source` and lists the chapters:

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

The file name is the project's own. Leaving `spec` unset makes Quirework
look for `_framework.md` beside the chapters, which is one book's habit
rather than a rule, so a project that calls the file something else
simply says so here.

The slugs are the whole mechanism. A chapter does not restate a
commitment; it names the section it expresses, so changing a commitment
tells you which chapters have to change with it. The spec is not
rendered into the book.

`quirework book notes --primer` prints the spec's sections first, then
the marks in reading order. That is the one command to run at the start
of a writing session.

## 3. Marks

Marks are HTML comments, and they live in a sidecar beside the prose
rather than in it, so the rendered book never carries them and a
reflowed paragraph never disturbs them.

A chapter `sections/opening.qmd` has its sidecar at
`sections/_marks/opening.md`. The sidecar's headings mirror the
chapter's, which is how each mark is attributed to a section:

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

A procedure is a playbook an agent reads and follows; Quirework does not
execute one. They live in a directory of the project's choosing:

```toml
[sops]
root = "procedures"
catalog = "procedures/catalog.json"
skill_prefix = "nb-"
```

Files directly under that root are procedures. `workflows/` holds the
task-level entry points and `protocols/` holds shared fragments, which
open with a `# SOP fragment` heading and get no invocation route of
their own. A procedure declares its own one-line purpose in a marker:

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
    "tidy": {"family": "tidy", "kind": "procedure", "requires": ["care"],
             "mechanics": ["quirework text wrap"], "followups": [], "note": ""},
    "care": {"family": "tidy", "kind": "protocol", "requires": [],
             "mechanics": [], "followups": [], "note": ""}
  }
}
```

`requires` is reading a procedure depends on and is traversed into a
deduplicated reading order; `followups` is work that a result might
justify and is deliberately never traversed. `quirework sop list` groups
by family, `sop explain NAME` prints the reading order with its word
count, and `sop skills --target both` generates thin Claude and portable
routes so an agent can be pointed at a procedure by name. The generated
routes name the project's own guide, which `sops.operator_guide` sets.

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

An instrument under `sops.instruments` carries an `id`, a `revision`
string, a `prompts` object keyed by ask (`first`, `next`, `wrap`, and
any others a procedure uses), and optionally a `default_persona` and
`default_scope`. Bump the revision when the questions change: a prepared
run freezes the rendered prompt, so an old record keeps the questions it
actually asked.

## 6. Checks of your own

`checks.command` names a function in the project's package, and
`quirework dev check` runs it. That is where a book's own rules live:
prose guards, vocabulary rules, anything Quirework has no business
knowing. `text.classifier` similarly selects a function deciding each
source path's formatting mode, so a project can wrap its guides at 72
columns and keep its chapters one sentence per line.

## Where to go next

- [configuration.md](configuration.md): every setting, the layering
  rules, and the annotated templates
  (`quirework config template project`).
- [workflows.md](workflows.md): procedures, personas, panels, model
  runs, recovery and evidence.
- [development.md](development.md): the architecture and the tests, for
  changing Quirework itself.
