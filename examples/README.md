# Worked examples

A project in here consumes Quirework; it is not part of it. It supplies
content, configuration, procedures and editorial rules of its own, and
nothing in it is imported by the tool.

## demo-book

`demo-book` is a complete small book: two chapters and a preface, a
register of what the book commits to, a metadata sidecar for every prose
file, one illustration with the style guide that governs it, a cover,
its own procedures and reader persona, an installed package carrying two
prose rules, a derived edition for a reader who has not read the book,
and the captured session it was written in.

It is fiction, and it is about a software engineer who builds Quirework
instead of finishing his book. Read it in five minutes, or run it:

```bash
cd examples/demo-book
quirework book notes --primer      # what a writing session reads first
quirework book chapters --check    # the files and the reading order agree
quirework book notes --check       # the marks still describe the prose
quirework book figures --check     # every embed resolves and converts to gray
quirework sop list                 # the procedures this book keeps
quirework book build               # all four editions, and they agree
```

That last one stages the book three times, renders HTML, a PDF through
Typst, an EPUB and a DOCX, validates the EPUB with the W3C checker, and
checks that the one figure carries the same number in all four.

Two of those gates belong to the book rather than to the tool, and they
need its own package, which nothing else here installs:

```bash
pip install -e examples/demo-book/cvc_checks
quirework dev check                # the book's own prose rules
```

## Why it is in this repository

It is documentation and a test at once. `tests/test_example_book.py`
runs these gates on every test run, so a change to Quirework that would
break a working project fails here rather than in somebody's book.

Writing it is also what found the bugs it was written to find. A
directory named `docs`, a font stack read as a single family, a figure
caption that wrapped across two lines, a nested project collecting the
sessions of the repository around it: every one of those passed a
reading of the code and failed the first book that was not the one the
code grew up in.
