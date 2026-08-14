"""Contract fixture loading.

Fixtures are captured API responses stored on disk. The contract tests run
against them offline, so the suite never needs the network and the fixtures are
what change when an upstream API changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple

FIXTURE_DIR = Path(__file__).resolve().parent

PROVENANCE_SYNTHETIC = "synthetic"
PROVENANCE_LIVE = "live"


def load(name: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return ``(payload, metadata)`` for a fixture."""
    path = FIXTURE_DIR / ("%s.json" % name)
    with path.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    return document["payload"], document.get("_fixture", {})


def payload(name: str) -> Dict[str, Any]:
    return load(name)[0]


def is_live(name: str) -> bool:
    """True once this fixture has been captured from the real API.

    A synthetic fixture pins parser behaviour but cannot prove the vendor
    documentation matches reality, which is the exact gap that let two wrong
    assumptions ship. Tests that need a real response skip until this is true.
    """
    return load(name)[1].get("provenance") == PROVENANCE_LIVE


def requires_live(name: str) -> str:
    """Skip reason for a test that only means something against a real capture."""
    return (
        "%s is still a synthetic fixture. Capture a real response with "
        "`python scripts/capture_fixtures.py` to activate this contract test." % name
    )
