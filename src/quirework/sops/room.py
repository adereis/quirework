"""A writers room: several seats, one board, one round at a time.

Seats never see each other's workspaces. A seat writes to its own outbox and
the controller promotes that file onto the board between rounds, which is the
only moment shared state changes. Promotion is mechanical: it copies, hashes
and attributes, and it never reads a post to decide whether it deserves to be
there. The board a round was shown is therefore one object with one digest,
so "what everyone saw in round three" has an answer.

Rounds exist because a CLI harness answers once and exits. A round is a resume
of the session a seat already holds, not a fresh run of the same assignment:
the seat keeps its reading and its reasoning, and is handed only what changed.

The controller appends to ``room.log`` as it goes. An operator can follow a
running room by reading that file and the per-seat records beside it, without
taking a lock or touching a session.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from quirework.project import current_project, submit
from quirework.core import usage as usage_records
from quirework.providers import blind
from quirework.providers import quota
from quirework.providers import models as drivers
from quirework.runs import observe
from quirework.runs.storage import (
    digest,
    hashes,
    identifier,
    lock,
    process_identity,
    read,
    shown,
    slot,
    write,
)
from quirework.sops import personas

SCHEMA = 1
_LOG_LOCK = Lock()


def event(root, **fields):
    """Append one complete line to the room's watch trail.

    One write of a whole line under O_APPEND is what stops a reader from ever
    seeing half a record, which matters because the point of this file is that
    somebody is tailing it while the room runs.
    """
    line = json.dumps({"t": round(time.time(), 3), **fields}, ensure_ascii=False)
    with _LOG_LOCK:
        fd = os.open(root / "room.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (line + "\n").encode())
        finally:
            os.close(fd)


def load(root, *, verify=True):
    root = Path(root).expanduser().resolve()
    plan = read(root / "plan.json")
    if plan.get("schema") != SCHEMA:
        raise ValueError("unsupported room schema")
    if (
        verify
        and digest(root / "plan.json") != read(root / "plan-digest.json")["sha256"]
    ):
        raise ValueError("frozen room plan changed")
    return root, plan


ROOMS = "rooms"

# A seat's runner home is a live harness home: the adapter binds it over
# `~/.claude` or `~/.codex` for the duration of a turn, so it accumulates that
# harness's credentials. `prepare` accepts a path as readily as a name, and a
# room prepared into a checkout puts `codex/auth.json` one `git add` away from
# being published. The room writes this itself because the operator who most
# needs it is the one who has not yet looked inside a room directory.
IGNORE = """\
# A seat's private runner home. It is a live harness home and holds that
# harness's credentials. Never commit it, and never copy one anywhere.
seats/*.blind-state/
"""


def spelled_as_path(value):
    """Whether the operator spelled a path rather than naming a room.

    The rule lives here rather than inside `room_root` so a caller can say
    which of the two resolutions it used without restating the rule and
    drifting from it.
    """
    text = str(value)
    return "/" in text or text.startswith("~")


def room_root(value, *, must_exist=True):
    """Resolve a room by name under the project, or by an explicit path.

    A bare name is a room this project owns and is looked up in the project's
    rooms directory. Anything containing a separator is a path the operator
    spelled out. The two never overlap, so `status foo` and `status ./foo`
    name different things and the error says which one was tried.

    Rooms live outside the checkout, in a directory nobody navigates to by
    hand, so resolving a bare name is the difference between naming a room and
    remembering where the state root hashed it to.
    """
    text = str(value)
    if spelled_as_path(text):
        root = Path(text).expanduser().resolve()
    else:
        root = (current_project().state_root / ROOMS / identifier(text)).resolve()
    if must_exist and not (root / "plan.json").is_file():
        raise ValueError(
            f"no room at {shown(root)}; name a room under "
            f"{shown(current_project().state_root / ROOMS)} or give a path to one"
        )
    return root


def state_path(root, seat):
    return root / "records" / seat / "state.json"


def label(seat):
    return seat["persona_frozen"]["profile"]["label"]


def phases(plan):
    """The room's schedule, as a phase per round.

    A room without phases is one long phase in which everyone speaks every
    round, which is what every room did before phases existed.
    """
    declared = plan.get("phases")
    if not declared:
        return [{"name": "discussion", "rounds": plan["rounds"]}]
    return declared


def phase_for(plan, n):
    """Which phase round ``n`` belongs to, and nothing about who acts in it."""
    first = 1
    for phase in phases(plan):
        if first <= n < first + phase["rounds"]:
            return phase
        first += phase["rounds"]
    return phases(plan)[-1]


def acting(plan, n):
    """The seats that speak in round ``n``.

    A phase may seat only some of the room. The rest are not absent and have
    not failed: they are listening, which is a normal thing to do while one
    person reads their draft aloud.
    """
    named = phase_for(plan, n).get("seats")
    every = [s["id"] for s in plan["seats"]]
    if not named:
        return every
    unknown = set(named) - set(every)
    if unknown:
        raise ValueError(
            f"phase names seats this room does not have: {sorted(unknown)}"
        )
    return [s for s in every if s in named]


def start_text(root, plan, seat):
    """What a writer is told once: who they are, what to read, where to post.

    Deliberately says nothing about rounds, promotion, outboxes as a mechanism
    or the controller that runs them. A writer in a room knows the room, not
    the building's plumbing, and a model told it is executing a procedure
    starts writing like one. `{{persona}}` is a slot personas.compose fills in
    place, which keeps a machine header off the top of a person's character.
    """
    # Relative to the seat's own workspace, which the jail makes its working
    # directory. An absolute path here is the operator's home layout, restated
    # in a writer's context on every turn, naming a machine they cannot see.
    reads = "".join(f"- input/{rel}\n" for rel in seat["reads"])
    text = (
        "{{persona}}\n\n"
        "You are in a room of writers working on the same material.\n\n"
        f"Read:\n\n{reads}\n"
        "Post to outbox/post.md, replacing your last post each\n"
        f"time. That file is what the room sees. About {plan['post_words']} words.\n"
    )
    roster = holdings(plan)
    if roster:
        text += "\nIn the room, and what each person brought:\n\n" + roster
    return personas.compose(text, seat["persona_frozen"])


def holdings(plan):
    """Who is in the room and what each of them was given beyond the common set.

    Seats are handed different material on purpose, and that asymmetry is what
    makes their disagreement substantive. It also makes a colleague's citation
    impossible to check: a seat without the file cannot tell a private source
    from an invention, and a room told to cite its sources will eventually
    accuse someone of fabricating one. Naming the holdings is the same courtesy
    as seeing a colleague reach for the book they brought.
    """
    common = (
        set.intersection(*(set(s.get("reads", [])) for s in plan["seats"]))
        if plan["seats"]
        else set()
    )
    # A path is only the default name. Whatever is in it reaches every seat,
    # so a filename carrying a model id, an author or a verdict puts that in
    # the room: one measured run named six reader reports after the models
    # that wrote them and had the seats addressing each other as "Sol" and
    # "Astra" within two rounds, each attached to the wrong colleague. A
    # `label` on the file says what it is without saying what it is called.
    named = {
        entry["path"]: entry["label"]
        for seat in plan["seats"]
        for entry in seat.get("files", [])
        if entry.get("label")
    }
    lines = []
    for seat in plan["seats"]:
        extra = sorted(set(seat.get("reads", [])) - common)
        shown_as = [named.get(rel, rel) for rel in extra]
        brought = ", ".join(shown_as) if extra else "nothing the rest of you lack"
        lines.append(f"- {label(seat)}: {brought}\n")
    return "".join(lines)


def chair_note(root, n):
    """What the operator told the room before round ``n``, if anything.

    A room is a closed conversation on purpose, and the operator is the only
    person who can see both the room and the world it is writing for. The
    packet is hashed, so a fact discovered after preparation cannot be
    corrected in it, and the board carries posts and nothing else. In the
    first measured room four seats put direct questions to the chair in their
    opening round -- a miscounted phrase, a reference with two possible
    referents, a ruling only the editor held -- and nothing could reach them.

    The note travels with the turn so every acting seat is told the same thing
    at the same instant, and it is copied onto that round's digest so the
    record shows what the room was working from. It is not a post: the chair
    does not take a seat, is not answered by name, and never arrives in the
    middle of a round.
    """
    path = root / "chair" / f"r{n:03d}.md"
    if not path.exists():
        return None
    return path.read_text().strip() or None


def turn_prompt(root, plan, seat, n, *, cold=False):
    """The one line a writer gets when the room moves.

    Everything standing -- the perspective, the reading, the length, where a
    post goes -- is already in the session from the first turn, so repeating
    it every round would buy nothing and cost tokens on every seat.
    """
    phase = phase_for(plan, n)
    words = phase.get("words", plan["post_words"])
    extra = phase.get("instruction", "").strip()
    # Relative to the seat's workspace, as in the standing assignment: this
    # line is sent on every round, so an absolute path is the operator's home
    # directory spelled out again in a writer's context every time the room
    # moves, for a file they are already standing next to.
    opening = n == 1 or n in plan["silent_rounds"]
    if opening:
        text = (
            "Read START.md and post. Nobody has posted yet, so say "
            "what you think before you hear anyone else."
        )
    elif cold:
        # This seat has no session, so it has none of the standing assignment
        # and none of the conversation. Saying only that the board moved would
        # be addressed to a reader who has never read the brief.
        text = (
            "You are joining a room already in progress. Read START.md "
            "first, then the boards in board/, beginning with "
            f"board/round-{n - 1:03d}/digest.md. Then post as the others "
            "do. Answer your colleagues by name. You were expected earlier "
            "and could not be reached, so the room has not heard from you "
            "yet and nothing it has said is a reply to you."
        )
    elif others_spoke(root, plan, seat["id"], n - 1):
        text = (
            f"The board has moved: board/round-{n - 1:03d}/digest.md. Read it "
            "and post again, replacing what is there. Answer your colleagues "
            "by name."
        )
    else:
        # A phase seating one writer hands that writer a board with no other
        # voice on it, and announcing that the board has moved is then false.
        # A drafter reads that silence as an answer: a measured room had its
        # drafter say "absent an answer I am treating that beat as landed"
        # about a note three colleagues were writing at that moment, one round
        # behind where she had been told to look.
        heard = last_heard(root, plan, seat["id"], n - 1)
        text = (
            "Nobody else has posted since"
            + (
                f" round {heard}: board/round-{heard:03d}/digest.md."
                if heard
                else " the room opened."
            )
            + " Post again, replacing what is there. Anything you asked them "
            "is still outstanding, not declined."
        )
    missed = [r for r in range(1, n) if seat["id"] not in acting(plan, r)]
    if missed and not opening:
        # A seat that sat out a phase comes back to a board it has not read.
        # Saying only that the board moved would have it answer the last round
        # as though it were the whole conversation. A silent round is shown no
        # board at all, deliberately, so it is not told where the others are.
        text += (
            f" You did not post in round{'s' if len(missed) > 1 else ''} "
            f"{', '.join(str(r) for r in missed)}; those boards are in "
            "board/ and are worth reading first."
        )
    made = deliverable(plan, n)
    if made:
        # Two files because they are two things, and the word count belongs to
        # the work rather than to the note about it. Afterwards the work is a
        # file, not a passage somebody has to find inside a conversation.
        text += (
            f" Write the work itself to outbox/{made}, as finished text and"
            f" nothing else, about {words} words. Your post is your note to the"
            f" room about it, about {plan['post_words']} words."
        )
    elif words != plan["post_words"]:
        text += f" About {words} words this time."
    text = f"{text}{(' ' + extra) if extra else ''}"
    note = chair_note(root, n)
    if note:
        text += f"\n\nFrom the chair, to everyone acting this round:\n\n{note}"
    return f"{text}\n"


def prepare(plan_path, destination, *, resolve_models=True):
    """Stage one workspace per seat and freeze the plan that governs them."""
    plan_path = Path(plan_path).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    supplied = read(plan_path)
    if destination.exists():
        raise ValueError("room exists; continue or inspect it, do not replace it")
    source_root = Path(supplied.get("source_root", current_project().root)).resolve()
    rounds = supplied.get("rounds")
    if not isinstance(rounds, int) or rounds < 1:
        raise ValueError("rounds must be a positive integer")
    silent = supplied.get("silent_rounds", [])
    if not isinstance(silent, list) or any(
        r not in range(1, rounds + 1) for r in silent
    ):
        raise ValueError("silent_rounds must name declared rounds")
    if not isinstance(supplied.get("post_words"), int) or supplied["post_words"] < 1:
        raise ValueError("post_words must be a positive integer")
    if not supplied.get("seats"):
        raise ValueError("declare at least one seat")
    declared = supplied.get("phases") or []
    if declared:
        if sum(p["rounds"] for p in declared) != rounds:
            raise ValueError("phase rounds must add up to the room's rounds")
        named = {s["id"] for s in supplied["seats"]}
        for phase in declared:
            if not isinstance(phase.get("rounds"), int) or phase["rounds"] < 1:
                raise ValueError("each phase needs a positive round count")
            if set(phase.get("seats") or named) - named:
                raise ValueError(f"phase {phase.get('name')!r} names unknown seats")
            if not phase.get("seats", named):
                raise ValueError(f"phase {phase.get('name')!r} seats nobody")
            if "deliverable" in phase:
                safe_name(phase["deliverable"])

    fields = ("harness", "model", "effort")
    config = {k: supplied[k] for k in fields}
    if resolve_models:
        config = drivers.resolve(config["harness"], config["model"], config["effort"])

    profiles = plan_path.parent / supplied.get("profiles_dir", "profiles")
    seats = []
    for item in supplied["seats"]:
        source = profiles / f"{item['persona']}.json"
        if not source.is_file():
            # A persona id is not guessable, and an assistant writing a plan
            # will invent one. Name the ones that exist so the fix is a copy.
            offered = sorted(p.stem for p in profiles.glob("*.json"))
            raise ValueError(
                f"seat {item['id']!r}: no persona {item['persona']!r} in "
                f"{shown(profiles)}; it has {', '.join(offered) or 'none'}"
            )
        profile = personas.validate(personas.parse(source.read_text()))
        # A seat may name its own model. Everything downstream already takes a
        # config rather than reading the room's, so a mixed room needs no
        # special case beyond resolving each seat separately.
        chosen = {
            **{k: supplied[k] for k in fields},
            **{k: item[k] for k in fields if k in item},
        }
        if resolve_models:
            chosen = drivers.resolve(
                chosen["harness"], chosen["model"], chosen["effort"]
            )
        # The digest tells the room which seats can search the web, so the
        # claim has to be true: the policy travels in the seat's config to the
        # launcher, and a harness that cannot enforce it is refused here
        # rather than marked on the board as if it could.
        tools = item.get("tools", "write")
        if tools not in TOOLS:
            raise ValueError(f"seat {item['id']!r}: tools must be one of {TOOLS}")
        if tools == "research" and chosen["harness"] == "agy":
            raise ValueError(
                f"seat {item['id']!r}: agy cannot confine a seat to a tool set, "
                "so a research seat there cannot be told from a writing one"
            )
        chosen = {**chosen, "tools": tools}
        seats.append(
            dict(
                item,
                id=identifier(item["id"]),
                config=chosen,
                persona_frozen=personas.resolve({"inline": profile}),
            )
        )
    if len({s["id"] for s in seats}) != len(seats):
        raise ValueError("duplicate seat id")

    def staged(entry):
        if "local_source" in entry:
            return (plan_path.parent / entry["local_source"]).read_bytes()
        return (source_root / entry["source"]).read_bytes()

    shared = {supplied["brief"]["path"]: staged(supplied["brief"])}
    for entry in supplied.get("shared_files", []):
        shared[entry["path"]] = staged(entry)

    destination.mkdir(parents=True, mode=0o700)
    (destination / ".gitignore").write_text(IGNORE)
    (destination / "board" / "posts").mkdir(parents=True)
    (destination / "board" / "rounds").mkdir(parents=True)
    (destination / "chair").mkdir()
    frozen = dict(
        supplied,
        schema=SCHEMA,
        config=config,
        seats=seats,
        source_root=str(source_root),
        silent_rounds=silent,
        prepared_at=time.time(),
    )
    for seat in seats:
        work = destination / "seats" / seat["id"]
        for sub in ("input", "outbox", "board"):
            (work / sub).mkdir(parents=True)
        files = dict(shared)
        for entry in seat.get("files", []):
            files[entry["path"]] = staged(entry)
        for rel, content in files.items():
            target = work / "input" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        seat["reads"] = sorted(files)
        seat["input_hashes"] = hashes(work / "input")
        (destination / "board" / "posts" / seat["id"]).mkdir(parents=True)
        (destination / "records" / seat["id"]).mkdir(parents=True)
        write(
            state_path(destination, seat["id"]),
            {
                "status": "prepared",
                "session_id": None,
                "rounds": {},
                "persona": personas.identity(seat["persona_frozen"]),
            },
        )
    # Second pass: the roster in START.md names what every seat holds, which
    # is only knowable once each of them has been staged.
    for seat in seats:
        start = destination / "seats" / seat["id"] / "START.md"
        start.write_text(start_text(destination, frozen, seat))
        seat["start_sha256"] = digest(start)
    write(destination / "plan.json", frozen)
    write(
        destination / "plan-digest.json",
        {"sha256": digest(destination / "plan.json")},
    )
    event(destination, event="prepared", seats=[s["id"] for s in seats], rounds=rounds)
    # The roster is the one part of the packet an operator writes without
    # reading: it is assembled from paths chosen for the filesystem and then
    # shown to every seat. Printing it at the last moment anything can change
    # is what turns a filename back into a decision.
    frozen["roster"] = holdings(frozen)
    return frozen


# A room that has stopped producing positions keeps producing paragraphs, so
# it cannot be trusted to notice it is finished. Both signals below read the
# board and nothing else: no model call is spent asking a room whether it is
# done. They are heuristics fitted to two ten-round rooms, not laws, which is
# why a plan can move either of them.
STOP_NOVELTY = 55.0
STOP_CLOSING = 0.75
CLOSING = re.compile(
    r"\b(unanimous|our work here|the room has|settled|adjourn|send the contract"
    r"|sign the|close the room|no more objections|verdict)",
    re.IGNORECASE,
)
WORD = re.compile(r"[a-z][a-z'-]+")
COMMON = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "is",
        "it",
        "that",
        "this",
        "for",
        "with",
        "as",
        "on",
        "not",
        "you",
        "we",
        "they",
        "be",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "will",
        "would",
        "can",
        "could",
        "their",
        "its",
        "his",
        "her",
        "them",
        "me",
        "my",
        "our",
        "your",
        "if",
        "then",
        "than",
        "so",
        "what",
        "which",
        "who",
        "when",
        "how",
        "all",
        "any",
        "each",
        "other",
        "more",
        "most",
        "some",
        "such",
        "no",
        "nor",
        "only",
        "own",
        "same",
        "too",
        "very",
        "just",
        "do",
        "does",
        "did",
        "at",
        "by",
        "from",
        "up",
        "down",
        "out",
        "off",
        "over",
        "under",
        "again",
        "once",
        "here",
        "there",
        "why",
        "while",
        "about",
        "against",
        "between",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
    ]
)


def _content_words(text):
    return {w for w in WORD.findall(text.lower()) if w not in COMMON and len(w) > 3}


def spoke(root, plan, n):
    """The seats that both acted in round ``n`` and left a post to read.

    A seat that was not seated by the phase never had a turn, and a seat whose
    turn produced nothing is recorded absent. Neither has said anything the
    stop rule may read as the room running out of things to say.
    """
    return [
        name
        for name in acting(plan, n)
        if (root / "board" / "posts" / name / f"r{n:03d}.md").exists()
    ]


def others_spoke(root, plan, seat, n):
    """Whether anyone but ``seat`` left a post on round ``n``'s board."""
    return bool([name for name in spoke(root, plan, n) if name != seat])


def last_heard(root, plan, seat, n):
    """The most recent round up to ``n`` where ``seat`` heard somebody else.

    A drafter coming out of a solo phase needs the board where the room last
    spoke, not the one where it last changed. Those are the same round for
    everyone except the seat that was drafting alone.
    """
    return next((r for r in range(n, 0, -1) if others_spoke(root, plan, seat, r)), None)


def convergence(root, plan, n):
    """How close the room is to having nothing left to say, after round ``n``.

    Novelty is the share of a post's content words that seat has never used
    before; it falls as writers begin restating themselves. Closing is the
    share of seats reaching for consensus language. Either signal must hold
    for two consecutive rounds, because a single round of agreement is noise:
    one measured room hit three-quarters closing at round four and was back to
    one-quarter at round five, still arguing.
    """
    seats = [s["id"] for s in plan["seats"]]
    seen = {s: set() for s in seats}
    series = []
    for number in range(1, n + 1):
        novel, closers = [], 0
        # Only the seats that spoke. A resting seat scored zero novelty and was
        # averaged in with the rest, so a phase that seats one writer read as a
        # room three-quarters out of things to say: the drafting round of the
        # first real room reported convergence at 15 percent while the drafter
        # was putting the first new page of the book on the board. Silence
        # under a phase is not a seat repeating itself. It is not a turn.
        for name in spoke(root, plan, number):
            text = (root / "board" / "posts" / name / f"r{number:03d}.md").read_text()
            words = _content_words(text)
            novel.append(100 * len(words - seen[name]) / max(1, len(words)))
            seen[name] |= words
            closers += bool(CLOSING.search(text))
        series.append(
            {
                "round": number,
                "novelty": round(sum(novel) / len(novel), 1) if novel else None,
                "closing": round(closers / len(novel), 2) if novel else None,
            }
        )
    quiet_at = plan.get("stop_novelty", STOP_NOVELTY)
    closed_at = plan.get("stop_closing", STOP_CLOSING)
    last = series[-2:]
    # A round nobody spoke in measures nothing, so it cannot be one of the two
    # consecutive rounds that stop a room. Dropping it instead would let two
    # rounds either side of it read as consecutive when they are not.
    usable = last if all(x["novelty"] is not None for x in last) else []
    quiet = len(usable) == 2 and all(x["novelty"] <= quiet_at for x in usable)
    closed = len(usable) == 2 and all(x["closing"] >= closed_at for x in usable)
    return {
        "round": n,
        "novelty": series[-1]["novelty"] if series else None,
        "closing": series[-1]["closing"] if series else None,
        "converged": quiet or closed,
        "reason": "novelty" if quiet else "closing" if closed else None,
        "series": series,
    }


# What a seat may do beyond writing its post: `write` is the room's floor,
# `research` adds the harness's web tools for a seat that must check sources.
TOOLS = ("write", "research")


def seat_config(plan, seat):
    """This seat's model. Seats may differ; the room supplies the default."""
    return seat.get("config") or plan["config"]


def budget(root, plan):
    """How many rounds this room may run: what it declared, plus extensions.

    The frozen plan holds the assignment -- personas, packets, the standing
    brief -- and must stay byte-identical for its digest to mean anything. How
    long the room runs is not part of the assignment; it is a decision the
    operator keeps making. Keeping it in its own file lets the budget move
    without ever touching what the writers were promised.
    """
    path = root / "budget.json"
    return read(path)["rounds"] if path.exists() else plan["rounds"]


def extend(root, rounds):
    """Raise the round budget. One way, because lowering it orphans rounds."""
    root, plan = load(root)
    current = budget(root, plan)
    if not isinstance(rounds, int) or rounds <= current:
        raise ValueError(f"this room already allows {current} rounds")
    write(root / "budget.json", {"rounds": rounds, "declared": plan["rounds"]})
    event(root, event="budget-extended", rounds=rounds, was=current)
    return rounds


def board_order(seats, n):
    """Seat order for round ``n``'s digest, rotated so no seat always leads.

    Whoever appears first anchors the read. A fixed order hands the same seat
    that advantage in every round; rotating by the round number spreads it
    evenly and keeps the digest deterministic.
    """
    names = [s["id"] for s in seats]
    offset = (n - 1) % len(names)
    return names[offset:] + names[:offset]


def deliverable(plan, n):
    """The file round ``n``'s phase asks its seats to write, if it asks for one.

    A drafting phase produces two different things that were previously one:
    a note to the room, and the passage itself. Welded together in a post, the
    passage has to be cut back out by hand afterwards, against whatever
    separator the room improvised. Named here, it is a file.
    """
    return phase_for(plan, n).get("deliverable")


def declared_files(plan):
    """Every file this plan asks for anywhere, not only in the current round.

    A seat that corrects a section after its phase has passed has nowhere to
    put the correction: only one file is asked for per round, and a file left
    in an outbox is invisible until something asks for it again. In the
    measured run the drafter fixed the preface four rounds after the preface
    phase, then spent a round arguing that the book would be bound stale,
    because nothing had acknowledged the file. Taking any file the plan names
    costs nothing and keeps the latest version of every section collected,
    hashed and attributed the moment it exists.
    """
    return sorted({p["deliverable"] for p in phases(plan) if p.get("deliverable")})


def safe_name(value):
    """A deliverable name a seat may write and the room may copy.

    The seat chooses the contents and the plan chooses the name, but the name
    is a path the controller resolves inside a seat's outbox, so it stays a
    plain filename. Anything else is a way out of the directory.
    """
    text = str(value)
    if not text or "/" in text or text.startswith(".") or text != Path(text).name:
        raise ValueError(f"deliverable must be a plain file name: {value!r}")
    return text


def attributed(text, plan):
    """The board as the operator reads it: each post's seat named by model.

    Deliberately applied when printing and never when composing. The stored
    digest is what the seats are shown, and a model name on it would tell
    every writer which model wrote each post, which is a different room: a
    seat would start weighing a colleague by its make rather than by what it
    said. The operator has the opposite need, because the seats are the
    instrument and reading the room means knowing which one spoke.

    It also answers a real failure. A seat invented model nicknames for
    colleagues it could not see and attached one of them to the wrong
    argument, which is invisible until someone holds the board beside the
    plan.
    """
    named = {
        s["id"]: f"{s['config']['harness']}/{s['config'].get('model')}"
        for s in plan["seats"]
    }
    for seat, made in named.items():
        text = text.replace(f"({seat})", f"({seat}, {made})")
    return text


def compose_digest(root, plan, n):
    """Concatenate round ``n``'s posts, attributed and whole. Never summarize.

    A digest that compressed four posts into a paragraph would make the
    controller the room's narrator, and every later round would be a response
    to its reading rather than to what the seats actually wrote.
    """
    rule = "-" * 68
    labels = {s["id"]: label(s) for s in plan["seats"]}
    research = {s["id"] for s in plan["seats"] if s.get("tools") == "research"}
    # Attribution is the whole frame. A name above a post is what makes it a
    # colleague's opinion rather than loose text next to the brief, and it
    # needs no sentence explaining itself. Describing the order, the promotion
    # or the rotation would only tell a writer about machinery it does not
    # occupy.
    made = deliverable(plan, n)
    text = f"# The board -- round {n}\n\n"
    note = chair_note(root, n)
    if note:
        text += f"{rule}\n## From the chair\n{rule}\n\n{note}\n\n"
    included = {}
    for name in board_order(plan["seats"], n):
        post = root / "board" / "posts" / name / f"r{n:03d}.md"
        if not post.exists():
            continue
        mark = "  [can search the web]" if name in research else ""
        text += f"{rule}\n## {labels[name]}  ({name}){mark}\n{rule}\n\n"
        text += post.read_text().strip() + "\n\n"
        included[name] = digest(post)
        # A phase's deliverable is the work itself, and the room still has to
        # read it. It goes on the board under its author's name like anything
        # else, so a reviewer sees the note and the thing the note is about,
        # while the file stays separately readable as a file.
        work = root / "board" / "made" / name / f"r{n:03d}" / made if made else None
        if work and work.exists():
            text += f"### {made}\n\n{work.read_text().strip()}\n\n"
    absent = [
        s["id"]
        for s in plan["seats"]
        if s["id"] in acting(plan, n) and s["id"] not in included
    ]
    if absent:
        names = ", ".join(labels[a] for a in absent)
        text += f"{rule}\n\nNo post this round from: {names}.\n"
    return text, included, absent


def promote(root, n):
    """Move every outbox onto the board, then publish the round's digest.

    This is the only writer of shared state and it runs with every seat
    stopped. It copies, hashes and attributes; it never reads a post to decide
    whether it belongs. A missing or empty outbox means the seat was absent
    for the round, which is a fact about the round and not a failure.
    """
    root, plan = load(root)
    speaking = acting(plan, n)
    for seat in plan["seats"]:
        if seat["id"] not in speaking:
            continue
        outbox = root / "seats" / seat["id"] / "outbox" / "post.md"
        if not outbox.exists() or not outbox.read_bytes().strip():
            event(root, round=n, seat=seat["id"], event="absent")
            continue
        post = root / "board" / "posts" / seat["id"] / f"r{n:03d}.md"
        if post.exists():
            raise RuntimeError(f"round {n} already promoted for {seat['id']}")
        shutil.copyfile(outbox, post)
        outbox.unlink()
        event(
            root,
            round=n,
            seat=seat["id"],
            event="promoted",
            bytes=post.stat().st_size,
            sha256=digest(post),
        )
        made = deliverable(plan, n)
        for name in declared_files(plan):
            written = root / "seats" / seat["id"] / "outbox" / name
            if not written.exists() or not written.read_bytes().strip():
                if name == made:
                    # The seat posted and did not produce the file the phase
                    # asked for. That is a fact about the round, like an
                    # absence, and not a reason to fail a round whose post is
                    # already on the board.
                    event(root, round=n, seat=seat["id"], event="unmade", file=name)
                continue
            kept = root / "board" / "made" / seat["id"] / f"r{n:03d}" / name
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(written, kept)
            written.unlink()
            event(
                root,
                round=n,
                seat=seat["id"],
                event="made" if name == made else "revised",
                file=name,
                bytes=kept.stat().st_size,
                sha256=digest(kept),
            )
    text, included, absent = compose_digest(root, plan, n)
    out = root / "board" / "rounds" / f"r{n:03d}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "digest.md").write_text(text)
    write(
        out / "digest.json",
        {
            "round": n,
            "order": board_order(plan["seats"], n),
            "posts": included,
            "absent": absent,
            "digest_sha256": digest(out / "digest.md"),
        },
    )
    signal = convergence(root, plan, n)
    write(out / "convergence.json", signal)
    event(root, round=n, event="round-closed", posted=len(included), absent=len(absent))
    event(
        root,
        round=n,
        event="convergence",
        novelty=signal["novelty"],
        closing=signal["closing"],
        converged=signal["converged"],
        reason=signal["reason"],
    )
    return {
        "posted": sorted(included),
        "absent": absent,
        "convergence": {
            k: signal[k] for k in ("novelty", "closing", "converged", "reason")
        },
    }


def open_round(root, n):
    """Put the previous round's digest inside every workspace, read-only."""
    root, plan = load(root)
    if n == 1 or n in plan["silent_rounds"]:
        return None
    source = root / "board" / "rounds" / f"r{n - 1:03d}" / "digest.md"
    if not source.exists():
        raise RuntimeError(f"round {n - 1} has not been promoted; nothing to show")
    for seat in plan["seats"]:
        target = root / "seats" / seat["id"] / "board" / f"round-{n - 1:03d}"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target / "digest.md")
    event(root, round=n, event="board-published", digest_sha256=digest(source))
    return str(source)


def _terminate(proc):
    """Stop a seat and everything it started; it is its own session leader."""
    for number in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), number)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=20)
            return
        except subprocess.TimeoutExpired:
            continue


def compaction(stream):
    """Compaction markers in one turn's event stream, if the harness said so.

    A room's whole economy rests on a round being a resume: the seat keeps its
    reading and its reasoning and is handed only what changed. Compaction is
    that continuity being silently rewritten mid-conversation, which is why
    `docs/workflows.md` makes it a red flag for every procedure that invokes a
    harness. The room had no way to raise it, so a compacted room and a clean
    one produced identical records.

    Detected, never enforced: the answer is already bought and stays
    available, and a supervisor decides what a compacted round is worth.
    """
    if not stream.exists():
        return []
    try:
        return observe.compaction_markers(observe.native_rows(stream))
    except (ValueError, OSError):
        # A malformed or truncated stream is the failure the receipt already
        # reports. It must not also turn into a missing compaction check that
        # reads as a clean one.
        return [{"timestamp": None, "tags": ["unreadable-stream"]}]


def run_seat(root, plan, seat, n, *, timeout=None):
    """Run one seat's turn for round ``n``: launch it, or resume its session."""
    name = seat["id"]
    work, config = root / "seats" / name, seat_config(plan, seat)
    record = root / "records" / name / f"r{n:03d}"
    state = read(state_path(root, name))
    if str(n) in state["rounds"]:
        return state["rounds"][str(n)]["status"]
    # The packet and the standing assignment are frozen and checked every
    # round. The board is deliberately not: it is the one thing that is
    # supposed to change, which is why it lives outside `input/`.
    if hashes(work / "input") != seat["input_hashes"]:
        raise RuntimeError(f"seat input changed: {name}")
    if digest(work / "START.md") != seat["start_sha256"]:
        raise RuntimeError(f"standing assignment changed: {name}")
    resume = state.get("session_id")
    # A seat with no session is one whose earlier turn produced nothing at
    # all: a provider refusing before the model ran leaves a failed round and
    # no conversation to continue. Refusing to run it again costs the seat for
    # the rest of the room, and the alternative the procedure offered was to
    # throw away every turn the other seats had already bought. So it opens a
    # session now and joins in progress, reading the boards it missed. What it
    # cannot recover is its opening position, which the room heard from
    # everyone else before it arrived; `status` reports the round it joined so
    # no later reading mistakes a late arrival for an independent opening.
    cold = n > 1 and not resume
    if resume:
        blind.require_session(work, config["harness"], resume)
    record.mkdir(parents=True)
    return launch_turn(
        root,
        plan,
        seat,
        n,
        turn_prompt(root, plan, seat, n, cold=cold),
        resume,
        timeout=timeout,
    )


def launch_turn(
    root,
    plan,
    seat,
    n,
    prompt,
    resume,
    *,
    timeout=None,
    workflow="room",
    readonly=("board",),
):
    """Run one turn and bank what it produced, however it ended.

    Split from ``run_seat`` so a continuation after a quota stop is the same
    launch, the same jail, the same receipt and the same records as the turn
    it replaces. A recovery that took a different path would be evidence
    about the recovery rather than about the room.

    Public because the roundtable takes its turns through it too: one jail,
    one receipt and one record layout for every procedure that seats models
    in rounds. ``readonly`` names the workspace folders bound read-only over
    the writable workspace, and ``workflow`` is what the usage record calls
    the procedure that launched the seat.
    """
    name = seat["id"]
    work, config = root / "seats" / name, seat_config(plan, seat)
    record = root / "records" / name / f"r{n:03d}"
    record.mkdir(parents=True, exist_ok=True)
    # A family such as `opus` means whatever is newest when the CLI runs, so
    # a resumed session names the version its first answer was served on. A
    # release between rounds would otherwise switch the seat's model while
    # the room reads it as the same voice. A seat whose plan already names
    # that exact version needs no pin, and claiming one would add nothing.
    pinned = read(state_path(root, name)).get("model_served") if resume else None
    if pinned and pinned != config["model"]:
        config = {**config, "model_pinned": pinned}
    proven = drivers.canary(config, f"{workflow} seat {name}")
    # Open before the jail is built, since its argv names the proxy's socket,
    # and closed only after the seat's process has exited.
    with blind.session_bus([config["harness"]]) as bus:
        args, payload = drivers.command(config, work, prompt, resume=resume)
        jail, env, private = drivers.launch_context(config, work, bus, proven)
        for folder in readonly:
            jail += ["--ro-bind", str(work / folder), str(work / folder)]
        (record / "prompt.md").write_text(prompt)
        usage_records.seat(workflow, config)
        write(
            record / "launch.json",
            {
                "argv": args,
                "jail": jail,
                "resumed": resume,
                "harness_version": blind.runner_version(config["harness"]),
                "private_runtime": str(private),
                "window_assurance": drivers.window_assurance(config["harness"]),
                "session_bus": bus.receipt() if bus else None,
                "canary": proven,
            },
        )
        with slot(config["harness"]):
            event(root, round=n, seat=name, event="launching", resumed=resume)
            started = time.time()
            with (
                (record / "events.jsonl").open("xb") as out,
                (record / "stderr.log").open("xb") as err,
            ):
                proc = blind.popen(
                    jail + args,
                    bus,
                    stdin=subprocess.PIPE,
                    stdout=out,
                    stderr=err,
                    cwd=current_project().root,
                    env=env,
                    start_new_session=True,
                )
                event(
                    root,
                    round=n,
                    seat=name,
                    event="running",
                    pid=proc.pid,
                    process_identity=process_identity(proc.pid),
                )
                timed_out = False
                try:
                    proc.communicate(payload, timeout=timeout)
                except subprocess.TimeoutExpired:
                    _terminate(proc)
                    proc.communicate()
                    timed_out = True
            code, seconds = proc.returncode, round(time.time() - started, 2)
            event(
                root,
                round=n,
                seat=name,
                event="exited",
                code=code,
                seconds=seconds,
                timed_out=timed_out,
            )
    try:
        got = drivers.receipt(config, record, private, resumed=resume)
        status, detail = "answered", None
    except (RuntimeError, ValueError, OSError) as exc:
        got, status, detail = {}, "failed", str(exc)
    compacted = compaction(record / "events.jsonl")
    # A provider limit and a crash both leave a failed turn, and only one of
    # them is worth waiting out. Classifying here is what lets `continue`
    # refuse everything else: a timeout re-run looks identical to a quota
    # re-run at the command line and duplicates work that actually happened.
    stop = quota.classify(config["harness"], record) if status == "failed" else None
    if stop:
        status = "quota"
    write(
        record / "receipt.json",
        {
            **got,
            "exit_code": code,
            "seconds": seconds,
            "timed_out": timed_out,
            "error": detail,
            "compaction_markers": compacted,
            "quota_stop": stop["phase"] if stop else None,
            "quota_scope": stop.get("scope") if stop else None,
            "quota_resets_at": stop.get("resets_at") if stop else None,
            "red_flags": [blind.Bus.flag(bus.receipt())] if bus else [],
        },
    )
    if compacted:
        event(root, round=n, seat=name, event="compacted", markers=len(compacted))
    state = read(state_path(root, name))
    if got.get("session_id"):
        state["session_id"] = got["session_id"]
    if status == "answered" and got.get("model") and not state.get("model_served"):
        state["model_served"] = got["model"]
    state["rounds"][str(n)] = {
        "status": status,
        "seconds": seconds,
        "error": detail,
        **(
            {"quota_stop": stop["phase"], "quota_scope": stop.get("scope")}
            if stop
            else {}
        ),
    }
    # A refused stop leaves no session; an interrupted one leaves the session
    # holding the reasoning, and losing its id here is what turns a
    # continuation into a cold restart wearing a continuation's receipt.
    if stop and stop.get("session_id") and not state.get("session_id"):
        state["session_id"] = stop["session_id"]
    state["status"] = status
    write(state_path(root, name), state)
    event(root, round=n, seat=name, event="collected", status=status, error=detail)
    return status


def continue_seat(root, n, name, *, timeout=None):
    """Take one seat's turn again after a provider limit stopped it.

    Two stops, opposite recoveries, and `quota` owns which is which. *Refused*
    means the provider declined before the model ran, so nothing exists to
    continue: the identical turn is delivered again, in a fresh session, out
    of the prompt the stopped attempt was given. Nothing is lost by that,
    because nothing was produced. *Interrupted* means the model ran and the
    wall arrived partway, so the session holds the reading and the reasoning
    and the turn is continued inside it, with no re-delivery of anything the
    seat has already been shown.

    It refuses everything that is not a classified quota stop. A crash, a
    timeout and an ambiguous transport fault look identical to this at the
    command line, and re-running one of those can duplicate work that really
    happened. The stopped attempt is archived whole beside the new one, so the
    round reads as two submissions rather than one that changed its mind, and
    the receipt says so.
    """
    root, plan = load(root)
    # `seat_named` resolves an id or a position and returns the id, so the
    # seat itself still has to be looked up.
    name = seat_named(plan, name)
    seat = next(s for s in plan["seats"] if s["id"] == name)
    record = root / "records" / name / f"r{n:03d}"
    state = read(state_path(root, name))
    attempt = state.get("rounds", {}).get(str(n))
    if not attempt:
        raise ValueError(f"{name} has no round {n} to continue")
    if attempt["status"] == "answered":
        raise ValueError(f"{name} already answered round {n}")
    if (root / "board" / "posts" / name / f"r{n:03d}.md").exists():
        raise RuntimeError(f"round {n} is already published for {name}")
    config = seat_config(plan, seat)
    stop = quota.classify(config["harness"], record)
    if not stop:
        raise RuntimeError(
            f"{name} does not carry a provider quota stop in round {n}; "
            "diagnose the failure rather than taking the turn again"
        )
    resume = quota.resumable_session(stop)
    if resume:
        # Before the stopped attempt is filed away, so a refusal leaves the
        # round exactly as it was.
        blind.require_session(root / "seats" / name, config["harness"], resume)
    prompt = quota.continuation() if resume else (record / "prompt.md").read_text()
    number = len(list((record / "attempts").glob("[0-9][0-9]"))) + 1
    folder = record / "attempts" / f"{number:02d}"
    folder.mkdir(parents=True)
    for item in (
        "events.jsonl",
        "stderr.log",
        "prompt.md",
        "launch.json",
        "receipt.json",
    ):
        source = record / item
        if source.exists():
            shutil.move(str(source), str(folder / item))
    del state["rounds"][str(n)]
    state.setdefault("attempts", []).append(
        {
            "round": n,
            "attempt": number,
            "quota_stop": stop["phase"],
            "quota_scope": stop.get("scope"),
            "resumed": resume,
        }
    )
    write(state_path(root, name), state)
    event(
        root,
        round=n,
        seat=name,
        event="continuing",
        quota_stop=stop["phase"],
        resumed=resume,
        attempt=number,
    )
    return launch_turn(root, plan, seat, n, prompt, resume, timeout=timeout)


def run_round(root, n, *, jobs=1, timeout=None):
    """Open the round, run every seat, then close it by promoting the outboxes.

    Seats run one at a time unless ``jobs`` says otherwise, and the account
    lease caps them again per harness. Sequential is the default because
    seats sharing one account drain a rate window several times faster
    together and the wall then lands mid-turn on all of them at once; it also
    preserves whatever spend order the plan recorded. The round closes once
    every seat has stopped, so the board changes at exactly one instant and no
    seat reads a partial one.
    """
    root, plan = load(root)
    with lock(root / "room.lock"):
        allowed = budget(root, plan)
        if n < 1 or n > allowed:
            raise ValueError(f"round must be 1..{allowed}; extend the room first")
        # Every seat resumes its session each round. One whose private home
        # has gone would fail after the round opened and the board went out
        # to the others, so the round is refused before either happens.
        for seat in plan["seats"]:
            state = read(state_path(root, seat["id"]))
            if seat["id"] in acting(plan, n) and str(n) not in state["rounds"]:
                if state.get("session_id"):
                    blind.require_session(
                        root / "seats" / seat["id"],
                        seat_config(plan, seat)["harness"],
                        state["session_id"],
                    )
        event(root, round=n, event="round-opened", jobs=jobs)
        open_round(root, n)
        results = {}

        speaking = acting(plan, n)
        for seat in plan["seats"]:
            if seat["id"] not in speaking:
                event(root, round=n, seat=seat["id"], event="resting")

        def task(seat):
            try:
                return seat["id"], run_seat(root, plan, seat, n, timeout=timeout)
            except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as e:
                event(root, round=n, seat=seat["id"], event="stopped", error=str(e))
                return seat["id"], f"stopped: {e}"

        # `submit` carries the caller's project context into each thread.
        # A bare pool.map would let a worker re-resolve the project from disk,
        # which is exactly the cross-thread leak the project layer prevents.
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            speakers = [s for s in plan["seats"] if s["id"] in speaking]
            for future in [submit(pool, task, s) for s in speakers]:
                name, outcome = future.result()
                results[name] = outcome
        # A quota stop is not an absence, and the digest is the durable record
        # of what the room was shown. Publishing one that files a seat as
        # silent would put a false fact beyond reach, because a published
        # board is never rewritten. So the round stays open, the operator
        # waits the provider out, continues that seat, and promotes then.
        stopped = sorted(k for k, v in results.items() if v == "quota")
        if stopped:
            event(root, round=n, event="round-held", waiting_on=stopped)
            results["_board"] = {"held": stopped}
            return results
        results["_board"] = promote(root, n)
    return results


def run_until(root, *, limit=None, jobs=1, timeout=None):
    """Run rounds until the room converges or the budget runs out.

    The room is asked to stop, never silenced: a single `round` still runs on
    request, and this only decides when to stop asking for another. Leaving
    the decision to the room itself is the one thing that does not work, since
    a room with nothing left to say goes on writing paragraphs.

    A room that declared phases is not asked. Its schedule already says how
    long it runs and in what order, and the stop rule reads a board, not a
    plan: the signal cannot tell "these writers have exhausted the question"
    from "this phase is two rounds of notes on a draft, and the revision that
    answers them comes next." Stopping such a room mid-schedule ships the
    draft nobody rewrote. The signal is still measured and still recorded on
    every round, because an operator watching a phased room still wants to
    know when a discussion has gone quiet.
    """
    root = room_root(root)
    played = []
    while True:
        _, plan = load(root)
        ceiling = (
            budget(root, plan) if limit is None else min(limit, budget(root, plan))
        )
        number = len(list((root / "board" / "rounds").glob("r[0-9][0-9][0-9]"))) + 1
        if number > ceiling:
            return played, "budget"
        outcome = run_round(root, number, jobs=jobs, timeout=timeout)
        played.append((number, outcome))
        # A held round has no board and no signal: a seat hit a provider
        # wall and the round stays open until the operator continues it.
        # Asking for another round here would run it on top of an unclosed
        # one, so the loop hands back instead of reading a digest that was
        # deliberately never written.
        held = outcome["_board"].get("held")
        if held:
            return played, f"held on {', '.join(held)}"
        signal = outcome["_board"]["convergence"]
        if signal["converged"] and not plan.get("phases"):
            return played, f"converged on {signal['reason']}"


def status(root):
    """A read-only view of a room, safe to call while it runs.

    It takes no lock, opens no session and writes nothing, so watching cannot
    perturb what it watches. A partly written final log line is ignored rather
    than treated as corruption, because the writer may be mid-append.
    """
    root, plan = load(root, verify=False)
    rows = []
    log = root / "room.log"
    if log.exists():
        for line in log.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    allowed = budget(root, plan)
    seats = {}
    for seat in plan["seats"]:
        state = read(state_path(root, seat["id"]))
        posts = sorted(
            p.name for p in (root / "board" / "posts" / seat["id"]).glob("r*.md")
        )
        outbox = root / "seats" / seat["id"] / "outbox" / "post.md"
        receipts = {
            int(path.parent.name[1:]): read(path)
            for path in (root / "records" / seat["id"]).glob(
                "r[0-9][0-9][0-9]/receipt.json"
            )
        }
        seats[seat["id"]] = {
            "label": label(seat),
            "status": state.get("status"),
            "session_id": state.get("session_id"),
            "rounds": state.get("rounds", {}),
            # The rounds this seat speaks in. It listens through the others, so
            # they are not turns it owes, and counting them would report a
            # phase seat as permanently behind. Read over the budget rather
            # than the declared count, so an extended room counts its own.
            "acting_rounds": [
                n for n in range(1, allowed + 1) if seat["id"] in acting(plan, n)
            ],
            "posts": posts,
            "drafting": outbox.stat().st_size if outbox.exists() else 0,
            "served": f"{seat['config']['harness']}/{drivers.model_text(seat['config'])}",
            "red_flags": {
                n: got["red_flags"]
                for n, got in sorted(receipts.items())
                if got.get("red_flags")
            },
            "quota_resets_at": {
                n: got["quota_resets_at"]
                for n, got in receipts.items()
                if got.get("quota_resets_at")
            },
            # A seat that opened its session after round one heard the room
            # before it spoke, so its first post is a reply and not an opening
            # position. Round one is the only independent evidence a room
            # contains, and a late arrival is the one way a seat can appear in
            # it and not have supplied any.
            "joined_late": next(
                (
                    int(r)
                    for r, v in sorted(
                        state.get("rounds", {}).items(), key=lambda i: int(i[0])
                    )
                    if v.get("status") == "answered"
                ),
                None,
            ),
        }
    published = sorted(p.name for p in (root / "board" / "rounds").glob("r*"))
    # The latest signal carries the whole measured series, so the approach to
    # convergence can be shown without recomputing anything from the board.
    signal = (
        read(root / "board" / "rounds" / published[-1] / "convergence.json")
        if published
        and (root / "board" / "rounds" / published[-1] / "convergence.json").exists()
        else None
    )
    # A phased room is not asked whether it converged, so the measured
    # signal must not end it early here either; `run_until` has the same rule.
    converged = bool(signal and signal["converged"] and not plan.get("phases"))
    held = [r for r in rows if r.get("event") == "round-held"]
    still_held = (
        held[-1]
        if held and f"r{held[-1].get('round', 0):03d}" not in published
        else None
    )
    # An operator writes `chair/r<NNN>.md` before opening round NNN and has
    # nothing else to confirm it will be picked up, or to notice one left over
    # from a round that has already run.
    spoken = sorted(p.stem for p in (root / "chair").glob("r[0-9][0-9][0-9].md"))
    chair = {
        "delivered": [r for r in spoken if f"{r}" in {p for p in published}],
        "queued": [r for r in spoken if r not in {p for p in published}],
    }
    value = {
        "room": str(root),
        "title": plan.get("title"),
        "rounds": allowed,
        "declared_rounds": plan["rounds"],
        "published_rounds": published,
        "convergence": signal,
        "converged": converged,
        "held": still_held,
        "chair": chair,
        "seats": seats,
        "red_flags": sum(
            len(flags) for one in seats.values() for flags in one["red_flags"].values()
        ),
        # Read from the trail rather than from every receipt: the trail is
        # already open, and a red flag an operator has to go looking for is
        # one they will find after deciding the room went fine.
        "compacted": [
            {"seat": r.get("seat"), "round": r.get("round")}
            for r in rows
            if r.get("event") == "compacted"
        ],
        "last_events": rows[-8:],
    }
    value["next"] = next_step(value)
    return value


def next_step(value):
    """The one command to type next.

    Every other room command ends by naming the one that follows it; status
    read the room and stopped, so a prepared room and a finished one ended
    the same way and neither said what to do. The branches mirror
    `run_until`'s stop rule and `continue_seat`'s refusals, so a reader is
    never sent to a command the room would refuse.
    """
    room, command = shown(value["room"]), "quirework sop room"
    held = value["held"]
    if held:
        number, waiting = held.get("round"), held.get("waiting_on") or []
        first = waiting[0] if waiting else "SEAT"
        more = f" (and {len(waiting) - 1} more)" if len(waiting) > 1 else ""
        return (
            f"{command} continue {room} {number} {first}{more} once the window "
            f"resets, then {command} promote {room} {number}"
        )
    published = value["published_rounds"]
    if len(published) < value["rounds"] and not value["converged"]:
        return f"{command} rounds {room}"
    last = int(published[-1][1:]) if published else 1
    if value["converged"] and len(published) < value["rounds"]:
        # `rounds` would stop again at once, so say both halves: the room
        # thinks it is done, and running past that is the operator's call.
        return (
            f"{command} board {room} {last}; the room converged, and "
            f"{command} rounds {room} runs past it"
        )
    return f"{command} board {room} {last}"


def _spans(numbers):
    """[1, 2, 3, 5] -> "1-3, 5", because a Python list repr is not a notation."""
    spans = []
    for n in sorted(numbers):
        if spans and n == spans[-1][1] + 1:
            spans[-1][1] = n
        else:
            spans.append([n, n])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in spans)


def _seat_summary(seat):
    """A seat's record across the room, not just how its last turn ended.

    The seat's state keeps one `status`, overwritten every turn, so a seat
    that answered twenty rounds and failed the twenty-first used to read
    `failed`, which looks like a verdict on the seat.
    """
    rounds = seat["rounds"]
    expected = len(seat["acting_rounds"]) or len(rounds)
    plural = "" if expected == 1 else "s"
    if not rounds:
        return f"not started, {expected} round{plural} to speak in"
    done = sum(1 for v in rounds.values() if v.get("status") == "answered")
    trouble = [
        f"r{int(n):03d} {v.get('status')}"
        for n, v in sorted(rounds.items(), key=lambda i: int(i[0]))
        if v.get("status") != "answered"
    ]
    return f"answered {done} of {expected} round{plural}" + "".join(
        f", {t}" for t in trouble
    )


def usage(root):
    """What each seat spent, per seat and per harness, from its own receipts.

    A mixed room is the case that needs this. Every adapter records what its
    harness reports and nothing reconciles the three, so an operator asking
    what a room cost reads whichever seats they happened to parse. Claude is
    the only harness quoting a price, and a room that omits the other seats
    rather than saying their cost is unknown reports a cheaper room than it
    ran.
    """
    root, plan = load(root, verify=False)
    rows = []
    for seat in plan["seats"]:
        config = seat_config(plan, seat)
        totals = {k: 0 for k in ("input", "output", "reasoning", "cache_read")}
        cost, priced, rounds, inside = 0.0, 0, 0, False
        for receipt in sorted((root / "records" / seat["id"]).glob("r*/receipt.json")):
            counted = drivers.usage_totals(
                config["harness"], read(receipt).get("usage")
            )
            if not counted:
                continue
            rounds += 1
            for name in totals:
                totals[name] += counted[name]
            inside = inside or counted["cache_inside_input"]
            if counted["cost_usd"] is not None:
                cost, priced = cost + counted["cost_usd"], priced + 1
        rows.append(
            {
                "seat": seat["id"],
                "label": label(seat),
                "harness": config["harness"],
                "model": config["model"],
                "rounds": rounds,
                **totals,
                "cache_inside_input": inside,
                "cost_usd": cost if priced else None,
            }
        )
    return {"room": str(root), "title": plan.get("title"), "seats": rows}


