#!/usr/bin/env python3
"""Capture live Buffer responses as sanitised contract fixtures.

**This script writes to your Buffer account.** Capturing a real ``createPost``
response requires actually calling ``createPost``. It is sent with
``saveToDraft: true`` so the post is staged, never published, but a draft does
appear in Buffer and you should delete it afterwards. The script prints the id.

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

from app.publisher.buffer import (  # noqa: E402
    API_BASE,
    CREATE_POST,
    FIRST_COMMENT_METADATA_KEY,
    MODE_ADD_TO_QUEUE,
    POST_METRICS,
    SCHEDULING_AUTOMATIC,
)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

DRAFT_TEXT = (
    "RDX contract fixture capture. This is a draft created by "
    "scripts/capture_fixtures.py to pin the Buffer API shape. Safe to delete."
)
FIRST_COMMENT_TEXT = "Contract fixture capture."

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

    if post_id:
        print("Buffer created draft post id: %s" % post_id)
        print("Delete it in Buffer when you are done.")
    else:
        print("Buffer returned no post. Response: %s" % json.dumps(response)[:500])

    notes: List[str] = []
    document = {
        "_fixture": _meta(
            "Buffer createPost (saveToDraft: true)",
            notes,
            "Captured live. Draft only; nothing was published.",
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
