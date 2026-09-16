# Project configuration

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
generated skill, for a project whose guide is not `AGENTS.md`. Setting it
to an empty string leaves the generated skills pointing only at the
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

## Project policy and extensions

Policy files are commented TOML data referenced by configuration.
Existing JSON policy files remain readable. They keep project vocabulary
and visual choices out of Quirework's implementation:

- `book.chapter_policy`: `retired_tokens`, `metadata_layers`, and
  `identity_layers`.
- `book.layout_policy`: `typst_prologues` and `typst_epilogues`.
- `book.render_policy`: `print_drops` and `epub_style_headers` requiring
  CSS-comment cleanup in staged EPUB headers.
- `text.terminology`: `terms`, `allow`, `scope_glob`, and
  `exclude_names`.
- `sops.persona_policy`: the optional `default` persona ID.
- `sops.review_policy`: review frames and reviewable source paths.
- `book.variant_policy`: the selected model and optional assignment.
- `provenance.metrics_policy` and `provenance.coldread_policy`:
  historical source classifications and explicitly selected analysis
  models.

A project can select installed Python extensions. `checks.command` names
its check-suite function as `module:function`. `text.classifier` selects
a function returning `sentence`, `column`, `trailing`, or `skip` for a
source path. `book.grayscale_transform` accepts SVG text and its asset
name and returns transformed SVG text. `book.figure_checks` is a list of
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
Use `template run` for a tiny model plan or `template terminology` for
an advisory word policy. Add `--out PATH` to create a new file; an
existing file is never overwritten. Every template is bundled in the
wheel.

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
