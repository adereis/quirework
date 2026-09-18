# Room book project guide

This directory is a **book project**, not part of Quirework's source. It
is a consumer of the tool: it supplies the brief, the personas, the room
plan and the prose. Quirework supplies the room.

The repository guide two levels up governs the tool's own source under
`src/`. It does not govern this directory, and nothing invented here may
travel back into it.

Everything here is fiction and synthetic. The cast is invented. Copy
nothing from any real book project.

## What this example is for

`examples/demo-book` shows an author writing a book a chapter at a time
with a procedure. This directory shows the other shape: a writers room,
four seats, one board, twenty-two rounds, where the book is the thing
the room argued its way to. It is the reference implementation of
`quirework sop room`. It tells the same story as that book, because the
interesting comparison is between two ways of making the same one.

The room ran once, for real, against four models on three harnesses. The
board, the per-seat records and the trail in `room/` are that run, kept
rather than regenerated, so the procedure can be read as something that
happened and not only as something that is described.

## Operating facts

- Quirework is installed in the repository's virtual environment and is
  on `PATH` as `quirework`. Run it from this directory; it selects this
  project by the `quirework.toml` that lives here.
- The room lives in `room/`, inside the checkout, which is not where
  `prepare` puts one by default. Name it by a path (`./room`), never by
  the bare word `room`: a bare name resolves to this project's hidden
  rooms directory under the state root, which is a different room that
  does not exist.
- `room/seats/*.blind-state/` is each seat's private runner home. It
  holds live harness credentials and is ignored by
  `room/.gitignore`. Never commit it, and never copy one anywhere.
- Never edit anything outside this directory.

## House rules for the prose

These are the rules the room was given, and the rules the manuscript is
held to:

1. No em-dashes. Not one, anywhere in the prose.
2. The preface says plainly that the book is fiction and that Quirework
   is the one real thing in it.
3. Dry and concrete and funny. No corporate register, no
   throat-clearing.
4. Short. If a paragraph can go, it goes.
5. Nobody is a villain, Em Dash and Hal Lucinate included.
