# Writing a book or a site with Quirework

This is the path from an empty directory to a book an AI companion can
work on without keeping the whole project in one conversation. The
finished project separates its spec, canon prose, variants, companion
documents, scratchpad, and private material. The last part walks a site
of posts through the same layers. Every command below was run against a
throwaway project. Nothing here is specific to a particular book.

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

The next command is `quirework spec notes --primer`. It puts the spec
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
| Canon prose | Quarto chapters in a declared reading order, or a site's pages and where each stands | `publish build`, `spec units` |
| Spec | numbered commitments, each with a slug, in one file or one per unit | `spec notes --primer` |
| Marks | per-chapter sidecars of `@`-annotations, including which spec section a chapter expresses | `spec notes`, `spec notes bind` |
| Companion documents | reader-facing material that cites the spec or canon | `spec notes --check` |
| Sources | a register of what the work stands on, cited by slug from the marks | `spec sources` |
| Variants | derived editions whose master remains the canon | `publish variant` |
| Figures | the book's illustration set, materialized into the build | `publish figures` |
| Scratchpad | disposable working material with no authority | project policy |
| Private material | ignored sources and records that must not publish | project policy |
| Procedures | the playbooks an AI companion follows, with personas and questions | `sop list`, `sop explain`, `sop skills` |
| Provenance | the sessions the book was written in | `provenance search`, `provenance metrics` |
| Spec trail | pointers from each spec section to the turns that shaped it | `spec trail` |

## 1. The manuscript

Make a directory and put a `quirework.toml` at its root. That file is
what makes the directory a project; Quirework finds it from any
subdirectory, and the installed package's own location never selects a
book.

```toml
schema = 2

[project]
name = "A Small Notebook"
scratchpad = "scratchpad"
private = "private"

[manuscript]
source = "manuscript"
quarto = "manuscript/_quarto.yml"
chapters = "manuscript/sections"
companion_dirs = ["companion"]
noncanonical_roots = ["scratchpad"]

[publish]
build = "build"
variants = "variants"
```

Quarto owns reading order and publication settings. `manuscript.quarto`
names the file that lists the chapters:

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
  typst: default
  epub:
    toc: true
  docx: default
```

`publish build` renders the formats this file lists, and only those:
here all four, HTML, a PDF through Typst, an EPUB and a DOCX. A format
left out is never built, and nothing says so.

Declare `lang`, at the top level and not inside `book`. Without it the
build inherits the machine's locale, which produces an EPUB whose stated
language is whatever the machine happened to be set to, and no error.
The key is easy to get wrong in a way nothing reports: a `language:`
under `book:` is accepted by the parser, ignored by the renderer, and
leaves the locale in the EPUB's `dc:language` exactly as if the line
were absent.

The PDF's default Typst template draws its title page, headings and
contents in one color, and shades each Part's divider page with a tint
of it. That color is the brand's primary color, and blue when the book
has no brand, so a book printed in black meets blue on its first PDF. A
color set in `_quarto.yml` does not reach it. Set it in a `_brand.yml`
beside `_quarto.yml`, which the HTML theme reads for its links as well:

```yaml
color:
  primary: "#000000"
```

Each chapter is a `.qmd` opening on a level-1 heading with an anchor:

```markdown
# The opening {#sec-opening}

