"""A roundtable: AI models, as themselves, taking turns towards one goal.

The writers room seats personas and hides its machinery, because a writer
told it is executing a procedure starts writing like one. A roundtable does
the opposite on purpose. Each seat is a model named as itself, told that it
is one of several models collaborating autonomously, and told who the
others are. What it is not told in advance is how the room is steered: how
the order is drawn, what the supervisor watches, or what would earn an
extension.

Turns are sequential. The room runs one seat at a time, so the board, the
direct messages and the shared folder change after every turn, and the
next seat reads what the previous one wrote minutes earlier. That is what
makes the order worth scheduling: whoever speaks last in a round has heard
everyone, and whoever follows a seat is the one who answers it. The order
is drawn when the room is prepared, balanced so every seat holds every
position once per block of rounds, never takes two turns back to back
across a round boundary, and follows the same neighbour as rarely as the
arithmetic allows. The supervisor may override a round that has not opened,
and the reason is read out when it does.

The supervisor is a person. The controller never decides anything a
supervisor would: it refuses to open the round the room was promised as its
last until the supervisor has said whether it is the last, and it stops
between rounds when a seat has written to the supervisor. Everything a seat
sees is recorded, including direct messages, and the seats are told so.

A person may also take a seat, named and described as a person. The
controller never speaks for one: it stops when that seat's turn comes and
waits for the person's words, which pass through the same limits as a
model's.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import re
import shutil
import subprocess
import sys
import textwrap
import time
from collections import Counter
from pathlib import Path

from quirework.project import current_project
from quirework.providers import blind
from quirework.providers import models as drivers
from quirework.providers import quota
from quirework.runs.storage import (
    digest,
    hashes,
    identifier,
    lock,
    read,
    relative,
    shown,
    write,
)
from quirework.sops import room

SCHEMA = 1
KIND = "roundtable"
TABLES = "roundtables"
MAX_SEATS = 8
DEFAULTS = {
    "rounds": 5,
    "post_words": 200,
    "message_chars": 300,
    "messages_per_turn": 3,
    # Only a hang guard. The seats are told there is no time limit and are
    # told this one; a frontier model working alone for an hour and a half is
    # doing its turn, and stopping it partway would contradict what it read.
    "turn_timeout": 4 * 3600,
}
# How much of its window a seat may fill before the room stops resuming it.
# A small window keeps most of itself; a large one keeps less, because every
# request re-sends the whole conversation and a model reasons less reliably
# near the end of a very long one. Between the two, a larger window never
# gets a smaller allowance than a smaller one.
SMALL_WINDOW_SHARE = 0.8
LARGE_WINDOW_SHARE = 0.6
LARGE_WINDOW = 500_000
# Folders a seat reads and the room writes. The workspace around them is the
# seat's own and writable; these are bound read-only over it for the turn.
READONLY = ("board", "mail", "bin")
HARNESS_NAMES = {
    "claude": "Claude Code",
    "codex": "Codex CLI",
    "agy": "Antigravity CLI",
}
MAKERS = {"claude": "Anthropic", "gpt": "OpenAI", "gemini": "Google"}
HOUSES = {"claude": "Anthropic", "codex": "OpenAI", "agy": "Google"}
# Words in a model id that name its maker or its effort, not the model.
NOT_A_NAME = ("claude", "gpt", *drivers.AGY_EFFORTS)
SUPERVISOR = "supervisor"
# What the controller stops at rather than taking a turn: a decision only the
# supervisor makes, a provider limit, a person's turn, or a room with no seat
# left to speak.
WAITS = ("closed", "decision", "held", "human", "spent")


# -- Where a roundtable lives ------------------------------------------------


def table_root(value, *, must_exist=True):
    """Resolve a roundtable by name under the project, or by an explicit path.

    The same rule as a room's: a bare name lives in the project's state root,
    anything with a separator is a path the operator spelled out.
    """
    text = str(value)
    if room.spelled_as_path(text):
        root = Path(text).expanduser().resolve()
    else:
        root = (current_project().state_root / TABLES / identifier(text)).resolve()
    if must_exist and not (root / "plan.json").is_file():
        raise ValueError(
            f"no roundtable at {shown(root)}; name one under "
            f"{shown(current_project().state_root / TABLES)} or give a path to one"
        )
    return root


def load(root, *, verify=True):
    root = Path(root).expanduser().resolve()
    plan = read(root / "plan.json")
    if plan.get("kind") != KIND or plan.get("schema") != SCHEMA:
        raise ValueError(f"{shown(root)} is not a roundtable this version can run")
    if (
        verify
        and digest(root / "plan.json") != read(root / "plan-digest.json")["sha256"]
    ):
        raise ValueError("frozen roundtable plan changed")
    return root, plan


def state(root, seat):
    return read(room.state_path(root, seat))


def record_dir(root, seat, n):
    return root / "records" / seat / f"r{n:03d}"


def names(plan):
    return {s["id"]: s["name"] for s in plan["seats"]}


def seat_by(plan, value):
    """A seat by id, by name in any case, or by its position in the plan."""
    text = str(value).strip().lstrip("@").lower()
    for number, seat in enumerate(plan["seats"], 1):
        if text in (seat["id"], seat["name"].lower(), str(number)):
            return seat
    raise ValueError(
        f"no seat {value!r}; the seats are "
        + ", ".join(f"{i}. {s['name']}" for i, s in enumerate(plan["seats"], 1))
    )


# -- Naming a seat after its model --------------------------------------------


def seat_name(model):
    """What a seat is called: its model's family, without maker or version.

    ``luna`` is Luna, ``gemini-flash`` is Gemini-Flash, and an exact id such
    as ``claude-haiku-5-5`` is still Haiku. The name is how the other models
    address the seat, so it is the one people use rather than the catalog's.
    """
    words = [
        w
        for w in re.split(r"[-_\s]+", str(model).strip().lower())
        if w and not w[0].isdigit() and w not in NOT_A_NAME
    ]
    if not words:
        raise ValueError(f"cannot name a seat after {model!r}; give it a name")
    return "-".join(w.capitalize() for w in words)


def is_human(seat):
    return bool(seat.get("human"))


def human_seat(item):
    """A seat a person takes: named by the plan and described in its words.

    The models are told who is at the table, so a person is described as
    what they are, in the plan's own words, rather than passed off as one
    more model: a table whose roster lies about one seat is no evidence of
    how models work when told the truth. A human seat has no model, window
    or budget, and its turns are delivered by the operator with ``speak``.
    """
    name = str(item.get("name") or "").strip()
    if not name:
        raise ValueError("a human seat needs the name the table will call it by")
    description = str(item.get("description") or "a human").strip()
    return {
        "id": identifier(name.lower()),
        "name": name,
        "human": True,
        "description": description,
    }


def maker(config):
    """Who made the model, from its id, or from the harness serving it."""
    first = re.split(r"[-_.]", str(config["model"]).lower())[0]
    return MAKERS.get(first) or HOUSES[config["harness"]]


def allowance_for(window):
    """The tokens a seat may fill: 80% of a small window, 60% of a large one."""
    if window <= LARGE_WINDOW:
        return int(window * SMALL_WINDOW_SHARE)
    return int(max(LARGE_WINDOW * SMALL_WINDOW_SHARE, window * LARGE_WINDOW_SHARE))


def duration(seconds):
    hours, rest = divmod(int(seconds), 3600)
    if not rest:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{int(seconds) // 60} minutes"


# -- The order of turns -------------------------------------------------------


def next_order(ids, history, rng, *, boundary=True):
    """The order for the round after ``history``, as balanced as it can be.

    Three properties, in priority order. No seat takes two turns in a row
    across the round boundary, which would let it answer itself. Within each
    block of as many rounds as there are seats, every seat holds every
    position once, so nobody always leads and nobody always closes. Among
    the orders that keep both, a seat follows the same neighbour as rarely
    as possible, because in a sequential room the seat before you is the one
    you answer.

    The third gives way to the second. With three seats the first two rules
    leave exactly one order per round, a rotation, so the neighbours only
    change when the next block begins. With five seats in five rounds no
    order balances every neighbour pair and also avoids back-to-back turns.

    ``boundary=False`` drops the first rule, for seats that never meet
    across a boundary because a seat with a fixed place stands between.
    """
    n = len(ids)
    block = history[len(history) - len(history) % n :]
    pairs = Counter((a, b) for order in history for a, b in itertools.pairwise(order))
    last = history[-1] if history and boundary else None
    best, score = None, None
    for order in itertools.permutations(ids):
        if last and order[0] == last[-1]:
            continue
        repeats = sum(1 for past in block for p in range(n) if past[p] == order[p])
        crowding = sum(pairs[pair] for pair in itertools.pairwise(order))
        key = (repeats, crowding, rng.random())
        if score is None or key < score:
            best, score = list(order), key
    return best


def extend_schedule(schedule, ids, seed, through, fixed=None):
    """Draw orders for every round up to ``through`` that has none yet.

    Each round draws from a generator seeded by the room and the round
    number, so the schedule an extension adds is the one the same room would
    have drawn at preparation, and a rerun reproduces it.

    ``fixed`` maps "first" or "last" to a seat that always holds that place,
    such as a person who closes every round to take in what the models
    said. The other seats are balanced among themselves, and since the
    fixed seat stands between one round's end and the next one's start,
    none of them can answer itself across the boundary.
    """
    fixed = fixed or {}
    free = [i for i in ids if i not in fixed.values()]
    head = [fixed["first"]] if "first" in fixed else []
    tail = [fixed["last"]] if "last" in fixed else []
    rounds = schedule.setdefault("rounds", {})
    for n in range(1, through + 1):
        if str(n) in rounds:
            continue
        history = [[s for s in rounds[str(k)] if s in free] for k in range(1, n)]
        order = next_order(
            free, history, random.Random(f"{seed}:{n}"), boundary=not fixed
        )
        rounds[str(n)] = head + order + tail
    return schedule


def fixed_places(seats):
    """Which seat, if any, always speaks first or last."""
    places = {}
    for seat in seats:
        where = seat.get("position")
        if where is None:
            continue
        if where not in ("first", "last"):
            raise ValueError(
                f"seat {seat['name']!r}: a position is first or last, not {where!r}"
            )
        if where in places:
            raise ValueError(f"only one seat can always speak {where}")
        places[where] = seat["id"]
    return places


# -- The packet -----------------------------------------------------------------

HEADING = re.compile(r"(#{1,6})[ \t]+(.*?)[ \t]*$")
ANCHOR = re.compile(r"\{[^}]*#([\w:.-]+)[^}]*\}$")
FENCE = re.compile(r"(`{3,}|~{3,})")


def excerpt(text, anchors, source="the source"):
    """The sections of a Markdown text named by their heading anchors, verbatim.

    A section is its heading line and everything up to the next heading of
    the same or a higher level, so its subsections come with it. Sections
    are returned in the order asked for. Nothing is summarized or
    reflowed: a packet that says it quotes the source quotes it. A heading
    inside a code fence is text, not a boundary.
    """
    lines = text.splitlines(keepends=True)
    heads, fence = [], None
    for number, line in enumerate(lines):
        stripped = line.strip()
        opener = FENCE.match(stripped)
        if fence:
            # Only a bare run of the same character, at least as long, closes.
            closes = opener and stripped == opener.group(1)
            if closes and stripped[0] == fence[0] and len(stripped) >= len(fence):
                fence = None
            continue
        if opener:
            fence = opener.group(1)
            continue
        heading = HEADING.match(line.rstrip("\n"))
        if heading:
            anchor = ANCHOR.search(heading.group(2))
            heads.append((number, len(heading.group(1)), anchor and anchor.group(1)))
    found = {}
    for i, (start, level, anchor) in enumerate(heads):
        if anchor and anchor not in found:
            stop = next((s for s, lv, _ in heads[i + 1 :] if lv <= level), len(lines))
            found[anchor] = "".join(lines[start:stop]).rstrip() + "\n"
    missing = [a for a in anchors if a not in found]
    if missing:
        raise ValueError(f"{shown(source)} has no section {', '.join(missing)}")
    return "\n".join(found[a] for a in anchors)


def git_state(path):
    """The commit a packet file was read against, and whether it matched it.

    ``None`` outside a repository. A file that is untracked or differs from
    the commit is said so, since "verbatim from the book at this commit" is
    then not what the packet holds.
    """
    path = Path(path)

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(path.parent), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    head = git("rev-parse", "HEAD")
    if head.returncode:
        return None
    tracked = not git("ls-files", "--error-unmatch", "--", path.name).returncode
    same = tracked and not git("diff", "--quiet", "HEAD", "--", path.name).returncode
    return {"commit": head.stdout.strip(), "tracked": tracked, "matches_commit": same}


# -- Preparing a roundtable ---------------------------------------------------


def prepare(plan_path, destination, *, resolve_models=True):
    """Stage one workspace per seat and freeze what the seats were promised."""
    plan_path = Path(plan_path).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    supplied = read(plan_path)
    if destination.exists():
        raise ValueError("roundtable exists; continue or inspect it, do not replace it")
    goal = supplied.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("state the goal the models are to work towards")
    limits = {k: supplied.get(k, v) for k, v in DEFAULTS.items()}
    for key, value in limits.items():
        if not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} must be a positive integer")
    if limits["rounds"] < 2:
        raise ValueError(
            "rounds must be at least 2: the supervisor says after the "
            "second-to-last round whether the last one is final"
        )
    declared = supplied.get("seats") or []
    if not 2 <= len(declared) <= MAX_SEATS:
        raise ValueError(f"a roundtable seats between 2 and {MAX_SEATS} models")
    source_root = Path(supplied.get("source_root", current_project().root)).resolve()

    seats = []
    for item in declared:
        if item.get("human"):
            seats.append(human_seat(item))
            continue
        requested = item.get("model")
        if not requested:
            raise ValueError("every seat names its model")
        harness = item.get("harness") or drivers.harness_for(requested)
        effort = item.get("effort", supplied.get("effort", "medium"))
        config = (
            drivers.resolve(harness, requested, effort)
            if resolve_models
            else {
                "harness": harness,
                "model": requested,
                "effort": effort,
                "requested_model": requested,
            }
        )
        # Every seat gets the same tools, so nothing on the board has to say
        # which of them could check a source.
        config = {**config, "tools": "workbench"}
        name = item.get("name") or seat_name(requested)
        # The budget is stated before the first turn, and only Codex publishes
        # a window before a model has run, so the rest come from documented
        # figures. A model nothing here knows is refused: a guessed window
        # would be a budget told to the seat as fact.
        window, source = (
            (item["window"], "plan")
            if item.get("window")
            else drivers.stated_window(config)
        )
        if not isinstance(window, int) or window < 1:
            raise ValueError(
                f"seat {name!r}: no context window is known for "
                f"{config['model']!r}; give the seat a window in tokens"
            )
        seats.append(
            {
                "id": identifier(name.lower()),
                "name": name,
                "maker": maker(config),
                "config": config,
                "window": window,
                "window_source": source,
                "allowance": allowance_for(window),
                # Every harness now reports its requests. A table prepared
                # while agy was estimated keeps "estimated" in its plan.
                "occupancy": "measured",
            }
        )
    if all(is_human(s) for s in seats):
        raise ValueError("a roundtable seats at least one model")
    for item, seat in zip(declared, seats, strict=True):
        if item.get("position") is not None:
            seat["position"] = item["position"]
    fixed = fixed_places(seats)
    for key, what in (("id", "name"), ("model", "model")):
        values = [
            s["id"] if key == "id" else s["config"]["model"]
            for s in seats
            if key == "id" or not is_human(s)
        ]
        repeated = sorted({v for v in values if values.count(v) > 1})
        if repeated:
            raise ValueError(
                f"a roundtable seats each model once; the {what} "
                f"{', '.join(repeated)} appears more than once"
            )

    sources = []

    def staged(entry):
        path = (
            plan_path.parent / entry["local_source"]
            if "local_source" in entry
            else source_root / entry["source"]
        )
        content = path.read_bytes()
        if entry.get("sections"):
            content = excerpt(content.decode(), entry["sections"], path).encode()
        sources.append(
            {
                "path": entry["path"],
                "source": str(path),
                "sections": entry.get("sections"),
                "source_sha256": digest(path),
                "git": git_state(path),
                "inline": bool(entry.get("inline")),
            }
        )
        return content

    packet = {}
    if supplied.get("brief"):
        packet[supplied["brief"]["path"]] = staged(supplied["brief"])
    for entry in supplied.get("shared_files", []):
        packet[entry["path"]] = staged(entry)

    destination.mkdir(parents=True, mode=0o700)
    (destination / ".gitignore").write_text(room.IGNORE)
    for sub in ("board/posts", "mail", "shared", "records"):
        (destination / sub).mkdir(parents=True)
    frozen = dict(
        supplied,
        kind=KIND,
        schema=SCHEMA,
        **limits,
        seats=seats,
        source_root=str(source_root),
        sources=sources,
        # What every seat is handed in full with its first turn, in order.
        inline=[s["path"] for s in sources if s["inline"]],
        seed=supplied.get("seed", random.SystemRandom().randrange(2**31)),
        prepared_at=time.time(),
    )
    for seat in seats:
        work = destination / "seats" / seat["id"]
        for sub in ("input", *READONLY, "outbox", "shared"):
            (work / sub).mkdir(parents=True)
        for rel, content in packet.items():
            target = work / "input" / relative(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        seat["reads"] = sorted(packet)
        seat["input_hashes"] = hashes(work / "input")
        write(
            room.state_path(destination, seat["id"]),
            {"status": "prepared", "session_id": None, "rounds": {}},
        )
    # Second pass: the roster and the tool both name every seat.
    for seat in seats:
        work = destination / "seats" / seat["id"]
        tool = work / "bin" / "room"
        tool.write_text(tool_text(frozen, seat))
        tool.chmod(0o755)
        seat["bin_hashes"] = hashes(work / "bin")
        start = work / "START.md"
        start.write_text(start_text(frozen, seat))
        seat["start_sha256"] = digest(start)
    write(destination / "plan.json", frozen)
    write(
        destination / "plan-digest.json", {"sha256": digest(destination / "plan.json")}
    )
    ids = [s["id"] for s in seats]
    write(
        destination / "schedule.json",
        extend_schedule({"changes": []}, ids, frozen["seed"], limits["rounds"], fixed),
    )
    write(
        destination / "budget.json",
        {"declared": limits["rounds"], "end": limits["rounds"], "decisions": []},
    )
    render_board(destination, frozen)
    room.event(destination, event="prepared", seats=ids, rounds=limits["rounds"])
    return frozen


def start_text(plan, seat):
    """The standing assignment: who the seat is, who else is here, the rules.

    It says the seat is a model among models and that the supervisor reads
    everything. It does not explain how the order is drawn, what the
    supervisor watches, or what would extend the room: telling a model the
    instrument it is measured by is how it starts playing to the instrument.

    A person at the table is named as one, and the norm of checking covers
    their posts too. A model told the user convened the room tends to defer
    to whoever sounds like the user, and deference is agreement nobody
    tested. A table of models alone is told nothing about people.
    """
    people = [s for s in plan["seats"] if is_human(s)]
    human = is_human(seat)
    handed = set(plan.get("inline") or [])
    reads = [
        f"  - input/{rel}"
        + (" (given in full with your first turn)" if rel in handed else "")
        for rel in seat["reads"]
    ]
    last = plan["rounds"]
    if human:
        opening = (
            f"You are {seat['name']}, seated here as a person, not a model. "
            "The models read the roster below, so they know you as it "
            "describes you."
        )
    else:
        opening = (
            f"You are {seat['name']}, an AI model made by {seat['maker']}. In "
            "this room you are not playing a character: you are yourself."
        )
    # A norm for every seat, never a role for one. The first pilot was
    # given permission to disagree and no expectation to check, and its
    # three seats were signing off by round three. A skeptic's chair would
    # let the others hand their doubt to it, and "expect disagreement"
    # invites objections made to comply; checking yields disagreement
    # where a check finds a reason for it.
    if people:
        who = ", ".join(s["name"] for s in people)
        company = (
            f"one person, {who}" if len(people) == 1 else f"{len(people)} people, {who}"
        )
        norm = (
            f"This is a collaboration between AI models and {company}. The "
            "models take their turns autonomously. The user who convened it "
            "wants everyone at the table to work together towards one goal, "
            "and expects both collaboration and disagreement. Check each "
            "other's work rather than taking it on trust, a person's work "
            "included: agreement nobody tested is not what the user is looking "
            "for, and neither is agreement given because a person said so. "
            "Say plainly where you disagree, and keep a disagreement that "
            "stays open visible rather than smoothing it over."
        )
    else:
        norm = (
            "This is an autonomous collaboration between AI models. The user "
            "who convened it wants several models to work together towards "
            "one goal, and expects both collaboration and disagreement. Check "
            "each other's work rather than taking it on trust: agreement "
            "nobody tested is not what the user is looking for. Say plainly "
            "where you disagree, and keep a disagreement that stays open "
            "visible rather than smoothing it over."
        )
    everyone = "everyone at the table" if people else "every model"
    others = "the others" if people else "the other models"
    fixed = fixed_places(plan["seats"])
    labels = names(plan)
    if fixed:
        steady = " ".join(
            f"{labels[who]} speaks {where} in every round."
            for where, who in sorted(fixed.items())
        )
        order_text = f"one after another. {steady} The order of the others"
    else:
        order_text = "one after another, and the order"
    blocks = [
        f"# {plan.get('title', 'Roundtable')}",
        opening,
        norm,
        "## The goal",
        plan["goal"].strip(),
        "## Who is in the room",
        "\n".join(roster_line(s, s is seat) for s in plan["seats"]),
        "No one here outranks another."
        if people
        else "No model here outranks another.",
        "The supervisor is the user who convened the room and runs it. The "
        "supervisor reads everything in the room, direct messages included, "
        "and may post to the board or write to you between turns.",
        "## How the room works",
        f"The room meets in rounds. In each round {everyone} takes one turn, "
        f"{order_text} changes from round to round. The "
        f"room runs at least {last} rounds. After round {last - 1} the "
        f"supervisor will say whether round {last} is the last or the room is "
        "extended.",
    ]
    if not human:
        blocks += [
            "Your turn has no time limit of its own. You may read, search the "
            "web, run commands and write files as much as you like. The one "
            "exception is a turn still running after "
            f"{duration(plan['turn_timeout'])}, which is treated as hung and "
            "stopped; what you queued before then is still delivered.",
            budget_text(seat),
        ]
    blocks += [
        "## Where things are",
        "\n".join(
            [
                "- input/: the material for this room. Read-only.",
                *reads,
                "- board/board.md: everything posted to the board so far. Read-only.",
                "- mail/: your direct messages, received and sent. Read-only.",
                # A pilot seat drafted its post as outbox/post1.md; the
                # controller then reported the draft as a stray file.
                "- outbox/: what bin/room has queued this turn. Keep drafts "
                "anywhere else: a file bin/room did not write there is not "
                "delivered.",
                f"- shared/: files {everyone} can read and change. Each "
                "change is recorded under the name of whoever made it."
                if people
                else "- shared/: files every model in the room can read and "
                "change. Each change is recorded under the name of the model "
                "that made it.",
                "- Anything else you write in this folder stays between your "
                f"turns. {others.capitalize()} cannot see it; the supervisor can.",
            ]
        ),
        "## How you speak",
        "Only bin/room puts words on the board or in someone's mail.",
        "\n".join(
            [
                "- bin/room post FILE: your post to the board this turn, at "
                f"most {plan['post_words']} words. Posting again replaces it. "
                "Not posting is a pass.",
                "- bin/room message NAME TEXT: a direct message to "
                f"{'anyone at the table' if people else 'one model'}, "
                f'or to "supervisor", at most {plan["message_chars"]} '
                f"characters. Up to {plan['messages_per_turn']} per turn.",
                "- bin/room outbox: what you have queued this turn.",
            ]
        ),
        "Your post and your messages are delivered when your turn ends. In "
        "each post, say what you add, what you checked, what you dispute, or "
        "what you changed. Everyone reads the board, so a message that only "
        "points at your post adds nothing; use messages for questions and "
        "for answers.",
    ]
    return "\n\n".join(_filled(block) for block in blocks) + "\n"


def roster_line(seat, you):
    """One seat as the roster names it: a model by maker, id and harness,
    a person in the plan's own words."""
    mark = " (you)" if you else ""
    if is_human(seat):
        return f"- {seat['name']}: {seat['description']}{mark}"
    config = seat["config"]
    return (
        f"- {seat['name']}: {seat['maker']} model {config['model']}, running in "
        f"{HARNESS_NAMES[config['harness']]}{mark}"
    )


