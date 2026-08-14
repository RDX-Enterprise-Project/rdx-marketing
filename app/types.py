"""Dialect-portable column types.

The engine is authoritative on PostgreSQL but must be fully runnable (and
testable) on SQLite so that a developer, CI job, or replay can execute a real
daily run without a database server. Every type below therefore has a defined
representation on both dialects and round-trips to the same Python value.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from sqlalchemy import DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON, TypeDecorator

UTC = dt.timezone.utc


class UtcDateTime(TypeDecorator):
    """Timezone-aware UTC timestamp.

    PostgreSQL stores TIMESTAMP WITH TIME ZONE. SQLite stores an ISO string and
    hands back a naive datetime, which we re-stamp as UTC on the way out so
    callers never have to guess whether a value is aware.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Optional[dt.datetime], dialect: Any) -> Optional[dt.datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime rejected; pass an aware UTC datetime")
        return value.astimezone(UTC)

    def process_result_value(self, value: Optional[dt.datetime], dialect: Any) -> Optional[dt.datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


#: JSON document column. JSONB on PostgreSQL (indexable), JSON text on SQLite.
JsonDoc = JSON().with_variant(JSONB(), "postgresql")

#: Long free text (descriptions, rendered reports).
LongText = Text()
