"""Injectable clock.

Nothing in the engine may call ``datetime.now()`` directly. Every run carries a
clock so that a replay of a historical date produces byte-identical output to
the original run.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

UTC = dt.timezone.utc
ET = dt.timezone(dt.timedelta(hours=-4), "ET")  # see note in as_business_date


class Clock:
    """Real wall-clock time."""

    def now(self) -> dt.datetime:
        return dt.datetime.now(tz=UTC)

    def today(self) -> dt.date:
        return self.now().date()


class FrozenClock(Clock):
    """A clock pinned to an instant. Used by tests and by ``app.replay``."""

    def __init__(self, instant: dt.datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FrozenClock requires an aware datetime")
        self._instant = instant.astimezone(UTC)

    def now(self) -> dt.datetime:
        return self._instant


def parse_date(value: Optional[str]) -> Optional[dt.date]:
    """Parse a date from the several shapes government feeds emit."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    # Trailing timezone offsets and 'Z' are common on SAM timestamps.
    candidate = value.replace("Z", "+00:00")
    for parser in (_iso_datetime, _iso_date, _us_date):
        parsed = parser(candidate)
        if parsed is not None:
            return parsed
    return None


def _iso_datetime(value: str) -> Optional[dt.date]:
    try:
        return dt.datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _iso_date(value: str) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _us_date(value: str) -> Optional[dt.date]:
    try:
        return dt.datetime.strptime(value[:10], "%m/%d/%Y").date()
    except ValueError:
        return None