def _render_usage(value):
    lines = [value["title"], f"  {shown(value['room'])}", ""]
    head = f"  {'seat':<16}{'harness':<9}{'rounds':>7}{'output':>10}{'cache read':>12}"
    lines += [head + f"{'cost':>10}"]
    unknown = []
    for row in value["seats"]:
        price = f"${row['cost_usd']:.2f}" if row["cost_usd"] is not None else "unknown"
        if row["cost_usd"] is None:
            unknown.append(row["harness"])
        lines.append(
            f"  {row['label']:<16}{row['harness']:<9}{row['rounds']:>7}"
            f"{row['output']:>10,}{row['cache_read']:>12,}{price:>10}"
        )
    known = sum(r["cost_usd"] or 0 for r in value["seats"])
    lines += ["", f"  reported cost: ${known:.2f}"]
    if unknown:
        # Naming them is the point: a total missing two of four seats looks
        # exactly like a total of a cheaper room.
        lines.append(
            f"  no price reported by {', '.join(sorted(set(unknown)))}; "
            "the room cost more than this"
        )
    if any(r["cache_inside_input"] for r in value["seats"]):
        lines.append(
            "  codex counts cached tokens inside input; claude counts them beside it"
        )
    return "\n".join(lines)


NOVELTY_SHOWN = 6


