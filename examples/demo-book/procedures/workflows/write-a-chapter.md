# Write a chapter

<!-- @skill: Write or revise a chapter of Chapter and Verse Control,
     from the primer through the gates. -->

Read `quirework spec notes --primer` before touching any prose. It
prints the spec's commitments and every chapter's guidance and bindings;
know what a chapter is supposed to express before changing what it says.

Write or revise the one chapter the human named. Keep every rule in this
directory's `AGENTS.md`: no em-dash anywhere, present tense, dry and
concrete, short. If a paragraph can go, it goes.

When a chapter's content changes enough to add or drop a commitment it
expresses, update its `@bind` lines in its sidecar to match, and say so.
A `@bind` that used to be true and no longer is is worse than none.

Before changing what a commitment says, read how it got there:
`quirework spec trail --chapter NAME` for every section the chapter
binds, then `quirework spec trail SLUG` for the one you are touching.
The trail points at the author's own turns, oldest first. It is context,
never a ruling: what the human asks today wins. When the human's words
in this session change what a spec section holds, add a breadcrumb for
that turn with
`quirework spec trail add SLUG --kind KIND --find "words they typed" --note "..."`,
and say so.

Before calling the work done, run the gates in this order and fix
whatever they report:

1. `quirework spec units --check`
2. `quirework spec notes --check`
3. `quirework spec trail --check`
4. `quirework publish figures --check`
5. `quirework config check`

All five have to pass. None of them reads the prose for whether it is
any good; that judgment is the human's, and the primer is how you stay
inside it.
