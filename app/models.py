"""Canonical schema for the RDX Marketing Engine.

Two architectural rules are enforced by the shape of these tables:

* **Content creation and content publishing are separate authorities.** A row in
  ``content_items`` is a draft and nothing more. Nothing in it can authorise its
  own publication: that lives in ``approvals``, written by a human or by an
  explicitly configured auto-approval rule, and in ``publications``, which
  records what a publisher actually did.

* **A generated statement is never its own evidence.** ``evidence_records`` is a
  separate table with its own provenance, and ``content_evidence`` links claims
  to it. Nothing produced by the drafting layer can be inserted as evidence;
  see ``app/content/evidence.py``.

Logical separation from the opportunity-intelligence database is deliberate. The
two systems connect through ``marketing_events`` — controlled, sanitised
signals — not through cross-table reads.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

from .types import JsonDoc, LongText, UtcDateTime

metadata = MetaData()


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #

evidence_records = Table(
    "evidence_records",
    metadata,
    Column("evidence_id", String(64), primary_key=True),
    Column("created_at", UtcDateTime, nullable=False),
    # EXTERNAL_DOCUMENT | PUBLIC_URL | INTERNAL_RECORD | HUMAN_ATTESTATION |
    # CERTIFICATE. Never a generated artefact.
    Column("kind", String(32), nullable=False),
    Column("summary", LongText, nullable=False),
    Column("locator", LongText),
    Column("recorded_by", String(128), nullable=False),
    Column("verified_at", UtcDateTime),
    Column("verified_by", String(128)),
    # Disclosure ceiling for anything relying on this evidence.
    Column("max_disclosure", String(32), nullable=False),
    Column("detail", JsonDoc),
    Index("ix_evidence_kind", "kind"),
)


# --------------------------------------------------------------------------- #
# Content
# --------------------------------------------------------------------------- #

content_items = Table(
    "content_items",
    metadata,
    Column("content_id", String(32), primary_key=True),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column("campaign", String(128), nullable=False),
    Column("pillar", String(64), nullable=False),
    Column("topic", String(256), nullable=False),
    Column("core_message", LongText, nullable=False),
    Column("target_audience", String(256)),
    Column("cta", LongText),
    Column("media_requirement", String(64), nullable=False, default="NONE"),
    # PUBLIC | PUBLIC_AFTER_APPROVAL | MARKETING_SAFE_SUMMARY | INTERNAL_ONLY |
    # CONFIDENTIAL | NEVER_PUBLISH. Nullable on purpose: an unclassified item
    # must be representable, and must fail closed at the publish boundary.
    Column("disclosure_class", String(32)),
    Column("classified_by", String(128)),
    Column("classified_at", UtcDateTime),
    # AUTO_APPROVED | HUMAN_APPROVAL_REQUIRED | NEVER_PUBLISH
    Column("approval_requirement", String(32), nullable=False),
    # DRAFT | PENDING_APPROVAL | APPROVED | REJECTED | WITHDRAWN
    Column("approval_status", String(32), nullable=False, default="DRAFT"),
    # DRAFT | SCHEDULED | PUBLISHED | PARTIALLY_PUBLISHED | FAILED | BLOCKED
    Column("lifecycle_status", String(32), nullable=False, default="DRAFT"),
    Column("origin", String(64), nullable=False),  # calendar | event | manual
    Column("origin_reference", String(128)),
    Column("notes", JsonDoc),
    Index("ix_content_pillar", "pillar"),
    Index("ix_content_lifecycle", "lifecycle_status"),
)

content_evidence = Table(
    "content_evidence",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("content_id", String(32), ForeignKey("content_items.content_id"), nullable=False),
    Column("evidence_id", String(64), ForeignKey("evidence_records.evidence_id"), nullable=False),
    Column("claim", LongText, nullable=False),
    Column("linked_at", UtcDateTime, nullable=False),
    UniqueConstraint("content_id", "evidence_id", "claim", name="uq_content_evidence"),
)

platform_variants = Table(
    "platform_variants",
    metadata,
    Column("variant_id", String(48), primary_key=True),
    Column("content_id", String(32), ForeignKey("content_items.content_id"), nullable=False),
    Column("platform", String(32), nullable=False),  # linkedin | facebook | instagram
    Column("post_type", String(32), nullable=False),  # post | carousel | reel | story
    Column("body", LongText, nullable=False),
    Column("first_comment", LongText),
    Column("hashtags", JsonDoc, nullable=False, default=list),
    Column("cta", LongText),
    Column("media_ids", JsonDoc, nullable=False, default=list),
    Column("hook_type", String(48)),
    Column("body_fingerprint", String(64), nullable=False),
    Column("generated_by", String(64), nullable=False),  # template | ai:<model>
    Column("created_at", UtcDateTime, nullable=False),
    UniqueConstraint("content_id", "platform", name="uq_variant_per_platform"),
    Index("ix_variant_fingerprint", "platform", "body_fingerprint"),
)


# --------------------------------------------------------------------------- #
# Approval
# --------------------------------------------------------------------------- #

approvals = Table(
    "approvals",
    metadata,
    Column("approval_id", Integer, primary_key=True, autoincrement=True),
    Column("content_id", String(32), ForeignKey("content_items.content_id"), nullable=False),
    Column("requested_at", UtcDateTime, nullable=False),
    Column("decided_at", UtcDateTime),
    # PENDING | APPROVED | REJECTED
    Column("decision", String(24), nullable=False, default="PENDING"),
    # human:<name> | rule:<rule_id>. Never a model.
    Column("decided_by", String(128)),
    Column("rationale", LongText),
    Column("policy_snapshot", JsonDoc, nullable=False),
    Index("ix_approval_decision", "decision"),
)

policy_decisions = Table(
    "policy_decisions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("content_id", String(32), ForeignKey("content_items.content_id"), nullable=False),
    Column("evaluated_at", UtcDateTime, nullable=False),
    Column("policy_version", String(64), nullable=False),
    Column("publish_allowed", Boolean, nullable=False),
    Column("requires_approval", Boolean, nullable=False),
    Column("blocking_reasons", JsonDoc, nullable=False),
    Column("advisory_reasons", JsonDoc, nullable=False),
)


# --------------------------------------------------------------------------- #
# Scheduling and publication
# --------------------------------------------------------------------------- #

schedule_slots = Table(
    "schedule_slots",
    metadata,
    Column("slot_id", String(96), primary_key=True),
    Column("slot_date", Date, nullable=False),
    Column("weekday", String(16), nullable=False),
    # The pillar this slot actually resolved to. For a shared slot this is
    # decided at fill time; until then it is the first editorial preference.
    Column("pillar", String(64), nullable=False),
    # Candidate pillars in editorial priority order. A slot may be shared
    # between subjects, taking whichever has approved content waiting.
    Column("candidate_pillars", JsonDoc, nullable=False, default=list),
    Column("platform", String(32), nullable=False),
    Column("post_at", UtcDateTime, nullable=False),
    Column("content_id", String(32), ForeignKey("content_items.content_id")),
    # OPEN | FILLED | SKIPPED_NO_QUALIFIED_CONTENT
    Column("status", String(40), nullable=False, default="OPEN"),
    Column("skip_reason", LongText),
    Column("created_at", UtcDateTime, nullable=False),
    # Identity is the slot_id, which is built from the candidate list and so is
    # stable regardless of which pillar the slot resolves to.
    Index("ix_slot_date", "slot_date"),
    Index("ix_slot_day_platform", "slot_date", "platform"),
)

publications = Table(
    "publications",
    metadata,
    Column("publication_id", String(64), primary_key=True),
    Column("content_id", String(32), ForeignKey("content_items.content_id"), nullable=False),
    Column("variant_id", String(48), ForeignKey("platform_variants.variant_id"), nullable=False),
    Column("platform", String(32), nullable=False),
    Column("provider", String(32), nullable=False),  # buffer | ...
    Column("provider_post_id", String(128)),
    Column("scheduled_for", UtcDateTime),
    Column("published_at", UtcDateTime),
    # QUEUED | SCHEDULED | PUBLISHED | FAILED | CANCELLED
    Column("status", String(24), nullable=False, default="QUEUED"),
    Column("attempts", Integer, nullable=False, default=0),
    Column("last_error", LongText),
    Column("retryable", Boolean, nullable=False, default=True),
    Column("permalink", LongText),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    UniqueConstraint("variant_id", name="uq_publication_per_variant"),
    Index("ix_publication_status", "status"),
)

#: Append-only. Nothing here is ever updated or deleted, so the history of what
#: RDX published, when, and on whose authority stays auditable.
publication_events = Table(
    "publication_events",
    metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column("publication_id", String(64), nullable=False),
    Column("content_id", String(32), nullable=False),
    Column("occurred_at", UtcDateTime, nullable=False),
    Column("event_type", String(40), nullable=False),
    Column("actor", String(128), nullable=False),
    Column("detail", JsonDoc, nullable=False),
    Index("ix_pub_event_content", "content_id", "occurred_at"),
)


# --------------------------------------------------------------------------- #
# Media
# --------------------------------------------------------------------------- #

media_assets = Table(
    "media_assets",
    metadata,
    Column("media_id", String(48), primary_key=True),
    Column("kind", String(32), nullable=False),  # logo | graphic | photo | video | template
    Column("label", String(256), nullable=False),
    Column("locator", LongText, nullable=False),
    Column("provenance", String(256), nullable=False),
    Column("usage_rights", String(256), nullable=False),
    Column("rights_expire_on", Date),
    Column("people_depicted", JsonDoc, nullable=False, default=list),
    Column("release_on_file", Boolean, nullable=False, default=False),
    Column("added_at", UtcDateTime, nullable=False),
    Column("last_used_at", UtcDateTime),
    Column("use_count", Integer, nullable=False, default=0),
)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #

post_metrics = Table(
    "post_metrics",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("publication_id", String(64), ForeignKey("publications.publication_id"), nullable=False),
    Column("collected_at", UtcDateTime, nullable=False),
    Column("platform", String(32), nullable=False),
    Column("impressions", Integer),
    Column("reach", Integer),
    Column("reactions", Integer),
    Column("comments", Integer),
    Column("shares", Integer),
    Column("clicks", Integer),
    Column("engagement_rate", Float),
    # Denormalised attribution so the weekly report is one query.
    Column("campaign", String(128)),
    Column("pillar", String(64)),
    Column("topic", String(256)),
    Column("cta", LongText),
    Column("asset_type", String(32)),
    Column("hook_type", String(48)),
    Column("posted_at", UtcDateTime),
    Column("raw", JsonDoc),
    UniqueConstraint("publication_id", "collected_at", name="uq_metric_sample"),
    Index("ix_metrics_pillar", "pillar"),
)


# --------------------------------------------------------------------------- #
# Inbound marketing events (including the CaptureOS bridge)
# --------------------------------------------------------------------------- #

marketing_events = Table(
    "marketing_events",
    metadata,
    Column("event_id", String(64), primary_key=True),
    Column("received_at", UtcDateTime, nullable=False),
    Column("source_system", String(64), nullable=False),
    Column("event_type", String(64), nullable=False),
    Column("payload", JsonDoc, nullable=False),
    # ACCEPTED | REJECTED_UNSANITISED | REJECTED_UNKNOWN_TYPE | CONVERTED
    Column("intake_status", String(40), nullable=False),
    Column("rejection_reason", LongText),
    Column("content_id", String(32), ForeignKey("content_items.content_id")),
    Index("ix_event_source", "source_system", "received_at"),
)


# --------------------------------------------------------------------------- #
# AI usage ledger
# --------------------------------------------------------------------------- #

ai_usage = Table(
    "ai_usage",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("content_id", String(32)),
    Column("created_at", UtcDateTime, nullable=False),
    Column("task", String(48), nullable=False),  # draft | hooks | adapt | shorten
    Column("provider", String(48), nullable=False),
    Column("model", String(128), nullable=False),
    Column("tokens_in", Integer, nullable=False, default=0),
    Column("tokens_out", Integer, nullable=False, default=0),
    Column("cost_usd", Float, nullable=False, default=0.0),
    Column("status", String(32), nullable=False),  # OK | BUDGET_EXCEEDED | ERROR | DISABLED
    Column("error_message", LongText),
    Index("ix_ai_usage_day", "created_at"),
)


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #

runs = Table(
    "runs",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("kind", String(32), nullable=False),
    Column("started_at", UtcDateTime, nullable=False),
    Column("finished_at", UtcDateTime),
    Column("business_date", Date, nullable=False),
    Column("status", String(32), nullable=False),
    Column("policy_version", String(64), nullable=False),
    Column("slots_considered", Integer, nullable=False, default=0),
    Column("slots_filled", Integer, nullable=False, default=0),
    Column("slots_skipped", Integer, nullable=False, default=0),
    Column("published", Integer, nullable=False, default=0),
    Column("blocked", Integer, nullable=False, default=0),
    Column("failed", Integer, nullable=False, default=0),
    Column("ai_calls", Integer, nullable=False, default=0),
    Column("ai_cost_usd", Float, nullable=False, default=0.0),
    Column("notes", JsonDoc),
)


ALL_TABLES = tuple(metadata.sorted_tables)
