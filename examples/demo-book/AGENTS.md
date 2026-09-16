# Demo book project guide

This directory is a **book project**, not part of Quirework's source. It
is a consumer of the tool: it supplies the content, the configuration,
the marks, the procedures and the editorial rules, and Quirework
supplies the machinery.

The repository guide two levels up governs the tool's own source under
`src/`. It does not govern this directory, and nothing invented here may
travel back into it.

Everything here is fiction and synthetic. Invent freely. Copy nothing
from any real book project.

## Operating facts

- Quirework is installed in the repository's virtual environment and is
  on `PATH` as `quirework`. Run it from this directory; it selects this
  project by the `quirework.toml` that lives here.
- The tool's own documentation is in `../../docs/`. Start with
  `../../docs/authoring.md`.
- Never edit anything outside this directory.
- Keep the book small. It exists so that someone can read the whole
  project in a few minutes.

## House rules for the prose

1. No em-dashes. Not one, anywhere in the prose. It is a plot point. Use
   a colon where the second part names the first, a comma for a looser
   join, or two sentences.
2. The subtitle and the preface must say plainly that this is fiction.
3. Dry and concrete and funny. No corporate register, no
   throat-clearing.
4. Short. If a paragraph can go, it goes.
