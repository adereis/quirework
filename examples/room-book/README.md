# The Room Where Nobody Agreed

A short, fictional book about an engineer whose manuscript argues with
itself, written by four models in a writers room. Every character is
invented. Quirework is real, and this directory is a working example of
a book written with `quirework sop room`.

`examples/demo-book` shows an author writing a chapter at a time with a
procedure. This shows the other shape: four seats, one board, twenty
rounds, and a book that is the thing the room argued its way to.

## What's here

- `manuscript/sections/`: the book. A preface and two chapters, exactly
  as the room left them.
- `room-plan/`: what the room was given. The brief, the four personas,
  and `room.json`, which declares the seats, their models and the phase
  schedule.
- `room/`: the run itself, kept rather than regenerated. The board every
  round was shown, every post, the per-seat records, and the trail in
  `room.log`.

## The room

Four seats, four models, three harnesses. One seat drafts; the other
three read it from different angles and are never handed the same job.

| Seat | Role | Harness | Model |
| --- | --- | --- | --- |
| Nadia Quill | drafts the prose | claude | Opus 5 |
| Oren Strunk | line editing and house style | codex | gpt-5.6-terra |
| Priya Ledger | facts, dates and continuity | claude | Sonnet |
| Gus Firstpass | an ordinary first read | agy | gemini-3.1-pro |

Twenty rounds in fifteen phases: the room plans a section, one seat
drafts it, the room reads it for two rounds, that seat revises. Preface,
then chapter one, then chapter two, then two rounds on the whole book
and a final assembly.

## Reading the run

From this directory. Name the room by a path, never by the bare word
`room`:

```
quirework sop room status ./room          # who is in it and how far it got
quirework sop room board ./room 6         # what everyone saw in round six
quirework sop room follow ./room 4        # one seat's side of it
```

Round 6 is a good place to start. The continuity seat notices that the
preface's "forty-one drafts" would make a joke two chapters later
accidentally true, and the reader seat and the editor seat disagree with
each other in front of the drafter.

`room/seats/*.blind-state/` is each seat's private runner home. It holds
live harness credentials, it is ignored by `room/.gitignore`, and it is
not part of the example.

## What it cost

Twenty rounds took twenty-eight minutes. The two Claude seats report
$3.44; the other two harnesses record tokens but not cost, so the true
figure is higher. The book is about 2,370 words, which is short of the
3,250 the brief asked for: every revision in the run came out shorter
than the draft it replaced, and nobody in the room was responsible for
the word count.
