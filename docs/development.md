# Architecture and verification

Quirework has one distribution and one namespace. Command discovery is
standard-library argparse and loads domain implementations lazily.
Parsing and presentation stay outside the reusable execution engine.

| Namespace | Responsibility |
|---|---|
| `manuscript` | Formats and units, spec, marks, sources, quotations, trail, lifecycle, footprint: the written work in any format |
| `publish` | Backends and targets; for Quarto, rendering, figures, editions, rendered review artifacts |
| `text` | Formatting and editorial inspection |
| `provenance` | Runtime readers, capture, the session store, its ledger and its backups, catalog, retrieval, measurements |
| `sops` | Procedure discovery, personas, instruments, workflow preparation |
| `runs` | Execution state, recovery, checkpoints, evidence |
| `providers` | Native invocation, model resolution, receipts, isolation |
| `delivery` | Frozen material transforms and turn delivery |
| `core` | Artifact classification, check dispatch, pinned tools, usage records, path globs, frontmatter |

Some operations need a program too large to vendor: an EPUB validator,
an e-reader renderer. `core.pinned` fetches one by version, URL and
SHA-256 into the shared cache, hashing the archive before anything is
unpacked so a substituted download never lands as an executable. A
consumer keeps its own pin and calls `pinned.ensure`; a tool Quirework
does not ship a command for is not Quirework's version to choose.

A book or a site depends on Quirework. Quirework never imports a
project's extension unless that project explicitly selects it. A format
(`manuscript.formats.Format`), a backend (`publish.backends.Backend`)
and a backup driver (`provenance.backup.Driver`) are objects registered
by name, and a project or a store may select its own as an extension;
everything above them asks the selected one rather than testing which it
is. A backup driver that copies file by file subclasses `Transfer`,
whose `run` alone decides the order of copies and deletions at the
destination, so a new driver cannot get that order wrong. Which files
are the work is answered once, by `manuscript.footprint`. The execution
and provider layers import neither `manuscript` nor `publish`. Executor
snapshots include their runtime dependencies without copying publishing
code or book content.

## The jail

`providers.blind` confines every seat with bubblewrap, and it is an
allow-list all the way down. The home is blanked and a list of paths is
bound back. The root is the same: a fixed set of system trees bound
read-only at their own paths, and nothing else. Do not reintroduce a
whole-root bind or a deny-list of filesystem types. Every earlier repair
to the root followed from that shape: a stale network share failed the
bind, the mount table changed under the jail, and a read-write root left
writable paths behind the blanked set. A runner that needs a tree
outside the list fails at launch; add the tree to `OPTIONAL_TREES`, do
not bind `/`.

`/run/user/<uid>` is never bound, because the session bus there lets
`systemd --user` start a process outside any sandbox. A runner that must
reach a bus name declares it in `Runner.bus_names`. The caller then
opens `blind.session_bus` around the launch, passes it to the jail, and
starts the process with `blind.popen`, which hands bwrap the proxy's
pipe. The proxy lives exactly as long as the jail.

## Configuration lifetime

A `Project` is immutable. Public entry points select it explicitly.
Internal helpers use an operation-local context variable rather than
mutable module-level paths. The context is restored on exceptions.
Policy data is read once per project instance and returned as isolated
copies. Load a new instance to observe edits made after that snapshot.
Thread submissions capture the operation context explicitly. Subprocess
and frozen-run boundaries serialize the selected settings. Package
installation paths are used only to locate implementation resources,
never the book.

## The commit gate

Enable the hook once per clone. Git does not run a hook out of the
working tree on its own, so a hook nobody enables is the same as no
hook:

```bash
git config core.hooksPath hooks
```

`hooks/pre-commit` runs `quirework dev check --staged`, which parses
every staged Python file and asserts that `ruff format` would leave it
alone, and that `quirework text wrap` would leave every staged Markdown
file alone. `ruff` must be on `PATH`; it is a contributor tool rather
than a package dependency, so a standard install does not supply it.

