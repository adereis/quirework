"""Backfilling a spec trail: packets for cheap readers, and a ledger of leads.

A trail kept from the start needs none of this. The agent that heard the
author adds each breadcrumb as it happens. A spec that grew before it had a
trail needs its history read, and a corpus of hundreds of sessions is too
large for a strong model to read whole. So the work is split by what it
costs. A cheap model reads each session once, as a compact packet, and flags
every author turn that might have shaped any section. A strong model then
works one section at a time through everything flagged for it, and decides.
Reading a session costs the same whether it is read for one section or for
all of them, which is why each session is read once, against the whole menu.

This module owns what sits between the two readers:

* the packet a reader is handed, and the menu it names sections from;
* a run plan that hands packets to readers through the run engine, one seat
  per batch, so no reader's context grows from one batch to the next;
* the ledger: each reader's flags, imported and checked against the corpus,
  and the ruling reader's rejections, so no later pass pays to read the same
  candidate again.

A flag is a lead and never a breadcrumb; a breadcrumb is written with
``trail add``. Only the ruling reader drops a candidate, and it records why.

The ledger lives where ``book.spec_trail_leads`` puts it. It is as private as
the trail, and more so: a reader's reasons paraphrase the author's turns.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from quirework.book import notes, trail
from quirework.project import ConfigurationError, current_project

# Bump when a packet shows a reader something materially different, so the
# ledger can tell a session read from the current packet from an older one.
PACKET_VERSION = 1

# How much of the reply a turn answered is shown. The end is kept, because a
# reply closes on what it proposes, and that is what a bare "yes" accepts.
REPLY_CHARS = 1200
# A long turn is usually pasted material; its head and tail carry the
# author's own framing of it. The ruling reader opens the whole turn.
TURN_HEAD = 4000
TURN_TAIL = 1500
GIST_CHARS = 280
BATCH_TOKENS = 30000
# A seat that flags almost nothing over many turns may have written its done
# lines without reading. Seats in one real pass flagged 12% to 79% of their
# author turns, by what the sessions were about, so the floor is absolute and
# low: it catches a seat that skipped, not one that read tooling sessions.
DILIGENCE_TURNS = 20
DILIGENCE_RATE = 0.05

CONFIDENCE = ("high", "medium", "low")
READ_KINDS = (*trail.KINDS, "unsure")

# The classes search's extractor gives a human turn, as a reader should read
# them: whether the words are the author's own is the question that matters.
_SHAPES = {
    "TRIVIAL": "short",
    "STRUCTURED": "document-shaped",
    "RELAY": "mostly pasted",
    "REVIEW": "comments on a quote",
    "AMBIGUOUS": "long",
    "TYPED": "typed",
}

# A red-pen round is read like a session, but it is not a conversation, and
# the reader is told what its parts are before it reads one.
REDPEN_NOTE = """
This is not a conversation. It is a red-pen round: the author's marks on a
rendered copy of the book, one mark per turn, with no replies. A comment's
anchor quotes the book, not the author; its note is the author's. A tracked
edit is the author's. A review-notes entry shows a note block as it was
("- was") and as the author left it ("+ now"); only what the author added or
changed is the author's, since a block can repeat text it already held.
"""

INSTRUCTIONS = """\
You are reading conversations in which an author worked out the ideas of a
book. The book keeps a spec: a list of sections, each stating one thing the
book holds. The menu below lists them. The packets below hold the author's
turns from one or more sessions, each turn with the end of the reply it
answered.

Flag every author turn that may have shaped what a section holds, so that a
later reader can decide. Flag generously. A turn you are unsure about is worth
flagging with low confidence, because a turn nobody flags is lost, while a
later, careful reader rejects what does not belong.

A turn shapes a section when the author, in their own words:
- seed: first raises the idea;
- sharpen: makes it more precise, or changes its words;
- correct: rejects a wording or claim the model had proposed;
- reverse: moves off a position the author held earlier;
- accept: adopts a wording or claim the model drafted, even with a bare "yes";
- probe: tries the idea out, or wonders, without settling it.
Use "unsure" when a turn bears on a section but you cannot tell how.

Rules:
- Only the author's words count. Text the author pasted from elsewhere (a
  review, another model's output, a document) shapes nothing by itself; the
  author's own comment on it may.
- A bare approval ("yes", "go", "commit") matters only when the reply it
  answered proposed something specific about a section. Then it is an accept.
- The conversations may use older names for things than the menu does.
  Match by meaning, not by words.
- Name sections only by the slugs in the menu. A turn may shape several
  sections; list each one.
- Leave out turns that shape no section.

Answer with JSON lines and nothing else. One line per flagged turn:
{"session": "<the id after '# Session'>", "turn": <its number>, "sections": [{"slug": "<slug>", "kind": "<kind>", "confidence": "high|medium|low", "reason": "<one short sentence>"}]}
After a session's last flagged turn, one line saying you finished it, with
one sentence, in your own words, on what the author and the model were doing
across the whole session:
{"session": "<id>", "done": true, "about": "<one sentence>"}
Every session in the packets needs its done line, even one with nothing to
flag. The about sentence is how a later reader knows the session was read, so
write it from the turns, not from the first one alone.
"""


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
def ledger():
    """The ledger's directory: what readers flagged, and what was rejected."""
    project = current_project()
    if not trail.configured():
        raise ConfigurationError(
            "a backfill ledger belongs to a spec trail: set book.spec_trail first"
        )
    if project.get("book.spec_trail_leads") is None:
        raise ConfigurationError(
            "this project keeps no backfill ledger: set book.spec_trail_leads in "
            "quirework.toml to the directory that should hold it"
        )
    return project.path("book.spec_trail_leads")


