"""The whole run, and the weekly report built from it.

This is the test that proves the operating modes actually behave the way the
architecture claims: routine approved content flows, and anything touching
customers, partners, contracts, or company claims stops for a human.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.approval import queue
from app.capture_bridge import intake, mark_converted, to_content_draft
from app.clock import FrozenClock
from app.content.item import APPROVED, AUTO_APPROVED, HUMAN_APPROVAL_REQUIRED, PENDING_APPROVAL
from app.content.store import load_item, save_item
from app.models import content_items, publications, runs
from app.pipeline import run_daily
from app.reports.weekly import build

from .conftest import CORE_EDUCATION, NOW, TODAY, FakePublisher, make_item, seed_evidence

MONDAY = dt.date(2026, 8, 17)


def test_mixed_mode_flows_routine_content_and_stops_sensitive_content(engine, config, publisher):
    routine = make_item(
        content_id="MKT-2026-00300",
        pillar="cybersecurity_education",
        approval_requirement=AUTO_APPROVED,
    )
    sensitive = make_item(
        content_id="MKT-2026-00301",
        pillar="approved_milestones",
        disclosure="PUBLIC_AFTER_APPROVAL",
        approval_requirement=HUMAN_APPROVAL_REQUIRED,
        topic="New partnership",
        core_message=(
            "RDX has an accredited training pathway that helps analysts move into "
            "security automation roles, and the programme keeps growing as more teams "
            "look for people who can build the integrations their tools assume."
        ),
    )

    with engine.begin() as conn:
        save_item(conn, routine, NOW)
        save_item(conn, sensitive, NOW)
        seed_evidence(conn, sensitive.content_id, NOW, config)

    result = run_daily(
        engine=engine,
        config=config,
        clock=FrozenClock(NOW),
        publisher=publisher,
        business_date=MONDAY,
    )

    with engine.connect() as conn:
        routine_after = load_item(conn, routine.content_id)
        sensitive_after = load_item(conn, sensitive.content_id)
        pending = queue.pending(conn)

    # Routine, auto-eligible, PUBLIC content is cleared by rule.
    assert routine_after.approval_status == APPROVED
    # A company claim waits for a person.
    assert sensitive_after.approval_status == PENDING_APPROVAL
    assert [e.content_id for e in pending] == [sensitive.content_id]
    assert result.status in ("OK", "PARTIAL")


def test_an_approval_lets_previously_held_content_publish(engine, config, publisher):
    held = make_item(
        content_id="MKT-2026-00310",
        pillar="academy_workforce",
        disclosure="PUBLIC_AFTER_APPROVAL",
        approval_requirement=HUMAN_APPROVAL_REQUIRED,
        topic="Academy cohort",
    )
    with engine.begin() as conn:
        save_item(conn, held, NOW)
        seed_evidence(conn, held.content_id, NOW, config)

    run_daily(engine=engine, config=config, clock=FrozenClock(NOW), publisher=publisher,
              business_date=MONDAY)
    assert publisher.requests == []

    with engine.begin() as conn:
        queue.decide(conn, held.content_id, True, "human:william.farrell", NOW, "reviewed")

    thursday = MONDAY + dt.timedelta(days=3)
    second = run_daily(
        engine=engine,
        config=config,
        clock=FrozenClock(NOW + dt.timedelta(days=3)),
        publisher=publisher,
        business_date=thursday,
    )

    assert second.published >= 1
    assert publisher.requests, "approved content should have reached the publisher"


def test_a_capture_trend_signal_becomes_a_held_draft(engine, config, publisher):
    payload = {
        "signal_code": "TREND_SECURITY_AUTOMATION_DEMAND",
        "observed_period": "2026-Q3",
        "direction": "INCREASING",
        "confidence": "MODERATE",
    }
    with engine.begin() as conn:
        intake_result = intake(conn, payload, NOW)
        draft = to_content_draft(config, intake_result.signal, "MKT-2026-00320")
        save_item(conn, draft, NOW)
        mark_converted(conn, intake_result.event_id, draft.content_id)

    run_daily(engine=engine, config=config, clock=FrozenClock(NOW), publisher=publisher,
              business_date=MONDAY)

    with engine.connect() as conn:
        stored = load_item(conn, "MKT-2026-00320")
        pending = queue.pending(conn)

    # A market signal produced an educational draft that still needs a person.
    assert stored.approval_status == PENDING_APPROVAL
    assert "MKT-2026-00320" in [e.content_id for e in pending]
    assert publisher.requests == []


def test_the_run_is_recorded(engine, config, publisher):
    with engine.begin() as conn:
        save_item(conn, make_item(content_id="MKT-2026-00330"), NOW)

    result = run_daily(engine=engine, config=config, clock=FrozenClock(NOW), publisher=publisher,
                       business_date=MONDAY)

    with engine.connect() as conn:
        row = conn.execute(select(runs).where(runs.c.run_id == result.run_id)).mappings().one()

    assert row["policy_version"] == config.policy_version
    assert row["slots_considered"] > 0
    assert row["ai_calls"] == 0
    assert row["ai_cost_usd"] == 0.0


def test_weekly_report_shows_skipped_slots_rather_than_hiding_a_thin_week(
    engine, config, publisher
):
    with engine.begin() as conn:
        save_item(conn, make_item(content_id="MKT-2026-00340"), NOW)

    run_daily(engine=engine, config=config, clock=FrozenClock(NOW), publisher=publisher,
              business_date=MONDAY)

    with engine.connect() as conn:
        report = build(conn, MONDAY, NOW)

    body = report.body_markdown
    assert "## Publishing consistency" in body
    assert "Skipped slots are recorded rather than filled with weaker material" in body
    assert report.summary["slots_skipped"] > 0
    assert "Performance informs what gets written next" in body

    for heading in (
        "Summary",
        "Best and weakest posts",
        "Content pillars",
        "Platforms",
        "Publishing consistency",
        "Awaiting approval",
        "Failed publications",
    ):
        assert "## %s" % heading in body, heading


def test_blocked_content_is_marked_blocked_not_queued(engine, config, publisher):
    leaky = make_item(
        content_id="MKT-2026-00350",
        core_message=(
            "We are pursuing a major federal programme this quarter and the capture "
            "strategy is coming together across the whole team right now."
        ),
    )
    with engine.begin() as conn:
        save_item(conn, leaky, NOW)

    result = run_daily(engine=engine, config=config, clock=FrozenClock(NOW), publisher=publisher,
                       business_date=MONDAY)

    with engine.connect() as conn:
        stored = load_item(conn, leaky.content_id)
        pending = queue.pending(conn)

    assert result.blocked == 1
    assert stored.lifecycle_status == "BLOCKED"
    assert leaky.content_id not in [e.content_id for e in pending]
    assert publisher.requests == []
