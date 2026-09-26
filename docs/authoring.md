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
| Spec trail | pointers from each spec section to the turns that shaped it | `book trail` |

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

### The spec's trail

How a section got here still matters to whoever changes it next. The
words you used in June and the sharper ones from last week tell the
story of an idea, and an agent about to edit it should read them. The
trail keeps that story outside the spec, in a sidecar of its own:

```toml
[book]
spec = "manuscript/sections/_spec.md"
spec_trail = "private/spec-trail.md"
```

The sidecar mirrors the spec's headings. Under each one it lists
breadcrumbs. A breadcrumb says which conversation, and when in it, you
shaped that section:

```markdown
## Cooling is not the reverse of boiling {#cooling-asymmetry}

- 2031-04-02T10:09:12Z · correct · claude-code 0badc0de-…-000000000001

  Refuses speed as the difference and keeps the route.
```

A breadcrumb is a pointer, not a copy. The session stays the only source
of truth, so your words are read from it whenever the trail is read. The
pointer is the time the harness logged your turn, in UTC, because anyone
can find that moment: in the captured corpus, in the harness's own
transcript, or in the web conversation, with or without Quirework. A
source that logs no time names the turn by its number instead: a
pre-rendered Gemini review, and a red-pen round where
`provenance.redpen` declares the archive, since each mark on your
rendered copy is one of your turns
(`--session redpen-v1 --turn 3`). The
kind says what the turn did to the section: `seed`, `sharpen`, `correct`
(refused a wording the agent proposed), `reverse` (moved off an earlier
position), `accept` (took a wording the agent drafted) or `probe` (tried
something without settling it).

The trail quotes the private corpus by reference, so keep it with the
private material. It is context, never authority: the spec states what
the book holds now, and a request made now outranks anything on the
trail. Leave `spec_trail` unset and the feature is off.

Reading is one command per depth. `book notes --primer` counts each
section's breadcrumbs, and every listing of bindings marks a binding
whose section has any, as `[trail ×2]`, so an agent following one from
the prose learns there is a history to read. A section with none
carries no marker. `book trail --chapter opening` lists, one line
each, the trails of the sections a chapter binds.
`book trail cooling-asymmetry` reads one section's trail oldest first,
and prints the `provenance show … --turn N` that opens each turn with
the reply it drew.

Writing happens while the spec is edited. When your words in the current
conversation change what a section holds, the agent adds a breadcrumb:

```bash
quirework book trail add cooling-asymmetry --kind correct \
  --find "a route, not a speed" --note "Refuses speed as the difference."
```

`--find` takes words you typed. It looks for them in the corpus and then
in this project's live Claude Code and Codex transcripts, so a turn from
the conversation still going on can be pointed at before it is captured.
`--session ID --turn N` takes the number `provenance search` printed. An
`--excerpt` is optional: it highlights words in a long turn where they
would otherwise be buried, and it must be words the turn holds.

The judgment is the agent's, and it should err toward including. Add a
breadcrumb for every turn that seeds, sharpens, corrects, reverses,
accepts or probes what a section holds, since a reader can skip one but
cannot recover a turn nobody pointed at. Point at your turn, not the
reply. Never delete a breadcrumb because you moved on: add a `reverse`,
because the earlier position is part of the story. Delete one only when
it was wrong about which section the turn shaped.

`book notes --check` fails a trail heading that names a section the spec
no longer has, so a renamed section takes its trail along in the same
edit. `book trail --check` follows every breadcrumb to your turn. It
fails a pointer that leads to no turn, and one into a session that
curation removed. It only warns about a session not captured yet, which
is what pointing at a live conversation costs until that session is
captured.

### Backfilling a trail

A spec that grew before it had a trail needs its history read, and a
corpus of hundreds of sessions is too much for a strong model to read
whole. `book trail backfill` splits the work by what it costs. A cheap
model reads each session once and flags every turn of yours that might
have shaped any section. A strong model then takes one section at a
time, reads everything flagged for it oldest first, and decides.
Reading a session costs the same whether it is read for one section or
for all of them, which is why each session is read once, against the
whole list.

```toml
[book]
spec_trail = "private/spec-trail.md"
spec_trail_leads = "private/spec-trail-leads"
```

