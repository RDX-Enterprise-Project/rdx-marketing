"""Scheduled bridge operation cannot publish.

Deployment-level pins: publisher off, Marketing AI off, Buffer not required,
accepted trends become HUMAN_APPROVAL_REQUIRED drafts only.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from sqlalchemy import select

from app.capture_http import handle_trend_post
from app.config import load_config
from app.content.item import HUMAN_APPROVAL_REQUIRED
from app.models import content_items, publications
from app.publisher.base import NullPublisher

from .conftest import NOW

ROOT = Path(__file__).resolve().parent.parent
GOOD = {
    "signal_code": "TREND_SECURITY_AUTOMATION_DEMAND",
    "observed_period": "2026-08",
    "direction": "INCREASING",
    "confidence": "MODERATE",
}
SECRET = "bridge-secret"


def test_publisher_is_disabled_in_production_config():
    config = load_config()
    assert config.platforms.publisher.get("enabled") is False


def test_marketing_ai_is_disabled_in_production_config():
    config = load_config()
    assert config.ai.enabled is False
    assert int(config.ai.budget.get("max_calls_per_run") or 0) == 0


def test_buffer_credentials_are_not_required_for_intake():
    render = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    keys = {item["key"] for item in render["services"][0]["envVars"]}
    assert "BUFFER_ACCESS_TOKEN" not in keys
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "BUFFER_" not in dockerfile
    wrangler = (ROOT / "receiver" / "wrangler.jsonc").read_text(encoding="utf-8")
    assert "BUFFER_" not in wrangler
    assert "linkedin" not in wrangler.lower()


def test_accepted_trend_is_human_approval_draft_only(engine, config):
    config.env["RDX_MARKETING_BRIDGE_SECRET"] = SECRET
    status, body = handle_trend_post(
        json.dumps(GOOD).encode(), "Bearer " + SECRET, config, engine, NOW
    )
    assert status == 200
    assert body["content_id"]
    with engine.connect() as conn:
        item = conn.execute(select(content_items)).mappings().one()
        pubs = conn.execute(select(publications)).mappings().all()
    assert item["approval_requirement"] == HUMAN_APPROVAL_REQUIRED
    assert item["approval_status"] == "DRAFT"
    assert item["lifecycle_status"] == "DRAFT"
    assert pubs == []


def test_daily_run_uses_null_publisher_when_disabled():
    from app.daily_run import build_publisher

    publisher = build_publisher(load_config())
    assert isinstance(publisher, NullPublisher)
