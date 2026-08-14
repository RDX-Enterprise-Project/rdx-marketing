"""Tests 1-4: the disclosure and approval boundary.

1. Unknown disclosure classification cannot publish.
2. Confidential content cannot publish.
3. Never-publish content cannot be overridden by scheduling.
4. An approval-required post cannot publish before approval.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.content.item import (
    APPROVED,
    AUTO_APPROVED,
    CONFIDENTIAL,
    HUMAN_APPROVAL_REQUIRED,
    INTERNAL_ONLY,
    NEVER_PUBLISH,
    PENDING_APPROVAL,
    PUBLIC,
    REQUIREMENT_NEVER_PUBLISH,
)
from app.platforms.adapters import build_all
from app.policy.engine import (
    MARKED_NEVER_PUBLISH,
    NO_CLASSIFICATION,
    NOT_PUBLISHABLE_CLASS,
    UNKNOWN_CLASSIFICATION,
    NotAuthorised,
    authorise_publication,
    evaluate,
)

from .conftest import NOW, make_item


def _variants(config, item):
    return build_all(config, item, ["linkedin", "facebook"])


def test_unknown_disclosure_classification_cannot_publish(config):
    """Test 1."""
    unclassified = make_item(disclosure=None)
    decision = evaluate(config, unclassified, _variants(config, unclassified))

    assert decision.blocked
    assert NO_CLASSIFICATION in decision.blocking_codes
    with pytest.raises(NotAuthorised):
        authorise_publication(unclassified, decision)

    bogus = make_item(content_id="MKT-2026-00002", disclosure="PROBABLY_FINE")
    bogus_decision = evaluate(config, bogus, _variants(config, bogus))
    assert bogus_decision.blocked
    assert UNKNOWN_CLASSIFICATION in bogus_decision.blocking_codes


def test_confidential_content_cannot_publish(config):
    """Test 2."""
    for classification in (CONFIDENTIAL, INTERNAL_ONLY):
        item = make_item(disclosure=classification)
        decision = evaluate(config, item, _variants(config, item))
        assert decision.blocked, classification
        assert NOT_PUBLISHABLE_CLASS in decision.blocking_codes
        with pytest.raises(NotAuthorised):
            authorise_publication(item, decision)


def test_never_publish_cannot_be_overridden_by_scheduling(config):
    """Test 3."""
    item = make_item(
        disclosure=PUBLIC,
        approval_requirement=REQUIREMENT_NEVER_PUBLISH,
        approval_status=APPROVED,   # even pre-approved
    )
    decision = evaluate(config, item, _variants(config, item))

    assert decision.blocked
    assert MARKED_NEVER_PUBLISH in decision.blocking_codes
    # An approval does not unblock it, and neither does a scheduler.
    with pytest.raises(NotAuthorised):
        authorise_publication(item, decision, approver="human:william.farrell")


def test_never_publish_classification_is_also_blocked(config):
    item = make_item(disclosure=NEVER_PUBLISH)
    decision = evaluate(config, item, _variants(config, item))
    assert decision.blocked
    assert NOT_PUBLISHABLE_CLASS in decision.blocking_codes


def test_approval_required_post_cannot_publish_before_approval(config):
    """Test 4."""
    item = make_item(
        pillar="rdx_capabilities",
        disclosure="PUBLIC_AFTER_APPROVAL",
        approval_requirement=HUMAN_APPROVAL_REQUIRED,
        approval_status=PENDING_APPROVAL,
    )
    # rdx_capabilities requires evidence, so give it some.
    decision = evaluate(
        config,
        item,
        _variants(config, item),
        linked_evidence=[{"evidence_id": "EV-1", "claim": "RDX delivers SOAR engineering"}],
    )

    assert not decision.blocked
    assert decision.requires_approval
    with pytest.raises(NotAuthorised, match="requires approval"):
        authorise_publication(item, decision)

    item.approval_status = APPROVED
    authorise_publication(item, decision)  # no raise


def test_a_model_can_never_be_the_approver(config):
    item = make_item(disclosure=PUBLIC, approval_requirement=AUTO_APPROVED)
    decision = evaluate(config, item, _variants(config, item))

    with pytest.raises(NotAuthorised, match="not a valid approver"):
        authorise_publication(item, decision, approver="ai:claude")
    with pytest.raises(NotAuthorised, match="not a valid approver"):
        authorise_publication(item, decision, approver="model:gpt")

    authorise_publication(item, decision, approver="human:william.farrell")
    authorise_publication(item, decision, approver="rule:auto_eligible_pillar")


def test_restricted_capture_strategy_language_blocks_publication(config):
    item = make_item(
        core_message=(
            "We are pursuing a large federal opportunity this quarter and our capture "
            "strategy is coming together nicely across the team."
        )
    )
    decision = evaluate(config, item, _variants(config, item))
    assert decision.blocked
    assert "RESTRICTED_CONTENT" in decision.blocking_codes


def test_prohibited_positioning_language_blocks_publication(config):
    item = make_item(
        core_message=(
            "RDX delivers AI security services for federal agencies that want faster "
            "response times across their security operations centre."
        )
    )
    decision = evaluate(config, item, _variants(config, item))
    assert decision.blocked
    assert "PROHIBITED_PHRASE" in decision.blocking_codes


def test_unclassified_defaults_to_human_approval_not_to_publication(config):
    """Absence of a decision is never permission."""
    item = make_item(disclosure=None, approval_requirement=AUTO_APPROVED)
    decision = evaluate(config, item, _variants(config, item))
    assert decision.blocked
    assert decision.requires_approval is False  # blocked, not merely queued