def _write_private(path, text):
    """Write ``path`` atomically, readable by its owner only, like the trail."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _utc(moment):
    """A logged time as a reader sees it, to the second."""
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _instant(moment):
    """A logged time as the ledger stores it: exact, since turns share seconds."""
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


# --------------------------------------------------------------------------- #
# what a reader is handed
# --------------------------------------------------------------------------- #
def _gist(body):
    text = " ".join(re.sub(r"<!--.*?-->", " ", body, flags=re.S).split())
    if len(text) <= GIST_CHARS:
        return text
    return text[: GIST_CHARS - 1].rsplit(" ", 1)[0] + "…"


def menu():
    """The spec's sections as a reader names them.

    Each line gives the slug, the title, how the section opens, and the
    chapters that bind it. A conversation often names the chapter it was
    working on rather than the idea, and the binding is how a reader gets from
    one to the other.
    """
    bound = {}
    for chapter, slugs in notes.chapter_bindings().items():
        for slug in slugs:
            bound.setdefault(slug, set()).add(chapter)
    lines = [
        "# The spec's sections",
        "",
        (
            "Name a section only by its slug. Each line gives the section's "
            "title, how it opens, and the chapters that express it."
        ),
        "",
    ]
    for slug, title, body in notes.spec_sections():
        if slug:
            chapters = ", ".join(sorted(bound.get(slug, ()))) or "none"
            lines.append(f"- `{slug}`: {title}. {_gist(body)} [chapters: {chapters}]")
    return "\n".join(lines) + "\n"


def _tail(text, limit):
    text = text.strip()
    return text if len(text) <= limit else "… " + text[-limit:].lstrip()


def _clip(text):
    text = text.strip()
    if len(text) <= TURN_HEAD + TURN_TAIL + 200:
        return text
    cut = len(text) - TURN_HEAD - TURN_TAIL
    return (
        f"{text[:TURN_HEAD].rstrip()}\n\n[… {cut:,} characters not shown …]\n\n"
        f"{text[-TURN_TAIL:].lstrip()}"
    )


def _quoted(text):
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines())


def packet(sid, family, path):
    """One session as a reader reads it: each author turn, and what it answered.

    Turns are numbered as ``provenance show`` numbers them, and stamped with
    the logged UTC time, so a flag can be followed back to the turn it names.
    """
    from quirework.provenance import humaninput, show

    rows = show.exchanges(family, path)
    day = show.session_date(family, path)
    begun = f"started {day:%Y-%m-%d}" if day else "undated"
    lines = [
        f"# Session {sid}",
        "",
        f"{trail._HARNESS_OF[family]} · {begun} · {len(rows)} author turn(s)",
    ]
    if family == "redpen":
        lines += ["", REDPEN_NOTE.strip()]
    for number, logged, text, reply in rows:
        shape = _SHAPES.get(humaninput.classify(text), "typed")
        lines += [
            "",
            f"## Turn {number} · {_utc(logged) or 'no logged time'} · {shape}",
        ]
        if reply.strip():
            lines += [
                "",
                "It answered the end of this reply:",
                "",
                _quoted(_tail(reply, REPLY_CHARS)),
            ]
        lines += ["", "The author wrote:", "", _clip(text) or "(nothing)"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# the ledger's reads
# --------------------------------------------------------------------------- #
def _reads():
    """Every imported read, as the dicts the ledger stores."""
    root = ledger() / "reads"
    return [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(root.glob("*/*.json"))
    ]


def _current(read):
    """Whether a read was made from today's packet.

    A section added since is deliberately not a reason to read the session
    again: that would hand out the whole corpus for one section, whose
    history is mostly in the session that added it. ``status`` counts such
    reads so the choice stays visible.
    """
    return read.get("packet_version") == PACKET_VERSION


def _kept(corpus):
    """Captured sessions curation has accepted, as ``sid -> (family, path)``."""
    kept = trail._kept_paths(corpus)
    return {
        sid: (family, path)
        for sid, (family, path) in corpus.captured().items()
        if kept is None or Path(path).resolve() in kept
    }


# --------------------------------------------------------------------------- #
# a run plan
# --------------------------------------------------------------------------- #
def run_directory(model):
    """A new run directory in the project's private state, named for today.

    Packets and the run record hold corpus text, so they belong outside the
    checkout; a second pass on the same day takes the next free suffix.
    """
    root = current_project().state_root
    stem = f"trail-read-{date.today().isoformat()}-{re.sub(r'[^A-Za-z0-9.-]+', '-', model)}"
    candidate, n = root / stem, 1
    while candidate.exists():
        n += 1
        candidate = root / f"{stem}-{n}"
    return candidate


def plan(
    out,
    *,
    harness,
    model,
    effort,
    tier="cheap",
    sessions=None,
    scope=None,
    limit=None,
    budget=BATCH_TOKENS,
    materials=(),
    reread=False,
    external=False,
):
    """Write packets and a run plan that hands them to readers; return a summary.

    Sessions are taken oldest first from the curated corpus, skipping any a
    current read already covers unless ``reread`` or named in ``sessions``.
    A source that logs no times but records its date, a gemini review or a
    red-pen round, takes its place by that date; one with neither sorts
    first, and the summary names it. ``out`` of ``None`` plans into a new
    ``run_directory``, as ``RUN/packets``, beside which the record goes.
    Each seat gets one batch of packets up to ``budget`` estimated tokens,
    with the menu and any book ``materials`` in front, and no tools.
    """
    from quirework.provenance import scope as SCOPE
    from quirework.provenance import show

    out = (
        run_directory(model) / "packets"
        if out is None
        else Path(out).expanduser().resolve()
    )
    project = current_project()
    if out.is_relative_to(project.root) and not out.is_relative_to(
        project.path("book.build", "build")
    ):
        raise ValueError(
            "packets hold private corpus text: write them outside the checkout, "
            "or under build/"
        )
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} is not empty; plan into a new directory")
    ledger()
    corpus = trail.Corpus()
    kept = _kept(corpus)
    slugs = [s for s, _, _ in notes.spec_sections() if s]
    if sessions:
        chosen = []
        for prefix in sessions:
            sid, family, path, live = corpus.session(prefix)
            if live or sid not in kept:
                raise LookupError(f"{sid[:8]} is not in the curated corpus")
            chosen.append(sid)
    else:
        covered = {r["session"] for r in _reads() if _current(r)}
        chosen = [
            sid
            for sid in kept
            if (reread or sid not in covered)
            and (scope is None or SCOPE.scope_of_id(sid) == scope)
        ]
    dated, undated = [], set()
    for sid in chosen:
        family, path = kept[sid]
        if not show.author_turns(family, path):
            continue
        day = show.session_date(family, path)
        start = show.session_start(family, path) or (
            datetime.combine(day, datetime.min.time(), timezone.utc) if day else None
        )
        if start is None:
            undated.add(sid)
        dated.append((start or datetime.min.replace(tzinfo=timezone.utc), sid))
    dated.sort()
    if limit is not None:
        dated = dated[:limit]

    (out / "packets").mkdir(parents=True, exist_ok=True)
    (out / "menu.md").write_text(menu(), encoding="utf-8")
    extra = [
        {"source": str(Path(m).expanduser().resolve(strict=True)), "delivery": "text"}
        for m in materials
    ]
    batches, current, size = [], [], 0
    for _, sid in dated:
        family, path = kept[sid]
        text = packet(sid, family, path)
        name = f"packets/{sid}.md"
        (out / name).write_text(text, encoding="utf-8")
        tokens = len(text) // 4
        if current and size + tokens > budget:
            batches.append(current)
            current, size = [], 0
        current.append((sid, name, tokens))
        size += tokens
    if current:
        batches.append(current)

    seats = []
    for index, batch in enumerate(batches, 1):
        seat = {
            "id": f"read-{index:03d}",
            "harness": harness,
            "model": model,
            "effort": effort,
            "tier": tier,
            # agy audits file access and cannot enforce isolation; the seat
            # has no tools either way, and its receipt says which it was.
            "policy": {
                "tools": "none",
                "filesystem": "audited" if harness == "agy" else "enforced",
            },
            # agy keeps its login in the keyring, which every agy jail can
            # reach; a seat with no tools cannot use it, so the flag is
            # expected and only another one stops the import.
            **({"accept_red_flags": ["session-bus"]} if harness == "agy" else {}),
            "metadata": {
                "trail_read": {
                    "sessions": [sid for sid, _, _ in batch],
                    "packet_version": PACKET_VERSION,
                    "slugs": slugs,
                }
            },
            "turns": [
                {
                    "id": "read",
                    "prompt": INSTRUCTIONS,
                    "materials": [
                        {"source": "menu.md", "delivery": "text", "label": "menu"},
                        *extra,
                        *(
                            {"source": name, "delivery": "text", "label": sid[:8]}
                            for sid, name, _ in batch
                        ),
                    ],
                    "min_bytes": 20,
                }
            ],
        }
        if external:
            seat["mode"] = "external"
        seats.append(seat)
    if not seats:
        raise LookupError("no session is left to read")
    document = {
        "title": f"Spec trail reading: {len(dated)} session(s)",
        "metadata": {"trail_read": {"packet_version": PACKET_VERSION}},
        "seats": seats,
    }
    (out / "plan.json").write_text(json.dumps(document, indent=2) + "\n")
    # What every seat is handed besides its packets, estimated as packets are.
    fixed = (
        len(INSTRUCTIONS)
        + len((out / "menu.md").read_text(encoding="utf-8"))
        + sum(Path(m["source"]).stat().st_size for m in extra)
    ) // 4
    packets = sum(t for batch in batches for _, _, t in batch)
    harnesses = {}
    for _, sid in dated:
        name = trail._HARNESS_OF[kept[sid][0]]
        harnesses[name] = harnesses.get(name, 0) + 1
    return {
        "plan": str(out / "plan.json"),
        "record": str(out.parent / "record") if out.name == "packets" else None,
        "seats": len(seats),
        "sessions": len(dated),
        "harnesses": harnesses,
        "undated": [sid for _, sid in dated if sid in undated],
        "tokens": packets,
        "total_tokens": packets + fixed * len(seats),
    }


def plan_lines(done):
    """A plan's summary as printable lines, ending with the commands to run."""
    lines = [
        f"{done['sessions']} session(s) in {done['seats']} seat(s): "
        + ", ".join(f"{n} {h}" for h, n in done["harnesses"].items()),
        f"about {done['tokens']:,} tokens of packets, "
        f"{done['total_tokens']:,} with each seat's menu and instructions",
    ]
    if done["undated"]:
        lines.append(
            f"{len(done['undated'])} session(s) have no start time and are "
            "planned first: " + ", ".join(sid[:16] for sid in done["undated"])
        )
    record = done["record"] or "RECORD"
    lines += [
        f"plan: {done['plan']}",
        "next:",
        f"  quirework sop runs prepare {done['plan']} {record}",
        f"  quirework sop runs start {record}",
        f"  quirework book trail backfill import {record}",
    ]
    return lines


