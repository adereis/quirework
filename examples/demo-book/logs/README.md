This is the book's provenance corpus: the real Claude Code sessions this
book was written in, captured by `quirework provenance ingest`, accepted
with `quirework provenance accept`, and never edited afterward.

The directory is the corpus's ledger. `accepted.txt` lists each accepted
session's file with its SHA-256, and `index.json` and `INDEX.md` are the
catalog built from them. The sessions themselves sit in the store, in
`store/`, each in a version directory named by a hash of what it holds.

The configuration guide keeps a project's store outside its repository,
so the sessions never weigh on the work's history. This book keeps its
store inside, in `logs/store/`, on purpose, and the directory is not
called `private` because it is not: an example of a provenance corpus
that contains no sessions teaches nothing, so this one carries the
actual sessions instead of standing in for them.

`project-trees.txt` names the directory those sessions ran in. Capture
wrote it while that directory existed, and the catalog measures every
recorded edit from it, which is how it tells a manuscript edit from a
tooling edit on a machine where that path means nothing.

Run `quirework provenance catalog` first; it builds the index the other
commands read. `quirework provenance search` finds a human turn by its
words, and `quirework provenance show` reads one session as clean prose.
`quirework provenance metrics` measures who contributed what, which is
the same question this book's own plot keeps asking, put to the actual
log instead of to a character.
