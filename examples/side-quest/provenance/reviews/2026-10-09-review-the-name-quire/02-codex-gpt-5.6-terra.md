# Continuity re-review: the-name reversal and year admission

**Reviewed:** 2026-10-09, against HEAD, using the current worktree and index. There are no staged paths. I re-read the master spec and chapter, the three composed editions that bind the-name, their sidecars, AGENTS.md, and the applicable variant procedures.

## Status of the first review's findings

| Earlier finding | Current status | Evidence |
| --- | --- | --- |
| Young-readers edition defined *quire* and added an unmastered explanation of book construction. | **Closed in this delta.** | The revision now says only: “The last word on the page, at ten to two, was *quire*: the folded gathering of pages a book is sewn from” (variants/young-readers/the-name.qmd:30). Its sidecar now says that the sentence carries the book's words “and nothing more” (variants/young-readers/_marks/the-name.md:11-15). The removed “a few sheets” and “one folded bundle after another” explanation is gone. |
| The changed poem stanzas did not hold anapestic tetrameter. | **Still open, but partly remediated in this delta.** | The new opening pair, penned/end, is a workable anapestic couplet. The changed poem nevertheless retains an unscannable non-quotation line and introduces a bare, unrhymed line without a permitted exception. Finding F1 below gives the current evidence. |

## Findings

### F1. The poem still breaks its required form

**Status:** in the delta. This is the remaining portion of the earlier poem finding, with different current evidence.

The poems procedure assigns *The Name* “anapestic tetrameter couplets” and requires: “Hold the metre through the poem and make it scan; make every rhyme a full rhyme” (procedures/workflows/variant-poems.md:55,63-64). It permits a bare line outside the form only when the chapter itself ends on that line, or when the spec asks for a sentence once and word for word (procedures/workflows/variant-poems.md:29-34).

The revised line below is neither a quotation nor a regular four-anapest line. It has about fifteen syllables and several independent stresses:

> variants/poems/the-name.qmd:23  
> “Which is how two in the morning happens. The last line had come”

The line after it retains the master gloss verbatim, which can reasonably use the procedure's word-for-word exception. That exception does not extend to the new narrative setup line above.

The poem also gives this master narration a one-line stanza, outside the form and without a rhyme:

> variants/poems/the-name.qmd:35  
> “I liked it better than anything on the page.”

That sentence does **not** end the chapter. The master immediately continues with “I don't remember what I meant to ask” and then the repository scene (manuscript/sections/the-name.qmd:45-50). Nor does the spec ask for this sentence once and word for word: it describes the fact as “Cy liking the word better than anything on the page is the joke” (manuscript/sections/_spec.md:97-99).

The poem sidecar acknowledges that the line is bare and unrhymed, but calls it a quotation:

> variants/poems/_marks/the-name.md:21-26  
> “Three of the chapter's own lines stand bare and unrhymed, as quotations ... ‘I liked it better than anything on the page.’”

The chapter gives that line as unquoted narration, and the sidecar cannot create one of the procedure's two exceptions. It is therefore a direct sidecar and procedure conflict as well as a metrical and rhyme failure.

The revised couplets otherwise do not introduce a separate full-rhyme defect: penned/end, drew/through, come/from, inquire/quire, pun/none, and though/no are full rhymes in US pronunciation. The unpaired line above is the current rhyme failure.

### F2. The main chapter's sidecar says there is nothing after a line that has following prose

**Status:** in the delta.

The sidecar says:

> manuscript/sections/_marks/the-name.md:9-13  
> “Cy likes the result better than anything on the page, with nothing after that line.”

The chapter has a paragraph immediately after that line:

> manuscript/sections/the-name.qmd:45-49  
> “I liked it better than anything on the page.  
> I don't remember what I meant to ask.  
> I made the repository that night ...”

As written, the sidecar's literal instruction is false. If the intended guidance is that nothing *explains the joke* after the liking line, it needs to say that, as the poems sidecar does. The following “I don't remember” does not explain the joke, but it is still text after the line.

## Checks that pass

### The new one-year admission agrees everywhere it is required

The master chapter places the exact two sentences immediately after Otto's completion:

> manuscript/sections/the-name.qmd:35-38  
> “Otto finished it as "quirework".  
> For a year I told people Otto named it.  
> Then I found the page.”

The spec requires the same order, once and without an explanation in the chapter (manuscript/sections/_spec.md:92-99). The young-readers chapter (variants/young-readers/the-name.qmd:35-38), six-pager (variants/six-pager/memo.qmd:190-194), and poems (variants/poems/the-name.qmd:28-30) each carry it in that order. Their sidecars record the same placement and absence of an in-prose explanation. The master sidecar also records the admission correctly apart from F2.

The two retained statements about the chat still agree with the new account: Cy was “meaning to ask something else entirely” and does not remember what that was. The book, young-readers edition, and six-pager state both; the poem keeps the latter as “What I'd meant to ask Otto, I don't recall, though” and the former as a different thing to inquire of Otto. Nothing requires the book to resolve that doubt.

### The name-origin reversal and its historical record are correct

The live canon consistently says that the pencil page's last line is quire, Cy typed quire at two, and Otto completed it as quirework. The old quirk account remains only where it should:

> provenance/spec-trail.md:125,137-149  
> “Otto completed quirk as quirework” and “quirk typed” are preserved above the reverse entry, which says the page was found later and that “The name came from "quire".”

The additional trail entry gives the reason for adding the year admission and records that it was carried into every applicable edition (provenance/spec-trail.md:151-158). This is a reversal record, not a second live account. The other quirk instances remain in the three blind drafts under variants/*/_raw/, which the brief and project guide require to remain unedited.

### House rules and edition-specific checks

- No current manuscript, composed edition, sidecar, or procedure retains quirk or asserts that Otto actually originated the tool name.
- The changed narrative gives Otto no pronoun. In “Otto finished it,” it has the typed word as its antecedent; in “Otto named it,” it has the tool as its antecedent. Neither is a pronoun for Otto.
- A literal search of manuscript/ and the composed variants/, excluding preserved _raw/ records, found no em dash character, &mdash;, or smart-typography triple hyphen. The delta adds no figure or color change.
- The young-readers text now follows the master wording without a glossary, added definition, moral, or new event.
- The six-pager still uses first-person prose to Hedda as you, makes no request or proposal, and carries the exact new admission. Its current rendered PDF, build/variants/six-pager/book/_site/memo.pdf, has six pages; the memo copied into that build matches the current source.
- quirework.toml now includes provenance/reviews in runs.evidence_roots, as this turn's brief requires. That configuration change creates no narrative or continuity conflict.
- git diff --check passed. quirework is not available on PATH in this environment, so I could not run quirework spec notes --check or quirework dev check; the manual house-rule search and PDF page-count check above are not substitutes for those project gates.

## Leads outside the supplied delta

These are not findings against the current edit.

1. procedures/personas/continuity-editor.json is an untracked new persona not named in the brief. It contains no conflicting name-origin claim.
2. Historical Otto-room provenance records still contain the superseded account: provenance/rooms/otto/plan/brief.md:46-47 says “Otto named it,” and provenance/rooms/otto/board/r001.md:37 says “Otto named Quirework.” They are not current book, edition, sidecar, or procedure text. They should remain historical records unless Cy changes their role, but need a reversal note if they are used as active writing guidance.

