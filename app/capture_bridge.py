"""The CaptureOS boundary.

CaptureOS knows what RDX is chasing. Marketing must never learn it. The bridge
therefore accepts one shape of input and one only: a **sanitised trend signal**
drawn from a fixed vocabulary.

Allowed::

    TREND_SECURITY_AUTOMATION_DEMAND

Not allowed, and rejected rather than redacted::

    "RDX is pursuing solicitation ABC with Prime XYZ"

Rejection is deliberate. Stripping identifying fields out of a rich payload and
publishing the remainder is how capture strategy leaks: the interesting part
survives the redaction. So the bridge does not sanitise — it refuses anything
that is not already sanitised, and records the refusal.

Receiving a marketing event is not permission to publish. A converted event
enters the pipeline as a draft and goes through the same policy engine and
approval queue as everything else.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.engine import Connection

from .config import AppConfig
from .content.item import (
    HUMAN_APPROVAL_REQUIRED,
    MARKETING_SAFE_SUMMARY,
    ORIGIN_EVENT,
    ContentItem,
)
from .content.store import next_content_id, save_item
from .models import marketing_events

INTAKE_ACCEPTED = "ACCEPTED"
INTAKE_REJECTED_UNSANITISED = "REJECTED_UNSANITISED"
INTAKE_REJECTED_UNKNOWN_TYPE = "REJECTED_UNKNOWN_TYPE"
INTAKE_CONVERTED = "CONVERTED"

EVENT_TREND_SIGNAL = "TREND_SIGNAL"

#: The complete vocabulary CaptureOS may emit to marketing. Adding to this list
#: is a deliberate, reviewable act.
ALLOWED_TREND_CODES = frozenset(
    {
        "TREND_SECURITY_AUTOMATION_DEMAND",
        "TREND_SOC_MODERNISATION_DEMAND",
        "TREND_ZERO_TRUST_DEMAND",
        "TREND_AI_GOVERNANCE_DEMAND",
        "TREND_CYBER_WORKFORCE_DEMAND",
        "TREND_INCIDENT_RESPONSE_DEMAND",
    }
)

#: Fields whose presence proves the payload is not sanitised. These are the
#: things that turn a market observation into capture intelligence.
FORBIDDEN_KEYS = frozenset(
    {
        "solicitation_number",
        "solicitationnumber",
        "notice_id",
        "noticeid",
        "opportunity_id",
        "opportunityid",
        "agency",
        "office",
        "contracting_officer",
        "prime",
        "prime_name",
        "teaming_partner",
        "incumbent",
        "award_amount",
        "recommended_action",
        "score",
        "total_score",
        "capture_strategy",
        "price_to_win",
        "response_deadline",
        "set_aside",
        "naics",
        "psc",
        "customer",
        "customer_name",
        "url",
        "source_url",
    }
)

#: The only keys a trend signal may carry.
ALLOWED_KEYS = frozenset({"signal_code", "observed_period", "direction", "confidence"})

ALLOWED_DIRECTIONS = frozenset({"INCREASING", "STEADY", "DECREASING"})
ALLOWED_CONFIDENCE = frozenset({"LOW", "MODERATE", "HIGH"})

#: 2026 | 2026-Q3 | 2026-08. Anything else is prose, and prose is where leaks hide.
PERIOD_PATTERN = re.compile(r"^\d{4}(-(Q[1-4]|0[1-9]|1[0-2]))?$")


class UnsanitisedSignal(ValueError):
    """Raised when CaptureOS offers marketing something it may not have."""


@dataclass
class TrendSignal:
    signal_code: str
    observed_period: str
    direction: str = "INCREASING"
    confidence: str = "MODERATE"

    def to_payload(self) -> Dict[str, Any]:
        return {
            "signal_code": self.signal_code,
            "observed_period": self.observed_period,
            "direction": self.direction,
            "confidence": self.confidence,
        }


@dataclass
class IntakeResult:
    event_id: str
    status: str
    signal: Optional[TrendSignal] = None
    rejection_reason: Optional[str] = None

    @property
    def accepted(self) -> bool:
        return self.status in (INTAKE_ACCEPTED, INTAKE_CONVERTED)


def event_id_for(payload: Dict[str, Any], received_at: Optional[dt.datetime] = None) -> str:
    """Stable identity: one sanitised (signal_code, observed_period) is one event."""
    present = {str(k).lower() for k in payload.keys()} if isinstance(payload, dict) else set()
    if present <= ALLOWED_KEYS and "signal_code" in present and "observed_period" in present:
        seed = json.dumps(
            {
                "signal_code": str(payload.get("signal_code", "")).strip().upper(),
                "observed_period": str(payload.get("observed_period", "")).strip(),
            },
            sort_keys=True,
        )
    else:
        seed = json.dumps(payload, sort_keys=True, default=str)
    return "EVT-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def validate(payload: Dict[str, Any]) -> TrendSignal:
    """Return the signal, or raise explaining exactly what disqualified it."""
    if not isinstance(payload, dict):
        raise UnsanitisedSignal("trend signal payload must be a mapping")

    present = {str(k).lower() for k in payload.keys()}
    forbidden: Set[str] = present & FORBIDDEN_KEYS
    if forbidden:
        raise UnsanitisedSignal(
            "payload carries capture-intelligence field(s) %s; marketing accepts only "
            "sanitised trend signals" % ", ".join(sorted(forbidden))
        )

    unexpected = present - ALLOWED_KEYS
    if unexpected:
        raise UnsanitisedSignal(
            "payload carries unrecognised field(s) %s; the trend-signal shape is fixed"
            % ", ".join(sorted(unexpected))
        )

    code = str(payload.get("signal_code", "")).strip().upper()
    if code not in ALLOWED_TREND_CODES:
        raise UnsanitisedSignal(
            "%r is not an allowed trend signal code" % payload.get("signal_code")
        )

    direction = str(payload.get("direction", "INCREASING")).upper()
    if direction not in ALLOWED_DIRECTIONS:
        raise UnsanitisedSignal("direction %r is not recognised" % direction)

    period = str(payload.get("observed_period", "")).strip()
    if not period:
        raise UnsanitisedSignal("trend signal requires an observed_period")
    # Free text is where leaks hide, so the period is a code, not prose:
    # 2026, 2026-Q3, or 2026-08.
    if not PERIOD_PATTERN.match(period):
        raise UnsanitisedSignal(
            "observed_period %r carries free text; expected a period code such as "
            "2026, 2026-Q3, or 2026-08" % period
        )

    if str(payload.get("confidence", "MODERATE")).upper() not in ALLOWED_CONFIDENCE:
        raise UnsanitisedSignal(
            "confidence %r is not recognised" % payload.get("confidence")
        )

    return TrendSignal(
        signal_code=code,
        observed_period=period,
        direction=direction,
        confidence=str(payload.get("confidence", "MODERATE")).upper(),
    )


def intake(
    conn: Connection,
    payload: Dict[str, Any],
    now: dt.datetime,
    source_system: str = "captureos",
    event_type: str = EVENT_TREND_SIGNAL,
) -> IntakeResult:
    """Record an inbound marketing event and say whether it was accepted."""
    event_id = event_id_for(payload, now)
    existing = (
        conn.execute(
            select(marketing_events).where(marketing_events.c.event_id == event_id)
        )
        .mappings()
        .first()
    )
    if existing:
        signal = None
        if existing["intake_status"] in (INTAKE_ACCEPTED, INTAKE_CONVERTED):
            try:
                signal = validate(dict(existing["payload"] or {}))
            except UnsanitisedSignal:
                signal = None
        return IntakeResult(
            existing["event_id"],
            existing["intake_status"],
            signal=signal,
            rejection_reason=existing.get("rejection_reason"),
        )

    if event_type != EVENT_TREND_SIGNAL:
        _record(
            conn, event_id, now, source_system, event_type, payload,
            INTAKE_REJECTED_UNKNOWN_TYPE,
            "only %s events are accepted from %s" % (EVENT_TREND_SIGNAL, source_system),
        )
        return IntakeResult(
            event_id, INTAKE_REJECTED_UNKNOWN_TYPE, rejection_reason="unknown event type"
        )

    try:
        signal = validate(payload)
    except UnsanitisedSignal as exc:
        # The payload itself is stored so the rejection is auditable, but it is
        # marked rejected and can never be converted into content.
        _record(
            conn, event_id, now, source_system, event_type, payload,
            INTAKE_REJECTED_UNSANITISED, str(exc),
        )
        return IntakeResult(
            event_id, INTAKE_REJECTED_UNSANITISED, rejection_reason=str(exc)
        )

    _record(
        conn, event_id, now, source_system, event_type, signal.to_payload(),
        INTAKE_ACCEPTED, None,
    )
    return IntakeResult(event_id, INTAKE_ACCEPTED, signal=signal)


TOPIC_BY_CODE = {
    "TREND_SECURITY_AUTOMATION_DEMAND": (
        "soar_automation",
        "Security automation demand",
        "Security operations modernisation is becoming less about adding another "
        "security product and more about connecting the tools an organisation "
        "already owns, with a human still accountable for the outcome.",
    ),
    "TREND_SOC_MODERNISATION_DEMAND": (
        "cybersecurity_education",
        "SOC modernisation",
        "Modernising a security operations centre is mostly an integration and "
        "process problem, not a procurement problem.",
    ),
    "TREND_ZERO_TRUST_DEMAND": (
        "cybersecurity_education",
        "Zero trust in practice",
        "Zero trust succeeds or fails on identity and instrumentation, long before "
        "any product decision gets made.",
    ),
    "TREND_AI_GOVERNANCE_DEMAND": (
        "cybersecurity_education",
        "Governing agent-assisted operations",
        "Automating a response step does not remove accountability for it. "
        "Governance is what makes an automated action defensible afterwards.",
    ),
    "TREND_CYBER_WORKFORCE_DEMAND": (
        "academy_workforce",
        "Cyber workforce development",
        "The shortage in security operations is rarely raw headcount. It is people "
        "who can build and maintain the automation the tools assume you already have.",
    ),
    "TREND_INCIDENT_RESPONSE_DEMAND": (
        "cybersecurity_education",
        "Incident response readiness",
        "Response speed comes from rehearsed handoffs and working integrations, "
        "not from adding another alert source.",
    ),
}


def to_content_draft(
    config: AppConfig, signal: TrendSignal, content_id: str, campaign: str = "RDX Authority Building"
) -> ContentItem:
    """Turn an accepted signal into a draft.

    The output is educational and says nothing about any specific pursuit. It
    still enters the pipeline as a draft requiring approval: an event is an
    input, not an authorisation.
    """
    pillar, topic, core_message = TOPIC_BY_CODE[signal.signal_code]
    pillar_cfg = config.pillars.get(pillar) or {}

    return ContentItem(
        content_id=content_id,
        campaign=campaign,
        pillar=pillar,
        topic=topic,
        core_message=core_message,
        origin=ORIGIN_EVENT,
        origin_reference=signal.signal_code,
        # A market observation is publishable as education, never as a statement
        # about what RDX is bidding.
        disclosure_class=pillar_cfg.get("default_disclosure", MARKETING_SAFE_SUMMARY),
        approval_requirement=HUMAN_APPROVAL_REQUIRED,
        target_audience="security leaders and practitioners",
        cta="More on how RDX approaches this at rdxenterprise.com",
        notes={"derived_from_signal": signal.signal_code, "direction": signal.direction},
    )


def convert_accepted_event(
    conn: Connection,
    config: AppConfig,
    result: IntakeResult,
    now: dt.datetime,
) -> Optional[str]:
    """Create at most one HUMAN_APPROVAL_REQUIRED draft for an accepted event."""
    row = (
        conn.execute(
            select(marketing_events).where(marketing_events.c.event_id == result.event_id)
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    if row["content_id"]:
        return row["content_id"]
    if row["intake_status"] not in (INTAKE_ACCEPTED, INTAKE_CONVERTED):
        return None
    if result.signal is None:
        return None
    content_id = next_content_id(conn, now.year)
    draft = to_content_draft(config, result.signal, content_id)
    save_item(conn, draft, now)
    mark_converted(conn, result.event_id, content_id)
    return content_id


def convert_pending_accepted_events(
    conn: Connection, config: AppConfig, now: dt.datetime
) -> List[str]:
    """Idempotent: ACCEPTED rows with no content_id become one draft each."""
    created: List[str] = []
    rows = (
        conn.execute(
            select(marketing_events).where(
                marketing_events.c.intake_status == INTAKE_ACCEPTED,
                marketing_events.c.content_id.is_(None),
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        result = IntakeResult(
            row["event_id"],
            INTAKE_ACCEPTED,
            signal=validate(dict(row["payload"] or {})),
        )
        content_id = convert_accepted_event(conn, config, result, now)
        if content_id:
            created.append(content_id)
    return created


def mark_converted(conn: Connection, event_id: str, content_id: str) -> None:
    conn.execute(
        marketing_events.update()
        .where(marketing_events.c.event_id == event_id)
        .values(intake_status=INTAKE_CONVERTED, content_id=content_id)
    )


def rejected_events(conn: Connection) -> List[Dict[str, Any]]:
    rows = (
        conn.execute(
            select(marketing_events)
            .where(
                marketing_events.c.intake_status.in_(
                    (INTAKE_REJECTED_UNSANITISED, INTAKE_REJECTED_UNKNOWN_TYPE)
                )
            )
            .order_by(marketing_events.c.received_at)
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def _record(
    conn: Connection,
    event_id: str,
    now: dt.datetime,
    source_system: str,
    event_type: str,
    payload: Dict[str, Any],
    status: str,
    reason: Optional[str],
) -> None:
    existing = conn.execute(
        select(marketing_events.c.event_id).where(marketing_events.c.event_id == event_id)
    ).first()
    if existing:
        return
    conn.execute(
        marketing_events.insert().values(
            event_id=event_id,
            received_at=now,
            source_system=source_system,
            event_type=event_type,
            payload=payload,
            intake_status=status,
            rejection_reason=reason,
        )
    )
