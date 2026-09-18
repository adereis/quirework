# Procedures and model runs

An SOP remains a procedure the operator follows. Its catalog describes
required reading, mechanical tools, and conditional follow-ups. A link
to another procedure never launches it automatically.

## Discovery and assignments

`quirework sop list` and `quirework sop explain NAME` read the project's
catalog. `quirework sop skills --target both` generates thin entry
points for Claude and portable agents. The SOP remains the content
owner. Project configuration selects the skill-name prefix.

Personas specify perspectives. Instruments specify questions. Panels
specify an allocation of perspectives and models. None grants filesystem
access. Preparation resolves the selected data and freezes the composed
assignment alongside its source identities.

Research is a supervised workflow. A research assignment can request
external source access through its tool policy. The operator still
checks sources and evaluates the answer. Provenance retrieval and model
output are evidence to inspect.

## Run plans

A minimal plan contains explicit seats and ordered turns:

```json
{
  "title": "Read a small note",
  "seats": [{
    "id": "reader",
    "harness": "codex",
    "model": "gpt-5.6-luna",
    "effort": "low",
    "policy": {"tools": "none", "filesystem": "enforced"},
    "turns": [{
      "id": "opening",
      "prompt": "What is clear in this note? Answer briefly.",
      "materials": [{"source": "note.md", "delivery": "text"}]
    }]
  }]
}
```

The model is an explicit choice. `quirework sop models list PROVIDER`
and `resolve PROVIDER MODEL --effort LEVEL` inspect available
information without starting a model session. Unsupported capabilities
fail during preparation. A native model alias remains explicit; the
receipt records the model actually served.

`quirework book plan` compiles rendered book packets, comparisons, and
bundles. `quirework sop panel` compiles a declared panel. These helpers
live above the generic execution engine.

Supported delivery includes text, clean prose, prose with figures,
images, rendered PDF pages, and native files. Delivery and exposure are
separate choices. A native file receipt does not establish that its
pages were inspected visually.

## Writers rooms

A room runs several seats against one question and lets them read each
other between rounds. `quirework sop room prepare` stages one workspace
per seat from a room plan; `round` opens, runs and closes a numbered
round; `status`, `board` and `watch` read a room without disturbing it.

Commands name a room, not a path. A bare name is a room in the project's
rooms directory, which is where `prepare` puts one; anything containing
a separator is an explicit path. A name that is no room fails at once
and says both places it looked, `watch` included.

Seats never share a workspace. A seat writes to its own outbox, and
promotion copies that file onto the board once every seat has stopped.
Promotion is mechanical: it copies, hashes and attributes, and it does
not judge a post. A missing or empty outbox records the seat as absent
for that round rather than failing the round.

A round is a resume of the session a seat already holds, so a seat keeps
its reading and its reasoning and is handed only what changed. The first
round has no board. A round listed in `silent_rounds` has one and is
deliberately not shown it, which keeps opening positions independent.

The digest concatenates a round's posts whole, attributed, in an order
rotated by round number. It never summarizes. The staged packet and the
standing assignment are frozen and checked before every turn; the board
is not, because it is the one thing that is meant to change.

`quirework sop room usage ROOM` reads the seats' own receipts and says
what each one spent. Every harness reports usage in its own shape and
only Claude quotes a price, so the command names the harnesses it cannot
price rather than leaving them out of a total: a room missing half its
seats reports a cheaper room than the one that ran. Codex counts cached
tokens inside its input figure and Claude counts them beside it, which
the output states rather than reconciling into a number no receipt
contains.

`quirework sop room follow ROOM SEAT` prints one seat's side of the room
as it happens: the turn it was given, what it decided to do, its
reasoning and its post. Tool results are dropped, because a single file
read returns kilobytes of quoted source and buries what the seat
actually said. `--no-thinking` leaves only actions and posts. A seat may
be named by its position in the room as `status` numbers them, so
following a room is `follow ROOM 1` through `follow ROOM 4` rather than
four persona slugs. A seat whose id is itself a number keeps that id.
One terminal per seat reads as four people working. Following is
implemented for the Antigravity adapter; another harness reports that
rather than printing nothing.

Seats may name their own harness, model and effort; the room supplies
the default for any a seat omits. A mixed room costs no special
handling, because every adapter call already takes a seat's own
configuration.

A room may run in phases. A phase names how many rounds it lasts, and
may seat only part of the room, set its own post length, and add one
line of instruction. Rounds a seat sits out are not absences: it is
listening, and when it speaks again it is told which boards it has not
read. Phase rounds must add up to the room's declared rounds.

A drafting round needs no new machinery, because a draft is a post that
happens to be long. One seat writes while the others rest, the draft is
promoted to the board like anything else, and the next phase reads it:

```json
"phases": [
  {"name": "what-it-must-do", "rounds": 3},
  {"name": "draft", "rounds": 1, "seats": ["wren"], "words": 900,
   "instruction": "Write the passage itself, as finished prose."},
  {"name": "read-the-page", "rounds": 2, "seats": ["mara", "teo"]},
  {"name": "rewrite", "rounds": 1, "seats": ["wren"], "words": 900}
]
```

The same mechanism covers a plain discussion (one phase, everyone), a
panel that hears a witness, and a room that drafts, reviews and revises.