def budget_text(seat):
    """The context budget, in tokens, as the standing assignment states it."""
    share = round(100 * seat["allowance"] / seat["window"])
    text = (
        "Your context is not compacted, and it has to last the whole room. "
        f"Your budget is {seat['allowance']:,} tokens, {share}% of your "
        f"{seat['window']:,}-token window. The room does not resume a model "
        "whose context has passed its budget, so the turn that crosses it is "
        "that model's last, and what you spend early is not available later. "
        "Each turn tells you how much you have used, how much your last turn "
        "added, and what remains."
    )
    if seat["occupancy"] == "estimated":
        text += (
            " Your harness does not report its usage, so those figures are estimates."
        )
    return text


def _filled(block, width=76):
    """One paragraph or list, wrapped after the names are in it.

    Wrapping the template by hand leaves a ragged line wherever a long name
    or number lands, so the text is filled once the values are known. A
    heading stays one line and each list item keeps its own indent.
    """
    if block.startswith("#"):
        return block
    lines = block.split("\n")
    if not all(line.lstrip().startswith("- ") for line in lines):
        return textwrap.fill(" ".join(lines), width)
    return "\n".join(
        textwrap.fill(
            line,
            width,
            subsequent_indent=" " * (len(line) - len(line.lstrip()) + 2),
        )
        for line in lines
    )


