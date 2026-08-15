"""Buffer's API shape, verified against its developer docs on 2026-08-14.

The first implementation of this adapter used `postCreate`/`PostCreateInput`/
`isDraft`/`scheduledAt`, omitted the non-null `assets`, `mode`, and
`schedulingType` fields, pointed at the wrong host, and expected metrics as
named numeric fields. Every one of those would have failed on the first live
call. These tests pin the real shape.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.publisher.base import (
    STATUS_FAILED,
    STATUS_PUBLISHED,
    STATUS_SCHEDULED,
    PublishRequest,
)
from app.publisher.buffer import (
    API_BASE,
    MODE_ADD_TO_QUEUE,
    MODE_CUSTOM_SCHEDULED,
    SCHEDULING_AUTOMATIC,
    BufferConfig,
    BufferPublisher,
)

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 8, 17, 13, 30, tzinfo=UTC)

CHANNELS = {"linkedin": "ch_li", "facebook": "ch_fb", "instagram": "ch_ig"}


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.calls = []
        self._response = response or {}
        self._error = error

    def post_json(self, url, payload, headers=None, timeout=60):
        self.calls.append({"url": url, "payload": payload, "headers": headers or {}})
        if self._error is not None:
            raise self._error
        return self._response


def _success(post_id="post_1", status="buffer"):
    return {"data": {"createPost": {"post": {"id": post_id, "text": "x", "status": status}}}}


def _publisher(transport, **overrides):
    config = BufferConfig(token="tok", channels=dict(CHANNELS))
    for key, value in overrides.items():
        setattr(config, key, value)
    return BufferPublisher(transport, config)


def _request(**overrides):
    base = dict(
        content_id="MKT-2026-00001",
        variant_id="MKT-2026-00001:linkedin",
        platform="linkedin",
        body="Security automation is an integration problem.",
        create_as_draft=False,
    )
    base.update(overrides)
    return PublishRequest(**base)


def test_it_posts_to_the_documented_endpoint_with_a_bearer_token():
    transport = FakeTransport(_success())
    _publisher(transport).publish(_request())

    call = transport.calls[0]
    assert call["url"] == API_BASE == "https://api.buffer.com"
    assert call["headers"]["Authorization"] == "Bearer tok"
    assert call["headers"]["Content-Type"] == "application/json"


def test_it_uses_the_createPost_mutation_and_CreatePostInput():
    transport = FakeTransport(_success())
    _publisher(transport).publish(_request())

    query = transport.calls[0]["payload"]["query"]
    assert "mutation CreatePost($input: CreatePostInput!)" in query
    assert "createPost(input: $input)" in query
    # The union arms must both be selected or errors come back empty.
    assert "... on PostActionSuccess" in query
    assert "... on MutationError" in query


def test_required_non_null_fields_are_always_sent():
    """assets, mode, and schedulingType are non-null; omitting one fails the call."""
    transport = FakeTransport(_success())
    _publisher(transport).publish(_request())

    sent = transport.calls[0]["payload"]["variables"]["input"]
    assert sent["channelId"] == "ch_li"
    assert sent["assets"] == [], "a text-only post must still send an empty asset list"
    assert sent["mode"] == MODE_ADD_TO_QUEUE
    assert sent["schedulingType"] == SCHEDULING_AUTOMATIC


def test_a_scheduled_post_uses_customScheduled_and_dueAt():
    transport = FakeTransport(_success())
    _publisher(transport).publish(_request(scheduled_for=NOW))

    sent = transport.calls[0]["payload"]["variables"]["input"]
    assert sent["mode"] == MODE_CUSTOM_SCHEDULED
    assert sent["dueAt"] == "2026-08-17T13:30:00.000Z"


def test_a_draft_uses_saveToDraft_not_isDraft():
    transport = FakeTransport(_success(status="draft"))
    result = _publisher(transport).publish(_request(create_as_draft=True))

    sent = transport.calls[0]["payload"]["variables"]["input"]
    assert sent["saveToDraft"] is True
    assert "isDraft" not in sent
    # Buffer's own approval flag is kept in step with ours.
    assert sent["needsApproval"] is True
    assert result.status == STATUS_SCHEDULED


def test_media_is_sent_as_assets():
    transport = FakeTransport(_success())
    _publisher(transport).publish(_request(platform="instagram", media_ids=["a1", "a2"]))

    sent = transport.calls[0]["payload"]["variables"]["input"]
    assert sent["assets"] == [{"id": "a1"}, {"id": "a2"}]
    assert sent["channelId"] == "ch_ig"


def test_a_first_comment_travels_in_metadata():
    transport = FakeTransport(_success())
    _publisher(transport).publish(_request(first_comment="#soar #cybersecurity"))

    sent = transport.calls[0]["payload"]["variables"]["input"]
    assert sent["metadata"] == {"linkedin": {"firstComment": "#soar #cybersecurity"}}


def test_a_sent_post_is_published_and_a_queued_one_is_scheduled():
    assert (
        _publisher(FakeTransport(_success(status="sent"))).publish(_request()).status
        == STATUS_PUBLISHED
    )
    assert (
        _publisher(FakeTransport(_success(status="buffer"))).publish(_request()).status
        == STATUS_SCHEDULED
    )


def test_a_mutation_error_is_a_refusal_not_a_blip():
    transport = FakeTransport({"data": {"createPost": {"message": "text too long"}}})
    result = _publisher(transport).publish(_request())

    assert result.status == STATUS_FAILED
    assert result.error_message == "text too long"
    assert result.retryable is False, "retrying a rejected post forever helps nobody"


def test_a_transport_outage_stays_retryable():
    transport = FakeTransport(error=ConnectionError("connection reset"))
    result = _publisher(transport).publish(_request())

    assert result.status == STATUS_FAILED
    assert result.retryable is True


def test_a_graphql_validation_error_is_not_retryable():
    transport = FakeTransport(
        {"errors": [{"message": "bad input", "extensions": {"code": "BAD_USER_INPUT"}}]}
    )
    result = _publisher(transport).publish(_request())

    assert result.status == STATUS_FAILED
    assert result.retryable is False


def test_a_missing_channel_fails_without_calling_buffer():
    transport = FakeTransport(_success())
    publisher = _publisher(transport, channels={})
    result = publisher.publish(_request())

    assert result.status == STATUS_FAILED
    assert result.retryable is False
    assert transport.calls == []


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #


def _metrics_response(metrics):
    return {"data": {"post": {"id": "post_1", "status": "sent", "metrics": metrics}}}


def test_metrics_are_mapped_by_type_not_by_position():
    """Post.metrics is a list of typed objects, not named numeric fields."""
    transport = FakeTransport(
        _metrics_response(
            [
                {"type": "reach", "name": "Reach", "value": 1200, "unit": "count"},
                {"type": "impressions", "name": "Impressions", "value": 2870, "unit": "count"},
                {"type": "comments", "name": "Comments", "value": 7, "unit": "count"},
                {"type": "reactions", "name": "Reactions", "value": 40, "unit": "count"},
            ]
        )
    )
    sample = _publisher(transport).fetch_metrics("post_1", "linkedin")

    # Deliberately out of order in the response: order must not matter.
    assert sample.impressions == 2870
    assert sample.reach == 1200
    assert sample.reactions == 40
    assert sample.comments == 7
    # Engagement is reactions + comments + shares + clicks over impressions,
    # rounded to 6 decimal places.
    assert sample.engagement_rate() == round(47 / 2870, 6)


def test_unknown_metric_types_are_ignored_rather_than_guessed():
    transport = FakeTransport(
        _metrics_response(
            [
                {"type": "impressions", "value": 100},
                {"type": "somethingNew", "value": 999},
            ]
        )
    )
    sample = _publisher(transport).fetch_metrics("post_1", "linkedin")
    assert sample.impressions == 100
    assert sample.clicks is None


def test_network_synonyms_fold_into_one_field():
    transport = FakeTransport(
        _metrics_response(
            [
                {"type": "likes", "value": 30},
                {"type": "reactions", "value": 5},
                {"type": "reposts", "value": 4},
                {"type": "shares", "value": 1},
            ]
        )
    )
    sample = _publisher(transport).fetch_metrics("post_1", "linkedin")
    assert sample.reactions == 35
    assert sample.shares == 5


def test_no_metrics_yet_returns_none_rather_than_zeroes():
    """A post that has not reported yet must not look like a post that flopped."""
    assert _publisher(FakeTransport(_metrics_response([]))).fetch_metrics("p", "linkedin") is None
    assert (
        _publisher(FakeTransport({"data": {"post": None}})).fetch_metrics("p", "linkedin") is None
    )
    assert (
        _publisher(FakeTransport(error=TimeoutError("slow"))).fetch_metrics("p", "linkedin")
        is None
    )


# --------------------------------------------------------------------------- #
# deletion and post URL
# --------------------------------------------------------------------------- #


def test_delete_post_uses_the_documented_mutation():
    transport = FakeTransport({"data": {"deletePost": {"id": "post_1"}}})
    assert _publisher(transport).delete_post("post_1") is True

    query = transport.calls[0]["payload"]["query"]
    assert "mutation DeletePost($input: DeletePostInput!)" in query
    assert "... on DeletePostSuccess" in query
    assert "... on VoidMutationError" in query
    assert transport.calls[0]["payload"]["variables"] == {"input": {"id": "post_1"}}


def test_a_failed_delete_is_reported_not_assumed():
    """A leftover test draft must be visible, never silently assumed gone."""
    assert _publisher(FakeTransport({"data": {"deletePost": {"message": "not found"}}})).delete_post("p") is False
    assert _publisher(FakeTransport({"errors": [{"message": "nope"}]})).delete_post("p") is False
    assert _publisher(FakeTransport(error=ConnectionError("down"))).delete_post("p") is False


def test_the_post_url_comes_from_externalLink():
    """`externalLink`, not `permalink`; the publications column was always NULL."""
    transport = FakeTransport(
        {
            "data": {
                "createPost": {
                    "post": {
                        "id": "post_1",
                        "status": "buffer",
                        "externalLink": "https://linkedin.com/feed/update/123",
                    }
                }
            }
        }
    )
    result = _publisher(transport).publish(_request())
    assert result.permalink == "https://linkedin.com/feed/update/123"
    assert "externalLink" in transport.calls[0]["payload"]["query"]


def test_a_post_with_sentAt_is_published_whatever_its_status_string():
    transport = FakeTransport(
        {"data": {"createPost": {"post": {"id": "p", "status": "weird", "sentAt": "2026-08-17T09:00:00Z"}}}}
    )
    assert _publisher(transport).publish(_request()).status == STATUS_PUBLISHED


def test_an_unknown_status_without_sentAt_reads_as_staged():
    """Staged is the safe reading; the raw response keeps the real value."""
    transport = FakeTransport({"data": {"createPost": {"post": {"id": "p", "status": "mystery"}}}})
    result = _publisher(transport).publish(_request())
    assert result.status == STATUS_SCHEDULED
    assert result.raw["data"]["createPost"]["post"]["status"] == "mystery"


# --------------------------------------------------------------------------- #
# schema corrections from live introspection, 2026-08-15
# --------------------------------------------------------------------------- #


def test_the_metrics_query_uses_PostInput_not_a_bare_id():
    """`post` takes PostInput, confirmed by introspecting the live schema."""
    transport = FakeTransport(_metrics_response([{"type": "impressions", "value": 10}]))
    _publisher(transport).fetch_metrics("post_1", "linkedin")

    payload = transport.calls[0]["payload"]
    assert "$input: PostInput!" in payload["query"]
    assert "post(input: $input)" in payload["query"]
    assert payload["variables"] == {"input": {"id": "post_1"}}


def test_a_float_metric_value_is_not_truncated_to_zero():
    """PostMetric.value is a Float. int() would turn a 3.4% rate into 0."""
    transport = FakeTransport(
        _metrics_response(
            [
                {"type": "impressions", "value": 2870.0},
                {"type": "engagementRate", "value": 0.0341},
            ]
        )
    )
    sample = _publisher(transport).fetch_metrics("post_1", "linkedin")
    assert sample.impressions == 2870
    assert sample.engagement_rate() == 0.0341


def test_buffers_own_engagement_rate_beats_our_derived_one():
    """The platform's definition is the one its other reporting will agree with."""
    transport = FakeTransport(
        _metrics_response(
            [
                {"type": "impressions", "value": 1000},
                {"type": "reactions", "value": 10},
                {"type": "engagementRate", "value": 0.25},
            ]
        )
    )
    sample = _publisher(transport).fetch_metrics("post_1", "linkedin")
    # Derived would be 10/1000 = 0.01. Buffer says 0.25, so Buffer wins.
    assert sample.engagement_rate() == 0.25


def test_a_post_reporting_only_a_rate_still_yields_a_sample():
    transport = FakeTransport(_metrics_response([{"type": "engagementRate", "value": 0.02}]))
    sample = _publisher(transport).fetch_metrics("post_1", "linkedin")
    assert sample is not None
    assert sample.engagement_rate() == 0.02


def test_every_metric_type_in_the_live_enum_is_recognised():
    """The 16 PostMetricType values read off the live schema on 2026-08-15."""
    from app.publisher.buffer import ALL_KNOWN_METRIC_TYPES

    live_enum = {
        "clicks", "comments", "engagementRate", "follows", "impressions", "likes",
        "postCount", "quotes", "reach", "reactions", "reposts", "saves",
        "shares", "totalTimeWatched", "viewers", "views",
    }
    unrecognised = sorted(v for v in live_enum if v.lower() not in ALL_KNOWN_METRIC_TYPES)
    assert not unrecognised, unrecognised
