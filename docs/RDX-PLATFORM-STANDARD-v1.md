# RDX Platform Standard v1

Every RDX platform owns its own data, deployment, schedule, secrets, health,
tests, and failure domain. Platforms communicate only through documented
versioned contracts.

This file is the reusable standard. Platform-specific facts live in
`README.md`, `ARCHITECTURE.md`, `OPERATIONS.md`, `SECURITY.md`, and
`INTERFACES.md`.

## Lifecycle

```
LOCAL → CONTROLLED → GREEN → SCHEDULED
```

| Stage | Meaning |
| --- | --- |
| LOCAL | Code and tests. SQLite or a non-production branch. No live side effects. |
| CONTROLLED | One authorised live action with a named expected outcome. |
| GREEN | Controlled action matched the expected outcome. Production controls stay frozen. |
| SCHEDULED | The same path runs unattended. Observation, not invention. |

Do not skip stages. Do not treat GREEN as permission to expand eligibility,
raise budgets, or turn on a second hop.

## Repository structure

A platform repository contains:

```
app/                 runtime
config/              versioned judgement (not code)
migrations/          ordered SQL
tests/               offline-first; contract fixtures where a vendor exists
scripts/             operator entrypoints
.github/workflows/   schedule + workflow_dispatch; tests on that workflow
docs/                this standard, plus follow-up designs
README.md
ARCHITECTURE.md
OPERATIONS.md
SECURITY.md
INTERFACES.md
```

Optional hosting artifacts (`Dockerfile`, `render.yaml`, `fly.toml`, a Worker)
are allowed. They must contain no secret literals.

## Ownership

| Concern | Rule |
| --- | --- |
| Service | One GitHub repository. One runtime identity (`rdx-intelligence`, `rdx-marketing`). |
| Database | Dedicated Postgres. No cross-table reads between platforms. |
| Deployment | Owned by the platform. No shared app host. |
| Schedule | Owned by the platform workflow. Timezone declared on the cron. |
| Secrets | GitHub Actions secrets or the host secret store, injected by **name only**. |
| Health | Documented. Unauthenticated liveness only. Authenticated work stays on its path. |
| Tests | `python -m pytest` in the platform repo. A green suite is a deploy gate. |
| Failure domain | A down peer never takes down this platform's primary job. |

## Environment and secrets

- Prefix platform-owned names with `RDX_`. Vendor names stay vendor-shaped (`SAM_API_KEY`, `XAI_API_KEY`, `BUFFER_ACCESS_TOKEN`).
- Config files store **env names**, never values.
- Workflows map `${{ secrets.NAME }}` to `NAME`. No inline values.
- `.env` is gitignored. `.env.example` lists names only.
- Rotating a shared secret requires updating every holder in the same change window.

## Health and readiness

- `GET /health` is process/database liveness. No auth. No secrets. No business payload.
- Authenticated intake is a different path (`POST /v1/...`).
- A scheduled job's health is the workflow conclusion plus the `runs` row. It does not need an HTTP server.

## Scheduled jobs

- Trigger: `schedule` + `workflow_dispatch`. Push does not run the production job.
- Concurrency group per platform; do not cancel an in-progress run.
- `--json` stdout is the operator envelope. The `runs` row is the system of record.
- A `PARTIAL` run is a real outcome (some sources/slots failed). Exit 0 for `OK` and `PARTIAL`.

## Run envelope

Target operator JSON (adopt without a forced migration):

| Field | Meaning |
| --- | --- |
| `run_id` | Stable id for the attempt |
| `service` | Platform identity |
| `version` | Config/schema version that governed the run |
| `status` | `OK` / `PARTIAL` / `FAILED` |
| `started_at` / `finished_at` | UTC |
| `records_in` / `records_out` | Platform-specific counts |
| `errors` | Bounded error list or notes |
| `external_calls` | Vendor HTTP / AI attempts |
| `estimated_cost` | Advisory USD, not a bill |
| `downstream_status` | Peer contract outcomes (e.g. `marketing_bridge`) |

**Current alignment:** both platforms persist `run_id`, `status`, timestamps, AI counts/cost, and notes on a `runs` row. Stdout JSON is a subset. Filling the remaining envelope fields is follow-up, not a blocker.

## Migrations

- Canonical schema lives in `app/models.py`.
- `migrations/` is the ordered Postgres apply path.
- Additive, reviewed, authorised. A code increment does not apply production DDL by surprise.
- SQLite remains the test dialect.

## Failure semantics

- Collector/source isolation: one failed adapter is `PARTIAL` or `SKIPPED`, not a crash of the run.
- Downstream peers are **fail-open** unless the contract says otherwise. CaptureOS continues if Marketing is down.
- Inbound contracts **fail closed** on auth, shape, and sensitive data.
- Missing classification, missing secret, or missing approval is refusal, not permission.

## API and event contracts

- Versioned by name (`TREND_SIGNAL v1`). Adding a field is a new version.
- Refuse extra keys. Do not redact-and-accept.
- Document auth, timeout, retries, idempotency, status codes, size limit, and the sensitive-data prohibition on the contract page.

The current integration contract is **TREND_SIGNAL v1** (see below).

## Authentication

- Shared secret in `Authorization: Bearer <secret>`, compared in constant time.
- Empty server secret → `503`, not an open receiver.
- Wrong secret → `401`. Nothing is persisted.
- Browser-held secrets are forbidden.

## Idempotency

Inbound events identify on **payload identity**, not arrival time. A duplicate
accepted body returns the same event id and does not create a second draft.

## Sensitive-data boundaries

