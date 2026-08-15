"""The publishing gateway contract.

Buffer is the initial provider, but the engine talks only to
:class:`SocialPublisher`. Swapping to direct LinkedIn / Meta integrations later
is a new implementation of this interface, not a rewrite.

Two properties the interface guarantees regardless of provider:

* **Draft is a first-class outcome.** ``create_as_draft`` means the post is
  staged at the provider and still needs a human, so the approval control exists
  at the publishing layer as well as inside this application.
* **A provider outage never loses approved content.** A failed attempt returns a
  result, is recorded with its error, and stays retryable. Nothing is discarded.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

STATUS_QUEUED = "QUEUED"
STATUS_SCHEDULED = "SCHEDULED"
STATUS_PUBLISHED = "PUBLISHED"
STATUS_FAILED = "FAILED"
STATUS_CANCELLED = "CANCELLED"


@dataclass
class PublishRequest:
    content_id: str
    variant_id: str
    platform: str
    body: str
    post_type: str = "post"
    first_comment: Optional[str] = None
    media_ids: List[str] = field(default_factory=list)
    scheduled_for: Optional[dt.datetime] = None
    create_as_draft: bool = True
    idempotency_key: Optional[str] = None


@dataclass
class PublishResult:
    status: str
    provider: str
    provider_post_id: Optional[str] = None
    permalink: Optional[str] = None
    error_message: Optional[str] = None
    retryable: bool = True
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_SCHEDULED, STATUS_PUBLISHED, STATUS_QUEUED)


@dataclass
class MetricsSample:
    provider_post_id: str
    platform: str
    impressions: Optional[int] = None
    reach: Optional[int] = None
    reactions: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    clicks: Optional[int] = None
    #: Supplied by the provider when it computes one itself. Preferred over a
    #: derived figure, because the platform's own definition is the one the
    #: numbers elsewhere in its reporting will agree with.
    reported_engagement_rate: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def engagement(self) -> int:
        return sum(v or 0 for v in (self.reactions, self.comments, self.shares, self.clicks))

    def engagement_rate(self) -> Optional[float]:
        if self.reported_engagement_rate is not None:
            return round(self.reported_engagement_rate, 6)
        base = self.impressions or self.reach
        if not base:
            return None
        return round(self.engagement / float(base), 6)


class SocialPublisher(Protocol):
    name: str

    def publish(self, request: PublishRequest) -> PublishResult:
        ...

    def fetch_metrics(self, provider_post_id: str, platform: str) -> Optional[MetricsSample]:
        ...


class NullPublisher:
    """Used when publishing is disabled. Records intent, transmits nothing."""

    name = "null"

    def __init__(self) -> None:
        self.requests: List[PublishRequest] = []

    def publish(self, request: PublishRequest) -> PublishResult:
        self.requests.append(request)
        return PublishResult(
            status=STATUS_QUEUED,
            provider=self.name,
            error_message="publishing is disabled in config/platforms.yaml",
            retryable=True,
        )

    def fetch_metrics(self, provider_post_id: str, platform: str) -> Optional[MetricsSample]:
        return None
