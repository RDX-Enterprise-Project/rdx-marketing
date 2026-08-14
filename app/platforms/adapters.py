"""Platform adapters.

One content object becomes three genuinely different posts. Identical copy on
LinkedIn, Facebook, and Instagram is treated as a defect, not a shortcut: the
audiences do not overlap much and the formats reward opposite things.

Copy is produced from deterministic templates. A model can improve the prose
later (see :mod:`app.ai.drafting`), but the engine never depends on one being
available, and the templates alone must produce something publishable.

House style, enforced here rather than trusted to the writer:

* no em dashes — RDX public copy must not read as machine-written
* no marketing filler from the configured forbid list
* hashtag counts within the platform's own limit
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from ..config import AppConfig
from ..content.item import ContentItem, PlatformVariant, variant_id

LINKEDIN = "linkedin"
FACEBOOK = "facebook"
INSTAGRAM = "instagram"

GENERATED_BY_TEMPLATE = "template"

EM_DASHES = ("—", "–")


class StyleViolation(ValueError):
    pass


@dataclass
class VariantRequest:
    item: ContentItem
    platform: str
    post_type: str = "post"
    media_ids: Optional[List[str]] = None
    hook: Optional[str] = None
    supporting_points: Optional[List[str]] = None


def build_variant(config: AppConfig, request: VariantRequest) -> PlatformVariant:
    platform = request.platform
    if platform == LINKEDIN:
        variant = _linkedin(config, request)
    elif platform == FACEBOOK:
        variant = _facebook(config, request)
    elif platform == INSTAGRAM:
        variant = _instagram(config, request)
    else:
        raise ValueError("no adapter for platform %r" % platform)

    enforce_style(config, variant)
    return variant


def build_all(
    config: AppConfig,
    item: ContentItem,
    platforms: Sequence[str],
    media_ids: Optional[Dict[str, List[str]]] = None,
    supporting_points: Optional[List[str]] = None,
) -> List[PlatformVariant]:
    media = media_ids or {}
    variants = [
        build_variant(
            config,
            VariantRequest(
                item=item,
                platform=platform,
                media_ids=media.get(platform, []),
                supporting_points=supporting_points,
            ),
        )
        for platform in platforms
    ]
    if config.platforms.style.get("require_distinct_bodies", True):
        fingerprints = [v.body_fingerprint for v in variants]
        if len(set(fingerprints)) != len(fingerprints):
            raise StyleViolation(
                "platform variants must not share identical copy; "
                "the same text on every network is a defect"
            )
    return variants


# --------------------------------------------------------------------------- #
# per-platform templates
# --------------------------------------------------------------------------- #


def _points(request: VariantRequest) -> List[str]:
    if request.supporting_points:
        return list(request.supporting_points)
    return []


def _linkedin(config: AppConfig, request: VariantRequest) -> PlatformVariant:
    """Technical and business-oriented. One real takeaway, few hashtags."""
    item = request.item
    lines: List[str] = []

    hook = request.hook or _statement_hook(item.core_message)
    lines.append(hook)
    lines.append("")
    lines.append(_expand(item.core_message))

    points = _points(request)
    if points:
        lines.append("")
        for point in points[:4]:
            lines.append("- %s" % _clean(point))

    lines.append("")
    lines.append(_takeaway(item))

    if item.cta:
        lines.append("")
        lines.append(_clean(item.cta))

    hashtags = _hashtags(config, LINKEDIN, item)
    if hashtags:
        lines.append("")
        lines.append(" ".join("#%s" % h for h in hashtags))

    return PlatformVariant(
        variant_id=variant_id(item.content_id, LINKEDIN),
        content_id=item.content_id,
        platform=LINKEDIN,
        post_type=request.post_type,
        body="\n".join(lines).strip(),
        generated_by=GENERATED_BY_TEMPLATE,
        hashtags=hashtags,
        cta=item.cta,
        media_ids=list(request.media_ids or []),
        hook_type="statement",
    )


def _facebook(config: AppConfig, request: VariantRequest) -> PlatformVariant:
    """Conversational company voice. Shorter, community-oriented."""
    item = request.item
    core = _clean(item.core_message)
    first = _first_sentence(core)

    lines = [
        first,
        "",
        _shorten(core, 420),
    ]
    if item.cta:
        lines.append("")
        lines.append(_clean(item.cta))

    hashtags = _hashtags(config, FACEBOOK, item)
    if hashtags:
        lines.append("")
        lines.append(" ".join("#%s" % h for h in hashtags))

    return PlatformVariant(
        variant_id=variant_id(item.content_id, FACEBOOK),
        content_id=item.content_id,
        platform=FACEBOOK,
        post_type=request.post_type,
        body="\n".join(lines).strip(),
        generated_by=GENERATED_BY_TEMPLATE,
        hashtags=hashtags,
        cta=item.cta,
        media_ids=list(request.media_ids or []),
        hook_type="conversational",
    )


def _instagram(config: AppConfig, request: VariantRequest) -> PlatformVariant:
    """Visual-first. The caption supports the image, it does not repeat LinkedIn."""
    item = request.item
    caption = _shorten(_first_sentence(_clean(item.core_message)), 180)

    lines = [caption]
    if item.cta:
        lines.append("")
        lines.append(_shorten(_clean(item.cta), 90))

    hashtags = _hashtags(config, INSTAGRAM, item)
    body = "\n".join(lines).strip()

    # Instagram hashtags live in the first comment, keeping the caption clean.
    first_comment = " ".join("#%s" % h for h in hashtags) if hashtags else None

    return PlatformVariant(
        variant_id=variant_id(item.content_id, INSTAGRAM),
        content_id=item.content_id,
        platform=INSTAGRAM,
        post_type=request.post_type,
        body=body,
        generated_by=GENERATED_BY_TEMPLATE,
        hashtags=hashtags,
        first_comment=first_comment,
        cta=item.cta,
        media_ids=list(request.media_ids or []),
        hook_type="visual",
    )


# --------------------------------------------------------------------------- #
# style
# --------------------------------------------------------------------------- #


def enforce_style(config: AppConfig, variant: PlatformVariant) -> None:
    style = config.platforms.style
    platform_cfg = config.platforms.platform(variant.platform)
    haystack = " ".join(
        filter(None, [variant.body, variant.first_comment or "", variant.cta or ""])
    )

    if style.get("forbid_em_dash", True):
        for dash in EM_DASHES:
            if dash in haystack:
                raise StyleViolation(
                    "%s copy contains an em/en dash; RDX public copy must not read "
                    "as machine-written" % variant.platform
                )

    lowered = haystack.lower()
    for phrase in style.get("forbid_phrases", []) or []:
        if str(phrase).lower() in lowered:
            raise StyleViolation(
                "%s copy contains forbidden filler: %r" % (variant.platform, phrase)
            )

    hard_max = int((platform_cfg.get("body", {}) or {}).get("hard_max_chars", 0) or 0)
    if hard_max and len(variant.body) > hard_max:
        raise StyleViolation(
            "%s body is %d chars, over the %d limit"
            % (variant.platform, len(variant.body), hard_max)
        )

    max_tags = int((platform_cfg.get("hashtags", {}) or {}).get("max", 0) or 0)
    if max_tags and len(variant.hashtags) > max_tags:
        raise StyleViolation(
            "%s carries %d hashtags, over the %d limit"
            % (variant.platform, len(variant.hashtags), max_tags)
        )

    if platform_cfg.get("first_comment_supported") is False and variant.first_comment:
        raise StyleViolation("%s does not support a first comment" % variant.platform)


# --------------------------------------------------------------------------- #
# text helpers
# --------------------------------------------------------------------------- #


def _clean(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    for dash in EM_DASHES:
        cleaned = cleaned.replace(dash, ",")
    return cleaned


def _first_sentence(text: str) -> str:
    match = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
    return match[0] if match else text


def _statement_hook(core_message: str) -> str:
    return _first_sentence(_clean(core_message))


def _expand(core_message: str) -> str:
    return textwrap.fill(_clean(core_message), width=100000)


def _takeaway(item: ContentItem) -> str:
    sentence = _shorten(_first_sentence(_clean(item.core_message)), 200).rstrip(".")
    if not sentence:
        return "The practical takeaway is in the detail above."
    return "The practical takeaway: %s%s." % (sentence[0].lower(), sentence[1:])


def _shorten(text: str, limit: int) -> str:
    text = _clean(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "."


def _hashtags(config: AppConfig, platform: str, item: ContentItem) -> List[str]:
    platform_cfg = config.platforms.platform(platform)
    max_tags = int((platform_cfg.get("hashtags", {}) or {}).get("max", 0) or 0)
    if not max_tags:
        return []

    candidates = ["cybersecurity", "securityautomation", "SOAR", "SOC", "govcon", "RDXEnterprise"]
    pillar_tags = {
        "cybersecurity_education": ["cybersecurity", "SOC"],
        "soar_automation": ["SOAR", "securityautomation"],
        "rdx_capabilities": ["RDXEnterprise", "cybersecurity"],
        "academy_workforce": ["cybersecuritytraining", "workforcedevelopment"],
        "founder_expertise": ["leadership", "cybersecurity"],
        "smallbiz_federal_education": ["govcon", "smallbusiness"],
        "approved_milestones": ["RDXEnterprise", "SDVOSB"],
    }
    tags = pillar_tags.get(item.pillar, candidates[:2])
    return [t for t in tags][:max_tags]
