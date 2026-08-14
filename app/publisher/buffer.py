"""Buffer publishing gateway.

Buffer fronts LinkedIn, Facebook, and Instagram through one API, which is why
the engine does not start by building three separate social integrations. It is
reached only through :class:`~app.publisher.base.SocialPublisher`, so replacing
it later does not touch the marketing engine.

Buffer supports creating a post as a draft rather than publishing it. The engine
uses that deliberately: when policy says an item needs a human, the post is
staged as a draft at the provider too, so the approval control exists on both
sides of the boundary.

Channel ids are resolved from configuration once, not guessed per call. A
platform with no configured channel is a hard failure with a clear message, not
a silent skip.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

from .base import (
    STATUS_FAILED,
    STATUS_PUBLISHED,
    STATUS_SCHEDULED,
    MetricsSample,
    PublishRequest,
    PublishResult,
)

PROVIDER = "buffer"

CREATE_POST = """
mutation CreatePost($input: PostCreateInput!) {
  postCreate(input: $input) {
    id
    status
    permalink
  }
}
"""

POST_METRICS = """
query PostMetrics($id: ID!) {
  post(id: $id) {
    id
    metrics { impressions reach reactions comments shares clicks }
  }
}
"""


class GraphQlTransport(Protocol):
    def post_json(
        self,
        url: str,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        ...


class MissingChannel(RuntimeError):
    pass


@dataclass
class BufferConfig:
    api_base: str
    token: str
    channels: Dict[str, str]
    timeout_seconds: int = 45
    default_create_as_draft: bool = True


class BufferPublisher:
    name = PROVIDER

    def __init__(self, transport: GraphQlTransport, config: BufferConfig) -> None:
        self._transport = transport
        self._config = config

    def _channel(self, platform: str) -> str:
        channel = self._config.channels.get(platform)
        if not channel:
            raise MissingChannel(
                "no Buffer channel configured for %r; set the channel id in "
                "config/platforms.yaml before publishing" % platform
            )
        return channel

    def publish(self, request: PublishRequest) -> PublishResult:
        try:
            channel = self._channel(request.platform)
        except MissingChannel as exc:
            # Configuration, not a transient outage. Retrying will not help.
            return PublishResult(
                status=STATUS_FAILED,
                provider=PROVIDER,
                error_message=str(exc),
                retryable=False,
            )

        create_as_draft = request.create_as_draft or self._config.default_create_as_draft

        variables: Dict[str, Any] = {
            "input": {
                "channelId": channel,
                "text": request.body,
                "isDraft": create_as_draft,
                "postType": request.post_type,
            }
        }
        if request.scheduled_for is not None:
            variables["input"]["scheduledAt"] = request.scheduled_for.isoformat()
        if request.media_ids:
            variables["input"]["media"] = [{"id": m} for m in request.media_ids]
        if request.first_comment:
            variables["input"]["firstComment"] = request.first_comment
        if request.idempotency_key:
            variables["input"]["clientRequestId"] = request.idempotency_key

        try:
            response = self._transport.post_json(
                self._config.api_base,
                {"query": CREATE_POST, "variables": variables},
                headers=self._headers(),
                timeout=self._config.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - an outage must not lose content
            return PublishResult(
                status=STATUS_FAILED,
                provider=PROVIDER,
                error_message="%s: %s" % (type(exc).__name__, exc),
                retryable=True,
            )

        errors = response.get("errors")
        if errors:
            message = "; ".join(str(e.get("message", e)) for e in errors)
            return PublishResult(
                status=STATUS_FAILED,
                provider=PROVIDER,
                error_message=message,
                # 4xx-shaped GraphQL errors are usually our fault, not a blip.
                retryable=not _is_client_error(errors),
                raw=response,
            )

        created = ((response.get("data") or {}).get("postCreate") or {})
        provider_status = str(created.get("status", "")).lower()
        status = STATUS_PUBLISHED if provider_status == "sent" else STATUS_SCHEDULED

        return PublishResult(
            status=status,
            provider=PROVIDER,
            provider_post_id=str(created.get("id")) if created.get("id") else None,
            permalink=created.get("permalink"),
            raw=response,
        )

    def fetch_metrics(self, provider_post_id: str, platform: str) -> Optional[MetricsSample]:
        try:
            response = self._transport.post_json(
                self._config.api_base,
                {"query": POST_METRICS, "variables": {"id": provider_post_id}},
                headers=self._headers(),
                timeout=self._config.timeout_seconds,
            )
        except Exception:  # noqa: BLE001 - a metrics gap is not a publishing failure
            return None

        post = (response.get("data") or {}).get("post") or {}
        metrics = post.get("metrics") or {}
        if not metrics:
            return None

        return MetricsSample(
            provider_post_id=provider_post_id,
            platform=platform,
            impressions=_int(metrics.get("impressions")),
            reach=_int(metrics.get("reach")),
            reactions=_int(metrics.get("reactions")),
            comments=_int(metrics.get("comments")),
            shares=_int(metrics.get("shares")),
            clicks=_int(metrics.get("clicks")),
            raw=response,
        )

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": "Bearer %s" % self._config.token,
            "Content-Type": "application/json",
        }


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_client_error(errors: Any) -> bool:
    for error in errors or []:
        code = str(((error or {}).get("extensions") or {}).get("code", "")).upper()
        if code in ("BAD_USER_INPUT", "UNAUTHENTICATED", "FORBIDDEN", "VALIDATION_ERROR"):
            return True
    return False
