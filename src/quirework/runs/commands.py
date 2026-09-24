"""Command interface for generic model runs."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from quirework.providers import harness
from quirework.runs import engine
from quirework.runs.engine import WAIT_QUOTA_DEFAULT
from quirework.runs.storage import lock, read


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="quirework sop runs", description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser(
        "prepare", help="freeze a plan, its inputs and the executor into a record"
    )
    p.add_argument("plan")
    p.add_argument("record")
    p = sub.add_parser("start", help="run the selected seats' turns in order")
    p.add_argument("record")
    p.add_argument("seats", nargs="*")
    p.add_argument("--jobs", type=int, default=1)
    p.add_argument("--timeout", type=float)
    p.add_argument("--through")
    p.add_argument(
        "--wait-quota",
        type=float,
        default=WAIT_QUOTA_DEFAULT,
        help="seconds to wait out a provider quota refusal and "
        "resume the same session (0 to stop instead)",
    )
    for name, text in (
        ("inspect", "show each seat's model, policy, progress and red flags"),
        ("verify", "check that the frozen plan, inputs and evidence are unchanged"),
        ("collect", "bank what a submitted turn left, without buying a new answer"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("record")
        if name == "inspect":
            p.add_argument("--prompts", action="store_true")
    p = sub.add_parser("export", help="copy answers and curated receipts out")
    p.add_argument("record")
    p.add_argument("destination")
    p.add_argument(
        "--partial",
        action="store_true",
        help="release the seats that finished; the receipt records "
        "each seat's completeness",
    )
    p.add_argument(
        "--flat",
        action="store_true",
        help="one seat's files straight into the destination, outputs under "
        "their own names, with a receipt for that seat alone",
    )
    p = sub.add_parser(
        "retry", help="resubmit a turn after a provable rejection without an answer"
    )
    p.add_argument("record")
    p.add_argument("seat")
    p.add_argument("turn")
    p = sub.add_parser(
        "import", help="record an answer made outside the run, with its provenance"
    )
    p.add_argument("record")
    p.add_argument("seat")
    p.add_argument("turn")
    p.add_argument("answer")
    p.add_argument("--provenance", required=True)
    p = sub.add_parser("observe", help="report red flags and policy violations")
    p.add_argument("record")
    p = sub.add_parser(
        "check-coverage", help="check a ledger against the population it covers"
    )
    p.add_argument("population")
    p.add_argument("ledger")
    p.add_argument("--partial", action="store_true")
    p.add_argument("--status", action="append")
    p.add_argument("--require", action="append", default=[])
    p = sub.add_parser("capabilities", help="what each harness can provide")
    args = parser.parse_args(argv)
    try:
        if args.action == "observe":
            from quirework.runs.observe import observe

            result = observe(args.record)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return int(
                any(
                    s.get("red_flags") or s.get("policy_violations")
                    for s in result["seats"].values()
                )
            )
        if args.action == "check-coverage":
            from quirework.runs.coverage import check

            result = check(
                args.population,
                args.ledger,
                partial=args.partial,
                statuses=args.status,
                require=args.require,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result["valid"] else 1
        if args.action == "prepare":
            result = engine.prepare(args.plan, args.record)
        elif args.action == "capabilities":
            result = harness.CAPABILITIES
        else:
            args.record = str(Path(args.record).expanduser().resolve())
            forwarded = [args.action, args.record]
            if args.action == "start":
                forwarded += args.seats + ["--jobs", str(args.jobs)]
                if args.timeout is not None:
                    forwarded += ["--timeout", str(args.timeout)]
                if args.through:
                    forwarded += ["--through", args.through]
                # Only pass this on when the operator actually chose a value.
                # The bootstrap dispatches into whatever executor the record
                # froze, and a record frozen before this flag existed rejects
                # an argument it never had a parser for. Silence means the
                # frozen executor applies its own default.
                if args.wait_quota != WAIT_QUOTA_DEFAULT:
                    forwarded += ["--wait-quota", str(args.wait_quota)]
            elif args.action == "export":
                args.destination = str(Path(args.destination).expanduser().resolve())
                forwarded += [args.destination]
                if args.partial:
                    forwarded += ["--partial"]
                # Forwarded only when chosen: a record frozen before this flag
                # existed has no parser for it.
                if args.flat:
                    forwarded += ["--flat"]
            elif args.action == "inspect" and args.prompts:
                forwarded += ["--prompts"]
            elif args.action == "retry":
                forwarded += [args.seat, args.turn]
            elif args.action == "import":
                args.answer = str(Path(args.answer).expanduser().resolve())
                args.provenance = str(Path(args.provenance).expanduser().resolve())
                forwarded += [
                    args.seat,
                    args.turn,
                    args.answer,
                    "--provenance",
                    args.provenance,
                ]
            code = engine.frozen_dispatch(args.record, forwarded)
            if code is not None:
                return code
            run = engine.Run(args.record)
            if args.action == "inspect":
                result = run.inspect(prompts=args.prompts)
            elif args.action in ("retry", "import"):
                if args.seat not in run.seats or args.turn not in {
                    t["id"] for t in run.seats[args.seat]["turns"]
                }:
                    raise ValueError("unknown seat or turn")
                with (
                    lock(run.root / "run.lock"),
                    lock(run.root / "locks" / f"{args.seat}.lock"),
                ):
                    run.verify()
                    result = (
                        engine.retry(run, args.seat, args.turn)
                        if args.action == "retry"
                        else engine.import_answer(
                            run,
                            args.seat,
                            args.turn,
                            args.answer,
                            read(args.provenance),
                        )
                    )
            elif args.action == "verify":
                result = dict(integrity=run.verify(), **run.inspect())
            elif args.action == "export":
                result = run.export(
                    args.destination, partial=args.partial, flat=args.flat
                )
            elif args.action == "collect":
                result = {}
                with lock(run.root / "run.lock"):
                    for name, spec in run.seats.items():
                        state = run.state(name)
                        if state["pending"]:
                            index = next(
                                i
                                for i, t in enumerate(spec["turns"])
                                if t["id"] == state["pending"]["turn"]
                            )
                            with lock(run.root / "locks" / f"{name}.lock"):
                                result[name] = run.collect_turn(name, index)
            else:
                result = run.run(
                    args.seats,
                    args.jobs,
                    args.timeout,
                    args.through,
                    wait_quota=(args.wait_quota or None),
                )
                print(json.dumps(result, indent=2))
                return int(
                    any(v not in ("completed", "paused") for v in result.values())
                )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"run: {exc}", file=sys.stderr)
        return 2
