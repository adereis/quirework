# Side Quest: project guide

This directory is a Quirework book project: a short comic memoir by Cy
Dequest about building Quirework instead of finishing another book. Read
this file before doing anything here. It is the one place any assistant
working on the book keeps what it needs to remember.

## Where things are remembered

Anything about the book worth remembering (the cast, their pronouns, the
house rules, decisions about shape or voice) goes in this project, where
any assistant Cy uses later can read it:

- people, pronouns and house rules: this file;
- what the book commits to: the spec, `manuscript/sections/_spec.md`;
- how one chapter should read, and its open work: that chapter's sidecar
  under `_marks/`;
- how a spec section got to where it is: the trail,
  `provenance/spec-trail.md`, written with `quirework spec trail add`.

Never keep any of it in an assistant's own memory or notes. If you learn
it in conversation, write it here in the same session.

## Operating facts

- Quirework is on `PATH` as `quirework`. Run it from this directory; it
  finds the project by `quirework.toml`.
- The tool's guides are in `~/projects/quirework/docs`. Start with
  `authoring.md`.
- Procedures live in `procedures/`; `quirework sop list` shows them and
  `quirework sop show NAME` reads one. Writing or revising prose follows
  `write-a-chapter`.
- Start every writing session with `quirework spec notes --primer`.
- `quirework publish build` renders HTML, PDF (Typst), EPUB and Word
  into `build/`, never into the source.
- Provenance is on, with sessions kept in the `side-quest` store.
  Sessions are captured by hand, by Cy, when each one ends, with
  `quirework provenance ingest claude`; there is no capture hook, by
  Cy's choice, so do not add one. Claude Code deletes a transcript 30
  days after its last change, so a session not captured by then is lost.
  Accepting or rejecting a pending session is Cy's decision too, made
  with `quirework provenance store status`, `show`, `accept` and
  `reject`.
- The store's backup and the EPUB validator are Cy's to set up at their
  own terminal. Leave both alone.
- `quirework dev check` runs the book's own suite, the `sq_checks`
  package in `sq_checks/`: Quirework's default suite plus the house rule
  against em dashes. Run it before every commit.
- `scratchpad/` and `private/` are ignored by git. Nothing in
  `scratchpad/` has authority.

## The book

- A preface and nine chapters, in three parts, about twelve thousand
  words in all: each chapter about thirteen hundred words, the preface
  shorter.
- Part one, The Book That Disagreed: How's the Book?, Two Shops, Index
  Cards. Part two, The Side Quest: Otto, The Name, Nine Hundred Lines.
  Part three, The Book That Got Finished: Em, The Demo, Full Stop. The
  book that got finished is this one; Full Stop stays unfinished.
- The title is Side Quest.
- The other book is Full Stop, a natural history of punctuation, which
  stalled eleven chapters in. That count is Full Stop's, not this
  book's: wherever the prose says "eleven chapters", its sidecar carries
  `<!-- @count(eleven chapters) Full Stop's, not this book's -->`.
- A chapter's file name is its permanent identifier. Retitling a chapter
  does not rename its file.

## Cast

Use only the pronouns recorded here. Where none is recorded, use the
person's name or they/them, and ask Cy.

Every name is changed. The two printer's shops in Full Stop are never
named at all.

- **Cy Dequest**: the author and narrator. Pronouns: they/them. A
  documentation engineer: Cy writes about software for a living, and is
  not a programmer.
- **Hedda Count**: Cy's manager. Kind, busy, ends every one-on-one with
  "how's the book?" Pronouns: she/her.
- **Red Penn**: Cy's editor at a small press. Waited three years for
  chapter twelve of Full Stop. Pronouns: he/him.
- **Otto Complete**: the AI assistant Cy used before Quirework existed.
  Eager, agreeable, finishes Cy's sentences. A mirror, not a villain.
  Pronouns: none, ever, and never "it". The book says "Otto" every time,
  which is a little absurd and exactly right.
- **Em Dash**: a colleague whose every message holds three em dashes. Cy
  suspects Em is a language model and has never asked, and never will in
  this book. Never on Cy's team, always on the same threads. The first
  person after Cy to use the tool, from a link Cy posted in the team
  channel on a Friday afternoon; read the README after using the tool,
  and was its second full reader, after Red. Not a villain. Pronouns:
  never stated. The book says "Em" every time.

## House rules for the prose

Add each rule here as Cy states it.

1. No em dashes, anywhere, ever. Not in the chapters, the spec, the
   sidecars or the Quarto file, and not by the back door either: three
   hyphens and `&mdash;` both render as one. Use a colon, a comma, or
   two sentences. `quirework dev check` refuses one, so nobody has to
   remember.
2. Dry and concrete.
3. Never explain a joke.
4. Short. If a paragraph can go, it goes. A chapter's word count is a
   ceiling, not a target: short is a rule, not a shortfall.
5. US spelling, everywhere: color, not colour. Cy's habits predate the
   style guide; the style guide wins.