def _render(value):
    published, allowed = value["published_rounds"], value["rounds"]
    rounds = f"  rounds: {len(published)} of {allowed} published"
    if published:
        rounds += f" ({_spans(int(r[1:]) for r in published)})"
    if allowed != value["declared_rounds"]:
        rounds += f"; budget raised from the {value['declared_rounds']} declared"
    lines = [value["title"], f"  {shown(value['room'])}", rounds]
    series = (value.get("convergence") or {}).get("series") or []
    if len(series) > 1:
        # Novelty is the room's own stop rule. Printing it only once it has
        # fired hides the approach, which is the part worth watching. A lone
        # round is left out: with nothing to differ from it is always 100%.
        shown_series = series[-NOVELTY_SHOWN:]
        measured = ", ".join(
            f"r{s['round']:03d} "
            + ("-" if s["novelty"] is None else f"{s['novelty']:.0f}%")
            for s in shown_series
        )
        signal = value["convergence"]
        fired = (
            f" (converged at round {signal['round']} on {signal['reason']})"
            if value["converged"]
            else ""
        )
        more = "..., " if len(series) > len(shown_series) else ""
        lines.append(f"  novelty: {more}{measured}{fired}")
    if value.get("red_flags"):
        lines.append(f"  red flags: {value['red_flags']} (marked ! below)")
    if value.get("held"):
        number = value["held"].get("round")
        waiting = value["held"].get("waiting_on") or []
        resets = sorted(
            {
                when
                for name in waiting
                for n, when in value["seats"]
                .get(name, {})
                .get("quota_resets_at", {})
                .items()
                if n == number
            }
        )
        lines.append(
            f"  HELD: round {number} waiting on {', '.join(waiting)} "
            f"(provider quota{f'; resets {resets[0]}' if resets else ''})"
        )
    lines.append("")
    width = max(len(v["label"]) for v in value["seats"].values())
    served = max(len(v["served"]) for v in value["seats"].values())
    for number, seat in enumerate(value["seats"].values(), 1):
        drafting = f", drafting {seat['drafting']}B" if seat["drafting"] else ""
        lines.append(
            f"  {number}. {seat['label']:<{width}}  {seat['served']:<{served}}  "
            f"{_seat_summary(seat)}; posts: {len(seat['posts'])}{drafting}"
        )
        if (joined := seat.get("joined_late")) and joined > 1:
            lines.append(f"     joined in round {joined}; it has no opening position")
        for n, flags in seat.get("red_flags", {}).items():
            for flag in flags:
                lines.append(f"     ! r{n:03d}: {flag}")
    chair = value.get("chair") or {}
    if chair.get("queued"):
        lines += ["", f"  chair note queued for {', '.join(chair['queued'])}"]
    if chair.get("delivered"):
        lines.append(f"  chair spoke in {', '.join(chair['delivered'])}")
    if value.get("compacted"):
        where = ", ".join(
            f"{r['seat']} in round {r['round']}" for r in value["compacted"]
        )
        lines += [
            "",
            f"  COMPACTED: {where}",
            (
                "  a compacted round lost context it was resumed to keep;"
                " review before trusting it"
            ),
        ]
    if value["last_events"]:
        lines += ["", "  recent:"]
        for row in value["last_events"]:
            when = time.strftime("%H:%M:%S", time.localtime(row["t"]))
            parts = [
                f"{k}={v}"
                for k, v in row.items()
                if k not in ("t", "event", "seat") and v not in (None, "")
            ]
            who = row.get("seat", "-")
            lines.append(
                f"    {when}  {who:<18} {row.get('event', '?'):<16} {' '.join(parts)}"
            )
    lines += ["", f"next: {value['next']}"]
    return "\n".join(lines)


