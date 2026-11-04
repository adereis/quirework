# Open issues carried from a consuming project's sessions

Problems seen while running Quirework from a book project, kept here
until a session takes them up. Each states the problem and what a fix
would need to do, not how to do it. Some were seen weeks ago, so check
that each one still holds before working on it, and delete its entry
when it is fixed or no longer applies.

## The live capture rules are tested only in a consumer

About 40 tests of the capture rules (guardian skip, archived codex
rollouts, review runs, `--force`, unreadable transcripts) live in a
consuming project's suite, rewritten there for the store. For most of
those rules they are the only coverage anywhere. Requirement: Quirework
covers its own capture rules in its own suite.

## Some mechanism tests depend on one project's configuration

Several tests of Quirework mechanisms run in a consuming project and use
that project's configuration as their data: the SOP and catalog tests,
the prose-workshop tests, the codex session-sync tests, the path tests,
the cold-read triage tests and parts of the review tests. Under an empty
project they fail (about 50 failures and 36 errors when last measured).
Requirement: each runs against a synthetic project, so the mechanism is
tested without any one book's configuration.

## Trail import matches flags against a pending capture

Importing a trail reader's flags matches them against sessions through
the corpus's captured view, which includes a capture still pending
review. Requirement: decide whether a flag on a session not yet accepted
is held until acceptance, and make the import say which it did.
