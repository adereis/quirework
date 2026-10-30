# Project configuration

The [authoring guide](authoring.md) walks a new book through these
settings in the order a project adopts them. This page is the reference.

A project supplies a versioned TOML file. Paths are relative to the file
that declares them. Explicit absolute paths and home expansion are also
supported.

```toml
schema = 2

[project]
name = "A Small Notebook"
scratchpad = "scratchpad"
private = "private"

[manuscript]
source = "manuscript"
quarto = "manuscript/_quarto.yml"
chapters = "manuscript/sections"
companion_dirs = ["companion"]
noncanonical_roots = ["scratchpad"]

[publish]
figures = "manuscript/sections/illustrations"
build = "build"
variants = "variants"

[sops]
root = "procedures"
catalog = "procedures/catalog.json"
personas = "profiles"
instruments = "questions"
panels = "panels"
skill_prefix = "notebook-"
```

`[manuscript]` describes what is written: where the source is, how its
units are found, and the spec, marks and trail that bind them.
`[publish]` describes how that source becomes something a reader opens.
A project that renders elsewhere declares no `[publish]` section, and
its spec commands work the same.

`manuscript.quarto` names the Quarto project file. The manuscript can
also name `manuscript.spec`, `manuscript.references` and
`manuscript.review_notes`, the book-wide annotations a red-pen review
copy carries, when the project uses those features. `manuscript.spec`
takes one path or an ordered list of paths, read as one register whose
anchors are unique across every file; each listed file must exist, and
an empty list or a file listed twice is refused.
`manuscript.spec_public` names the ones a reader of the published work
can see, each also listed in `manuscript.spec`; left unset, every spec
file is published. `manuscript.references_public`, true or false, says
whether the references register is published beside them; it requires
`manuscript.references`, and left unset the references register counts
as unpublished. The [authoring guide](authoring.md#2-the-spec) says what
the boundary refuses. `publish.typst_template` names the Typst file that
sets the printed page. Quirework reads it and never writes it. It takes
from it the faces `publish fonts` checks and the text block
`figures --check` measures type against, and it names the file when
figure numbers disagree across editions, since its numbering rule is one
place they are set. `publish.figure_fonts` lists font files that every
staged figure carries as subsets, so a figure shown as an image is
lettered in the book's face on a device that lacks it; the
[authoring guide](authoring.md#4-figures) explains it.
`publish.figure_type_floor_pt` sets the printed size, in points, under
which `figures --check` warns about a figure's type; it defaults to 6.
Annotation bindings resolve against the configured semantic register; a
book need not adopt another project's claims or vocabulary.

`manuscript.spec_trail` names the spec's trail sidecar, which points
each spec section at the conversations that shaped it. It requires a
register, `manuscript.spec` or `manuscript.unit_spec`, and the file need
not exist yet: the first `spec trail add` creates it, readable by its
owner only. Put it in the provenance ledger with the other private
material, since it points into the private corpus by session id and
keeps working wherever the store lives. Leaving it unset turns the trail
off: no command reads or writes one, and the primer shows no counts. The
[authoring guide](authoring.md#the-specs-trail) says what a breadcrumb
holds and when one is added.

`manuscript.spec_trail_leads` names the directory of the backfill
ledger: what cheap readers flagged in each session, and what the ruling
reader rejected. It requires `manuscript.spec_trail`, and is created on
first use. Keep it with the private material, since its reasons
paraphrase the corpus. The packets a backfill plan writes hold corpus
text, so they go outside the checkout or under `build/`, like run
records.

`project.scratchpad` and `project.private` document the two
non-publishing spaces. Session capture also skips any session whose
recorded working directory is inside `project.scratchpad`, so workshop
seats and experiments run there are never swept into the corpus.
`manuscript.noncanonical_roots` tells checks which working paths have no
authority. `manuscript.companion_dirs` names reader-facing documents
outside the canon. `publish.variants` names the root for derived
editions, and `publish build` renders each one composed there into
`<publish.build>/variants/KIND/book/_site`.

`sops.root` names the procedure directory, so a project can call it
`procedures` or anything else; discovery and reading both follow the
setting. `sops.operator_guide` names the project's own guide in every
generated skill, for a project whose guide is not `AGENTS.md`. Setting
it to an empty string leaves the generated skills pointing only at the
procedure itself.

## Manuscript formats

`manuscript.format` says how the written work is laid out, and so how
its units are found. A unit is a chapter of a book or a post on a site.
Its slug is its identity: the name of its marks sidecar, what an
`@unit(slug)` reference resolves, and the key its bindings are kept
under.

`quarto`, the default, reads the chapters a Quarto book project lists
under `book.chapters` in `manuscript.quarto`, in that order. A chapter's
slug is its file stem, its marks sit beside it as `_marks/<stem>.md`,
and a printed number is derived from its position.

`pages` reads the Markdown pages of a site, such as a Hugo blog:

```toml
[manuscript]
format = "pages"
source = "content"
pages = "posts/*/index.md"
marks = "specs/{slug}/marks.md"
unit_name = "post"
```

`pages` is a glob under `manuscript.source`. A page named `index.md` is
a page bundle and takes its slug from its directory, and any other page
from its file stem. A file starting with `_`, such as a section's
`_index.md` list page, is never a unit. Each page opens with TOML
frontmatter between `+++` lines or YAML between `---` lines. Its `date`
orders it, its `title` names it, and its `draft` flag is shown by
`spec units`. YAML is read for top-level scalars only, which is all the
fields Quirework reads need.

A site whose units are not all posts names collections instead of one
glob. An About page is then a unit beside the posts, with a spec and
marks of its own:

```toml
[manuscript.collections.posts]
pages = "posts/*/index.md"

[manuscript.collections.pages]
pages = "*.md"
order = "title"
```

Collections are read in the order declared, and a page two globs match
belongs to the first. `order` is `date`, the default, which lists the
oldest first and the undated last; `weight`, the frontmatter weight,
then the title; `title`; or `path`. `spec units` lists each collection
under its name. A project declares `pages` or `collections`, not both.

`marks` is where a page's sidecar lives, as a path pattern containing
`{slug}`, relative to the project root. Put it outside the source. A
site generator reads everything inside its source, and marks are
metadata no reader should be served. Leaving `marks` unset keeps the
site's spec commands working without sidecars. `unit_name` is the noun
messages use for a unit, when "page" is not what the project calls its
posts.

A format Quirework does not ship is an installed extension. Name its
factory as `module:function`, which returns a
`quirework.manuscript.formats.Format`:

```toml
[manuscript]
format = "kettle_wiki:wiki"
source = "entries"
```

The class answers for its layout: the units in reading order, which
files are prose, where a unit's marks live, and which unit a sidecar
belongs to. The spec, marks, quotations and trail read the work only
through it, so they need no change for a new layout.

### Where each unit stands

`spec units` lists more than the pages. A unit specified before it is
drafted is listed as planned. With `manuscript.ideas`, a glob relative
to the project root such as `"ideas/*/brief.md"`, each idea no unit has
taken up is listed too. An idea is named like a unit, by its directory
when the glob names a fixed file. It counts as taken up once a unit or a
planned spec carries its name, or a mark or a spec names where it lives,
which is what a post's `@genesis` does when it points at the brief it
grew from. An idea named by its directory lives there, so
`ideas/the-tide-clock` names it as well as the brief's own path does; an
idea that is one file among others is named only by that file.

A published page owes its readers more than a draft does, so
`spec units --check` adds two advisories for published pages. An open
`@todo` on one is waiting on something a reader cannot see. A page whose
own spec was revised after the page was last dated says something it no
longer holds, until it is revised and its revision field records it. The
field is `lastmod` unless `manuscript.revision_field` names another,
such as `last_modified_at`. The second advisory reads the history, so it
needs git, and it says so when there is none. Writing a spec for a post
already published is not revising it: only a change after the spec
exists counts, and a spec not yet tracked is never named. A book's
chapters have no publication state of their own, so neither advisory
applies to them.

### A spec per unit

A book argues one thing, so it keeps one spec. A site argues one thing
per post, so each post can state its own commitments in its own file:

```toml
[manuscript]
spec = "specs/_site.md"
unit_spec = "specs/{slug}/spec.md"
```

`unit_spec` is a path pattern with `{slug}`, relative to the project
root. `spec` becomes the shared file, or files, holding what several
units lean on, and either setting may stand alone. Together they are one
register. The shared files are read first, in the order listed, then
each unit's own in reading order, then the specs of units not written
yet, since a post is often specified before it is drafted.
`spec notes --check` reports those as an advisory, because a mistyped
slug looks the same.

A section of the shared spec is named by its anchor, and a section of a
unit's own spec by the unit and its anchor. Two posts can each write
`## The turn is the message {#thesis}`, and the register holds
`why-the-lamp-turns/thesis` and `what-the-fog-signal-is-for/thesis`. An
anchor is unique within its file, a shared anchor across every shared
file, and no anchor contains `/`.

A unit binds every section of its own spec without saying so. It is the
post those commitments describe. A `@bind` in its sidecar still says
which heading carries a commitment, and there the bare anchor is enough:
`@bind(thesis)` in a post's own marks means that post's thesis.
Everywhere else a unit's section is named in full, which is how another
post comes to share the claim: `@bind(why-the-lamp-turns/thesis)`. A
post's anchor may not repeat a shared one, since a bare name in its
marks could then mean either. `spec notes --primer -c SLUG` lists only
what that unit touches: its own spec whole, and the shared sections it
binds.

One trail serves the whole register, because breadcrumbs are kept by a
section's full name. `spec trail why-the-lamp-turns/thesis` reads one,
and `spec trail suggest` follows the history of whichever file holds the
section.

### Sources

`manuscript.sources` declares a register of what the work stands on, the
research half of the spec. It is a file, where each level-2 section
`## Title {#slug}` is one source and its body says what the source is:

```toml
[manuscript]
sources = "research/sources.md"
source_copies = "research/copies/{slug}.txt"
```

Or it is a glob with one file per source, such as `"research/*.md"`,
each named by its file stem, or by its directory when the glob names a
fixed file (`"research/*/notes.md"`). A file's title is its frontmatter
`title` or its first heading. A `README.md` and a `_`-prefixed file are
not sources.

A mark cites a source as `<!-- @source(slug) what it supports -->`.
`spec notes --check` refuses a slug the register does not hold, and two
sources with one slug. A `@source` with no slug stays free prose, since
a passing reference need not be registered. `spec sources` lists each
source with the units that cite it; `--slug S` and `-c UNIT` narrow the
list, and `--check` adds two advisories: a source no unit cites, and a
quotation that is not in the source.

`source_copies` is a path pattern with `{slug}` naming a plain-text
capture of each source as it was read. A quoted span of five words or
more in a mark citing that source is checked against it, with case,
whitespace and closing punctuation ignored. A source without a capture
is not checked, and `--check` says how many quotations that left.

## Publishing backends

`publish.backend` names what renders the manuscript. `quarto`, the
default, stages a book and renders the formats its Quarto file lists,
any of HTML, a Typst PDF, EPUB and DOCX, and every other `publish`
command is built on that staging: figures, fonts, red-pen copies, reader
packets, derived editions and chapter comparisons.

`hugo` builds a site with its own Hugo configuration:

```toml
[publish]
backend = "hugo"
build = "build"
# site = "."   # where hugo.toml lives; the project root by default
```

`publish build` then runs `hugo build` into `<publish.build>/site`, so a
build Quirework runs never overwrites the directory the site deploys
from. `--drafts` includes draft pages, and any flag the command does not
know goes to Hugo. That is the whole backend. A site's figures, theme
and deploy stay the site's own, and a Quarto-only command on a Hugo
project is refused by name rather than attempted. A project that renders
some other way leaves `[publish]` out and simply runs no `publish`
command.

A publisher Quirework does not ship is an installed extension:
`publish.backend = "module:function"`, where the function returns a
`quirework.publish.backends.Backend`. It validates its own settings,
builds a target when `publish build` runs, and says where that target's
output lands, which is what another target copies from. The commands
built on Quarto staging stay refused, as they are on a Hugo site.

### Several targets

A project may publish more than one thing: a book in its editions, and
the site that serves them. Each is a target with its own table, and
`[publish]` keeps what they share:

```toml
[publish]
build = "build"

[publish.targets.book]
backend = "quarto"

[publish.targets.site]
backend = "hugo"
site = "website"
needs = ["book"]
copy = [
  ["book:.", "book/"],
  ["book:*.pdf", "downloads/the-book.pdf"],
  ["book:*.epub", "downloads/the-book.epub"],
]
```

A target reads its own table laid over `[publish]`, so a key it declares
replaces the shared one, and a relative path in it resolves from the
file that declares it. `publish.backend` is then not set: each target
names its own. A project without `targets` has one, named for its
backend's product: `book` for Quarto, `site` for Hugo.

`needs` names the targets built first. `copy` takes their output into
this target's once it has built, one `["TARGET:GLOB", "DESTINATION"]`
row at a time. The glob is relative to that target's output, and `.` is
all of it. A destination ending in `/` is a directory that receives
every match; any other destination is one file, and the glob must then
match exactly one. A Hugo target's output is `<publish.build>/<target>`
and the Quarto target's is `<publish.build>/book/_site`. Nothing is
copied into a site's source, so a site that deploys the build tree
deploys what the copy put there.

```bash
quirework publish build                  # every target, each after its needs
quirework publish build site             # the site, after the book
quirework publish build site --no-needs  # the site, from the book's last build
quirework publish build book --to html   # arguments follow the target they are for
```

A project has at most one Quarto target, since the Quarto commands
(figures, fonts, red-pen copies, packets, editions, comparisons) all
work on its one staging. They run as that target sees the project, and
are refused by name where no target is Quarto.

## Configuration layers

Quirework loads `quirework.toml`, then the optional
`quirework.local.toml` beside it. Keep the local file out of Git. It is
appropriate for private corpus paths, account identities, and existing
run-storage locations. Store credentials in the provider's native
credential mechanism, not in these files.

One file belongs to you rather than to a project:
`$XDG_CONFIG_HOME/quirework/config.toml`, normally
`~/.config/quirework/config.toml`. It holds what every project on the
machine shares: `provenance.home` (see [Provenance](#provenance)),
`copies.log`, the file that numbers the proofs `publish copy` makes for
one person and names whom each was for, `copies.author`, the name your
own copies are made for, and `kindle.targets`, `kindle.reader_targets`
and `kindle.inbox`, the reading devices `publish kindle` delivers a copy
to and the folder the Kindle app's notebooks are shared into. The log
names people, and so do `copies.author` and `kindle.reader_targets`, so
`publish copy` refuses a log inside the project, where a commit could
carry it. A key it does not read is refused, as in a project's file.

`kindle.targets` is a list of rules, each choosing devices as the Kindle
store lists them, by kind or by name:

```toml
[kindle]
targets = [{ kind = "Kindle for Android" }, { kind = "Kindle Paperwhite" }]
inbox = "gdrive:Kindle notebooks"
```

A rule takes the most recently registered device it matches, so a new
phone replaces the old one without an edit, unless it says
`pick = "all"`. A rule that matches no device stops the plan rather than
skip a device you expect the copy on.

Two readers may share one library and one device, each reading a copy
made for them. `kindle.reader_targets` gives a reader devices of their
own, by the name their copies are made for, matched whatever its case or
spacing; a copy for anyone it does not name goes where `kindle.targets`
says:

```toml
[copies]
author = "Me"

[kindle.reader_targets]
Ada = [{ kind = "Kindle Paperwhite" }]
```

A new copy replaces only an earlier copy made for the same reader, and
another reader's copy stays where it is. `copies.author` says whose
notes are yours: those made on a copy for that name are archived with
your red-pen rounds, and those made on anyone else's are a reader's,
kept where `publish.reader_notes` says. Without it,
`publish kindle notes` refuses rather than guess, since a wrong guess
files someone else's words as yours.

`kindle.inbox` is an rclone path: a remote `rclone config` already
knows, and a folder in it. The app names an export after the book, so
every export of one copy has the same name, and Google Drive keeps any
number of files with one name in a folder. `publish kindle inbox`
therefore fetches each by its own id. On a remote that gives no ids, two
files of one name are refused rather than one silently taken for both.

An explicit `--project` or `--config` selects a directory or
configuration file. `QUIREWORK_PROJECT` supplies a project selection for
subprocesses. A library caller can construct a `Project` and invoke
commands or use its operation context without changing the process
working directory.

```python
from quirework.project import Project, project_scope
from quirework.manuscript.units import registry

project = Project.load("/path/to/a/book")
with project_scope(project):
    chapters = registry()
```

`quirework config show` prints effective settings and their origins.
`quirework config check` validates the configured components. An absent
optional corpus is different from an enabled corpus whose directory is
missing.

A setting left out takes its default and says nothing, and so would a
misspelled one, read as absent. So `config check` fails on any declared
key nothing reads, in `quirework.toml`, in `quirework.local.toml` or in
a policy file the project names, and names each with its file. An
extension may read keys Quirework does not, a setting of its own or a
key of its own in a policy file it shares with Quirework. The project
lists those under `[extensions]`, and the check counts them as its own:

```toml
[extensions]
# Settings this project's own code reads, dotted, `*` for any name.
settings = ["publish.cover_palette"]

[extensions.policy_keys]
# Keys of its own in a policy file Quirework also reads.
"text.terminology" = ["house_terms.*"]
```

Only a policy file Quirework reads takes a list, since a file nothing
here reads is never checked. Any other key under `[extensions]` fails
like a misspelling.

`quirework config keys` sets every key Quirework reads against the
project: each one declared or undeclared, the project's own keys its
`[extensions]` lists name, and each declared key nothing reads, which is
usually a misspelling. It reads into the policy files the project names
as well, where a key never appears in `quirework.toml`. A `*` stands for
a name the project chooses, as in `kinds.*.shape`. `--undeclared` lists
only what is left out and what is unknown, and `--json` gives the same
report to a script. Run it after moving to a new release to see what the
release added.

## Provenance

Enable a corpus explicitly, and name the store that keeps its sessions:

```toml
[provenance]
enabled = true
root = "private/provenance"
store = "my-book"
```

A corpus has two halves. The *store* holds the captured sessions. They
are large and never change once settled, so they live outside the
repository: the work's history does not carry them, and a search or a
cleanup across the repository never reads them. The *ledger* is the
directory `provenance.root` names, committed with the work. It holds the
decisions about the sessions: which were accepted, which were removed
and why, which are about the apparatus, and the catalog built from them.

`provenance.store` names the store, and Quirework looks for it under the
*provenance home*. The home is `$QUIREWORK_PROVENANCE_HOME` when that
variable is set. Otherwise it is `provenance.home` in your per-user
configuration, and without that it is
`$XDG_DATA_HOME/quirework/provenance`, which is normally
`~/.local/share/quirework/provenance`. The name is committed with the
project, so every worktree and every clone find the same store. Nothing
has to be set per checkout. `provenance.store_dir` names a directory
instead, for a store kept somewhere specific; it may sit inside the
repository, as the example book's does. Declare one of the two.

To keep every project's store on a disk that is backed up or synced,
name the home in the per-user file:

```toml
# ~/.config/quirework/config.toml
[provenance]
home = "~/sessions"
```

Prefer the file to the variable. A capture hook is started by the
harness, not by your shell, and it sees an exported variable only when
the harness itself was started from a shell that had already exported
it. A harness opened from a desktop launcher, or started before the
variable reached your profile, runs its hooks without it, and they look
for the store in the default home instead. A file reaches every process,
however it was started. The variable stays useful for an override, such
as a scratch home for one command.

Nothing copies the store off the machine until you describe a backup,
and Claude Code deletes its own transcripts after 30 days by default.
After that the store is the only copy.
[Backing up the store](#backing-up-the-store) shows how to keep another.
`quirework config check` and `quirework provenance store status` name
where the store is, which setting chose its home, whether a repository
holds it, and how it is backed up.

`quirework provenance store init` creates the store and records its
identity in the ledger's `accepted.txt`. A ledger that already names an
identity keeps it. On a second machine or in a fresh clone, init creates
an empty store under that identity, and `store verify` lists the
accepted files it lacks until you copy them in. A store another project
created under the same name is refused, so two projects never read each
other's sessions.

That same identity is why init is the wrong answer when a store goes
missing on the machine that made it. If the ledger lists accepted files
and no store is found, the store usually still exists, and Quirework was
looking under a different home, often from a hook that never saw the
variable your shell exports. Init there would create a second, empty
store under the same identity, and captures from that process would land
in it unnoticed. So the error names the home it looked in and the
setting that chose it, and it tells you to point the home at the store
rather than to run init. It suggests init only when the ledger lists
nothing yet. Init itself, when it creates a store for a ledger that
already lists files, names the home too and says what to do if that
store belongs elsewhere.

### Capturing Claude Code sessions

A `SessionEnd` hook in the project's `.claude/settings.json` captures
each Claude Code session as it closes:

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "quirework provenance ingest claude --hook",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

Keep the `timeout`. Without one, Claude Code gives a `SessionEnd` hook
1.5 seconds and then ends it, and reports "Hook cancelled" as it exits.
The capture often needs longer, because it captures again every session
of the project that grew since its last capture, so one closing session
pays for every other session still open. With the timeout, Claude Code
waits as long as the capture takes, up to 60 seconds whatever the
setting says. A hook ended anyway has captured the closing session
first, since its payload names it, and leaves no partial file behind.
What it did not reach waits for the next session to close.

### Accepting sessions

Capture writes into the store's pending area and never decides what
counts. `quirework provenance store status` lists what is pending. Read
each pending session, then accept it or curate it out:

```bash
quirework provenance store status
quirework provenance show 3f2a9c1e
quirework provenance accept 3f2a9c1e
quirework provenance reject 77aa01b2 --reason "a stub: one /model command"
```

Accepting copies the capture into the store as a version that is never
rewritten. It adds one line per file to `accepted.txt`: the file's
SHA-256 and its path in the store. Commit that file with the catalog. A
session modified in the last two hours may still be running, so `accept`
refuses it unless `--live` says to take what was captured so far.
`accept --all` skips such sessions and names them.

`store status` also lists the Claude Code sessions that no capture holds
as they stand: one never captured, or one that grew after its last
capture. It finds them by running the capture's own selection without
writing anything. A session there that is still being written is marked
as possibly running, and its hook captures it when it ends. One that has
stopped changing was missed, usually by a capture hook that failed as
its session closed, where nobody saw the error.
`quirework provenance ingest claude` captures it. Do that within 30 days
of its last change, because Claude Code deletes the transcript then by
default. Codex and Antigravity are captured by hand, and their sweeps
report what they captured, so status does not list theirs.

A session that grows after it was accepted is captured again as pending.
Accepting it then adds a new version beside the old one. A branch whose
ledger still lists the old version keeps reading exactly the bytes it
accepted, so accepting on one branch never breaks the checks of another.

A session also counts as grown when it gained sub-agents its capture
does not hold, which is how every session captured before sub-agents
were captured is taken again. Status says so: such a session is listed
as having gained sub-agents, and once captured it is pending as grown by
its sub-agents only, with its dialogue unchanged. Nothing a reader of
the dialogue sees has changed, so a review can keep the verdict it
already gave and accept it without reading it again.

The catalog, search, metrics and the cold-read read accepted sessions
only. `show` and the spec trail also read pending captures, so a session
can be read before it is accepted; `show` says so in its source line. A
session resumed under a new id is captured again under that id, and
accepting both captures is safe: the metrics and the cold-read count
their shared conversation once (see "Reading the corpus").
`coldread triage` sorts the pending captures by whether they edited the
work.

`reject` deletes a pending capture, or drops an accepted session from
this ledger, and lists the session in the ledger's
`removed-sessions.txt` with the reason. Capture never brings back a
session on that list. The ledger's `making-sessions.txt` lists sessions
about the apparatus rather than the work. Both lists hold one session
per line, as `<kind> <session id>  # reason`, where the kind is `cc`,
`codex`, `agy` or `web`.

`quirework provenance store verify` hashes every accepted file and names
any that is missing or no longer matches `accepted.txt`. A command that
needs a missing file refuses to run rather than read a smaller corpus.

### Backing up the store

Claude Code deletes a transcript 30 days after it last changed, and from
then on the store holds the only copy of the session. A store therefore
describes its own backup, in a `backup.toml` beside its `store.toml`,
and `quirework provenance store backup` carries it out:

```toml
# ~/.local/share/quirework/provenance/my-book/backup.toml
[targets.drive]
driver = "rclone"
destination = "gdrive:quirework-provenance/my-book"
```

The file lives in the store rather than in `quirework.toml` because what
it names belongs to you and to this machine, such as an rclone remote or
a mounted disk. The project file is committed and shared, and a clone
elsewhere would name a remote that machine does not have. Keeping the
file in the store also lets one command back up every store under the
provenance home without opening a project.
`quirework config template backup` prints an annotated file to start
from.

Each `[targets.<name>]` table is one destination, and a store may name
several, such as a cloud folder and a NAS. The name is yours. It labels
the target in status, and `--target` selects it. Four drivers are built
in:

| driver | settings | what it does |
|---|---|---|
| `rclone` | `destination = "remote:path"` | Copies to any remote `rclone config` has set up: Google Drive, SFTP, S3, or a `crypt` remote that encrypts before upload |
| `directory` | `destination = "/absolute/path"` | Copies in Python to a directory that already exists, such as a mounted share |
| `git` | `remote` and `branch`, both optional | Makes the store its own repository and each run a commit, pushed to `remote` when one is named |
| `command` | `command = ["script", "arg"]` | Runs a script kept at the store's top level |

`driver = "module:function"` selects an installed extension instead, as
`publish.backend` does. The function receives the target's name, its
table and the store's directory. It returns a
`quirework.provenance.backup.Driver`, and a subclass of `Transfer`
inherits the order described below.

#### What a backup may change

For `rclone` and `directory`, Quirework decides what may change at the
destination, so both keep the same rules:

- An accepted file is never overwritten or deleted there. A version
  never changes in the store, so a copy that differs means one side is
  damaged. The run stops and names the file rather than choosing a side.
  rclone compares checksums, and the directory driver compares sizes.
- A pending file is deleted there only once the version that replaced it
  has arrived.
- The first run writes the store's `store.toml` at the destination. A
  later run refuses a destination holding another store's identity, or
  holding files and no identity at all. A path copied from one store's
  file into another's therefore fails rather than mixing two corpora.
- The directory driver never creates its destination's top directory. A
  share that failed to mount leaves an empty mount point behind, and
  filling it would put the backup on the local disk. Create the
  directory once by hand.
- Names starting with a dot are never copied. They are the half-built
  directory of an accept in progress, a capture still being written, and
  a git backup's `.git`.

The order of a run is what keeps the second rule true while you work. A
run copies `pending/`, then `accepted/`, and then takes the lock that
`accept` and `reject` take. Holding it, the run uploads any version
accepted since it copied `accepted/`. Only then does it delete the
destination's pending files that the store no longer has.

Say you accept a session while a run is uploading. The run had already
copied the session's pending capture, and it missed the new version
because it listed `accepted/` before the version existed. Under the lock
it sees the version and uploads it, and only then removes the pending
copy. The session is at the destination throughout. A run that fails
partway has deleted nothing.

Pending files are deleted at all because `reject` deletes a capture on
purpose, and a backup should not keep a session you curated out. The one
loss this does not prevent is a pending file deleted by hand, which the
next run deletes at the destination too. Its original stays where Claude
Code keeps it until Claude Code prunes it, and the `git` driver keeps
such a file in its history.

#### Git and command targets

The `git` driver commits the store as it stands, under the store's lock.
It pushes `branch`, which is `main` unless set, to `remote` when one is
named. Cloning that repository restores the store. It costs disk: the
repository's objects roughly double the store's size, and a pending
capture that grows is committed whole again on every run. A store that
already sits inside another repository, as a `store_dir` inside a
project can, is refused, since that repository versions it already.
Commits carry your git identity, so a machine with none configured fails
the run with git's own message.

A `command` target runs its script from the store's directory with
`QUIREWORK_STORE`, `QUIREWORK_STORE_ID` and `QUIREWORK_BACKUP_TARGET`
set. Its exit status is the run's outcome. Quirework cannot see what a
script copies, so the rules above are the script's to keep, and it runs
without the store's lock. Because it runs unattended, the script must be
a regular file that you own, that is executable, and that nobody else
can write.

#### Running and checking a backup

```bash
quirework provenance store backup --check   # reach each destination, copy nothing
quirework provenance store backup           # every target of this project's store
quirework provenance store backup --target drive
quirework provenance store backup --all     # every store under the provenance home
```

Each run leaves a record and a log under
`$XDG_STATE_HOME/quirework/backup/<store id>/`. They are per machine,
because a backup is something one machine did. They are keyed by the
store's identity, so two stores never share one.
`quirework provenance store status` reads them. It shows each target's
last success, any failure since, and how many accepted versions and
pending sessions changed after that success. `quirework config check`
reads `backup.toml` and validates every target without reaching any. A
store with no `backup.toml` is reported as having no backup, unless a
repository around it already versions it.

The first run to a cloud remote can be slow, because the remote creates
files one request at a time. Google Drive takes a few files a second, so
a store of five hundred sessions takes about half an hour the first
time. Later runs copy only what changed, and rclone lists the whole
destination in a few batched requests, so a run with nothing new takes
seconds.

A session reaches the backup when it is captured, as a pending file, so
nothing needs to run after `accept`. What matters is how often a run
happens. `--all` needs no project, which suits a systemd user timer:

```ini
# ~/.config/systemd/user/quirework-backup.service
[Unit]
Description=Back up the Quirework provenance stores

[Service]
Type=oneshot
ExecStart=/path/to/quirework provenance store backup --all
```

```ini
# ~/.config/systemd/user/quirework-backup.timer
[Unit]
Description=Back up the Quirework provenance stores every four hours

[Timer]
OnCalendar=0/4:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now quirework-backup.timer
```

Give `ExecStart` the absolute path of the `quirework` your projects run,
since a user service does not see your shell's `PATH`. The service finds
the provenance home in the per-user configuration file, which it reads
like any other process. `Persistent=true` runs a backup the machine
slept through as soon as it wakes. A failed run shows in
`journalctl --user -u quirework-backup` and in `store status`.

Restoring is a copy in the other direction. On a new machine,
`quirework provenance store init` creates an empty store under the
identity the ledger names. Copy the backup into it, for example with
`rclone copy gdrive:quirework-provenance/my-book <store directory>`,
then run `quirework provenance store verify`, which proves that every
accepted file came back intact. A `git` backup is restored by cloning it
to where the store belongs, with no init.

### What capture selects

Capture selects sessions by their recorded relationship to the project
and its worktrees. Codex capture also excludes runtime subagents and
delegated evaluation seats whose recorded tool calls name a raw capture
in the store. It reports those exclusions. The ingest's main session
remains eligible, as does a delegated task that only discusses a capture
in prose. A store directory's bare name, such as
`cc-sessions/<id>.jsonl`, counts only where it starts a path, so a
matching directory name inside another project's absolute path does not
establish an evaluation of this project's corpus.

A Claude Code session's sub-agents are part of it. Claude Code writes
each sub-agent to its own transcript, in a `subagents` directory beside
the session's, and capture folds them into the session's capture under
the rule that shapes the session itself: what a sub-agent said and did
is kept, and what it was given and what it read is not, so its injected
instructions and the files and transcripts it read never reach the
store. Its records keep the harness's side-thread stamp, and the
capture's first line records each sub-agent's fingerprint and the task
the session gave it.

Capture reads each harness's transcripts where the harness keeps them:
Claude Code's under `~/.claude/projects`, Codex's under
`$CODEX_HOME/sessions` (normally `~/.codex/sessions`), and Antigravity's
under `~/.gemini/antigravity-cli/brain`. A harness set up with another
home needs capture pointed at it; otherwise the sweep stops and names
the directory it looked in. `providers.claude.sessions` names Claude
Code's directory, which `spec trail add --find` also reads, and
`providers.agy.sessions` names Antigravity's. Codex follows its own
`CODEX_HOME`.

Each capture sweep also records the project's directory in every
checkout it can see. The list is `project-trees.txt` in the ledger, and
it belongs in the same commits as `accepted.txt`. The catalog and
`coldread triage` judge whether an edit touched the work by measuring
its path from these directories. They never ask which worktrees exist
when they run. Removing a worktree, or reading the corpus from a fresh
clone, therefore changes no count. Capture only ever adds lines. A
checkout removed before its directory was recorded has to be added by
hand, as one absolute path per line. Until it is, the catalog refuses to
build and names the directories where its accepted sessions started.

### Reading the corpus

Search reads human-authored turns; provider metadata is not proof of
which model authored a historical turn.

Claude Code logs three kinds of author turn outside its user records. A
prompt typed while the model is working is delivered into the running
turn and logged only as queue operations; it counts at the moment it was
typed, and a queued sub-agent report, a task notice or a prompt pulled
back into the input box does not. An answer to a question the model
asked is logged as a tool result; `show`, the trail and the blind
cold-read keep it whole, while search, `provenance input` and the effort
metrics take only what the author typed, an answer of his own or his
notes, since an option's label was worded for him. A command that sends
the model a prompt, such as a skill typed with its arguments, is logged
as nothing but the harness's wrappers; `show`, the trail, search,
`provenance input` and the effort metrics count it as the line the
author typed, and the blind cold-read leaves it out as it always has. A
command the harness answers alone, such as `/model`, never reached the
model and counts nowhere. Each turn keeps the same number everywhere, so
a search hit opens with `show --turn`.

A harness logs its own text in the human's slot too, and none of it
counts as a turn of the author's: slash-command and system-reminder
wrappers, the report a background subagent sends back, and the summary a
compacted session resumes from. Search, show, the trail and the effort
metrics apply the same rule, and `show --raw` labels that text
`harness`. The same rule covers the notice Claude Code writes in the
model's slot when a request fails ("API Error: …"): it is the harness's,
not the model's, so a turn it follows reads as unanswered, and a blind
cold-read never sees it as something the model said.

A sub-agent's records are not the session's dialogue either. Search, the
trail and the blind cold-read leave them out, and `show` shows them only
with `--raw`: the task the session gave the sub-agent as `agent`, and
what the sub-agent wrote back, its prose and the report it handed back,
as `subagent`. The catalog names a session's model and dates from its
own thread, and counts its sub-agents' edits as the session's work. The
effort metrics count sub-agents, their tool calls and their words in a
block of their own, outside the conversation's totals and its
AI-to-human ratio, because a sub-agent's task is written by the
assistant and its report is read by the assistant.

Claude Code resumes or forks a session under a new id, and the new
transcript opens with the whole conversation it continues, each record
copied with its id. Both captures can be accepted, since each can hold
something the other lacks: the copy holds what came after, and the
original keeps its sub-agents and any turn the copy left behind. The
effort metrics and the blind cold-read count each copied record once, in
the capture holding most of the conversation, which for a resume is the
copy. A capture whose whole dialogue is counted there is not a session
of its own in the metrics, though its sub-agents still count, and the
report says how many were folded. The cold-read skips such a capture,
and `coldread status` names the capture that reads it. It decides this
among one run's sources, so a session stays in a run whose question its
copy is not asked. No render changes, so no cached report is read again.
Search, `show`, the catalog and the trail still read each capture whole,
as files of the corpus; the trail's backfill notes a turn captured
twice.

Recent Claude Code releases record on each assistant turn the reasoning
effort it asked for, and Codex records it on each turn's context. The
effort metrics count it beside the model id, in a table under the
models, with the share of records that carry one. The table appears only
when some accepted session records an effort, so a corpus captured
before any harness did prints the report it always printed. An effort is
the setting requested, not the reasoning done: the same word buys a
different amount from each model, and after a mid-session model swap it
names what was selected, as the model id does.

A spec trail reads sessions only where provenance is enabled. There,
`spec trail add --find` also reads this project's live Claude Code and
Codex transcripts, from the same locations capture sweeps, and never
writes to them. That is how a turn in a conversation still going on is
pointed at before the session is captured. A session captured while it
was still going on is read from its transcript once that has grown past
the capture, since the later turns exist only there. Without provenance,
a trail can still be kept by hand and read as pointers.

`provenance.redpen` names the archive of red-pen rounds that
`publish redpen archive` writes: your marks on a rendered copy of the
book, one file per round. `publish kindle notes` writes the highlights
and notes on your own Kindle copies there too, one file per export,
holding only the marks no earlier export of that copy held. Declared,
each round also reads as a session of your turns, one per mark, named
`redpen-` and its file name, so `show`, the trail and its backfill reach
words you typed on the page as well as in a conversation. A round
records the day of each mark and no time of day, so a breadcrumb names
the mark by its number. Nothing joins by default: the archive is
private, and an undeclared one stays out.

`publish.reader_notes` names where `publish kindle notes` keeps the
highlights and notes made on a copy for anyone but you, as
`copies.author` tells: another reader's feedback, one file per export,
in the same form as your own rounds. It must lie outside
`provenance.redpen`, and nothing reads it as a session, so a reader's
words never count as yours in `show`, the trail or a blind cold-read. It
has no default; a reader's notes are refused until it is declared.

`provenance.rulings` names the archive of review-sheet rulings. A review
sheet puts items to you (proposed edits, decisions, findings), and you
choose an option for each and may comment beside it. The page keeps
those choices in its own storage, so nothing else holds them.
`quirework provenance rulings archive RECORD.json --name NAME` writes
one round's ruling as `NAME.md` in this directory, and never overwrites
one. RECORD is JSON written by whatever built the sheet: `title`,
`round`, `sheet`, `ruled_at` (an ISO-8601 time), `notes`, and `items`,
each with a unique `label`, the `presented` text, the option `ruled` and
your `comment`. Declared, each round reads as a session named `rulings-`
and its file name, one author turn per item with what the sheet
presented as the text it answered, and your notes as a last turn. So
`show`, the trail and its backfill reach your rulings as they reach your
marks. The sheet records one time for the whole ruling, so a breadcrumb
names an item by its turn number; the round takes its place in date
order by the day it was ruled. Text is kept as you wrote it, except that
line endings become `\n` and space ending a line is dropped, so a
project that formats its Markdown leaves the file alone. Nothing joins
by default.

Declared, a ruling also counts as your input wherever the corpus counts
it. Keyword and semantic search and `provenance input` read your
comments and notes as your own words, numbered as `show` numbers them,
and leave out the option you chose, which was worded for you. The blind
cold-read reads each round as a source of its own era, `e-rulings`,
redacted like a local session, in the work scope, since the making list
names only captured sessions. `provenance metrics` counts each item
ruled as one of your turns, with only your comment and notes as your
words. Red-pen marks still join only `show` and the trail.

### Blind cold-reads

`quirework provenance coldread` puts accepted sessions in front of blind
analysts and keeps what they report. What it keeps lives in
`provenance.coldread`: the reports, cached by the content they read so
an unchanged session is never paid for twice, including one captured
again only to add its sub-agents, whose blind copy is unchanged; the
manifest that indexes them; and the analyst briefs the project writes,
`analyst-prompt.md` and, for sessions about the apparatus,
`analyst-prompt-making.md`. It defaults to `coldread` inside the ledger,
so the cache travels with the decisions it was made from.

The working copies are rebuilt on demand and default to the project's
cache. `provenance.rendered` holds the filtered sessions staged for the
analysts, one directory per scope. `provenance.reports` holds each
report as it arrives and before it is recorded, including any
quarantined because its analyst did not declare itself blind. Set them
only to keep these somewhere other than the cache.

An analyst's copy is filtered so that it cannot learn whose project it
is reading. `provenance.redaction_policy` names a TOML or JSON file
whose `markers` list the project's distinctive framing: its title, its
author's name, a phrase that would hand the analyst the conclusion it is
meant to reach. `provenance.redaction_markers` lists them inline
instead, and wins when both are set. Each occurrence, in any case,
becomes a visible placeholder, so the analyst still sees that context
was stated. A long human turn that still carries a marker once the
harness's wrappers are stripped is an injected context dump, and is
dropped whole. Without either setting nothing is redacted. The list is
part of the filter's version, so a change to it is checked against every
cached report: one whose session reads the same under the new list is
kept, and one whose filtered copy changed is analyzed again. Keep the
policy file private, since it lists exactly what an analyst must not
see.

### Caches and run storage

Derived caches default to a project-specific directory under the XDG
cache directory, and `project.cache` moves it. Run storage defaults to a
project-specific directory under the XDG state directory. Configure
`runs.store` to retain an existing private store and `runs.reviews` for
reviewed output.

Nothing on a typical Linux system empties the XDG cache directory on its
own, so treat anything written there as kept until someone deletes it.
An artifact that should not outlive the command that made it goes to a
private per-run directory instead: under `$XDG_RUNTIME_DIR` where the
session has one, which is per-user, created 0700 and emptied at logout,
and otherwise a `tempfile` directory honoring `$TMPDIR`. The verbatim
human-input compilation defaults there because it is private and is
rebuilt in seconds; `provenance.human_input` keeps it somewhere instead.

To have the cache collected rather than grow, install the bundled
`systemd-tmpfiles` rule. `systemd-tmpfiles-clean.timer` runs daily in
the user session and applies it; nothing else has to run.

```bash
quirework config template tmpfiles \
  --out ~/.config/user-tmpfiles.d/quirework.conf
systemd-tmpfiles --user --clean   # apply now instead of waiting
```

It removes entries under the cache directory that have gone untouched
for 30 days, leaving anything a current project still reads. It creates
nothing, so it is inert until Quirework has cached something, and it
never touches the state directory, where run records live. A
distribution package installs the same file as
`/usr/share/user-tmpfiles.d/quirework.conf`, which a user's own copy in
`~/.config` overrides.

Every confined seat gets a private runner home, bound over the real one
so the seat cannot reach the operator's credentials or memory. A home is
the harness's own installation and scratch state. It is large and worth
nothing once its run is over: one measured room held 92 MB of them
against 1.6 MB of records. So homes live in the cache, under
`$XDG_CACHE_HOME/quirework/homes/`, and a run reaches each of its homes
through a link beside its run directory, named for that directory with
`.blind-state` added. The homes directory carries a `CACHEDIR.TAG`, so
borg, restic and GNU tar skip it under their exclude-caches option
wherever the cache is, while the records stay in the state directory a
backup keeps. A home made before homes moved stays beside its run until
that run launches a seat again, which moves it into the cache. A cache
on another filesystem cannot take that move, and the home then stays
where it is.

The cache rule collects a home with the rest of the cache, 30 days after
anything in it last changed. A run that waited that long loses its
sessions with it: its next resume refuses, says the cache was cleared,
and leaves the records as they were.

`quirework sop blind prune` removes finished homes sooner, and it is the
only thing that removes a home made before the move. Pruning keeps every
record, receipt and transcript, because a home's native transcripts have
already been read into the receipts beside it and `launch.json` records
the home's path whether or not it still exists. Where the link was, it
leaves a tombstone naming when, so a later resume says the home was
pruned.

A seat paused between turns looks idle, so prune does not judge a home
by its age alone. Each procedure that runs seats holds their homes from
a seat's launch until it knows the run is finished with them, and
releases them there:

- a room once every round of its budget is published or it converged;
- a roundtable once it closes or every seat has retired;
- the run engine once a seat completes and no fork child is still
  checked against it, and the copy of that seat's home its fork
  checkpoint keeps, which lives in the cache too, at the same point;
- a workshop writer once it completes or is retried;
- a chapter comparison's arms once it completes.

Two homes stay held. A chapter comparison keeps its parent, because
`inspect` checks every arm against it. A review is never finished,
because any turn can be continued, resumed or forked later. Prune keeps
a held home and names what holds it. `--force` removes it anyway, and
the tombstone records the hold, so the resume that then fails says so. A
home nothing holds was made before holds existed or by a command that
runs one seat and is done. It is kept only while it was touched in the
last hour. A home whose seat still has a live process is never removed.

### A test suite of the project's own

A book that tests its own extension, checks or fixtures drives Quirework
from pytest, and Quirework writes into the running user's home: run and
usage records, seat homes, the per-user configuration and a provenance
home. A suite that redirects some of these writes into the rest, and
nothing says so. A leaked home that a procedure holds even survives
`prune`. One line in the suite's top-level `conftest.py` isolates all of
them:

```python
pytest_plugins = ["quirework.testing"]
```

For the session, `HOME` and the four XDG trees point into a sandbox of
its own, `QUIREWORK_PROVENANCE_HOME` and `CODEX_HOME` are dropped, and
usage records and live canaries are switched off. A write that still
reaches the person's own XDG trees, a harness's home or the provenance
home fails the test that made it. Tools installed with `pip --user` and
the user's fonts stay usable. Git's per-user settings do not reach a
test, so a test that commits names its author, as
`git -c user.name=... -c user.email=...` does. The list of locations
ships with Quirework, so one a later release adds is covered with no
edit here, and Quirework's own suite loads the same plugin.

A suite that fakes a harness must leave the session a resume continues,
because every resume first checks the seat's home still holds it.
`quirework.testing.native_session(run_dir, runner, session_id)` puts it
where that runner keeps one, as Quirework itself states it, so the suite
never writes a harness's layout down.

## Usage records

Quirework records which of its commands ran, so the tool can be shaped
by what people use rather than by what somebody guessed they use. It is
on by default and entirely local: nothing is transmitted, and nothing
but `quirework usage` reads the file.

```toml
[usage]
enabled = true
store = "~/.local/state/quirework/usage"
```

Set `enabled = false` to record nothing for a project.
`QUIREWORK_USAGE=0` does the same for one process or one shell and
overrides the file, which is what a shared machine or a test harness
needs. `quirework usage prune --before 90d` removes old records, and
deleting the directory is always safe.

One record is one dispatched command, appended as a line of JSON to a
monthly file: the command, the project root, the exit status, how long
it took, and the version that ran. Command ARGUMENTS ARE NOT RECORDED.
`quirework provenance search` takes a phrase out of the author's own
notes, so a file that accumulated arguments would become a private
corpus nobody agreed to keep. The single exception is a procedure's
name, and only once the project has resolved it to a file under
`sops.root`, because which procedure gets used is the question these
records exist to answer.

A second kind of record is one model seat launched: by a room, a
workshop, a review, a run or an experiment. It carries the workflow, the
harness, the model, the effort and the project, and nothing about the
assignment, for the same reason arguments are kept out. It is written
before the process starts, so a seat the provider refuses is still
counted as the launch it was. `quirework usage seats` ranks harness and
model pairs by launches, with the projects each ran in, which is how to
learn whether a harness is used at all before deciding to keep
supporting it.

The store is per-user rather than per-project, which is the opposite of
how run storage works and is the point: the comparison worth having is
across the books one person writes, so the project root is a field in
each record instead of a separate directory. `quirework usage sops`
therefore lists, under each procedure, the projects it was read in and
how many reads each one made, because one name can be a procedure in
several books. Set `store` to keep a project's records somewhere else,
which also separates them from everything else that user runs.

Three kinds of invocation are outside the count, and a report read
without knowing that will understate them. `--help` is not use of a
command. The `config` and `usage` groups inspect the tool rather than do
its work. And a procedure read straight off disk never reaches Quirework
at all. A generated skill therefore routes the companion through
`quirework sop show NAME`, which prints the procedure verbatim and
counts the read; a companion that opens the file anyway, or a person
reading it in an editor, is not counted, so procedure counts remain a
floor rather than a total.

Recording never costs a command its result. A store that cannot be
written is reported once on stderr and then left alone for the rest of
the process. `quirework config check` resolves a configured `store` so a
typo fails where somebody is asking rather than on every later command.

## Project policy and extensions

Policy files are commented TOML data referenced by configuration.
Existing JSON policy files remain readable. They keep project vocabulary
and visual choices out of Quirework's implementation:

- `manuscript.chapter_policy`: `metadata_layers`. The metadata layers
  default to the sidecar directories under `manuscript.source`, so a
  project declares this only to scan something else. An extension that
  runs a guard of its own over the same files asks
  `manuscript.units.layer_files()` for them and blanks fenced examples
  with `manuscript.notes.mask_fences()`, so it reads exactly what
  `spec units --check` reads.
- `publish.layout_policy`: `typst_prologues` and `typst_epilogues`.
- `publish.render_policy`: `print_drops`, `noparts_drops`,
  `folio_sides`, `epub_style_headers` requiring CSS-comment cleanup in
  staged EPUB headers, and `reader_metadata`, the metadata a reader
  instrument's render sets. A book that renders differently until it is
  published, such as a copyright page that reserves every right until
  then, names here what its published state sets: `[reader_metadata]`
  with `draft-copy = false`, in a book whose `_quarto.yml` declares
  `draft-copy: true`. Each key is set at the top level of the reader
  packet's staged `_quarto.yml`, so a seat meets the book a reader will
  hold, not a copy that tells it it holds a draft. Values are strings,
  numbers or booleans, and a boolean stays one. Only the packet's own
  stage applies them. `publish build`, `publish redpen` and every copy
  made for a person keep the book's own metadata, and nothing else
  switches it. `revision_metadata = true` has every stage record the
  source it was copied from, so a copy handed to a reviewer can say
  which revision it is: `source-commit` (the full hash),
  `source-commit-short`, `source-date` (the commit's date, YYYY-MM-DD)
  and `source-modified`, at the top of the staged `_quarto.yml`. A book
  prints them where it chooses, or nowhere. `source-modified` is true
  when the sources had uncommitted changes or untracked files, or moved
  while they were copied; files git ignores are not compared. Each stage
  is copied at its own moment, so each records its own revision. Outside
  a git checkout no stage records one, and a value that is not a boolean
  stops the render.

  `print_drops` and `noparts_drops` name chapters a stage empties
  because its formats do not carry them: the print stage, and the
  parts-less stage that renders EPUB and DOCX. A screen-only landing is
  the usual case. Quarto will not render a book without its home page,
  and it synthesizes a numbered heading for a file without one, so a
  dropped chapter keeps its entry and becomes a single unnumbered,
  unlisted heading carrying the marker `quirework-omit-from-this-format`
  (`bookstage.OMIT_MARKER`). Every format the stage renders must then
  drop the marker. A Typst template renders nothing for that heading. An
  EPUB or DOCX format runs a Lua filter at `post-render` that removes
  the marker's heading and everything after it up to the next chapter
  heading; it runs that late because Quarto would otherwise synthesize
  the heading it was given.

  `folio_sides = true` makes a render that produced a PDF read the PDF's
  page labels back and fail when an arabic folio sits on the wrong side:
  an odd number on a left-hand page, counting the file's first page as a
  right-hand one. A viewer shows one page at a time and cannot show
  this, and one page gained or lost before the body flips every folio
  after it. Leave it unset for a PDF made only for screens. A value
  other than true or false stops the render.

  `proof_switch` names the top-level metadata key that says the book is
  not yet published, `draft-copy` in the example above. Until that key
  is the boolean `false`, every render is a proof; a missing, quoted or
  misspelled value leaves it one. A proof needs `revision_metadata`. Its
  files are named for their commit by `proof_name`, a pattern over
  `{slug}` (the book's title or `book.output-file`, lowercased and
  hyphenated), `{title}`, `{date}`, `{short}` and `{commit}`, by default
  `{slug}_proof_{date}_{short}`, with `-modified` added when the sources
  had changes and `_copy-NN` for a copy made for one person. Its library
  title, `proof_title` over the same fields and `{copy_note}` (empty, or
  `, copy N`), by default
  `{title} (uncorrected proof {short}{copy_note})`, replaces the first
  `dc:title` of an EPUB and a DOCX's core title after the render, and
  each stage sets it as `proof-title` for a PDF template. A copy for one
  person also sets `copy-number` and `copy-recipient`. The book prints
  the stamp where it chooses. The build then reads every proof back and
  stops when one does not say what it is: the short commit on every page
  of a PDF and every HTML page, in every reading-order document of an
  EPUB but its picture-only pages and in a DOCX's text, the library
  title naming it, an EPUB's cover being the proof's own, and a copy for
  one person naming them. The reader packet's stage keeps the published
  state its `reader_metadata` sets and names its file
  `reader-instrument-not-a-copy`.

  `cover_source` names a Typst source, relative to the manuscript, that
  every stage compiles into the image `book.cover-image` names, at 300
  ppi, as JPEG or PNG by that file's extension (Pillow writes it). It
  reads `sys.inputs`: `proof`, "true" or "false" when the policy names a
  switch, and on a proof `commit`, `short`, `date`, `modified`, and for
  a copy `copy` and `recipient`. A proof's cover carries its commit in
  the image file's comment, which is how the check recognizes it.
- `text.terminology`: `terms`, `retired`, `allow`, `scope_glob`, and
  `exclude_names`. A term warns and waits for the author's ruling; a
  `retired` phrase is a ruling already made, so `text check` fails while
  one is back in reader-facing prose. It matches in any case, across a
  line break, and with either apostrophe: a ruling typed "it's" also
  finds "it’s" as a word processor or a social site writes it. Without
  `scope_glob`, the scope is the manuscript's prose files.
- `text.tells_policy`: `phrases`, a table of regular expressions mapped
  to why each reads as machine-made; `contrast`, which turns the English
  contrast detectors off when false; and `scope_glob` and
  `exclude_names`, read as the terminology policy reads them. A phrase
  is written uncontracted, the way the study's models write it, and it
  also matches the sentence with its English contractions spelled out,
  so "is the whole" finds "that's the whole" in prose held to
  contractions. An "'s" is spelled out only after a pronoun, where it
  cannot be a possessive. The phrase list goes stale as models change,
  so date its measurement in a comment. `text tells` warns and never
  gates. Quirework ships no default list, because a stale one would
  report nothing and read as clean prose. `template tells` prints a
  dated seed instead, drawn from Graphite's "AI Tells" (2026) study,
  with the recipe for refreshing it against the models that draft your
  prose.
- `sops.persona_policy`: the optional `default` persona ID.
- `sops.review_policy`: review frames, reviewable source paths, and
  `hidden`, the checkout paths a jailed reviewer must not read.
- `publish.variant_policy`: who drafts each derived edition, and how.
  `default_model`, `default_harness` and `default_effort` apply to every
  kind, and a `[kinds.KIND]` table overrides any of `model`, `harness`
  and `effort` for one kind. Its `shape` is `chapters`, one draft per
  chapter, or `document`, one draft of the whole edition, and its `omit`
  lists the chapters, by file name, that a by-chapter edition never
  carries and so never drafts. `blind_tells` names marks of the
  project's own that a drafter's bundle must never contain, and
  `prompt_intro` and `document_intro` replace the opening instructions
  of a chapter's drafter and a document's.
- `provenance.metrics_policy` and `provenance.coldread_policy`:
  historical source classifications, explicitly selected analysis
  models, and `output_counts`, which names each collection the metrics
  report counts and the glob that finds it.

Three settings describe a project's own placement, and all three default
to describing nothing rather than to another project's directory names.
`manuscript.companion_dirs` names reader-facing collections beside the
book, whose quotations and spec citations `spec notes --check` then
gates. `manuscript.noncanonical_roots` names directories that hold
working material rather than authority, so a mark resting on one is
reported. `runs.evidence_roots` names every directory holding saved run
records. A project that keeps one needs only `runs.reviews`. Files
inside a run record are never reflowed by `text wrap` and never read as
authority, which is the guarantee these three settings buy. An edition's
blind drafts, in `_raw/` inside each edition under `publish.variants`,
are never reflowed either, with no declaration.

Each Markdown file directly inside a companion directory is an entry,
except a `README.md`, which indexes the collection, and a file whose
name starts with `_`, such as a template. An entry names what it rests
on in a visible run in its header paragraph, labelled `**Anchors:**`
unless `manuscript.companion_anchors` names another label:

```markdown
**Anchors:** `@unit(why-the-lamp-turns)` `@bind(lamp-period)`
```

A unit is named by `@unit(slug)`, by its bare slug, or by its file as
the format writes it (`opening.qmd`,
`posts/why-the-lamp-turns/index.md`). Each `@bind(slug)` after it must
be a section that unit binds. `spec notes --coverage` then maps which
commitments the entries work through.

### Where the work lives

Several tools ask whether a file is part of the work: the catalog and
`coldread triage` when they split a session's edits into writing and
tooling, `sop review audit` when it lists commits a reviewer must see,
`provenance metrics` when it follows the history of the prose, and
`text check` when no `scope_glob` names its scope. They share one
answer, built from what the project already declares:

- the units and their marks, as the format lays them out: a book's whole
  source, or a site's pages, their bundles and `manuscript.marks`;
- the register: each `manuscript.spec` file, each
  `manuscript.unit_spec`, and the references and review notes;
- `manuscript.companion_dirs`;
- and `manuscript.noncanonical_roots`, kept apart as writing without
  authority. Authorship counts it, and a reviewer is not owed it.

A site whose spec lives in `specs/` therefore has its spec edits counted
as writing and reviewed, the same as a book whose spec sits beside its
chapters. A policy that names its own paths still wins:
`coldread_policy.doc_dirs`, `review_policy.reviewable`, and
`metrics_policy.canonical_dir` and `essay_paths`. The metrics report
counts the words of every prose file, without frontmatter or comments.

`text.modes` chooses how `text wrap` treats each file without any code.
It is a list of `[glob, mode]` rows over project-relative paths, and the
first match wins:

```toml
[text]
modes = [["content/**/*.md", "sentence"], ["**/*.md", "column"]]
```

A mode is `sentence`, `column`, `trailing` or `skip`. `**/` crosses any
number of directories, and `*` stays inside one. An unmatched file falls
back to the built-in rule, which puts `.qmd` in sentence mode and other
Markdown in column mode. A file inside a saved run record is skipped
whatever the table says. Both modes copy either frontmatter convention
verbatim and pass a Hugo shortcode line through untouched.

A project can select installed Python extensions. `checks.command` names
its check-suite function as `module:function`. Without one,
`quirework dev check` runs the default suite, which checks the project's
Python and asks whether `text wrap` would change any of its Markdown. A
directory below the project holding its own `quirework.toml` is left to
its own suite. It judges the files git tracks, a new one from the moment
it is added, so nothing is committed unjudged. A file nobody has added,
such as notes kept in the checkout, is checked too but only reported
under a heading saying it is not counted; it never makes the suite fail.
`text.classifier` selects a function returning `sentence`, `column`,
`trailing`, or `skip` for a source path, for a rule `text.modes` cannot
state; a project uses one or the other. The function receives an
absolute path, and `quirework.text.mdwrap.project_relative` turns that
into the path as the project names it, which is what a rule about
location needs. `publish.grayscale_transform` accepts SVG text and its
asset name and returns transformed SVG text. `publish.figure_checks` is
a list of functions returning diagnostic strings. Extensions belong to
the consumer's package and must be installed explicitly.

`providers.codex.catalog` pins the Codex model catalog to a file, which
is then used as given instead of asking `codex debug models`. Leave it
unset unless a run must resolve against a frozen list. Claude Code keeps
no local catalog, so `quirework sop models list claude` prints the list
`providers.claude.models` declares. A model on that list resolves
verbatim, which is how a project runs a seat through a gateway whose
model id is neither a Claude family nor a version; the receipt still
records which model answered. `providers.claude.gateway_env` names the
environment variables such a seat needs passed through to its runner, a
base URL and its key among them. A launch passes them through when the
operator's own environment carries them and refuses rather than fall
back, and a seat with a declared gateway never borrows the host login:
the gateway is the auth path.

`providers.claude.output_ceiling` is the most a Claude seat may write in
one response, thinking included, such as `128000`. Claude Code sets the
ceiling per model and asks some models for half of what they allow, so a
long answer at high effort is cut and resumed, and the message the
harness writes to resume it asks the model to break its remaining work
into smaller pieces. Every driver freezes the value into each Claude
seat it prepares, run seats, rooms, roundtables, workshops, reviews and
experiments alike, and the launch passes it as
`CLAUDE_CODE_MAX_OUTPUT_TOKENS`, which the CLI caps at the model's own
ceiling. A record keeps the ceiling it was prepared with, and one
prepared without it runs at the CLI's default. A run seat may declare
its own in its policy. The receipt holds each cut response to the
declaration (see "Execution and recovery" in the workflow guide).

Every launch proves its seat blind before the seat starts. A runner
release can change what a flag does without rejecting it, and only a
live seat can say what it holds, so the launch canary spends one short
exchange asking it what it can read, which tools it has and what
instructions it was given. It runs the seat's own command, rebuilt by
the launcher that will start the seat, with the canary's prompt, model
and session in its place. A passing canary is recorded under the runner,
its exact installed version, a fingerprint of that command, and what the
seat may hold on purpose: nothing of the project for a blind seat, the
repository and its guidance for a review, the web for a research seat. A
later launch with the same key starts at once. A new release of any
size, or a change to the seat's flags, runs the canary again first. A
seat that holds more than its contract allows, or a canary whose answer
proves nothing, refuses the launch, and the output is kept under `~/tmp`
for inspection. A Codex canary first asks Codex's app server which MCP
servers the seat would hold. Any server refuses the launch without
spending the exchange. An inventory that cannot be read proves nothing,
so it refuses the launch too. The record keeps the inventory as
`mcp_servers`.

Antigravity cannot be confined to a tool set, so every agy seat holds
its web search and page-reading tools under any flags, and its canary
cannot prove a seat without the web. A launch that reads the agy
transcript afterwards and refuses a run that used those tools, as the
run engine and the writers of a workshop or room do, says so in its
contract. Its agy canary then accepts the web tools and still refuses
MCP, the tree and the project's guidance. `sop blind run` and
`sop blind canary` have no such audit, so they refuse an agy seat.
Records proven before the canary knew agy's tool names do not carry
over: the contract changed for every runner, so each runs its canary
again on its next launch.

The records are per machine, in `providers/canaries.json` under the
project's cache, because blindness is a property of the runner installed
here. Each launch writes the record it relied on into its own receipt,
`launch.json` or the blind receipt, so the evidence stays with the run.
The cold-read keeps no launch receipt; it prints the canary instead. An
edition's blind drafts run as seats of a run, with its receipts.
`quirework sop blind canary` runs the canary on demand under the shared
blind flag set, the set a command written by hand for `sop blind run` is
proven under, and `sop blind check` reports which releases have been
canaried without failing on one that has not. A command written by hand
has to carry that set itself. A Codex command without
`-c features.apps=false`, for example, holds the account's connected
apps however its canary passed. `providers.validation_record`, which
kept a record in the repository and failed `check` on a minor version
change, is retired, and a project that still sets it is refused with
that explanation.

`QUIREWORK_CANARY=0` forbids live canaries in one process, so any launch
there that has no record is refused. A test suite sets it: a test that
drives a launch without stubbing the gate would otherwise reach a real
runner and spend a real model's tokens. `quirework.testing` sets it,
with the rest of a test session's isolation (see "A test suite of the
project's own").

The canary runs on the cheapest model each runner offers.
`providers.canary_models` names that model per runner, as in
`canary_models = {claude = "haiku", codex = "luna"}`. A name resolves as
a seat's `--model` does: a family to its newest member at every launch,
so the next release of that model is canaried as itself with no edit
here, and an exact slug to itself. A name the runner's catalog cannot
resolve refuses the canary and names this setting. Without an entry,
Antigravity's is picked from `agy models`, and any other runner's canary
refuses to run rather than fall back to the account's default, which may
be its most expensive tier. Preparing a run, a workshop, a writers room
or a roundtable checks this for every seat and refuses the plan when a
seat's runner has no canary model, before anything is staged. A seat is
proven blind only when it first launches, which may be rounds later. A
run's record also keeps the configuration it was prepared with, so an
entry added afterwards would never reach it, and the seat would stop at
its first launch.

Provider account leases have a separate scope from project data. Use the
same `providers.agy.account` identity for projects sharing an account.
`providers.agy.state` can select an existing shared lease directory.
Changing a project or worktree does not create a second account quota.

The publisher controls each staged render's `_site` output directory.
Quarto publication settings and reading order remain source-owned, while
build output stays inside the configured build tree.

## Annotated templates

`quirework config template book` prints a commented configuration for a
Quarto book, and `template site` one for a site of Markdown pages built
by Hugo. Use `template run` for a tiny model plan,
`template terminology` for an advisory word policy, `template tells` for
a machine-habit policy, or `template tmpfiles` for the cache-cleanup
rule above. Add `--out PATH` to create a new file; an existing file is
never overwritten. Every template is bundled in the wheel.

Run plans accept TOML or JSON. Preparation still freezes canonical JSON
records, so saved runs and their integrity checks retain their format.
The comments explain choices and do not enter the model assignment.

A book can define `sops.default_reader` and named `sops.reader_presets`.
Each preset selects an `instrument` and `include_matter` boolean.
`include_matter` decides whether a sequential read is handed the front
and back matter; a self-driven read never is, and is told the back
matter's titles instead. The instrument supplies its own default
persona. An explicit `--instrument` or `--persona` overrides that
selection for a prepared book read.

A preset may also declare `delivery`, the list of deliveries its
questions were written for, from `pages`, `file` and `self-driven`. A
reader instrument opens by telling the seat what it has been handed, so
the same questions asked under another delivery begin with a false
sentence, and the seat answers the courier rather than the book.
Declaring the list makes that a refusal instead. Declaring nothing keeps
the questions usable everywhere, because a project with one delivery
should not have to say so. An explicit `--instrument` overrides the
check, which is how a deliberate mismatch is run.

A preset may name `ask` and `wrap_ask` for a self-driven read. An
instrument written for that mode has no continuation ask, so it has no
sequence to declare, and naming the asks in the preset keeps them off
every launch command.

`quirework publish compare prepare` forks one reader across a chapter's
revisions, and every arm is asked the same frozen questions.
`sops.reader_driver` names the Python file they come from. It defines
`TRADE_TURN_ONE`, the opening ask, `TRADE_TURN_NEXT`, the ask that
continues the read, and `TRADE_CHAPTER_HEADINGS`, the headings each
answer must carry. Preparing a comparison freezes those questions into
its plan and copies the file into its record, so every arm is asked what
was frozen, whatever the file says later. The setting has no default,
and nothing checks it until a comparison is prepared.

`quirework sop workshop prepare` seats the writers a plan names, and
falls back to the project's default seats when it names none.
`sops.roster` names that default: a JSON file whose `writers` list gives
each seat a `harness`, a `model` and an `effort`, and whose optional
`persona` applies to every seat for which neither the seat nor the plan
names one, including the seats a plan names itself.
`quirework sop workshop roster --check` resolves each seat against the
installed model catalogs, so a model a provider has since renamed fails
there rather than halfway through an experiment. A project whose plans
always name their writers can leave the setting unset.

`providers.isolation_policy` can list `mount_markers`: path words that
would tell a seat whose book it is reading. A self-driven read refuses a
mount matching one. They are deliberately separate from
`project_markers`, which the blind canary matches against a seat's
answer. A book about kettles wants that word to fail a mount path and
never to fail a verdict, and one list cannot do both.

## From schema 1

Schema 1 kept everything about the written work in one `[book]` section.
Schema 2 splits it by job, and a schema-1 file is refused with a pointer
here rather than read with guesses. Set `schema = 2`, then move each key
to the section this table names. The key names do not change.

| Schema 1 | Schema 2 |
|---|---|
| `book.source`, `book.chapters`, `book.quarto` | `manuscript.*` |
| `book.spec`, `book.spec_trail`, `book.spec_trail_leads` | `manuscript.*` |
| `book.references`, `book.review_notes` | `manuscript.*` |
| `book.companion_dirs`, `book.noncanonical_roots`, `book.chapter_policy` | `manuscript.*` |
| `book.build`, `book.figures`, `book.figure_fonts` | `publish.*` |
| `book.figure_type_floor_pt`, `book.figure_checks` | `publish.*` |
| `book.grayscale_transform`, `book.grayscale_policy` | `publish.*` |
| `book.render_policy`, `book.layout_policy`, `book.typst_template` | `publish.*` |
| `book.variants`, `book.variant_policy` | `publish.*` |

The commands moved the same way:

| Schema 1 | Schema 2 |
|---|---|
| `book notes`, `book trail`, `book quotes` | `spec notes`, `spec trail`, `spec quotes` |
| `book chapters` | `spec units` |
| `book build`, `book figures`, `book fonts`, `book epubcheck` | `publish build`, `publish figures`, `publish fonts`, `publish epubcheck` |
| `book redpen`, `book packet`, `book variant` | `publish redpen`, `publish packet`, `publish variant` |
| `book plan`, `book compare` | `publish plan`, `publish compare` |

A project's own procedures, guides and generated skills name these
commands too. Regenerate the skills with `quirework sop skills` after
editing the procedures.

Three names in a project's own files changed with the vocabulary, since
a site's post is no chapter and a site is no book:

| Before | Now | Where it is written |
|---|---|---|
| `@ch(slug)` | `@unit(slug)` | marks, spec, companion entries |
| scope `book` | scope `work` | instruments, panels, run plans |
| `--scope book` | `--scope work` | `provenance metrics`, `provenance coldread`, `spec trail backfill plan` |

Saved evidence keeps its words. A prepared run keeps the scope it was
prepared with, and a cold-read report cached under the corpus scope
`book` is read as `work` and still hits, so none is bought again. A
committed catalog is rebuilt once, since its rows now say `work`.