def watch(root, *, poll=1.0):
    """Follow the room's trail. Read-only: no lock, no session, no writes."""
    root = room_root(root)
    log, offset = root / "room.log", 0
    print(f"following {shown(log)} -- interrupt to stop", file=sys.stderr)
    while True:
        if log.exists():
            with log.open("rb") as stream:
                stream.seek(offset)
                data = stream.read()
                # Stop at the last complete line; the writer may be mid-append.
                cut = data.rfind(b"\n") + 1
                offset += cut
                for line in data[:cut].decode().splitlines():
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    when = time.strftime("%H:%M:%S", time.localtime(row["t"]))
                    rest = {k: v for k, v in row.items() if k not in ("t", "event")}
                    print(
                        f"{when}  {row.get('event', '?'):<16} "
                        f"{json.dumps(rest, ensure_ascii=False)}",
                        flush=True,
                    )
        time.sleep(poll)


def seat_named(plan, value):
    """A seat by its id, or by its position in the room.

    Position exists because following a room means four terminals and four
    commands, and `follow room 2` is easier to type and to keep straight than
    a persona's full slug. An id that happens to be a number wins over the
    position, because a seat's own name is never a guess.
    """
    names = [s["id"] for s in plan["seats"]]
    text = str(value)
    if text in names:
        return text
    if text.isdigit() and 1 <= int(text) <= len(names):
        return names[int(text) - 1]
    listed = ", ".join(f"{i}. {n}" for i, n in enumerate(names, 1))
    raise ValueError(f"no seat {value!r} here; this room seats {listed}")


