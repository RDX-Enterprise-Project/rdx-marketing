"""The weekly marketing report.

Deterministic, like the rest of the engine. It is built from stored metrics,
publications, slots, and the approval queue, so every number in it can be traced
to a row.

One discipline enforced in the wording: performance is reported, and it informs
what RDX writes next. It never widens what RDX is allowed to say. There is no
path from "this topic performed well" to a looser disclosure classification.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.engine import Connection

from ..approval.queue import pending as pending_approvals
from ..calendar.planner import STATUS_FILLED, STATUS_SKIPPED, slots_for_week
from ..capture_bridge import rejected_events
from ..metrics.collector import metrics_between
from ..models import content_items, publications


@dataclass
class WeeklyReport:
    week_start: dt.date
    title: str
    body_markdown: str
    summary: Dict[str, Any] = field(default_factory=dict)


def build(
    conn: Connection,
    week_start: dt.date,
    now: dt.datetime,
    lookback_days: int = 7,
) -> WeeklyReport:
    week_end = week_start + dt.timedelta(days=6)
    window_start = now - dt.timedelta(days=lookback_days)

    samples = metrics_between(conn, window_start, now)
    slots = slots_for_week(conn, week_start)
    filled = [s for s in slots if s["status"] == STATUS_FILLED]
    skipped = [s for s in slots if s["status"] == STATUS_SKIPPED]
    queue = pending_approvals(conn)
    failures = _failed_publications(conn)
    backlog = _backlog(conn)
    rejected = rejected_events(conn)

    by_pillar = _aggregate(samples, "pillar")
    by_platform = _aggregate(samples, "platform")
    ranked = sorted(samples, key=lambda s: _engagement(s), reverse=True)

    summary = {
        "week_start": week_start.isoformat(),
        "posts_published": len([s for s in slots if s["status"] == STATUS_FILLED]),
        "slots_planned": len(slots),
        "slots_skipped": len(skipped),
        "metrics_samples": len(samples),
        "pending_approvals": len(queue),
        "failed_publications": len(failures),
        "content_backlog": backlog,
        "rejected_capture_events": len(rejected),
    }

    lines: List[str] = []
    title = "RDX Marketing Week — %s to %s" % (week_start.isoformat(), week_end.isoformat())
    lines.append("# %s" % title)
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | ---: |")
    lines.append("| Slots planned | %d |" % summary["slots_planned"])
    lines.append("| Slots filled | %d |" % summary["posts_published"])
    lines.append("| Slots skipped for want of qualified content | %d |" % summary["slots_skipped"])
    lines.append("| Posts with metrics this window | %d |" % summary["metrics_samples"])
    lines.append("| Awaiting approval | %d |" % summary["pending_approvals"])
    lines.append("| Failed publications | %d |" % summary["failed_publications"])
    lines.append("| Approved content not yet scheduled | %d |" % summary["content_backlog"])
    lines.append("")

    lines.extend(_best_and_weakest(ranked))
    lines.extend(_table("Content pillars", by_pillar))
    lines.extend(_table("Platforms", by_platform))
    lines.extend(_consistency(slots, filled, skipped))
    lines.extend(_queue_section(queue))
    lines.extend(_failure_section(failures))

    if rejected:
        lines.append("## Capture boundary")
        lines.append("")
        lines.append(
            "%d inbound event(s) from CaptureOS were rejected as unsanitised and did not "
            "reach marketing." % len(rejected)
        )
        lines.append("")

    lines.append(
        "_Built from stored metrics and publication records. Performance informs what "
        "gets written next; it does not change what may be disclosed._"
    )

    return WeeklyReport(
        week_start=week_start,
        title=title,
        body_markdown="\n".join(lines).rstrip() + "\n",
        summary=summary,
    )


# --------------------------------------------------------------------------- #


def _engagement(sample: Dict[str, Any]) -> int:
    return sum(
        int(sample.get(k) or 0) for k in ("reactions", "comments", "shares", "clicks")
    )


def _reach(sample: Dict[str, Any]) -> int:
    """Best available "how many saw it", per platform.

    LinkedIn supplies views and reach but no impressions; ranking on
    impressions alone would show every LinkedIn post as zero.
    """
    for key in ("impressions", "views", "reach"):
        value = sample.get(key)
        if value:
            return int(value)
    return 0


def _aggregate(samples: Sequence[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for sample in samples:
        name = sample.get(key) or "(unattributed)"
        bucket = buckets.setdefault(
            name, {"name": name, "posts": 0, "impressions": 0, "engagement": 0}
        )
        bucket["posts"] += 1
        bucket["impressions"] += _reach(sample)
        bucket["engagement"] += _engagement(sample)

    rows = []
    for bucket in buckets.values():
        posts = bucket["posts"] or 1
        bucket["avg_impressions"] = round(bucket["impressions"] / posts)
        bucket["avg_engagement"] = round(bucket["engagement"] / posts, 1)
        rows.append(bucket)
    return sorted(rows, key=lambda b: b["avg_impressions"], reverse=True)


def _table(heading: str, rows: Sequence[Dict[str, Any]]) -> List[str]:
    lines = ["## %s" % heading, ""]
    if not rows:
        lines += ["No metrics collected in this window.", ""]
        return lines
    lines.append("| %s | Posts | Avg reached | Avg engagement |" % heading.rstrip("s"))
    lines.append("| --- | ---: | ---: | ---: |")
    for row in rows:
        lines.append(
            "| %s | %d | %d | %.1f |"
            % (row["name"], row["posts"], row["avg_impressions"], row["avg_engagement"])
        )
    lines.append("")
    return lines


def _best_and_weakest(ranked: Sequence[Dict[str, Any]]) -> List[str]:
    lines = ["## Best and weakest posts", ""]
    if not ranked:
        lines += ["No posts carried metrics in this window.", ""]
        return lines

    lines.append("**Best**")
    lines.append("")
    for sample in ranked[:3]:
        lines.append(
            "- %s / %s — %d reached, %d engagements"
            % (
                sample.get("pillar") or "(unattributed)",
                sample.get("topic") or "(no topic)",
                _reach(sample),
                _engagement(sample),
            )
        )
    lines.append("")

    if len(ranked) > 3:
        lines.append("**Weakest**")
        lines.append("")
        for sample in ranked[-3:]:
            lines.append(
                "- %s / %s — %d reached, %d engagements"
                % (
                    sample.get("pillar") or "(unattributed)",
                    sample.get("topic") or "(no topic)",
                    _reach(sample),
                    _engagement(sample),
                )
            )
        lines.append("")
    return lines


def _consistency(slots, filled, skipped) -> List[str]:
    lines = ["## Publishing consistency", ""]
    if not slots:
        lines += ["No calendar was planned for this week.", ""]
        return lines

    lines.append(
        "%d of %d planned slots were filled." % (len(filled), len(slots))
    )
    lines.append("")
    if skipped:
        lines.append(
            "Skipped slots are recorded rather than filled with weaker material:"
        )
        lines.append("")
        for slot in skipped:
            lines.append(
                "- %s %s / %s — %s"
                % (
                    slot["slot_date"],
                    slot["weekday"],
                    slot["platform"],
                    slot.get("skip_reason") or "no qualified content",
                )
            )
        lines.append("")
    return lines


def _queue_section(queue) -> List[str]:
    lines = ["## Awaiting approval", ""]
    if not queue:
        lines += ["Nothing is waiting on a decision.", ""]
        return lines
    for entry in queue:
        item = entry.item
        lines.append(
            "- %s — %s / %s (queued %s)"
            % (
                entry.content_id,
                item.pillar if item else "?",
                item.topic if item else "?",
                entry.requested_at.date().isoformat(),
            )
        )
    lines.append("")
    return lines


def _failure_section(failures) -> List[str]:
    lines = ["## Failed publications", ""]
    if not failures:
        lines += ["None. Nothing approved is stuck.", ""]
        return lines
    for row in failures:
        lines.append(
            "- %s on %s — %d attempt(s); %s%s"
            % (
                row["content_id"],
                row["platform"],
                int(row["attempts"] or 0),
                (row.get("last_error") or "no error recorded")[:160],
                " (retryable)" if row.get("retryable") else " (not retryable)",
            )
        )
    lines.append("")
    return lines


def _failed_publications(conn: Connection) -> List[Dict[str, Any]]:
    rows = (
        conn.execute(
            select(publications)
            .where(publications.c.status == "FAILED")
            .order_by(publications.c.updated_at)
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def _backlog(conn: Connection) -> int:
    rows = conn.execute(
        select(content_items.c.content_id)
        .where(content_items.c.approval_status == "APPROVED")
        .where(content_items.c.lifecycle_status == "DRAFT")
    ).all()
    return len(rows)