The hook refuses the commit when it cannot run those checks, whether
Quirework is not installed or the formatter is missing. A gate that
passes when its tools are absent is the gate this repository already
had. That is stricter than `dev check` itself, which tolerates a missing
formatter because a consuming book need not use one; this tree declares
that it sits at the formatter's fixed point and cannot assert that
without the formatter. While the tree is under heavy development the
friction of a blocked commit is worth more than the drift.

The tree sits at the formatter's fixed point, which is what lets a
contributor run `ruff format` out of tidiness and get an empty diff. The
gate exists because that invariant was not holding on its own:
formatting drift arrived inside ordinary feature commits and was swept
up later by commits that did nothing else, twice in the first
seventy-seven. A sweep is not a fix, since the drift is invisible in
review and the sweep hides the next one.

The guides sit at the wrapper's fixed point for the same reason. A guide
off it is reflowed whole the first time somebody wraps it to fit a
one-paragraph edit, so the edit arrives inside hundreds of lines that
are not the edit. Each example under `examples/` is a project of its
own, with its own `quirework.toml` and its own wrap modes, so the check
leaves its files to it rather than judging them by this tree's rules.

`--no-verify` skips the hook for a commit that genuinely should not be
gated. `quirework dev check --list` describes the suite without running
it. A consuming book selects its own suite with `checks.command`, and
the hook then runs that instead.

## Tests

```bash
.venv/bin/python -m pytest
```

The suite refuses to run against any Quirework but this checkout's. A
shell with a consuming book's environment active would otherwise import
the book's pinned copy, test it, and pass.

Each session writes its scratch files under a directory of its own,
`~/tmp/quirework-tests-*`, and cleans up after itself. A passing test's
files go as soon as it passes, and a passing session removes its whole
directory when it ends. A failing session keeps only the failed tests'
directories, beside the session's own home, for inspection. The next
session sweeps any directory left more than three days. Passing
`--basetemp` opts out of all of it, since that directory is the caller's
to keep.

Every key the code reads, in `quirework.toml` or in a policy file, is
listed in `src/quirework/settings.py`, which is what `config keys` tells
a project. The suite records every key read through a project and fails
the session on one the list lacks, so a new setting is listed in the
commit that reads it.

The suite loads `quirework.testing`, the plugin a consumer's suite opts
into, so the two are isolated the same way. Each session gets a home and
XDG trees of its own, and a test that writes into the contributor's real
ones fails. A test that needs a harness's login plants a fictitious one
in a home of its own, never reading the real one.

Unit tests use fictional text and diagrams. They check project
isolation, custom catalogs, formatting, artifact transforms, independent
seats, interrupted receipts, forks, quota recovery, and immutable
snapshots. A consuming book keeps its own integration tests for its
questions and project-specific visual rules.

Run a minimal unrelated book through rendering as an installation check.
Run the consumer's gates and relevant publication formats before
releasing a migration. These checks do not call a model.

Live tests require explicit authorization. Use inexpensive models, tiny
synthetic assignments, no automatic quota retries, and a hard wall-clock
limit. Development smoke tests must not receive book content or large
writing assignments. Count the provider's own prompt overhead as well as
the supplied material.

The suite holds one live test, marked `live`, and it skips itself unless
asked for:

```bash
QUIREWORK_LIVE=1 .venv/bin/python -m pytest -m live
```

It runs one seat per harness through the run engine, each on its
cheapest model at its lowest effort: agy's Flash, Claude's Haiku and
Codex's Luna. They are named as families, so the next release of each is
tested as itself. The seat reads a synthetic book of two short chapters
and a figure, writes a reaction to each, and edits one. The test then
checks that the receipt proves the reading and that the run's feed names
every file the seat touched. A harness release can break that without
breaking any unit test, because the unit tests' fixtures are written
from the records of the release before. agy 1.4.3 did so by renaming its
file tools. A turn stops at ten minutes, and a quota limit stops the
test rather than being waited out.

Each result is kept per machine, for the exact version it ran on, in the
clone's git directory, so every worktree shares it and no commit carries
it. The pre-commit hook warns, without blocking the commit, of each
installed harness whose version has not passed.
`.venv/bin/python tests/live_drivers.py` prints every harness's
standing.