Each platform declares what it may store. Capture intelligence (solicitation,
agency, prime, score, strategy) is not a Marketing field. Web intake does not
store IP, email, or name. Contract tests must fail the build if a forbidden
key is accepted.

## AI governance

- Off or tightly budgeted in config, not in prompt text.
- A model is never an approver, never a publisher, and never the source of `recommended_action` / disclosure class.
- Every call is ledgered. Exhausted budget degrades to the deterministic path.
- Strategic significance and publication permission are human-only.

## Approval versus execution

Creation, approval, and execution are separate authorities. A draft is not
permission to publish. Retries re-enter the policy engine. `NEVER_PUBLISH`
cannot be overridden.

## Deployment gates

A change may not move LOCAL → SCHEDULED unless:

1. `pytest` is green in the platform repo.
2. Production controls named in `OPERATIONS.md` still match config.
3. Secrets are referenced by name only.
4. No new external-action surface is enabled without a CONTROLLED test.

## Incident and rollback

- Rollback is `git revert` of the platform repo to the last GREEN SHA, plus
  disabling a flag if the flag was the change.
- Do not rewrite history of `runs`, `publication_events`, or intake rows.
- A bad downstream POST is recovered by fail-open plus a later duplicate-safe retry, not by deleting evidence.

---

## TREND_SIGNAL v1

Sanitised CaptureOS → Marketing market observation. Not capture intelligence.

### Schema version

`TREND_SIGNAL v1`. Allowed keys, in this order conceptually:

```
signal_code
observed_period
direction
confidence
```

No other keys. Extra keys are refused, not stripped.

| Field | Type | Allowed values |
| --- | --- | --- |
| `signal_code` | string | Fixed vocabulary: `TREND_SECURITY_AUTOMATION_DEMAND`, `TREND_SOC_MODERNISATION_DEMAND`, `TREND_ZERO_TRUST_DEMAND`, `TREND_AI_GOVERNANCE_DEMAND`, `TREND_CYBER_WORKFORCE_DEMAND`, `TREND_INCIDENT_RESPONSE_DEMAND` |
| `observed_period` | string | `YYYY`, `YYYY-QN`, or `YYYY-MM` |
| `direction` | string | `INCREASING` / `STEADY` / `DECREASING` |
| `confidence` | string | `LOW` / `MODERATE` / `HIGH` |

CaptureOS emits a code only when **at least 3 distinct opportunities** in the
calendar month match configured phrases. That threshold is not part of the
wire schema; it is a sender policy and must not be lowered to force a signal.

### Transport

- Sender: CaptureOS `marketing_bridge` HTTP POST.
- Receiver: `POST /v1/capture/trends`.
- Auth: `Authorization: Bearer` + `RDX_MARKETING_BRIDGE_SECRET`.
- URL: `RDX_MARKETING_BRIDGE_URL` (full path, secrets-only).
- `Content-Type: application/json`.
- Timeout: 10 seconds.
- Retries: 0 (one attempt).
- Body limit: 4 KiB.

### Sender (fail-open)

If the flag is off, URL/secret missing, or Marketing errors, CaptureOS records
`marketing_bridge` notes and **still finishes** the intelligence run.
`failure_is_fatal: false`. Both platforms remain useful with the bridge off.

### Receiver (fail-closed)

| HTTP | Meaning |
| --- | --- |
| 200 | Accepted or idempotent replay; may include `event_id`, `content_id` |
| 400 | Rejected shape / unsanitised / unknown type |
| 401 | Bad or missing bearer |
| 404 | Wrong path |
| 405 | Wrong method |
| 413 | Body too large |
| 503 | Secret or database not configured |
| 5xx | Unhandled receiver error; sender fail-open |

Unauthenticated POST persists nothing.

### Idempotency

`event_id` is `EVT-` + SHA-256 of the sanitised `(signal_code, observed_period)`
pair. Duplicate POST → same event, same draft, no second content row.

### Sensitive-data prohibition

Forbidden (non-exhaustive): `opportunity_id`, `solicitation_number`, `agency`,
`office`, `prime`, `incumbent`, `score`, `recommended_action`, `url`, NAICS/PSC,
award amount, capture strategy. Presence of any extra or forbidden key is
`REJECTED_UNSANITISED`. Marketing must not learn what RDX is bidding.

### Downstream effect

An accepted event becomes **one** `HUMAN_APPROVAL_REQUIRED` draft. It is not
approval, not a publication, and not an AI call.

---

## Reference inventory (v1)

| Platform | Production status | Database | Schedule | Health | AI | External action | Inbound | Outbound |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CaptureOS (`rdx-intelligence`) | GREEN — scheduled | Dedicated Neon (`neondb`) | Weekdays 05:45 America/New_York + dispatch | Web intake `GET /health` (hosting artifact, not live). Job health = Actions + `runs` row | Observation: max 1 call, $0.01, retries 0. Does not change `recommended_action` | No-send. Notion/email draft sinks off. Second hop (web marketing_handoff) off | None required for the daily job | TREND_SIGNAL v1 (fail-open) |
| Marketing (`rdx-marketing`) | GREEN — scheduled, draft-only | Dedicated Neon (`rdx_marketing`) | Weekdays 07:15, weekends 09:00, Monday weekly 06:30 America/New_York + dispatch | `GET /health` on the capture receiver (live HTTPS) | Off (0 calls) | Publisher off; Buffer not required; NullPublisher | TREND_SIGNAL v1 | None live |

## Follow-ups (not blockers)

See each platform `OPERATIONS.md`. Do not reopen frozen production controls
to address a follow-up.
