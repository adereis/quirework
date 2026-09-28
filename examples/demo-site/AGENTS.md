# Demo site project guide

This directory is a **site project**, not part of Quirework's source. It
consumes the tool: it supplies the posts, the specs, the marks, the
procedure and the voice policy, and Quirework supplies the machinery.

The repository guide two levels up governs the tool's own source under
`src/`. It does not govern this directory, and nothing invented here may
travel back into it. Everything here is fiction.

## Operating facts

- Run `quirework` from this directory; it selects this project by the
  `quirework.toml` beside this file.
- Posts are Hugo page bundles under `content/posts/`. Their specs and
  marks live under `specs/`, outside the tree Hugo renders.
- The one procedure is `quirework sop show write-a-post`.

## House rules for the prose

- One sentence per line in posts. `text wrap` enforces it.
- No em-dashes. `text check` fails on one.
- Plain words. The readers are not sailors.

## How work happens

The keeper says what a post should do. The companion resolves the
procedure (`quirework sop show write-a-post`), reads the post's primer,
drafts or revises, runs the procedure's checks, and brings back the
change with anything left for the keeper to rule on. The spec states
what a post holds; a change to it is proposed, never made in passing.
