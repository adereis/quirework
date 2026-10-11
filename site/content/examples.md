---
title: Examples
lede: >-
  Two projects in Quirework's repository consume it the way yours would: a
  sample book and a sample site. Both are fiction, invented to exercise the
  tool, and both run in its test suite.
description: >-
  Side Quest, a short book with a spec, a trail, four formats and three
  derived editions, and the Lighthouse Log, a Hugo site with a spec per
  post: the two worked examples in Quirework's repository.
---

## Side Quest

{{% aside %}}
{{< shot src="images/side-quest/cover.jpg" alt="Read Side Quest, the fictional sample book. Its cover is a pencil drawing of a kitchen table with a tied manuscript, a box of index cards, a laptop and a mug, and one line on a loose page circled in orange." width="240" class="cover" link="/side-quest/book/" caption="[Read the book](/side-quest/book/)" >}}
{{% /aside %}}

[Side Quest](/side-quest/book/) is a short comic memoir by Cy Dequest, a
documentation engineer who builds Quirework instead of finishing *Full
Stop*, a natural history of punctuation. It is a preface and nine
chapters, about seven thousand words, with a spec of what it commits to
and a trail of how each commitment got there.

Everything in it is fiction, its author included. Cy Dequest, Cy's
manager, editor and colleagues, and the book Cy never finished do not
exist, and none of it is an account of how Quirework was made. An AI
played Cy, writing every one of Cy's messages from a brief, and drove an
AI companion that did the work. So the book demonstrates the method and
nothing more. Its provenance metrics, which measure what the human
contributed against what the machine did, measure one model against
another.

### The problem it is about

The book's premise is the problem Quirework exists for. The narrator's
unfinished book, a history of punctuation, credited one print shop with
a habit in chapter four and a rival shop with the same habit in chapter
nine. Fixing either chapter broke the other, and every fix needed a
footnote in the other chapter, until the footnotes cited footnotes.

{{< shot src="images/side-quest/sections/illustrations/hedda-screen.svg" alt="A claim, 'The dash is given its first printed rule', dated 1694, above five sentences from five chapters. Three of them say 1684 and are marked as disagreeing; two say 1694 and agree." width="620" caption="The book's one figure, from its chapter called The Demo: a claim, and the five sentences in five chapters that state it. Three disagree." >}}

### One commitment, followed through the book

One of the book's commitments says where the tool's name came from. The
invented author first told the story one way, then found a page that
proved it wrong, and corrected it. The spec changed, every document that
carries the commitment was listed, and an independent review checked the
change against all of them.

{{< diagram name="bind" narrow="bind-narrow" caption="The commitment and the four documents that carry it. The review found that the young readers' edition now defined a word its notes say to show. The circle marks that finding." >}}

{{< diagram name="trail" caption="The commitment's trail. The author first told the story with Otto, the AI assistant in the book, naming the tool. A page of names found later showed that the word was the author's own. The reversal sits on the trail, after what it reversed." >}}

### One spec, four formats, three editions

`quirework publish build` renders the book to HTML, PDF, EPUB and Word,
then renders each derived edition the same way. Every edition answers to
the same spec. Each edition's notes bind or omit, with a reason, every
commitment the book makes.

{{% gallery %}}

{{< shot src="images/side-quest/book-html.png" alt="Chapter 5 of Side Quest, The Name, in the HTML edition." width="440" caption="The book's fifth chapter, in the HTML edition." >}}

{{< shot src="images/side-quest/book-pdf.png" alt="Chapter 5 of Side Quest in the PDF, under a boxed chapter heading." width="440" caption="The same chapter in the PDF, set by Typst." >}}

{{< shot src="images/side-quest/poems-html.png" alt="Chapter 5 of Side Quest retold as rhyming verse." width="440" caption="The poems: one per chapter, in real metre and full rhyme, drafted on a different model from the other editions." >}}

