# Project configuration

The [authoring guide](authoring.md) walks a new book through these
settings in the order a project adopts them. This page is the reference.

A project supplies a versioned TOML file. Paths are relative to the file
that declares them. Explicit absolute paths and home expansion are also
supported.

```toml
schema = 1

[project]
name = "A Small Notebook"
scratchpad = "scratchpad"
private = "private"

[book]
source = "manuscript"
quarto = "manuscript/_quarto.yml"
chapters = "manuscript/sections"
figures = "manuscript/sections/illustrations"
build = "build"
variants = "variants"
companion_dirs = ["companion"]
noncanonical_roots = ["scratchpad"]

[sops]
root = "procedures"
catalog = "procedures/catalog.json"
personas = "profiles"
instruments = "questions"
panels = "panels"
skill_prefix = "notebook-"
```

`book.quarto` names the Quarto project file. Book settings can also name
`spec`, `references`, `review_notes`, and `typst_template` when the
project uses those features. Annotation bindings resolve
against the configured semantic register; a book need not adopt another
project's claims or vocabulary.

`project.scratchpad` and `project.private` document the two non-publishing
spaces. `book.noncanonical_roots` tells checks which working paths have no
authority. `book.companion_dirs` names reader-facing documents outside the
canon. `book.variants` names the root for derived editions.

`sops.root` names the procedure directory, so a project can call it
`procedures` or anything else; discovery and reading both follow the
setting. `sops.operator_guide` names the project's own guide in every
generated skill, for a project whose guide is not `AGENTS.md`. Setting
it to an empty string leaves the generated skills pointing only at the
procedure itself.

## Configuration layers

Quirework loads `quirework.toml`, then the optional
`quirework.local.toml` beside it. Keep the local file out of Git. It is
appropriate for private corpus paths, account identities, and existing
run-storage locations. Store credentials in the provider's native
credential mechanism, not in these files.

An explicit `--project` or `--config` selects a directory or
configuration file. `QUIREWORK_PROJECT` supplies a project selection for
subprocesses. A library caller can construct a `Project` and invoke
commands or use its operation context without changing the process
working directory.

```python
from quirework.project import Project, project_scope
from quirework.book.chapters import registry

project = Project.load("/path/to/a/book")
with project_scope(project):
    chapters = registry()
```

`quirework config show` prints effective settings and their origins.
`quirework config check` validates the configured components. An absent
optional corpus is different from an enabled corpus whose directory is
missing.

## Provenance

Enable a corpus explicitly. Different runtime formats have separate
source locations:

```toml
[provenance]
enabled = true
root = "private/corpus"
```

Runtime-specific subdirectories are derived from `root`. Declare an individual
path only when an existing corpus uses another layout.

Capture selects sessions by their recorded relationship to the project
and its worktrees. Capturing does not curate, stage, or commit sessions.
Codex capture also excludes runtime subagents and delegated evaluation
seats whose recorded tool calls name a raw capture in a configured
session directory. It reports those exclusions. The ingest's main
session remains eligible, as does a delegated task that only discusses
a capture in prose. The configured paths apply across project worktrees;
a matching directory name inside another project's absolute path does
not establish an evaluation of this project's corpus.
The catalog retains the existing tracked-source curation convention.
Search reads human-authored turns; provider metadata is not proof of
which model authored a historical turn.

Derived caches default to a project-specific directory under the XDG
cache directory. Run storage defaults to a project-specific directory
under the XDG state directory. Configure `runs.store` to retain an
existing private store and `runs.reviews` for reviewed output.

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

- `book.chapter_policy`: `metadata_layers`. The metadata layers default to the sidecar
  directories under `book.source`, so a project declares this only to
  scan something else.
- `book.layout_policy`: `typst_prologues` and `typst_epilogues`.
- `book.render_policy`: `print_drops` and `epub_style_headers` requiring
  CSS-comment cleanup in staged EPUB headers.
- `text.terminology`: `terms`, `allow`, `scope_glob`, and
  `exclude_names`.
- `sops.persona_policy`: the optional `default` persona ID.
- `sops.review_policy`: review frames and reviewable source paths.
- `book.variant_policy`: the selected model and optional assignment.
- `provenance.metrics_policy` and `provenance.coldread_policy`:
  historical source classifications, explicitly selected analysis
  models, and `output_counts`, which names each collection the metrics
  report counts and the glob that finds it.

Three settings describe a project's own placement, and all three default
to describing nothing rather than to another project's directory names.
`book.companion_dirs` names reader-facing collections beside the book,
whose quotations and spec citations `book notes --check` then gates.
`book.noncanonical_roots` names directories that hold working material
rather than authority, so a mark resting on one is reported.
`runs.evidence_roots` names every directory holding saved run records.
A project that keeps one needs only `runs.reviews`. Files inside a run
record are never reflowed by `text wrap` and never read as authority,
which is the guarantee these three settings buy.

A project can select installed Python extensions. `checks.command` names
its check-suite function as `module:function`. `text.classifier` selects
a function returning `sentence`, `column`, `trailing`, or `skip` for a
source path; it receives an absolute path, and
`quirework.text.mdwrap.project_relative` turns that into the path as the
project names it, which is what a rule about location needs.
`book.grayscale_transform` accepts SVG text and its asset name and
returns transformed SVG text. `book.figure_checks` is a list of
functions returning diagnostic strings. Extensions belong to the
consumer's package and must be installed explicitly.

`providers.codex.catalog` pins the Codex model catalog to a file,
which is then used as given instead of asking `codex debug models`.
Leave it unset unless a run must resolve against a frozen list.

Provider account leases have a separate scope from project data. Use the
same `providers.agy.account` identity for projects sharing an account.
`providers.agy.state` can select an existing shared lease directory.
Changing a project or worktree does not create a second account quota.

The publisher controls each staged render's `_site` output directory.
Quarto publication settings and reading order remain source-owned, while
build output stays inside the configured build tree.

## Annotated templates

`quirework config template project` prints a commented project template.
Use `template run` for a tiny model plan, `template terminology` for an
advisory word policy, or `template tmpfiles` for the cache-cleanup rule
above. Add `--out PATH` to create a new file; an existing file is never
overwritten. Every template is bundled in the wheel.

Run plans accept TOML or JSON. Preparation still freezes canonical JSON
records, so saved runs and their integrity checks retain their format.
The comments explain choices and do not enter the model assignment.

External corpora can declare `provenance.curation = "presence"` to
accept the files in that directory. Git-curated external stores instead
name a `provenance.tracking_root` that contains them. Format locations
remain explicit even when their directories have different names.

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

`providers.isolation_policy` can list `mount_markers`: path words that
would tell a seat whose book it is reading. A self-driven read refuses a
mount matching one. They are deliberately separate from
`project_markers`, which the blind canary matches against a seat's
answer. A book about consciousness wants that word to fail a mount path
and never to fail a verdict, and one list cannot do both.
