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

[book]
source = "manuscript"
chapters = "manuscript/sections"
figures = "manuscript/sections/illustrations"
build = "build"

[sops]
root = "procedures"
catalog = "procedures/catalog.json"
personas = "profiles"
instruments = "questions"
panels = "panels"
skill_prefix = "notebook-"
```

The book source directory contains its `_quarto.yml`. Book settings can
also name `spec`, `references`, `review_notes`, and `typst_template`
when the project uses those features. Annotation bindings resolve
against the configured semantic register; a book need not adopt another
project's claims or vocabulary.

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
root = "private/provenance"
conversations = "private/provenance/conversations"
claude_sessions = "private/provenance/cc-sessions"
codex_sessions = "private/provenance/codex-sessions"
agy_sessions = "private/provenance/agy-sessions"
coldread = "private/provenance/coldread"
```

Capture selects sessions by their recorded relationship to the project
and its worktrees. Capturing does not curate, stage, or commit sessions.
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

It removes entries under the cache directory that have gone untouched
for 30 days, leaving anything a current project still reads. It creates
nothing, so it is inert until Quirework has cached something, and it
never touches the state directory, where run records live. A
distribution package installs the same file as
`/usr/share/user-tmpfiles.d/quirework.conf`, which a user's own copy in
`~/.config` overrides.

## Project policy and extensions

Policy files are commented TOML data referenced by configuration.
Existing JSON policy files remain readable. They keep project vocabulary
and visual choices out of Quirework's implementation:

- `book.chapter_policy`: `retired_tokens`, `metadata_layers`, and
  `identity_layers`. The metadata layers default to the sidecar
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
`runs.evidence_roots` names every directory holding saved run records,
including locations a project has since moved away from but still reads;
a project that keeps one needs only `runs.reviews`. Files inside a run
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