def transcripts(root, plan, seat):
    """Every native transcript this seat has, oldest first.

    Not one file: codex opens a fresh rollout each time a session is resumed,
    so a seat ten rounds in has ten of them and the conversation is their
    concatenation. Claude keeps one per session but also writes subagent
    files beside it, which are not this seat talking.
    """
    record = next(s for s in plan["seats"] if s["id"] == seat)
    harness = seat_config(plan, record)["harness"]
    pattern = observe.NATIVE_TRANSCRIPTS.get(harness)
    if pattern is None:
        raise ValueError(f"no known transcript location for {harness}")
    private = blind.state_root(root / "seats" / seat) / harness
    found = [p for p in private.glob(pattern) if "subagents" not in p.parts]
    return sorted(found, key=lambda p: (p.stat().st_mtime, p.name))


def _agy_lines(row, *, thinking, width):
    """One agy transcript row as the lines a reader wants, or none at all.

    Tool *results* are dropped. A single file read returns two kilobytes of
    quoted source, and four of those bury the one thing the reader came for,
    which is what the seat said and what it decided to do next.
    """
    kind, status = row.get("type"), row.get("status")
    content = (row.get("content") or "").strip()
    if kind == "USER_INPUT":
        # The harness appends its own blocks -- a clock reading, a note that
        # the model was selected -- to every prompt. They are its bookkeeping,
        # not the turn the room sent, and printing them buries the turn.
        text = re.sub(
            r"<(ADDITIONAL_METADATA|USER_SETTINGS_CHANGE)>.*?</\1>",
            "",
            content,
            flags=re.DOTALL,
        )
        text = re.sub(r"</?USER_REQUEST>", "", text).strip()
        wrapped = textwrap.wrap(" ".join(text.split()), width - 4)
        shown = wrapped[:8] + (["..."] if len(wrapped) > 8 else [])
        return ["", "=" * width, *(f"  {line}" for line in shown), "=" * width, ""]
    if status == "ERROR":
        # The harness prefixes every result with its own timestamps. Printing
        # those instead of the message is how an error line ends up saying
        # nothing at all about what went wrong.
        detail = " ".join(
            line
            for line in content.splitlines()
            if not line.startswith(("Created At:", "Completed At:"))
        )
        # Errors wrap rather than truncate: a cut-off error names the tool
        # that failed and never the reason, which is the half nobody needs.
        lines = textwrap.wrap(" ".join(detail.split()), width - 5)
        return [f"  !  {line}" for line in lines]
    if kind != "PLANNER_RESPONSE":
        return []
    out = []
    if thinking and row.get("thinking"):
        for line in textwrap.wrap(" ".join(row["thinking"].split()), width - 5):
            out.append(f"  ·  {line}")
    for call in row.get("tool_calls") or []:
        args = call.get("args") or {}
        what = args.get("toolSummary") or args.get("toolAction") or call.get("name")
        out.append(f"  \u2192  {what}")
    if content:
        out.append("")
        for paragraph in content.splitlines():
            out.extend(textwrap.wrap(paragraph, width) or [""])
        out.append("")
    return out


