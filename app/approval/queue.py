"""The approval queue.

Drafting authority and publishing authority are separate, and this is the seam
between them. An item enters the queue when policy says a human is needed, and
leaves it only on a decision attributed to a person or to an explicitly
configured rule.

A model can never appear here as an approver. :func:`decide` rejects any actor
that is not ``human:<name>`` or ``rule:<rule_id>``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, select
from sqlalchemy.engine import Connection

from ..content.item import (
    APPROVED,
    PENDING_APPROVAL,
    REJECTED,
    ContentItem,
)
from ..content.store import load_item, save_item
from ..models import approvals, content_items
from ..policy.engine import PolicyDecision

DECISION_PENDING = "PENDING"
DECISION_APPROVED = "APPROVED"
DECISION_REJECTED = "REJECTED"


class InvalidApprover(ValueError):
    """Raised when something that is not a person or a rule tries to approve."""


@dataclass
class QueueEntry:
    approval_id: int
    content_id: str
    requested_at: dt.datetime
    policy_snapshot: Dict[str, Any]
    item: Optional[ContentItem] = None


def request_approval(
    conn: Connection,
    item: ContentItem,
    decision: PolicyDecision,
    now: dt.datetime,
) -> Optional[int]:
    """Put an item in the queue, or return the id of its existing open request."""
    open_request = conn.execute(
        select(approvals.c.approval_id)
        .where(
            and_(
                approvals.c.content_id == item.content_id,
                approvals.c.decision == DECISION_PENDING,
            )
        )
        .limit(1)
    ).first()
    if open_request:
        return int(open_request[0])

    result = conn.execute(
        approvals.insert().values(
            content_id=item.content_id,
            requested_at=now,
            decision=DECISION_PENDING,
            policy_snapshot=decision.to_json(),
        )
    )

    item.approval_status = PENDING_APPROVAL
    save_item(conn, item, now)

    key = result.inserted_primary_key
    return int(key[0]) if key else None


def decide(
    conn: Connection,
    content_id: str,
    approved: bool,
    actor: str,
    now: dt.datetime,
    rationale: Optional[str] = None,
) -> ContentItem:
    """Record a human (or configured-rule) decision on a queued item."""
    if not _valid_actor(actor):
        raise InvalidApprover(
            "%r cannot approve content; an approver must be 'human:<name>' or "
            "'rule:<rule_id>'. A model is never an approver." % actor
        )

    row = (
        conn.execute(
            select(approvals)
            .where(approvals.c.content_id == content_id)
            .where(approvals.c.decision == DECISION_PENDING)
            .order_by(approvals.c.approval_id.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    if row is None:
        raise LookupError("no pending approval for %s" % content_id)

    conn.execute(
        approvals.update()
        .where(approvals.c.approval_id == row["approval_id"])
        .values(
            decision=DECISION_APPROVED if approved else DECISION_REJECTED,
            decided_at=now,
            decided_by=actor,
            rationale=rationale,
        )
    )

    item = load_item(conn, content_id)
    if item is None:
        raise LookupError("content %s vanished while being approved" % content_id)
    item.approval_status = APPROVED if approved else REJECTED
    save_item(conn, item, now)
    return item


def auto_approve(
    conn: Connection, item: ContentItem, rule_id: str, now: dt.datetime, decision: PolicyDecision
) -> ContentItem:
    """Record an auto-approval, attributed to the rule that granted it.

    Auto-approval is still an approval record with an attributed actor, so the
    audit trail reads the same whether a person or a rule cleared the post.
    """
    conn.execute(
        approvals.insert().values(
            content_id=item.content_id,
            requested_at=now,
            decided_at=now,
            decision=DECISION_APPROVED,
            decided_by="rule:%s" % rule_id,
            rationale="auto-approved under %s" % rule_id,
            policy_snapshot=decision.to_json(),
        )
    )
    item.approval_status = APPROVED
    save_item(conn, item, now)
    return item


def pending(conn: Connection) -> List[QueueEntry]:
    rows = (
        conn.execute(
            select(approvals)
            .where(approvals.c.decision == DECISION_PENDING)
            .order_by(approvals.c.requested_at)
        )
        .mappings()
        .all()
    )
    entries = []
    for row in rows:
        entries.append(
            QueueEntry(
                approval_id=int(row["approval_id"]),
                content_id=row["content_id"],
                requested_at=row["requested_at"],
                policy_snapshot=dict(row["policy_snapshot"] or {}),
                item=load_item(conn, row["content_id"]),
            )
        )
    return entries


def pending_count(conn: Connection) -> int:
    return len(pending(conn))


def history(conn: Connection, content_id: str) -> List[Dict[str, Any]]:
    rows = (
        conn.execute(
            select(approvals)
            .where(approvals.c.content_id == content_id)
            .order_by(approvals.c.approval_id)
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def _valid_actor(actor: str) -> bool:
    if not actor:
        return False
    if actor.startswith("human:") and len(actor) > len("human:"):
        return True
    if actor.startswith("rule:") and len(actor) > len("rule:"):
        return True
    return False
