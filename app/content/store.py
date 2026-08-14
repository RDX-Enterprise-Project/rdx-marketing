"""Persistence for content items and their platform variants."""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from ..models import content_items, platform_variants
from .item import ContentItem, PlatformVariant, content_id


def next_content_id(conn: Connection, year: int) -> str:
    prefix = "MKT-%04d-" % year
    highest = conn.execute(
        select(func.max(content_items.c.content_id)).where(
            content_items.c.content_id.like(prefix + "%")
        )
    ).scalar()
    sequence = int(str(highest).rsplit("-", 1)[-1]) + 1 if highest else 1
    return content_id(year, sequence)


def save_item(conn: Connection, item: ContentItem, now: dt.datetime) -> None:
    existing = conn.execute(
        select(content_items.c.content_id).where(
            content_items.c.content_id == item.content_id
        )
    ).first()
    row = item.to_row(now)
    if existing:
        row.pop("content_id")
        row.pop("created_at")
        conn.execute(
            content_items.update()
            .where(content_items.c.content_id == item.content_id)
            .values(**row)
        )
    else:
        conn.execute(content_items.insert().values(**row))


def load_item(conn: Connection, content_id_value: str) -> Optional[ContentItem]:
    row = (
        conn.execute(
            select(content_items).where(content_items.c.content_id == content_id_value)
        )
        .mappings()
        .first()
    )
    return ContentItem.from_row(dict(row)) if row else None


def save_variants(
    conn: Connection, variants: Sequence[PlatformVariant], now: dt.datetime
) -> None:
    for variant in variants:
        existing = conn.execute(
            select(platform_variants.c.variant_id).where(
                platform_variants.c.variant_id == variant.variant_id
            )
        ).first()
        row = variant.to_row(now)
        if existing:
            row.pop("variant_id")
            row.pop("created_at")
            conn.execute(
                platform_variants.update()
                .where(platform_variants.c.variant_id == variant.variant_id)
                .values(**row)
            )
        else:
            conn.execute(platform_variants.insert().values(**row))


def load_variants(conn: Connection, content_id_value: str) -> List[PlatformVariant]:
    rows = (
        conn.execute(
            select(platform_variants)
            .where(platform_variants.c.content_id == content_id_value)
            .order_by(platform_variants.c.platform)
        )
        .mappings()
        .all()
    )
    return [PlatformVariant.from_row(dict(r)) for r in rows]


def find_duplicate_variant(
    conn: Connection,
    platform: str,
    body_fingerprint: str,
    since: dt.datetime,
    exclude_content_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """A previously-used body on the same platform inside the lookback window."""
    query = (
        select(platform_variants)
        .where(platform_variants.c.platform == platform)
        .where(platform_variants.c.body_fingerprint == body_fingerprint)
        .where(platform_variants.c.created_at >= since)
    )
    if exclude_content_id:
        query = query.where(platform_variants.c.content_id != exclude_content_id)
    row = conn.execute(query.limit(1)).mappings().first()
    return dict(row) if row else None


def items_by_status(
    conn: Connection, approval_status: Optional[str] = None, pillar: Optional[str] = None
) -> List[ContentItem]:
    query = select(content_items)
    if approval_status:
        query = query.where(content_items.c.approval_status == approval_status)
    if pillar:
        query = query.where(content_items.c.pillar == pillar)
    rows = conn.execute(query.order_by(content_items.c.created_at)).mappings().all()
    return [ContentItem.from_row(dict(r)) for r in rows]
