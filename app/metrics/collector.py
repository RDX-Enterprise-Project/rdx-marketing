"""Post-performance collection.

Metrics attach to the publication they came from, and carry denormalised
attribution (campaign, pillar, topic, CTA, asset type, hook, posted-at) so the
weekly report is one query rather than a join chain that quietly drops rows.

Nothing here can change a disclosure classification or an approval. Performance
data informs what RDX writes next; it never widens what RDX is allowed to say.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.engine import Connection

from ..models import content_items, platform_variants, post_metrics, publications
from ..publisher.base import STATUS_PUBLISHED, MetricsSample, SocialPublisher


@dataclass
class CollectionResult:
    collected: int = 0
    skipped_no_provider_id: int = 0
    skipped_no_data: int = 0
    errors: List[str] = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def collect(
    conn: Connection,
    publisher: SocialPublisher,
    now: dt.datetime,
    since: Optional[dt.datetime] = None,
) -> CollectionResult:
    """Pull metrics for published posts and attach them to their publication."""
    query = select(publications).where(publications.c.status == STATUS_PUBLISHED)
    if since is not None:
        query = query.where(publications.c.published_at >= since)

    result = CollectionResult()
    for row in conn.execute(query.order_by(publications.c.published_at)).mappings().all():
        if not row["provider_post_id"]:
            result.skipped_no_provider_id += 1
            continue
        try:
            sample = publisher.fetch_metrics(row["provider_post_id"], row["platform"])
        except Exception as exc:  # noqa: BLE001 - a metrics gap is not an outage
            result.errors.append("%s: %s" % (row["publication_id"], exc))
            continue
        if sample is None:
            result.skipped_no_data += 1
            continue

        attribution = _attribution(conn, row)
        _store(conn, row["publication_id"], sample, attribution, row, now)
        result.collected += 1

    return result


def _attribution(conn: Connection, publication_row: Dict[str, Any]) -> Dict[str, Any]:
    content_row = (
        conn.execute(
            select(content_items).where(
                content_items.c.content_id == publication_row["content_id"]
            )
        )
        .mappings()
        .first()
    )
    variant_row = (
        conn.execute(
            select(platform_variants).where(
                platform_variants.c.variant_id == publication_row["variant_id"]
            )
        )
        .mappings()
        .first()
    )
    return {
        "campaign": content_row["campaign"] if content_row else None,
        "pillar": content_row["pillar"] if content_row else None,
        "topic": content_row["topic"] if content_row else None,
        "cta": (variant_row or {}).get("cta") or (content_row or {}).get("cta"),
        "asset_type": (variant_row or {}).get("post_type"),
        "hook_type": (variant_row or {}).get("hook_type"),
    }


def _store(
    conn: Connection,
    publication_id: str,
    sample: MetricsSample,
    attribution: Dict[str, Any],
    publication_row: Dict[str, Any],
    now: dt.datetime,
) -> None:
    existing = conn.execute(
        select(post_metrics.c.id)
        .where(post_metrics.c.publication_id == publication_id)
        .where(post_metrics.c.collected_at == now)
    ).first()
    if existing:
        return

    conn.execute(
        post_metrics.insert().values(
            publication_id=publication_id,
            collected_at=now,
            platform=sample.platform,
            impressions=sample.impressions,
            reach=sample.reach,
            reactions=sample.reactions,
            comments=sample.comments,
            shares=sample.shares,
            clicks=sample.clicks,
            views=sample.views,
            engagement_rate=sample.engagement_rate(),
            campaign=attribution["campaign"],
            pillar=attribution["pillar"],
            topic=attribution["topic"],
            cta=attribution["cta"],
            asset_type=attribution["asset_type"],
            hook_type=attribution["hook_type"],
            posted_at=publication_row.get("published_at"),
            raw=sample.raw,
        )
    )


def latest_metrics(conn: Connection, publication_id: str) -> Optional[Dict[str, Any]]:
    row = (
        conn.execute(
            select(post_metrics)
            .where(post_metrics.c.publication_id == publication_id)
            .order_by(post_metrics.c.collected_at.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def metrics_between(
    conn: Connection, start: dt.datetime, end: dt.datetime
) -> List[Dict[str, Any]]:
    """Latest sample per publication inside the window."""
    rows = (
        conn.execute(
            select(post_metrics)
            .where(post_metrics.c.collected_at >= start)
            .where(post_metrics.c.collected_at <= end)
            .order_by(post_metrics.c.publication_id, post_metrics.c.collected_at.desc())
        )
        .mappings()
        .all()
    )
    latest: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        latest.setdefault(row["publication_id"], dict(row))
    return list(latest.values())
