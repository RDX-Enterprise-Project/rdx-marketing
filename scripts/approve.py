#!/usr/bin/env python3
"""The human end of the approval queue.

    python scripts/approve.py list
    python scripts/approve.py show MKT-2026-00042
    python scripts/approve.py approve MKT-2026-00042 --as william.farrell --note "checked"
    python scripts/approve.py reject  MKT-2026-00042 --as william.farrell --note "not yet"

An approver is always a named person. ``--as`` becomes ``human:<name>`` in the
audit trail; there is no way to record an approval without one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.approval import queue  # noqa: E402
from app.clock import Clock  # noqa: E402
from app.content.evidence import evidence_for  # noqa: E402
from app.content.store import load_variants  # noqa: E402
from app.db import create_all, make_engine  # noqa: E402


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="RDX marketing approval queue")
    parser.add_argument("--database-url", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list")

    show = sub.add_parser("show")
    show.add_argument("content_id")

    for name in ("approve", "reject"):
        decision = sub.add_parser(name)
        decision.add_argument("content_id")
        decision.add_argument("--as", dest="actor", required=True, help="your name")
        decision.add_argument("--note", default=None)

    args = parser.parse_args(argv)

    engine = make_engine(args.database_url)
    create_all(engine)
    now = Clock().now()

    if args.command == "list":
        with engine.connect() as conn:
            entries = queue.pending(conn)
        if not entries:
            print("Nothing is waiting on a decision.")
            return 0
        for entry in entries:
            item = entry.item
            print(
                "%s  %-28s %-22s %s"
                % (
                    entry.content_id,
                    item.pillar if item else "?",
                    item.topic if item else "?",
                    entry.requested_at.date().isoformat(),
                )
            )
        return 0

    if args.command == "show":
        with engine.connect() as conn:
            entries = {e.content_id: e for e in queue.pending(conn)}
            entry = entries.get(args.content_id)
            variants = load_variants(conn, args.content_id)
            evidence = evidence_for(conn, args.content_id)

        if entry is None:
            print("no pending approval for %s" % args.content_id, file=sys.stderr)
            return 1

        item = entry.item
        print("%s — %s / %s" % (item.content_id, item.pillar, item.topic))
        print("Classification: %s" % item.disclosure_class)
        print("Campaign: %s" % item.campaign)
        print()
        snapshot = entry.policy_snapshot
        advisory = snapshot.get("advisory_reasons") or []
        if advisory:
            print("Flagged for review:")
            for reason in advisory:
                print("  - %s" % reason.get("message"))
            print()
        if evidence:
            print("Evidence:")
            for record in evidence:
                print("  - [%s] %s — %s" % (record["kind"], record["claim"], record["summary"]))
            print()
        for variant in variants:
            print("--- %s (%s) ---" % (variant.platform, variant.post_type))
            print(variant.body)
            if variant.first_comment:
                print("[first comment] %s" % variant.first_comment)
            print()
        return 0

    approved = args.command == "approve"
    with engine.begin() as conn:
        item = queue.decide(
            conn,
            args.content_id,
            approved,
            "human:%s" % args.actor,
            now,
            rationale=args.note,
        )
    print("%s is now %s" % (item.content_id, item.approval_status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
