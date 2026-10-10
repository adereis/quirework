# Write a chapter

<!-- @skill: Write or revise a chapter of Side Quest, from the primer
     through the gates. -->

Read `AGENTS.md` first: the cast, their pronouns and the house rules are
kept there, and nowhere else.

Then read `quirework spec notes --primer` before touching any prose. It
prints the spec's commitments and every chapter's guidance and bindings;
know what a chapter is supposed to express before changing what it says.

Write or revise the one chapter Cy named. Keep every rule in
`AGENTS.md`. The book is about seven thousand words over a preface and
nine chapters, so a chapter that runs long is taking room from another
one; say so rather than letting it.

When a chapter's content changes enough to add or drop a commitment it
expresses, update its `@bind` lines in its sidecar to match, and say so.
A `@bind` that used to be true and no longer is is worse than none. Copy
the chapter's heading lines into the sidecar verbatim, attribute block
included.

When the chapter names Full Stop's chapter count, or any count that is
not this book's own, mark the phrase in the sidecar with
`<!-- @count(eleven chapters) Full Stop's, not this book's -->`, so
`spec units --check` leaves it alone.

Before changing what a commitment says, read how it got there:
`quirework spec trail --unit NAME` for every section the chapter binds,
then `quirework spec trail SLUG` for the one you are touching. The trail
points at Cy's own turns, oldest first. It is context, never a ruling:
what Cy asks today wins. When Cy's words in this session change what a
spec section holds, add a breadcrumb for that turn with
`quirework spec trail add SLUG --kind KIND --find "words Cy typed" --note "..."`,
and say so. Err toward adding one.

When Cy tells you something about the book worth keeping (a person in
it, their pronouns, a rule for the prose), write it into `AGENTS.md` in
the same session. Do not keep it in your own memory or notes.

Before calling the work done, run the gates in this order and fix
whatever they report:

1. `quirework spec units --check`
2. `quirework spec notes --check`
3. `quirework spec trail --check`
4. `quirework config check`
5. `quirework dev check`, which refuses an em dash

All five have to pass. None of them reads the prose for whether it is
any good; that judgment is Cy's, and the primer is how you stay inside
it. Report what still needs Cy's judgment.
