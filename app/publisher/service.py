"""Publication lifecycle.

Every path to a platform runs through :func:`publish_variant`, and the first
thing it does is ask the policy engine for permission. Generation is not
approval, scheduling is not approval, and a retry is not a fresh authorisation:
policy is re-evaluated on every attempt.

Failures are kept, not dropped. A provider outage leaves the publication row in
FAILED with its error and ``retryable=True``, the content stays approved, and
:func:`retryable_publications` hands it back on the next run. Approved content
does not evaporate because Buffer had a bad afternoon.

``publication_events`` is append-only: what was published, when, on whose
authority, and what happened is permanently readable.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, select
from sqlalchemy.engine import Connection

from ..config import AppConfig
from ..content.item import (
    LIFECYCLE_FAILED,
    LIFECYCLE_PARTIAL,
    LIFECYCLE_PUBLISHED,
    LIFECYCLE_SCHEDULED,
    ContentItem,
    PlatformVariant,
)
from ..content.store import load_variants, save_item
from ..models import publication_events, publications
from ..policy.engine import NotAuthorised, PolicyDecision, authorise_publication
from .base import (
    STATUS_FAILED,
    STATUS_PUBLISHED,
    STATUS_SCHEDULED,
    PublishRequest,
    PublishResult,
    SocialPublisher,
)

EVENT_AUTHORISED = "AUTHORISED"
EVENT_SUBMITTED = "SUBMITTED"
EVENT_SCHEDULED = "SCHEDULED"
EVENT_PUBLISHED = "PUBLISHED"
EVENT_FAILED = "FAILED"
EVENT_RETRY = "RETRY"
EVENT_BLOCKED = "BLOCKED"


@dataclass
class PublishOutcome:
    publication_id: str
    status: str
    result: Optional[PublishResult] = None
    blocked_reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_SCHEDULED, STATUS_PUBLISHED)


def publication_id_for(variant_id: str) -> str:
    return "PUB-" + hashlib.sha256(variant_id.encode("utf-8")).hexdigest()[:32]


def publish_variant(
    conn: Connection,
    config: AppConfig,
    publisher: SocialPublisher,
    item: ContentItem,
    variant: PlatformVariant,
    decision: PolicyDecision,
    now: dt.datetime,
    scheduled_for: Optional[dt.datetime] = None,
    approver: Optional[str] = None,
) -> PublishOutcome:
    pub_id = publication_id_for(variant.variant_id)

    try:
        authorise_publication(item, decision, approver=approver)
    except NotAuthorised as exc:
        _record_event(
            conn,
            pub_id,
            item.content_id,
            now,
            EVENT_BLOCKED,
            actor="policy:%s" % decision.policy_version,
            detail={"reason": str(exc), "codes": decision.blocking_codes},
        )
        return PublishOutcome(pub_id, "BLOCKED", blocked_reason=str(exc))

    existing = _load_publication(conn, pub_id)
    attempts = int(existing["attempts"]) if existing else 0

    if existing and existing["status"] in (STATUS_PUBLISHED, STATUS_SCHEDULED):
        # Already live or staged. Re-publishing would double-post.
        return PublishOutcome(pub_id, existing["status"])

    _record_event(
        conn,
        pub_id,
        item.content_id,
        now,
        EVENT_AUTHORISED,
        actor=approver or "policy:%s" % decision.policy_version,
        detail={"requires_approval": decision.requires_approval},
    )

    # Draft at the provider whenever a human is still in the loop, so the
    # approval control exists on the publishing side too.
    create_as_draft = decision.requires_approval or bool(
        config.platforms.publisher.get("default_create_as_draft", True)
    )

    request = PublishRequest(
        content_id=item.content_id,
        variant_id=variant.variant_id,
        platform=variant.platform,
        body=variant.body,
        post_type=variant.post_type,
        first_comment=variant.first_comment,
        media_ids=list(variant.media_ids),
        scheduled_for=scheduled_for,
        create_as_draft=create_as_draft,
        idempotency_key=pub_id,
    )

    _record_event(
        conn, pub_id, item.content_id, now, EVENT_SUBMITTED,
        actor="publisher:%s" % publisher.name,
        detail={"platform": variant.platform, "as_draft": create_as_draft},
    )

    result = publisher.publish(request)
    attempts += 1

    _upsert_publication(
        conn,
        pub_id=pub_id,
        item=item,
        variant=variant,
        result=result,
        attempts=attempts,
        scheduled_for=scheduled_for,
        now=now,
        existed=existing is not None,
    )

    event_type = {
        STATUS_PUBLISHED: EVENT_PUBLISHED,
        STATUS_SCHEDULED: EVENT_SCHEDULED,
        STATUS_FAILED: EVENT_FAILED,
    }.get(result.status, EVENT_SUBMITTED)

    _record_event(
        conn,
        pub_id,
        item.content_id,
        now,
        event_type,
        actor="publisher:%s" % result.provider,
        detail={
            "status": result.status,
            "provider_post_id": result.provider_post_id,
            "error": result.error_message,
            "retryable": result.retryable,
            "attempts": attempts,
        },
    )

    _update_item_lifecycle(conn, item, now)
    return PublishOutcome(pub_id, result.status, result=result)


def retryable_publications(
    conn: Connection, config: AppConfig, now: dt.datetime
) -> List[Dict[str, Any]]:
    """Failed attempts that are still worth retrying, oldest first."""
    max_attempts = int(
        (config.platforms.publisher.get("retry", {}) or {}).get("max_attempts", 4)
    )
    rows = (
        conn.execute(
            select(publications)
            .where(
                and_(
                    publications.c.status == STATUS_FAILED,
                    publications.c.retryable.is_(True),
                    publications.c.attempts < max_attempts,
                )
            )
            .order_by(publications.c.updated_at)
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def _load_publication(conn: Connection, pub_id: str) -> Optional[Dict[str, Any]]:
    row = (
        conn.execute(select(publications).where(publications.c.publication_id == pub_id))
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _upsert_publication(
    conn: Connection,
    pub_id: str,
    item: ContentItem,
    variant: PlatformVariant,
    result: PublishResult,
    attempts: int,
    scheduled_for: Optional[dt.datetime],
    now: dt.datetime,
    existed: bool,
) -> None:
    values = {
        "platform": variant.platform,
        "provider": result.provider,
        "provider_post_id": result.provider_post_id,
        "scheduled_for": scheduled_for,
        "published_at": now if result.status == STATUS_PUBLISHED else None,
        "status": result.status,
        "attempts": attempts,
        "last_error": result.error_message,
        "retryable": result.retryable,
        "permalink": result.permalink,
        "updated_at": now,
    }
    if existed:
        conn.execute(
            publications.update()
            .where(publications.c.publication_id == pub_id)
            .values(**values)
        )
    else:
        values.update(
            publication_id=pub_id,
            content_id=item.content_id,
            variant_id=variant.variant_id,
            created_at=now,
        )
        conn.execute(publications.insert().values(**values))


def _record_event(
    conn: Connection,
    publication_id: str,
    content_id: str,
    now: dt.datetime,
    event_type: str,
    actor: str,
    detail: Dict[str, Any],
) -> None:
    conn.execute(
        publication_events.insert().values(
            publication_id=publication_id,
            content_id=content_id,
            occurred_at=now,
            event_type=event_type,
            actor=actor,
            detail=detail,
        )
    )


def _update_item_lifecycle(conn: Connection, item: ContentItem, now: dt.datetime) -> None:
    variants = load_variants(conn, item.content_id)
    if not variants:
        return

    rows = (
        conn.execute(
            select(publications.c.status).where(publications.c.content_id == item.content_id)
        )
        .mappings()
        .all()
    )
    statuses = [r["status"] for r in rows]
    if not statuses:
        return

    if all(s == STATUS_PUBLISHED for s in statuses) and len(statuses) == len(variants):
        item.lifecycle_status = LIFECYCLE_PUBLISHED
    elif any(s in (STATUS_PUBLISHED, STATUS_SCHEDULED) for s in statuses) and any(
        s == STATUS_FAILED for s in statuses
    ):
        item.lifecycle_status = LIFECYCLE_PARTIAL
    elif all(s == STATUS_FAILED for s in statuses):
        item.lifecycle_status = LIFECYCLE_FAILED
    else:
        item.lifecycle_status = LIFECYCLE_SCHEDULED

    save_item(conn, item, now)


def audit_trail(conn: Connection, content_id: str) -> List[Dict[str, Any]]:
    rows = (
        conn.execute(
            select(publication_events)
            .where(publication_events.c.content_id == content_id)
            .order_by(publication_events.c.event_id)
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]