A kettle boils.
The whistle is a signal, not a thought.
```

Then `quirework publish build --to html` renders into `build/`, never
back into the source. `quirework spec units` prints the reading order,
and `--check` verifies that the files on disk and the files
`_quarto.yml` names are the same set.

A copy handed out before the book is finished can name the source it
came from, so a reviewer's notes find the text they were written
against. `revision_metadata = true` in the render policy has every build
record its commit, the commit's date and whether the sources had changes
not yet committed, as metadata a copyright page can print. The
configuration guide lists the keys.

A book can go further and render every copy as a proof until it is
published. Its render policy names the switch (`proof_switch`), and each
proof is named for its commit, carries a library title that says it is a
proof, and has its cover drawn with the commit when the book declares a
Typst cover source. The book's own filters and templates print the stamp
on its pages, and the build reads the files back and stops if one page
does not carry the commit, because a template that silently drops the
stamp renders a copy that passes for the published book.
`quirework publish copy --for NAME` makes a proof for one person: it
needs a committed tree, gives the copy the next number in the per-user
log of copies, and names the file by number and never by the person.

A reader who reads on a Kindle needs the copy in a Kindle library, and
the highlights and notes made there are marks on the book like any
other. `quirework publish kindle` keeps that loop. The store's pages do
the sending, from a browser tab already signed in:
`publish kindle driver` names a script for that tab, which uploads the
copy through Send to Kindle, lists the library and the reading devices,
and delivers a document to a device or takes it off one, using the calls
those pages make themselves. `publish kindle plan` says what to send,
under the title the EPUB carries, and which devices to deliver it to,
chosen by `kindle.targets` in the per-user configuration.
`publish kindle record` logs each step beside the copy's number. The
order matters because the store keeps a document's notes with the
document: a new copy arrives with none, and the notes on the copy it
replaces live only on the old one. So the reader exports the old copy's
notebook from the app into the folder `kindle.inbox` names,
`publish kindle inbox` fetches what is there and says which copy each
export is and whether its marks are already kept, and
`publish kindle notes EXPORT --copy N` finds each highlight in the
manuscript and archives the marks. Marks on a copy made for you, as
`copies.author` names you, go where `provenance.redpen` keeps red-pen
rounds, where they read as your own turns; marks on a copy made for
anyone else are that reader's feedback, kept apart where
`publish.reader_notes` says, so nothing reads them as yours. A copy
still being read may be exported again, and a later export archives only
the marks no earlier one of that copy held. A highlight is looked for in
the chapter the export's section names, and in the copy's own commit
where the text has changed since. The export never says which highlight
a note was written on, so a note names the highlight listed beside it at
its place, or the two it sits between. The plan lets an earlier copy
leave the devices only once its notes were archived or declared not
worth keeping, and only a newer copy for the same reader makes a copy
earlier: two readers sharing a library or a device each keep their own.
No command deletes a document from the library: deletion is permanent
and takes the notes with it, so it stays with the reader.

How precisely a mark is placed depends on what the export carries. A
highlight carries its own words, so a highlight of a few words is found
on its lines. A highlight of a word or two names a spot rather than
quoting a passage: it is looked for in its chapter alone, named as one
of several places where the word recurs there, and left unlocated where
the chapter does not contain it, as with front matter no chapter heads
or a label the book generates, such as a figure's number. A note carries
only its own words, its page and its section, never the words it was
written on, so it is placed through the highlight listed beside it. A
note made on a highlighted phrase is as precise as that phrase; a note
made at a bare point has only its page and chapter; and a note listed
between two highlights at one place names both, since the export cannot
say which it belongs to. The page is the Kindle's own pagination, which
does not lead back to a line. So a reader who wants every mark placed
highlights a phrase of a few words and writes the note on that
highlight.

A chapter's file name is its identifier. Retitling a chapter does not
rename it, and inserting one does not renumber anything, because numbers
are derived at render time and nothing else refers to them.

A page that belongs in the book but not in its contents, such as a
copyright page or a back cover, carries `.unlisted` on its heading, with
`.unnumbered` if it would otherwise take a number:
`# Back Cover {.unnumbered .unlisted}`. Every format keeps it out of the
contents. In a DOCX that takes one more step, because Word builds the
contents from heading styles whenever a reader updates them, so the
build gives the page's title a style that looks like a heading and is
not one. Such a page belongs before the first listed chapter or after
the last: there it is front or back matter, which a blind read leaves
out by default, as most readers turn past it. Between two chapters, the
PDF reader packet has no segment to put it in, and refuses the book.

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
[manuscript]
spec = "manuscript/sections/_spec.md"
```

The file name is the project's own. Commands that use bindings require
this setting instead of guessing a name or location.

A project can keep its register in more than one shared file, for
instance what the work claims in one and the decisions about how the
work states it in another. List them, in the order they are read:

```toml
[manuscript]
spec = ["spec/claims.md", "spec/house.md"]
```

The files are one register. A binding, an `@omit`, a companion citation,
the trail and `spec rename` reach a section in either file by its
anchor, and an anchor is unique across all of them, so moving a section
from one file to the other keeps its name, its bindings and its trail.
The primer lists each file under its own heading. A single path is the
same register with one file in it.

Not every file need be published. A project that publishes its claims
and keeps its house rules to itself says which files a reader can see:

```toml
[manuscript]
spec = ["spec/claims.md", "spec/house.md"]
spec_public = ["spec/claims.md"]
```

Each published file must also be listed in `spec`. What is published may
not cite what is not: `notes --check` refuses a companion entry that
cites a section of an unpublished file, and a `#slug` reference in a
published spec file that names one, since its reader would follow it to
a page they cannot open. An unpublished file may cite anything, and a
unit's marks may bind any section. Without `spec_public` every spec file
is published.

