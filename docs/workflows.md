# Writing and research with an AI companion

Quirework is designed to sit underneath an AI companion such as Claude
Code or Codex. You describe the writing or research job in conversation.
The companion reads the project's instructions, follows its SOP, drives
the relevant Quirework tools, and brings the result back for your
judgment.

The companion is the operator. It can inspect the whole project and make
an authorized change. A model seat is different. It receives a declared
packet through a selected harness and returns evidence to the companion.

For example, you might ask Codex to revise a chapter whose central claim
has changed. Codex can load the spec and find every bound chapter. It
can then ask blind readers what the revision communicates. You decide
whether their reaction should change the book.

## The authoring loop

Most work follows the same loop even when the SOP changes:

1. The human names the aim and the scope.
2. The companion reads the project guide and the selected SOP.
3. The companion runs `quirework book notes --primer` to load the spec,
   bindings, guidance, and open work.
4. The companion retrieves only the prose, sources, and prior human
   decisions the task needs.
5. The companion writes, researches, or prepares a controlled model run.
6. The companion analyzes the returned evidence against the spec and the
   human's request.
7. The companion runs the SOP's checks and reports any decision it could
   not make safely.

The spec gives this loop a stable center. A sentence can move during a
rewrite. A commitment should move only when the author means to change
what the book holds. Bindings let the companion propagate that change
through the whole book instead of polishing one chapter into
disagreement with the rest.

The spec also gives outside model work a reference point. A workshop
draft or blind review can reveal a problem. It cannot silently redefine
the book's position. The companion must reconcile the finding with the
spec before it proposes an edit.

## Procedures and skills

An SOP remains a procedure the companion follows. Its catalog describes
required reading, mechanical tools, and conditional follow-ups. A link
to another procedure never launches it automatically.

## Discovery and assignments

`quirework sop list` and `quirework sop explain NAME` read the project's
catalog. `quirework sop skills --target both` generates thin entry
points for Claude Code and portable agents such as Codex. The generated
skill routes the companion to the SOP through `quirework sop show`,
which prints it verbatim and records that it was read. The SOP remains
the content owner. Project configuration selects the skill-name prefix.

Personas specify perspectives. Instruments specify questions. Panels
specify an allocation of perspectives and models. None grants filesystem
access. Preparation resolves the selected data and freezes the composed
assignment alongside its source identities.

Research is a supervised workflow. A research assignment can request
external source access through its tool policy. The companion still
checks the sources. The human still evaluates the conclusion. Provenance
retrieval and model output are evidence to inspect.

## Companions and harnesses

The companion you are talking to does not determine which models a
procedure may use. Quirework supports Codex, Claude Code, and
Antigravity as harnesses for controlled seats. One procedure can use a
single harness or mix several.

That separation is useful for independence. A Claude Code companion can
ask a Codex model to review its edit. A Codex companion can compare
drafts made through Claude and Antigravity. Quirework records the
requested model and the served model, so changing providers does not
erase what happened.

Harnesses do not all make the same guarantees. A plan declares tools,
filesystem exposure, model, and effort per seat. Preparation rejects a
capability the selected harness cannot provide. The resulting receipt
records the boundary that was actually enforced or audited.

## Rooms, workshops, and reviews

Each book owns the exact procedure and its editorial rules. Quirework
supplies the mechanics for several recurring patterns.

The useful workflows differ mainly in who may see what:

| Need | Workflow | What each seat sees | Durable result |
|---|---|---|---|
| Develop one answer through disagreement | Writers room | Its own packet, then the shared board between rounds | Attributed posts and named deliverables |
| Produce genuinely independent alternatives | Prose workshop | One frozen packet, never another writer's draft | Anonymous drafts with a sealed identity key |
| Learn what the page tells a reader | Blind reader panel | Only the selected reader-facing material | Reports tied to personas, questions, and receipts |
| Test an edit against the whole book | Consistency review | The project guide, spec, marks, brief, and Git delta | An independent findings report |

## Run plans

A minimal plan contains explicit seats and ordered turns:

```json
{
  "title": "Read a small note",
  "seats": [{
    "id": "reader",
    "harness": "codex",
    "model": "luna",
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

The model is an explicit choice, and it is best named by family:
`opus`, `fable`, `sonnet`, `terra`, `sol`, `luna`, `gemini-pro`,
`flash`. Preparation resolves a family to the newest version that
harness's own catalog lists and freezes it, so a plan written today
picks up next month's release without an edit. For example, `luna`
prepares as `gpt-5.6-luna` today, and every turn of that run uses it.
A version such as `opus-5.5` or `gpt-5.6-luna` pins the seat instead.
A name no catalog can place is refused, and the refusal lists the
families that exist. Claude Code keeps no local catalog, so a Claude
family passes to the CLI as its own alias and the receipt records the
version that answered. Every later turn of that session is pinned to
the version that answered first, and its receipt shows the pin as
`model_pinned`. An Opus released between a room's second and third
rounds therefore does not change who is speaking.

`quirework sop models list PROVIDER` and `resolve PROVIDER MODEL
--effort LEVEL` inspect available information without starting a model
session. Unsupported capabilities fail during preparation.

`quirework book plan` compiles rendered book packets, comparisons, and
bundles. `quirework sop panel` compiles a declared panel. These helpers
live above the generic execution engine.

A panel model allocation accepts `tier: cheap | premium | standard`.
Every seat built from that allocation inherits its tier. For example, a
panel can declare one inexpensive model as `cheap` and another as
`premium`, even when both read from several perspectives. The run engine
puts one cheap seat first, then the premium seats, then the remaining
seats in declared order. Additional cheap seats stay with that
remainder. An omitted tier is `standard`; a panel with no tiers keeps
its declared order. Invalid tiers fail when the panel is compiled,
before any model runs.

Supported delivery includes text, clean prose, prose with figures,
images, rendered PDF pages, and native files. Delivery and exposure are
separate choices. A native file receipt does not establish that its
pages were inspected visually.

## Prose workshops

A prose workshop asks several independent writers to solve the same
bounded problem. Use it when you want real alternatives rather than a
single draft with several rounds of self-commentary. The writers do not
see one another's work.

`quirework sop workshop prepare` freezes the assignment, orientation,
source files, required reading, outputs, and writer roster. Each writer
receives a private workspace. Project instruction files and companion
memory are kept out of that workspace, because the experiment is meant
to test the supplied brief rather than the habits of the operator who
prepared it.

The companion can run the writers through different harnesses and
models. It can also include a manually operated seat. Quota stops
preserve partial work and resume the same session when the provider
makes that possible.

Seal the drafts before comparing them.
`quirework sop workshop anonymize` shuffles completed drafts under
letters and writes the identity key without printing it. The reporting
commands withhold near-unique draft sizes until that seal exists. Read
the prose first. Open model identities and usage only after the
editorial judgment has been recorded.

A useful workshop SOP therefore separates four jobs. The companion
prepares the shared packet. Independent writers make alternatives. A
blind editor compares the anonymous drafts. The human chooses what, if
anything, enters the book.

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

`quirework sop room status ROOM` reads the records and changes nothing.
Its header counts published rounds against the budget. It shows the
room's novelty for the last six rounds once there are two to compare,
and counts the red flags marked `!` below it. Each seat then reports how
many of the rounds its phase lets it speak in it has answered. A seat
resting through a drafting phase is therefore not reported as behind.
The last line names the command to run next: `rounds` while the budget
lasts, `continue` and `promote` for a round held on a provider quota,
and `board` once the room is done. An assistant driving the room reports
that line rather than deriving its own, and does not run it unasked,
because `rounds` and `continue` spend the user's provider quota.
`status --help` reads a worked example line by line.

A round takes minutes, because every seat answers in turn. Start
`rounds` in the background rather than in the foreground, and follow it
with `watch` or `follow`. The operator can then report progress and
write a chair note for the next round while this one runs. A seat's
persona names a profile in the plan's `profiles` directory. Read the
names there rather than guessing one: `prepare` refuses a missing one
and lists the profiles that exist.

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
implemented for all three harnesses, each with its own renderer for the
shape of its native stream.

Seats may name their own harness, model and effort; the room supplies
the default for any a seat omits. A mixed room costs no special
handling, because every adapter call already takes a seat's own
configuration.

A seat may also declare `tools`. The default, `write`, is what a post
needs. `research` adds the harness's web tools for a seat that must
check a source, and the digest marks that seat as able to search so the
others can weigh its citations. The mark is only ever made when the
launcher enforces it: Antigravity cannot confine a seat to a tool set,
so a research seat there is refused when the room is prepared.

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
phase produces both whether or not the plan says so. Without a name, the
work and the note arrive welded into one post. The operator must then
split them using whatever separator the room improvised. The phase's
`words` describes the work. Its `post_words` describes the note.

```json
{"name": "draft", "rounds": 1, "seats": ["wren"], "words": 1400,
 "deliverable": "the-contradiction.md"}