def _banner(text, width):
    """A turn the room sent, framed so round boundaries are visible."""
    wrapped = textwrap.wrap(" ".join(text.split()), width - 4)
    shown = wrapped[:8] + (["..."] if len(wrapped) > 8 else [])
    return ["", "=" * width, *(f"  {line}" for line in shown), "=" * width, ""]


def _said(text, width):
    out = [""]
    for paragraph in text.strip().splitlines():
        out.extend(textwrap.wrap(paragraph, width) or [""])
    return out + [""]


def _thought(text, width):
    return [f"  ·  {line}" for line in textwrap.wrap(" ".join(text.split()), width - 5)]


def _claude_lines(row, *, thinking, width):
    """One Claude Code transcript row. Tool results are dropped, as for agy."""
    kind = row.get("type")
    if kind not in ("user", "assistant"):
        return []
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return _banner(content, width) if kind == "user" else _said(content, width)
    out = []
    for block in content or []:
        shape = block.get("type")
        if shape == "text":
            out.extend(
                _banner(block["text"], width)
                if kind == "user"
                else _said(block["text"], width)
            )
        elif shape == "thinking" and thinking and block.get("thinking", "").strip():
            out.extend(_thought(block["thinking"], width))
        elif shape == "tool_use":
            target = (block.get("input") or {}).get("file_path", "")
            name = block.get("name", "tool")
            out.append(
                f"  \u2192  {name} {Path(target).name if target else ''}".rstrip()
            )
    return out


