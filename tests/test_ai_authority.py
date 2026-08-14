"""Test 12: AI output cannot grant publication permission."""

from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy import select

from app.ai.drafting import (
    STATUS_BUDGET_EXCEEDED,
    STATUS_DISABLED,
    STATUS_OK,
    DraftResponse,
    DraftingService,
    apply_draft,
)
from app.config import AiConfig
from app.content.item import APPROVED, DRAFT, PUBLIC
from app.models import ai_usage
from app.platforms.adapters import build_all
from app.policy.engine import NotAuthorised, authorise_publication, evaluate

from .conftest import NOW, RecordingDraftProvider, make_item


def _enabled_config(config, max_calls=5, max_cost=1.0):
    raw = dict(config.ai.raw)
    raw["enabled"] = True
    raw["tasks"] = dict(raw["tasks"])
    raw["tasks"]["draft"] = {"provider": "recording", "model": "test-model", "max_cost_usd": 0.05}
    raw["budget"] = {"max_calls_per_run": max_calls, "max_cost_usd_per_run": max_cost}
    return dataclasses.replace(config, ai=AiConfig(version=config.ai.version, raw=raw))


def test_ai_output_cannot_grant_publication_permission(engine, config):
    """Test 12."""
    provider = RecordingDraftProvider()
    enabled = _enabled_config(config)
    service = DraftingService(enabled, providers={"recording": provider})

    item = make_item(disclosure=None, approval_status=DRAFT)
    variant = build_all(enabled, item, ["linkedin"])[0]

    with engine.begin() as conn:
        text = service.improve(conn, item, variant, "draft", NOW)

    assert text, "the provider should have been called"
    apply_draft(variant, text, "test-model")

    # The model wrote the body and nothing else.
    assert variant.generated_by == "ai:test-model"
    assert item.disclosure_class is None
    assert item.approval_status == DRAFT

    decision = evaluate(enabled, item, [variant])
    assert decision.blocked
    with pytest.raises(NotAuthorised):
        authorise_publication(item, decision)


def test_the_draft_response_has_no_authority_fields():
    """A model literally cannot return a classification or an approval."""
    fields = {f.name for f in dataclasses.fields(DraftResponse)}
    for forbidden in (
        "disclosure_class",
        "approval_status",
        "approved",
        "publish",
        "scheduled_for",
        "requires_approval",
    ):
        assert forbidden not in fields


def test_the_prompt_never_carries_the_disclosure_class(engine, config):
    provider = RecordingDraftProvider()
    enabled = _enabled_config(config)
    service = DraftingService(enabled, providers={"recording": provider})

    item = make_item(disclosure=PUBLIC)
    variant = build_all(enabled, item, ["linkedin"])[0]

    with engine.begin() as conn:
        service.improve(conn, item, variant, "draft", NOW)

    request = provider.calls[0]
    payload = repr(dataclasses.asdict(request))
    assert PUBLIC not in payload
    assert "disclosure" not in payload.lower()


def test_ai_is_off_by_default_and_the_template_stands(engine, config):
    service = DraftingService(config)
    item = make_item()
    variant = build_all(config, item, ["linkedin"])[0]
    original = variant.body

    with engine.begin() as conn:
        assert service.improve(conn, item, variant, "draft", NOW) is None

    assert variant.body == original
    assert variant.generated_by == "template"

    with engine.connect() as conn:
        row = conn.execute(select(ai_usage)).mappings().one()
    assert row["status"] == STATUS_DISABLED
    assert row["cost_usd"] == 0.0


def test_ai_budget_fails_closed(engine, config):
    provider = RecordingDraftProvider()
    enabled = _enabled_config(config, max_calls=0, max_cost=0.0)
    service = DraftingService(enabled, providers={"recording": provider})

    item = make_item()
    variant = build_all(enabled, item, ["linkedin"])[0]

    with engine.begin() as conn:
        assert service.improve(conn, item, variant, "draft", NOW) is None

    assert provider.calls == []
    with engine.connect() as conn:
        row = conn.execute(select(ai_usage)).mappings().one()
    assert row["status"] == STATUS_BUDGET_EXCEEDED


def test_ai_usage_is_ledgered_with_cost(engine, config):
    provider = RecordingDraftProvider()
    enabled = _enabled_config(config)
    service = DraftingService(enabled, providers={"recording": provider})

    item = make_item()
    variant = build_all(enabled, item, ["linkedin"])[0]

    with engine.begin() as conn:
        service.improve(conn, item, variant, "draft", NOW)

    with engine.connect() as conn:
        row = conn.execute(select(ai_usage)).mappings().one()

    assert row["status"] == STATUS_OK
    assert row["provider"] == "recording"
    assert row["model"] == "test-model"
    assert row["cost_usd"] == pytest.approx(0.02)
    assert row["tokens_in"] == 100
    assert row["content_id"] == item.content_id


def test_a_provider_error_leaves_the_template_in_place(engine, config):
    class BrokenProvider:
        name = "recording"
        model = "test-model"

        def draft(self, request):
            raise RuntimeError("provider down")

    enabled = _enabled_config(config)
    service = DraftingService(enabled, providers={"recording": BrokenProvider()})
    item = make_item()
    variant = build_all(enabled, item, ["linkedin"])[0]
    original = variant.body

    with engine.begin() as conn:
        assert service.improve(conn, item, variant, "draft", NOW) is None

    assert variant.body == original
