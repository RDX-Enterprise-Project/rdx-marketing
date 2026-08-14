"""The publication policy engine.

This is the only thing in the application that may say "yes, this can leave the
building". It fails closed in every ambiguous case:

* no disclosure classification  -> blocked
* an unrecognised classification -> blocked
* a restricted category in the blocking bucket -> blocked
* a claim category that requires evidence, with no evidence linked -> blocked
* anything else it is not certain about -> human approval required

Nothing in the drafting layer, and no model, can reach past it. A model's output
is copy; it is never an authorisation, and ``authorise_publication`` will not
accept a model as an approver.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.engine import Connection

from ..config import AppConfig
from ..content.evidence import evidence_for
from ..content.item import (
    AUTO_APPROVED,
    APPROVED,
    HUMAN_APPROVAL_REQUIRED,
    REQUIREMENT_NEVER_PUBLISH,
    ContentItem,
    PlatformVariant,
)
from ..models import policy_decisions
from .restricted import RestrictedHit, scan, scan_phrases

MODE_APPROVAL_REQUIRED = "MODE_1_APPROVAL_REQUIRED"
MODE_TRUSTED_AUTOPUBLISH = "MODE_2_TRUSTED_AUTOPUBLISH"
MODE_MIXED = "MODE_3_MIXED"

# Blocking reason codes.
NO_CLASSIFICATION = "NO_DISCLOSURE_CLASSIFICATION"
UNKNOWN_CLASSIFICATION = "UNKNOWN_DISCLOSURE_CLASSIFICATION"
NOT_PUBLISHABLE_CLASS = "CLASSIFICATION_NOT_PUBLISHABLE"
MARKED_NEVER_PUBLISH = "MARKED_NEVER_PUBLISH"
RESTRICTED_CONTENT = "RESTRICTED_CONTENT"
PROHIBITED_PHRASE = "PROHIBITED_PHRASE"
MISSING_EVIDENCE = "CLAIM_WITHOUT_EVIDENCE"
MISSING_MEDIA = "PLATFORM_REQUIRES_MEDIA"
NO_VARIANTS = "NO_PLATFORM_VARIANTS"


class NotAuthorised(RuntimeError):
    """Raised when something tries to publish without a valid authorisation."""


@dataclass
class PolicyDecision:
    content_id: str
    publish_allowed: bool
    requires_approval: bool
    blocking_reasons: List[Dict[str, Any]] = field(default_factory=list)
    advisory_reasons: List[Dict[str, Any]] = field(default_factory=list)
    policy_version: str = ""

    @property
    def blocked(self) -> bool:
        return not self.publish_allowed

    @property
    def blocking_codes(self) -> List[str]:
        return [r["code"] for r in self.blocking_reasons]

    @property
    def advisory_codes(self) -> List[str]:
        return [r["code"] for r in self.advisory_reasons]

    def to_json(self) -> Dict[str, Any]:
        return {
            "content_id": self.content_id,
            "publish_allowed": self.publish_allowed,
            "requires_approval": self.requires_approval,
            "blocking_reasons": self.blocking_reasons,
            "advisory_reasons": self.advisory_reasons,
            "policy_version": self.policy_version,
        }


def _reason(code: str, message: str, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"code": code, "message": message, "detail": detail or {}}


def _outbound_fields(
    item: ContentItem, variants: Sequence[PlatformVariant]
) -> List[Tuple[str, str]]:
    fields: List[Tuple[str, str]] = [
        ("topic", item.topic or ""),
        ("core_message", item.core_message or ""),
        ("cta", item.cta or ""),
    ]
    for variant in variants:
        prefix = "%s.body" % variant.platform
        fields.append((prefix, variant.body or ""))
        if variant.first_comment:
            fields.append(("%s.first_comment" % variant.platform, variant.first_comment))
        if variant.cta:
            fields.append(("%s.cta" % variant.platform, variant.cta))
        for tag in variant.hashtags:
            fields.append(("%s.hashtag" % variant.platform, tag))
    return fields


def evaluate(
    config: AppConfig,
    item: ContentItem,
    variants: Sequence[PlatformVariant] = (),
    linked_evidence: Sequence[Dict[str, Any]] = (),
) -> PolicyDecision:
    policy = config.policy
    blocking: List[Dict[str, Any]] = []
    advisory: List[Dict[str, Any]] = []

    fields = _outbound_fields(item, variants)

    # 1. Classification. Absence is not permission.
    if not item.disclosure_class:
        blocking.append(
            _reason(
                NO_CLASSIFICATION,
                "Content has no disclosure classification; publication fails closed.",
            )
        )
    elif item.disclosure_class not in policy.disclosure_classes:
        blocking.append(
            _reason(
                UNKNOWN_CLASSIFICATION,
                "Disclosure classification %r is not a recognised class."
                % item.disclosure_class,
            )
        )
    elif item.disclosure_class not in policy.publishable_classes:
        blocking.append(
            _reason(
                NOT_PUBLISHABLE_CLASS,
                "Classification %s is not publishable to any external platform."
                % item.disclosure_class,
                {"disclosure_class": item.disclosure_class},
            )
        )

    # 2. An explicit never-publish requirement cannot be overridden by anything,
    #    including a scheduler, an approval, or a model.
    if item.approval_requirement == REQUIREMENT_NEVER_PUBLISH:
        blocking.append(
            _reason(
                MARKED_NEVER_PUBLISH,
                "Content is marked NEVER_PUBLISH; scheduling cannot override it.",
            )
        )

    # 3. Restricted categories.
    blocking_hits = scan(fields, policy.restricted("block_publication"), "block_publication")
    for hit in blocking_hits:
        blocking.append(
            _reason(RESTRICTED_CONTENT, hit.describe(), hit.to_json())
        )

    approval_hits = scan(
        fields, policy.restricted("require_human_approval"), "require_human_approval"
    )
    for hit in approval_hits:
        advisory.append(_reason(RESTRICTED_CONTENT, hit.describe(), hit.to_json()))

    prohibited_hits = scan_phrases(
        fields, policy.prohibited_phrases, "PROHIBITED_PHRASE", "block_publication"
    )
    for hit in prohibited_hits:
        blocking.append(_reason(PROHIBITED_PHRASE, hit.describe(), hit.to_json()))

    # 4. Evidence for claim categories that require it.
    claim_categories = {hit.category for hit in approval_hits}
    required = set(policy.evidence_required_categories) & claim_categories
    pillar_cfg = config.pillars.get(item.pillar) or {}
    if pillar_cfg.get("requires_evidence"):
        required.add("PILLAR_%s" % item.pillar.upper())

    if required and not linked_evidence:
        blocking.append(
            _reason(
                MISSING_EVIDENCE,
                "Business claim(s) present with no evidence record linked: %s"
                % ", ".join(sorted(required)),
                {"categories": sorted(required)},
            )
        )

    # 5. Platform requirements.
    if variants:
        for variant in variants:
            platform_cfg = config.platforms.platform(variant.platform)
            if platform_cfg.get("requires_media") and not variant.media_ids:
                blocking.append(
                    _reason(
                        MISSING_MEDIA,
                        "%s is visual-first and the variant carries no media."
                        % platform_cfg.get("label", variant.platform),
                        {"platform": variant.platform},
                    )
                )
    else:
        advisory.append(
            _reason(NO_VARIANTS, "No platform variants have been generated yet.")
        )

    publish_allowed = not blocking
    requires_approval = _requires_approval(
        config, item, publish_allowed, bool(approval_hits)
    )

    return PolicyDecision(
        content_id=item.content_id,
        publish_allowed=publish_allowed,
        requires_approval=requires_approval,
        blocking_reasons=blocking,
        advisory_reasons=advisory,
        policy_version=policy.version,
    )


def _requires_approval(
    config: AppConfig, item: ContentItem, publish_allowed: bool, sensitive: bool
) -> bool:
    policy = config.policy

    if not publish_allowed:
        # Blocked content is not "pending approval"; it is blocked. Reporting it
        # as awaiting a decision would put un-publishable material in the queue.
        return False
    if item.approval_requirement == REQUIREMENT_NEVER_PUBLISH:
        return False
    if item.approval_requirement == HUMAN_APPROVAL_REQUIRED:
        return True

    mode = policy.operating_mode
    if mode == MODE_APPROVAL_REQUIRED:
        return True

    auto_class = item.disclosure_class in policy.auto_publishable_classes
    pillar_cfg = config.pillars.get(item.pillar) or {}
    auto_pillar = bool(pillar_cfg.get("auto_eligible")) and (
        item.pillar in policy.auto_eligible_pillars
    )

    if mode == MODE_TRUSTED_AUTOPUBLISH:
        return not (auto_class and auto_pillar)

    # MODE_3_MIXED: routine approved categories flow; anything touching
    # customers, partners, contracts, or major company claims stops for a human.
    if sensitive:
        return True
    return not (auto_class and auto_pillar and item.approval_requirement == AUTO_APPROVED)


def persist(conn: Connection, decision: PolicyDecision, now: dt.datetime) -> None:
    conn.execute(
        policy_decisions.insert().values(
            content_id=decision.content_id,
            evaluated_at=now,
            policy_version=decision.policy_version,
            publish_allowed=decision.publish_allowed,
            requires_approval=decision.requires_approval,
            blocking_reasons=decision.blocking_reasons,
            advisory_reasons=decision.advisory_reasons,
        )
    )


def authorise_publication(
    item: ContentItem, decision: PolicyDecision, approver: Optional[str] = None
) -> None:
    """Raise unless this item is genuinely cleared to publish right now.

    Every publish path goes through here. ``approver`` must be a human or an
    explicitly configured rule; a model is never an acceptable approver.
    """
    if decision.blocked:
        raise NotAuthorised(
            "policy blocked %s: %s" % (item.content_id, ", ".join(decision.blocking_codes))
        )
    if decision.requires_approval and item.approval_status != APPROVED:
        raise NotAuthorised(
            "%s requires approval and is currently %s" % (item.content_id, item.approval_status)
        )
    if approver is not None and not _is_valid_approver(approver):
        raise NotAuthorised(
            "%r is not a valid approver; approval must come from a human or a "
            "configured rule" % approver
        )


def _is_valid_approver(approver: str) -> bool:
    if approver.startswith("human:") and len(approver) > len("human:"):
        return True
    if approver.startswith("rule:") and len(approver) > len("rule:"):
        return True
    return False


def load_evidence(conn: Connection, content_id: str) -> List[Dict[str, Any]]:
    return evidence_for(conn, content_id)
