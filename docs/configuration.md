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
copy carries, when the project uses those features.
`publish.typst_template` names the Typst file that sets the printed
page. Quirework reads it and never writes it. It takes from it the faces
`publish fonts` checks and the text block `figures --check` measures
type against, and it names the file when figure numbers disagree across
editions, since its numbering rule is one place they are set.
`publish.figure_fonts` lists font files that every staged figure carries
as subsets, so a figure shown as an image is lettered in the book's face
on a device that lacks it; the [authoring guide](authoring.md#4-figures)
explains it. `publish.figure_type_floor_pt` sets the printed size, in
points, under which `figures --check` warns about a figure's type; it
defaults to 6. Annotation bindings resolve against the configured
semantic register; a book need not adopt another project's claims or
vocabulary.

`manuscript.spec_trail` names the spec's trail sidecar, which points
each spec section at the conversations that shaped it. It requires a
register, `manuscript.spec` or `manuscript.unit_spec`, and the file need
not exist yet: the first `spec trail add` creates it, readable by its
owner only. Put it in the provenance ledger with the other private
material, since it points into the private corpus by session id and
keeps working wherever the store lives. Leaving it unset turns the
trail off: no command reads or writes one, and the primer shows no
counts. The
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
seats and experiments run there are never swept into the corpus. `manuscript.noncanonical_roots` tells checks
which working paths have no authority. `manuscript.companion_dirs` names
reader-facing documents outside the canon. `publish.variants` names the
root for derived editions.

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
`spec units`. YAML is read for top-level scalars only, which is all
the fields Quirework reads need.

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
`ideas/the-tide-clock` names it as well as the brief's own path does;
an idea that is one file among others is named only by that file.

A published page owes its readers more than a draft does, so
`spec units --check` adds two advisories for published pages. An open
`@todo` on one is waiting on something a reader cannot see. A page
whose own spec was revised after the page was last dated says something
it no longer holds, until it is revised and its revision field records
it. The field is `lastmod` unless `manuscript.revision_field` names
another, such as `last_modified_at`. The second advisory reads the
history, so it needs git, and it says so when there is none. Writing a
spec for a post already published is not revising it: only a change
after the spec exists counts, and a spec not yet tracked is never
named. A book's chapters have no publication state of their own, so
neither advisory applies to them.

### A spec per unit

A book argues one thing, so it keeps one spec. A site argues one thing
per post, so each post can state its own commitments in its own file:

```toml
[manuscript]
spec = "specs/_site.md"
unit_spec = "specs/{slug}/spec.md"
```

`unit_spec` is a path pattern with `{slug}`, relative to the project
root. `spec` becomes the shared file, holding what several units lean
on, and either setting may stand alone. Together they are one register.
The shared file is read first, then each unit's own in reading order,
then the specs of units not written yet, since a post is often
specified before it is drafted. `spec notes --check` reports those as an
advisory, because a mistyped slug looks the same.

A section of the shared spec is named by its anchor, and a section of a
unit's own spec by the unit and its anchor. Two posts can each write
`## The turn is the message {#thesis}`, and the register holds
`why-the-lamp-turns/thesis` and `what-the-fog-signal-is-for/thesis`. An
anchor is unique within its file, a shared anchor across the shared
spec, and no anchor contains `/`.

A unit binds every section of its own spec without saying so. It is the
post those commitments describe. A `@bind` in its sidecar still says
which heading carries a commitment, and there the bare anchor is
enough: `@bind(thesis)` in a post's own marks means that post's thesis.
Everywhere else a unit's section is named in full, which is how another
post comes to share the claim: `@bind(why-the-lamp-turns/thesis)`. A
post's anchor may not repeat a shared one, since a bare name in its
marks could then mean either. `spec notes --primer -c SLUG` lists only
what that unit touches: its own spec whole, and the shared sections it
binds.

One trail serves the whole register, because breadcrumbs are kept by a
section's full name. `spec trail why-the-lamp-turns/thesis` reads one,
and `spec trail suggest` follows the history of whichever file holds
the section.

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
fixed file (`"research/*/notes.md"`). A file's title is its
frontmatter `title` or its first heading. A `README.md` and a
`_`-prefixed file are not sources.

A mark cites a source as `<!-- @source(slug) what it supports -->`.
`spec notes --check` refuses a slug the register does not hold, and two
sources with one slug. A `@source` with no slug stays free prose, since
a passing reference need not be registered. `spec sources` lists each
source with the units that cite it; `--slug S` and `-c UNIT` narrow
the list, and `--check` adds two advisories: a source no unit cites,
and a quotation that is not in the source.

`source_copies` is a path pattern with `{slug}` naming a plain-text
capture of each source as it was read. A quoted span of five words or
more in a mark citing that source is checked against it, with case,
whitespace and closing punctuation ignored. A source without a capture
is not checked, and `--check` says how many quotations that left.

## Publishing backends

`publish.backend` names what renders the manuscript. `quarto`, the
default, stages a book and renders HTML, a Typst PDF, EPUB and DOCX,
and every other `publish` command is built on that staging: figures,
fonts, red-pen copies, reader packets, derived editions and chapter
comparisons.

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

A target reads its own table laid over `[publish]`, so a key it
declares replaces the shared one, and a relative path in it resolves
from the file that declares it. `publish.backend` is then not set: each
target names its own. A project without `targets` has one, named for
its backend's product: `book` for Quarto, `site` for Hugo.

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
machine shares, and today that is only `provenance.home` (see
[Provenance](#provenance)). A key it does not read is refused, as in a
project's file.

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
directory `provenance.root` names, committed with the work. It holds
the decisions about the sessions: which were accepted, which were
removed and why, which are about the apparatus, and the catalog built
from them.

`provenance.store` names the store, and Quirework looks for it under
the *provenance home*. The home is `$QUIREWORK_PROVENANCE_HOME` when
that variable is set. Otherwise it is `provenance.home` in your
per-user configuration, and without that it is
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
for the store in the default home instead. A file reaches every
process, however it was started. The variable stays useful for an
override, such as a scratch home for one command.

Nothing copies the store off the machine until you describe a backup,
and Claude Code deletes its own transcripts after 30 days by default.
After that the store is the only copy. [Backing up the
store](#backing-up-the-store) shows how to keep another. `quirework
config check` and `quirework provenance store status` name where the
store is, which setting chose its home, whether a repository holds it,
and how it is backed up.

`quirework provenance store init` creates the store and records its
identity in the ledger's `accepted.txt`. A ledger that already names an
identity keeps it. On a second machine or in a fresh clone, init
creates an empty store under that identity, and `store verify` lists
the accepted files it lacks until you copy them in. A store another
project created under the same name is refused, so two projects never
read each other's sessions.

That same identity is why init is the wrong answer when a store goes
missing on the machine that made it. If the ledger lists accepted
files and no store is found, the store usually still exists, and
Quirework was looking under a different home, often from a hook that
never saw the variable your shell exports. Init there would create a
second, empty store under the same identity, and captures from that
process would land in it unnoticed. So the error names the home it
looked in and the setting that chose it, and it tells you to point the
home at the store rather than to run init. It suggests init only when
the ledger lists nothing yet. Init itself, when it creates a store for
a ledger that already lists files, names the home too and says what to
do if that store belongs elsewhere.

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
session modified in the last two hours may still be running, so
`accept` refuses it unless `--live` says to take what was captured so
far. `accept --all` skips such sessions and names them.

`store status` also lists the Claude Code sessions that no capture
holds as they stand: one never captured, or one that grew after its
last capture. It finds them by running the capture's own selection
without writing anything. A session there that is still being written
is marked as possibly running, and its hook captures it when it ends.
One that has stopped changing was missed, usually by a capture hook
that failed as its session closed, where nobody saw the error.
`quirework provenance ingest claude` captures it. Do that within 30
days of its last change, because Claude Code deletes the transcript
then by default. Codex and Antigravity are captured by hand, and their
sweeps report what they captured, so status does not list theirs.

A session that grows after it was accepted is captured again as
pending. Accepting it then adds a new version beside the old one. A
branch whose ledger still lists the old version keeps reading exactly
the bytes it accepted, so accepting on one branch never breaks the
checks of another.

The catalog, search, metrics and the cold-read read accepted sessions
only. `show` and the spec trail also read pending captures, so a
session can be read before it is accepted; `show` says so in its source
line. `coldread triage` sorts the pending captures by whether they
edited the work.

`reject` deletes a pending capture, or drops an accepted session from
this ledger, and lists the session in the ledger's
`removed-sessions.txt` with the reason. Capture never brings back a
session on that list. The ledger's `making-sessions.txt` lists sessions
about the apparatus rather than the work. Both lists hold one session
per line, as `<kind> <session id>  # reason`, where the kind is `cc`,
`codex`, `agy` or `web`.

`quirework provenance store verify` hashes every accepted file and
names any that is missing or no longer matches `accepted.txt`. A
command that needs a missing file refuses to run rather than read a
smaller corpus.

### Backing up the store

Claude Code deletes a transcript 30 days after it last changed, and
from then on the store holds the only copy of the session. A store
therefore describes its own backup, in a `backup.toml` beside its
`store.toml`, and `quirework provenance store backup` carries it out:

```toml
# ~/.local/share/quirework/provenance/my-book/backup.toml
[targets.drive]
driver = "rclone"
destination = "gdrive:quirework-provenance/my-book"
```

The file lives in the store rather than in `quirework.toml` because
what it names belongs to you and to this machine, such as an rclone
remote or a mounted disk. The project file is committed and shared, and
a clone elsewhere would name a remote that machine does not have.
Keeping the file in the store also lets one command back up every store
under the provenance home without opening a project. `quirework config
template backup` prints an annotated file to start from.

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
  damaged. The run stops and names the file rather than choosing a
  side. rclone compares checksums, and the directory driver compares
  sizes.
- A pending file is deleted there only once the version that replaced
  it has arrived.
- The first run writes the store's `store.toml` at the destination. A
  later run refuses a destination holding another store's identity, or
  holding files and no identity at all. A path copied from one store's
  file into another's therefore fails rather than mixing two corpora.
- The directory driver never creates its destination's top directory.
  A share that failed to mount leaves an empty mount point behind, and
  filling it would put the backup on the local disk. Create the
  directory once by hand.
- Names starting with a dot are never copied. They are the half-built
  directory of an accept in progress, a capture still being written,
  and a git backup's `.git`.

The order of a run is what keeps the second rule true while you work. A
run copies `pending/`, then `accepted/`, and then takes the lock that
`accept` and `reject` take. Holding it, the run uploads any version
accepted since it copied `accepted/`. Only then does it delete the
destination's pending files that the store no longer has.

Say you accept a session while a run is uploading. The run had already
copied the session's pending capture, and it missed the new version
because it listed `accepted/` before the version existed. Under the
lock it sees the version and uploads it, and only then removes the
pending copy. The session is at the destination throughout. A run that
fails partway has deleted nothing.

Pending files are deleted at all because `reject` deletes a capture on
purpose, and a backup should not keep a session you curated out. The
one loss this does not prevent is a pending file deleted by hand, which
the next run deletes at the destination too. Its original stays where
Claude Code keeps it until Claude Code prunes it, and the `git` driver
keeps such a file in its history.

#### Git and command targets

The `git` driver commits the store as it stands, under the store's
lock. It pushes `branch`, which is `main` unless set, to `remote` when
one is named. Cloning that repository restores the store. It costs
disk: the repository's objects roughly double the store's size, and a
pending capture that grows is committed whole again on every run. A
store that already sits inside another repository, as a `store_dir`
inside a project can, is refused, since that repository versions it
already. Commits carry your git identity, so a machine with none
configured fails the run with git's own message.

A `command` target runs its script from the store's directory with
`QUIREWORK_STORE`, `QUIREWORK_STORE_ID` and `QUIREWORK_BACKUP_TARGET`
set. Its exit status is the run's outcome. Quirework cannot see what a
script copies, so the rules above are the script's to keep, and it runs
without the store's lock. Because it runs unattended, the script must
be a regular file that you own, that is executable, and that nobody
else can write.

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
store's identity, so two stores never share one. `quirework provenance
store status` reads them. It shows each target's last success, any
failure since, and how many accepted versions and pending sessions
changed after that success. `quirework config check` reads
`backup.toml` and validates every target without reaching any. A store
with no `backup.toml` is reported as having no backup, unless a
repository around it already versions it.

The first run to a cloud remote can be slow, because the remote creates
files one request at a time. Google Drive takes a few files a second,
so a store of five hundred sessions takes about half an hour the first
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

Give `ExecStart` the absolute path of the `quirework` your projects
run, since a user service does not see your shell's `PATH`. The service
finds the provenance home in the per-user configuration file, which it
reads like any other process. `Persistent=true` runs a backup the
machine slept through as soon as it wakes. A failed run shows in
`journalctl --user -u quirework-backup` and in `store status`.

Restoring is a copy in the other direction. On a new machine,
`quirework provenance store init` creates an empty store under the
identity the ledger names. Copy the backup into it, for example with
`rclone copy gdrive:quirework-provenance/my-book <store directory>`,
then run `quirework provenance store verify`, which proves that every
accepted file came back intact. A `git` backup is restored by cloning
it to where the store belongs, with no init.

### What capture selects

Capture selects sessions by their recorded relationship to the project
and its worktrees. Codex capture also excludes runtime subagents and
delegated evaluation seats whose recorded tool calls name a raw capture
in the store. It reports those exclusions. The ingest's main session
remains eligible, as does a delegated task that only discusses a
capture in prose. A store directory's bare name, such as
`cc-sessions/<id>.jsonl`, counts only where it starts a path, so a
matching directory name inside another project's absolute path does not
establish an evaluation of this project's corpus.

Capture reads each harness's transcripts where the harness keeps them:
Claude Code's under `~/.claude/projects`, Codex's under
`$CODEX_HOME/sessions` (normally `~/.codex/sessions`), and
Antigravity's under `~/.gemini/antigravity-cli/brain`. A harness set up
with another home needs capture pointed at it; otherwise the sweep stops
and names the directory it looked in. `providers.claude.sessions` names
Claude Code's directory, which `spec trail add --find` also reads, and
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

A harness logs its own text in the human's slot too, and none of it
counts as a turn of the author's: slash-command and system-reminder
wrappers, the report a background subagent sends back, and the summary
a compacted session resumes from. Search, show, the trail and the effort
metrics apply the same rule, and `show --raw` labels that text
`harness`. The same rule covers the notice Claude Code writes in the
model's slot when a request fails ("API Error: …"): it is the
harness's, not the model's, so a turn it follows reads as unanswered,
and a blind cold-read never sees it as something the model said.

A spec trail reads sessions only where provenance is enabled. There,
`spec trail add --find` also reads this project's live Claude Code and
Codex transcripts, from the same locations capture sweeps, and never
writes to them. That is how a turn in a conversation still going on is
pointed at before the session is captured. Without provenance, a trail
can still be kept by hand and read as pointers.

`provenance.redpen` names the archive of red-pen rounds that
`publish redpen archive` writes: your marks on a rendered copy of the
book, one file per round. Declared, each round also reads as a session
of your turns, one per mark, named `redpen-` and its file name, so
`show`, the trail and its backfill reach words you typed on the page as
well as in a conversation. A round records the day of each mark and no
time of day, so a breadcrumb names the mark by its number. Nothing joins
by default: the archive is private, and an undeclared one stays out.

### Blind cold-reads

`quirework provenance coldread` puts accepted sessions in front of blind
analysts and keeps what they report. What it keeps lives in
`provenance.coldread`: the reports, cached by the content they read so
an unchanged session is never paid for twice; the manifest that indexes
them; and the analyst briefs the project writes, `analyst-prompt.md`
and, for sessions about the apparatus, `analyst-prompt-making.md`. It
defaults to `coldread` inside the ledger, so the cache travels with the
decisions it was made from.

The working copies are rebuilt on demand and default to the project's
cache. `provenance.rendered` holds the filtered sessions staged for the
analysts, one directory per scope. `provenance.reports` holds each
report as it arrives and before it is recorded, including any
quarantined because its analyst did not declare itself blind. Set
them only to keep these somewhere other than the cache.

An analyst's copy is filtered so that it cannot learn whose project it
is reading. `provenance.redaction_policy` names a TOML or JSON file
whose `markers` list the project's distinctive framing: its title, its
author's name, a phrase that would hand the analyst the conclusion it
is meant to reach. `provenance.redaction_markers` lists them inline
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
cache directory, and `project.cache` moves it. Run storage defaults to
a project-specific directory under the XDG state directory. Configure
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

`quirework sop blind prune` collects the other half. Every confined seat
gets a private runner home beside its workspace, bound over the real one
so the seat cannot reach the operator's credentials or memory. Those
homes are large and nothing else removes them: one measured room held 92
MB of them against 1.6 MB of records. Pruning keeps every record,
receipt and transcript, because a home's native transcripts have already
been read into the receipts beside it and `launch.json` records the
home's path whether or not it still exists. It refuses a home whose seat
still has a live process, and by default keeps anything touched in the
last hour, since a seat paused between turns looks idle.

It removes entries under the cache directory that have gone untouched
for 30 days, leaving anything a current project still reads. It creates
nothing, so it is inert until Quirework has cached something, and it
never touches the state directory, where run records live. A
distribution package installs the same file as
`/usr/share/user-tmpfiles.d/quirework.conf`, which a user's own copy in
`~/.config` overrides.

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
its work. And a procedure read straight off disk never reaches
Quirework at all. A generated skill therefore routes the companion
through `quirework sop show NAME`, which prints the procedure verbatim
and counts the read; a companion that opens the file anyway, or a
person reading it in an editor, is not counted, so procedure counts
remain a floor rather than a total.

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
- `publish.render_policy`: `print_drops` and `epub_style_headers`
  requiring CSS-comment cleanup in staged EPUB headers.
- `text.terminology`: `terms`, `retired`, `allow`, `scope_glob`, and
  `exclude_names`. A term warns and waits for the author's ruling; a
  `retired` phrase is a ruling already made, so `text check` fails while
  one is back in reader-facing prose. Without `scope_glob`, the scope is
  the manuscript's prose files.
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
- `publish.variant_policy`: the selected model and optional assignment.
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
authority, which is the guarantee these three settings buy.

Each Markdown file directly inside a companion directory is an entry,
except a `README.md`, which indexes the collection, and a file whose
name starts with `_`, such as a template. An entry names what it rests
on in a visible run in its header paragraph, labelled `**Anchors:**`
unless `manuscript.companion_anchors` names another label:

```markdown
**Anchors:** `@unit(why-the-lamp-turns)` `@bind(lamp-period)`
```

A unit is named by `@unit(slug)`, by its bare slug, or by its file as
the format writes it (`opening.qmd`, `posts/why-the-lamp-turns/index.md`).
Each `@bind(slug)` after it must be a section that unit binds.
`spec notes --coverage` then maps which commitments the entries work
through.

### Where the work lives

Several tools ask whether a file is part of the work: the catalog and
`coldread triage` when they split a session's edits into writing and
tooling, `sop review audit` when it lists commits a reviewer must see,
`provenance metrics` when it follows the history of the prose, and
`text check` when no `scope_glob` names its scope. They share one
answer, built from what the project already declares:

- the units and their marks, as the format lays them out: a book's
  whole source, or a site's pages, their bundles and `manuscript.marks`;
- the register: `manuscript.spec`, each `manuscript.unit_spec`, and the
  references and review notes;
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
its check-suite function as `module:function`. `text.classifier` selects
a function returning `sentence`, `column`, `trailing`, or `skip` for a
source path, for a rule `text.modes` cannot state; a project uses one or
the other. The function receives an absolute path, and
`quirework.text.mdwrap.project_relative` turns that into the path as the
project names it, which is what a rule about location needs.
`publish.grayscale_transform` accepts SVG text and its asset name and
returns transformed SVG text. `publish.figure_checks` is a list of
functions returning diagnostic strings. Extensions belong to the
consumer's package and must be installed explicitly.

`providers.codex.catalog` pins the Codex model catalog to a file,
which is then used as given instead of asking `codex debug models`.
Leave it unset unless a run must resolve against a frozen list. Claude
Code keeps no local catalog, so `quirework sop models list claude`
prints the list `providers.claude.models` declares. It is a reference
for the project's own choices and constrains nothing: a Claude seat
passes its family or version to the CLI, and the receipt records which
model answered.

Every launch proves its seat blind before the seat starts. A runner
release can change what a flag does without rejecting it, and only a
live seat can say what it holds, so the launch canary spends one short
exchange asking it what it can read, which tools it has and what
instructions it was given. It runs the seat's own command, rebuilt by
the launcher that will start the seat, with the canary's prompt, model
and session in its place. A passing canary is recorded under the
runner, its exact installed version, a fingerprint of that command, and
what the seat may hold on purpose: nothing of the project for a blind
seat, the repository and its guidance for a review, the web for a
research seat. A later launch with the same key starts at once. A new
release of any size, or a change to the seat's flags, runs the canary
again first. A seat that holds more than its contract allows, or a
canary whose answer proves nothing, refuses the launch, and the output
is kept under `~/tmp` for inspection.

The records are per machine, in `providers/canaries.json` under the
project's cache, because blindness is a property of the runner installed
here. Each launch writes the record it relied on into its own receipt,
`launch.json` or the blind receipt, so the evidence stays with the run.
The cold-read and the variant drafts keep no launch receipt; they print
the canary instead. `quirework sop blind canary` runs the canary on
demand under the shared blind flag set, the set a command written by
hand for `sop blind run` is proven under, and `sop blind check` reports
which releases have been canaried without failing on one that has not.
`providers.validation_record`, which kept a record in the repository and
failed `check` on a minor version change, is retired, and a project
that still sets it is refused with that explanation.

`QUIREWORK_CANARY=0` forbids live canaries in one process, so any
launch there that has no record is refused. A test suite sets it: a test
that drives a launch without stubbing the gate would otherwise reach a
real runner and spend a real model's tokens. Quirework's own suite sets
it in its `conftest.py`, and a consumer's suite should do the same.

The canary runs on the cheapest model each runner offers.
`providers.canary_models` names that model per runner, as in
`canary_models = {claude = "haiku"}`. Without an entry, Antigravity's is
picked from `agy models`, and any other runner's canary refuses to run
rather than fall back to the account's default, which may be its most
expensive tier.

Provider account leases have a separate scope from project data. Use the
same `providers.agy.account` identity for projects sharing an account.
`providers.agy.state` can select an existing shared lease directory.
Changing a project or worktree does not create a second account quota.

The publisher controls each staged render's `_site` output directory.
Quarto publication settings and reading order remain source-owned, while
build output stays inside the configured build tree.

## Annotated templates

`quirework config template book` prints a commented configuration for
a Quarto book, and `template site` one for a site of Markdown pages
built by Hugo. Use `template run` for a tiny model plan,
`template terminology` for an advisory word policy, `template tells`
for a machine-habit policy, or `template tmpfiles` for the
cache-cleanup rule above. Add `--out PATH` to create a new file; an
existing file is never overwritten. Every template is bundled in the
wheel.

Run plans accept TOML or JSON. Preparation still freezes canonical JSON
records, so saved runs and their integrity checks retain their format.
The comments explain choices and do not enter the model assignment.

A book can define `sops.default_reader` and named `sops.reader_presets`.
Each preset selects an `instrument` and `include_matter` boolean. The
instrument supplies its own default persona. An explicit `--instrument`
or `--persona` overrides that selection for a prepared book read.

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

Schema 1 kept everything about the written work in one `[book]`
section. Schema 2 splits it by job, and a schema-1 file is refused with
a pointer here rather than read with guesses. Set `schema = 2`, then
move each key to the section this table names. The key names do not
change.

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
