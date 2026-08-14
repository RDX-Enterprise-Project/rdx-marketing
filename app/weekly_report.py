"""Weekly marketing report.

    python -m app.weekly_report --week-of 2026-08-17
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import List, Optional

from .clock import Clock, FrozenClock
from .config import load_config
from .db import create_all, make_engine
from .reports.weekly import build

UTC = dt.timezone.utc


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the RDX weekly marketing report")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--config-dir", default=None)
    parser.add_argument("--week-of", default=None, help="any date in the week, YYYY-MM-DD")
    parser.add_argument("--as-of", default=None, help="freeze the clock, ISO-8601 UTC")
    parser.add_argument("--out", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    load_config(Path(args.config_dir) if args.config_dir else None)
    engine = make_engine(args.database_url)
    create_all(engine)

    clock = (
        FrozenClock(dt.datetime.fromisoformat(args.as_of).replace(tzinfo=UTC))
        if args.as_of
        else Clock()
    )
    now = clock.now()
    anchor = dt.date.fromisoformat(args.week_of) if args.week_of else now.date()
    monday = anchor - dt.timedelta(days=anchor.weekday())

    with engine.connect() as conn:
        report = build(conn, monday, now)

    if args.out:
        Path(args.out).write_text(report.body_markdown, encoding="utf-8")
    if args.json:
        print(json.dumps(report.summary, indent=2))
    else:
        print(report.body_markdown)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
