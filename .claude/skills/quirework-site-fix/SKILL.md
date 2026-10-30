---
name: quirework-site-fix
description: "Make a change to quirework.com alone, and deploy it from main between releases."
disable-model-invocation: true
argument-hint: "[what to change on the site]"
---

Website-only change: $ARGUMENTS

Read `site/AGENTS.md` in full, and `site/README.md`, section "Deploying
between releases". The first holds the rules every change to the site
follows. The second is the procedure, and this skill only says when to
stop. Read both each time, since either may have changed.

A website-only change touches files under `site/` and nothing else. If
it needs the tool, an example or a workflow, say so and stop: that work
is not this procedure, and a release is how the tool reaches the site.

Carry out step 1 without asking. When the request leaves the wording or
the place unclear, ask once before editing. A failing build or check
stops the procedure there: report it, and do not commit.

Then stop once, before anything leaves this machine, and show:

- the diff, or for a long one a summary and the rendered passage;
- every commit `git push origin main` would carry, marking any that is
  not this change;
- whether the tool on `main` differs from the last tag, with the
  section's `git diff --stat`. A deploy from `main` renders the sample
  book with `main`'s Quirework.

Ask with AskUserQuestion. A go authorizes pushing `main` and running the
site workflow from `main`. It never covers a tag.

After the go, carry out steps 2 and 3. Report the run job by job, and
check that quirework.com serves the change.
