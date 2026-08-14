"""Buffer publishing gateway.

Buffer fronts LinkedIn, Facebook, and Instagram through one GraphQL API, which
is why the engine does not start by building three separate social
integrations. It is reached only through
:class:`~app.publisher.base.SocialPublisher`, so replacing it later does not
touch the marketing engine.

Schema verified against Buffer's developer documentation on 2026-08-14:

* endpoint ``https://api.buffer.com``, ``Authorization: Bearer <key>``
* mutation ``createPost(input: CreatePostInput!)``
* required input fields: ``channelId``, ``assets`` (``[]`` for a text-only
  post — it is non-null, so omitting it fails the whole call), ``mode``
  (``addToQueue`` | ``customScheduled``) and ``schedulingType``
* ``dueAt`` is ISO-8601 UTC and is only meaningful with ``customScheduled``
* drafts are ``saveToDraft: true`` on the same mutation, not a separate one
* the response is a union: ``PostActionSuccess`` or ``MutationError``
* per-network extras such as a first comment live in ``metadata``
* ``Post.metrics`` is a **list** of typed metric objects, not named numeric
  fields, so it is mapped by ``type`` rather than read positionally

Buffer supports creating a post as a draft rather than publishing it, and the
engine uses that deliberately: when policy says an item needs a human, the post
is staged as a draft at the provider too, so the approval control exists on both
sides of the boundary.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from .base import (
    STATUS_FAILED,
    STATUS_PUBLISHED,
    STATUS_SCHEDULED,
    MetricsSample,
    PublishRequest,
    PublishResult,
)

PROVIDER = "buffer"
API_BASE = "https://api.buffer.com"

# ShareMode
MODE_ADD_TO_QUEUE = "addToQueue"
MODE_CUSTOM_SCHEDULED = "customScheduled"

# SchedulingType
SCHEDULING_AUTOMATIC = "automatic"
SCHEDULING_NOTIFICATION = "notification"

CREATE_POST = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess {
      post {
        id
        text
        dueAt
        status
      }
    }
    ... on MutationError {
      message
    }
  }
}
"""

POST_METRICS = """
query PostMetrics($id: PostId!) {
  post(id: $id) {
    id
    status
    metrics {
      type
      name
      value
      unit
    }
  }
}
"""

#: Buffer's PostMetricType values mapped onto the engine's own fields.
METRIC_TYPE_MAP = {
    "impressions": "impressions",
    "reach": "reach",
    "reactions": "reactions",
    "likes": "reactions",
    "comments": "comments",
    "shares": "shares",
    "reposts": "shares",
    "clicks": "clicks",
}

#: Where a first comment lives, per network, inside PostInputMetaData.
FIRST_COMMENT_METADATA_KEY = {
    "linkedin": "linkedin",
    "facebook": "facebook",
    "instagram": "instagram",
}

#: GraphQL error codes that mean "this request was wrong", not "try later".
CLIENT_ERROR_CODES = frozenset(
    {"BAD_USER_INPUT", "UNAUTHENTICATED", "FORBIDDEN", "VALIDATION_ERROR", "GRAPHQL_VALIDATION_FAILED"}
)


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
    api_base: str = API_BASE
    token: str = ""
    channels: Dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 45
    default_create_as_draft: bool = True
    scheduling_type: str = SCHEDULING_AUTOMATIC


