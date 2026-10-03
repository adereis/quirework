"""Accept captured sessions into the corpus, or curate them out of it.

The two decisions an ingest procedure makes about each pending capture, after
reading it. Both write the ledger, never the work: ``accept`` lists the
session's files in ``accepted.txt``, and ``reject`` lists the session on
``removed-sessions.txt`` with the reason, so capture never brings it back.
"""

import argparse

from quirework.provenance import store
from quirework.runs.storage import shown


def accept_main(argv=None):
    parser = argparse.ArgumentParser(
        prog="quirework provenance accept",
        description="Accept pending sessions into the corpus this ledger lists.",
    )
    parser.add_argument("sessions", nargs="*", metavar="ID", help="session id prefix")
    parser.add_argument(
        "--all", action="store_true", help="accept every pending session"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="accept a session that may still be running, as captured so far",
    )
    args = parser.parse_args(argv)
    if bool(args.sessions) == args.all:
        parser.error("name the sessions to accept, or pass --all")
    done, skipped = store.accept(args.sessions, everything=args.all, live=args.live)
    for family, unit, version, grown in done:
        verb = "updated" if grown else "accepted"
        print(f"  {verb} {store.KIND_OF[family]} {unit} as {family}/{version}")
    for family, unit in skipped:
        print(f"  skipped {store.KIND_OF[family]} {unit}: it may still be running")
    if done:
        print(
            f"{len(done)} session(s) accepted; rebuild the catalog and commit "
            f"{shown(store.ledger() / store.MANIFEST)} with it"
        )
    else:
        print("nothing accepted")
    return 0


def reject_main(argv=None):
    parser = argparse.ArgumentParser(
        prog="quirework provenance reject",
        description="Curate sessions out of the corpus and keep them out.",
    )
    parser.add_argument("sessions", nargs="+", metavar="ID", help="session id prefix")
    parser.add_argument("--reason", required=True, help="why, kept on the removed list")
    args = parser.parse_args(argv)
    for family, unit in store.reject(args.sessions, args.reason):
        print(f"  removed {store.KIND_OF[family]} {unit}")
    print(
        f"listed on {shown(store.ledger() / store.REMOVED)}; capture will not "
        "bring them back"
    )
    return 0


def main(argv):
    action, *rest = argv
    return {"accept": accept_main, "reject": reject_main}[action](rest)
