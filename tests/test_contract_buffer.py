"""Contract tests: the Buffer response shapes the adapter depends on.

Offline, against stored fixtures. When Buffer changes its schema, re-capturing
the fixture makes one named test fail instead of the engine silently reporting
every post as having zero engagement.

The live-only tests are the ones that settle the two remaining unknowns: the
``PostInputMetaData`` key for a first comment, and the scalar name in the
metrics query. They skip, loudly, until a real response is captured.
"""

from __future__ import annotations

import pytest

from app.publisher.buffer import (
    ALL_KNOWN_METRIC_TYPES,
    FIRST_COMMENT_METADATA_KEY,
    CREATE_POST,
    CREATE_POST_REQUIRED_INPUT,
    CREATE_POST_RESPONSE_KEYS,
    METRIC_ENTRY_KEYS,
    METRIC_TYPE_MAP,
    POST_METRICS,
    BufferConfig,
    BufferPublisher,
)

from . import fixtures
from .test_buffer_adapter import CHANNELS, FakeTransport, _request

CREATE = "buffer_create_post_draft"
METRICS = "buffer_post_metrics"


def _publisher(transport):
    return BufferPublisher(
        transport, BufferConfig(token="tok", channels=dict(CHANNELS))
    )


# --------------------------------------------------------------------------- #
# createPost
# --------------------------------------------------------------------------- #


def _document():
    """The whole fixture, including the `request` block.

    The create fixture stores both halves: what was sent and what came back.
    The request half is what proves the non-null input fields were present, and
    it is the half that would silently drift if only the response were pinned.
    """
    import json
    from pathlib import Path

    path = Path(fixtures.FIXTURE_DIR) / ("%s.json" % CREATE)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _request_block():
    return _document()["request"]


def test_the_captured_request_carries_every_non_null_input_field():
    """The failure mode this pins: omitting a non-null field fails the call."""
    sent = _request_block()["variables"]["input"]
    for key in CREATE_POST_REQUIRED_INPUT:
        assert key in sent, "createPost input lost required field %r" % key


def test_a_text_only_post_still_sends_an_empty_asset_list():
    sent = _request_block()["variables"]["input"]
    assert sent["assets"] == [], "assets is non-null; a text post must send []"


def test_the_draft_flag_is_saveToDraft():
    sent = _request_block()["variables"]["input"]
    assert sent["saveToDraft"] is True
    assert "isDraft" not in sent, "isDraft does not exist on CreatePostInput"


def test_the_parser_reads_the_captured_create_response():
    payload = fixtures.payload(CREATE)
    transport = FakeTransport(payload)
    result = _publisher(transport).publish(_request(create_as_draft=True))

    assert result.ok
    assert result.provider_post_id
    # A draft is staged, not live.
    assert result.status == "SCHEDULED"


def test_the_create_response_carries_the_keys_the_parser_reads():
    post = fixtures.payload(CREATE)["data"]["createPost"]["post"]
    for key in CREATE_POST_RESPONSE_KEYS:
        assert key in post, "createPost response lost %r" % key


def test_both_union_arms_are_selected_in_the_query():
    """A union query that only selects the success arm swallows errors."""
    assert "... on PostActionSuccess" in CREATE_POST
    assert "... on MutationError" in CREATE_POST


def test_no_account_identifiers_survive_in_the_fixture():
    """Fixtures are committed, so channel and post ids must be redacted."""
    import json
    from pathlib import Path

    path = Path(fixtures.FIXTURE_DIR) / ("%s.json" % CREATE)
    raw = path.read_text(encoding="utf-8")
    document = json.loads(raw)

    channel = document["request"]["variables"]["input"]["channelId"]
    assert channel.startswith("REDACTED"), "unsanitised channel id in fixture"
    post_id = document["payload"]["data"]["createPost"]["post"]["id"]
    assert post_id.startswith("REDACTED"), "unsanitised post id in fixture"
    assert "Bearer" not in raw, "an access token reached the fixture"


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #


def test_metrics_are_a_list_of_typed_objects():
    """The shape that broke the first implementation, pinned.

    Reading Post.metrics as named numeric fields returns nothing and reports
    every post as zero engagement, which fails silently rather than loudly.
    """
    metrics = fixtures.payload(METRICS)["data"]["post"]["metrics"]
    assert isinstance(metrics, list)
    for entry in metrics:
        for key in METRIC_ENTRY_KEYS:
            assert key in entry, "metric entry lost %r" % key


def test_the_parser_reads_the_captured_metrics_response():
    transport = FakeTransport(fixtures.payload(METRICS))
    sample = _publisher(transport).fetch_metrics("post_1", "linkedin")

    assert sample is not None
    assert sample.impressions == 2870
    assert sample.reach == 1980
    assert sample.reactions == 41
    assert sample.comments == 6
    assert sample.shares == 3
    assert sample.clicks == 52
    assert sample.engagement_rate() == round(102 / 2870, 6)


