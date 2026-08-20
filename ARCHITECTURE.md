# RDX Marketing architecture

Service: `rdx-marketing`. Standard: `docs/RDX-PLATFORM-STANDARD-v1.md`.

## What it is

A scheduled marketing engine: calendar → content object → platform variants →
policy → approval → (publisher). Creation and publication are separate
authorities. CaptureOS is an optional inbound event source, not a shared
database.

```
calendar + TREND_SIGNAL v1 ──> content item (DRAFT)
                                    │
                                    ▼
                             policy engine (fail closed)
                                    │
                    HUMAN_APPROVAL_REQUIRED ──> queue ──> publisher (OFF)
```

## Process boundaries

| Process | Command | Database |
| --- | --- | --- |
| Daily job | `python -m app.daily_run` | `RDX_MARKETING_DATABASE_URL` |
| Weekly report | `python -m app.weekly_report` | same |
| Capture receiver | Cloudflare Worker `rdx-marketing-capture` (production HTTPS); `python -m app.capture_http` (local/fallback) | same |

The daily job does not serve HTTP. The receiver does not publish.

## Ownership

- **Data:** dedicated Neon `rdx_marketing`. Not the CaptureOS database.
- **Config:** `config/policy.yaml`, `pillars.yaml`, `cadence.yaml`, `platforms.yaml`, `ai.yaml`.
- **Draft identity:** `MKT-YYYY-NNNNN`.
- **Event identity:** `EVT-` + hash of sanitised `(signal_code, observed_period)`.

## Failure domain

Publisher disabled → `NullPublisher`. CaptureOS down → no new events; the
calendar still runs. A rejected inbound payload does not become content.

## Layout

See `README.md`. Receiver: `receiver/` (Worker) and `app/capture_http.py`.
Policy: `app/policy/engine.py`. Bridge: `app/capture_bridge.py`.
