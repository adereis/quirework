---
title: Quirework
headline: Write and research with an AI companion, and keep the work agreeing with itself.
lede: >-
  Quirework sits under the AI companion you already work with in a terminal.
  It gives the collaboration a spec of what the work holds, procedures the
  companion follows, checks that catch drift between chapters, and a record
  of how the work was made. You set the aim and make the calls. The
  companion does the work and brings back the evidence.
---

## The spec holds the center

A long work makes commitments. A year in, they live in one chapter's
phrasing and contradict another's. Quirework gives them a file of their
own. Each commitment is a section with a short name, its slug, and each
chapter's notes name the commitments the chapter carries. When one
changes, the companion lists every chapter, post or edition that has to
change with it, before a local edit turns into a contradiction somewhere
else.

{{< diagram name="commitment" narrow="commitment-narrow" class="draw-on-view" caption="One commitment, and every place the work carries it. When the commitment changes, `quirework spec notes bind --slug` lists them all. The circle marks the one that still says what the work no longer holds." >}}

{{% aside %}}
The notes live in a sidecar beside each chapter, never in its prose. The
rendered book carries none of them, and reflowing a paragraph never
disturbs one.
{{% /aside %}}

Checks keep that structure honest. They fail a note left in the prose, a
chapter that binds a commitment the spec does not declare, a quotation
the text no longer contains, and a citation of a source the research
register does not hold.

[How the spec and the trail work](/spec-and-trail/)

## The trail remembers why

The spec says what the work holds now. The trail says how it got there.
For each commitment it keeps pointers to the moments in your
conversations where you seeded it, sharpened it, refused a wording, or
changed your mind. A pointer is never a copy, so your words are read
from the session itself. Before a companion changes a commitment, it
reads your own turns about it, oldest first.

{{< diagram name="trail-general" caption="The trail of one commitment, oldest first. Each breadcrumb points at the turn where you shaped it, and says what that turn did. A change of mind is added to the trail and never replaces what came before." >}}

The trail is context, never authority. The spec states what the work
holds, and what you ask for now outranks anything on it.

## Procedures your companion follows

A procedure is a file your project writes: how this work drafts a
chapter, checks a claim, researches a source or prepares a blind read.
Quirework catalogs them, prints the reading each one requires in
dependency order, and generates short entry points so a companion can be
pointed at one by name. The procedure stays the source of truth.

Five patterns recur, and Quirework supplies their mechanics. What tells
them apart is who sees what.

{{< diagram name="seeing" caption="Five procedures, by what each seat may see." >}}

[The procedures, and what each one is for](/procedures/)

## How a session goes

You describe the job in conversation, the way you would to a
collaborator: revise chapter five, check this claim, find what the
sources say about it. The companion follows the procedure your project
wrote for that kind of job.

{{% aside %}}
A procedure is a Markdown file in the project. The companion opens it
with `quirework sop show NAME`, which prints it verbatim and records
that it was read.
{{% /aside %}}

1. You name the aim and the scope.
2. The companion reads the project's guide and the procedure for the
   job.
3. It loads the spec: what the work commits to, where each commitment is
   carried, and the notes on the chapter at hand.
4. It drafts, revises or researches inside the procedure's limits.
5. Where other views would help, it convenes other models, each seeing
   only what the procedure allows.
6. It weighs what they return against the spec. A model's answer is
   evidence, never authority.
7. It runs the checks the procedure names, and brings back the change,
   the evidence, and any decision that is yours to make.

{{< diagram name="loop" caption="Every procedure runs the same loop. The companion is the operator. The other models are seats: each receives a declared packet and returns evidence." >}}

You can run every command yourself, and the
[workflow guide](repo:docs/workflows.md) shows how. The tool was made
for the other way of working: describing the job, and judging what comes
back.

## Any model, in any seat

The companion you talk to is not tied to the models it calls on. One
procedure can seat models from different makers, through different
harnesses, beside a person. Each seat declares its model, its effort,
and what it may see and use. Where the operating system can confine a
seat, it is confined. Where only an audit is possible, the record says
so instead of claiming isolation.

