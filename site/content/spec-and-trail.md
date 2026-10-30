---
title: The spec and the trail
lede: >-
  The spec states what the work holds now. The trail keeps how it got there.
  Between them, a companion can change a commitment everywhere it is
  carried, and read why it was made before it does.
description: >-
  How Quirework keeps a work's commitments in a spec, binds every chapter,
  post and edition to them, checks the structure, and keeps a trail from
  each commitment back to the conversations that shaped it.
---

## The problem it was built for

A claim stated in one chapter gets restated in another, and the
restatements drift. Each one reads well where it stands. Together they
disagree, and nobody notices until a reader does. It happens to a long
argument, a history, a site whose posts lean on one another: anything
that has to stay consistent with itself while it grows.

{{< diagram name="commitment" narrow="commitment-narrow" caption="One commitment, and every place the work carries it. When the commitment changes, every one of them is listed. The circle marks the one that still says what the work no longer holds." >}}

The listings on this page are real output, run against Side Quest, the
sample book in Quirework's repository. The book is fiction that AI wrote
to test the tool, and no person reviewed its prose.
[The examples page](/examples/#side-quest) says more.

## A file of commitments

The spec is one Markdown file. Each commitment is a level-two section,
and its heading carries a slug, a short permanent name. This one, from
the sample book, is a commitment a check enforces:

```markdown
## Em's dashes stay off the page {#em-dashes-off-the-page}

The prose contains no em dash anywhere, so Em's are counted and never
shown. A message from Em is reported or paraphrased, never quoted with
its punctuation intact. The book says why in its own terms: it has
promised not to, and the software is checking. The word "spec" never
reaches the reader.
```

A chapter never restates a commitment. Its notes name the section it
expresses, and that name is the whole mechanism: change the section, and
the names lead to every chapter that has to change with it.

{{% aside %}}
The checks flag a spec that journals: a date, a review round, a commit
hash, or what a section used to say. They report it as an advisory,
never as a failure.
{{% /aside %}}

The spec states and does not argue. It holds no dates, no review rounds,
and no account of what a section used to say. A dated ruling reads as
authority to the next session, which then works around the section
instead of proposing a change to it.

A session starts by reading it. `quirework spec notes --primer` prints
the spec's sections first, then each chapter's notes in reading order.

{{< listing name="primer" lines="1-23" caption="The opening of the sample book's primer: twelve commitments, each with a count of the moments that shaped it, then the notes of the first chapter." >}}

## Notes beside the prose

Each chapter has a sidecar, a Markdown file whose headings mirror the
chapter's. It holds the chapter's marks. A `@bind` names a commitment
the chapter carries. A `@guidance` says how the chapter should read.
Other marks hold open work, counts that must stay true, and quotations.
Here is the start of one chapter's sidecar in the sample book:

```markdown
# The Name {#sec-the-name}

<!-- @guidance the irony of who named the tool is left for the reader to
     find: "quire" is the page's last line and the word Cy types, Otto
     only finishes it, and Cy likes the result better than anything on
     the page, and nothing after it explains the joke. "For a year I
     told people Otto named it. Then I found the page." comes right
     after Otto's word, once, and nothing explains it -->
<!-- @bind(the-name) -->
```

{{% aside %}}
A site keeps its notes outside the tree its generator renders, so no
visitor is ever served one. The
[Lighthouse Log](/examples/#the-lighthouse-log) shows the layout.
{{% /aside %}}

The marks never touch the prose. The rendered book carries none of them,
and reflowing a paragraph never moves one. The prose stays the prose,
and the notes about it stay findable.

## One commitment, everywhere it is carried

`spec notes bind --slug` lists every unit that binds a commitment. A
derived edition binds the same spec, so a commitment can be followed
from the chapter that states it into every edition that retells it. An
edition that leaves a commitment out says so with an `@omit` mark and a
reason.

{{< listing name="bind" caption="One commitment in the sample book, about where the tool's name came from, in its chapter and in the three derived editions." >}}

A slug can outgrow its name. It is written in the spec, in every sidecar
that binds it, and on the trail. `quirework spec rename OLD NEW` renames
it in all of them, or refuses before writing anything.

## What the checks catch

`spec notes --check` gates the invariants of the structure. Among them:
a mark left in the prose, a sidecar that no longer mirrors its chapter,
a binding to a commitment the spec does not declare, and a reading order
that disagrees with the files on disk. `spec quotes` checks that a
phrase quoted in a note still exists in the work, and
`spec quotes --grep TERM` finds every quotation of a term you have just
retired.

{{< listing name="checks" caption="The sample book's structural checks as they stand. The advisory is real: one note carries a date, and a note, like the spec, states the current position, never its history." >}}

Research gets a register of its own. Each source has a slug, and a mark
cites it as `@source(slug)`. `spec sources --slug S` lists every chapter
that leans on a source, and the check refuses a citation of a source the
register does not hold. A source can keep a plain-text capture of
itself, and a quotation from it is checked against that capture. A
misremembered line is caught the way a misquoted chapter is.

## The trail

A spec section says what the work holds. The words you used in June and
the sharper ones from last week say how it got there, and whoever
changes the section next should read them. The trail keeps that story
outside the spec, in a sidecar of its own, kept with your private
material. Under each section's heading it lists breadcrumbs. A
breadcrumb names a conversation, the moment in it when you shaped the
section, and what that turn did:

- **seed**: first stated what the section holds
- **sharpen**: made it more precise
- **correct**: refused a wording the companion proposed
- **reverse**: moved off an earlier position
- **accept**: took a wording the companion drafted
- **probe**: tried something without settling it

{{< diagram name="trail-general" caption="The trail of one commitment, oldest first. Each breadcrumb points at the turn where you shaped it, and says what that turn did." >}}

A breadcrumb is a pointer, never a copy. It names a session and the time
the harness logged your turn, which anyone can find again, with
Quirework or without it. Your words are read from the session itself
whenever the trail is read, and the command to open each turn is printed
beside it.

{{< listing name="trail" lines="1-14,21-24,36" caption="A real trail, from the sample book, as `spec trail` prints it. Each breadcrumb shows the note the companion wrote, how the author's turn opened, and the command that opens it. The author is invented: an AI wrote every one of the author's turns." >}}

{{% aside %}}
`spec trail --check` follows every breadcrumb back to your turn, and
fails one that leads to no turn or into a session that was curated out.
{{% /aside %}}

The trail is written while the spec is edited. When your words in the
current conversation change what a section holds, the companion adds a
breadcrumb in the same session, because it is the one reader that knows
which words did it. It errs toward including one, since a reader can
skip a breadcrumb but nobody can recover a turn that nobody pointed at.
A breadcrumb is never deleted because you moved on. A `reverse` is added
instead, because the earlier position is part of the story. A project's
commit check can name the sections a change rewrote with no breadcrumb
beside them, as a reminder and never a gate.

The trail is context, never authority. The spec states what the work
holds now, and what you ask for now outranks anything on the trail.

## Reading older sessions into leads

A spec that grew before it had a trail needs its history read, and
hundreds of sessions are too much for a strong model to read whole.
`spec trail backfill` sends cheap readers through each session once.
They flag every turn of yours that may have shaped a section, each flag
with its kind, its reason and how sure the reader is. Each session is
read once against the whole list, because reading it costs the same
whether it is read for one section or for all of them.

A flag that nobody has kept or rejected is a lead. `spec trail SLUG`
counts a section's leads, and `--leads` lists them after its
breadcrumbs: the ones a reader flagged with high confidence, or every
one with `--all`. Whoever reads the trail judges a lead in context, and
can keep it as a breadcrumb or reject it. A lead stays a reader's guess
until then.

When a section's history has to be right, a strong model takes the
section whole, reads every lead for it oldest first, and decides. That
is the thorough route and the expensive one, so it is kept for the
sections that need it.

The [authoring guide](repo:docs/authoring.md) walks through a spec, its
marks and its trail from an empty directory.