{{< shot src="images/side-quest/young-html.png" alt="Chapter 5 of Side Quest in the young readers' edition." width="440" caption="The young readers' edition, for readers of about ten who read well and alone." >}}

{{< shot src="images/side-quest/memo-pdf.png" alt="The first page of the six-pager, a memo that opens with the heading How's the Book?" width="440" caption="The six-pager: the whole book as one memo to the narrator's manager." >}}

{{% /gallery %}}

The build checks what goes wrong silently. A figure must have the same
number in every format. The EPUB must pass the W3C checker, pinned by
version and checksum. A grayscale set of figures is made for e-ink,
where color turns to mud. The render refuses to go on in a substituted
typeface.

### Read it

Each edition is rendered from the book's source every time this site is
published, by the same `quirework publish build` an author runs. Every
page carries a notice that the book is fiction.

| Edition | In the browser | Download |
|---|---|---|
| The book | [Read the book](/side-quest/book/) | [PDF](/side-quest/book/Side-Quest.pdf), [EPUB](/side-quest/book/Side-Quest.epub), [Word](/side-quest/book/Side-Quest.docx) |
| The poems | [Read the poems](/side-quest/poems/) | [PDF](/side-quest/poems/Side-Quest-poems.pdf), [EPUB](/side-quest/poems/Side-Quest-poems.epub), [Word](/side-quest/poems/Side-Quest-poems.docx) |
| The young readers' edition | [Read the edition](/side-quest/young-readers/) | [PDF](/side-quest/young-readers/Side-Quest-young-readers.pdf), [EPUB](/side-quest/young-readers/Side-Quest-young-readers.epub), [Word](/side-quest/young-readers/Side-Quest-young-readers.docx) |
| The six-pager | [Read the memo](/side-quest/six-pager/memo.html) | [PDF](/side-quest/six-pager/Side-Quest-six-pager.pdf) |

### What to read in it

- The spec, `manuscript/sections/_spec.md`, and its trail,
  `provenance/spec-trail.md`.
- The records of the workshop, room, blind read, roundtable and review
  that made parts of it, under `provenance/`.
- The fifteen sessions it was written in, in `provenance/store`.
- Its own checks, in `sq_checks/`: no em dash anywhere, and two inks in
  every figure.

Run it from its directory, in
[the repository](repo:examples/side-quest/):

```
cd examples/side-quest
quirework spec notes --primer
quirework spec trail the-name
quirework spec notes bind --slug the-name
quirework publish build
```

## The Lighthouse Log

The Lighthouse Log is a small blog built by Hugo: the log of a fictional
lighthouse keeper. It has two posts in page bundles, one of them a
draft. Each post has a spec of its own, with its reader, its thesis and
its boundaries. A shared spec holds what both posts lean on, and a post
that binds another's claim is listed when that claim changes.

{{< listing name="files" caption="Every file in the site. Hugo reads `hugo.toml` and `content/`. Quirework reads the rest, and the specs and notes sit outside the tree Hugo renders, so no visitor is ever served one. The skill under `.claude/` is the entry point `sop skills` generated." >}}

{{< listing name="units" caption="Where each post stands: published, in draft, planned with a spec and no text yet, or an idea no post has taken up." >}}

Its voice is checked like its structure. A post with an em dash fails
`quirework dev check`. `quirework text check` reports a term from the
project's list where a reader would meet it, so the term is avoided,
glossed on first use, or kept where it belongs.

The site is in [the repository](repo:examples/demo-site/), with a README
that lists the commands a companion runs on it.

## Why they live in the repository

Each example is documentation and a test at once. The test suite runs
their gates on every run, so a change to Quirework that would break a
working project fails there, before it reaches anyone's book or site.

Writing them found the bugs they were written to find: a directory named
`docs`, a font stack read as a single family, a figure caption that
wrapped across two lines, a nested project collecting the sessions of
the repository around it. Every one passed a reading of the code and
failed the first project that was not the one the code grew up in.
