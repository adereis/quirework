# Room: Otto

Chapter four was argued and drafted by a writers room on 2026-10-09:
four seats on three harnesses, six rounds, with Cy in the chair. The
room argued what the chapter must do before anyone drafted, then one
seat drafted, the other three read the draft twice, and the drafter
rewrote it.

`plan/` is the plan as it was prepared: `room.json`, the brief every
seat was given, and the four persona profiles. `board/` is what the
seats saw, round by round, as `quirework sop room board` printed it,
the chair's notes included. `drafts/` holds the two drafts exactly as
the drafter saved them; their hashes match the ones the room logged when
it promoted them. All of it is evidence, not the book: nothing reflows
it or holds it to the house rules. The seats' raw records, sessions and
receipts stay in the room, outside the book.

## The seats

| Seat | Role | Harness | Model | Effort | Output tokens | Cost |
|---|---|---|---|---|---|---|
| Manny Script | drafts | Claude Code | opus (Opus 5.5) | high | 52,573 | $2.07, list-price equivalent under a subscription |
| Drew Contrary | argues Otto's side | Codex | gpt-6-astra | high | 8,312 | not reported by the harness |
| Archie Tecture | tests the argument end to end | Antigravity | gemini-3.1-pro-high | high | 41,198 | not reported by the harness |
| Joe King | tests the jokes | Codex | gpt-6.1-sol | high | 11,525 | not reported by the harness |

Cache reads, as each harness reports them: Manny 628,019, Drew 479,488,
Archie 612,445, Joe 715,264. Codex counts cached tokens inside its
input, Claude beside it.

Beyond the shared brief, the spec and Otto's sidecar, each seat held
different material on purpose. Manny had the preface and Index Cards.
Drew had nothing more, so that Otto's best defense came from a seat that
had not been told what the book thinks. Archie had all of part one and
every sidecar. Joe had How's the Book? and Two Shops.

Archie's seat ran with a session bus filtered to the keyring's secrets
service, which Antigravity needs for its login. Each of its four
receipts carries that as an open red flag. Cy chose to leave it open.

## The rounds

1. Opening positions, written blind to one another.
2. The four answered each other by name.
3. Manny drafted, after a chair note from Cy: no script line; what Cy
   still asks Otto for; when the Tuesday fell; Quirework is not the cure
   for Otto.
4. The other three said what the draft still owed and what must
   survive.
5. The same three gave the faults.
6. Manny rewrote, after a second chair note from Cy: the scrollback is
   true; the regular-expression check said plainly; where Cy looked on
   the Tuesday; short is right.

Both chair notes carry a short operator's gloss below a rule, marked as
not Cy's.

## What became of it

Cy took the rewrite as it stands, and it is the chapter. Nothing was
changed between `drafts/r006-otto.md` and `otto.qmd` except the
heading's attribute block. Cy's answers at the chair went into the spec
and the sidecar in the same session.
