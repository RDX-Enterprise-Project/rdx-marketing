"""Authenticated HTTP intake for sanitised CaptureOS TREND_SIGNAL payloads.

This is not a public website. CaptureOS posts one JSON object. Extra keys are
refused, not stripped. An empty or missing shared secret refuses every request.
"""

from __future__ import annotations

import datetime as dt
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

from .capture_bridge import EVENT_TREND_SIGNAL, convert_accepted_event, intake
from .clock import Clock
from .config import AppConfig, load_config
from .db import make_engine, transaction

UTC = dt.timezone.utc
MAX_BODY_BYTES = 4 * 1024
PATH = "/v1/capture/trends"
SECRET_ENV = "RDX_MARKETING_BRIDGE_SECRET"


def handle_trend_post(
    body: bytes,
    authorization: str,
    config: AppConfig,
    engine,
    now: dt.datetime,
) -> Tuple[int, Dict[str, Any]]:
    secret = (config.env_value(SECRET_ENV, "") or "").strip()
    if not secret:
        return 503, {"status": "unavailable", "reason": "bridge_secret_not_configured"}
    provided = authorization or ""
    if provided.lower().startswith("bearer "):
        provided = provided[7:].strip()
    if not hmac.compare_digest(provided, secret):
        return 401, {"status": "unauthorized"}
    if len(body) > MAX_BODY_BYTES:
        return 413, {"status": "rejected", "reason": "body_too_large"}
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"status": "rejected", "reason": "invalid_json"}
    if not isinstance(payload, dict):
        return 400, {"status": "rejected", "reason": "payload_must_be_object"}

    with transaction(engine) as conn:
        result = intake(conn, payload, now, event_type=EVENT_TREND_SIGNAL)
        content_id = convert_accepted_event(conn, config, result, now)

    if not result.accepted:
        return 400, {
            "status": result.status,
            "event_id": result.event_id,
            "reason": result.rejection_reason,
        }
    return 200, {
        "status": result.status,
        "event_id": result.event_id,
        "content_id": content_id,
    }


class _Handler(BaseHTTPRequestHandler):
    config: AppConfig
    engine: Any
    clock: Clock

    def log_message(self, fmt: str, *args) -> None:  # pragma: no cover
        return

    def do_POST(self) -> None:  # pragma: no cover - exercised via handle_trend_post
        if self.path.split("?", 1)[0] != PATH:
            self._write(404, {"status": "not_found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        status, payload = handle_trend_post(
            body,
            self.headers.get("Authorization") or "",
            self.config,
            self.engine,
            self.clock.now(),
        )
        self._write(status, payload)

    def _write(self, status: int, payload: Dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def serve(
    host: str = "127.0.0.1",
    port: int = 8088,
    config: Optional[AppConfig] = None,
    engine=None,
) -> ThreadingHTTPServer:  # pragma: no cover
    cfg = config or load_config()
    eng = engine or make_engine()
    handler = type(
        "CaptureHandler",
        (_Handler,),
        {"config": cfg, "engine": eng, "clock": Clock()},
    )
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:  # pragma: no cover
    host = os.environ.get("RDX_MARKETING_INTAKE_HOST", "127.0.0.1")
    port = int(os.environ.get("RDX_MARKETING_INTAKE_PORT", "8088"))
    httpd = serve(host, port)
    httpd.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
