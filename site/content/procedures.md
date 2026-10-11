---
title: Procedures
lede: >-
  A procedure is how your project does one kind of job. Quirework makes
  procedures findable and followable, and supplies the mechanics for the
  five that recur when a work is written with more than one model.
description: >-
  How Quirework catalogs a project's procedures, and the mechanics it
  supplies for writers rooms, roundtables, prose workshops, blind reads and
  consistency reviews across models and harnesses.
---

## What a procedure is

A procedure is a Markdown file in the project: how this work drafts a
chapter, checks a claim, researches a source, or makes an edition. A
catalog says what each one requires reading first, which tools it uses,
and which other procedures may follow it.

{{< listing name="sop-list" caption="The procedures of Side Quest, the sample book in Quirework's repository, grouped by the question each one answers. The book is fiction, invented to exercise the tool." >}}

`sop explain` prints a procedure's required reading in dependency order,
with its length, so the companion knows what to read before it starts.
Follow-up procedures are listed and never launched. Whether one is
triggered is the operator's call.

{{< listing name="sop-explain" caption="What writing a chapter of the sample book asks the companion to read, and the tools it runs." >}}

{{% aside %}}
Personas say who is reading. Instruments say what a reader is asked.
Panels say which perspectives run on which models. All three are
versioned data in the project, frozen with each run.
{{% /aside %}}

`sop skills` generates thin entry points for the companions, so you can
name a procedure in conversation and the companion opens it. The entry
point only routes. It opens the procedure through `sop show`, which
prints it verbatim and records that it was read, and the procedure stays
the one place its content lives.

## Five that recur

Each project owns its procedures and its editorial rules. Quirework
supplies the mechanics for five patterns, and what sets them apart is
who sees what. Each pattern below closes with how the sample book used
it, marked as such, since that book and its author are invented.

{{< diagram name="seeing" caption="The five patterns, by what each seat may see." >}}

| You want | Use | Each seat sees |
|---|---|---|
| Real alternatives to choose between | A prose workshop | One frozen packet, never another writer's draft |
| An answer argued out through disagreement | A writers room | Its own packet, then the shared board between rounds |
| Several models working together, as themselves | A roundtable | The shared packet, every turn as it happens, and a shared folder |
| What the page tells a reader | A blind read | Only the material a reader would see |
| An edit tested against the whole work | A consistency review | The guide, the spec, the notes and the exact change, read-only |

### Prose workshop

Several writers solve the same bounded problem, each blind to the
others. Every writer gets the same frozen packet in a private workspace,
without the project's instruction files or the companion's memory, so
the experiment tests the brief rather than the habits of whoever
prepared it. The drafts are sealed under letters before anyone reads
them, and the writers' identities open only after the editorial judgment
is recorded.

{{% sample %}}
In the sample book, the first chapter came from a workshop of three
writers on three different models. The invented author read A, B and C,
chose A, took one paragraph from B, and only then opened the key. The
sealed drafts and the key are in the book's `provenance/workshops/`.
{{% /sample %}}

### Writers room

Several seats argue one question over numbered rounds and read each
other between them. Each seat keeps its own session, so a later round
resumes with everything it has read and reasoned, and is handed only
what changed. A seat writes to its own outbox, and promotion copies each
post onto the board, hashed and attributed. The digest of a round
concatenates its posts whole and never summarizes them.

{{% sample %}}
In the sample book, the fourth chapter was argued by a room of four
seats on three harnesses over six rounds, with the author in the chair.
Each seat held different material on purpose. The seat arguing for Otto,
the assistant the book could have made a villain, was given nothing
beyond the brief, so that the best defense came from a seat that had not
been told what the book thinks. The plan, the personas and the board are
in `provenance/rooms/`.
{{% /sample %}}

### Roundtable

A roundtable seats models as themselves. Each is named for its model
family, told which others are at the table, and told that the supervisor
reads everything. Turns are sequential, so a seat reads what the one
before it wrote minutes earlier, and every seat has the same tools and a
folder the table shares. A person can take a seat as well, described in
the roster as a person, never as one more model.

{{% sample %}}
In the sample book, the back cover was written at a roundtable of three
models, and the text that went on the book was the author's choice. The
board, the drafts and each model's final text are in
`provenance/roundtables/`.
{{% /sample %}}

### Blind read

A blind reader meets the work as a reader would: no spec, no notes, and
no author's explanation, unless the question requires them. The absence
of that context is the test. A reader can be handed one chapter per
launch, which freezes exactly the bytes it saw, or walk the whole book
with its own tools, writing a reaction to each chapter before it opens
the next.

Blindness does not make a reader right. It makes the reaction answerable
to the material the reader actually saw.

{{% sample %}}
In the sample book, the whole book was read by two readers on two
harnesses, each alone, with a closing question shown only after the last
chapter. Both asked for gaps to be filled, and neither asked for more
pages, which settled that the book was the length it wanted. Each
reaction is in `provenance/readings/`.
{{% /sample %}}

### Consistency review

Consistency has a mechanical layer and a judgment layer. The checks
cover what can be stated exactly. The review asks whether an edit that
is coherent on its own is coherent with the rest of the work. An
independent reviewer gets the project guide, the spec, the notes, a
brief and the exact Git range, with the repository read-only and its
history in reach, and without the operator's saved conclusions.

{{% sample %}}
In the sample book, when the author corrected the story of the name, a
review of the change found two problems. The young readers' edition now
defined "quire", which its own notes say to show in a sentence. Two new
lines of the poem did not scan in the metre its procedure assigns. The
reviewer's reports are in `provenance/reviews/`.
{{% /sample %}}

## Models and harnesses

The companion is the operator, and a model seat is something it
convenes. One procedure can mix seats from different makers and
harnesses, and a person. Preparation refuses a capability the selected
harness cannot provide. The receipt records the boundary that was
actually enforced or audited, the effort the model actually ran at, and
the model the provider actually served.

{{< diagram name="seats" caption="A run, from the plan the companion writes to the receipts that come back." >}}

## Runs you can trust later

A run is a plan of seats and ordered turns, with an explicit model and
effort for each. Preparation freezes the prompts and inputs and hashes
them. Launch intent is recorded before a process starts, and a pending
request is reconciled before anything is resubmitted, so an answer is
never bought twice.

A run stopped by a quota limit continues in its own session instead of
starting over. A turn cut off by an error waits for your decision, with
its session intact. A compaction is reported even when the answer looks
fine. Earlier answers and rejected attempts stay exactly as they were.

## Reading the record

Captured sessions support procedures of their own. `provenance search`
reads your own turns, by keyword or by meaning. `input` compiles your
verbatim words. `metrics` estimates the division of labor between you
and the machine, with its caveats attached. `coldread` puts the record
in front of blind analysts and caches their reports by content, so a
changed question misses the cache on purpose.

The [workflow guide](repo:docs/workflows.md) covers each pattern, run
plans, and recovery in full.
