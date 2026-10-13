# Side Quest

A short comic memoir by Cy Dequest about building Quirework instead of
finishing *Full Stop*, a natural history of punctuation. It is the
example book for Quirework: one spec, a book in four formats, and three
derived editions bound to that spec.

Everything here is fiction. Cy Dequest does not exist, nor do Hedda,
Red, Otto or Em. The book is 100% AI-driven: an AI played Cy, writing
every one of Cy's messages from a brief, and drove an AI companion that
did the work in this directory. None of the Quirework maintainer's own
words are in it, apart from the note on its first page, which says what
the book is.

No person edited or reviewed its prose, either. The book exists to test
Quirework and to document it, so read it for the structure the tool
keeps: the spec, the trail, the editions and the records. It is not an
example of the writing Quirework helps an author make. The tool is built
for an author who judges every page, and this book had none.

## What to look at

- **The spec and its trail.** `manuscript/sections/_spec.md` states what
  the book commits to. `quirework spec trail the-name` follows one
  commitment through the turns that shaped it, including the session
  where Cy remembered the name's origin wrongly and the one where Cy
  corrected it.
- **One commitment in every artifact.**
  `quirework spec notes bind --slug the-name` lists the chapter and
  every edition that carries it.
- **The editions.** `variants/` holds the six-pager (one memo, drafted
  as one document), the young readers' edition and the book as poems.
  Each edition's `_raw/receipts.json` names the model that drafted each
  piece, and its sidecars say what it carries and what it leaves out.
- **How the parts were made.** `provenance/` keeps the records of the
  prose workshop that wrote chapter one, the writers room that argued
  the Otto chapter, the blind read that drove a revision, the roundtable
  that wrote the back cover, and the consistency review of the reversal.
- **The sessions.** `provenance/store` holds the fifteen sessions the
  book was written in, and `quirework provenance show ID` reads one.
- **The book's own checks.** `sq_checks/` refuses an em dash anywhere
  and holds every figure to two inks.

## Running it

From this directory, with Quirework installed and the book's extension
importable (`pip install -e sq_checks`, or `PYTHONPATH=sq_checks/src`):

```bash
quirework spec units --check
quirework spec notes --check
quirework spec trail --check
quirework dev check
quirework publish build        # needs Quarto; renders the editions too
```

## Two things that read differently here

Every session's human was an AI playing an author, so
`provenance metrics`, which measures what the human contributed against
what the model did, measures one model against another in this book. And
the paste audit reads Cy's stories, which quote other people's lines, as
relayed text; that is what quoted dialogue looks like to it.

The sessions record the paths of the machine they ran on. The book was
written outside this repository and imported with its history.
