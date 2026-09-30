# Chapter and Verse Control

A short, fictional book about an engineer who cannot finish his book and
builds a tool instead. Every character is invented; the tool, Quirework,
is real, and this directory is a working example of a book built with
it.

## What's here

- `manuscript/`: the book itself. The chapters, the preface, the cover,
  the spec of what the book commits to, and the per-chapter marks and
  figures beside it.
- `procedures/`: the playbooks for writing a chapter and for producing a
  derived edition.
- `profiles/`, `questions/`: a reader persona and the questions a first
  read is checked against.
- `config/`: the model policy for derived editions, and the
  machine-habit policy `text tells` reads, adopted from Quirework's
  template.
- `variants/`: derived editions of the book. Today, one retelling for a
  reader who has not read it.
- `logs/`: the real session logs this book was written in, committed
  rather than kept private. `logs/README.md` says why.
- `cvc_checks/`: this book's own installable checks, the rules Quirework
  has no business knowing, like "no em-dash."

Read `manuscript/sections/preface.qmd` first. It says the rest better
than this file does.