Every `#slug` written in a spec file, bare or as a `](#slug)` link, must
name a section of the register, as a binding must. A heading's own
`{#slug}`, code, comments and frontmatter are not read as references.

The slugs are the whole mechanism. A chapter does not restate a
commitment; it names the section it expresses, so changing a commitment
tells you which chapters have to change with it. The spec is not
rendered into the book.

A slug is a name, and a section can outgrow it: reverse what a section
says and its slug may now say the opposite. The slug is written in the
spec's heading and in its links, in every sidecar that binds the
section, a derived edition's included, in an edition's `@omit` marks, in
companion citations, in the trail's heading and in the backfill ledger,
so renaming it by hand means finding all of them.
`quirework spec rename OLD NEW` renames it in every one, and refuses
before writing anything if it cannot: a new name already in use, an
anchor that is not one, a unit's own section moved to another unit.
`--dry-run` lists the files it would change.

`quirework spec notes --primer` prints the spec's sections first, then
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
[manuscript]
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
rendered copy is one of your turns (`--session redpen-v1 --turn 3`). The
kind says what the turn did to the section: `seed`, `sharpen`, `correct`
(refused a wording the agent proposed), `reverse` (moved off an earlier
position), `accept` (took a wording the agent drafted) or `probe` (tried
something without settling it).

The trail quotes the private corpus by reference, so keep it with the
private material. It is context, never authority: the spec states what
the book holds now, and a request made now outranks anything on the
trail. Leave `spec_trail` unset and the feature is off.

Reading is one command per depth. `spec notes --primer` counts each
section's breadcrumbs, and every listing of bindings marks a binding
whose section has any, as `[trail ×2]`, so an agent following one from
the prose learns there is a history to read. A section with none carries
no marker. `spec trail --unit opening` lists, one line each, the trails
of the sections a chapter binds. `spec trail cooling-asymmetry` reads
one section's trail oldest first, and prints the
`provenance show … --turn N` that opens each turn with the reply it
drew.

Writing happens while the spec is edited. When your words in the current
conversation change what a section holds, the agent adds a breadcrumb:

```bash
quirework spec trail add cooling-asymmetry --kind correct \
  --find "a route, not a speed" --note "Refuses speed as the difference."
```

`--find` takes words you typed, and an answer you picked when the agent
asked you a question with options is yours too, with any notes you
added. The preview the agent drew for an option is its own words, not
yours, so it is never found. It looks for them in the corpus and then in
this project's live Claude Code and Codex transcripts, so a turn from
the conversation still going on can be pointed at before it is captured.
A session captured partway through, say by a sweep run in the middle of
it, is read from its transcript while that holds turns the capture does
not. `--session ID --turn N` takes the number `provenance search`
printed. An `--excerpt` is optional: it highlights words in a long turn
where they would otherwise be buried, and it must be words the turn
holds.

`--harness H --session ID --at TIME` writes a pointer by hand, for a
conversation Quirework may not be able to read. A breadcrumb leads only
to a whole session id, so the id is taken whole: given a prefix, such as
the eight characters `show` heads a session with, the pointer takes the
whole id of the one session Quirework can read that starts with it,
captured or still live, and a prefix that names several sessions, or
none, is refused.

The judgment is the agent's, and it should err toward including. Add a
breadcrumb for every turn that seeds, sharpens, corrects, reverses,
accepts or probes what a section holds, since a reader can skip one but
cannot recover a turn nobody pointed at. Point at your turn, not the
reply. Never delete a breadcrumb because you moved on: add a `reverse`,
because the earlier position is part of the story. Delete one only when
it was wrong about which section the turn shaped.

`spec notes --check` fails a trail heading that names a section the spec
no longer has, so a renamed section takes its trail along in the same
edit; `spec rename` does that for you. `spec trail --check` follows
every breadcrumb to your turn. It fails a pointer that leads to no turn,
one into a session that curation removed, and one that names its session
by a prefix. It only warns about a session not captured yet, or captured
before the turn was made, which is what pointing at a live conversation
costs until that session is captured.

