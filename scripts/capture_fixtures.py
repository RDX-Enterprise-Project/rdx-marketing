#!/usr/bin/env python3
"""Capture live Buffer responses as sanitised contract fixtures.

**This script writes to your Buffer account.** Capturing a real ``createPost``
response requires actually calling ``createPost``. It is sent with
``saveToDraft: true``, which should staged rather than publish it — but that is
the assumption under test, so it is verified rather than assumed:

    create -> classify the returned post -> STAGED?  sanitise, write, delete
                                         -> anything else? STOP, preserve

An unknown status counts as published for cleanup purposes. ``_status_from``
reads an unrecognised status as staged, which is right for the engine and wrong
here: absence of evidence that a post went out is not evidence that it did not.
On abort nothing is deleted and no fixture is written, because deleting would
destroy the evidence and, if the post did go out, a delete through Buffer may
not retract it from the network.

Because of that, it does nothing without ``--execute``:

    export BUFFER_ACCESS_TOKEN="..."
    export BUFFER_CHANNEL_LINKEDIN="..."

    python scripts/capture_fixtures.py                    # dry run, sends nothing
    python scripts/capture_fixtures.py --execute          # creates ONE draft
    python scripts/capture_fixtures.py --execute --metrics-post-id <id>

Metrics cannot be captured from a fresh draft: a post that has not been sent
has no metrics. Pass ``--metrics-post-id`` with the id of a post that has
actually gone out to capture that fixture.

Fixtures are committed, so channel ids, post ids, and anything token-shaped are
stripped on the way in. Read `git diff tests/fixtures/` before committing.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.publisher.base import STATUS_PUBLISHED  # noqa: E402
from app.publisher.buffer import (  # noqa: E402
    API_BASE,
    CREATE_POST,
    FIRST_COMMENT_METADATA_KEY,
    MODE_ADD_TO_QUEUE,
    POST_METRICS,
    SCHEDULING_AUTOMATIC,
    STAGED_STATUSES,
    _status_from,
)

# Observed publication state at capture. Three-way on purpose: "staged" and
# "we cannot tell" are different findings, and only one of them is safe to
# clean up automatically.
STATE_STAGED = "STAGED_NOT_SENT"
STATE_PUBLISHED = "PUBLISHED"
STATE_AMBIGUOUS = "AMBIGUOUS"


def classify_publication_state(post: Dict[str, Any]) -> str:
    """What the response actually shows, using the adapter's own status logic.

    Fails closed. ``_status_from`` reads an unrecognised status as staged, which
    is the right default for the engine but the wrong one for deciding whether
    to delete something: an unknown status is not evidence of a draft. Only a
    documented staged status with no ``sentAt`` counts as confirmed.
    """
    if _status_from(post, True) == STATUS_PUBLISHED:
        return STATE_PUBLISHED
    if post.get("sentAt"):
        return STATE_PUBLISHED
    if str(post.get("status", "")).lower() in STAGED_STATUSES:
        return STATE_STAGED
    return STATE_AMBIGUOUS

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# Unmistakably a test, in case anything ever looks at the account.
DRAFT_TEXT = (
    "RDX Adapter Contract Test - DO NOT PUBLISH. "
    "Created by scripts/capture_fixtures.py to pin the Buffer API schema. "
    "Safe to delete."
)
FIRST_COMMENT_TEXT = "RDX Adapter Contract Test - DO NOT PUBLISH."

REDACTED_CHANNEL = "REDACTED_CHANNEL_ID"
REDACTED_POST = "REDACTED_POST_ID"

#: Anything that looks like a credential must never reach a committed file.
TOKEN_PATTERN = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)


def sanitise(node: Any, channel_id: str, post_ids: List[str], notes: List[str]) -> Any:
    if isinstance(node, dict):
        cleaned: Dict[str, Any] = {}
        for key, value in node.items():
            if key in ("channelId", "channel_id") and isinstance(value, str):
                cleaned[key] = REDACTED_CHANNEL
                notes.append("redacted channelId")
            elif key in ("id", "postId", "organizationId") and isinstance(value, str):
                cleaned[key] = REDACTED_POST if value in post_ids else _redact_id(value, notes)
            else:
                cleaned[key] = sanitise(value, channel_id, post_ids, notes)
        return cleaned
    if isinstance(node, list):
        return [sanitise(item, channel_id, post_ids, notes) for item in node]
    if isinstance(node, str):
        text = node
        if channel_id and channel_id in text:
            text = text.replace(channel_id, REDACTED_CHANNEL)
            notes.append("redacted channelId inside a string")
        for post_id in post_ids:
            if post_id and post_id in text:
                text = text.replace(post_id, REDACTED_POST)
        text, count = TOKEN_PATTERN.subn(r"\1REDACTED", text)
        if count:
            notes.append("stripped a bearer token")
        return text
    return node


def _redact_id(value: str, notes: List[str]) -> str:
    notes.append("redacted an account-scoped id")
    return REDACTED_POST


def write_fixture(name: str, document: Dict[str, Any]) -> Path:
    path = FIXTURE_DIR / ("%s.json" % name)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _meta(api: str, sanitised: List[str], note: str) -> Dict[str, Any]:
    return {
        "provenance": "live",
        "captured_at": dt.datetime.now(tz=dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "api": api,
        "endpoint": API_BASE,
        "documented_at": "2026-08-14",
        "sanitised": sorted(set(sanitised)),
        "note": note,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Capture Buffer contract fixtures")
    parser.add_argument("--token", default=None, help="defaults to $BUFFER_ACCESS_TOKEN")
    parser.add_argument("--channel", default=None, help="defaults to $BUFFER_CHANNEL_LINKEDIN")
    parser.add_argument("--platform", default="linkedin", choices=sorted(FIRST_COMMENT_METADATA_KEY))
    parser.add_argument(
        "--metrics-post-id",
        default=None,
        help="id of an already-SENT post; a fresh draft has no metrics",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually call Buffer. Without this the script only prints the request.",
    )
    parser.add_argument(
        "--keep-draft",
        action="store_true",
        help="do not delete the test draft afterwards (default is to delete it)",
    )
    args = parser.parse_args(argv)

    token = args.token or os.environ.get("BUFFER_ACCESS_TOKEN", "")
    channel = args.channel or os.environ.get(
        "BUFFER_CHANNEL_%s" % args.platform.upper(), ""
    )

    variables = {
        "input": {
            "channelId": channel or "<BUFFER_CHANNEL_%s>" % args.platform.upper(),
            "text": DRAFT_TEXT,
            "assets": [],
            "mode": MODE_ADD_TO_QUEUE,
            "schedulingType": SCHEDULING_AUTOMATIC,
            "saveToDraft": True,
            "needsApproval": True,
            "metadata": {
                FIRST_COMMENT_METADATA_KEY[args.platform]: {
                    "firstComment": FIRST_COMMENT_TEXT
                }
            },
        }
    }

    print("POST %s" % API_BASE)
    print("Authorization: Bearer REDACTED")
    print()
    print("createPost input:")
    printable = copy.deepcopy(variables["input"])
    printable["channelId"] = REDACTED_CHANNEL
    print(json.dumps(printable, indent=2))
    print()

    if not args.execute:
        print("Dry run. Nothing sent.")
        print()
        print("This call CREATES A DRAFT in your Buffer account. It is staged, not")
        print("published, and the script prints its id so you can delete it.")
        print("Re-run with --execute when you are ready.")
        return 0

    if not token:
        print("BUFFER_ACCESS_TOKEN is not set", file=sys.stderr)
        return 1
    if not channel:
        print(
            "no channel id; set BUFFER_CHANNEL_%s or pass --channel"
            % args.platform.upper(),
            file=sys.stderr,
        )
        return 1

    from app.collectors_http import RequestsHttpClient

    http = RequestsHttpClient()
    headers = {"Authorization": "Bearer %s" % token, "Content-Type": "application/json"}

    try:
        response = http.post_json(
            API_BASE, {"query": CREATE_POST, "variables": variables}, headers=headers, timeout=45
        )
    except Exception as exc:  # noqa: BLE001
        print("createPost failed: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1

    created = ((response.get("data") or {}).get("createPost") or {})
    post = created.get("post") or {}
    post_id = str(post.get("id") or "")

    if not post_id:
        print("Buffer returned no post. Response: %s" % json.dumps(response)[:500])
        return 1

    # Verify before asserting anything, and before deleting anything.
    state = classify_publication_state(post)
    print("Buffer post id: %s" % post_id)
    print("Observed publication state: %s" % state)

    if state != STATE_STAGED:
        print()
        print("!! ABORTING. The post is not confirmed staged.")
        print("!!")
        print("!!   post id     : %s" % post_id)
        print("!!   status      : %r" % post.get("status"))
        print("!!   sentAt      : %r" % post.get("sentAt"))
        print("!!   externalLink: %r" % post.get("externalLink"))
        print("!!")
        if state == STATE_PUBLISHED:
            print("!! This post appears to have been PUBLISHED, not drafted.")
            print("!! saveToDraft did not do what the adapter assumes.")
        else:
            print("!! The status is one this tool does not recognise, so it cannot")
            print("!! confirm the post is unpublished. Unknown is treated as published.")
        print("!!")
        print("!! The post has NOT been deleted, and no fixture was written.")
        print("!! Deleting could destroy the evidence, and if it did go out, a")
        print("!! delete here may not retract it from the network.")
        print("!! Inspect it in Buffer and clean up deliberately.")
        print("!! Do not enable publishing until this is understood.")
        return 2

    notes: List[str] = []
    document = {
        "_fixture": _meta(
            "Buffer createPost (saveToDraft: true)",
            notes,
            # Evidence, not intent: what was observed, not what was intended.
            "Captured live. Observed publication state at capture: %s" % state,
        ),
        "request": {
            "query_name": "CreatePost",
            "variables": sanitise(
                copy.deepcopy(variables), channel, [post_id], notes
            ),
        },
        "payload": sanitise(copy.deepcopy(response), channel, [post_id], notes),
    }
    document["_fixture"]["sanitised"] = sorted(set(notes))
    print("wrote %s" % write_fixture("buffer_create_post_draft", document).name)

    # Clean up. Only reachable once the post is *confirmed* staged: the abort
    # above returns before this point for published or ambiguous states.
    if not args.keep_draft:
        from app.publisher.buffer import BufferConfig, BufferPublisher

        publisher = BufferPublisher(
            http, BufferConfig(token=token, channels={args.platform: channel})
        )
        if publisher.delete_post(post_id):
            print("deleted the test draft (%s)" % post_id)
        else:
            print()
            print("!! COULD NOT DELETE the test draft.")
            print("!! Post id %s is still in your Buffer account." % post_id)
            print("!! It is marked 'RDX Adapter Contract Test - DO NOT PUBLISH'.")
            print("!! Delete it manually.")
    else:
        print("--keep-draft: draft %s left in the account, marked as a test." % post_id)

    if args.metrics_post_id:
        try:
            metrics_response = http.post_json(
                API_BASE,
                {"query": POST_METRICS, "variables": {"id": args.metrics_post_id}},
                headers=headers,
                timeout=45,
            )
        except Exception as exc:  # noqa: BLE001
            print("metrics query failed: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
            metrics_response = None

        if metrics_response is not None:
            metric_notes: List[str] = []
            metrics_document = {
                "_fixture": _meta(
                    "Buffer Post.metrics",
                    metric_notes,
                    "Captured live from an already-sent post.",
                ),
                "payload": sanitise(
                    copy.deepcopy(metrics_response),
                    channel,
                    [args.metrics_post_id],
                    metric_notes,
                ),
            }
            metrics_document["_fixture"]["sanitised"] = sorted(set(metric_notes))
            print("wrote %s" % write_fixture("buffer_post_metrics", metrics_document).name)
            notes.extend(metric_notes)
    else:
        print()
        print("No --metrics-post-id given, so the metrics fixture was not updated.")
        print("A fresh draft has no metrics; pass the id of a post that has been sent.")

    print()
    if notes:
        print("sanitiser changes:")
        for note in sorted(set(notes)):
            print("  - %s (x%d)" % (note, notes.count(note)))
    print()
    print("Read `git diff tests/fixtures/` before committing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