`backfill plan` writes each session as a packet: your turns, numbered
and stamped as `provenance show` gives them, each with the end of the
reply it answered, so a bare "yes" can be read as the acceptance it
was. It then writes a run plan that hands the packets to readers with
no tools, one seat per batch, so no reader's context grows from one
batch to the next. A batch is bounded by estimated tokens (`--budget`)
and by your turns (`--turns`, 80 by default), because a reader's answer,
thinking included, grows with the turns it answers for and stops at its
model's output limit. Short turns, such as red-pen marks, fill a token
budget with more turns than a reader can answer. A session longer than
`--turns` is read in parts of nearly equal length, one part to a seat.
A part's packet says which turns it holds, and its turns keep their
numbers. The plan names every session it hands out in parts. The run
engine runs the plan, and resumes it after a quota
stop. Without `--out`, the plan takes a new directory in the project's
private state, named for the day and the model, with a suffix for a
second pass the same day. It prints the sessions by harness, the whole
cost including each seat's menu, any session with neither a start
time nor a recorded date (sorted first), and the prepare, start and
import commands with their real paths. A Gemini review and a red-pen
round log no times but record their date, and take their place by it.
A red-pen round's packet opens by saying what its parts are, since only
some of each mark is your words. A review-notes entry compares your copy
of a block with the block rebuilt from source when the round was
archived. A copy built by an older renderer held more than that rebuild,
such as headings and notes agents had written, so the packet warns that
those can show up among your "+ now" lines. A round records the book
revision it was matched against, and the packet leaves out any "+ now"
line the book's source already held at that revision, since such a line
was not written in the round. It says how many it left out, or that git
could not read the revision. A comment's file and line are where the
archive matched its anchor, and a one-word anchor can match the wrong
place, which the packet also says.

`backfill import RECORD` checks each flag against the corpus and
keeps it in the ledger: a turn that is not yours, or a slug the spec
does not have, is dropped and reported with its reason. A session whose
reader did not say it finished is left for the next plan to hand out
again.

Import says when a reader was cut off: an answer that stops partway
through a line, with sessions left unfinished, is reported with the
seat's output and thinking tokens. Those sessions are handed out again
by the next plan; give them fewer turns per seat. The turn cap lowers
this risk and cannot remove it, because thinking depends on what the
turns hold: one dense session can use up a reader's room alone. Plan it
again with a lower `--turns`, and it is read in parts.

A read of a part records the turns it covers. A session counts as read
once its reads cover every turn, and the next plan hands out only the
turns no reader finished. `backfill status` counts a session read only
in part.

Import holds any seat whose receipt carries a red flag its plan did not
accept, records nothing it read, and exits nonzero, so an unexpected
flag reaches you rather than the ledger. An Antigravity plan accepts
the keyring flag every Antigravity seat carries, because its readers
have no tools; a record planned before that was possible takes
`--accept session-bus`. Import then prints each seat's share of flagged
turns and warns about a seat with many turns and almost no flags. A
flag count cannot tell a reader that skipped from one whose sessions
held nothing, so a reader closes each session with an `about` sentence
on what it was, written from turns it had to read. A warned seat lists
each session's title, which the reader never saw, beside that sentence,
and a mismatch or a missing one is the mark of a skipped packet. It ends
with the pass as a whole: sessions read, models, flags by confidence,
and sessions with no flag. A session with no flag is not suspect by
itself; a turn can matter only beside another session, such as the
unrelated thread in which a question is asked as a control.

`backfill SLUG` lists a section's candidates oldest first: what each
reader flagged, and the turns before the commits that changed the
section. A reader names the section it thinks fits, so a turn that fits
two can reach only one of them; `--also OTHER` adds what readers
flagged for a neighbouring section, marked with its slug. The list notes
two kinds of copy before you rule on either: a turn you sent twice,
saying which copy drew the reply, and a turn captured twice, when a
resumed session repeats the history it inherited under a new id.
`--read` prints each candidate whole, after the end of the reply it
answered, cut as a packet cuts it. A bare "yes, go" means only what
that reply proposed, so ruling on one needs the reply, and a section
with sixty candidates would otherwise take sixty `show` commands.

A commit leads to the session that made it only where that session's
model wrote the commit's subject, in the command, the message file or
the reply reporting it; a session that saw the subject in a `git log`
listing read about the commit and did not make it. A commit's quote is
matched whatever quotation marks either side used. A quote of fewer
than four words is looked for only in the session that made its commit,
since "agreed" is in half the corpus. `trail suggest SLUG --diff` adds
what each commit changed in the section, which tells you which
paragraph a session's turns went into.

A candidate the strong model keeps becomes a breadcrumb through
`trail add`; one it drops is recorded with
`backfill reject SLUG --session ID --turn N --reason "…"`, so no later
pass pays to read it again. A section's whole ruling can go through
`backfill rule SLUG FILE` instead, one JSON line per candidate:

```
{"keep": "correct", "session": "0badc0de", "turn": 3, "note": "Refuses speed."}
{"reject": "Placement, not the claim.", "session": "0badc0de", "turn": 1}
```

Every line is checked before anything is written, so a mistake leaves
the section as it was rather than half ruled. Only this step drops a
candidate. A flag is a lead, never a breadcrumb, and the ledger is as
private as the trail: its reasons paraphrase your turns.
`backfill status` says how many sessions are read and which sections
have candidates left; its counts are the readers' flags, and a
section's list adds the turns its commits lead to.

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