```

Promotion keeps it at `board/made/SEAT/rNNN/NAME`, hashed and logged
like a post, and the digest carries it under its author's name so the
next phase reads the note and the work together. The work stays a
separate file. Assembly is therefore a copy operation rather than a
parsing task. A deliverable is a plain file name, checked when the room
is prepared. A seat that posts without producing the file is recorded as
having not made it. That is a fact about the round rather than a failed
round.

Promotion takes any file the plan names in any phase, not only the one
the current round asked for. A seat correcting an earlier section after
its phase has passed otherwise has nowhere to put the correction, and a
file left in an outbox is invisible until something asks for it again. A
file the round did not ask for is logged as a revision rather than as
that round's work and stays off the board: collecting it keeps the
latest version of every section hashed and attributed, while putting it
in the digest would push work into a conversation that did not ask for
it.

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

## Blind reader panels

A blind reader should encounter the book as a reader would. Do not give
that seat the spec, sidecars, workshop debate, or the author's
explanation unless the question specifically requires them. The absence
of that context is the test.

Personas state who is reading. Instruments state what the reader is
asked. Panels allocate those perspectives across explicit models and
harnesses. `quirework book plan` prepares the selected book material.
`quirework sop panel` compiles the declared panel into a run plan. The
generic run engine then freezes and executes the seats.

This pattern is useful at several scales. A sample read can stop after
the opening chapters and report whether the book earns the next page. A
chapter read can find an unexplained term or a broken inference. A full
read-through can show where interest, trust, or comprehension changes
over time.

Blindness does not make a reader correct. It makes the reaction
answerable to the material the reader actually saw. The companion
compares that reaction with the intended reading experience and brings
the difference to the human.

### Two ways to deliver a whole book

Sequential delivery hands the seat one chapter per launch and the
operator controls every reveal. `quirework book packet` builds the
segments and `quirework book plan read` compiles them. The material is
frozen into the record, which is what lets the record certify the bytes
the reader saw, and the cost is one launch per chapter.

A self-driven read launches once. `quirework book packet --prose-dir
DIR` writes the reader-facing projection of each chapter plus the
figures under the names the chapter text references, and `quirework
book plan selfdriven DIR PLAN --mount PATH` compiles the walk: the seat
opens each chapter with its own tools, opens the figures that chapter
references, and writes a reaction file before opening the next one. One
session's prefix only grows, so most of its input is served from cache.

The two are not interchangeable, and the differences belong in the
charter. The self-driven material is mounted rather than frozen, so the
bytes live outside the record and have to be pinned beside it. The seat
also sees its own surroundings, which is why the mount is named
neutrally and nothing else lives beside it: a seat with a shell looks
around before it reads, and the first thing a measured one did was
search for an agent guide. `plan selfdriven` refuses a mount inside the
project, and refuses one matching a declared mount marker.

Figures are where a self-driven read fails silently, because the seat is
handed no images: `history_images` is 0 by design, and a seat that rated
every plate from its caption would look exactly like one that opened
every file. So the turn declares `referenced_images`, and the receipt
reports `referenced_images_opened` and red-flags a shortfall. Any turn
may declare it; a turn whose material is mounted rather than attached is
where it earns its place.

A self-driven read needs its own instrument, not the printed-book one.
A reader instrument opens by telling the seat what it has been handed,
and a sentence promising attached pages is a lie told in the first line
to a seat that fetches its own files. The tool composes the questions it
is given and rewrites nothing: declare a reader preset whose questions
describe a seat that opens its own material, and declare that preset's
`delivery` so the wrong one is refused rather than composed.

The whole book is read inside one turn, so the block the seat repeats
per chapter has to stand on its own. That is the instrument's opening
ask, which states the form, rather than its continuation ask, which says
to reply in the same form as before and has no before here. `--ask`
names another one for an instrument that words it differently, and an
instrument with no declared sequence requires it. `--withhold-wrap`
moves the closing question into a second turn, for a read whose closing
question is itself the measurement and must not be visible while the
seat reads; `--wrap-ask` names it where the sequence does not.

## Consistency review

Consistency has a mechanical layer and a judgment layer. Both belong in
the default workflow.

The mechanical layer catches relationships that can be stated exactly.
`quirework book notes --check` validates the spec and the chapter
sidecars. `quirework book notes bind --slug S` finds every chapter that
expresses one commitment. `book chapters --check`, `book quotes`,
`book figures --check`, `text check`, and `dev check` cover the other
declared invariants. A companion should run the checks named by the SOP
before presenting a change as complete.

The judgment layer asks whether a coherent edit is actually coherent
with the rest of the book. `quirework sop review` gives an independent
reviewer the project guide, the spec, the marks, a brief, and a concrete
Git delta. By default, the reviewer sees the repository read-only. Its
private home does not carry the operator's saved conclusions. An
unjailed fallback is disclosed in the run record.

A range `A..B` covers the commits after `A`, not `A` itself. To review
the last three commits, pass `HEAD~3..HEAD`, not `HEAD~2..HEAD`. Before
the reviewer starts, `review run` prints the commits the range covers
and names the base it leaves out. Read that list before waiting on the
review.

The reviewer must not be the model that made the edit. That requirement
makes a cross-harness check straightforward: a Codex edit can go to
Claude, or a Claude edit can go to Codex. After a repair,
`quirework sop review resume` returns to the same reviewer and lets it
inspect the new delta. Do not brief it with the argument that its first
finding was wrong, because agreement after coaching is not independent
evidence.

These layers catch different failures. The mechanical check can prove
that a binding points to a real spec section. The contextual reviewer
can notice that the chapter now argues the opposite of that section
while still naming it correctly. The companion reconciles both results
and the human decides whether the commitment or the prose should move.

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

A confined seat sees only what is bound into its jail. The system trees
a runner needs (`/usr`, `/etc`, and `/opt`, `/var/lib`, `/sys`, `/nix` or
`/snap` where present) are bound read-only. Nothing else of the machine
exists inside, so the operator's home, network shares and removable
media are simply absent. The home, `/tmp` and `/var/tmp` are empty, and
the seat's own run directory, runner state and credentials are bound
back. A reviewer that must see the repository is given it back
read-only. The seat has its own process and IPC namespaces and a
minimal `/dev`. `sop blind check` prints the root, and a confinement
receipt lists it.

The jail never contains the runtime directory `/run/user/<uid>`. Its
session bus would let a seat ask `systemd --user` to start a process
outside the jail. Antigravity keeps its login in the desktop keyring,
which it reaches over that bus. An Antigravity seat therefore gets a
bus socket from `xdg-dbus-proxy` that answers only for the secrets
service. The receipt marks that as a red flag, because the secrets
service can read every secret in the keyring. Without `xdg-dbus-proxy`
an Antigravity seat is refused rather than run with less confinement.

The jail does not cut off the network, because a seat needs it to reach
its provider. Anything listening on the machine, such as a container
socket or a local server, is reachable from inside.

Compaction is always reported as a red flag. Completed answers remain
available, but a failed run condition does not become a clean result.
Export copies answers and curated receipts. It does not publish them or
copy runtime homes and raw event streams.

Workshop and contextual-review commands retain their specialized state
and manual-session features. They share provider adapters and account
leases with the generic engine.

Project redaction policies are separate data. Changing a marker list changes
the analysis recipe. Primary transcripts are never rewritten by redaction.
