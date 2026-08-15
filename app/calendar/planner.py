"""The content calendar.

The cadence describes the shape of a good week. It is a target, never an
obligation.

The rule that matters most here: **a slot with no qualified content is skipped
and recorded as skipped.** The planner will not reach for weaker material to
make a weekday look complete. Quality outranks schedule completion, and the
weekly report shows skipped slots so a thin week is visible rather than filled
with something nobody wanted to publish.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import and_, select
from sqlalchemy.engine import Connection

from ..config import AppConfig
from ..content.item import APPROVED, ContentItem, PlatformVariant
from ..models import publications, schedule_slots

WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

STATUS_OPEN = "OPEN"
STATUS_FILLED = "FILLED"
STATUS_SKIPPED = "SKIPPED_NO_QUALIFIED_CONTENT"

SKIP_NO_CANDIDATE = "no approved, classified content available for this pillar"
SKIP_NO_VARIANT = "no platform variant exists for this platform"
SKIP_TOO_SHORT = "the only available copy is below the minimum length for this platform"
SKIP_DAILY_LIMIT = "daily post limit already reached"
SKIP_WEEKLY_LIMIT = "weekly post limit already reached"
SKIP_SPACING = "another post on this platform is too close in time"
SKIP_DUPLICATE = "the only available copy duplicates a recent post"


@dataclass
class Slot:
    slot_id: str
    slot_date: dt.date
    weekday: str
    pillar: str
    platform: str
    post_at: dt.datetime
    optional: bool = False
    content_id: Optional[str] = None
    status: str = STATUS_OPEN
    skip_reason: Optional[str] = None

    def to_row(self, now: dt.datetime) -> Dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "slot_date": self.slot_date,
            "weekday": self.weekday,
            "pillar": self.pillar,
            "platform": self.platform,
            "post_at": self.post_at,
            "content_id": self.content_id,
            "status": self.status,
            "skip_reason": self.skip_reason,
            "created_at": now,
        }


@dataclass
class PlanResult:
    slots: List[Slot] = field(default_factory=list)

    @property
    def filled(self) -> List[Slot]:
        return [s for s in self.slots if s.status == STATUS_FILLED]

    @property
    def skipped(self) -> List[Slot]:
        return [s for s in self.slots if s.status == STATUS_SKIPPED]


def plan_week(config: AppConfig, monday: dt.date) -> List[Slot]:
    """Build the empty slot grid for the week beginning ``monday``."""
    if monday.weekday() != 0:
        raise ValueError("plan_week expects a Monday, got %s" % monday.isoformat())

    slots: List[Slot] = []
    for offset, weekday in enumerate(WEEKDAYS):
        day = monday + dt.timedelta(days=offset)
        for entry in config.cadence.plan_for(weekday):
            pillar = str(entry["pillar"])
            post_at_str = str(entry.get("post_at", "09:00"))
            hour, minute = (int(p) for p in post_at_str.split(":", 1))
            for platform in entry.get("platforms", []):
                slots.append(
                    Slot(
                        slot_id="%s|%s|%s" % (day.isoformat(), pillar, platform),
                        slot_date=day,
                        weekday=weekday,
                        pillar=pillar,
                        platform=str(platform),
                        post_at=dt.datetime.combine(
                            day, dt.time(hour, minute), tzinfo=dt.timezone.utc
                        ),
                        optional=bool(entry.get("optional")),
                    )
                )
    return slots


@dataclass
class Candidate:
    item: ContentItem
    variants: Dict[str, PlatformVariant]


def fill_slots(
    conn: Connection,
    config: AppConfig,
    slots: Sequence[Slot],
    candidates: Sequence[Candidate],
    now: dt.datetime,
) -> PlanResult:
    """Assign approved content to slots, skipping rather than settling."""
    limits = config.cadence.limits
    max_per_day = int(limits.get("max_posts_per_day", 99))
    max_per_week = int(limits.get("max_posts_per_week", 99))
    min_gap_hours = int(limits.get("min_hours_between_posts_same_platform", 0))

    # content_id -> the date it was assigned to.
    #
    # One content object is *meant* to reach several platforms on its day: that
    # is the entire reason it carries per-platform variants. What must not
    # happen is the same idea running again on a different day. So the guard is
    # scoped to a date rather than to the week.
    assigned_date: Dict[str, dt.date] = {}
    per_day: Dict[dt.date, int] = {}
    week_total = 0
    last_post_on_platform: Dict[str, dt.datetime] = {}

    result = PlanResult()

    for slot in sorted(slots, key=lambda s: (s.post_at, s.platform)):
        if week_total >= max_per_week:
            _skip(slot, SKIP_WEEKLY_LIMIT)
            result.slots.append(slot)
            continue
        if per_day.get(slot.slot_date, 0) >= max_per_day:
            _skip(slot, SKIP_DAILY_LIMIT)
            result.slots.append(slot)
            continue

        previous = last_post_on_platform.get(slot.platform)
        if previous is not None and min_gap_hours:
            gap = (slot.post_at - previous).total_seconds() / 3600.0
            if gap < min_gap_hours:
                _skip(slot, SKIP_SPACING)
                result.slots.append(slot)
                continue

        chosen, reason = _choose(conn, config, slot, candidates, assigned_date, now)
        if chosen is None:
            _skip(slot, reason or SKIP_NO_CANDIDATE)
            result.slots.append(slot)
            continue

        slot.content_id = chosen.item.content_id
        slot.status = STATUS_FILLED
        assigned_date[chosen.item.content_id] = slot.slot_date
        per_day[slot.slot_date] = per_day.get(slot.slot_date, 0) + 1
        week_total += 1
        last_post_on_platform[slot.platform] = slot.post_at
        result.slots.append(slot)

    return result


def _choose(
    conn: Connection,
    config: AppConfig,
    slot: Slot,
    candidates: Sequence[Candidate],
    assigned_date: Dict[str, dt.date],
    now: dt.datetime,
):
    eligibility = config.cadence.slot_eligibility
    min_chars = config.cadence.min_body_chars(slot.platform)
    reason: Optional[str] = None

    for candidate in candidates:
        item = candidate.item
        # Already running on another day: that would be republishing the same
        # idea. Already running on *this* day is fine and expected, because a
        # LinkedIn variant and an Instagram variant are different posts.
        prior = assigned_date.get(item.content_id)
        if prior is not None and prior != slot.slot_date:
            continue
        if item.pillar != slot.pillar:
            continue
        if eligibility.get("require_classification", True) and not item.is_classified:
            continue
        if eligibility.get("require_approved", True) and item.approval_status != APPROVED:
            continue

        variant = candidate.variants.get(slot.platform)
        if variant is None:
            reason = reason or SKIP_NO_VARIANT
            continue
        if min_chars and len(variant.body) < min_chars:
            reason = SKIP_TOO_SHORT
            continue
        if _recently_published(conn, config, slot.platform, variant, now):
            reason = SKIP_DUPLICATE
            continue

        return candidate, None

    return None, reason


def _recently_published(
    conn: Connection,
    config: AppConfig,
    platform: str,
    variant: PlatformVariant,
    now: dt.datetime,
) -> bool:
    from ..content.store import find_duplicate_variant

    since = now - dt.timedelta(days=config.policy.duplicate_lookback_days)
    duplicate = find_duplicate_variant(
        conn,
        platform=platform,
        body_fingerprint=variant.body_fingerprint,
        since=since,
        exclude_content_id=variant.content_id,
    )
    if duplicate is None:
        return False

    # Only a duplicate that actually reached a platform blocks a slot; an
    # unpublished draft sharing copy is a drafting problem, not a repeat post.
    published = conn.execute(
        select(publications.c.publication_id)
        .where(
            and_(
                publications.c.variant_id == duplicate["variant_id"],
                publications.c.status.in_(("PUBLISHED", "SCHEDULED")),
            )
        )
        .limit(1)
    ).first()
    return published is not None


def _skip(slot: Slot, reason: str) -> None:
    slot.status = STATUS_SKIPPED
    slot.skip_reason = reason


def persist(conn: Connection, slots: Sequence[Slot], now: dt.datetime) -> None:
    for slot in slots:
        existing = conn.execute(
            select(schedule_slots.c.slot_id).where(schedule_slots.c.slot_id == slot.slot_id)
        ).first()
        row = slot.to_row(now)
        if existing:
            row.pop("slot_id")
            row.pop("created_at")
            conn.execute(
                schedule_slots.update()
                .where(schedule_slots.c.slot_id == slot.slot_id)
                .values(**row)
            )
        else:
            conn.execute(schedule_slots.insert().values(**row))


def slots_for_week(conn: Connection, monday: dt.date) -> List[Dict[str, Any]]:
    sunday = monday + dt.timedelta(days=6)
    rows = (
        conn.execute(
            select(schedule_slots)
            .where(schedule_slots.c.slot_date >= monday)
            .where(schedule_slots.c.slot_date <= sunday)
            .order_by(schedule_slots.c.post_at)
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]