# --------------------------------------------------------------------------- #
# importing what readers flagged
# --------------------------------------------------------------------------- #
def _objects(answer):
    """The JSON objects in an answer, one per line, tolerating fences and prose."""
    found, skipped = [], []
    for line in answer.splitlines():
        start, end = line.find("{"), line.rfind("}")
        if start < 0 or end <= start:
            if line.strip() and not line.strip().startswith("```"):
                skipped.append(line.strip()[:120])
            continue
        try:
            value = json.loads(line[start : end + 1])
        except json.JSONDecodeError:
            skipped.append(line.strip()[:120])
            continue
        if isinstance(value, dict):
            found.append(value)
        else:
            skipped.append(line.strip()[:120])
    return found, skipped


def parse_answer(answer, sessions, corpus):
    """``{sid: read}`` for each session the answer finished, plus what it dropped.

    Every flag is checked against the corpus: the session must be one the seat
    was handed, the turn one of the author's, and each slug a spec section. A
    flag that fails is dropped and reported, never guessed into shape. The
    logged time stored with a flag comes from the corpus, not the reader, so
    the flag survives a later renumbering of the session's turns.
    """
    titles = trail._spec_titles()
    captured = corpus.captured()
    handed = {sid: captured[sid] for sid in sessions if sid in captured}
    turns = {
        sid: {t.number: t for t in corpus.turns(sid, *handed[sid])} for sid in handed
    }
    objects, skipped = _objects(answer)
    dropped = [{"line": s, "why": "not a JSON object"} for s in skipped]
    dropped += [
        {"line": sid, "why": "no longer in the corpus"}
        for sid in sessions
        if sid not in handed
    ]
    flags = {sid: {} for sid in handed}
    finished = set()
    about = {}

    def which(value):
        hits = [
            s
            for s in handed
            if isinstance(value, str) and value and s.startswith(value)
        ]
        return hits[0] if len(hits) == 1 else None

    for obj in objects:
        sid = which(obj.get("session"))
        if sid is None:
            dropped.append({"line": json.dumps(obj)[:200], "why": "unknown session"})
            continue
        if obj.get("done") is True:
            finished.add(sid)
            about[sid] = " ".join(str(obj.get("about") or "").split())[:300] or None
            continue
        number = obj.get("turn")
        turn = turns[sid].get(number) if type(number) is int else None
        if turn is None:
            dropped.append(
                {"line": json.dumps(obj)[:200], "why": "no such author turn"}
            )
            continue
        for item in obj.get("sections") or []:
            if not isinstance(item, dict) or item.get("slug") not in titles:
                dropped.append(
                    {"line": json.dumps(item)[:200], "why": "not a spec section"}
                )
                continue
            kind = item.get("kind") if item.get("kind") in READ_KINDS else "unsure"
            confidence = item.get("confidence")
            confidence = confidence if confidence in CONFIDENCE else "low"
            reason = " ".join(str(item.get("reason", "")).split())[:300]
            entry = flags[sid].setdefault(
                number,
                {"turn": number, "logged": _instant(turn.logged), "sections": []},
            )
            entry["sections"].append(
                {
                    "slug": item["slug"],
                    "kind": kind,
                    "confidence": confidence,
                    "reason": reason,
                }
            )
    reads = {
        sid: {
            "session": sid,
            "harness": trail._HARNESS_OF[handed[sid][0]],
            "turns": len(turns[sid]),
            "about": about[sid],
            "candidates": [flags[sid][n] for n in sorted(flags[sid])],
        }
        for sid in finished
    }
    return reads, dropped