### Backfilling a trail

A spec that grew before it had a trail needs its history read, and a
corpus of hundreds of sessions is too much for a strong model to read
whole. `spec trail backfill` splits the work by what it costs. A cheap
model reads each session once and flags every turn of yours that might
have shaped any section. A strong model then takes one section at a
time, reads everything flagged for it oldest first, and decides. Reading
a session costs the same whether it is read for one section or for all
of them, which is why each session is read once, against the whole list.

```toml
[manuscript]
spec_trail = "private/spec-trail.md"
spec_trail_leads = "private/spec-trail-leads"
```

`backfill plan` writes each session as a packet: your turns, numbered
and stamped as `provenance show` gives them, each with the end of the
reply it answered, so a bare "yes" can be read as the acceptance it was.
It then writes a run plan that hands the packets to readers with no
tools, one seat per batch, so no reader's context grows from one batch
to the next. A batch is bounded by estimated tokens (`--budget`) and by
your turns (`--turns`, 80 by default), because a reader's answer,
thinking included, grows with the turns it answers for and stops at its
model's output limit. Short turns, such as red-pen marks, fill a token
budget with more turns than a reader can answer. A session longer than
`--turns` is read in parts of nearly equal length, one part to a seat. A
part's packet says which turns it holds, and its turns keep their
numbers. The plan names every session it hands out in parts. The run
engine runs the plan, and resumes it after a quota stop. Without
`--out`, the plan takes a new directory in the project's private state,
named for the day and the model, with a suffix for a second pass the
same day. It prints the sessions by harness, the whole cost including
each seat's menu, any session with neither a start time nor a recorded
date (sorted first), and the prepare, start and import commands with
their real paths. A Gemini review and a red-pen round log no times but
record their date, and take their place by it. A red-pen round's packet
opens by saying what its parts are, since only some of each mark is your
words. A review-notes entry compares your copy of a block with the block
rebuilt from source when the round was archived. A copy built by an
older renderer held more than that rebuild, such as headings and notes
agents had written, so the packet warns that those can show up among
your "+ now" lines. A round records the book revision it was matched
against, and the packet leaves out any "+ now" line the book's source
already held at that revision, since such a line was not written in the
round. It says how many it left out, or that git could not read the
revision. A comment's file and line are where the archive matched its
anchor, and a one-word anchor can match the wrong place, which the
packet also says.

`backfill import RECORD` checks each flag against the corpus and keeps
it in the ledger: a turn that is not yours, or a slug the spec does not
have, is dropped and reported with its reason. A session whose reader
did not say it finished is left for the next plan to hand out again.

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
flag reaches you rather than the ledger. An Antigravity plan accepts the
keyring flag every Antigravity seat carries, because its readers have no
tools; a record planned before that was possible takes
`--accept session-bus`. Import then prints each seat's share of flagged
turns and warns about a seat with many turns and almost no flags. A flag
count cannot tell a reader that skipped from one whose sessions held
nothing, so a reader closes each session with an `about` sentence on
what it was, written from turns it had to read. A warned seat lists each
session's title, which the reader never saw, beside that sentence, and a
mismatch or a missing one is the mark of a skipped packet. It ends with
the pass as a whole: sessions read, models, flags by confidence, and
sessions with no flag. A session with no flag is not suspect by itself;
a turn can matter only beside another session, such as the unrelated
thread in which a question is asked as a control.

`backfill SLUG` lists a section's candidates oldest first: what each
reader flagged, and the turns before the commits that changed the
section. A reader names the section it thinks fits, so a turn that fits
two can reach only one of them; `--also OTHER` adds what readers flagged
for a neighbouring section, marked with its slug. The list notes two
kinds of copy before you rule on either: a turn you sent twice, saying
which copy drew the reply, and a turn captured twice, when a resumed
session repeats the history it inherited under a new id. `--read` prints
each candidate whole, after the end of the reply it answered, cut as a
packet cuts it. A bare "yes, go" means only what that reply proposed, so
ruling on one needs the reply, and a section with sixty candidates would
otherwise take sixty `show` commands.

