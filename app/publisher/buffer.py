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
        sentAt
        externalLink
      }
    }
    ... on MutationError {
      message
    }
  }
}
"""

#: Cleanup. Used by scripts/capture_fixtures.py to remove the test draft it
#: creates, so a contract capture leaves no residue in the account.
DELETE_POST = """
mutation DeletePost($input: DeletePostInput!) {
  deletePost(input: $input) {
    ... on DeletePostSuccess {
      id
    }
    ... on VoidMutationError {
      message
    }
  }
}
"""

POST_METRICS = """
query PostMetrics($input: PostInput!) {
  post(input: $input) {
    id
    status
    sentAt
    metricsUpdatedAt
    metrics {
      type
      name
      value
      unit
    }
  }
}
"""

#: Count-style PostMetricType values mapped onto MetricsSample fields.
#: Verified against the live schema 2026-08-15.
METRIC_TYPE_MAP = {
    "impressions": "impressions",
    "reach": "reach",
    "reactions": "reactions",
    "likes": "reactions",
    "comments": "comments",
    "shares": "shares",
    "reposts": "shares",
    "quotes": "shares",
    "clicks": "clicks",
}

#: Rate-style metrics. Buffer computes these itself, and its number is
#: authoritative over anything derived from the counts.
RATE_METRIC_TYPES = frozenset({"engagementrate"})

#: Real PostMetricType values with no home on MetricsSample. Listed explicitly
#: so the contract test can tell "we chose not to carry this" apart from
#: "Buffer added something new and we are silently dropping it".
KNOWN_UNMAPPED_METRICS = frozenset(
    {"follows", "postcount", "saves", "totaltimewatched", "viewers", "views"}
)

#: Everything the adapter recognises, in any capacity.
ALL_KNOWN_METRIC_TYPES = (
    frozenset(METRIC_TYPE_MAP) | RATE_METRIC_TYPES | KNOWN_UNMAPPED_METRICS
)

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

# --------------------------------------------------------------------------- #
# Contract
#
# What this adapter sends and reads. `tests/test_contract_buffer.py` pins a
# captured response to these, so a Buffer schema change fails one named test
# rather than silently producing wrong numbers. Add to these lists when the
# adapter starts depending on something new.
# --------------------------------------------------------------------------- #

#: Non-null on CreatePostInput. Omitting any one of these fails the whole call.
CREATE_POST_REQUIRED_INPUT = ("channelId", "assets", "mode", "schedulingType", "needsApproval")

#: Sent when relevant.
CREATE_POST_OPTIONAL_INPUT = ("text", "dueAt", "saveToDraft", "metadata")

#: Read off the success arm of the response union.
CREATE_POST_RESPONSE_KEYS = ("id", "status")

#: Read off each entry of Post.metrics.
METRIC_ENTRY_KEYS = ("type", "value")


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
            # `externalLink`, not `permalink`. The publications table has always
            # had this column and it was never being filled.
            permalink=post.get("externalLink"),
            raw=response,
        )

    def delete_post(self, provider_post_id: str) -> bool:
        """Delete a post. Used to clean up a contract-capture test draft.

        Returns True only on a confirmed delete. A failure is reported, never
        assumed away, so a leftover draft is visible rather than silent.
        """
        try:
            response = self._transport.post_json(
                self._config.api_base,
                {"query": DELETE_POST, "variables": {"input": {"id": provider_post_id}}},
                headers=self._headers(),
                timeout=self._config.timeout_seconds,
            )
        except Exception:  # noqa: BLE001
            return False

        if response.get("errors"):
            return False
        payload = (response.get("data") or {}).get("deletePost") or {}
        return bool(payload.get("id")) and not payload.get("message")

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
                # post takes PostInput, not a bare id argument.
                {"query": POST_METRICS, "variables": {"input": {"id": provider_post_id}}},
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
        reported_rate: Optional[float] = None

        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            kind = str(metric.get("type", "")).lower()
            # PostMetric.value is a Float. Truncating it to int would turn an
            # engagement rate of 0.034 into 0.
            raw_value = _float(metric.get("value"))
            if raw_value is None:
                continue

            if kind in RATE_METRIC_TYPES:
                # Buffer computes this itself; its number beats ours.
                reported_rate = raw_value
                continue

            field_name = METRIC_TYPE_MAP.get(kind)
            if field_name is None:
                continue
            values[field_name] = values.get(field_name, 0) + int(round(raw_value))

        if not values and reported_rate is None:
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
            reported_engagement_rate=reported_rate,
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


#: PostStatus values that mean the post is staged, not live.
STAGED_STATUSES = frozenset({"draft", "buffer"})


def _status_from(post: Dict[str, Any], create_as_draft: bool) -> str:
    """Staged or live.

    `sentAt` is checked as well as `status`: the documentation names `draft` and
    `buffer` explicitly but does not pin the sent value, and treating an unknown
    status as staged when the post has actually gone out would under-report a
    publication. A populated `sentAt` is unambiguous.
    """
    if post.get("sentAt"):
        return STATUS_PUBLISHED
    status = str(post.get("status", "")).lower()
    if status in STAGED_STATUSES:
        return STATUS_SCHEDULED
    if status == "sent":
        return STATUS_PUBLISHED
    # Unknown status with no sentAt: staged is the safe reading, and the raw
    # response is stored so the real value is recoverable.
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


def _float(value: Any) -> Optional[float]:
    """PostMetric.value is a Float; counts and rates both arrive through it."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_client_error(errors: Any) -> bool:
    for error in errors or []:
        code = str(((error or {}).get("extensions") or {}).get("code", "")).upper()
        if code in CLIENT_ERROR_CODES:
            return True
    return False