class BufferPublisher:
    name = PROVIDER

    def __init__(self, transport: GraphQlTransport, config: BufferConfig) -> None:
        self._transport = transport
        self._config = config

    # -- publishing --------------------------------------------------------- #

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
        variables = {"input": self._build_input(request, channel, create_as_draft)}

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
            return PublishResult(
                status=STATUS_FAILED,
                provider=PROVIDER,
                error_message="; ".join(str(e.get("message", e)) for e in errors),
                retryable=not _is_client_error(errors),
                raw=response,
            )

        payload = (response.get("data") or {}).get("createPost") or {}

        # The union's error arm. A MutationError is a real refusal, not a blip.
        if payload.get("message") and not payload.get("post"):
            return PublishResult(
                status=STATUS_FAILED,
                provider=PROVIDER,
                error_message=str(payload["message"]),
                retryable=False,
                raw=response,
            )

        post = payload.get("post") or {}
        if not post.get("id"):
            return PublishResult(
                status=STATUS_FAILED,
                provider=PROVIDER,
                error_message="Buffer returned no post id: %s" % payload,
                retryable=True,
                raw=response,
            )

        return PublishResult(
            status=_status_from(post, create_as_draft),
            provider=PROVIDER,
            provider_post_id=str(post["id"]),
            raw=response,
        )

    def _build_input(
        self, request: PublishRequest, channel: str, create_as_draft: bool
    ) -> Dict[str, Any]:
        scheduled = request.scheduled_for is not None
        payload: Dict[str, Any] = {
            "channelId": channel,
            "text": request.body,
            # Non-null on CreatePostInput. A text-only post must still send an
            # empty list, and omitting it fails the entire call.
            "assets": [{"id": media_id} for media_id in request.media_ids],
            "mode": MODE_CUSTOM_SCHEDULED if scheduled else MODE_ADD_TO_QUEUE,
            "schedulingType": self._config.scheduling_type,
            "saveToDraft": create_as_draft,
            # Buffer's own approval workflow. Kept in step with ours: if this
            # engine says a human is needed, Buffer is told the same thing.
            "needsApproval": create_as_draft,
        }
        if scheduled:
            payload["dueAt"] = _iso8601_utc(request.scheduled_for)

        metadata = self._metadata(request)
        if metadata:
            payload["metadata"] = metadata
        return payload

    def _metadata(self, request: PublishRequest) -> Dict[str, Any]:
        """Per-network extras. Currently the first comment."""
        if not request.first_comment:
            return {}
        key = FIRST_COMMENT_METADATA_KEY.get(request.platform)
        if key is None:
            return {}
        return {key: {"firstComment": request.first_comment}}

    # -- metrics ------------------------------------------------------------ #

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

        if response.get("errors"):
            return None

        post = (response.get("data") or {}).get("post") or {}
        metrics = post.get("metrics")
        if not metrics:
            return None

        # Post.metrics is a list of typed objects, so it is mapped by `type`.
        # Reading it positionally, or expecting named numeric fields, silently
        # produces wrong numbers rather than an error.
        values: Dict[str, int] = {}
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            field_name = METRIC_TYPE_MAP.get(str(metric.get("type", "")).lower())
            if field_name is None:
                continue
            value = _int(metric.get("value"))
            if value is None:
                continue
            values[field_name] = values.get(field_name, 0) + value

        if not values:
            return None

        return MetricsSample(
            provider_post_id=provider_post_id,
            platform=platform,
            impressions=values.get("impressions"),
            reach=values.get("reach"),
            reactions=values.get("reactions"),
            comments=values.get("comments"),
            shares=values.get("shares"),
            clicks=values.get("clicks"),
            raw=response,
        )

    # -- helpers ------------------------------------------------------------ #

    def _channel(self, platform: str) -> str:
        channel = self._config.channels.get(platform)
        if not channel:
            raise MissingChannel(
                "no Buffer channel configured for %r; set the channel id in "
                "config/platforms.yaml before publishing" % platform
            )
        return channel

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": "Bearer %s" % self._config.token,
            "Content-Type": "application/json",
        }


def _status_from(post: Dict[str, Any], create_as_draft: bool) -> str:
    status = str(post.get("status", "")).lower()
    if status == "sent":
        return STATUS_PUBLISHED
    # "draft" and "buffer" (queued) are both staged, not live.
    return STATUS_SCHEDULED


def _iso8601_utc(value: dt.datetime) -> str:
    """Buffer expects ISO-8601 UTC, e.g. 2026-03-10T15:00:00.000Z."""
    if value.tzinfo is None:
        raise ValueError("scheduled_for must be timezone-aware")
    utc = value.astimezone(dt.timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (utc.microsecond // 1000)


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_client_error(errors: Any) -> bool:
    for error in errors or []:
        code = str(((error or {}).get("extensions") or {}).get("code", "")).upper()
        if code in CLIENT_ERROR_CODES:
            return True
    return False
