"""Evidence records.

The rule this module enforces: **a generated statement is never its own
evidence.** Marketing copy about an RDX capability, certification, partnership,
or customer outcome has to point at something that existed before the copy did.

Accepted kinds are fixed in ``config/policy.yaml`` and none of them is a
generated artefact. :func:`record_evidence` rejects anything whose kind is not
on that list, and :func:`link_claim` refuses to treat a content item or a model
output as a source.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.engine import Connection

from ..models import content_evidence, evidence_records

KIND_EXTERNAL_DOCUMENT = "EXTERNAL_DOCUMENT"
KIND_PUBLIC_URL = "PUBLIC_URL"
KIND_INTERNAL_RECORD = "INTERNAL_RECORD"
KIND_HUMAN_ATTESTATION = "HUMAN_ATTESTATION"
KIND_CERTIFICATE = "CERTIFICATE"

#: Kinds that are explicitly *not* evidence, listed so the rejection message can
#: say what was actually wrong.
GENERATED_KINDS = frozenset(
    {"GENERATED", "AI_OUTPUT", "DRAFT_COPY", "CONTENT_ITEM", "MODEL_RESPONSE"}
)


class NotEvidence(ValueError):
    """Raised when something generated is offered as its own source."""


@dataclass
class EvidenceRecord:
    evidence_id: str
    kind: str
    summary: str
    recorded_by: str
    max_disclosure: str
    locator: Optional[str] = None
    verified_at: Optional[dt.datetime] = None
    verified_by: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)


def evidence_id_for(kind: str, locator: str, summary: str) -> str:
    seed = "%s|%s|%s" % (kind, locator or "", summary)
    return "EV-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def record_evidence(
    conn: Connection,
    record: EvidenceRecord,
    now: dt.datetime,
    accepted_kinds: List[str],
) -> str:
    """Persist an evidence record, rejecting anything generated."""
    if record.kind in GENERATED_KINDS:
        raise NotEvidence(
            "%s is a generated artefact and cannot be evidence for itself" % record.kind
        )
    if record.kind not in accepted_kinds:
        raise NotEvidence(
            "evidence kind %r is not accepted; accepted kinds are %s"
            % (record.kind, ", ".join(accepted_kinds))
        )
    if not record.summary.strip():
        raise NotEvidence("evidence requires a summary of what the source shows")
    if not record.recorded_by.strip():
        raise NotEvidence("evidence requires an attributed recorder")

    existing = conn.execute(
        select(evidence_records.c.evidence_id).where(
            evidence_records.c.evidence_id == record.evidence_id
        )
    ).first()
    if existing:
        return record.evidence_id

    conn.execute(
        evidence_records.insert().values(
            evidence_id=record.evidence_id,
            created_at=now,
            kind=record.kind,
            summary=record.summary,
            locator=record.locator,
            recorded_by=record.recorded_by,
            verified_at=record.verified_at,
            verified_by=record.verified_by,
            max_disclosure=record.max_disclosure,
            detail=record.detail,
        )
    )
    return record.evidence_id


def link_claim(
    conn: Connection, content_id: str, evidence_id: str, claim: str, now: dt.datetime
) -> None:
    """Attach a specific claim in a content item to a specific evidence record."""
    if evidence_id.startswith("MKT-"):
        raise NotEvidence(
            "content item %s cannot be evidence for content item %s" % (evidence_id, content_id)
        )
    row = conn.execute(
        select(evidence_records.c.evidence_id).where(
            evidence_records.c.evidence_id == evidence_id
        )
    ).first()
    if row is None:
        raise NotEvidence("no evidence record %r; record it before linking a claim" % evidence_id)

    already = conn.execute(
        select(content_evidence.c.id)
        .where(content_evidence.c.content_id == content_id)
        .where(content_evidence.c.evidence_id == evidence_id)
        .where(content_evidence.c.claim == claim)
    ).first()
    if already:
        return

    conn.execute(
        content_evidence.insert().values(
            content_id=content_id,
            evidence_id=evidence_id,
            claim=claim,
            linked_at=now,
        )
    )


def evidence_for(conn: Connection, content_id: str) -> List[Dict[str, Any]]:
    rows = (
        conn.execute(
            select(
                content_evidence.c.claim,
                evidence_records.c.evidence_id,
                evidence_records.c.kind,
                evidence_records.c.summary,
                evidence_records.c.locator,
                evidence_records.c.max_disclosure,
                evidence_records.c.verified_at,
            )
            .select_from(
                content_evidence.join(
                    evidence_records,
                    content_evidence.c.evidence_id == evidence_records.c.evidence_id,
                )
            )
            .where(content_evidence.c.content_id == content_id)
            .order_by(content_evidence.c.id)
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def has_evidence(conn: Connection, content_id: str) -> bool:
    return bool(evidence_for(conn, content_id))
