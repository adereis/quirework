---
name: quirework-release
description: "Release Quirework: bump the version, verify, and push the tag that deploys the site and publishes to PyPI."
disable-model-invocation: true
argument-hint: "[version, such as 0.2.0]"
---

Release Quirework $ARGUMENTS.

A pushed `v*` tag deploys quirework.com and publishes that version to
PyPI, where its number can never be used again. This runs only because
the person typed `/quirework-release`.

Read `docs/development.md`, section "Releasing", in full, and follow its
list. It is the procedure, and this skill only says when to stop. Read
it each time rather than from memory of an earlier release, since it may
have changed.

The version may be given as `0.2.0` or `v0.2.0`; the tag is always
`v0.2.0`. If none was given, propose one from the commits since the last
tag and say why. The number is the person's call.

Carry out steps 1 to 3 without asking. A failing test or check stops the
release there: report it, and do not commit the bump.

Then stop once, before anything leaves this machine, and show:

- the version, the tag, and PyPI's 404 for that number;
- the bump commit, and every commit `git push origin main` would carry;
- the commits since the last tag, by subject;
- the suite's counts and the result of `quirework dev check`.

Ask with AskUserQuestion, naming the tag in the option that pushes it.
Choosing that option authorizes pushing `main` and that one tag.
Approval of the plan in general, or of an earlier release, does not.

After the go, carry out steps 4 and 5. Push the tag by name, never with
`--tags`. Report the run's outcome job by job. On success, confirm that
PyPI lists the version with both files and that quirework.com answers.
On failure, say which job failed and whether any file reached PyPI. Then
propose the section's recovery: moving a tag needs its own, explicit go.
