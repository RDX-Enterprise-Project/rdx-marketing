"""Media library.

Every asset carries provenance and usage rights, because "we have the file" and
"we may publish the file" are different facts. Stock licences expire, a
photograph of a person may need a release, and a screenshot may show a customer
environment that must never appear in a public post.

:func:`assert_publishable` is the gate. It answers only what the records show
and refuses when they are silent, rather than assuming permission.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.engine import Connection

from ..models import media_assets

KIND_LOGO = "logo"
KIND_GRAPHIC = "graphic"
KIND_PHOTO = "photo"
KIND_VIDEO = "video"
KIND_TEMPLATE = "template"
KIND_SCREENSHOT = "screenshot"

RIGHTS_OWNED = "OWNED"
RIGHTS_LICENSED = "LICENSED"
RIGHTS_ATTRIBUTION_REQUIRED = "ATTRIBUTION_REQUIRED"
RIGHTS_INTERNAL_ONLY = "INTERNAL_ONLY"
RIGHTS_UNKNOWN = "UNKNOWN"

PUBLISHABLE_RIGHTS = frozenset({RIGHTS_OWNED, RIGHTS_LICENSED, RIGHTS_ATTRIBUTION_REQUIRED})


class MediaNotPublishable(RuntimeError):
    pass


@dataclass
class MediaAsset:
    media_id: str
    kind: str
    label: str
    locator: str
    provenance: str
    usage_rights: str
    rights_expire_on: Optional[dt.date] = None
    people_depicted: List[str] = field(default_factory=list)
    release_on_file: bool = False


def add_asset(conn: Connection, asset: MediaAsset, now: dt.datetime) -> str:
    existing = conn.execute(
        select(media_assets.c.media_id).where(media_assets.c.media_id == asset.media_id)
    ).first()
    if existing:
        return asset.media_id

    conn.execute(
        media_assets.insert().values(
            media_id=asset.media_id,
            kind=asset.kind,
            label=asset.label,
            locator=asset.locator,
            provenance=asset.provenance,
            usage_rights=asset.usage_rights,
            rights_expire_on=asset.rights_expire_on,
            people_depicted=list(asset.people_depicted),
            release_on_file=asset.release_on_file,
            added_at=now,
            use_count=0,
        )
    )
    return asset.media_id


def get(conn: Connection, media_id: str) -> Optional[Dict[str, Any]]:
    row = (
        conn.execute(select(media_assets).where(media_assets.c.media_id == media_id))
        .mappings()
        .first()
    )
    return dict(row) if row else None


def assert_publishable(conn: Connection, media_ids: Sequence[str], today: dt.date) -> None:
    """Raise unless every asset is cleared for external publication today."""
    for media_id in media_ids:
        asset = get(conn, media_id)
        if asset is None:
            raise MediaNotPublishable("no media record for %r" % media_id)

        rights = str(asset["usage_rights"]).split(":", 1)[0].upper()
        if rights not in PUBLISHABLE_RIGHTS:
            raise MediaNotPublishable(
                "%s has usage rights %r, which do not clear external publication"
                % (media_id, asset["usage_rights"])
            )
        if asset["rights_expire_on"] and _as_date(asset["rights_expire_on"]) < today:
            raise MediaNotPublishable(
                "%s licence expired on %s" % (media_id, asset["rights_expire_on"])
            )
        if asset["people_depicted"] and not asset["release_on_file"]:
            raise MediaNotPublishable(
                "%s depicts %s and has no release on file"
                % (media_id, ", ".join(asset["people_depicted"]))
            )


def record_use(conn: Connection, media_ids: Sequence[str], now: dt.datetime) -> None:
    for media_id in media_ids:
        asset = get(conn, media_id)
        if asset is None:
            continue
        conn.execute(
            media_assets.update()
            .where(media_assets.c.media_id == media_id)
            .values(last_used_at=now, use_count=int(asset["use_count"]) + 1)
        )


def _as_date(value: Any) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])
