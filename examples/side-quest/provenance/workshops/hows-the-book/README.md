# Workshop: How's the Book?

Chapter one was drafted by a prose workshop on 2026-10-09: three
writers, each blind to the others and to the project, given the same
frozen packet, one draft each. The drafts were sealed under letters
before anyone read them. Cy read them as A, B and C, chose, and only
then was the key opened.

`A.md`, `B.md` and `C.md` are the sealed drafts exactly as the writers
saved them. They are evidence, not the book: nothing reflows them or
holds them to the house rules. `identity-key.json` is the key.

## The writers

| Draft | Harness | Model | Effort | Seconds | Output tokens | Cost |
|---|---|---|---|---|---|---|
| A | Claude Code | claude-opus-5-5 | high | 154 | 14,638 | $0.54, list-price equivalent under a subscription |
| B | Codex | gpt-6-astra | high | 116 | 3,768 | not reported by the harness |
| C | Antigravity | gemini-3.1-pro-high | high | 118 | 14,026 | not reported by the harness |

Input tokens, as each harness reports them: A 18 plus 175,011 cached;
B 96,185, of which 58,496 cached, since Codex counts cached tokens
inside its input; C 46,689 plus 113,643 cached. Output includes
reasoning: 9,276 for A, 724 for B, 7,248 for C.

The Claude and Codex writers ran in a confined jail holding only their
own workspace. The Antigravity writer ran in the same jail, with a
session bus filtered to the keyring's secrets service, which
Antigravity needs for its login.

## The packet

Each writer was given the spec, the chapter's sidecar, the book's
shape, cast and house rules, an outline of what every chapter carries,
and Cy's own telling of the story. The assignment held the chapter to
the first of Cy's answers to Hedda, "chapter four".

## What became of them

Cy chose A. The chapter is A with three changes: one paragraph from B
(selecting the whole manuscript to count the words), placed after A's
walk to Hedda's office; "That needs a word of explanation" cut down to
the explanation itself; and A's inventions, Hedda's running document
and Red's "Checking in" emails, kept and written into the spec. Nothing
was taken from C, whose last paragraph says how the book ends, which
only the last chapter may do.
