"""Tests 13 and 15.

13. Metrics attach to the correct published post.
15. The scheduler does not generate low-value filler simply to meet quota.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.calendar import planner
from app.calendar.planner import STATUS_FILLED, STATUS_SKIPPED, Candidate
from app.content.item import APPROVED, DRAFT, PENDING_APPROVAL
from app.content.store import save_item, save_variants
from app.metrics.collector import collect, latest_metrics
from app.models import post_metrics
from app.platforms.adapters import build_all
from app.policy.engine import evaluate
from app.publisher.base import MetricsSample
from app.publisher.service import publish_variant

from .conftest import NOW, TODAY, make_item

MONDAY = dt.date(2026, 8, 17)


def _candidate(config, item, platforms=("linkedin", "facebook", "instagram")):
    variants = build_all(config, item, list(platforms))
    return Candidate(item=item, variants={v.platform: v for v in variants})


def test_scheduler_skips_rather_than_publishing_filler(engine, config):
    """Test 15."""
    slots = planner.plan_week(config, MONDAY)
    assert slots, "the cadence should produce slots"

    # Nothing approved at all.
    with engine.connect() as conn:
        empty = planner.fill_slots(conn, config, slots, [], NOW)

    assert empty.filled == []
    assert len(empty.skipped) == len(slots)
    assert all(s.skip_reason for s in empty.skipped), "every skip must say why"
    assert all(s.status == STATUS_SKIPPED for s in empty.skipped)


def test_unapproved_content_does_not_fill_a_slot(engine, config):
    slots = planner.plan_week(config, MONDAY)
    pending = make_item(approval_status=PENDING_APPROVAL)

    with engine.connect() as conn:
        plan = planner.fill_slots(conn, config, slots, [_candidate(config, pending)], NOW)

    assert plan.filled == []


def test_unclassified_content_does_not_fill_a_slot(engine, config):
    slots = planner.plan_week(config, MONDAY)
    unclassified = make_item(disclosure=None, approval_status=APPROVED)

    with engine.connect() as conn:
        plan = planner.fill_slots(conn, config, slots, [_candidate(config, unclassified)], NOW)

    assert plan.filled == []


def test_copy_below_the_platform_minimum_is_skipped_not_stretched(engine, config):
    slots = [s for s in planner.plan_week(config, MONDAY) if s.platform == "linkedin"]
    thin = make_item(
        approval_status=APPROVED,
        core_message="Short note about automation.",
    )
    candidate = _candidate(config, thin, platforms=("linkedin",))

    with engine.connect() as conn:
        plan = planner.fill_slots(conn, config, slots, [candidate], NOW)

    assert plan.filled == []
    assert any(s.skip_reason == planner.SKIP_TOO_SHORT for s in plan.skipped)


def test_a_qualified_item_does_fill_its_slot(engine, config):
    slots = [
        s
        for s in planner.plan_week(config, MONDAY)
        if s.pillar == "cybersecurity_education" and s.platform == "linkedin"
    ]
    approved = make_item(approval_status=APPROVED)

    with engine.connect() as conn:
        plan = planner.fill_slots(
            conn, config, slots, [_candidate(config, approved, ("linkedin",))], NOW
        )

    assert len(plan.filled) == 1
    assert plan.filled[0].content_id == approved.content_id
    assert plan.filled[0].status == STATUS_FILLED


def test_weekly_limits_are_respected(engine, config):
    slots = planner.plan_week(config, MONDAY)
    max_week = int(config.cadence.limits["max_posts_per_week"])

    candidates = []
    for index in range(20):
        item = make_item(content_id="MKT-2026-%05d" % (100 + index), approval_status=APPROVED)
        candidates.append(_candidate(config, item))

    with engine.connect() as conn:
        plan = planner.fill_slots(conn, config, slots, candidates, NOW)

    assert len(plan.filled) <= max_week


def test_metrics_attach_to_the_correct_published_post(engine, config, publisher):
    """Test 13."""
    first = make_item(content_id="MKT-2026-00200", approval_status=APPROVED, topic="Playbooks")
    second = make_item(content_id="MKT-2026-00201", approval_status=APPROVED, topic="Handoffs")

    outcomes = []
    for item in (first, second):
        variants = build_all(config, item, ["linkedin"])
        with engine.begin() as conn:
            save_item(conn, item, NOW)
            save_variants(conn, variants, NOW)
            decision = evaluate(config, item, variants)
            outcome = publish_variant(conn, config, publisher, item, variants[0], decision, NOW)
        outcomes.append((item, outcome))

    # Mark both as published so metrics collection considers them.
    from app.models import publications

    with engine.begin() as conn:
        conn.execute(
            publications.update().values(status="PUBLISHED", published_at=NOW)
        )

    publisher.set_metrics(
        "post-1",
        MetricsSample(provider_post_id="post-1", platform="linkedin", impressions=2870, reactions=40),
    )
    publisher.set_metrics(
        "post-2",
        MetricsSample(provider_post_id="post-2", platform="linkedin", impressions=640, reactions=5),
    )

    with engine.begin() as conn:
        result = collect(conn, publisher, NOW + dt.timedelta(hours=1))

    assert result.collected == 2

    with engine.connect() as conn:
        rows = {r["publication_id"]: dict(r) for r in conn.execute(select(post_metrics)).mappings()}

    first_metrics = rows[outcomes[0][1].publication_id]
    second_metrics = rows[outcomes[1][1].publication_id]

    assert first_metrics["impressions"] == 2870
    assert first_metrics["topic"] == "Playbooks"
    assert second_metrics["impressions"] == 640
    assert second_metrics["topic"] == "Handoffs"
    # Attribution travels with the sample so the weekly report is one query.
    assert first_metrics["pillar"] == "cybersecurity_education"
    assert first_metrics["hook_type"] == "statement"
    assert first_metrics["engagement_rate"] is not None


def test_metrics_are_skipped_cleanly_when_the_provider_has_none(engine, config, publisher):
    item = make_item(approval_status=APPROVED)
    variants = build_all(config, item, ["linkedin"])
    with engine.begin() as conn:
        save_item(conn, item, NOW)
        save_variants(conn, variants, NOW)
        decision = evaluate(config, item, variants)
        publish_variant(conn, config, publisher, item, variants[0], decision, NOW)

    from app.models import publications

    with engine.begin() as conn:
        conn.execute(publications.update().values(status="PUBLISHED", published_at=NOW))
        result = collect(conn, publisher, NOW + dt.timedelta(hours=1))

    assert result.collected == 0
    assert result.skipped_no_data == 1
    assert result.errors == []
