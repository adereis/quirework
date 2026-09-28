# The manager edition

<!-- @skill: Produce the manager edition of Chapter and Verse Control
     for a reader who has not read the book and has four minutes. -->

This edition retells the book for Della Gate: Mark's manager, who
manages him but has not read a word he has written, and has four minutes
between two other meetings to find out why he keeps mentioning a tool
called Quirework.

Run `quirework publish variant build manager` to fan out one blind drafter
per chapter. Each drafter reads the whole book for context and rewrites
only its own chapter for Della, informed by `variant-common.md`'s voice
rules and the reader profile below. The tool stops at raw drafts in
`variants/manager/_raw/`; read every one against its source chapter and
this book's own `_spec.md` before composing the final edition. A draft
that drops a hedge, invents a scene, or resolves the disagreement
between chapter one and chapter nine that chapter one is actually about
is a fidelity failure, not a simplification, and gets sent back.

Compose the final `.qmd` files and their `_quarto.yml` entry under
`variants/manager/` once every chapter's draft has passed that check.

## Blind-draft brief

The reader is Della Gate. She manages Mark and has not read the book;
she has four minutes and one real question, which is why Mark will not
stop bringing up a tool named Quirework in one-on-ones. Write for
someone standing up, reading on a phone, who will make one decision from
this: whether the tool is worth fifteen more minutes of her attention
later. Open each chapter with the one sentence that answers that, then
the scene that earns it. Cut nothing that is a genuine open question in
the source; compress the scene-setting instead. Do not use the word
"leverage."