A commit leads to the session that made it only where that session's
model wrote the commit's subject, in the command, the message file or
the reply reporting it; a session that saw the subject in a `git log`
listing read about the commit and did not make it. A commit's quote is
matched whatever quotation marks either side used. A quote of fewer than
four words is looked for only in the session that made its commit, since
"agreed" is in half the corpus. `trail suggest SLUG --diff` adds what
each commit changed in the section, which tells you which paragraph a
session's turns went into.

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
have candidates left; its counts are the readers' flags, and a section's
list adds the turns its commits lead to.

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
  section, and `spec notes bind --slug S` is the reverse lookup that
  answers "what else does this commitment touch?".
  `spec notes relations` is the same filter, for a shell or harness
  guard that refuses any command containing the word "bind".
- `@guidance`: how the chapter should read, its length, what it is for.
- `@genesis`: where the material came from.
- `@todo`: open work.
- `@figure(name)`: a note about a plate.
- `@see(slug)`: a pointer into book-wide review notes.
- `@omit(slug)`: in a derived edition's sidecar only, a spec section the
  edition leaves out, and the reason, as its body (see Derived editions
  below).
- `@count(phrase)`: a spelled-out chapter count in this chapter that
  counts something else. `spec units --check` warns when a phrase such
  as "twelve chapters" differs from the book's own count, since an
  inserted chapter leaves it stale; a memoir about an unfinished
  eleven-chapter book names that count with
  `<!-- @count(eleven chapters) the other book's -->`, and the check
  leaves it alone. A mark whose phrase the chapter no longer says is
  reported, so it cannot excuse the next count written in those words.

`quirework spec notes --check` is the gate. It refuses a mark left in
the prose, a sidecar with no chapter, a sidecar heading that no longer
matches the chapter, a `@bind` naming a section the spec does not
declare, and a spec section missing its anchor. Run it before every
commit; it is what keeps the metadata from drifting away from the text
it describes.

It also prints advisories: a mark carrying a date, a spec section
written as an order, a line-number pin. They never fail the check, and
in a long-lived book they accumulate, so an edit's own advisory is easy
to lose among the standing ones. `--check --since REF` compares the
working tree with the book at a revision and lists the advisories that
are new first, then only a count of the rest. `--since HEAD` is what a
pre-commit hook wants: what this change is about to add. An advisory
that merely moved down because of an edit above it is not new.

`quirework spec quotes` checks that wording a mark quotes is still in
the book, and it is narrow on purpose. It checks only a long quotation
introduced as one (`reads "…"`, `the sentence "…"`,
`the sentence after it, "…"`, `the last line ("…")`), because most
quoted spans in marks are guards, coinages or outside sources that are
meant to be absent. After a rewrite or a retired term that is the wrong
trade, since the quotations left stale are short or uncued: a note that
says a sentence is `now "…"` names no cue. `--all` adds every other
quoted span in a group of its own. `--grep PATTERN` keeps the spans a
regular expression matches, so retiring a word is one command:

```bash
quirework spec quotes --grep '\bbreaker\b'
```

Most of what `--all` lists is correct. Read it once after the edit it
was run for.

## 4. Figures

Put the illustration sources in one directory and point at it:

```toml
[publish]
figures = "manuscript/sections/illustrations"
```

A figure counts as part of the book when a chapter references it:

```markdown
![A kettle, and the steam that reports it.](illustrations/kettle.svg){#fig-kettle fig-alt="A rounded kettle in outline with a curl of steam leaving the spout."}
```

`quirework publish figures list` shows what is embedded, `build`
materializes the set into the build tree, and `--check` validates it.
The sources stay tracked; the rasters and the derived galleries are
build output.

Figures often share a definition: a glyph placed with
`<use href="#steam">`, an arrowhead marker, a gradient. A style guide
that lists which figures place the glyph goes stale as soon as a figure
is added or cut. `quirework publish figures inventory` counts every such
reference instead, per definition. `--uses steam` gives one definition's
figures and counts, so the guide can say "run this" in place of the
list.

Two hooks make the figure pipeline a project's own without touching
Quirework. `publish.grayscale_transform` names a function taking SVG
text and an asset name and returning SVG text, for a book that wants its
own color-to-gray meaning in the EPUB rather than a generic
desaturation. `publish.figure_checks` is a list of functions returning
diagnostic strings, for conventions only this book has. Both are
installed Python in the project's own package, selected explicitly.