def _codex_cmd(text):
    """The shell command inside a codex exec call, for a one-line summary."""
    found = re.search(r'cmd:\s*"((?:[^"\\]|\\.)*)"', text or "")
    return found.group(1) if found else (text or "")[:60]


def _codex_lines(row, *, thinking, width):
    """One codex rollout row, minus the harness's own developer preamble."""
    if row.get("type") != "response_item":
        return []
    payload = row.get("payload") or {}
    shape = payload.get("type")
    if shape == "message":
        role = payload.get("role")
        # The CLI injects its own preamble -- skills, plugins, recommendations
        # -- partly as `developer` and partly as `user`, so the role alone does
        # not separate it from the turn the room sent. Every such block opens
        # with a tag and a room prompt never does.
        if role == "developer":
            return []
        text = " ".join(
            part.get("text", "") for part in payload.get("content") or []
        ).strip()
        if not text or text.startswith("<"):
            return []
        return _banner(text, width) if role == "user" else _said(text, width)
    if shape == "reasoning" and thinking:
        summary = " ".join(
            part.get("text", "") for part in payload.get("summary") or []
        ).strip()
        return _thought(summary, width) if summary else []
    if shape == "custom_tool_call":
        return [f"  \u2192  {_codex_cmd(payload.get('input'))[: width - 5]}"]
    return []


