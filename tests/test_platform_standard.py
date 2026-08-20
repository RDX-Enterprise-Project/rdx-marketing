"""RDX Platform Standard v1 is present and production freeze is intact."""

from __future__ import annotations

from pathlib import Path

from app.config import load_config
from app.content.item import HUMAN_APPROVAL_REQUIRED

ROOT = Path(__file__).resolve().parent.parent
DOCS = (
    "README.md",
    "ARCHITECTURE.md",
    "OPERATIONS.md",
    "SECURITY.md",
    "INTERFACES.md",
    "docs/RDX-PLATFORM-STANDARD-v1.md",
    "docs/PHASE-13-BACKLOG.md",
)
TREND_KEYS = ("signal_code", "observed_period", "direction", "confidence")


def test_required_platform_docs_exist():
    for name in DOCS:
        path = ROOT / name
        assert path.is_file(), name
        assert path.stat().st_size > 200, name


def test_standard_v1_lifecycle_and_contract():
    text = (ROOT / "docs/RDX-PLATFORM-STANDARD-v1.md").read_text(encoding="utf-8")
    assert "LOCAL → CONTROLLED → GREEN → SCHEDULED" in text
    assert "TREND_SIGNAL v1" in text
    for key in TREND_KEYS:
        assert key in text


def test_interfaces_document_trend_signal_v1():
    text = (ROOT / "INTERFACES.md").read_text(encoding="utf-8")
    for key in TREND_KEYS:
        assert key in text
    assert "/v1/capture/trends" in text
    assert "/health" in text


def test_operator_json_names_the_service():
    text = (ROOT / "app/daily_run.py").read_text(encoding="utf-8")
    assert '"service": "rdx-marketing"' in text


def test_production_freeze_is_unchanged():
    config = load_config()
    assert config.platforms.publisher.get("enabled") is False
    assert config.ai.enabled is False
    assert int(config.ai.budget.get("max_calls_per_run") or 0) == 0
    assert config.policy.default_approval_requirement == HUMAN_APPROVAL_REQUIRED
    wrangler = (ROOT / "receiver" / "wrangler.jsonc").read_text(encoding="utf-8")
    assert "BUFFER_" not in wrangler
    assert "linkedin" not in wrangler.lower()


def test_phase_13_is_backlog_not_authorised():
    text = (ROOT / "docs/PHASE-13-BACKLOG.md").read_text(encoding="utf-8")
    assert "FOLLOW-UP, NOT BLOCKERS" in text
    assert "Not authorised for implementation" in text
    assert "HUMAN_APPROVAL_REQUIRED" in text
