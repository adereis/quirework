# Backfill a trail

<!-- @skill: Point each spec section of Chapter and Verse Control at the
     turns in logs/ that shaped it, one section at a time. -->

A trail is a list of pointers, one for each turn of the author's that
shaped a spec section. This procedure fills in the pointers the spec did
not get while it was being written. It is a reading job. Every
breadcrumb is a judgment about what one turn did to one section, so it
goes to a strong model and never rides along with capturing sessions,
which is mechanical.

Work one section at a time, in the order `quirework book trail` lists
them, or only the sections the human named.

For each section:

1. Read the section in the spec, then its trail so far with
   `quirework book trail SLUG`.
2. Gather leads. `quirework book trail suggest SLUG` follows the commits
   that changed the section to the sessions that made them. Then search
   the author's own words for the idea:
   `quirework provenance search WORDS`, and
   `--semantic "the idea in plain words"` when the words have changed
   since.
3. Read every lead where it happened, with
   `quirework provenance show ID --turn N`. Read the turn before it too
   when the author was answering a proposal, because "no, the other one"
   means nothing alone.
4. Add a breadcrumb for each turn that seeds, sharpens, corrects,
   reverses, accepts or probes what the section holds:
   `quirework book trail add SLUG --kind KIND --session ID --turn N --note "..."`.
   When in doubt, add it. Point at the author's turn, not the reply.
   Write the note in your own words, and add an `--excerpt` only when
   the words that matter are buried in a long turn.
5. Leave every breadcrumb that is already there. A position the author
   moved off is half of the story, so answer it with a `reverse`, not a
   deletion.

Run `quirework book trail --check` after each section and
`quirework book notes --check` before stopping.

Report, section by section: how many breadcrumbs you added, the story in
one sentence (where the idea started and where it is now), and every
lead you left out, with the reason. A lead you were unsure about is the
human's call.