def import_run(record, accept=()):
    """Bring each finished seat's flags into the ledger; return one row per seat.

    The record is verified first, so a changed answer or input is refused. A
    seat with a red flag its plan did not accept is held, and nothing it
    read is recorded: an unexpected flag is the author's call, not the
    importer's. ``accept`` names flag codes to accept here instead, for a
    record prepared before its plan could declare them, and only under the
    same policy a plan would need. A session whose done line is missing is
    not recorded as read: it is left for a later plan to hand out again.
    What an answer held that could not be checked is kept beside the reads,
    in the seat's own record, for audit.
    """
    from quirework.provenance import show
    from quirework.runs.engine import ACCEPTABLE_FLAGS, Run, flag_code

    unknown = set(accept) - set(ACCEPTABLE_FLAGS)
    if unknown:
        raise ValueError(f"no such flag code: {', '.join(sorted(unknown))}")
    run = Run(record)
    run.verify()
    corpus = trail.Corpus()
    root = ledger()
    tag = run.plan["id"][:8]
    report = []
    for name, seat in run.seats.items():
        meta = (seat.get("metadata") or {}).get("trail_read")
        if not meta:
            continue
        row = {
            "seat": name,
            "read": 0,
            "unread": [],
            "dropped": [],
            "answered": False,
            "held": [],
            "accepted": [],
            "model": None,
            "turns": 0,
            "flagged": 0,
            "empty": [],
            "sections": [],
            "sessions": [],
        }
        report.append(row)
        done = run.state(name)["done"].get("read")
        if not done:
            continue
        row["answered"] = True
        receipt = done["receipt"]
        row["model"] = receipt.get("model")
        open_flags, row["accepted"] = run.red_flags(name)
        for flag in list(open_flags):
            code = flag_code(flag)
            if code in accept and ACCEPTABLE_FLAGS[code][1](seat["policy"]):
                open_flags.remove(flag)
                row["accepted"].append(flag)
        if open_flags:
            row["held"] = open_flags
            continue
        answer = (run.record(name, "read") / "answers" / "answer.md").read_text(
            encoding="utf-8"
        )
        reads, dropped = parse_answer(answer, meta["sessions"], corpus)
        captured = corpus.captured()
        for sid, read in reads.items():
            # What the operator checks a quiet seat against: the reader's own
            # account of each session beside the title the reader never saw.
            row["sessions"].append(
                {
                    "session": sid,
                    "turns": read["turns"],
                    "flagged": len(read["candidates"]),
                    "about": read["about"],
                    "title": show.session_title(*captured[sid]),
                }
            )
            row["turns"] += read["turns"]
            row["flagged"] += len(read["candidates"])
            if not read["candidates"]:
                row["empty"].append((sid, read["turns"]))
            row["sections"] += [
                (s["slug"], s["confidence"])
                for c in read["candidates"]
                for s in c["sections"]
            ]
            target = root / "reads" / sid / f"{tag}-{name}.json"
            if target.exists():
                continue
            read.update(
                packet_version=meta["packet_version"],
                slugs=meta["slugs"],
                run=run.plan["id"],
                seat=name,
                requested=seat["config"].get("model"),
                model=receipt.get("model"),
                effort=seat["config"].get("effort"),
                red_flags=receipt.get("red_flags", []),
                imported=date.today().isoformat(),
            )
            _write_private(target, json.dumps(read, indent=2) + "\n")
        row["read"] = len(reads)
        row["unread"] = [sid for sid in meta["sessions"] if sid not in reads]
        row["dropped"] = dropped
        seat_record = {
            "run": run.plan["id"],
            "seat": name,
            "model": receipt.get("model"),
            "sessions": meta["sessions"],
            "unread": row["unread"],
            "dropped": dropped,
        }
        _write_private(
            root / "seats" / f"{tag}-{name}.json",
            json.dumps(seat_record, indent=2) + "\n",
        )
    return report


