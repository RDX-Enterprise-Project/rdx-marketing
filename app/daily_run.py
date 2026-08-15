"""Executable boundary for the marketing run.

    python -m app.daily_run

Prepares content, evaluates policy, routes what needs a human, fills the
calendar, publishes what is genuinely cleared, retries what failed, and collects
metrics. No Claude session involved.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import List, Optional

from .ai.drafting import DraftingService
from .clock import Clock, FrozenClock
from .config import AppConfig, load_config
from .db import create_all, make_engine
from .pipeline import MarketingRunResult, run_daily
from .publisher.base import NullPublisher, SocialPublisher
from .publisher.buffer import BufferConfig, BufferPublisher

UTC = dt.timezone.utc


def build_publisher(config: AppConfig) -> SocialPublisher:
    publisher_cfg = config.platforms.publisher
    if not publisher_cfg.get("enabled"):
        return NullPublisher()

    provider = str(publisher_cfg.get("provider", "buffer"))
    if provider != "buffer":
        raise ValueError(
            "no adapter for publisher provider %r; implement SocialPublisher for it" % provider
        )

    from .collectors_http import RequestsHttpClient

    token = config.env_value(publisher_cfg.get("token_env", "BUFFER_ACCESS_TOKEN"), "") or ""
    # Keyed "platform:role". A role with no configured id is simply absent, and
    # the publisher refuses rather than substituting a different role.
    channels = {}
    for key, env_name in config.platforms.channel_env_vars().items():
        value = config.env_value(env_name, "") or ""
        if value:
            channels[key] = value

    from .publisher.buffer import API_BASE, SCHEDULING_AUTOMATIC

    return BufferPublisher(
        RequestsHttpClient(),
        BufferConfig(
            api_base=str(publisher_cfg.get("api_base", API_BASE)),
            token=token,
            channels=channels,
            timeout_seconds=int(publisher_cfg.get("request_timeout_seconds", 45)),
            default_create_as_draft=bool(publisher_cfg.get("default_create_as_draft", True)),
            scheduling_type=str(publisher_cfg.get("scheduling_type", SCHEDULING_AUTOMATIC)),
        ),
    )


def execute(args: argparse.Namespace) -> MarketingRunResult:
    config = load_config(Path(args.config_dir) if args.config_dir else None)
    engine = make_engine(args.database_url)
    create_all(engine)

    clock: Clock
    if args.as_of:
        clock = FrozenClock(dt.datetime.fromisoformat(args.as_of).replace(tzinfo=UTC))
    else:
        clock = Clock()

    business_date = dt.date.fromisoformat(args.date) if args.date else clock.now().date()

    return run_daily(
        engine=engine,
        config=config,
        clock=clock,
        publisher=build_publisher(config),
        drafting=DraftingService(config),
        business_date=business_date,
        collect_metrics_too=not args.no_metrics,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the RDX marketing engine")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--config-dir", default=None)
    parser.add_argument("--date", default=None, help="business date override, YYYY-MM-DD")
    parser.add_argument("--as-of", default=None, help="freeze the clock, ISO-8601 UTC")
    parser.add_argument("--no-metrics", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = execute(args)

    summary = {
        "run_id": result.run_id,
        "business_date": result.business_date.isoformat(),
        "status": result.status,
        "slots_planned": len(result.plan.slots) if result.plan else 0,
        "slots_filled": len(result.plan.filled) if result.plan else 0,
        "slots_skipped": len(result.plan.skipped) if result.plan else 0,
        "published": result.published,
        "failed": result.failed,
        "blocked": result.blocked,
        "retried": result.retried,
        "metrics_collected": result.metrics_collected,
        "ai_calls": result.ai_calls,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(
            "run %s: %s — %d published, %d blocked, %d failed, %d AI call(s)"
            % (
                result.run_id,
                result.status,
                result.published,
                result.blocked,
                result.failed,
                result.ai_calls,
            )
        )
    return 0 if result.status in ("OK", "PARTIAL") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
