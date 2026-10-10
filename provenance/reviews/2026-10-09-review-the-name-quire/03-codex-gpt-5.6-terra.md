# Continuity re-review: poem and sidecars

**Reviewed:** 2026-10-09, against HEAD, using the current worktree and index. There are no staged paths. This pass covers the master chapter and spec, the poems edition and its sidecar, every other composed edition and sidecar that binds the-name, AGENTS.md, and the variant procedures.

## Disposition of earlier findings

| Earlier finding | Current result |
| --- | --- |
| The young-readers edition defined *quire* and added unmastered book-construction material. | **Closed in the delta.** It now uses the master's “folded gathering of pages a book is sewn from” and its sidecar says “nothing more.” |
| The poems did not hold their required form. | **Still open, with different evidence.** The exact year admission is now a proper bare-line exception, and the earlier bare liking line has become the second half of an age/page rhyme. Other changed, non-exempt lines still do not scan. |
| The master sidecar said there was nothing after the liking line. | **Closed in the delta.** It now correctly says “nothing after it explains the joke,” which the chapter observes. |

## Findings

### F1. The poem reverses the chapter's order of the check and the observation

**Classification:** fact and rule.  
**Age:** in the delta.

The master puts the observation before the search and check:

> manuscript/sections/the-name.qmd:40-43  
> “I looked at the word for a while.  
> It was not a pun.  
> It sounded like something that should already exist, and as far as I could find, it didn't.  
> I checked.”

The poem puts the check and its result first, then says how the word sounded:

> variants/poems/the-name.qmd:32-35  
> “For a while I just looked at the word. Not a pun.  
> And I checked, and as far as I could find, there was none,  
> Though it sounded already made, a word of some age.  
> I liked it better than anything on the page.”

Its sidecar explicitly confirms the reversal:

> variants/poems/_marks/the-name.md:31-34  
> “'sounded like something that should already exist' is 'sounded already made, a word of some age', **after the check rather than before it**.”

The editions procedure requires the facts to stay exact, including their order where the scene establishes it: “Every scene, line of dialogue, fact, number, date, feeling and outcome in your text must be in the book” and “Keep the facts exact” (procedures/protocols/variant-common.md:60-68). The sidecar records the discrepancy instead of resolving it.

### F2. The poems sidecar falsely gives the gloss the year admission's word-for-word spec exception

**Classification:** rule, with a false sidecar claim about the spec.  
**Age:** in the delta.

The new spec makes exactly one new word-for-word commitment:

> manuscript/sections/_spec.md:94-96  
> “Right after Otto finishes it, the chapter admits it once, in these words: ‘For a year I told people Otto named it. Then I found the page.’”

That properly supports the poem's two bare year-admission lines. The spec describes the *quire* gloss in ordinary narrative, not as a sentence required once and word for word:

> manuscript/sections/_spec.md:90-92  
> “The page's last line, at ten to two, is ‘quire’: the folded gathering of pages a book is sewn from.”

The poems sidecar nevertheless groups the two together:

> variants/poems/_marks/the-name.md:19-24  
> “The gloss and the year ... are the spec's words, asked for once and word for word, so they stand outside the metre.”

That statement is not supported by the spec. The new trail entry records the
narrower commitment:

> provenance/spec-trail.md:163-166  
> “The admission is a commitment in Cy's words ... No line is made a spec commitment only so a poem can leave it bare.”

That record supports the spec's distinction: the year admission, not the
gloss, received the new word-for-word commitment.

The poems procedure permits a bare or out-of-form line when a chapter ends on it, or when “the spec asks for a sentence once and word for word” (procedures/workflows/variant-poems.md:29-34). The gloss is neither. The sidecar therefore cannot exempt poem line 24 from the required metre by declaring it a word-for-word spec commitment.

### F3. Several revised, non-exempt poem lines do not scan as anapestic tetrameter

**Classification:** metre alone.  
**Age:** in the delta.

The assigned form is “anapestic tetrameter couplets,” and the procedure requires: “Hold the metre through the poem and make it scan” (procedures/workflows/variant-poems.md:55,63-64). AGENTS.md also requires real metre and full rhyme for every poem.

The following revised lines have too many strong beats for four anapests and do not become exempt merely because they carry a fact:

| Line | Evidence of failure |
| --- | --- |
| variants/poems/the-name.qmd:26, “At two, meaning something else, I went off to a chat,” | About thirteen syllables, with at least six natural stresses: *two*, *mean*, *thing*, *else*, *went*, *chat*. |
| variants/poems/the-name.qmd:27, “And I typed ‘quire’. Otto finished it, ‘quirework’, like that.” | About thirteen syllables, with stress collisions on *typed*, *quire*, *Otto*, *finished*, *quirework*, and *that*. |
| variants/poems/the-name.qmd:34, “Though it sounded already made, a word of some age.” | About thirteen syllables and at least six natural stresses, rather than four repeating anapests. |

The changed rhyme pairs themselves pass: chat/that, pun/none, age/page, and though/no are full rhymes in US pronunciation. The two bare year-admission lines are now authorized because the spec quotes them once and word for word. There is no new rhyme finding.

## Checks that pass

### The new admission and all other editions agree

The master, spec, young-readers chapter, six-pager, and poem all place the exact admission immediately after Otto completes quirework:

> “For a year I told people Otto named it.  
> Then I found the page.”

The master sidecar, young-readers sidecar, and six-pager sidecar correctly say that this comes immediately after Otto's word, only once, and without an in-prose explanation. The poems sidecar is also correct about the admission itself. The revised main sidecar is now consistent with the chapter: it says no text after the liking line explains the joke, rather than saying there is no text after it.

The live canon still agrees that the page was written in pencil, its last line at ten to two was quire, Cy typed quire at two, Otto completed it as quirework, and Cy meant to ask something else but does not remember what. No current manuscript, composed edition, sidecar, or procedure retains the old quirk account or says Otto actually originated the name.

### House and edition rules other than the findings

- Otto receives no pronoun. In “Otto finished it,” the object is the typed word; in “Otto named it,” the object is the tool.
- The composed book and editions have no em dash character, em-dash entity, or smart-typography triple hyphen. No figure or color changed.
- The young-readers chapter has no added definition, glossary, moral, or new event.
- The six-pager remains first person to Hedda as you, makes no request or proposal, and its current rendered PDF is six pages. The source memo copied into the build matches the current memo.
- The spec trail correctly preserves quirk only as the historical account reversed by the current name account. Blind drafts under variants/*/_raw/ still retain quirk by design.
- quirework.toml includes provenance/reviews in runs.evidence_roots as required by the brief, with no narrative conflict.
- git diff --check passed. quirework is not available on PATH, so quirework spec notes --check and quirework dev check could not be run. The manual house-rule and page-count checks are not replacements for those gates.

## Leads outside the delta

These are not findings against the present changes.

1. **Older metre lead.** Unchanged poem lines 18 and 21-22 are also substantially longer than anapestic tetrameter, for example: “Most were puns. Punctuation rewards them, and shouldn't. One drew” and “Every time that I typed one. I kept going, again and again,” They predate this delta, so they are leads rather than current findings.
2. **Unlisted worktree file.** procedures/personas/continuity-editor.json remains an untracked persona not named in the brief. It contains no competing name-origin claim.
3. **Historical provenance.** provenance/rooms/otto/plan/brief.md:46-47 and provenance/rooms/otto/board/r001.md:37 retain the superseded “Otto named it” account. They are historical room records rather than current canon. Do not alter them as part of this delta; add a reversal note only if they will be reused as active writing guidance.

