"""Test 11: capture intelligence cannot bypass sanitisation."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.capture_bridge import (
    INTAKE_ACCEPTED,
    INTAKE_REJECTED_UNKNOWN_TYPE,
    INTAKE_REJECTED_UNSANITISED,
    UnsanitisedSignal,
    intake,
    to_content_draft,
    validate,
)
from app.content.item import HUMAN_APPROVAL_REQUIRED
from app.models import marketing_events

from .conftest import NOW

GOOD = {
    "signal_code": "TREND_SECURITY_AUTOMATION_DEMAND",
    "observed_period": "2026-Q3",
    "direction": "INCREASING",
    "confidence": "MODERATE",
}


def test_a_sanitised_trend_signal_is_accepted(engine):
    """Test 11, the allowed half."""
    with engine.begin() as conn:
        result = intake(conn, dict(GOOD), NOW)

    assert result.accepted
    assert result.signal.signal_code == "TREND_SECURITY_AUTOMATION_DEMAND"


def test_capture_intelligence_cannot_bypass_sanitisation(engine):
    """Test 11, the blocked half."""
    leaky = dict(GOOD)
    leaky.update(
        {
            "solicitation_number": "36C10B26Q0286",
            "prime": "Example Prime Inc",
            "agency": "DEPT OF VETERANS AFFAIRS",
        }
    )

    with engine.begin() as conn:
        result = intake(conn, leaky, NOW)

    assert result.status == INTAKE_REJECTED_UNSANITISED
    assert "solicitation_number" in result.rejection_reason
    assert "prime" in result.rejection_reason

    with engine.connect() as conn:
        row = conn.execute(select(marketing_events)).mappings().one()
    assert row["intake_status"] == INTAKE_REJECTED_UNSANITISED
    assert row["content_id"] is None


def test_rejection_not_redaction(engine):
    """A rich payload is refused, not stripped and used.

    Sanitising in place is how the interesting part survives; the bridge accepts
    only what was already sanitised upstream.
    """
    with pytest.raises(UnsanitisedSignal, match="capture-intelligence field"):
        validate({**GOOD, "incumbent": "Example Corp"})

    with pytest.raises(UnsanitisedSignal, match="unrecognised field"):
        validate({**GOOD, "internal_note": "watch this one"})


def test_free_text_is_refused(engine):
    """The period is a code, not prose. Prose is where a leak would ride in."""
    with pytest.raises(UnsanitisedSignal, match="free text"):
        validate(
            {
                **GOOD,
                "observed_period": "RDX is pursuing a large opportunity with a prime in Q3 2026",
            }
        )
    for good_period in ("2026", "2026-Q3", "2026-08"):
        assert validate({**GOOD, "observed_period": good_period})


def test_an_unlisted_signal_code_is_refused():
    with pytest.raises(UnsanitisedSignal, match="not an allowed trend signal code"):
        validate({**GOOD, "signal_code": "TREND_WE_ARE_BIDDING_ABC"})


def test_a_non_trend_event_type_is_refused(engine):
    with engine.begin() as conn:
        result = intake(conn, dict(GOOD), NOW, event_type="OPPORTUNITY_SCORED")
    assert result.status == INTAKE_REJECTED_UNKNOWN_TYPE


def test_an_accepted_event_is_not_publishing_permission(engine, config):
    """Receiving a marketing event does not authorise a post."""
    with engine.begin() as conn:
        result = intake(conn, dict(GOOD), NOW)

    draft = to_content_draft(config, result.signal, "MKT-2026-00030")

    assert draft.approval_requirement == HUMAN_APPROVAL_REQUIRED
    assert draft.approval_status == "DRAFT"
    # The generated copy is educational and names no pursuit.
    lowered = draft.core_message.lower()
    for forbidden in ("solicitation", "pursuing", "prime", "bid", "rfp"):
        assert forbidden not in lowered