`quirework publish fonts` reports the families the build needs, read out
of the Typst template and the figure sources rather than declared
anywhere. A missing family is a hard stop, because Typst and rsvg both
substitute silently and the book would render whole in the wrong face.
`--any-font` overrides it.

A figure the page shows as an image is drawn in isolation. It can use
the fonts installed on the reader's device and nothing else, so the
book's own web fonts never reach its labels, and a reader without the
face gets a fallback of different widths. `publish.figure_fonts` lists
font files for the figures to carry instead:

```toml
[publish]
figure_fonts = ["manuscript/fonts/Face-Regular.woff2", "manuscript/fonts/Face-Bold.woff2"]
```

At build time each staged figure, color and gray, gets a subset of every
listed face it could ask for, holding only the characters it letters.
The sources are left as they are. `figures --check` fails when a face
cannot draw a character a figure uses, since that glyph would reach the
reader from a fallback. The setting needs fontTools, with Brotli to
write WOFF2 (the `quarto` extra installs both).

A figure is drawn at one size and printed at another. Where
`publish.typst_template` sets the page, `figures --check` works out how
large each figure's type prints across the text block and warns about
anything under 6pt, the size below which incidental type stops being
readable. It warns and never fails, because raising a label moves the
drawing around it. A book that letters its figures larger raises the
threshold to the size it chose, so a plate that slips back under it is
reported:

```toml
[publish]
figure_type_floor_pt = 7.4
```

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

Every route also defines two start words, which decide whether the
companion waits after presenting its plan. A request whose first word is
`go` is carried through. `discuss`, or no start word at all, stops once
the plan is shown and before any model run, sub-agent or commit, so a
bare route never spends what the author has not seen. The plan is shown
as concretely as that allows: a frozen run record that has not started,
or an edit left uncommitted. The stop is the default because the two
misreadings cost different amounts: taking `go` for `discuss` costs a
round-trip, and the reverse costs a run nobody approved. A start word
stands alone, so `/qw-tidy go: the attic notebook` starts at once, while
`/qw-tidy go through the attic notebook` is an ordinary request and
waits. Claude routes add an `argument-hint`, so the words show while the
route's name is typed; portable routes carry them in their prose. A
route open to implicit invocation keeps the words for a human's request
and has no stop when the assistant invokes it unasked.

A long procedure is often read for one of the things it covers.
`sop show NAME --outline` lists its headings, and `--section TITLE`
prints one section with its subsections, verbatim. The option repeats,
and a title used twice is named by its path, `'One page > Checks'`. When
the procedure already knows what a kind of request needs, it says so in
a marker, one per mode:

```markdown
<!-- @mode one-page: Choosing a pass; One page -->
```

`sop show NAME --mode one-page` then prints the procedure's opening
(everything before its second heading) followed by those sections, and
the generated route lists the modes. A reader handling a one-page tidy
reads two sections and the goal, not the whole notebook procedure. The
catalog check resolves every declared section, so renaming a heading a
mode names fails the gate rather than the next reader.

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
  "default_scope": "work",
  "prompts": {
    "first": {"text": "{{persona}} Read the opening. What do you expect next?"},
    "next": {"text": "Keep reading. Where did you stop believing it?"},
    "wrap": {"text": "Say in three sentences what the book was for."}
  },
  "sequence": {"first": "first", "next": "next", "wrap": "wrap"}
}
```

Every ask is an object with `text`, not a bare string, so an ask can
also carry `scopes` and `headings` later without changing shape. A scope
says how much of the work the seat is given: `work` for a progressive
read of all of it, `artifact` for one unit or file, `passage` for part
of one, and `changes` for a diff. `sequence` names which ask opens,
which repeats, and which closes; it is what makes a set of questions an
order rather than a collection. `{{persona}}` is where the selected
profile's prompt lands. `revision` is a string and is bumped whenever
the questions change, because a prepared run freezes the rendered prompt
and an old record has to keep the questions it actually asked. Two
optional fields are read only by a self-driven read, which the
[workflow guide](workflows.md) describes: `reading_task`, what the seat
writes for each chapter, and `orientation_ask`, an ask the seat reads
before the book.

## 6. Derived editions

A derived edition retells the book for another reader or in another
form: a memo for a manager, a version for young readers, the book as
poems. The canon stays the master. An edition is made from it and never
feeds back into it.

Each kind of edition is a procedure, `variant-KIND.md`, beside a shared
`variant-common.md` that says what every edition keeps. Both carry a
`## Blind-draft brief` section, which is the text the drafters are
handed: the shared one holds the voice rules, the kind's own describes
its reader. `quirework publish variant` lists the kinds and
`publish variant KIND` prints one's procedure.