def import_lines(report):
    """The import's report as printable lines: each seat, then the whole pass.

    The pass lines are the numbers a ledger commit reports, so the operator
    copies them rather than recomputing them.
    """
    lines = []
    for row in report:
        name = row["seat"]
        if not row["answered"]:
            lines.append(f"  {name}: not answered yet")
            continue
        if row["held"]:
            lines.append(
                f"  {name}: HELD, nothing recorded; a red flag its plan did not accept:"
            )
            lines += [f"    ! {flag}" for flag in row["held"]]
            continue
        lines.append(
            f"  {name}: {row['read']} session(s) read, "
            f"{row['flagged']} of {row['turns']} author turn(s) flagged"
        )
        for sid in row["unread"]:
            lines.append(f"    {sid[:8]}: no done line, so not recorded as read")
        for drop in row["dropped"]:
            lines.append(f"    dropped ({drop['why']}): {drop['line'][:100]}")
        if (
            row["turns"] >= DILIGENCE_TURNS
            and row["flagged"] < DILIGENCE_RATE * row["turns"]
        ):
            lines.append(
                f"    ! flagged under {DILIGENCE_RATE:.0%} of its turns: check that "
                "each account below matches its session before trusting the pass"
            )
            for s in row["sessions"]:
                lines += [
                    f"    {s['session'][:8]} · {s['turns']} turn(s), "
                    f"{s['flagged']} flagged · {s['title']}",
                    f"      about: {s['about'] or '(no about line)'}",
                ]
    rows = [r for r in report if r["answered"] and not r["held"]]
    if not rows:
        return lines
    handed = sum(r["read"] + len(r["unread"]) for r in rows)
    models = sorted({r["model"] or "unknown" for r in rows})
    sections = [s for r in rows for s in r["sections"]]
    confidence = {c: sum(1 for _, x in sections if x == c) for c in CONFIDENCE}
    empty = [e for r in rows for e in r["empty"]]
    held = [r for r in report if r["held"]]
    lines += [
        "pass:",
        f"  {sum(r['read'] for r in rows)} of {handed} session(s) read in "
        f"{len(rows)} seat(s) on {', '.join(models)}",
        f"  red flags: {sum(len(r['accepted']) for r in rows)} accepted, "
        f"{len(held)} seat(s) held",
        f"  {len(sections):,} section flag(s) on {sum(r['flagged'] for r in rows):,} "
        f"of {sum(r['turns'] for r in rows):,} author turn(s), "
        f"{len({slug for slug, _ in sections})} section(s) "
        f"({', '.join(f'{n} {c}' for c, n in confidence.items())})",
        f"  {sum(len(r['dropped']) for r in rows)} flag(s) dropped, "
        f"{len(empty)} session(s) with no flag",
    ]
    bare = sum(1 for r in rows for s in r["sessions"] if not s["about"])
    if bare:
        lines.append(f"  {bare} done line(s) without an about sentence")
    return lines


# --------------------------------------------------------------------------- #
# rulings
# --------------------------------------------------------------------------- #
def _rulings_path(slug):
    return ledger() / "rulings" / f"{slug}.json"


def rejections(slug):
    path = _rulings_path(slug)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("rejected", [])


def _same(entry, turn):
    """Whether a stored ``{session, turn, logged}`` names this corpus turn."""
    if entry["session"] != turn.session:
        return False
    if entry.get("logged") and turn.logged is not None:
        return entry["logged"] == _instant(turn.logged)
    return entry.get("turn") == turn.number


def _rejection(slug, corpus, *, session, turn, reason):
    """``(entry, target)`` for one rejection, checked but not yet written."""
    reason = " ".join(reason.split())
    if not reason:
        raise ValueError(
            "a rejection needs its reason, so no later pass reads it again"
        )
    sid, family, path, live = corpus.session(session)
    if live:
        raise LookupError(f"{sid[:8]} is not captured yet; capture it first")
    target = next(
        (t for t in corpus.turns(sid, family, path) if t.number == turn), None
    )
    if target is None:
        raise LookupError(f"{sid[:8]} has no author turn {turn}")
    entry = {
        "session": sid,
        "turn": turn,
        "logged": _instant(target.logged),
        "reason": reason,
        "date": date.today().isoformat(),
    }
    return entry, target


def _record_rejections(slug, pairs):
    """Write checked rejections to a section's ruling, all or none."""
    path = _rulings_path(slug)
    with trail._locked(path):
        rejected = rejections(slug)
        for i, (_, target) in enumerate(pairs):
            if any(_same(e, target) for e in rejected) or any(
                _same(e, target) for e, _ in pairs[:i]
            ):
                raise LookupError(
                    f"{target.short} turn {target.number} is already rejected "
                    f"for #{slug}"
                )
        rejected += [entry for entry, _ in pairs]
        body = {"slug": slug, "rejected": rejected}
        _write_private(path, json.dumps(body, indent=2) + "\n")


def reject(slug, *, session, turn, reason):
    """Record that a candidate does not belong on a section's trail, and why."""
    trail._known_slug(slug, trail._spec_titles())
    pair = _rejection(slug, trail.Corpus(), session=session, turn=turn, reason=reason)
    _record_rejections(slug, [pair])
    return pair[1]


