"""The marketing run, end to end.

Order: intake -> variants -> policy -> approval -> calendar -> publish -> retry
-> metrics.

The policy engine sits between drafting and publishing and is consulted on every
attempt, including retries. Nothing skips it, and no amount of scheduling
pressure moves an item past it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.engine import Engine

from .ai.drafting import DraftingService, apply_draft
from .approval import queue
from .calendar import planner
from .calendar.planner import Candidate, Slot
from .clock import Clock
from .config import AppConfig
from .content.evidence import evidence_for
from .content.item import (
    APPROVED,
    AUTO_APPROVED,
    LIFECYCLE_BLOCKED,
    MEDIA_NONE,
    ContentItem,
    PlatformVariant,
)
from .content.store import load_variants, save_item, save_variants
from .db import transaction
from .media.library import MediaNotPublishable, assert_publishable, record_use
from .models import content_items, runs
from .platforms.adapters import build_all
from .policy import engine as policy_engine
from .publisher.base import SocialPublisher
from .publisher.service import publish_variant, retryable_publications
from .metrics.collector import collect as collect_metrics

KIND_DAILY = "DAILY"
RUN_OK = "OK"
RUN_PARTIAL = "PARTIAL"
RUN_FAILED = "FAILED"


@dataclass
class ContentOutcome:
    item: ContentItem
    variants: List[PlatformVariant] = field(default_factory=list)
    decision: Optional[policy_engine.PolicyDecision] = None
    queued_for_approval: bool = False
    auto_approved: bool = False
    blocked: bool = False


@dataclass
class MarketingRunResult:
    run_id: str
    business_date: dt.date
    status: str
    outcomes: List[ContentOutcome] = field(default_factory=list)
    plan: Optional[planner.PlanResult] = None
    published: int = 0
    failed: int = 0
    blocked: int = 0
    retried: int = 0
    metrics_collected: int = 0
    ai_calls: int = 0


def run_daily(
    engine: Engine,
    config: AppConfig,
    clock: Clock,
    publisher: SocialPublisher,
    drafting: Optional[DraftingService] = None,
    business_date: Optional[dt.date] = None,
    collect_metrics_too: bool = True,
) -> MarketingRunResult:
    now = clock.now()
    day = business_date or now.date()
    run_id = "%s-%s-%s" % (KIND_DAILY.lower(), day.isoformat(), uuid.uuid4().hex[:10])
    service = drafting or DraftingService(config)

    result = MarketingRunResult(run_id=run_id, business_date=day, status=RUN_OK)

    with transaction(engine) as conn:
        conn.execute(
            runs.insert().values(
                run_id=run_id,
                kind=KIND_DAILY,
                started_at=now,
                business_date=day,
                status="RUNNING",
                policy_version=config.policy_version,
            )
        )

        outcomes = _prepare_content(conn, config, service, now)
        result.outcomes = outcomes
        result.blocked = len([o for o in outcomes if o.blocked])

        candidates = [
            Candidate(item=o.item, variants={v.platform: v for v in o.variants})
            for o in outcomes
            if not o.blocked
        ]

        monday = day - dt.timedelta(days=day.weekday())
        slots = planner.plan_week(config, monday)
        plan = planner.fill_slots(conn, config, slots, candidates, now)
        planner.persist(conn, plan.slots, now)
        result.plan = plan

        published, failed = _publish_due(
            conn, config, publisher, plan.slots, outcomes, day, now
        )
        result.published, result.failed = published, failed

        result.retried = _retry_failed(conn, config, publisher, now)

        if collect_metrics_too:
            collection = collect_metrics(conn, publisher, now)
            result.metrics_collected = collection.collected

        result.ai_calls = len(service.calls)
        status = RUN_PARTIAL if (result.failed or result.blocked) else RUN_OK

        conn.execute(
            runs.update()
            .where(runs.c.run_id == run_id)
            .values(
                finished_at=clock.now(),
                status=status,
                slots_considered=len(plan.slots),
                slots_filled=len(plan.filled),
                slots_skipped=len(plan.skipped),
                published=result.published,
                blocked=result.blocked,
                failed=result.failed,
                ai_calls=result.ai_calls,
                ai_cost_usd=service.budget.spent,
                notes={"retried": result.retried, "metrics": result.metrics_collected},
            )
        )
        result.status = status

    return result


# --------------------------------------------------------------------------- #


def _platforms_for(config: AppConfig, item: ContentItem) -> List[str]:
    """Which networks this item can actually satisfy.

    Instagram is visual-first, so an item carrying no media requirement simply
    does not get an Instagram variant. Generating one and then blocking it on
    missing media would report a content problem where there is only an absent
    channel, and would take the whole item down with it.
    """
    names: List[str] = []
    for name in config.platforms.names:
        platform_cfg = config.platforms.platform(name)
        if platform_cfg.get("requires_media") and item.media_requirement == MEDIA_NONE:
            continue
        names.append(name)
    return names


def _prepare_content(
    conn, config: AppConfig, service: DraftingService, now: dt.datetime
) -> List[ContentOutcome]:
    rows = (
        conn.execute(
            select(content_items).where(
                content_items.c.approval_status.in_(("DRAFT", "PENDING_APPROVAL", "APPROVED"))
            )
        )
        .mappings()
        .all()
    )

    outcomes: List[ContentOutcome] = []
    for row in rows:
        item = ContentItem.from_row(dict(row))
        variants = load_variants(conn, item.content_id)

        if not variants:
            try:
                variants = build_all(config, item, _platforms_for(config, item))
            except Exception:  # noqa: BLE001 - a style failure is not a run failure
                variants = []
            for variant in variants:
                improved = service.improve(conn, item, variant, "draft", now)
                if improved:
                    apply_draft(variant, improved, service.model_for("draft"))
            if variants:
                save_variants(conn, variants, now)

        linked = evidence_for(conn, item.content_id)
        decision = policy_engine.evaluate(config, item, variants, linked)
        policy_engine.persist(conn, decision, now)

        outcome = ContentOutcome(item=item, variants=variants, decision=decision)

        if decision.blocked:
            outcome.blocked = True
            item.lifecycle_status = LIFECYCLE_BLOCKED
            save_item(conn, item, now)
            outcomes.append(outcome)
            continue

        if decision.requires_approval and item.approval_status != APPROVED:
            queue.request_approval(conn, item, decision, now)
            outcome.queued_for_approval = True
        elif (
            not decision.requires_approval
            and item.approval_requirement == AUTO_APPROVED
            and item.approval_status != APPROVED
        ):
            queue.auto_approve(
                conn, item, "auto_eligible_pillar:%s" % item.pillar, now, decision
            )
            outcome.auto_approved = True

        outcomes.append(outcome)
    return outcomes


def _publish_due(
    conn,
    config: AppConfig,
    publisher: SocialPublisher,
    slots: Sequence[Slot],
    outcomes: Sequence[ContentOutcome],
    day: dt.date,
    now: dt.datetime,
):
    by_content = {o.item.content_id: o for o in outcomes}
    published = failed = 0

    for slot in slots:
        if slot.status != planner.STATUS_FILLED or slot.content_id is None:
            continue
        if slot.slot_date != day:
            continue

        outcome = by_content.get(slot.content_id)
        if outcome is None or outcome.decision is None:
            continue

        variant = next(
            (v for v in outcome.variants if v.platform == slot.platform), None
        )
        if variant is None:
            continue

        if variant.media_ids:
            try:
                assert_publishable(conn, variant.media_ids, day)
            except MediaNotPublishable:
                failed += 1
                continue

        # Re-evaluate: the item may have been approved or rejected since the
        # decision recorded earlier in this run.
        from .content.store import load_item

        current = load_item(conn, outcome.item.content_id) or outcome.item
        decision = policy_engine.evaluate(
            config, current, outcome.variants, evidence_for(conn, current.content_id)
        )

        publish_result = publish_variant(
            conn,
            config,
            publisher,
            current,
            variant,
            decision,
            now,
            scheduled_for=slot.post_at,
        )
        if publish_result.ok:
            published += 1
            if variant.media_ids:
                record_use(conn, variant.media_ids, now)
        elif publish_result.status != "BLOCKED":
            failed += 1

    return published, failed


def _retry_failed(conn, config: AppConfig, publisher: SocialPublisher, now: dt.datetime) -> int:
    from .content.store import load_item
    from .models import platform_variants

    retried = 0
    for row in retryable_publications(conn, config, now):
        item = load_item(conn, row["content_id"])
        if item is None:
            continue
        variant_row = (
            conn.execute(
                select(platform_variants).where(
                    platform_variants.c.variant_id == row["variant_id"]
                )
            )
            .mappings()
            .first()
        )
        if variant_row is None:
            continue
        variant = PlatformVariant.from_row(dict(variant_row))
        variants = load_variants(conn, item.content_id)
        decision = policy_engine.evaluate(
            config, item, variants, evidence_for(conn, item.content_id)
        )
        publish_variant(
            conn,
            config,
            publisher,
            item,
            variant,
            decision,
            now,
            scheduled_for=row.get("scheduled_for"),
        )
        retried += 1
    return retried
