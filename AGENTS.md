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
  words in all.
- The title is Side Quest.
- The other book is Full Stop, a natural history of punctuation, which
  stalled eleven chapters in. That count is Full Stop's, not this
  book's: wherever the prose says "eleven chapters", its sidecar carries
  `<!-- @count(eleven chapters) Full Stop's, not this book's -->`.
- No chapter files exist yet beyond the preface. Each chapter's file is
  named for what it is about once the story gives it a name, because the
  file name is the chapter's permanent identifier.

## Cast

Use only the pronouns recorded here. Where none is recorded, use the
person's name or they/them, and ask Cy.

- **Cy Dequest**: the author and narrator. Pronouns: they/them.

## House rules for the prose

Add each rule here as Cy states it.

1. No em dashes, anywhere, ever. Not in the chapters, the spec, the
   sidecars or the Quarto file, and not by the back door either: three
   hyphens and `&mdash;` both render as one. Use a colon, a comma, or
   two sentences. `quirework dev check` refuses one, so nobody has to
   remember.