def rule(slug, lines):
    """Apply a section's rulings from JSON lines; nothing is written unless all hold.

    Each line keeps a turn or rejects it::

        {"keep": "sharpen", "session": "ID", "turn": 4, "note": "…"}
        {"reject": "why it does not belong", "session": "ID", "turn": 5}

    A keep may carry an ``excerpt``. Every line is checked against the corpus,
    the trail and the ledger before anything is written, so a mistake on the
    fortieth line leaves the first thirty-nine unapplied rather than half a
    section ruled. Returns ``(kept, rejected)`` as lists of turns.
    """
    titles = trail._spec_titles()
    trail._known_slug(slug, titles)
    corpus = trail.Corpus()
    keeps, drops, errors = [], [], []
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError("not a JSON object")
            if ("keep" in item) == ("reject" in item):
                raise ValueError("give either keep (a kind) or reject (a reason)")
            if not isinstance(item.get("turn"), int) or not item.get("session"):
                raise ValueError("name the turn with session and an integer turn")
            if "keep" in item:
                keeps.append(
                    trail.prepare(
                        slug,
                        item["keep"],
                        session=item["session"],
                        turn=item["turn"],
                        note=item.get("note", ""),
                        excerpt=item.get("excerpt", ""),
                        corpus=corpus,
                        titles=titles,
                    )
                )
            else:
                drops.append(
                    _rejection(
                        slug,
                        corpus,
                        session=item["session"],
                        turn=item["turn"],
                        reason=str(item["reject"]),
                    )
                )
        except (ValueError, LookupError) as exc:
            errors.append(f"line {number}: {exc}")
    for _, target in keeps:
        if any(_same(entry, target) for entry, _ in drops):
            errors.append(
                f"{target.short} turn {target.number} is both kept and rejected"
            )
    for entry in rejections(slug):
        for _, target in keeps:
            if _same(entry, target):
                errors.append(
                    f"{target.short} turn {target.number} is already rejected "
                    f"for #{slug}"
                )
    if errors:
        raise LookupError(
            "nothing was written; fix these and run it again:\n  " + "\n  ".join(errors)
        )
    if keeps:
        trail.append(slug, [crumb for crumb, _ in keeps], titles)
    if drops:
        _record_rejections(slug, drops)
    return [t for _, t in keeps], [t for _, t in drops]


# --------------------------------------------------------------------------- #
# a section's worklist
# --------------------------------------------------------------------------- #
@dataclass
class Candidate:
    turn: trail.Turn
    flags: list = field(default_factory=list)  # (model, flag as stored)
    commits: list = field(default_factory=list)  # (sha8, subject, how)
    notes: list = field(default_factory=list)  # what the ruler should know first


def worklist(slug, *, commits=True, everything=False, also=()):
    """``(pending, on_trail, rejected, stale)`` candidates for one section.

    Candidates come from the readers' flags and, with ``commits``, from the
    commits that changed the section. ``also`` names neighbouring sections
    whose flags are candidates here too: a reader chooses one section for a
    turn that fits two, and the ruler of the other would never see it. One
    already on the trail or rejected is left out unless ``everything``. A flag
    whose turn the corpus no longer holds is ``stale``, reported rather than
    silently dropped.
    """
    titles = trail._spec_titles()
    trail._known_slug(slug, titles)
    for other in also:
        trail._known_slug(other, titles)
    wanted = {slug, *also}
    corpus = trail.Corpus()
    found, stale = {}, []

    def at(sid, number=None, logged=None):
        captured = corpus.captured().get(sid)
        if captured is None:
            return None
        for t in corpus.turns(sid, *captured):
            if logged and t.logged is not None:
                if _instant(t.logged) == logged:
                    return t
            elif t.number == number:
                return t
        return None

    for read in _reads():
        for entry in read["candidates"]:
            for item in entry["sections"]:
                if item["slug"] not in wanted:
                    continue
                turn = at(read["session"], entry["turn"], entry.get("logged"))
                if turn is None:
                    stale.append((read["session"], entry["turn"]))
                    continue
                c = found.setdefault((turn.session, turn.number), Candidate(turn))
                c.flags.append(
                    (read.get("model") or read.get("requested") or "?", item)
                )
    if commits:
        leads, _ = trail.suggest_leads(slug, corpus)
        for lead in leads:
            for sid, _, before in lead.sessions:
                for t in before:
                    c = found.setdefault((t.session, t.number), Candidate(t))
                    c.commits.append(
                        (lead.sha[:8], lead.subject, "made in this session")
                    )
            for quote, hits, _ in lead.quotes:
                for t in hits:
                    c = found.setdefault((t.session, t.number), Candidate(t))
                    c.commits.append(
                        (lead.sha[:8], lead.subject, f"quoted “{quote[:60]}”")
                    )

    _annotate(found.values(), corpus)
    crumbs = [c for s, c in trail.load().crumbs() if s.slug == slug]
    rejected = rejections(slug)
    pending, on_trail, dropped = [], [], []
    for c in found.values():
        if any(trail._points_at(crumb, c.turn) for crumb in crumbs):
            on_trail.append(c)
        elif any(_same(e, c.turn) for e in rejected):
            dropped.append(c)
        else:
            pending.append(c)

    days = {}

    def order(c):
        return (_when(c.turn, corpus, days), c.turn.session, c.turn.number)

    if everything:
        pending = pending + on_trail + dropped
    return sorted(pending, key=order), on_trail, dropped, stale


def _day(turn, corpus, days):
    """The day a timeless turn's session is dated, cached per session."""
    if turn.session not in days:
        from quirework.provenance import show

        family, path = corpus.captured().get(turn.session, (turn.family, turn.path))
        days[turn.session] = show.session_date(family, path)
    return days[turn.session]


