"""Persistent capture receiver: HTTPS artifacts, secret-by-name, no publishing surface."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_binds_publicly_and_has_no_secret_literals():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "RDX_MARKETING_INTAKE_HOST=0.0.0.0" in text
    assert '"app.capture_http"' in text
    assert "RDX_MARKETING_BRIDGE_SECRET=" not in text.replace(
        "RDX_MARKETING_INTAKE_HOST=0.0.0.0", ""
    )
    assert "postgresql+" not in text
    assert "Bearer " not in text
    assert "BUFFER_" not in text


def test_render_yaml_marks_secrets_unsynced():
    payload = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    service = payload["services"][0]
    assert service["healthCheckPath"] == "/health"
    by_key = {item["key"]: item for item in service["envVars"]}
    assert by_key["RDX_MARKETING_BRIDGE_SECRET"]["sync"] is False
    assert by_key["RDX_MARKETING_DATABASE_URL"]["sync"] is False
    assert "value" not in by_key["RDX_MARKETING_BRIDGE_SECRET"]
    assert "value" not in by_key["RDX_MARKETING_DATABASE_URL"]
    assert "BUFFER_ACCESS_TOKEN" not in by_key


def test_wrangler_receiver_is_intake_only():
    text = (ROOT / "receiver" / "wrangler.jsonc").read_text(encoding="utf-8")
    assert '"name": "rdx-marketing-capture"' in text
    assert "workers.dev" in text.lower() or '"workers_dev": true' in text
    assert "BUFFER_" not in text
    assert "linkedin" not in text.lower()
    src = (ROOT / "receiver" / "src" / "index.ts").read_text(encoding="utf-8")
    assert "/v1/capture/trends" in src
    assert "/health" in src
    assert "HUMAN_APPROVAL_REQUIRED" in src
    assert "NullPublisher" not in src
    assert "buffer.com" not in src.lower()
    assert "api.linkedin.com" not in src.lower()