def test_every_metric_type_in_the_fixture_is_one_the_adapter_maps():
    """An unmapped type is silently dropped, so the contract names them."""
    metrics = fixtures.payload(METRICS)["data"]["post"]["metrics"]
    unknown = sorted(
        {
            str(entry["type"])
            for entry in metrics
            if str(entry["type"]).lower() not in ALL_KNOWN_METRIC_TYPES
        }
    )
    assert not unknown, (
        "Buffer reports metric type(s) the adapter does not recognise at all: %s. "
        "Either map them in METRIC_TYPE_MAP or list them in "
        "KNOWN_UNMAPPED_METRICS, so a deliberate omission stays distinct from a "
        "silent drop." % unknown
    )


# --------------------------------------------------------------------------- #
# live-only
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not fixtures.is_live(CREATE), reason=fixtures.requires_live(CREATE))
def test_the_live_call_was_accepted_at_all():
    """Contract, not content: Buffer took the request we actually send.

    Asserts nothing about the draft's text or id, because the next capture will
    be a different draft. What must hold is that the mutation, the input type,
    and every non-null field were acceptable together.
    """
    payload = fixtures.payload(CREATE)
    assert not payload.get("errors"), (
        "Buffer rejected the request at the GraphQL layer: %s. The mutation name, "
        "input type, or a field type in CREATE_POST is wrong." % payload.get("errors")
    )
    create = (payload.get("data") or {}).get("createPost") or {}
    assert create.get("post"), (
        "createPost returned the error arm: %s. Whatever it names is the field to "
        "correct in app/publisher/buffer.py." % create.get("message")
    )


@pytest.mark.skipif(not fixtures.is_live(CREATE), reason=fixtures.requires_live(CREATE))
def test_the_live_response_confirms_the_first_comment_metadata_key():
    """Unknown #1, settled by acceptance.

    ``metadata.linkedin.firstComment`` was sent and Buffer created the post.
    An earlier capture on a free plan had this rejected with "LinkedIn first
    comment requires a paid plan" — which already showed the key was *parsed*,
    but acceptance is the stronger evidence and is what this now pins.

    Note the operational dependency: first comments need a paid Buffer plan.
    If the plan lapses this call starts failing, and the Instagram and LinkedIn
    adapters put hashtags in the first comment.
    """
    sent = _request_block()["variables"]["input"]
    assert sent.get("metadata"), (
        "the capture omitted the first comment; re-capture without "
        "--no-first-comment so the metadata key stays pinned"
    )
    assert set(sent["metadata"]) <= set(FIRST_COMMENT_METADATA_KEY.values()), (
        "the metadata key is not one the adapter knows: %s" % sorted(sent["metadata"])
    )

    create = fixtures.payload(CREATE)["data"]["createPost"]
    assert create.get("post"), (
        "Buffer rejected the request carrying the first comment: %s"
        % create.get("message")
    )


@pytest.mark.skipif(not fixtures.is_live(CREATE), reason=fixtures.requires_live(CREATE))
def test_the_live_draft_came_back_staged_and_not_sent():
    """The safety assertion: saveToDraft must actually mean staged.

    Uses the adapter's own status logic rather than string-matching the fixture,
    so this tests what the engine would conclude, not what the JSON happens to
    say.
    """
    from app.publisher.base import STATUS_PUBLISHED

    transport = FakeTransport(fixtures.payload(CREATE))
    result = _publisher(transport).publish(_request(create_as_draft=True))

    assert result.status != STATUS_PUBLISHED, (
        "the contract capture PUBLISHED a post instead of drafting it. "
        "saveToDraft is not doing what the adapter assumes; do not enable "
        "publishing until this is understood."
    )
    post = fixtures.payload(CREATE)["data"]["createPost"]["post"]
    assert not post.get("sentAt"), "a draft came back with sentAt populated"


@pytest.mark.skipif(not fixtures.is_live(METRICS), reason=fixtures.requires_live(METRICS))
def test_the_live_metrics_query_was_accepted():
    """Unknown #2: the scalar name in the query signature."""
    payload = fixtures.payload(METRICS)
    assert not payload.get("errors"), (
        "the metrics query was rejected: %s. Correct the scalar name in POST_METRICS."
        % payload.get("errors")
    )
    assert (payload.get("data") or {}).get("post"), "metrics query returned no post"


@pytest.mark.skipif(not fixtures.is_live(METRICS), reason=fixtures.requires_live(METRICS))
def test_every_live_metric_type_is_one_the_adapter_maps():
    """An unmapped type is dropped silently, so a live capture must name them all."""
    metrics = (fixtures.payload(METRICS)["data"]["post"] or {}).get("metrics") or []
    unmapped = sorted(
        {
            str(m["type"])
            for m in metrics
            if isinstance(m, dict) and str(m.get("type", "")).lower() not in METRIC_TYPE_MAP
        }
    )
    assert not unmapped, (
        "Buffer reports metric type(s) the adapter drops on the floor: %s. Add them "
        "to METRIC_TYPE_MAP or they never reach the weekly report." % unmapped
    )
