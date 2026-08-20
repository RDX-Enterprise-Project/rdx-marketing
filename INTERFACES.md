# RDX Marketing interfaces

Standard and full wire spec: `docs/RDX-PLATFORM-STANDARD-v1.md` (TREND_SIGNAL v1).

## Inbound — TREND_SIGNAL v1

Production: `https://rdx-marketing-capture.william-farrell.workers.dev`

| Method | Path | Auth |
| --- | --- | --- |
| GET/HEAD | `/health` | none |
| POST | `/v1/capture/trends` | Bearer `RDX_MARKETING_BRIDGE_SECRET` |

Local fallback: `python -m app.capture_http` (loopback unless `PORT` is set).

Allowed body keys only: `signal_code`, `observed_period`, `direction`,
`confidence`. Max 4 KiB. Duplicate identity is one event and one draft.

HTTP: 200 accept/idempotent, 400 unsanitised, 401 unauthorized, 404/405,
413 too large, 503 unconfigured, 5xx fail-open at the sender.

Accepted → one `HUMAN_APPROVAL_REQUIRED` / `DRAFT` content item. Not a
publication. Marketing remains useful if CaptureOS never posts.

## Outbound — Buffer / LinkedIn / Facebook / Instagram

`publisher.enabled: false`. `NullPublisher`. No live outbound contract.

## Outbound — none to CaptureOS

Marketing does not write to the intelligence database and does not call
CaptureOS HTTP.
