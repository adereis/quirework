# Quirework contributor guide

Quirework is a reusable writing and research companion. Books and sites
are consumers of this package and supply their own content,
configuration, personas, procedures, and editorial rules.

Quirework is the tool, never a book or a site. Do not import a
consumer's vocabulary, claims, or private material into this repository,
and do not carry a consumer's guidance into a change here. A session is
usually driven from a consumer's checkout, so this is the boundary an
assistant arriving with that project's instructions is most likely to
cross.

## Architecture

Keep one distribution and one Python namespace. Domain modules expose
services independently of command parsing. Provider adapters and the run
engine import neither the manuscript nor a publisher, and the manuscript
never imports a publisher, since a project may render with none of them.
A format is a `manuscript.formats.Format` and a backend a
`publish.backends.Backend`, each registered by name, so one is added as
a module and never by teaching a caller a file extension. Resolve
project settings at the operation boundary, never from the installed
package location. Preserve isolation across projects, worktrees,
threads, and processes.

Configuration describes project choices. Tested code implements provider
protocols, recovery, capability validation, and evidence integrity.
Require explicit configuration for private corpora and custom
extensions.

## Development

Python environments must use
`python3 -m venv --system-site-packages .venv`. The tree sits at the
formatter's fixed point, so running `ruff format` on a file you are
already editing changes nothing. If it does change something, that is a
neighbour's code and it does not belong in your commit: a formatting
change travels alone. The Markdown sits at the fixed point of
`quirework text wrap` the same way, and for the same reason.
`quirework dev check` verifies both, and skips only the formatter where
none is installed. A contributor's own scratch files go under `~/tmp`,
never `/tmp`; what the tool itself writes at run time follows the
configuration guide's placement rules. Keep dependency-heavy imports out
of help paths. Run relevant tests and update documentation with
implementation changes. Use only fictitious test inputs. Never copy book
content, private transcripts, credentials, or runtime homes into this
repository.

Keep the public API and command interface understandable to an author
using a different project, a book or a site, and verify independent
installation and project isolation. Preserve execution evidence through
refactors. An uncertain provider request must be reconciled before any
resubmission. A completed answer with a failed run condition remains
available with its limitations.

## The examples

`examples/side-quest` is a book that consumes this package: a manuscript
with a spec, marks and a trail, a figure, its own procedures and
extension, three derived editions bound to the spec, and the captured
sessions it was written in, with the records of the workshop, room,
blind read, roundtable and review that made parts of it.
`examples/demo-site` is a site that does: posts in page bundles, a spec
per post beside a shared one, marks outside the rendered tree, declared
wrap modes and a voice policy. Each is documentation and a fixture at
once, and `tests/test_example_side_quest.py` and
`tests/test_example_site.py` run an author's gates against them on every
test run.

Treat them as consumers. Each one's vocabulary, characters and editorial
rules stay inside its directory and never enter `src/`, and its content
is fiction written for the purpose. When a change to Quirework breaks
one, the example is usually right: the examples are the only projects
here that were not designed around the implementation.

## The site

`site/` is Quirework's website, a Hugo site deployed from this
repository. It describes the tool, so it is a consumer too: nothing
under `src/` reads it, and its copy, layouts and pictures stay inside
it. Every listing and screenshot on it is captured from the examples by
its own scripts, so a change that alters what a command prints, or how
an example renders, is followed by a refresh of the site's captures.
`site/AGENTS.md` holds its rules, including what it may say about a book
written with Quirework.

The site serves the sample book's renders, made fresh by
`.github/workflows/site.yml` at every deploy with the Quirework of the
same commit. So a push that changes the tool redeploys the site, and a
change that breaks the sample book's render fails that deploy. The
workflow belongs to the site, and its changes travel with the site's.

## Git

Stage only reviewed files. Keep each commit on one side of the
tool-and-consumer boundary: a change to Quirework under `src/`, `docs/`
or `tests/` never shares a commit with a change to an example under
`examples/` or to the site under `site/`, so the tool's own history can
be reviewed without reading around a consumer's content. The site's
commits stay apart from an example's for the same reason. Keep commit
subjects within 50 columns and prose body lines within 72 columns.
Non-trivial commits explain their motivation and important invariants.
End commits with a Co-Authored-By trailer identifying the model
established by the harness. Do not push unless the user authorizes it.
Preserve unrelated concurrent changes.
