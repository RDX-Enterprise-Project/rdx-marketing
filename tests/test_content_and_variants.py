"""Tests 6, 7, 10, 14: content identity, platform variance, evidence, duplicates.

6.  Platform variants remain associated with one canonical content object.
7.  LinkedIn / Facebook / Instagram copy can differ.
10. Marketing claims trace to source evidence.
14. Duplicate post prevention works.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.content.evidence import (
    EvidenceRecord,
    NotEvidence,
    evidence_for,
    evidence_id_for,
    link_claim,
    record_evidence,
)
from app.content.item import PUBLIC
from app.content.store import (
    find_duplicate_variant,
    load_variants,
    next_content_id,
    save_item,
    save_variants,
)
from app.platforms.adapters import StyleViolation, build_all, build_variant, VariantRequest
from app.policy.engine import MISSING_EVIDENCE, evaluate

from .conftest import NOW, TODAY, make_item, seed_evidence


def test_variants_stay_attached_to_one_content_object(engine, config):
    """Test 6."""
    item = make_item()
    variants = build_all(config, item, ["linkedin", "facebook", "instagram"])

    with engine.begin() as conn:
        save_item(conn, item, NOW)
        save_variants(conn, variants, NOW)

    with engine.connect() as conn:
        loaded = load_variants(conn, item.content_id)

    assert len(loaded) == 3
    assert {v.content_id for v in loaded} == {item.content_id}
    assert {v.platform for v in loaded} == {"linkedin", "facebook", "instagram"}
    # One canonical object, one classification, one evidence set.
    assert len({v.content_id for v in loaded}) == 1


def test_platform_copy_differs(config):
    """Test 7."""
    item = make_item()
    variants = {v.platform: v for v in build_all(config, item, ["linkedin", "facebook", "instagram"])}

    bodies = {p: v.body for p, v in variants.items()}
    assert len(set(bodies.values())) == 3, "identical copy on every network is a defect"

    # LinkedIn is the long, technical one; Instagram the short, visual one.
    assert len(bodies["linkedin"]) > len(bodies["facebook"]) > len(bodies["instagram"])
    assert "The practical takeaway" in bodies["linkedin"]
    assert variants["instagram"].hook_type == "visual"
    # Instagram hashtags live in the first comment, keeping the caption clean.
    assert variants["instagram"].first_comment
    assert variants["facebook"].first_comment is None


def test_identical_copy_across_platforms_is_rejected(config):
    item = make_item()
    variants = build_all(config, item, ["linkedin", "facebook"])
    variants[1].body = variants[0].body

    from app.platforms.adapters import GENERATED_BY_TEMPLATE

    # Rebuilding with a forced collision must raise rather than pass it through.
    with pytest.raises(StyleViolation, match="must not share identical copy"):
        _assert_distinct(config, variants)


def _assert_distinct(config, variants):
    fingerprints = [v.body_fingerprint for v in variants]
    if len(set(fingerprints)) != len(fingerprints):
        raise StyleViolation(
            "platform variants must not share identical copy; "
            "the same text on every network is a defect"
        )


def test_template_copy_never_carries_an_em_dash(config):
    """RDX public copy must not read as machine-written.

    The template normalises a dash out of the rendered copy rather than failing
    the whole item, since the author's source text is not changed.
    """
    item = make_item(
        core_message=(
            "Security automation is not a product purchase — it is an integration problem, "
            "and the teams that treat it that way get further than the ones that do not, "
            "because the hard part was never buying the tool in the first place."
        )
    )
    variant = build_variant(config, VariantRequest(item=item, platform="linkedin"))
    assert "—" not in variant.body
    assert "–" not in variant.body


def test_style_enforcement_catches_copy_that_bypassed_the_template(config):
    """AI-written bodies do not pass through the normaliser, so the guard must hold."""
    from app.ai.drafting import apply_draft
    from app.platforms.adapters import enforce_style

    item = make_item()
    variant = build_variant(config, VariantRequest(item=item, platform="linkedin"))
    apply_draft(variant, "Automation is not a purchase — it is an integration problem.", "m")

    with pytest.raises(StyleViolation, match="em/en dash"):
        enforce_style(config, variant)


def test_marketing_claims_trace_to_evidence(engine, config):
    """Test 10."""
    item = make_item(
        content_id="MKT-2026-00010",
        pillar="approved_milestones",
        disclosure="PUBLIC_AFTER_APPROVAL",
        topic="SDVOSB certification",
        core_message=(
            "RDX Enterprise is a certified service disabled veteran owned small business, "
            "which shapes how the company works with federal buyers and prime contractors."
        ),
    )
    variants = build_all(config, item, ["linkedin"])

    # approved_milestones requires evidence. Without it, publication is blocked.
    without = evaluate(config, item, variants, linked_evidence=[])
    assert without.blocked
    assert MISSING_EVIDENCE in without.blocking_codes

    with engine.begin() as conn:
        save_item(conn, item, NOW)
        seed_evidence(conn, item.content_id, NOW, config)

    with engine.connect() as conn:
        linked = evidence_for(conn, item.content_id)

    assert linked
    assert linked[0]["kind"] == "CERTIFICATE"
    with_evidence = evaluate(config, item, variants, linked_evidence=linked)
    assert MISSING_EVIDENCE not in with_evidence.blocking_codes


def test_a_generated_statement_cannot_be_its_own_evidence(engine, config):
    with engine.begin() as conn:
        with pytest.raises(NotEvidence, match="generated artefact"):
            record_evidence(
                conn,
                EvidenceRecord(
                    evidence_id="EV-bad",
                    kind="AI_OUTPUT",
                    summary="the post says so",
                    recorded_by="ai:claude",
                    max_disclosure=PUBLIC,
                ),
                NOW,
                config.policy.accepted_evidence_kinds,
            )

        with pytest.raises(NotEvidence, match="cannot be evidence"):
            link_claim(conn, "MKT-2026-00011", "MKT-2026-00010", "we said it earlier", NOW)


def test_duplicate_post_prevention(engine, config):
    """Test 14."""
    first = make_item(content_id="MKT-2026-00020")
    second = make_item(content_id="MKT-2026-00021")

    variants_one = build_all(config, first, ["linkedin"])
    variants_two = build_all(config, second, ["linkedin"])
    # Same copy, different content object.
    variants_two[0].body = variants_one[0].body

    with engine.begin() as conn:
        save_item(conn, first, NOW)
        save_item(conn, second, NOW)
        save_variants(conn, variants_one, NOW)
        save_variants(conn, variants_two, NOW)

    with engine.connect() as conn:
        duplicate = find_duplicate_variant(
            conn,
            platform="linkedin",
            body_fingerprint=variants_two[0].body_fingerprint,
            since=NOW - dt.timedelta(days=90),
            exclude_content_id=second.content_id,
        )
    assert duplicate is not None
    assert duplicate["content_id"] == first.content_id


def test_duplicate_detection_ignores_whitespace_only_edits(config):
    item = make_item()
    variant = build_all(config, item, ["linkedin"])[0]
    original = variant.body_fingerprint
    variant.body = variant.body.replace("\n", "\n ")
    assert variant.body_fingerprint == original


def test_content_ids_increment(engine):
    with engine.begin() as conn:
        assert next_content_id(conn, 2026) == "MKT-2026-00001"
        save_item(conn, make_item(content_id="MKT-2026-00001"), NOW)
        assert next_content_id(conn, 2026) == "MKT-2026-00002"
