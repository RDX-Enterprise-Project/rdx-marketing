"""The capture script's publication-state gate.

The capture procedure used to *assert* draft state before verifying it: it
printed "created draft", stamped the fixture "nothing was published", and
deleted the post, all without checking. This pins the corrected behaviour.

The rule that matters most: **unknown is treated as published.** The adapter's
``_status_from`` reads an unrecognised status as staged, which is the right
default for the engine — under-reporting a publication is worse than
over-reporting one — but the wrong default for deciding whether to delete
something. An unknown status is not evidence of a draft.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "capture_fixtures.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("capture_fixtures", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capture = _load_script()


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #


def test_a_documented_draft_is_confirmed_staged():
    assert capture.classify_publication_state({"id": "p", "status": "draft"}) == capture.STATE_STAGED


def test_a_queued_post_is_confirmed_staged():
    assert capture.classify_publication_state({"id": "p", "status": "buffer"}) == capture.STATE_STAGED


def test_a_sent_post_is_published():
    assert (
        capture.classify_publication_state({"id": "p", "status": "sent"})
        == capture.STATE_PUBLISHED
    )


def test_a_populated_sentAt_is_published_whatever_the_status_says():
    """sentAt is unambiguous; the status string is not."""
    for status in ("draft", "buffer", "sent", "anything", ""):
        assert (
            capture.classify_publication_state(
                {"id": "p", "status": status, "sentAt": "2026-08-17T09:00:00Z"}
            )
            == capture.STATE_PUBLISHED
        ), status


def test_an_unrecognised_status_is_ambiguous_not_staged():
    """The whole point of the fix: absence of evidence is not evidence of a draft."""
    for status in ("pending_approval", "queued_somewhere", "", None, "SENT_PARTIAL"):
        assert (
            capture.classify_publication_state({"id": "p", "status": status})
            == capture.STATE_AMBIGUOUS
        ), status


def test_ambiguous_is_never_confused_with_staged():
    """Only these two states may reach the delete path, and only one of them does."""
    staged = capture.classify_publication_state({"id": "p", "status": "draft"})
    ambiguous = capture.classify_publication_state({"id": "p", "status": "mystery"})
    assert staged == capture.STATE_STAGED
    assert ambiguous != capture.STATE_STAGED


def test_the_three_states_are_distinct():
    assert len({capture.STATE_STAGED, capture.STATE_PUBLISHED, capture.STATE_AMBIGUOUS}) == 3


def test_the_fixture_note_would_record_evidence_not_intent():
    """The stamped note must be the observed state, not a claim about intent."""
    state = capture.classify_publication_state({"id": "p", "status": "draft"})
    note = "Captured live. Observed publication state at capture: %s" % state
    assert "Observed publication state at capture: STAGED_NOT_SENT" in note
    assert "nothing was published" not in note


def test_classification_agrees_with_the_adapter_on_the_documented_statuses():
    """The script must not drift from the adapter it is meant to be testing."""
    from app.publisher.base import STATUS_PUBLISHED, STATUS_SCHEDULED
    from app.publisher.buffer import _status_from

    for status, expected_adapter in (("draft", STATUS_SCHEDULED), ("buffer", STATUS_SCHEDULED), ("sent", STATUS_PUBLISHED)):
        post = {"id": "p", "status": status}
        adapter = _status_from(post, True)
        script = capture.classify_publication_state(post)
        assert adapter == expected_adapter
        if adapter == STATUS_PUBLISHED:
            assert script == capture.STATE_PUBLISHED
        else:
            assert script == capture.STATE_STAGED


def test_the_draft_text_is_unmistakably_a_test():
    assert "DO NOT PUBLISH" in capture.DRAFT_TEXT
    assert "DO NOT PUBLISH" in capture.FIRST_COMMENT_TEXT
    assert "contract" in capture.DRAFT_TEXT.lower()