def _when(turn, corpus, days):
    """Where a turn sorts: its logged time, else its session's date, else first."""
    if turn.logged is not None:
        return turn.logged
    day = _day(turn, corpus, days)
    if day is not None:
        return datetime.combine(day, datetime.min.time(), timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def _annotate(candidates, corpus):
    """Note the two copies a ruler should see before ruling on either.

    A turn sent twice: the author resent the same words, usually because the
    first copy drew no reply, and only the copy that was answered is the one
    the session went on from. A turn captured twice: a resumed or forked
    session repeats the history it inherited under a new id, logged at the
    same instant with the same words.
    """
    from quirework.provenance import show

    candidates = list(candidates)
    replies = {}
    for c in candidates:
        t = c.turn
        if (t.family, t.path) not in replies:
            rows = show.exchanges(t.family, t.path)
            replies[(t.family, t.path)] = {
                n: (text, reply) for n, _, text, reply in rows
            }
        rows = replies[(t.family, t.path)]
        mine = trail._squash(t.text)
        for other in (t.number - 1, t.number + 1):
            if other in rows and trail._squash(rows[other][0]) == mine:
                after = rows.get(t.number + 1)
                if after is None:
                    reply = "the session ends before its reply"
                elif after[1].strip():
                    reply = "this copy drew a reply"
                else:
                    reply = "this copy drew none"
                c.notes.append(f"same words as Human {other}; {reply}")
    twins = {}
    for c in candidates:
        if c.turn.logged is not None:
            key = (c.turn.logged, trail._squash(c.turn.text))
            twins.setdefault(key, []).append(c)
    for group in twins.values():
        if len({c.turn.session for c in group}) > 1:
            for c in group:
                others = ", ".join(
                    f"{o.turn.short} Human {o.turn.number}"
                    for o in group
                    if o.turn.session != c.turn.session
                )
                c.notes.append(f"the same turn is captured again as {others}")


def _worklist_lines(slug, *, commits, everything, utc, also=()):
    titles = trail._spec_titles()
    pending, on_trail, rejected, stale = worklist(
        slug, commits=commits, everything=everything, also=also
    )
    zone = timezone.utc if utc else None
    lines = [
        f"#{slug} — {titles[slug]}",
        (
            f"{len(pending)} candidate(s) to rule on, oldest first; "
            f"{len(on_trail)} on the trail, {len(rejected)} rejected. These are "
            f"leads, not breadcrumbs: read each turn before ruling on it."
        ),
        (
            f"  keep:   quirework book trail add {slug} --kind KIND "
            f'--session ID --turn N --note "…"'
        ),
        (
            f"  reject: quirework book trail backfill reject {slug} "
            f'--session ID --turn N --reason "…"'
        ),
        f"  many:   quirework book trail backfill rule {slug} FILE",
    ]
    if also:
        lines.append(
            "  with the flags of "
            + ", ".join(f"#{s}" for s in also)
            + ", marked where a reader named that section instead"
        )
    if stale:
        lines.append(
            f"  {len(stale)} flag(s) name a turn the corpus no longer holds: "
            + ", ".join(f"{s[:8]} turn {n}" for s, n in stale[:5])
        )
    corpus, days = trail.Corpus(), {}
    for c in pending:
        t = c.turn
        if t.logged:
            when = f"{t.logged.astimezone(zone):%Y-%m-%d %H:%M}"
        else:
            day = _day(t, corpus, days)
            when = f"{day:%Y-%m-%d} (day only)" if day else "undated"
        lines += ["", f"{when}  {t.harness} {t.short} Human {t.number}"]
        for model, item in c.flags:
            where = f" [#{item['slug']}]" if item["slug"] != slug else ""
            lines.append(
                f"  {model}{where}: {item['kind']} ({item['confidence']}) "
                f"{item['reason']}"
            )
        for sha, subject, how in c.commits:
            lines.append(f"  commit {sha} {trail._opening(subject, 50)}: {how}")
        for note in c.notes:
            lines.append(f"  note   {note}")
        lines.append(f"  opens  {trail._opening(t.text, 110)}")
        lines.append(f"  read   quirework provenance show {t.short} --turn {t.number}")
    return lines


# --------------------------------------------------------------------------- #
# coverage
# --------------------------------------------------------------------------- #
def status():
    """How far the backfill has come: sessions read, and sections left to rule."""
    from quirework.provenance import scope as SCOPE
    from quirework.provenance import show

    corpus = trail.Corpus()
    kept = _kept(corpus)
    slugs = [s for s, _, _ in notes.spec_sections() if s]
    reads = _reads()
    current = {r["session"] for r in reads if _current(r)}
    older = {r["session"] for r in reads} - current
    unoffered = {
        slug
        for r in reads
        if _current(r)
        for slug in slugs
        if slug not in r.get("slugs", [])
    }
    rows = {}
    for sid, (family, path) in kept.items():
        if not show.author_turns(family, path):
            continue
        key = (trail._HARNESS_OF[family], SCOPE.scope_of_id(sid))
        row = rows.setdefault(key, [0, 0, 0])
        row[0] += 1
        row[1] += sid in current
        row[2] += sid in older
    lines = ["sessions with author turns, by harness and scope:"]
    for (harness, scope), (total, done, old) in sorted(rows.items()):
        extra = f", {old} read only from an older packet" if old else ""
        lines.append(f"  {harness:<12} {scope:<7} {done:>4} of {total:>4} read{extra}")
    if unoffered:
        lines.append(
            f"  {len(unoffered)} section(s) are newer than some reads, which "
            f"were not offered them: " + ", ".join(f"#{s}" for s in sorted(unoffered))
        )
    flagged = {}
    for read in reads:
        for entry in read["candidates"]:
            for item in entry["sections"]:
                flagged.setdefault(item["slug"], set()).add(
                    (read["session"], entry.get("logged") or entry["turn"])
                )
    held = trail.counts()
    lines += [
        "",
        "sections: turns the readers flagged, breadcrumbs, rejections",
        "(a section's worklist adds the turns its commits lead to):",
    ]
    for slug in slugs:
        n = len(flagged.get(slug, ()))
        if n or held.get(slug) or rejections(slug):
            lines.append(
                f"  #{slug:<34} {n:>4} flagged  {held.get(slug, 0):>3} on the "
                f"trail  {len(rejections(slug)):>3} rejected"
            )
    return lines


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #
USAGE = """\
backfilling a trail from the corpus, in three steps:

  1. hand every session to a cheap reader, once:
     quirework book trail backfill plan --harness H --model M --effort E [--out DIR]
     then the prepare, start and import commands it prints; import holds a
     seat whose receipt carries a red flag its plan did not accept
  2. rule on one section at a time, with a strong reader:
     quirework book trail backfill SLUG          the section's candidates, oldest first
         --also OTHER                            with the turns readers flagged for
                                                 a neighbouring section instead
     quirework book trail add SLUG ...           keep one as a breadcrumb
     quirework book trail backfill reject SLUG --session ID --turn N --reason TEXT
     quirework book trail backfill rule SLUG FILE
                                                 keep and reject many at once, from
                                                 JSON lines; all or nothing
  3. see how far it has come:
     quirework book trail backfill status

  quirework book trail backfill packet ID...   what a reader is handed
  quirework book trail backfill menu           the sections a reader names
"""


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    command, rest = argv[0], argv[1:]
    try:
        if command == "packet":
            corpus = trail.Corpus()
            for prefix in rest:
                sid, family, path, _ = corpus.session(prefix)
                print(packet(sid, family, path))
            return 0
        if command == "menu":
            print(menu(), end="")
            return 0
        if command == "plan":
            ap = argparse.ArgumentParser(prog="quirework book trail backfill plan")
            ap.add_argument(
                "--out",
                help="a new directory for packets (default: RUN/packets in a "
                "new run directory in the project's private state)",
            )
            ap.add_argument(
                "--harness", required=True, choices=("claude", "codex", "agy")
            )
            ap.add_argument("--model", required=True, help="a model family or version")
            ap.add_argument("--effort", required=True)
            ap.add_argument(
                "--tier", default="cheap", choices=("cheap", "standard", "premium")
            )
            ap.add_argument("--session", action="append", dest="sessions", metavar="ID")
            ap.add_argument("--scope", help="only sessions of this provenance scope")
            ap.add_argument("--limit", type=int, help="at most N sessions")
            ap.add_argument(
                "--budget",
                type=int,
                default=BATCH_TOKENS,
                help="estimated tokens of packets per seat",
            )
            ap.add_argument(
                "--material",
                action="append",
                default=[],
                metavar="FILE",
                help="book guidance every reader gets, such as retired names",
            )
            ap.add_argument(
                "--reread",
                action="store_true",
                help="include sessions a current read already covers",
            )
            ap.add_argument(
                "--external",
                action="store_true",
                help="seats whose answers are imported by hand",
            )
            a = ap.parse_args(rest)
            done = plan(
                a.out,
                harness=a.harness,
                model=a.model,
                effort=a.effort,
                tier=a.tier,
                sessions=a.sessions,
                scope=a.scope,
                limit=a.limit,
                budget=a.budget,
                materials=a.material,
                reread=a.reread,
                external=a.external,
            )
            print("\n".join(plan_lines(done)))
            return 0
        if command == "import":
            ap = argparse.ArgumentParser(prog="quirework book trail backfill import")
            ap.add_argument("record")
            ap.add_argument(
                "--accept",
                action="append",
                default=[],
                metavar="CODE",
                help="a red flag to accept for a record whose plan predates "
                "declaring it (session-bus)",
            )
            a = ap.parse_args(rest)
            report = import_run(a.record, accept=a.accept)
            print("\n".join(import_lines(report)))
            return 1 if any(row["held"] for row in report) else 0
        if command == "reject":
            ap = argparse.ArgumentParser(prog="quirework book trail backfill reject")
            ap.add_argument("slug")
            ap.add_argument("--session", required=True, metavar="ID")
            ap.add_argument("--turn", required=True, type=int, metavar="N")
            ap.add_argument("--reason", required=True)
            a = ap.parse_args(rest)
            t = reject(a.slug, session=a.session, turn=a.turn, reason=a.reason)
            print(f"rejected for #{a.slug}: {t.harness} {t.short} Human {t.number}")
            return 0
        if command == "rule":
            ap = argparse.ArgumentParser(
                prog="quirework book trail backfill rule",
                description="Keep and reject a section's candidates from a file of "
                'JSON lines: {"keep": KIND, "session": ID, "turn": N, "note": '
                '"…"} or {"reject": REASON, "session": ID, "turn": N}. Every '
                "line is checked before any is written.",
            )
            ap.add_argument("slug")
            ap.add_argument("file", help="the rulings, or - for standard input")
            a = ap.parse_args(rest)
            source = (
                sys.stdin
                if a.file == "-"
                else open(Path(a.file).expanduser(), encoding="utf-8")
            )
            with source:
                kept, dropped = rule(a.slug, source.read().splitlines())
            print(f"#{a.slug}: {len(kept)} kept on the trail, {len(dropped)} rejected")
            return 0
        if command == "status":
            print("\n".join(status()))
            return 0
        ap = argparse.ArgumentParser(prog="quirework book trail backfill")
        ap.add_argument("slug")
        ap.add_argument("--all", action="store_true", help="include ruled candidates")
        ap.add_argument(
            "--no-commits",
            action="store_true",
            help="only the readers' flags, not the commit history",
        )
        ap.add_argument("--utc", action="store_true")
        ap.add_argument(
            "--also",
            action="append",
            default=[],
            metavar="SLUG",
            help="also the turns readers flagged for this neighbouring section",
        )
        a = ap.parse_args(argv)
        print(
            "\n".join(
                _worklist_lines(
                    a.slug,
                    commits=not a.no_commits,
                    everything=a.all,
                    utc=a.utc,
                    also=a.also,
                )
            )
        )
        print("\n" + trail._CONTEXT)
        return 0
    except (LookupError, ValueError) as exc:
        print(f"backfill: {exc}", file=sys.stderr)
        return 2