_RENDERERS = {"agy": _agy_lines, "claude": _claude_lines, "codex": _codex_lines}

# Whether a transcript row is finished and safe to print. Only one harness
# revises a row in place: agy writes a step, then marks it DONE. Claude and
# codex append rows already complete, so waiting for a status they never carry
# would print nothing at all -- which is exactly what it did.
_SETTLED = {
    "agy": lambda row: row.get("status") in ("DONE", "ERROR"),
    "claude": lambda row: True,
    "codex": lambda row: True,
}


def follow(root, seat, *, thinking=True, poll=1.0, width=88):
    """Print one seat's side of the room as it happens.

    Read-only, like `status` and `watch`: no lock, no session, no writes. Rows
    are consumed by position and only once they carry a terminal status, so a
    step still being written is waited for rather than printed half-formed,
    and a harness that rewrites its transcript instead of appending to it
    cannot produce duplicates.
    """
    root = room_root(root)
    _, plan = load(root, verify=False)
    seat = seat_named(plan, seat)
    record = next(s for s in plan["seats"] if s["id"] == seat)
    harness = seat_config(plan, record)["harness"]
    render = _RENDERERS.get(harness)
    if render is None:
        raise ValueError(f"following a {harness} seat is not implemented yet")
    settled = _SETTLED[harness]
    # Which seat and which model, because a live view is where an operator is
    # most likely to be asking who is speaking.
    config = seat_config(plan, record)
    who = f"{label(record)} [{config['harness']}/{config.get('model')}]"
    seen, waiting, opened = 0, False, False
    while True:
        paths = transcripts(root, plan, seat)
        if not paths:
            if not waiting:
                print(f"{who}: waiting for a session to open", file=sys.stderr)
                waiting = True
            time.sleep(poll)
            continue
        if not opened:
            print(f"--- {who} ({seat}, {harness}) ---", file=sys.stderr)
            opened = True
        rows = []
        for path in paths:
            for line in path.read_text(errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    break  # the writer is mid-row; pick it up next poll
        while seen < len(rows) and settled(rows[seen]):
            for out in render(rows[seen], thinking=thinking, width=width):
                print(out, flush=True)
            seen += 1
        time.sleep(poll)


STATUS_HELP = """\
Read a room without disturbing it: no lock, no session, no writes, so it
is safe while a round runs. A worked example, line by line:

  Lighthouse proposal                      the room's title
    ~/rooms/lighthouse                     where it lives
    rounds: 3 of 6 published (1-3)         published rounds against the budget
    novelty: r001 100%, r002 71%, r003 58% the stop rule, measured per round
    red flags: 1 (marked ! below)          count of the ! lines further down
    HELD: round 4 waiting on bo-larke (provider quota; resets 14:20Z)

    1. Ana Pell  claude/opus                       answered 3 of 3 rounds; posts: 3
    2. Bo Larke  codex/gpt-5.6-terra (from terra)  answered 3 of 4 rounds, r004 quota; posts: 3
       ! r002: Session bus proxied into the jail for ...

  next: quirework sop room continue ~/rooms/lighthouse 4 bo-larke once ...

Novelty is the share of words a seat has not used before; two quiet rounds
stop an unphased room, and a lone round is not shown because it is always
100%. A seat counts only the rounds its phase lets it speak in, so a seat
resting through a drafting phase is not behind. A red flag is a fact about
how a turn ran (a door the jail had to open, a compaction), not a judgment
of what the seat wrote. The last line names the command to run next.
"""


def main(argv=None):
    parser = argparse.ArgumentParser(prog="quirework sop room", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    made = sub.add_parser("prepare", help="stage a room from a plan")
    made.add_argument("plan")
    made.add_argument("destination")

    turn = sub.add_parser("round", help="open, run and close one round")
    turn.add_argument("root")
    turn.add_argument("number", type=int)
    turn.add_argument("--jobs", type=int, default=1)
    turn.add_argument("--timeout", type=float, default=None)

    for name, help_text in (
        ("open", "republish a round's board"),
        ("promote", "close a round from its outboxes"),
    ):
        recover = sub.add_parser(name, help=help_text)
        recover.add_argument("root")
        recover.add_argument("number", type=int)

    again = sub.add_parser(
        "continue", help="take a seat's turn again after a provider quota stop"
    )
    again.add_argument("root")
    again.add_argument("number", type=int)
    again.add_argument("seat")
    again.add_argument("--timeout", type=float, default=None)

    loop = sub.add_parser("rounds", help="run rounds until the room converges")
    loop.add_argument("root")
    loop.add_argument("--to", type=int, default=None, help="stop at this round")
    loop.add_argument("--jobs", type=int, default=1)
    loop.add_argument("--timeout", type=float, default=None)

    more = sub.add_parser("extend", help="raise the room's round budget")
    more.add_argument("root")
    more.add_argument("rounds", type=int)

    view = sub.add_parser(
        "status",
        help="read a room without disturbing it",
        description=STATUS_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    view.add_argument("root")
    view.add_argument("--json", action="store_true")

    board = sub.add_parser("board", help="print a published round's digest")
    board.add_argument("root")
    board.add_argument("number", type=int)

    spend = sub.add_parser("usage", help="what each seat spent, from its receipts")
    spend.add_argument("root")
    spend.add_argument("--json", action="store_true")

    trail = sub.add_parser("watch", help="follow the room's trail live")
    trail.add_argument("root")
    trail.add_argument("--poll", type=float, default=1.0)

    voice = sub.add_parser("follow", help="follow one seat's side of the room")
    voice.add_argument("root")
    voice.add_argument("seat")
    voice.add_argument("--poll", type=float, default=1.0)
    voice.add_argument("--width", type=int, default=88)
    voice.add_argument(
        "--no-thinking", action="store_true", help="show only actions and posts"
    )

    args = parser.parse_args(argv)
    # Every command resolves its room the same way, at the operation boundary
    # and before any work starts, so a mistyped name fails identically on all
    # of them instead of failing late or, in watch's case, not at all.
    if args.command == "prepare":
        destination = room_root(args.destination, must_exist=False)
        value = prepare(args.plan, destination)
        print(f"prepared {len(value['seats'])} seats in {shown(destination)}")
        if not spelled_as_path(args.destination):
            # The destination is printed either way, but a state-root path is
            # long, unfamiliar and easy to read past. An operator who meant a
            # directory here finds out now rather than at the next command.
            print(
                f"{args.destination!r} names a room in this project's store; "
                f"write ./{args.destination} for a room in the current "
                "directory"
            )
        return 0
    root = room_root(args.root)
    if args.command == "round":
        for name, outcome in run_round(
            root, args.number, jobs=args.jobs, timeout=args.timeout
        ).items():
            detail = json.dumps(outcome) if isinstance(outcome, dict) else outcome
            print(f"{name}: {detail}")
    elif args.command == "continue":
        # Not `status`: that name is a function in this module, and binding it
        # here makes it local to `main` for the whole body, so every other
        # command's call to it raises UnboundLocalError.
        outcome = continue_seat(root, args.number, args.seat, timeout=args.timeout)
        print(f"{args.seat}: {outcome}")
        if outcome == "answered":
            print(
                f"now close the round: quirework sop room promote {shown(root)} {args.number}"
            )
    elif args.command == "open":
        print(open_round(root, args.number) or "silent round; no board")
    elif args.command == "rounds":
        played, why = run_until(
            root, limit=args.to, jobs=args.jobs, timeout=args.timeout
        )
        for number, outcome in played:
            held = outcome["_board"].get("held")
            if held:
                print(
                    f"round {number}: held, waiting on {', '.join(held)}; "
                    f"continue each with: quirework sop room continue "
                    f"{shown(root)} {number} SEAT, then promote {number}"
                )
                continue
            signal = outcome["_board"]["convergence"]
            print(
                f"round {number}: {len(outcome['_board']['posted'])} posted, "
                f"novelty {signal['novelty']}%, closing {signal['closing']}"
            )
        print(f"stopped: {why}")
    elif args.command == "extend":
        print(f"this room may now run {extend(root, args.rounds)} rounds")
    elif args.command == "promote":
        print(json.dumps(promote(root, args.number), indent=2))
    elif args.command == "status":
        value = status(root)
        print(json.dumps(value, indent=2) if args.json else _render(value))
    elif args.command == "usage":
        value = usage(root)
        print(json.dumps(value, indent=2) if args.json else _render_usage(value))
    elif args.command == "board":
        digest_file = root / "board" / "rounds" / f"r{args.number:03d}" / "digest.md"
        if not digest_file.is_file():
            raise ValueError(f"round {args.number} has not been published in {root}")
        _, plan = load(root, verify=False)
        print(attributed(digest_file.read_text(), plan))
    elif args.command == "watch":
        try:
            watch(root, poll=args.poll)
        except KeyboardInterrupt:
            return 0
    elif args.command == "follow":
        try:
            follow(
                root,
                args.seat,
                thinking=not args.no_thinking,
                poll=args.poll,
                width=args.width,
            )
        except KeyboardInterrupt:
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