{{< diagram name="seats" caption="A run is a plan, frozen before any seat starts. Each receipt records the model the provider actually served, which can differ from the one requested." >}}

Independence is the reason. A draft can be reviewed by a model that did
not write it, a blind reader can run on a different model from the one
that drafted the chapter, and a work's editions can be drafted on
different models, so that they are not all one model's voice.

## A record of who did what

Your sessions are captured from each harness's own records into a store
outside your repository. They wait there until you read and accept them.
The repository keeps only the decision: which sessions count, each with
a hash of its bytes. The transcripts never bloat the work's history, and
the record still proves what was said.

{{< diagram name="provenance" caption="The record stays out of the repository, and the repository can still prove which sessions it stands on." >}}

From that record, Quirework compiles your own words and searches them,
by keyword or by meaning, with the index built and kept on your machine.
It estimates the division of labor between you and the machine, with its
caveats attached.

{{% band %}}

## A book being written with it

A book by Ademar Reis, with the subtitle *A software engineer's
philosophy of mind*, is due in early 2027. It is being written with
Quirework, and its site, mindwright.dev, describes the collaboration
this way.

> Human-directed, AI-articulated. The book is being written by a human and
> an AI together, over months and hundreds of sessions. The intuitions, the
> judgments and the direction are the author's. Most of the sentences are
> the machine's, and the book will account for exactly who did what.

The book is not published yet. When it is, it will be the measure of the
tool. The two samples below are not.

[Read about the book at mindwright.dev](https://mindwright.dev/)

{{% /band %}}

## Two samples, made to test the tool

Quirework's repository carries two worked examples, a book and a blog.
**Both are test fixtures, not showcases.** Their authors, their people
and their stories are invented. AI wrote them, and no person edited or
reviewed their prose. Quirework is built for an author who judges every
page, and these two had no such author. So read them for the structure
the tool keeps, not for the writing.

[Side Quest](/side-quest/book/) is a short comic memoir by Cy Dequest,
about building Quirework instead of finishing a book. Cy is invented: an
AI played the author and drove the companion that did the work. It is
not an account of how Quirework was made. The tool's part is real, and
it is the part to judge: a spec and its trail, a book in four formats,
three derived editions bound to the same spec, and the fifteen sessions
it was written in.

You can [read it in the browser, or download it](/examples/#read-it), in
every format and edition.

{{< diagram name="bind" narrow="bind-narrow" caption="One commitment in Side Quest, about where the tool's name came from, and the four documents that carry it. When the invented author corrected the story, an independent review found that one edition now defined a word its notes say to show. The circle marks that finding." >}}

[The Lighthouse Log](/examples/#the-lighthouse-log) is the site version,
and invented too: the log of a lighthouse keeper who does not exist. It
is a small Hugo blog with a spec for each post, a shared spec for what
the posts lean on, notes kept outside the tree Hugo renders, and a voice
policy the checks enforce.

Both run in Quirework's own test suite, so a change that would break a
working project fails there first.

## Get started

Quirework is under heavy development. Its commands and settings still
change between releases, and a new project can find rough edges in it.
So install it inside your project, as a checkout your companion can fix
or improve while it works.

{{% aside %}}
The project pins the revision of Quirework it runs. A fresh clone gets
the same tool, and moving to a newer one is a commit of its own.
{{% /aside %}}

Quirework needs Python 3.11 or later. In your book's or site's
repository, add it as a submodule and install it from there:

```
git submodule add https://github.com/adereis/quirework.git vendor/quirework
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -e 'vendor/quirework[quarto]'
```

The `quarto` extra installs what a book needs to render; leave it out
for a site. Then open your companion there and ask it to start a book,
or a site, by the [authoring guide](repo:docs/authoring.md). The
[configuration guide](repo:docs/configuration.md) connects a project you
already have.

The released package is on PyPI as `quirework`, for running the commands
yourself or trying the tool out. The
[install instructions](repo:README.md#install) cover both, and a clone
for working on Quirework itself.
