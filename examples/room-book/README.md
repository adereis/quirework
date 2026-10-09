# Chapter and Verse Control

A short, funny, entirely fictional story about a software engineer who
could not finish his book and built a tool instead. It ends with the
tool getting its name. Every character is invented and every name is a
pun. Quirework is real, and this directory is a working example of a
book written with `quirework sop room`.

`examples/demo-book` shows an author writing a chapter at a time with a
procedure. This shows the other shape: four seats, one board, twenty-two
rounds, and a book that is the thing the room argued its way to. It
tells the same story, because the interesting comparison is between two
ways of making the same book.

## What's here

- `manuscript/sections/`: the book. A preface and two chapters, copied
  out of the room without editing.
- `room-plan/`: what the room was given. The brief, the four personas,
  and `room.json`, which declares the seats, their models and the
  twenty-one phases.
- `room/`: the run itself, kept rather than regenerated. The board every
  round was shown, every post, the files the room produced, the per-seat
  records and the trail in `room.log`.

## The room

Four seats, four models, three harnesses. One seat drafts; the other
three read it from different angles and are never handed the same job.

| Seat | Role | Harness | Model |
| --- | --- | --- | --- |
| Nadia Quill | drafts the prose | claude | Opus 5 |
| Oren Strunk | line, house style, comic timing | codex | gpt-5.6-terra |
| Priya Ledger | facts, continuity, the pun scheme | claude | Sonnet |
| Gus Firstpass | an ordinary first read | agy | gemini-3.1-pro |

Each section is planned, drafted, then read twice: once for what it
still owes its reader, and once for what is wrong with it. That order is
deliberate and `docs/workflows.md` explains why. The drafting and
revising phases name a `deliverable`, so the book arrives as files and
the manuscript here was assembled by copying them.

## Reading the run

From this directory. Name the room by a path, never by the bare word
`room`:

```
quirework sop room status ./room          # who is in it and how far it got
quirework sop room usage ./room           # what each seat spent
quirework sop room board ./room 11        # what everyone saw in round eleven
quirework sop room follow ./room 4        # one seat's side of it
```

Round 10 is a good place to start. The continuity seat counts the words
in a sentence the model in the story invented, finds eighteen where the
prose claims twenty-two, and says so without being asked. The joke of
the chapter depends on the fabrication being flawless in every
checkable way, so that count was load-bearing.

Round 16 is the other one worth reading. Three seats talk the drafter
out of a scene, and she refuses a seventy-word expansion on the grounds
that a sidecar which changes how the bike rides is not a sidecar.

`room/seats/*.blind-state` is where each seat reached its private runner
home, the harness's own state for the length of the room. A room
prepared now keeps that home in the cache and leaves a link here. It is
ignored by `room/.gitignore`, and it is not part of the example.

## What it cost

Twenty-two rounds took forty minutes. The two Claude seats report
$11.62; the other two harnesses record tokens but not cost, so the true
figure is higher. No round compacted. The book is about 3,224 words
against a brief asking for 3,250.
