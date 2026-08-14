"""Shared fixtures for the marketing engine."""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

import pytest

from app.ai.drafting import DraftingProvider, DraftRequest, DraftResponse, DraftingService
from app.clock import FrozenClock
from app.config import load_config
from app.content.evidence import EvidenceRecord, evidence_id_for, record_evidence
from app.content.item import (
    AUTO_APPROVED,
    HUMAN_APPROVAL_REQUIRED,
    PUBLIC,
    PUBLIC_AFTER_APPROVAL,
    ContentItem,
)
from app.db import create_all, make_engine
from app.publisher.base import (
    STATUS_FAILED,
    STATUS_SCHEDULED,
    MetricsSample,
    PublishRequest,
    PublishResult,
)

UTC = dt.timezone.utc

TODAY = dt.date(2026, 8, 17)          # a Monday
NOW = dt.datetime(2026, 8, 17, 7, 0, tzinfo=UTC)

CORE_EDUCATION = (
    "Security operations modernisation is less about buying another product and "
    "more about connecting the tools an organisation already owns, with a person "
    "still accountable for every automated action. Teams that start by mapping "
    "their existing handoffs get further than teams that start by shopping."
)


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def engine():
    eng = make_engine("sqlite+pysqlite:///:memory:")
    create_all(eng)
    return eng


@pytest.fixture
def clock():
    return FrozenClock(NOW)


def make_item(
    content_id: str = "MKT-2026-00001",
    pillar: str = "cybersecurity_education",
    disclosure: Optional[str] = PUBLIC,
    approval_requirement: str = AUTO_APPROVED,
    core_message: str = CORE_EDUCATION,
    topic: str = "SOC modernisation",
    campaign: str = "RDX Authority Building",
    **overrides: Any,
) -> ContentItem:
    item = ContentItem(
        content_id=content_id,
        campaign=campaign,
        pillar=pillar,
        topic=topic,
        core_message=core_message,
        origin="manual",
        approval_requirement=approval_requirement,
        disclosure_class=disclosure,
        target_audience="security leaders",
        cta="More at rdxenterprise.com",
    )
    for key, value in overrides.items():
        setattr(item, key, value)
    return item


def seed_evidence(conn, content_id: str, now: dt.datetime, config) -> str:
    from app.content.evidence import link_claim

    record = EvidenceRecord(
        evidence_id=evidence_id_for("CERTIFICATE", "sba.gov/profile", "SDVOSB certification"),
        kind="CERTIFICATE",
        summary="SBA certification record showing RDX as a certified SDVOSB",
        recorded_by="human:william.farrell",
        max_disclosure=PUBLIC,
        locator="sba.gov/profile",
    )
    evidence_id = record_evidence(
        conn, record, now, config.policy.accepted_evidence_kinds
    )
    link_claim(conn, content_id, evidence_id, "RDX is a certified SDVOSB", now)
    return evidence_id


class FakePublisher:
    """Records what it was asked to do. Transmits nothing."""

    name = "fake"

    def __init__(self, fail_times: int = 0, retryable: bool = True) -> None:
        self.requests: List[PublishRequest] = []
        self.fail_times = fail_times
        self.retryable = retryable
        self._metrics: Dict[str, MetricsSample] = {}

    def publish(self, request: PublishRequest) -> PublishResult:
        self.requests.append(request)
        if self.fail_times > 0:
            self.fail_times -= 1
            return PublishResult(
                status=STATUS_FAILED,
                provider=self.name,
                error_message="provider unavailable",
                retryable=self.retryable,
            )
        post_id = "post-%d" % len(self.requests)
        return PublishResult(
            status=STATUS_SCHEDULED,
            provider=self.name,
            provider_post_id=post_id,
            permalink="https://example.test/%s" % post_id,
        )

    def set_metrics(self, provider_post_id: str, sample: MetricsSample) -> None:
        self._metrics[provider_post_id] = sample

    def fetch_metrics(self, provider_post_id: str, platform: str) -> Optional[MetricsSample]:
        return self._metrics.get(provider_post_id)


class RecordingDraftProvider:
    name = "recording"
    model = "test-model"

    def __init__(self) -> None:
        self.calls: List[DraftRequest] = []

    def draft(self, request: DraftRequest) -> DraftResponse:
        self.calls.append(request)
        return DraftResponse(
            text="Model written copy about security operations.",
            provider=self.name,
            model=self.model,
            tokens_in=100,
            tokens_out=80,
            cost_usd=0.02,
        )


@pytest.fixture
def publisher():
    return FakePublisher()


@pytest.fixture
def drafting(config):
    return DraftingService(config)