# The tool is standalone Python: the jail holds no Quirework, only the system
# interpreter. It checks the limits so a seat can fix an over-long post inside
# the turn that wrote it. The controller checks them again on delivery, and
# its check is the one that counts.
TOOL = '''\
#!/usr/bin/env python3
"""How you speak in the roundtable. Run: bin/room help"""

import json
import sys
from pathlib import Path

CONFIG = __CONFIG__
HOME = Path(__file__).resolve().parent.parent
OUTBOX = HOME / "outbox"
MESSAGES = OUTBOX / "messages"


def usage():
    return f"""\\
bin/room post FILE          post FILE to the board when your turn ends
bin/room post - <<'EOF'     ...or read the post from standard input
bin/room message NAME TEXT  send NAME a direct message when your turn ends
bin/room outbox             show what you have queued this turn
bin/room help               this text

NAME is one of {", ".join(CONFIG["labels"].values())}, or supervisor.
A post is at most {CONFIG["post_words"]} words, and posting again replaces it.
A message is at most {CONFIG["message_chars"]} characters, and a turn sends at
most {CONFIG["messages_per_turn"]}. To take a message back, delete its file
from outbox/messages/.
"""


def refuse(text):
    print(text, file=sys.stderr)
    return 2


def post(args):
    if not args or args == ["-"]:
        text = sys.stdin.read()
    elif len(args) == 1 and " " not in args[0]:
        # One word is always a file to read, never the post: a pilot seat
        # ran `post /dev/stdin <<EOF` and put the path on the board.
        try:
            text = Path(args[0]).read_text()
        except OSError:
            return refuse(
                f"Could not read a file named {args[0]}. Nothing was queued. "
                "To post text directly: bin/room post - <<'EOF'"
            )
    else:
        text = " ".join(args)
    text = text.strip()
    words = len(text.split())
    if not words:
        return refuse("That post is empty. Nothing was queued.")
    if words > CONFIG["post_words"]:
        return refuse(
            f"Not queued: the post has {words} words and the board takes at "
            f"most {CONFIG['post_words']}. Shorten it and post again."
        )
    OUTBOX.mkdir(exist_ok=True)
    (OUTBOX / "post.md").write_text(text + "\\n")
    print(
        f"Queued your post ({words} words). It goes on the board when your "
        "turn ends; posting again replaces it."
    )
    return 0


def message(args):
    if not args:
        return refuse("Say who it is for: bin/room message NAME TEXT")
    to = CONFIG["names"].get(args[0].strip().lstrip("@").lower())
    if to is None:
        return refuse(
            f"Nobody in the room is called {args[0]!r}. Write to one of "
            f"{', '.join(CONFIG['labels'].values())}, or supervisor."
        )
    if to == CONFIG["me"]:
        return refuse("That is you. Keep notes in your own folder instead.")
    text = (sys.stdin.read() if args[1:] in ([], ["-"]) else " ".join(args[1:]))
    text = text.strip()
    if not text:
        return refuse("That message is empty. Nothing was queued.")
    if len(text) > CONFIG["message_chars"]:
        return refuse(
            f"Not queued: the message has {len(text)} characters and the "
            f"limit is {CONFIG['message_chars']}. Shorten it and send it again."
        )
    MESSAGES.mkdir(parents=True, exist_ok=True)
    queued = sorted(MESSAGES.glob("*.json"))
    if len(queued) >= CONFIG["messages_per_turn"]:
        return refuse(
            f"Not queued: {len(queued)} messages are already queued this turn, "
            "the most a turn sends. Delete one from outbox/messages/ to send "
            "another."
        )
    number = len(queued) + 1
    while list(MESSAGES.glob(f"{number:02d}-*")):
        number += 1
    target = MESSAGES / f"{number:02d}-to-{to}.json"
    target.write_text(json.dumps({"to": to, "text": text}) + "\\n")
    left = CONFIG["messages_per_turn"] - len(queued) - 1
    print(
        f"Queued a message to {CONFIG['labels'].get(to, 'the supervisor')} "
        f"({len(text)} characters). It is delivered when your turn ends. "
        f"{left} more this turn."
    )
    return 0


def outbox(args):
    shown = False
    post = OUTBOX / "post.md"
    if post.is_file():
        text = post.read_text().strip()
        print(f"Post ({len(text.split())} words):\\n\\n{text}\\n")
        shown = True
    for path in sorted(MESSAGES.glob("*.json")) if MESSAGES.is_dir() else []:
        item = json.loads(path.read_text())
        who = CONFIG["labels"].get(item.get("to"), "the supervisor")
        print(f"Message to {who} ({path.name}):\\n\\n{item.get('text', '')}\\n")
        shown = True
    if not shown:
        print("Nothing queued this turn. Not posting is a pass.")
    return 0


def main(argv):
    commands = {"post": post, "message": message, "outbox": outbox}
    if not argv or argv[0] not in commands:
        print(usage())
        return 0 if argv[:1] in ([], ["help"], ["--help"], ["-h"]) else 2
    return commands[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


def tool_text(plan, seat):
    aliases = {SUPERVISOR: SUPERVISOR}
    for s in plan["seats"]:
        aliases[s["id"]] = s["id"]
        aliases[s["name"].lower()] = s["id"]
    config = {
        "me": seat["id"],
        "names": aliases,
        "labels": names(plan),
        **{k: plan[k] for k in ("post_words", "message_chars", "messages_per_turn")},
    }
    return TOOL.replace("__CONFIG__", repr(config))


# -- The board and the mail ----------------------------------------------------


def _append(path, lockfile, item):
    """Append one numbered line. Short lock: the supervisor writes here too."""
    with lock(lockfile, wait=True):
        seq = sum(1 for _ in path.open()) if path.exists() else 0
        line = json.dumps({"seq": seq, "t": round(time.time(), 3), **item})
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (line + "\n").encode())
        finally:
            os.close(fd)
    return seq


def _rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def entries(root):
    return _rows(root / "board" / "entries.jsonl")


def post_entry(root, plan, item):
    seq = _append(root / "board" / "entries.jsonl", root / "board.lock", item)
    render_board(root, plan)
    return seq


def mailbox(root, who):
    return _rows(root / "mail" / f"{who}.jsonl")


def send(root, sender, to, text, n, turn):
    item = {"from": sender, "to": to, "round": n, "turn": turn, "text": text}
    return _append(root / "mail" / f"{to}.jsonl", root / "mail.lock", item)


def ordinal(n):
    suffix = (
        "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    )
    return f"{n}{suffix}"


def render_entry(entry, labels):
    kind = entry["kind"]
    who = labels.get(entry.get("seat"), entry.get("seat"))
    if kind == "round":
        order = ", then ".join(labels[s] for s in entry["order"])
        text = f"## Round {entry['round']}\n\nOrder: {order}."
        if entry.get("reason"):
            text += f" The supervisor set this order: {entry['reason'].strip()}"
        if entry.get("final"):
            text += " This is the final round."
        return text
    if kind == "post":
        return (
            f"### {who} (round {entry['round']}, turn {entry['turn']})\n\n"
            f"{entry['text'].strip()}"
        )
    if kind == "silence":
        why = {
            "passed": f"{who} passed.",
            "incomplete": f"{who}'s turn did not complete.",
            "refused": f"{who}'s post was not accepted: {entry.get('detail')}.",
        }[entry["why"]]
        return (
            f"### {who} (round {entry['round']}, turn {entry['turn']})\n\n"
            f"*No post. {why}*"
        )
    if kind == "files":
        parts = [
            f"{verb} {', '.join(entry[key])}"
            for key, verb in (
                ("added", "added"),
                ("changed", "changed"),
                ("removed", "removed"),
            )
            if entry.get(key)
        ]
        return f"*{who} {'; '.join(parts)} in shared/.*"
    if kind == "supervisor":
        return f"### From the supervisor\n\n{entry['text'].strip()}"
    if kind == "retired":
        return (
            f"*{who} has used its context budget ({entry['tokens']:,} of "
            f"{entry['allowance']:,} tokens) and takes no more turns.*"
        )
    raise ValueError(f"unknown board entry: {kind}")


def render_board(root, plan):
    labels = names(plan)
    body = "\n\n".join(render_entry(e, labels) for e in entries(root))
    text = f"# The board: {plan.get('title', 'Roundtable')}\n\n"
    text += body + "\n" if body else "Nothing has been posted yet.\n"
    target = root / "board" / "board.md"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(text)
    temporary.replace(target)


def render_mail(rows, labels, *, received):
    lines = []
    for row in rows:
        other = row["from"] if received else row["to"]
        who = labels.get(other, "the supervisor")
        when = (
            f"round {row['round']}, turn {row['turn']}"
            if row.get("turn")
            else f"before round {row['round']}"
        )
        lines.append(
            f"{'From' if received else 'To'} {who} ({when}):\n\n{row['text'].strip()}"
        )
    return "\n\n".join(lines)


# -- Where the room stands -----------------------------------------------------


def budget(root):
    return read(root / "budget.json")


def final_round(root):
    """The round the supervisor has declared final, or None until then."""
    value = budget(root)
    closed = [d for d in value["decisions"] if d["kind"] == "close"]
    return closed[-1]["end"] if closed and closed[-1]["end"] == value["end"] else None


def schedule(root):
    return read(root / "schedule.json")


def opened(root, n):
    return any(e["kind"] == "round" and e["round"] == n for e in entries(root))


def delivered(root, seat, n):
    return (record_dir(root, seat, n) / "delivered.json").exists()


def active(root, order, n):
    """The round's order without the seats that retired before it began.

    A seat retires in the round whose turn crossed its budget, and that turn
    stays in its round, so numbering inside a round never shifts.
    """
    kept = []
    for seat in order:
        retired = state(root, seat).get("retired")
        if not retired or retired["round"] >= n:
            kept.append(seat)
    return kept


def position(root, plan):
    """What the room does next, derived from the records and nothing else.

    A turn is the first scheduled seat with no recorded turn. Before the
    round the room was promised as its last, the supervisor has to have
    said whether it is: the controller never guesses that. A turn stopped by
    a provider limit holds the room, and a turn recorded but not delivered
    is delivered before anything else happens.
    """
    orders = schedule(root)["rounds"]
    end = budget(root)["end"]
    final = final_round(root)
    for n in range(1, end + 1):
        order = active(root, orders[str(n)], n)
        if not order:
            return {"kind": "spent", "round": n}
        if n == end and final != end:
            return {"kind": "decision", "after": n - 1, "end": end}
        for t, seat in enumerate(order, 1):
            turn = state(root, seat)["rounds"].get(str(n))
            where = {"round": n, "turn": t, "seat": seat, "order": order}
            if turn is None:
                return {"kind": "turn", **where}
            if turn["status"] == "quota":
                return {"kind": "held", **where}
            if turn["status"] == "awaiting":
                return {"kind": "human", **where}
            if not delivered(root, seat, n):
                return {"kind": "deliver", **where}
    return {"kind": "closed", "rounds": end}


def rounds_finished(root, plan):
    orders = schedule(root)["rounds"]
    done = 0
    for n in range(1, budget(root)["end"] + 1):
        if all(delivered(root, s, n) for s in active(root, orders[str(n)], n)):
            done = n
        else:
            break
    return done


# -- Taking a turn -------------------------------------------------------------


def _listing(folder, refused):
    """Regular files under ``folder`` by path and digest; nothing else.

    A link in a shared folder is a pointer into whichever jail reads it next,
    so it is refused by name rather than followed or silently dropped.
    Python's bytecode caches are neither: the interpreter writes them beside
    any script a seat imports, and the second pilot's board announced them as
    that seat's work. They are left where they were made, as the frozen-tree
    digests leave them.
    """
    found = {}
    for path in sorted(folder.rglob("*")):
        rel = str(path.relative_to(folder))
        if "__pycache__" in path.relative_to(folder).parts:
            continue
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            refused.append(rel)
        elif path.is_file():
            found[rel] = digest(path)
    return found


def _copy_files(source, target, listing):
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for rel in listing:
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / rel, target / rel)


def open_round(root, plan, n):
    """Read the round's order onto the board, once, as the round opens."""
    with lock(root / "control.lock", wait=True):
        if opened(root, n):
            return
        value = schedule(root)
        change = next((c for c in reversed(value["changes"]) if c["round"] == n), None)
        post_entry(
            root,
            plan,
            {
                "kind": "round",
                "round": n,
                "order": active(root, value["rounds"][str(n)], n),
                "reason": change["reason"] if change else None,
                "final": final_round(root) == n,
            },
        )
    room.event(root, round=n, event="round-opened")


def stage_turn(root, plan, seat):
    """Put the room as it stands into the seat's workspace before it acts."""
    name = seat["id"]
    work = root / "seats" / name
    if hashes(work / "input") != seat["input_hashes"]:
        raise RuntimeError(f"seat input changed: {name}")
    if hashes(work / "bin") != seat["bin_hashes"]:
        raise RuntimeError(f"seat tool changed: {name}")
    if digest(work / "START.md") != seat["start_sha256"]:
        raise RuntimeError(f"standing assignment changed: {name}")
    leftover = [p for p in (work / "outbox").rglob("*") if p.is_file()]
    if leftover:
        raise RuntimeError(
            f"{name}'s outbox still holds an undelivered turn: "
            + ", ".join(str(p.relative_to(work)) for p in leftover)
        )
    shutil.copyfile(root / "board" / "board.md", work / "board" / "board.md")
    labels = names(plan)
    received = render_mail(mailbox(root, name), labels, received=True)
    sent = [
        row
        for other in [*labels, SUPERVISOR]
        for row in mailbox(root, other)
        if row["from"] == name
    ]
    (work / "mail" / "received.md").write_text((received or "No messages yet.") + "\n")
    (work / "mail" / "sent.md").write_text(
        (
            render_mail(sorted(sent, key=lambda r: r["t"]), labels, received=False)
            or "Nothing sent yet."
        )
        + "\n"
    )
    refused = []
    listing = _listing(root / "shared", refused)
    _copy_files(root / "shared", work / "shared", listing)


def context_line(value):
    if not value:
        return None
    tokens, window = value.get("tokens"), value.get("window")
    if tokens and window:
        share = round(100 * tokens / window)
        text = f"Your context holds about {tokens:,} of {window:,} tokens ({share}%)."
        if share >= 80:
            text += " Leave room for the rounds still to come."
        return text
    if tokens:
        return f"Your context holds about {tokens:,} tokens."
    return "Your harness does not report how full your context is."


def budget_line(seat, value):
    """What a seat has spent and has left, in tokens, at the start of a turn.

    A table prepared before seats had budgets gets the old occupancy line.
    """
    if not seat.get("allowance"):
        return context_line(value.get("context"))
    allowance = seat["allowance"]
    head = f"Your context budget is {allowance:,} tokens."
    if not value["rounds"]:
        return head + " This is your first turn."
    context = value.get("context") or {}
    tokens = context.get("tokens")
    if tokens is None:
        return head + (
            " Your usage was not measured last turn, so what remains is unknown."
        )
    about = "about " if context.get("estimated") else ""
    text = f"{head} You have used {about}{tokens:,}"
    if context.get("added") is not None:
        text += f"; your last turn added {about}{context['added']:,}"
    text += f". {about.capitalize()}{max(0, allowance - tokens):,} remain."
    if context.get("estimated"):
        text += " These are estimates: your harness does not report its usage."
    return text


def turn_prompt(root, plan, seat, where, *, shown_through, mail_shown):
    """What a seat is handed when its turn comes: only what changed.

    The standing assignment is already in the session, so a turn carries the
    position, what arrived on the board and in the mail since the seat was
    last shown them, and how full its context is. New posts are quoted in
    full rather than pointed at: a model handed a path decides whether to
    read it, and one handed the text has read it.
    """
    n, t, order = where["round"], where["turn"], where["order"]
    value = state(root, seat["id"])
    labels = names(plan)
    parts = []
    earlier = any(str(r) in value["rounds"] for r in range(1, n))
    # A person has no session to resume; what they have read is what they
    # have taken turns with.
    fresh = not earlier if is_human(seat) else not value.get("session_id")
    if fresh:
        parts.append(
            "You are joining a room already in progress. Read START.md, your "
            "standing assignment, then board/board.md for everything said so "
            "far."
            if earlier
            else "Read START.md first. It is your standing assignment for the "
            "whole room."
        )
    if fresh and plan.get("inline") and not is_human(seat):
        # A model handed a path decides whether to read it, and one handed
        # the text has read it. In the first pilot two of five seats never
        # opened the chapter the brief told them to start with, and argued
        # about it from the brief's summary.
        work = root / "seats" / seat["id"] / "input"
        parts.append(
            "The core material follows in full. It is also in input/, and "
            "the rest of input/ is there for reference.\n\n"
            + "\n\n".join(
                f"===== input/{rel} =====\n\n"
                f"{(work / relative(rel)).read_text().strip()}\n\n"
                f"===== end of input/{rel} ====="
                for rel in plan["inline"]
            )
        )
    final = final_round(root) == n
    head = f"Round {n}" + (
        ", the final round." if final else f" of at least {budget(root)['end']}."
    )
    head += f" You are {seat['name']}, {ordinal(t)} of {len(order)} this round."
    before, after = order[: t - 1], order[t:]
    if before:
        head += " Before you: " + ", ".join(labels[s] for s in before) + "."
    head += (
        " After you: " + ", ".join(labels[s] for s in after) + "."
        if after
        else " You are last this round."
    )
    parts.append(head)
    news = [
        render_entry(e, labels)
        for e in entries(root)
        if e["seq"] > shown_through and e.get("seat") != seat["id"]
    ]
    if news:
        parts.append(
            "New on the board since you were last shown it:\n\n" + "\n\n".join(news)
        )
    else:
        parts.append("Nothing new on the board since your last turn.")
    mail = mailbox(root, seat["id"])[mail_shown:]
    if mail:
        parts.append(
            "New direct messages for you:\n\n"
            + render_mail(mail, labels, received=True)
        )
    if value.get("refused"):
        parts.append(
            "Not delivered from your last turn: " + "; ".join(value["refused"]) + "."
        )
    line = budget_line(seat, value)
    if line:
        parts.append(line)
    if is_human(seat):
        parts.append(
            "Take your turn: quirework sop roundtable speak "
            f"{shown(root)} {seat['id']} --post FILE (at most "
            f"{plan['post_words']} words), with up to "
            f"{plan['messages_per_turn']} --message NAME TEXT, or --pass to "
            "post nothing. Edit shared/ in your seat's folder first to change "
            "the shared files."
        )
    else:
        parts.append(
            "Take your turn. Post with bin/room post (at most "
            f"{plan['post_words']} words), or pass by not posting. Send up to "
            f"{plan['messages_per_turn']} direct messages with bin/room message."
        )
    return "\n\n".join(parts) + "\n"


def run_turn(root, *, timeout=None):
    """Take the next turn the room owes, or say why none can be taken."""
    root, plan = load(root)
    with lock(root / "room.lock"):
        where = position(root, plan)
        if where["kind"] in WAITS:
            return where
        seat = seat_by(plan, where["seat"])
        n, t = where["round"], where["turn"]
        if where["kind"] == "deliver":
            return {**where, "outcome": deliver(root, plan, seat, n, t)}
        value = state(root, seat["id"])
        if not is_human(seat) and value.get("session_id"):
            # Before the round opens and the turn is staged, so a seat whose
            # private home has gone leaves the table exactly as it was.
            blind.require_session(
                root / "seats" / seat["id"],
                seat["config"]["harness"],
                value["session_id"],
            )
        open_round(root, plan, n)
        stage_turn(root, plan, seat)
        value = state(root, seat["id"])
        shown_through = max((e["seq"] for e in entries(root)), default=-1)
        mail_shown = value.get("mail_shown", 0)
        prompt = turn_prompt(
            root,
            plan,
            seat,
            where,
            shown_through=value.get("shown_through", -1),
            mail_shown=mail_shown,
        )
        record = record_dir(root, seat["id"], n)
        record.mkdir(parents=True)
        write(
            record / "turn.json",
            {
                "round": n,
                "turn": t,
                "order": where["order"],
                "shown_through": shown_through,
                "mail_shown": len(mailbox(root, seat["id"])),
            },
        )
        if is_human(seat):
            # The controller never speaks for a person. It stages the turn,
            # keeps what the person is to read where a model's prompt would
            # be, and waits for `speak`.
            (record / "prompt.md").write_text(prompt)
            value["rounds"][str(n)] = {"status": "awaiting", "error": None}
            write(room.state_path(root, seat["id"]), value)
            room.event(root, round=n, seat=seat["id"], event="awaiting")
            return {**where, "kind": "human"}
        status = room.launch_turn(
            root,
            plan,
            seat,
            n,
            prompt,
            value.get("session_id"),
            timeout=plan["turn_timeout"] if timeout is None else timeout,
            workflow=KIND,
            readonly=READONLY,
        )
        if status == "quota":
            room.event(root, round=n, seat=seat["id"], event="held")
            return {**where, "kind": "held"}
        return {**where, "outcome": deliver(root, plan, seat, n, t)}


def deliver(root, plan, seat, n, t):
    """Take what the turn left in the outbox to the board, the mail and shared/.

    The controller checks every limit again, because the tool is a courtesy
    and the seat could have written the files by hand. Anything refused is
    named on the board or to the seat at its next turn, never dropped: a
    truncated post would put words on the board the seat did not choose.
    The outbox is moved into the record whole, so the evidence of what was
    attempted survives what was delivered.
    """
    name = seat["id"]
    work = root / "seats" / name
    record = record_dir(root, name, n)
    status = state(root, name)["rounds"][str(n)]["status"]
    # The outbox moves into the record first, in one rename, and is read
    # from there. A delivery interrupted halfway is then taken again from
    # the same files, and what it already posted or sent is not repeated.
    outbox = record / "outbox"
    if not outbox.exists():
        shutil.move(str(work / "outbox"), str(outbox))
        (work / "outbox").mkdir()
    done = {
        (e["kind"], e["round"])
        for e in entries(root)
        if e.get("seat") == name and "round" in e
    }
    already_sent = any(
        row["from"] == name and row["round"] == n
        for who in [*names(plan), SUPERVISOR]
        for row in mailbox(root, who)
    )
    refused = []

    text = (outbox / "post.md").read_text() if (outbox / "post.md").is_file() else ""
    words = len(text.split())
    if words and words <= plan["post_words"]:
        (root / "board" / "posts" / name).mkdir(parents=True, exist_ok=True)
        kept = root / "board" / "posts" / name / f"r{n:03d}.md"
        kept.write_text(text.strip() + "\n")
        entry = {"kind": "post", "text": text.strip(), "sha256": digest(kept)}
    elif words:
        detail = f"{words} words, over the {plan['post_words']}-word limit"
        refused.append(f"your post ({detail})")
        entry = {"kind": "silence", "why": "refused", "detail": detail}
    else:
        entry = {
            "kind": "silence",
            "why": "passed" if status == "answered" else "incomplete",
        }
    if ("post", n) not in done and ("silence", n) not in done:
        post_entry(root, plan, {**entry, "round": n, "turn": t, "seat": name})

    sent = []
    folder = outbox / "messages"
    for path in sorted(folder.iterdir()) if folder.is_dir() else []:
        try:
            item = json.loads(path.read_text())
            to = str(item["to"]).strip().lstrip("@").lower()
            body = str(item["text"]).strip()
        except (ValueError, KeyError, TypeError):
            refused.append(f"{path.name} (not a message bin/room wrote)")
            continue
        if to != SUPERVISOR:
            try:
                to = seat_by(plan, to)["id"]
            except ValueError:
                refused.append(f"a message to {to!r} (nobody in the room)")
                continue
        if to == name:
            refused.append("a message to yourself")
        elif not body:
            refused.append(f"{path.name} (empty)")
        elif len(body) > plan["message_chars"]:
            refused.append(
                f"a message to {names(plan).get(to, 'the supervisor')} "
                f"({len(body)} characters, over the {plan['message_chars']} limit)"
            )
        elif len(sent) >= plan["messages_per_turn"]:
            refused.append(
                f"a message to {names(plan).get(to, 'the supervisor')} (over the "
                f"{plan['messages_per_turn']}-message limit for a turn)"
            )
        else:
            if not already_sent:
                send(root, name, to, body, n, t)
            sent.append(to)
    stray = [
        str(p.relative_to(outbox))
        for p in outbox.rglob("*")
        if p.is_file() and p.name != "post.md" and p.parent != folder
    ]
    if stray:
        # Said as what to do next time: a pilot seat told only that
        # post1.md was not bin/room's wrote post2.md the round after.
        refused.append(
            f"{', '.join(stray)} in outbox/, which only bin/room writes; "
            "keep drafts in your own folder"
        )

    links = []
    before = _listing(root / "shared", [])
    after = _listing(work / "shared", links)
    changes = {
        "added": sorted(set(after) - set(before)),
        "changed": sorted(k for k in after if k in before and after[k] != before[k]),
        "removed": sorted(set(before) - set(after)),
    }
    if links:
        refused.append("links in shared/, which are not copied: " + ", ".join(links))
    if any(changes.values()):
        for rel in changes["added"] + changes["changed"]:
            target = record / "shared" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(work / "shared" / rel, target)
        _copy_files(work / "shared", root / "shared", after)
        post_entry(
            root,
            plan,
            {"kind": "files", "round": n, "turn": t, "seat": name, **changes},
        )

    value = state(root, name)
    previous = value.get("context") or {}
    context = None if is_human(seat) else spent(record, seat, previous)
    tokens = context and context["tokens"]
    if tokens is not None:
        first = not any(str(r) in value["rounds"] for r in range(1, n))
        if first or previous.get("tokens") is not None:
            context["added"] = tokens - (previous.get("tokens") or 0)
    allowance = seat.get("allowance")
    if (
        allowance
        and tokens is not None
        and tokens >= allowance
        and not value.get("retired")
    ):
        # The turn that crossed the budget has already been delivered above:
        # the seat said what it came to say, and the room hears it before
        # hearing that the seat is done.
        value["retired"] = {"round": n, "tokens": tokens, "allowance": allowance}
        post_entry(
            root,
            plan,
            {
                "kind": "retired",
                "round": n,
                "seat": name,
                "tokens": tokens,
                "allowance": allowance,
            },
        )
        room.event(root, round=n, seat=name, event="retired", tokens=tokens)
    shown_at = read(record / "turn.json")
    if status == "answered":
        value["shown_through"] = shown_at["shown_through"]
        value["mail_shown"] = shown_at["mail_shown"]
    value["refused"] = refused
    value["context"] = context
    write(room.state_path(root, name), value)
    outcome = {
        "status": status,
        "post": entry["kind"] == "post",
        "messages": sent,
        "refused": refused,
        "shared": changes,
        "context": context,
    }
    write(record / "delivered.json", outcome)
    room.event(root, round=n, seat=name, event="delivered", **outcome)
    release_if_finished(root, plan)
    return outcome


def release_if_finished(root, plan):
    """Let `prune` take the seats' homes once the table has nothing left.

    That is where `position` stops for good: closed after its last round,
    or spent once every seat has retired. A decision, a held turn and a
    person's turn are pauses, and the seats resume their sessions after
    them, so their homes stay held.
    """
    if position(root, plan)["kind"] in ("closed", "spent"):
        for seat in plan["seats"]:
            blind.release(root / "seats" / seat["id"])


def spent(record, seat, previous):
    """How full a model's context is after a turn, from what its harness said."""
    got = read(record / "receipt.json") if (record / "receipt.json").exists() else {}
    return drivers.context_now(
        seat["config"]["harness"],
        record,
        got.get("native_transcripts") or [],
        usage=got.get("usage"),
        before=previous.get("marker"),
        estimate=bool(seat.get("allowance")),
    )


def speak(root, seat, *, post=None, messages=(), passing=False):
    """Deliver a person's turn: their post and messages, through the room's rules.

    The limits a model's tool enforces are checked here before anything is
    queued, and a refusal delivers nothing, so a person can shorten a post
    rather than find it named as refused on the board. What passes goes into
    the seat's outbox and out through the same delivery a model's turn takes,
    which checks it again. Anything already queued in the outbox with
    ``bin/room`` is kept: a post given here replaces a queued one, and
    messages given here join the queued ones.
    """
    root, plan = load(root)
    target = seat_by(plan, seat)
    if not is_human(target):
        raise ValueError(f"{target['name']} is a model; its turns are taken by run")
    with lock(root / "room.lock"):
        where = position(root, plan)
        if where["kind"] != "human" or where["seat"] != target["id"]:
            raise ValueError(
                f"it is not {target['name']}'s turn; "
                + _next_text(where, names(plan), root)
            )
        n, t = where["round"], where["turn"]
        outbox = root / "seats" / target["id"] / "outbox"
        folder = outbox / "messages"
        if passing and post is not None:
            raise ValueError("a pass posts nothing; give --post or --pass, not both")
        if post is not None:
            words = len(post.split())
            if not words:
                raise ValueError("that post is empty; nothing was delivered")
            if words > plan["post_words"]:
                raise ValueError(
                    f"the post has {words} words and the board takes at most "
                    f"{plan['post_words']}; nothing was delivered"
                )
        queued = sorted(folder.glob("*.json")) if folder.is_dir() else []
        if len(queued) + len(messages) > plan["messages_per_turn"]:
            raise ValueError(
                f"a turn sends at most {plan['messages_per_turn']} messages and "
                f"{len(queued) + len(messages)} were given; nothing was delivered"
            )
        letters = []
        for to, text in messages:
            text = text.strip()
            who = (
                SUPERVISOR
                if to.strip().lstrip("@").lower() == SUPERVISOR
                else seat_by(plan, to)["id"]
            )
            if who == target["id"]:
                raise ValueError("a message to yourself; nothing was delivered")
            if not text or len(text) > plan["message_chars"]:
                raise ValueError(
                    f"a message to {names(plan).get(who, 'the supervisor')} has "
                    f"{len(text)} characters and the limit is "
                    f"{plan['message_chars']}; nothing was delivered"
                )
            letters.append({"to": who, "text": text})
        has_post = post is not None or (outbox / "post.md").is_file()
        if passing and (outbox / "post.md").is_file():
            raise ValueError("a post is queued in the outbox; remove it to pass")
        if not (has_post or queued or letters or passing):
            raise ValueError("nothing to deliver: give --post, --message or --pass")
        outbox.mkdir(exist_ok=True)
        if post is not None:
            (outbox / "post.md").write_text(post.strip() + "\n")
        if letters:
            folder.mkdir(exist_ok=True)
        number = len(queued)
        for letter in letters:
            number += 1
            while list(folder.glob(f"{number:02d}-*")):
                number += 1
            write(folder / f"{number:02d}-to-{letter['to']}.json", letter)
        value = state(root, target["id"])
        value["rounds"][str(n)] = {"status": "answered", "error": None}
        write(room.state_path(root, target["id"]), value)
        room.event(root, round=n, seat=target["id"], event="spoke")
        result = {**where, "kind": "turn", "outcome": deliver(root, plan, target, n, t)}
    return result, attention(root, result)


def continue_turn(root, *, timeout=None):
    """Take a held turn again after a provider limit stopped it.

    The same two recoveries as a room's (`room.recovery`): a turn refused
    before the model ran is delivered again into the session it was
    continuing, and one interrupted partway is continued inside the session
    that holds its reading. Anything that is not a classified quota stop is
    refused, because re-running a crash can duplicate work that really
    happened.
    """
    root, plan = load(root)
    with lock(root / "room.lock"):
        where = position(root, plan)
        if where["kind"] != "held":
            raise ValueError("no turn is held by a provider limit")
        seat = seat_by(plan, where["seat"])
        n, name = where["round"], seat["id"]
        record = record_dir(root, name, n)
        stop = quota.classify(seat["config"]["harness"], record)
        if not stop:
            raise RuntimeError(
                f"{seat['name']}'s round {n} turn carries no provider quota stop; "
                "diagnose it rather than taking the turn again"
            )
        resume, prompt, rewound = room.recovery(
            record, root / "seats" / name, seat["config"]["harness"], stop
        )
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
            if (record / item).exists():
                shutil.move(str(record / item), str(folder / item))
        value = state(root, name)
        del value["rounds"][str(n)]
        value.setdefault("attempts", []).append(
            {
                "round": n,
                "attempt": number,
                "quota_stop": stop["phase"],
                "resumed": resume,
                "rewound": rewound,
            }
        )
        write(room.state_path(root, name), value)
        room.event(
            root,
            round=n,
            seat=name,
            event="continuing",
            resumed=resume,
            rewound=rewound,
        )
        status = room.launch_turn(
            root,
            plan,
            seat,
            n,
            prompt,
            resume,
            timeout=plan["turn_timeout"] if timeout is None else timeout,
            workflow=KIND,
            readonly=READONLY,
        )
        if status == "quota":
            return {**where, "kind": "held"}
        return {**where, "outcome": deliver(root, plan, seat, n, where["turn"])}


def run(root, *, to=None, timeout=None):
    """Take turns until the supervisor is needed or the room is done.

    Stops at a decision the supervisor owes, a turn held by a provider limit,
    the end of the room, and the end of any round in which a seat wrote to
    the supervisor or a turn failed: the supervisor answers between rounds,
    and a failing seat is worth a look before the next round spends on it.
    """
    root, plan = load(root)
    taken = []
    while True:
        where = position(root, plan)
        if where["kind"] in WAITS:
            return taken, where
        if to is not None and where["round"] > to:
            return taken, {"kind": "stopped", "round": where["round"]}
        result = run_turn(root, timeout=timeout)
        taken.append(result)
        if result["kind"] in WAITS:
            return taken, result
        stop = attention(root, result)
        if stop:
            return taken, stop


def attention(root, result):
    """Why the supervisor should look before the next round, if a round ended.

    A seat wrote to the supervisor, or a turn failed. Only the turn that
    closes its round asks, whether a model took it or a person spoke it.
    """
    n = result["round"]
    if result["turn"] != len(result["order"]):
        return None
    wrote = [m for m in mailbox(root, SUPERVISOR) if m["round"] == n]
    failed = [
        s
        for s in result["order"]
        if state(root, s)["rounds"][str(n)]["status"] != "answered"
    ]
    if wrote or failed:
        return {"kind": "attention", "round": n, "mail": len(wrote), "failed": failed}
    return None


# -- What the supervisor does ----------------------------------------------------


def announce(root, text):
    """Post to the board as the supervisor. The next seat to act reads it."""
    root, plan = load(root)
    if not text.strip():
        raise ValueError("an announcement needs words")
    seq = post_entry(root, plan, {"kind": "supervisor", "text": text})
    room.event(root, event="announced", seq=seq)
    return seq


def reply(root, seat, text):
    """Write to one seat as the supervisor, delivered at its next turn."""
    root, plan = load(root)
    target = seat_by(plan, seat)
    if not text.strip():
        raise ValueError("a message needs words")
    done = rounds_finished(root, plan)
    where = position(root, plan)
    n = where.get("round") or done + 1
    seq = send(root, SUPERVISOR, target["id"], text.strip(), n, None)
    room.event(root, event="replied", seat=target["id"], seq=seq)
    return target["name"]


def decide(root, kind, text, *, to=None):
    """Say whether the round the room was promised as its last is the last.

    Only the supervisor makes this call, and only once the round before it
    is finished. ``close`` makes it final. ``extend`` moves the end to
    ``to`` and draws orders for the rounds that adds, and the same question
    comes back before the new last round.
    """
    root, plan = load(root)
    with lock(root / "control.lock", wait=True):
        where = position(root, plan)
        if where["kind"] != "decision":
            raise ValueError(
                "no decision is due: it comes once the round before the "
                "room's last scheduled round is finished"
            )
        value = budget(root)
        end = value["end"]
        if kind == "close":
            fact = f"Round {end} is the final round."
        elif kind == "extend":
            if not isinstance(to, int) or to <= end:
                raise ValueError(f"extend to a round after {end}")
            fact = (
                f"The room is extended. It now runs at least to round {to}, "
                f"and the supervisor will say after round {to - 1} whether "
                f"round {to} is the last."
            )
            orders = extend_schedule(
                schedule(root),
                [s["id"] for s in plan["seats"]],
                plan["seed"],
                to,
                fixed_places(plan["seats"]),
            )
            write(root / "schedule.json", orders)
            value["end"] = to
        else:
            raise ValueError("decide close, or extend to a later round")
        value["decisions"].append(
            {"after": end - 1, "kind": kind, "end": value["end"], "t": time.time()}
        )
        write(root / "budget.json", value)
    words = f"{fact}\n\n{text.strip()}" if text and text.strip() else fact
    post_entry(root, plan, {"kind": "supervisor", "text": words, "decision": kind})
    room.event(root, event="decided", kind=kind, end=value["end"])
    release_if_finished(root, plan)
    return fact


def reorder(root, n, seats, reason):
    """Set a round's order before it opens. The reason is read out when it does."""
    root, plan = load(root)
    if not reason or not reason.strip():
        raise ValueError("say why: the room hears the reason when the round opens")
    ids = [seat_by(plan, s)["id"] for s in seats]
    if sorted(ids) != sorted(s["id"] for s in plan["seats"]):
        raise ValueError("name every seat exactly once")
    with lock(root / "control.lock", wait=True):
        value = schedule(root)
        if str(n) not in value["rounds"]:
            raise ValueError(f"round {n} is not scheduled")
        if opened(root, n):
            raise ValueError(f"round {n} has opened; its order is on the board")
        previous = value["rounds"][str(n)]
        value["rounds"][str(n)] = ids
        value["changes"].append(
            {
                "round": n,
                "order": ids,
                "was": previous,
                "reason": reason.strip(),
                "t": time.time(),
            }
        )
        write(root / "schedule.json", value)
    room.event(root, event="reordered", round=n, order=ids)
    return ids


# -- Reading a roundtable ------------------------------------------------------------


def signals(root):
    """Novelty and closing language per round, over posts only.

    The same two measures the room uses to notice it has run dry. They are
    for the supervisor, and the seats are never shown them.
    """
    seen, series = {}, []
    posts = [e for e in entries(root) if e["kind"] == "post"]
    for n in sorted({e["round"] for e in posts}):
        novel, closers = [], 0
        for e in (e for e in posts if e["round"] == n):
            words = {
                w
                for w in room.WORD.findall(e["text"].lower())
                if w not in room.COMMON and len(w) > 3
            }
            known = seen.setdefault(e["seat"], set())
            novel.append(100 * len(words - known) / max(1, len(words)))
            known |= words
            closers += bool(room.CLOSING.search(e["text"]))
        series.append(
            {
                "round": n,
                "novelty": round(sum(novel) / len(novel), 1),
                "closing": round(closers / len(novel), 2),
            }
        )
    return series


def status(root):
    root, plan = load(root)
    where = position(root, plan)
    value = budget(root)
    seats = []
    for number, seat in enumerate(plan["seats"], 1):
        mine = state(root, seat["id"])
        turns = mine["rounds"]
        flags = []
        for n, turn in sorted(turns.items(), key=lambda kv: int(kv[0])):
            if turn["status"] not in ("answered", "awaiting"):
                flags.append(
                    f"r{int(n):03d} {turn['status']}: {turn.get('error') or ''}".strip()
                )
            got = record_dir(root, seat["id"], int(n)) / "receipt.json"
            if got.exists() and read(got).get("compaction_markers"):
                flags.append(f"r{int(n):03d} compacted")
            made = record_dir(root, seat["id"], int(n)) / "delivered.json"
            if made.exists() and read(made)["refused"]:
                flags.append(
                    f"r{int(n):03d} refused: " + "; ".join(read(made)["refused"])
                )
        config = seat.get("config") or {}
        seats.append(
            {
                "number": number,
                "id": seat["id"],
                "name": seat["name"],
                "human": is_human(seat),
                "model": drivers.model_text(config) if config else None,
                "selected": config.get("model"),
                "served": mine.get("model_served"),
                "harness": config.get("harness"),
                "effort": config.get("effort"),
                "turns": len(turns),
                "context": mine.get("context"),
                "window": seat.get("window"),
                "allowance": seat.get("allowance"),
                "retired": mine.get("retired"),
                "flags": flags,
            }
        )
    mail = mailbox(root, SUPERVISOR)
    return {
        "root": str(root),
        "title": plan.get("title"),
        "goal": plan["goal"],
        "finished": rounds_finished(root, plan),
        "declared": value["declared"],
        "end": value["end"],
        "final": final_round(root),
        "next": where,
        "seats": seats,
        "signals": signals(root),
        "supervisor_mail": mail,
    }


def _next_text(where, labels, root):
    kind = where["kind"]
    command = f"quirework sop roundtable {{}} {shown(root)}"
    if kind == "turn":
        return (
            f"next: round {where['round']}, turn {where['turn']} "
            f"({labels[where['seat']]}): {command.format('run')}"
        )
    if kind == "deliver":
        return f"next: deliver {labels[where['seat']]}'s round {where['round']} turn"
    if kind == "human":
        record = record_dir(root, where["seat"], where["round"]) / "prompt.md"
        return (
            f"waiting for {labels[where['seat']]}: round {where['round']}, turn "
            f"{where['turn']}. What is new for them is in {shown(record)}; "
            f"{command.format('speak')} {where['seat']} --post FILE"
        )
    if kind == "held":
        return (
            f"held: {labels[where['seat']]}'s round {where['round']} turn hit a "
            f"provider limit; after it resets, {command.format('continue')}"
        )
    if kind == "decision":
        return (
            f"decision due after round {where['after']}: is round {where['end']} "
            f"the last? {command.format('decide')} close|extend --to N -m TEXT"
        )
    if kind == "spent":
        return (
            f"every seat has used its context budget before round "
            f"{where['round']}; the room cannot go on"
        )
    return f"closed after round {where['rounds']}"


def _occupancy(value, allowance=None, window=None):
    """A seat's context as the supervisor reads it, not as the seat is told."""
    if not value:
        return "no turn yet"
    if allowance:
        tokens = value.get("tokens")
        spent = (
            f"{tokens:,} of a {allowance:,}-token budget"
            f" ({round(100 * tokens / allowance)}%)"
            if tokens is not None
            else f"unmeasured, against a {allowance:,}-token budget"
        )
        return f"context {spent}, window {window:,}" + (
            ", estimated" if value.get("estimated") else ""
        )
    tokens, window = value.get("tokens"), value.get("window")
    if tokens and window:
        return (
            f"context {tokens:,} of {window:,} tokens ({round(100 * tokens / window)}%)"
        )
    if tokens:
        return f"context {tokens:,} tokens, window unknown"
    return f"context not reported ({value.get('evidence')})"


def render_status(value):
    root = Path(value["root"])
    labels = {s["id"]: s["name"] for s in value["seats"]}
    final = (
        f"round {value['final']} is final"
        if value["final"]
        else f"runs at least {value['end']}"
    )
    lines = [
        f"{value['title'] or 'Roundtable'}  ({shown(root)})",
        f"rounds finished: {value['finished']}; {final}",
        _next_text(value["next"], labels, root),
        "",
    ]
    for seat in value["seats"]:
        served = (
            f" -> {seat['served']}"
            if seat["served"] and seat["served"] != seat["selected"]
            else ""
        )
        if seat["human"]:
            lines.append(
                f"  {seat['number']}. {seat['name']:<14} a person  "
                f"turns: {seat['turns']}"
            )
        else:
            lines.append(
                f"  {seat['number']}. {seat['name']:<14} "
                f"{seat['harness']}/{seat['model']}{served} {seat['effort']}  "
                f"turns: {seat['turns']}"
            )
            context = _occupancy(
                seat["context"], seat.get("allowance"), seat.get("window")
            )
            lines.append(f"       {context}")
        lines += [f"       ! {flag}" for flag in seat["flags"]]
        if seat.get("retired"):
            gone = seat["retired"]
            lines.append(
                f"       ! retired after round {gone['round']}: {gone['tokens']:,} "
                f"of {gone['allowance']:,} tokens"
            )
    if value["signals"]:
        lines += ["", "  round  novelty  closing"]
        lines += [
            f"  {s['round']:>5}  {s['novelty']:>7}  {s['closing']:>7}"
            for s in value["signals"]
        ]
    mail = value["supervisor_mail"]
    if mail:
        lines += [
            "",
            (
                f"  {len(mail)} message(s) to the supervisor; read them with "
                f"quirework sop roundtable inbox {shown(root)}"
            ),
        ]
    return "\n".join(lines)


def inbox(root):
    root, plan = load(root)
    labels = names(plan)
    return render_mail(mailbox(root, SUPERVISOR), labels, received=True)


def all_mail(root, n=None):
    """Every direct message in the room, in the order sent.

    The supervisor reads everything, and the seats are told so, but `inbox`
    shows only what was sent to the supervisor. Briefing a person seated at
    the table meant reading the other seats' mail by hand after every round.
    """
    root, plan = load(root)
    labels = {**names(plan), SUPERVISOR: "supervisor"}
    rows = [
        row
        for who in [*names(plan), SUPERVISOR]
        for row in mailbox(root, who)
        if n is None or row["round"] == n
    ]
    lines = []
    for row in sorted(rows, key=lambda r: r["t"]):
        when = f"r{row['round']}" + (f" t{row['turn']}" if row.get("turn") else "")
        lines.append(
            f"{when} {labels.get(row['from'], row['from'])} -> "
            f"{labels.get(row['to'], row['to'])}: {row['text'].strip()}"
        )
    return "\n".join(lines)


def _text(value):
    return sys.stdin.read() if value == "-" else value


def _print_taken(taken, where, root, labels):
    for result in taken:
        outcome = result.get("outcome")
        if not outcome:
            continue
        what = "posted" if outcome["post"] else "no post"
        extra = (
            f", {len(outcome['messages'])} message(s)" if outcome["messages"] else ""
        )
        refused = (
            f", refused: {'; '.join(outcome['refused'])}" if outcome["refused"] else ""
        )
        print(
            f"round {result['round']} turn {result['turn']} "
            f"{labels[result['seat']]}: {outcome['status']}, {what}{extra}{refused}"
        )
    kind = where["kind"]
    if kind == "attention":
        why = []
        if where["mail"]:
            why.append(f"{where['mail']} message(s) to the supervisor")
        if where["failed"]:
            why.append(
                "failed turn(s): " + ", ".join(labels[s] for s in where["failed"])
            )
        print(f"stopped after round {where['round']}: {'; '.join(why)}")
    elif kind == "stopped":
        print(f"stopped before round {where['round']}")
    else:
        print(_next_text(where, labels, root))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="quirework sop roundtable",
        description=__doc__.split("\n\n")[0],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    made = sub.add_parser("prepare", help="stage a roundtable from a plan")
    made.add_argument("plan")
    made.add_argument("destination")

    for verb, text in (
        ("turn", "take the next turn"),
        ("run", "take turns until the supervisor is needed"),
        ("continue", "take a turn held by a provider limit again"),
    ):
        step = sub.add_parser(verb, help=text)
        step.add_argument("root")
        step.add_argument("--timeout", type=float, default=None, help="seconds")
        if verb == "run":
            step.add_argument(
                "--to", type=int, default=None, help="stop after this round"
            )

    view = sub.add_parser("status", help="read a roundtable without disturbing it")
    view.add_argument("root")
    view.add_argument("--json", action="store_true")

    board = sub.add_parser("board", help="print the board")
    board.add_argument("root")

    mail = sub.add_parser("inbox", help="print the messages sent to the supervisor")
    mail.add_argument("root")

    voice = sub.add_parser("speak", help="deliver a person's turn")
    voice.add_argument("root")
    voice.add_argument("seat")
    voice.add_argument("--post", help="a file holding the post, or - for stdin")
    voice.add_argument(
        "--message",
        nargs=2,
        action="append",
        default=[],
        metavar=("NAME", "TEXT"),
        help="a direct message; repeat for more",
    )
    voice.add_argument("--pass", dest="passing", action="store_true")

    letters = sub.add_parser("mail", help="print every direct message, in order")
    letters.add_argument("root")
    letters.add_argument("--round", type=int, default=None)

    say = sub.add_parser("announce", help="post to the board as the supervisor")
    say.add_argument("root")
    say.add_argument("text", help="the words, or - to read them from stdin")

    answer = sub.add_parser("reply", help="write to one seat as the supervisor")
    answer.add_argument("root")
    answer.add_argument("seat")
    answer.add_argument("text", help="the words, or - to read them from stdin")

    call = sub.add_parser(
        "decide", help="say whether the scheduled last round is final"
    )
    call.add_argument("root")
    call.add_argument("kind", choices=("close", "extend"))
    call.add_argument("--to", type=int, default=None, help="the new last round")
    call.add_argument("-m", "--message", default="", help="why, in your words")

    order = sub.add_parser("order", help="set a round's order before it opens")
    order.add_argument("root")
    order.add_argument("round", type=int)
    order.add_argument("seats", nargs="+")
    order.add_argument("--reason", required=True)

    args = parser.parse_args(argv)
    if args.command == "prepare":
        destination = table_root(args.destination, must_exist=False)
        value = prepare(args.plan, destination)
        print(f"prepared {len(value['seats'])} seats in {shown(destination)}")
        for seat in value["seats"]:
            what = (
                "a person"
                if is_human(seat)
                else f"{seat['config']['harness']}/{seat['config']['model']}"
            )
            print(f"  {seat['name']}: {what}")
        return 0
    root = table_root(args.root)
    labels = names(load(root)[1])
    if args.command == "turn":
        result = run_turn(root, timeout=args.timeout)
        _print_taken([result], position(root, load(root)[1]), root, labels)
    elif args.command == "run":
        taken, where = run(root, to=args.to, timeout=args.timeout)
        _print_taken(taken, where, root, labels)
    elif args.command == "continue":
        result = continue_turn(root, timeout=args.timeout)
        _print_taken([result], position(root, load(root)[1]), root, labels)
    elif args.command == "status":
        value = status(root)
        print(json.dumps(value, indent=2) if args.json else render_status(value))
    elif args.command == "board":
        print((root / "board" / "board.md").read_text(), end="")
    elif args.command == "mail":
        print(all_mail(root, args.round) or "No direct messages.")
    elif args.command == "inbox":
        print(inbox(root) or "No messages to the supervisor.")
    elif args.command == "speak":
        post = None
        if args.post is not None:
            post = sys.stdin.read() if args.post == "-" else Path(args.post).read_text()
        result, stop = speak(
            root,
            args.seat,
            post=post,
            messages=[tuple(m) for m in args.message],
            passing=args.passing,
        )
        _print_taken([result], stop or position(root, load(root)[1]), root, labels)
    elif args.command == "announce":
        announce(root, _text(args.text))
        print("posted to the board; the next seat to act reads it")
    elif args.command == "reply":
        who = reply(root, args.seat, _text(args.text))
        print(f"sent to {who}; delivered at their next turn")
    elif args.command == "decide":
        print(decide(root, args.kind, args.message, to=args.to))
    elif args.command == "order":
        ids = reorder(root, args.round, args.seats, args.reason)
        print(f"round {args.round}: " + ", then ".join(labels[s] for s in ids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
