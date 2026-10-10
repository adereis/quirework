# Reading: blind, whole book

The whole book was read blind on 2026-10-09 by two readers, on two
harnesses, each alone. Neither had the spec, the sidecars, this file or
anything else about the book: only the title page, the preface and the
nine chapters as plain text, and Hedda's screen as an image. Each opened
the chapters itself, one at a time, in order, and wrote a reaction to
each before opening the next. A closing question came only after the
last reaction, in a second turn, so neither saw it while reading.

The book they read was commit `a94ae30`, with no uncommitted edits.

`plan/` holds what the readers were asked: `reading.md` is the reading
turn as it was frozen, and `closing.md` the closing question. The engine
added only the paths to save each file under. The persona is
`procedures/personas/first-time-reader.json` and the questions are
`procedures/instruments/blind-read.json`. `reader-a/` and `reader-b/`
hold what each reader wrote, exactly as they saved it: one reaction per
file, named for the file it reacts to, and `wrap.md`, the answer to the
closing question. All of it is evidence, not the book: nothing reflows
it or holds it to the house rules. The raw run records, sessions and
receipts stay outside the book.

## The readers

| Reader | Harness | Model | Effort | Output tokens | Cost |
|---|---|---|---|---|---|
| A | Claude Code | sonnet (claude-sonnet-5-5) | medium | 6,849, of which 805 thinking | $0.20 for the reading turn, list-price equivalent under a subscription |
| B | Codex | gpt-6.1-sol | medium | 4,682 for both turns, of which 40 reasoning | not reported by the harness |

Input, as each harness reports it: A 28 plus 226,201 cache reads and
26,199 cache writes, for the reading turn; B 311,029 across both turns,
of which 286,720 cached, since Codex counts cached tokens inside its
input.

Both ran with the filesystem enforced and compaction refused, and none
was observed. Each passed the blindness canary before it launched, on
haiku and on gpt-6-luna. The book was mounted read-only at
`/home/ademar/library/book`, a path that names neither the book nor the
tool, and its digest held from planning to the end of each read.

## What the receipts proved

For both readers, the reading turn's receipt shows:

- all eleven files read in full, and in order;
- each reaction written before the next file was opened;
- the one figure opened (1 of 1), not judged from its caption;
- no red flags.

Reader B's closing turn completed with no red flags and wrote
`wrap.md`.

Reader A's closing turn did not complete in the record. The reader wrote
`wrap.md` in that turn, and its chat reply after it was shorter than the
200 bytes the plan asks of an answer, so the engine refused to bank the
turn and kept it pending. `reader-a/wrap.md` is the file the reader
wrote, copied byte for byte from its workspace; no receipt covers it.
It was not written during the reading turn, whose receipt lists the
eleven reactions and nothing else.

## Two things done by hand

The plan compiler puts the persona into the block of headings each
reaction must carry, and the engine also puts it at the top of the
first turn, so a reader would have seen it twice, once as if it were a
heading to copy. The instrument therefore names no persona, and the
persona was set on each seat in the compiled plan, which puts it once,
at the top. `plan/reading.md` shows the result.

The closing question was asked as approved. Its 200-byte floor is the
compiler's, measured against the chat reply rather than the file, which
is why Reader A's turn stopped.

## What they said

| | A: keep reading | B: keep reading |
|---|---|---|
| Title page | 6 | 7 |
| Preface | 7 | 8 |
| How's the Book? | 8 | 9 |
| Two Shops | 9 | 8 |
| Index Cards | 8 | 8 |
| Otto | 8 | 9 |
| The Name | 7 | 7 |
| Nine Hundred Lines | 8 | 8 |
| Em | 8 | 7 |
| The Demo | 9 | 9 |
| Full Stop | 8 | 8 |

Both readers, independently, found that Hedda's screen in The Demo does
not agree with the text around it. Reader A: chapter eight, which the
text names as where the corrected date came from, is not on the screen.
Reader B: chapter one's sentence, "No printer's manual gave the dash a
rule before 1684", does not contradict a first rule in 1694, so the
screen marks a disagreement that is not one. Both are right.

## What became of it

Cy read both readers side by side and decided, the same day:

- Hedda's screen is fixed. Chapter one's sentence says 1684 outright,
  chapter eight is on the screen as a second line that agrees, and The
  Demo counts five places and five lines.
- Index Cards quotes the script's first complaint, one line: the date
  chapter eight had moved.
- The Name no longer repeats "I write about software for a living",
  which stays in Index Cards, and its evening of names is shorter.
- Em says plainly that Em used the tool first and read the README
  after.
- The book is about seven thousand words, and that is the length it
  wants: the readers asked for gaps filled, not pages. `AGENTS.md` says
  so, and no chapter has a word target any longer.

Kept as they are: Em's withholding, "Fashion doesn't happen twice", and
"I've stopped minding". Reader B called Cy "he"; a first-person memoir
does not announce its author's pronouns, so the prose stays, and the
back cover's author line will say they/them.

The mount was put under `/home/ademar`, which is not Cy's home; Cy's
home is `~`. Cy removed it afterward.
