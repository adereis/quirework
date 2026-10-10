# Continuity review: `#the-name` reversal

**Reviewed:** 2026-10-09, current worktree and index.  The index has no
staged paths.  I reviewed the working-tree diff for every supplied path,
the current canon, all three composed editions that bind `#the-name`, their
sidecars, `AGENTS.md`, and the applicable variant procedures.

## Findings

### F1. The young-readers prose defines `quire`, contrary to its sidecar and edition rules

**Status:** in the delta.

The new sentence is an explicit definition and adds an explanatory claim
about how a book is assembled:

> `variants/young-readers/the-name.qmd:31`  
> “A quire is a few sheets of paper folded together, and a book is sewn
> from them, one folded bundle after another.”

Its sidecar says the opposite about that treatment:

> `variants/young-readers/_marks/the-name.md:10-12`  
> “A quire is shown, folded sheets a book is sewn from, in the sentence,
> not defined.”

The young-readers procedure makes the rule unambiguous:

> `procedures/workflows/variant-young-readers.md:39-46`  
> “say what it is by what it does, in the sentence that uses it. No
> definitions, no glossary, no brackets, no aside to the reader.”

The master keeps the needed fact shorter and leaves it in the scene:

> `manuscript/sections/the-name.qmd:30`  
> “The last line on the page, at ten to two, was *quire*: the folded
> gathering of pages a book is sewn from.”

Thus the composed young-readers text both contradicts its own guidance and
breaks the edition's no-definition rule.  “One folded bundle after another”
is also an added explanatory fact not stated by the master.

### F2. The changed poem stanzas do not hold anapestic tetrameter

**Status:** in the delta.

The assigned form is not optional.  The poems procedure assigns *The Name*
“anapestic tetrameter couplets” and requires: “Hold the metre through the
poem and make it scan” (`procedures/workflows/variant-poems.md:55,63-64`).
The local sidecar likewise calls for “anapestic couplets, loose as light
verse allows” (`variants/poems/_marks/the-name.md:9-10`).  The procedure
does allow a verbatim quotation to break metre, but the following newly
written lines are not quotations and still scan as prose rather than four
anapests:

| Changed line | Why it fails the assigned metre |
| --- | --- |
| “So I set aside an evening for names, with a pencil, and penned” (`variants/poems/the-name.qmd:16`) | About 16 syllables with at least five strong beats, not a four-anapest line. |
| “At two, I gave up the page for a chat, with something else to inquire” (`variants/poems/the-name.qmd:25`) | About 17 syllables and a run of independent stresses: *two*, *gave*, *page*, *chat*, *some*, *else*, *quire*. |
| “Of Otto entirely. I typed, and the word that I typed was \"quire\".” (`variants/poems/the-name.qmd:26`) | About 16 syllables and multiple prose stresses, rather than four repeating anapestic feet. |
| “Otto finished it, \"quirework\", and there with the word I stayed” (`variants/poems/the-name.qmd:28`) | About 14 syllables with irregular stress groups, not a regular tetrameter line. |

The factual gloss on line 24 may be retained word for word under the
quotation exception, but that exception cannot cover the non-quotation lines
above.  The facts need to stay exact, so the remedy is to rewrite the verse,
not reduce or alter the time, the pencil page, `quire`, or Otto's completion.

I found **no full-rhyme failure introduced by the changed material**:
`penned/end`, `drew/through`, `come/from`, `inquire/quire`, and
`stayed/made` are full rhymes in US pronunciation.  The nearby unchanged
`explain/again` can also be read with the permitted US `/eɪn/` pronunciation
of *again*, so it is not a definite older rhyme finding.

## Checks that pass

### The canonical account is now internally consistent

The spec states the new account:

> `manuscript/sections/_spec.md:89-95`  
> “The page's last line, at ten to two, is \"quire\" ... At two Cy typed
> \"quire\" ... and Otto finished it as \"quirework\". Otto finished a word
> Cy had already chosen.”

The book chapter, the six-pager, the young-readers edition, and the poems
all now carry the same essential facts: pencil page, `quire` at ten to two,
Cy typing `quire` at two, and Otto finishing it as `quirework`.  In each,
the result is preferred to the other names on the page and the prose does
not explicitly assign ownership of the name.  Their sidecars bind
`#the-name` and describe the same treatment, apart from F1.

### The earlier account remains findable as a reversal, not live canon

The trail preserves the old claim:

> `provenance/spec-trail.md:125`  
> “Otto completed quirk as quirework.”

It immediately records the reversal and current account:

> `provenance/spec-trail.md:144-149`  
> “Reverses the seed and the sharpen ... at two Cy typed \"quire\" and Otto
> finished it as \"quirework\". The name came from \"quire\" ...”

This satisfies the request to keep the prior telling discoverable on the
trail while making the reversal explicit.  The remaining `quirk` instances
are in the three `variants/*/_raw/` blind drafts and those two historical
trail entries.  The raw drafts are intentionally unchanged records under the
brief and project guide.  No current manuscript, composed edition, sidecar,
or procedure retains `quirk` or says Otto originated the tool name.

### House, pronoun, and form checks other than F1 and F2

- No current prose, spec, or sidecar gives Otto a pronoun.  The object
  `it` in “Otto finished it” has the typed word as its antecedent; the poems
  sidecar explicitly records that distinction.
- A literal search of `manuscript/` and composed `variants/`, excluding
  preserved `_raw/` records, found no em dash character, `&mdash;`, or
  smart-typography triple hyphen.  The delta also introduces no figure or
  color change.
- The six-pager still uses first-person prose to Hedda as `you`, has no
  request or proposal in the changed passage, and retains the agreed name
  facts.  Its rendered `build/variants/six-pager/book/_site/memo.pdf` has
  six pages.  Its source copy in that build matches the current memo.
- The changed poem material is kept in Pandoc line blocks, gives Otto no
  pronoun, and retains the source facts word for word where required.  The
  defect is metre, not the changed name fact or its end rhymes.
- `git diff --check` passed.  `quirework` itself was not available on
  `PATH` in this environment, so I could not run the prescribed
  `quirework spec notes --check` or `quirework dev check`; the house-rule
  search and PDF page-count check above are manual substitutes, not a
  replacement for those gates.

## Leads outside the supplied delta

These are not continuity findings against the requested change.  They are
outside the listed material or are historical provenance, and should be
handled only if their owners want their scope changed.

1. Two unlisted worktree changes are present: `quirework.toml` adds
   `runs.reviews = "provenance/reviews"`, and
   `procedures/personas/continuity-editor.json` is a new untracked persona.
   Neither carries a conflicting name-origin claim, but neither was named in
   the supplied delta.
2. Historical Otto-room records still state the superseded account, for
   example `provenance/rooms/otto/plan/brief.md:46-47` says “Otto named it”
   and `provenance/rooms/otto/board/r001.md:37` says “Otto named Quirework.”
   They are provenance records rather than current book, edition, sidecar,
   or procedure text.  They should remain historical records unless Cy asks
   to revise their status, but they need a reversal note if anyone will use
   them as current writing guidance.