`quirework publish variant build KIND` fetches blind drafts into
`variants/KIND/_raw/`, one per chapter. Each drafter reads the whole
book with every mark stripped, in a seat of a run with no tools and no
project files around it, so it retells the book as an outside reader
would. Any harness the run engine drives can draft. The variant policy
says which, for every kind or for one:

```toml
# config/variant-policy.toml, named by publish.variant_policy
default_model = "opus"
default_effort = "high"

[kinds.poems]
harness = "codex"
model = "gpt-sol"
```

An edition that is one piece rather than a book, such as a memo, is
drafted whole. `shape = "document"` in its kind's table gives it one
seat, which reads the book and retells all of it as a single document
with `##` sections, into `_raw/document.md`. Its brief then describes
the whole document's reader and form, not a chapter's.

An edition drafted by chapter may have no place for some of the book's
pages, as a retelling for young readers has none for a back cover.
`omit = ["back-cover"]` in its kind's table names them by file name, and
a build then drafts none of them and says so. A name that matches no
chapter is refused.

`--harness`, `--model` and `--effort` override the policy for one build.
The plan and its record stay in the project's private state, outside the
checkout. Each draft comes in with an entry in `_raw/receipts.json`: the
harness, the model asked for and the model that answered, the effort,
the price where the harness reports one, and the run and seat it came
from. A seat that has not answered, or that raised a red flag, stays out
of `_raw/`, and the build prints the commands that reconcile its run and
import what it then finishes, `publish variant import KIND RECORD`.
`--plan-only` writes the plan and prints those commands without starting
it, and `--only` redrafts the chapters it names, leaving every other
draft and receipt as it was.

The drafts are what a model wrote, so `text wrap` never reflows them.
The edition is composed from them by the procedure: each draft read
against the spec, then the documents written beside them with a
`_quarto.yml` of the edition's own. A book-shaped edition lists its
chapters as the book does. An edition that is one document is a plain
Quarto project:

```yaml
project:
  type: default
  render:
    - memo.qmd
format:
  html: default
  typst: default
```

A composed edition answers to the same spec as the canon. Each of its
documents may keep a marks sidecar, `_marks/STEM.md` beside it, whose
headings mirror the document's as a chapter's sidecar does. `@bind`
names a section the document carries, and `@omit` a section the edition
leaves out, with the reason:

```markdown
## Why the kettle matters

<!-- @bind(kettle-signal) -->
<!-- @omit(cooling-asymmetry) A memo to the buyer has no room for the physics. -->
```

`spec notes bind --slug kettle-signal` then lists the chapter and every
edition that carries the commitment, so changing it tells you every
artifact that has to change with it. An edition's unit is named
`KIND/STEM`, so an edition's `opening` is never taken for the canon's,
and a trail or a backfill menu only ever hears of the canon.
`spec notes --check` holds an edition's sidecars to the chapter rules,
refuses an `@omit` with no reason or outside an edition, and lists as an
advisory each section the canon binds that an edition neither binds nor
omits.

Once an edition has its `_quarto.yml`, `publish build` renders it after
the book, through the same three stages, into
`build/variants/KIND/book/_site/`, in the formats it declares. Quarto
never runs in the edition's directory, so nothing collects there to be
ignored. `--edition KIND` renders that edition alone and `--no-editions`
the book alone. An edition keeps its documents at its top level, beside
its `_quarto.yml`, and embeds a figure as a chapter does, as
`illustrations/NAME.svg`. The figure comes from the book's own gallery,
so the same source draws it in every edition that shows it.

## 7. Checks of your own

`checks.command` names a function in the project's package, and
`quirework dev check` runs it. That is where a book's own rules live:
prose guards, vocabulary rules, anything Quirework has no business
knowing. A project without one gets the default suite. It checks that
every Python file parses and that `ruff format` would change nothing. It
also checks that `quirework text wrap` would change no Markdown file, so
wrapping a chapter to fit one edit never reflows the rest of it. The
wrapper leaves a saved run record alone only when `runs.evidence_roots`
names its directory, so declare where a room or a review keeps its
records before you wrap anything the check names. An edition's blind
drafts need no declaration. `text.modes` maps paths to formatting modes,
so a project can wrap its guides at 72 columns and keep its chapters one
sentence per line; `text.classifier` selects a function instead, for a
rule a glob cannot say.

