# The book as poems

<!-- @skill: Make the poems edition of Side Quest, one poem of light
     verse per chapter, drafted blind on Codex and composed against the
     spec. -->

Read `variant-common` first: it holds what every edition keeps, and how
an edition is drafted and composed.

The poems edition is the book as light verse, one poem per chapter, in
the book's order and three parts. It is drafted on Codex, as the variant
policy says, so the editions are not all one model's voice. Each poem is
drafted blind into `variants/poems/_raw/STEM.md` and composed as
`variants/poems/STEM.qmd` with its own sidecar, keeping the book's file
names and titles.

Read each poem aloud before composing it. A line that does not scan, a
rhyme that is only near, or a fact bent to fit the rhyme is a failure,
and the fact wins: rewrite the line, never the fact. A forced rhyme is
better than a changed number. Em's chapter holds exactly one limerick,
and a second one is a failure too. Otto gets no pronoun in verse either,
however well a "he" or an "it" would scan: the line is rewritten.

Each chapter takes the form that suits it, so the poems are not one form
ten times over. Drafters work alone and each reaches for the same easy
form, so the brief assigns the forms, chapter by chapter. Within a poem
the metre holds.

Where a chapter ends on a bare line the form cannot hold without added
words ("I still haven't asked.", "There was a section called Getting
Started.", "She wrote that down."), the line stands bare, outside the
form, as the chapter sets it. The same holds for a sentence the spec
asks for once and word for word. Each sidecar says where its poem does
this.

A line break in a poem is a line. Each stanza is composed as a Pandoc
line block, every line starting `| `, so Quarto keeps the breaks in
every format, and `text.modes` puts `variants/poems/*.qmd` in trailing
mode, so `text wrap` never reflows them. A stanza break is a blank line
between two line blocks.

## Blind-draft brief

Retell the chapter as one poem of light verse: comic, quick, in real
metre and real rhyme, in the form this list gives your chapter. Each
chapter has its own, so the book is not one form ten times over:

- Preface: a Shakespearean sonnet, fourteen lines of iambic pentameter.
- How's the Book?: a villanelle, nineteen lines, its two refrains
  carrying "How's the book?" and "Chapter four".
- Two Shops: terza rima, iambic pentameter, the rhyme handed from each
  tercet to the next, closed by a single line or a couplet.
- Index Cards: ottava rima, iambic pentameter, abababcc.
- Otto: heroic couplets, iambic pentameter.
- The Name: anapestic tetrameter couplets.
- Nine Hundred Lines: In Memoriam stanzas, iambic tetrameter, abba.
- Em: triplets, three lines on one rhyme, in iambic pentameter, around
  the one limerick.
- The Demo: ballad stanzas, common metre, abcb.
- Full Stop: a sonnet sequence of two or three Shakespearean sonnets,
  answering the preface's.

Hold the metre through the poem and make it scan; make every rhyme a
full rhyme. The narrator is still "I".

The facts stay exact even when the rhyme would rather they did not:
every name, number, day, month, hour and quotation is the book's own. If
a fact will not fit the line, change the line. A forced rhyme is better
than a changed number. A quotation the book gives is kept word for word,
even when it breaks the metre.

Otto gets no pronoun in verse either, however well "he", "she", "they"
or "it" would scan. Write "Otto", or rewrite the line.

Every line break is a line of the poem: write one line of verse per line
of text, with a blank line between stanzas.

The chapter titled "Em" holds exactly one limerick: five lines, rhymed
AABBA, in the limerick's own bouncing metre. If the chapter needs more
room than a limerick, the poem around the limerick gives it, in a metre
of its own; never a second limerick.

Keep the poem as short as the chapter's material allows. No title other
than the chapter's own, no epigraph, no closing couplet that states a
moral. End where the chapter ends, on its own last beat, and add nothing
after it. A poem has no figure and no placeholder for one: what a
chapter's figure shows, the verse tells, or leaves out.
