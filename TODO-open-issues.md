# Open issues carried from a consuming project's sessions

Problems seen while running Quirework from a book project, kept here
until a session takes them up. Each states the problem and what a fix
would need to do, not how to do it. Some were seen weeks ago, so check
that each one still holds before working on it, and delete its entry
when it is fixed or no longer applies.

## Some mechanism tests depend on one project's configuration

Several tests of Quirework mechanisms run in a consuming project and use
that project's configuration as their data: the SOP and catalog tests,
the prose-workshop tests, the codex session-sync tests, the path tests,
the cold-read triage tests and parts of the review tests. Under an empty
project they fail (about 50 failures and 36 errors when last measured).
Requirement: each runs against a synthetic project, so the mechanism is
tested without any one book's configuration.