## 8. A site instead of a book

A site of long-lived posts, such as a blog built by Hugo, adopts the
same layers with three differences. Its units are pages rather than
chapters in a Quarto file. Each post argues its own thing, so each can
keep a spec of its own. And the site's generator renders everything
under its source, so marks live somewhere else.

```toml
schema = 2

[manuscript]
format = "pages"
source = "content"
pages = "posts/*/index.md"
unit_name = "post"
marks = "specs/{slug}/marks.md"
spec = "specs/_site.md"
unit_spec = "specs/{slug}/spec.md"
spec_trail = "private/spec-trail.md"

[publish]
backend = "hugo"

[text]
modes = [["content/**/*.md", "sentence"], ["**/*.md", "column"]]
terminology = "quirework/terms.toml"
```

A post in a page bundle, `content/posts/why-the-lamp-turns/index.md`, is
the unit `why-the-lamp-turns`. Its frontmatter, TOML or YAML, gives its
title, its date, which orders the posts, and whether it is a draft.
`quirework spec units` lists them, then each post specified but not yet
drafted, then each idea in `manuscript.ideas` no post has taken up. An
About page can be a unit too, once the site names its collections.

The post's spec is `specs/why-the-lamp-turns/spec.md`, written like any
spec: its reader, its thesis, its boundaries, each a section with an
anchor. The anchors can be short, `{#reader}` and `{#thesis}`, because
each is named with its post: `why-the-lamp-turns/thesis`. A post binds
its own sections without a mark, because it is the post they describe,
and in its own sidecar `@bind(thesis)` means its own thesis. The shared
spec holds what several posts lean on, and a post binds one of those
with `@bind` in its sidecar, under the heading that carries it. Another
post shares a post's claim by naming it in full. When a shared claim
changes, `spec notes bind --slug S` names every post that has to be read
again. A spec may come before its post: a post is often specified long
before it is drafted, and `spec notes --check` lists such specs as an
advisory so a mistyped slug does not hide among them.

`spec notes --primer -c why-the-lamp-turns` is how a session starts on
one post. It prints that post's spec, the shared sections it binds, and
its marks, rather than every post's commitments.

A published post is revised for years, and `spec units --check` keeps
that honest. It names a published post with an open `@todo`, and one
whose spec was revised after the post was last dated, until its
`lastmod` records the revision.

The trail works as it does for a book, over one register: breadcrumbs
are kept by a section's full name, whichever file holds it. A post often
begins in a conversation held in another project. Point at that project
with a `@genesis` mark rather than copying its sessions into this
corpus, which captures the sessions run here.

Voice rules the site holds to go in the terminology policy. A rule
already made, such as "no em-dash", is `retired` and fails the check. A
word that is usually wrong but sometimes right is a `term`, and waits
for a ruling. A habit that a model leaves in the posts it drafts is a
phrase in the tells policy, `text.tells_policy`, and `text tells`
reports it without failing anything. `text wrap` keeps the posts one
sentence per line and the guides at 72 columns, as `text.modes`
declares.

`quirework publish build` runs Hugo with the site's own configuration
into `build/site`, never into the directory the site deploys from.
Deploying stays the site's business.
[examples/demo-site](../examples/demo-site) is a small site that walked
this part.

## Where to go next

- [examples/side-quest](../examples/side-quest): a complete book that
  followed this guide, with every layer above adopted, three derived
  editions among them, and the sessions it was written in. Read its
  `quirework.toml` beside this page, and its published editions at
  [quirework.com/examples](https://quirework.com/examples/). AI wrote it
  to test the tool, and no person reviewed its prose, so read it for the
  layers, not the writing.
- [examples/demo-site](../examples/demo-site): a small site that
  followed its last part.
- [configuration.md](configuration.md): every setting, the layering
  rules, and the annotated templates (`quirework config template book`
  or `site`).
- [workflows.md](workflows.md): procedures, personas, panels, model
  runs, recovery and evidence.
- [development.md](development.md): the architecture and the tests, for
  changing Quirework itself.