Codex and Claude support explicit replacement system instructions. Codex
uses `model_instructions_file`; an unknown configuration key can be
silently ignored, so verify the native receipt and input usage. No-tools
Codex runs disable the shell, unified execution, apps, and local
image-view tool. Attached image delivery remains a separate capability.
Claude uses `--system-prompt` with dynamic system sections excluded.
Antigravity does not expose the same replacement control.

The supported Codex settings are documented in the
[official configuration reference](https://developers.openai.com/codex/config-reference/).
A requested setting is not evidence that the native client honored it.

Keep raw test sessions and runtime homes outside Git. Retain only
synthetic fixtures and the tests themselves. Record relevant validation
results with each release or integration change.

## Releasing

A release is a tag. Pushing `vX.Y.Z` runs
`.github/workflows/release.yml`, which builds the sdist and the wheel,
deploys the site, and then publishes the package to PyPI. PyPI never
accepts a version number twice, even after the release is deleted, so
whatever a tag publishes stays published under that number.

The site goes first because deploying it renders the sample book with
the tagged Quirework, and that render is the only check on a release
that exercises the tool end to end. A release whose book does not render
stops before PyPI, so the fix ships under the same number. A failed
deploy holds the publish back the same way, and "Re-run failed jobs"
resumes the run once the cause is fixed. A push runs no workflow at all;
`site/README.md` says how to deploy the site between releases.

The version lives in one place, `__version__` in
`src/quirework/__init__.py`, and the package metadata reads it from
there. The workflow refuses a tag that names any other version.

To release:

1. Start from `main` as GitHub has it: fetch, and rebase any local
   commits onto `origin/main`. No tracked file has uncommitted changes,
   and the tag will name a commit that everyone else can see.
2. Choose the number, which is the maintainer's call, and check that
   PyPI does not hold it: `https://pypi.org/pypi/quirework/X.Y.Z/json`
   answers 404. The workflow does not check this, and a taken number
   would deploy the site and then fail at the upload.
3. Set `__version__`, run the suite and `quirework dev check`, and
   commit the bump on its own. Its subject is "Release X.Y.Z", and its
   body says what the release brings since the last tag.
4. Push `main`, then tag the bump and push that tag alone:

   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```

5. Watch the run to its end (`gh run watch`). A release has landed when
   every job passed, PyPI lists the version with both files, and
   quirework.com answers. The site shows no version number, so its
   deploy job is the evidence of which commit it serves.

If the run fails before `publish`, nothing reached PyPI and the number
is still free. Fix the cause on `main`, then move the tag to the fix:
delete it on GitHub with `git push origin --delete vX.Y.Z`, tag the fix,
and push the tag again. If `publish` failed after a file reached PyPI,
that number is spent, and the fix ships as the next one.

In Claude Code, `/quirework-release X.Y.Z` runs this list
(`.claude/skills/quirework-release/`). It prepares everything through
step 3 and stops for the maintainer's word, naming the tag, before step 4.

The workflow holds no PyPI token. The project's trusted publisher on
PyPI names this repository, the file `release.yml` and the environment
`pypi`. PyPI accepts the short-lived identity GitHub issues to a job
that matches all three and nothing else, so a fork or another workflow
cannot publish. The `pypi` environment admits only `v*` tags, so a run
from a branch cannot either. Renaming the file or the environment needs
the publisher changed on PyPI first.

The build runs without isolation, against pinned versions of `build` and
setuptools, so every release is built by the same backend until a pin is
bumped on purpose. Its venv leaves out the runner's system packages,
unlike a contributor's, because setuptools prefers an installed copy of
a dependency over the one it bundles. The runner's `packaging` was older
than setuptools needs for an SPDX license, and it failed the first
release before anything was uploaded.

PyPI shows the README as the project's description, frozen with each
version. Its links are absolute for that reason: PyPI resolves a
relative link against pypi.org, where the guides do not exist.
