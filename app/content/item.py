"""The canonical content object.

One marketing idea is one :class:`ContentItem`. Platform copy hangs off it as
variants, so LinkedIn, Facebook, and Instagram can read completely differently
while remaining the same tracked piece of content with one classification, one
evidence set, one approval, and one performance story.

A ``ContentItem`` carries no authority. Being well-formed, well-written, or
even approved-looking does not make it publishable — that determination is made
by :mod:`app.policy.engine` against ``config/policy.yaml``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Disclosure classes.
PUBLIC = "PUBLIC"
PUBLIC_AFTER_APPROVAL = "PUBLIC_AFTER_APPROVAL"
MARKETING_SAFE_SUMMARY = "MARKETING_SAFE_SUMMARY"
INTERNAL_ONLY = "INTERNAL_ONLY"
CONFIDENTIAL = "CONFIDENTIAL"
NEVER_PUBLISH = "NEVER_PUBLISH"

# Approval requirements.
AUTO_APPROVED = "AUTO_APPROVED"
HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
REQUIREMENT_NEVER_PUBLISH = "NEVER_PUBLISH"

# Approval statuses.
DRAFT = "DRAFT"
PENDING_APPROVAL = "PENDING_APPROVAL"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
WITHDRAWN = "WITHDRAWN"

# Lifecycle statuses.
LIFECYCLE_DRAFT = "DRAFT"
LIFECYCLE_SCHEDULED = "SCHEDULED"
LIFECYCLE_PUBLISHED = "PUBLISHED"
LIFECYCLE_PARTIAL = "PARTIALLY_PUBLISHED"
LIFECYCLE_FAILED = "FAILED"
LIFECYCLE_BLOCKED = "BLOCKED"

MEDIA_NONE = "NONE"
MEDIA_IMAGE = "IMAGE"
MEDIA_CAROUSEL = "CAROUSEL"
MEDIA_VIDEO = "VIDEO"

ORIGIN_CALENDAR = "calendar"
ORIGIN_EVENT = "event"
ORIGIN_MANUAL = "manual"


def content_id(year: int, sequence: int) -> str:
    return "MKT-%04d-%05d" % (year, sequence)


@dataclass
class ContentItem:
    content_id: str
    campaign: str
    pillar: str
    topic: str
    core_message: str
    origin: str
    approval_requirement: str
    disclosure_class: Optional[str] = None
    target_audience: Optional[str] = None
    cta: Optional[str] = None
    media_requirement: str = MEDIA_NONE
    approval_status: str = DRAFT
    lifecycle_status: str = LIFECYCLE_DRAFT
    origin_reference: Optional[str] = None
    classified_by: Optional[str] = None
    classified_at: Optional[dt.datetime] = None
    notes: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_classified(self) -> bool:
        return bool(self.disclosure_class)

    @property
    def is_approved(self) -> bool:
        return self.approval_status == APPROVED

    def to_row(self, now: dt.datetime) -> Dict[str, Any]:
        return {
            "content_id": self.content_id,
            "created_at": now,
            "updated_at": now,
            "campaign": self.campaign,
            "pillar": self.pillar,
            "topic": self.topic,
            "core_message": self.core_message,
            "target_audience": self.target_audience,
            "cta": self.cta,
            "media_requirement": self.media_requirement,
            "disclosure_class": self.disclosure_class,
            "classified_by": self.classified_by,
            "classified_at": self.classified_at,
            "approval_requirement": self.approval_requirement,
            "approval_status": self.approval_status,
            "lifecycle_status": self.lifecycle_status,
            "origin": self.origin,
            "origin_reference": self.origin_reference,
            "notes": self.notes,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "ContentItem":
        return cls(
            content_id=row["content_id"],
            campaign=row["campaign"],
            pillar=row["pillar"],
            topic=row["topic"],
            core_message=row["core_message"],
            origin=row["origin"],
            approval_requirement=row["approval_requirement"],
            disclosure_class=row.get("disclosure_class"),
            target_audience=row.get("target_audience"),
            cta=row.get("cta"),
            media_requirement=row.get("media_requirement", MEDIA_NONE),
            approval_status=row.get("approval_status", DRAFT),
            lifecycle_status=row.get("lifecycle_status", LIFECYCLE_DRAFT),
            origin_reference=row.get("origin_reference"),
            classified_by=row.get("classified_by"),
            classified_at=row.get("classified_at"),
            notes=dict(row.get("notes") or {}),
        )


@dataclass
class PlatformVariant:
    """Platform-specific copy for one content item."""

    variant_id: str
    content_id: str
    platform: str
    post_type: str
    body: str
    generated_by: str
    first_comment: Optional[str] = None
    hashtags: List[str] = field(default_factory=list)
    cta: Optional[str] = None
    media_ids: List[str] = field(default_factory=list)
    hook_type: Optional[str] = None

    @property
    def body_fingerprint(self) -> str:
        """Normalised so trivial whitespace edits do not defeat duplicate checks."""
        normalised = re.sub(r"\s+", " ", self.body.strip().lower())
        return hashlib.sha256(normalised.encode("utf-8")).hexdigest()

    def to_row(self, now: dt.datetime) -> Dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "content_id": self.content_id,
            "platform": self.platform,
            "post_type": self.post_type,
            "body": self.body,
            "first_comment": self.first_comment,
            "hashtags": list(self.hashtags),
            "cta": self.cta,
            "media_ids": list(self.media_ids),
            "hook_type": self.hook_type,
            "body_fingerprint": self.body_fingerprint,
            "generated_by": self.generated_by,
            "created_at": now,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "PlatformVariant":
        return cls(
            variant_id=row["variant_id"],
            content_id=row["content_id"],
            platform=row["platform"],
            post_type=row["post_type"],
            body=row["body"],
            generated_by=row["generated_by"],
            first_comment=row.get("first_comment"),
            hashtags=list(row.get("hashtags") or []),
            cta=row.get("cta"),
            media_ids=list(row.get("media_ids") or []),
            hook_type=row.get("hook_type"),
        )


def variant_id(content: str, platform: str) -> str:
    return "%s:%s" % (content, platform)
