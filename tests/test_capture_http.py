"""HTTP TREND_SIGNAL intake: one draft, refuse-not-redact, idempotent."""

from __future__ import annotations

import json

from sqlalchemy import select

from app.capture_bridge import INTAKE_CONVERTED
from app.capture_http import handle_trend_post
from app.content.item import HUMAN_APPROVAL_REQUIRED
from app.models import content_items, marketing_events

from .conftest import NOW

GOOD = {
    "signal_code": "TREND_SECURITY_AUTOMATION_DEMAND",
    "observed_period": "2026-08",
    "direction": "INCREASING",
    "confidence": "MODERATE",
}

SECRET = "bridge-secret"


def _config(config):
    config.env["RDX_MARKETING_BRIDGE_SECRET"] = SECRET
    return config


def test_sanitised_post_creates_one_human_approval_draft(engine, config):
    cfg = _config(config)
    status, body = handle_trend_post(
        json.dumps(GOOD).encode(), "Bearer " + SECRET, cfg, engine, NOW
    )
    assert status == 200
    assert body["status"] in ("ACCEPTED", INTAKE_CONVERTED)
    assert body["content_id"]
    with engine.connect() as conn:
        items = conn.execute(select(content_items)).mappings().all()
        events = conn.execute(select(marketing_events)).mappings().all()
    assert len(items) == 1
    assert items[0]["approval_requirement"] == HUMAN_APPROVAL_REQUIRED
    assert items[0]["approval_status"] == "DRAFT"
    assert events[0]["intake_status"] == INTAKE_CONVERTED
    assert events[0]["content_id"] == items[0]["content_id"]


def test_duplicate_post_does_not_create_a_second_draft(engine, config):
    cfg = _config(config)
    first = handle_trend_post(json.dumps(GOOD).encode(), "Bearer " + SECRET, cfg, engine, NOW)
    later = NOW.replace(day=18)
    second = handle_trend_post(
        json.dumps(GOOD).encode(), "Bearer " + SECRET, cfg, engine, later
    )
    assert first[0] == 200
    assert second[0] == 200
    assert first[1]["event_id"] == second[1]["event_id"]
    assert first[1]["content_id"] == second[1]["content_id"]
    with engine.connect() as conn:
        assert conn.execute(select(content_items)).mappings().all().__len__() == 1
        assert conn.execute(select(marketing_events)).mappings().all().__len__() == 1


def test_sensitive_payload_is_rejected_not_redacted(engine, config):
    cfg = _config(config)
    leaky = dict(GOOD)
    leaky["opportunity_id"] = "sam:sol:IHS1527890"
    leaky["agency"] = "HEALTH AND HUMAN SERVICES, DEPARTMENT OF"
    status, body = handle_trend_post(
        json.dumps(leaky).encode(), "Bearer " + SECRET, cfg, engine, NOW
    )
    assert status == 400
    assert body["status"] == "REJECTED_UNSANITISED"
    with engine.connect() as conn:
        assert conn.execute(select(content_items)).mappings().all() == []
        row = conn.execute(select(marketing_events)).mappings().one()
    assert row["content_id"] is None
    assert row["intake_status"] == "REJECTED_UNSANITISED"


def test_missing_secret_refuses_all_requests(engine, config):
    status, body = handle_trend_post(
        json.dumps(GOOD).encode(), "Bearer anything", config, engine, NOW
    )
    assert status == 503
    with engine.connect() as conn:
        assert conn.execute(select(marketing_events)).mappings().all() == []


def test_wrong_secret_is_unauthorized(engine, config):
    cfg = _config(config)
    status, _body = handle_trend_post(
        json.dumps(GOOD).encode(), "Bearer wrong", cfg, engine, NOW
    )
    assert status == 401
    with engine.connect() as conn:
        assert conn.execute(select(marketing_events)).mappings().all() == []


def test_health_is_unauthenticated():
    from app.capture_http import handle_health

    status, body = handle_health()
    assert status == 200
    assert body["status"] == "ok"
    assert body["service"] == "rdx-marketing-capture"


def test_unauthenticated_post_does_not_ingest(engine, config):
    cfg = _config(config)
    status, body = handle_trend_post(json.dumps(GOOD).encode(), "", cfg, engine, NOW)
    assert status == 401
    assert body["status"] == "unauthorized"
    with engine.connect() as conn:
        assert conn.execute(select(marketing_events)).mappings().all() == []
