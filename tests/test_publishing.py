"""Tests 5, 8, 9, 16, 17: the publication lifecycle.

5.  Auto-approved content can follow the configured schedule.
8.  Publication status is persisted.
9.  A failed publication is visible and retryable.
16. A provider outage does not lose approved content.
17. Historical published content remains auditable.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.calendar import planner
from app.content.item import APPROVED, AUTO_APPROVED, PUBLIC
from app.content.store import save_item, save_variants
from app.models import publication_events, publications
from app.platforms.adapters import build_all
from app.policy.engine import evaluate
from app.publisher.base import STATUS_FAILED, STATUS_SCHEDULED
from app.publisher.service import audit_trail, publish_variant, retryable_publications

from .conftest import NOW, TODAY, FakePublisher, make_item


def _prepare(engine, config, item=None, platforms=("linkedin",)):
    item = item or make_item(approval_status=APPROVED)
    variants = build_all(config, item, list(platforms))
    with engine.begin() as conn:
        save_item(conn, item, NOW)
        save_variants(conn, variants, NOW)
    return item, variants


def test_auto_approved_content_follows_the_schedule(engine, config, publisher):
    """Test 5."""
    item, variants = _prepare(engine, config)
    decision = evaluate(config, item, variants)
    assert decision.requires_approval is False, "auto-eligible pillar should not queue"

    with engine.begin() as conn:
        outcome = publish_variant(
            conn, config, publisher, item, variants[0], decision, NOW,
            scheduled_for=NOW + dt.timedelta(hours=2),
        )

    assert outcome.ok
    assert publisher.requests[0].scheduled_for == NOW + dt.timedelta(hours=2)


def test_publication_status_is_persisted(engine, config, publisher):
    """Test 8."""
    item, variants = _prepare(engine, config)
    decision = evaluate(config, item, variants)

    with engine.begin() as conn:
        outcome = publish_variant(conn, config, publisher, item, variants[0], decision, NOW)

    with engine.connect() as conn:
        row = conn.execute(select(publications)).mappings().one()

    assert row["publication_id"] == outcome.publication_id
    assert row["status"] == STATUS_SCHEDULED
    assert row["provider_post_id"] == "post-1"
    assert row["attempts"] == 1
    assert row["content_id"] == item.content_id


def test_failed_publication_is_visible_and_retryable(engine, config):
    """Test 9."""
    failing = FakePublisher(fail_times=1)
    item, variants = _prepare(engine, config)
    decision = evaluate(config, item, variants)

    with engine.begin() as conn:
        outcome = publish_variant(conn, config, failing, item, variants[0], decision, NOW)

    assert outcome.status == STATUS_FAILED

    with engine.connect() as conn:
        row = conn.execute(select(publications)).mappings().one()
        pending = retryable_publications(conn, config, NOW)

    assert row["status"] == STATUS_FAILED
    assert row["last_error"] == "provider unavailable"
    assert row["retryable"] is True
    assert [p["publication_id"] for p in pending] == [outcome.publication_id]


def test_provider_outage_does_not_lose_approved_content(engine, config):
    """Test 16."""
    outage = FakePublisher(fail_times=2)
    item, variants = _prepare(engine, config)
    decision = evaluate(config, item, variants)

    with engine.begin() as conn:
        publish_variant(conn, config, outage, item, variants[0], decision, NOW)
    with engine.begin() as conn:
        publish_variant(
            conn, config, outage, item, variants[0], decision, NOW + dt.timedelta(minutes=10)
        )
    # Third attempt: the provider is back.
    with engine.begin() as conn:
        final = publish_variant(
            conn, config, outage, item, variants[0], decision, NOW + dt.timedelta(minutes=20)
        )

    assert final.ok
    with engine.connect() as conn:
        row = conn.execute(select(publications)).mappings().one()
    assert row["status"] == STATUS_SCHEDULED
    assert row["attempts"] == 3
    # The content itself was never discarded or downgraded.
    assert item.approval_status == APPROVED


def test_a_configuration_failure_is_not_marked_retryable(engine, config):
    from app.publisher.buffer import BufferConfig, BufferPublisher

    class DeadTransport:
        def post_json(self, url, payload, headers=None, timeout=60):
            raise AssertionError("should not be called without a channel")

    buffer = BufferPublisher(
        DeadTransport(), BufferConfig(api_base="https://x", token="t", channels={})
    )
    item, variants = _prepare(engine, config)
    decision = evaluate(config, item, variants)

    with engine.begin() as conn:
        outcome = publish_variant(conn, config, buffer, item, variants[0], decision, NOW)

    assert outcome.status == STATUS_FAILED
    with engine.connect() as conn:
        row = conn.execute(select(publications)).mappings().one()
        # Retrying a missing channel id forever helps nobody.
        assert row["retryable"] is False
        assert retryable_publications(conn, config, NOW) == []


def test_published_history_remains_auditable(engine, config, publisher):
    """Test 17."""
    item, variants = _prepare(engine, config)
    decision = evaluate(config, item, variants)

    with engine.begin() as conn:
        publish_variant(conn, config, publisher, item, variants[0], decision, NOW)

    with engine.connect() as conn:
        trail = audit_trail(conn, item.content_id)

    types = [e["event_type"] for e in trail]
    assert types == ["AUTHORISED", "SUBMITTED", "SCHEDULED"]
    assert all(e["actor"] for e in trail), "every event must name who did it"
    assert any("policy" in e["actor"] or "publisher" in e["actor"] for e in trail)

    # Append-only: re-publishing does not rewrite what already happened.
    with engine.begin() as conn:
        publish_variant(conn, config, publisher, item, variants[0], decision, NOW)
    with engine.connect() as conn:
        later = audit_trail(conn, item.content_id)
    assert later[: len(trail)] == trail


def test_blocked_content_never_reaches_the_publisher(engine, config, publisher):
    blocked = make_item(disclosure=None, approval_status=APPROVED)
    item, variants = _prepare(engine, config, blocked)
    decision = evaluate(config, item, variants)

    with engine.begin() as conn:
        outcome = publish_variant(conn, config, publisher, item, variants[0], decision, NOW)

    assert outcome.status == "BLOCKED"
    assert publisher.requests == []
    with engine.connect() as conn:
        events = conn.execute(select(publication_events)).mappings().all()
    assert [e["event_type"] for e in events] == ["BLOCKED"]


def test_an_item_needing_approval_is_drafted_at_the_provider(engine, config, publisher):
    """The approval control exists at the publishing layer too."""
    item = make_item(
        pillar="founder_expertise",
        disclosure="PUBLIC_AFTER_APPROVAL",
        approval_requirement="HUMAN_APPROVAL_REQUIRED",
        approval_status=APPROVED,
    )
    item, variants = _prepare(engine, config, item)
    decision = evaluate(config, item, variants)
    assert decision.requires_approval

    with engine.begin() as conn:
        publish_variant(conn, config, publisher, item, variants[0], decision, NOW)

    assert publisher.requests[0].create_as_draft is True