A phase that reads a draft is asked two different questions, and they do
not belong in the same round. Fault-finding is concrete and absence is
not, so a turn asking for both is answered on the faults. Put the
absence question first, while the draft is still new: what does this
still owe its reader, what was expected and not delivered, and what is
already working and must survive the next pass. Faults and replies come
in the round after it.

Order matters as much as wording. A room that hunts faults first has
framed the draft as wrong and overlong before anyone asks what is
missing, and the revision inherits that framing. A measured room given
only "say where you disagree" returned only cuts: every revision in it
came out shorter than the draft it replaced and the work finished at
about three quarters of the length its brief asked for, with no seat
having proposed a single addition. Nothing was wrong with the seats. The
room was never asked.

A phase may name a `deliverable`, and its seats then write two files: a
post, which is the note to the room, and the work itself. A drafting
phase produces both whether or not the plan says so, and without a name
for the second one they arrive welded into a single post that has to be
cut apart afterwards against whatever separator the room improvised.
The phase's `words` then describes the work, and `post_words` the note.

```json
{"name": "draft", "rounds": 1, "seats": ["wren"], "words": 1400,
 "deliverable": "the-contradiction.md"}
```

Promotion keeps it at `board/made/SEAT/rNNN/NAME`, hashed and logged
like a post, and the digest carries it under its author's name so the
next phase reads the note and the work together. The file itself stays
a file, so assembling the result is copying rather than parsing. A
deliverable is a plain file name, checked when the room is prepared. A
seat that posts without producing the file is recorded as having not
made it, which is a fact about the round rather than a failed round.

A room that has run out of positions keeps producing paragraphs, so it
cannot be trusted to notice it has finished. `quirework sop room rounds`
runs rounds until the room converges or its budget ends. Two signals are
computed from the board alone, with no model call: novelty, the share of
a post's content words that seat has not used before, and closing, the
share of seats reaching for consensus language. Either must hold for two
consecutive rounds, because one round of agreement is noise. A plan
moves the thresholds with `stop_novelty` and `stop_closing`. A single
`round` still runs on request; the rule decides when to stop asking for
another, never what a writer may say.

Both signals are measured over the seats that spoke, not the whole room.
A seat resting under a phase has not repeated itself and has not fallen
quiet: it had no turn. Averaging its silence in made a drafting round
read as a room with nothing left to say, which is the opposite of what a
drafting round is.

A room that declared phases is not asked to stop at all. Its schedule
already states how long it runs and in what order, and the signal reads
a board rather than a plan: it cannot tell a question that is exhausted
from two rounds of notes whose answering revision comes next. Stopping
there ships the draft nobody rewrote. The signal is still measured and
recorded every round, so an operator can see a discussion go quiet, and
`extend` still raises the budget when a schedule turns out to be short.

The declared round count is the starting budget, and `extend` raises it.
The budget lives outside the frozen plan on purpose: the plan holds what
the writers were promised and must stay byte-identical for its digest to
mean anything, while how long the room runs is a decision the operator
keeps making.

Each seat's standing assignment names who else is in the room and what
each of them was given beyond the common material. Titles only: naming a
file is not handing it over. Without that roster a seat cannot tell a
colleague's private source from an invention, and a room asked to cite
its sources will eventually accuse someone of fabricating one. A
measured room did exactly that, correctly observing that a cited
comparable title was absent from the brief, when the colleague did hold
the file in a packet the objecting seat had never been shown.

Seats may be given different packets on purpose. Asymmetric exposure is
what makes disagreement between them substantive rather than stylistic.

## Execution and recovery

Use `sop runs prepare`, `start`, `inspect`, `observe`, `collect`,
`verify`, and `export`. Preparation freezes inputs, settings, questions,
and the generic executor. Each run has separate seats, ordered turns,
attempts, and checkpoints.

Launch intent is recorded before process creation. A pending request
must be reconciled before another submission. `collect` recovers
existing evidence without buying a new answer. An explicit retry is
allowed only after a provable terminal rejection without an answer.
Earlier answers and rejected attempts remain immutable.

Native forks preserve their parent boundary and image ancestry. A
provider without native forks cannot substitute a summary or replay.
Tool restrictions and filesystem enforcement are checked separately.
Antigravity currently provides audited rather than enforced filesystem
assurance; it cannot satisfy a requirement for enforced isolation.

Compaction is always reported as a red flag. Completed answers remain
available, but a failed run condition does not become a clean result.
Export copies answers and curated receipts. It does not publish them or
copy runtime homes and raw event streams.

Existing records dispatch through their verified frozen executor.
Historical commands, prompts, answers, and hashes remain unchanged. The
current command surface translates a start request when dispatching a
supported pre-extraction executor. A provider version mismatch still
requires attention rather than silent reinterpretation.

Workshop and contextual-review commands retain their specialized state
and manual-session features. They share provider adapters and account
leases with the generic engine.

Capture schema identifiers remain unchanged for compatibility with
stored transcripts. Project redaction policies are separate data.
Changing a marker list changes the analysis recipe; a verified legacy
marker fingerprint preserves existing cache identities for the unchanged
book policy. Primary transcripts are never rewritten by redaction.
