This is the book's provenance corpus: the real Claude Code sessions this
book was written in, captured by `quirework provenance ingest` and never
edited afterward.

The configuration guide keeps a corpus private and out of git, under a
directory usually called `private/`. This one is committed on purpose,
and is not called `private` because it is not: an example of a
provenance corpus that contains no sessions teaches nothing, so this one
carries the actual sessions instead of standing in for them.

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
