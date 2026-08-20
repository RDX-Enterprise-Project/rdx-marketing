# RDX Marketing operations

## Production freeze (do not change in this phase)

| Control | Value |
| --- | --- |
| `publisher.enabled` | false |
| Marketing AI | false; `max_calls_per_run` 0 |
| Buffer | not required; credentials unused |
| LinkedIn / any network publish | off |
| Capture drafts | `HUMAN_APPROVAL_REQUIRED` |
| Automatic approval / auto-publish | off |
| Forced TREND_SIGNAL | forbidden |

Phase 12 is GREEN and waiting for a natural qualifying trend (min 3).

## Lifecycle

`LOCAL → CONTROLLED → GREEN → SCHEDULED`. Daily job is SCHEDULED (draft-only).
Capture receiver is SCHEDULED HTTPS. Publishing is not in this lifecycle.

## Schedule

`.github/workflows/marketing.yml`

- Weekdays `15 7 * * 1-5` America/New_York
- Weekends `0 9 * * 6,0`
- Monday weekly report `30 6 * * 1`
- `workflow_dispatch` with optional `date`
- Push does **not** run the job
- Concurrency: `rdx-marketing`, no cancel-in-progress

## Secrets (names only)

| Name | Required now |
| --- | --- |
| `RDX_MARKETING_DATABASE_URL` | yes (daily job) |
| `RDX_MARKETING_BRIDGE_SECRET` | yes (receiver) |
| Buffer / AI keys | no |

Receiver secrets are Cloudflare Worker secrets of the same names, not GitHub
workflow env, because the daily job does not serve HTTP.

## Health

Live: `GET https://rdx-marketing-capture.william-farrell.workers.dev/health`

Unauthenticated. Returns `{"status":"ok","service":"rdx-marketing-capture","database":"ok"}`.

Authenticated work is only `POST /v1/capture/trends`.

Job health: Actions conclusion + `runs` row (`daily-YYYY-MM-DD-<hex>`).

## Run envelope (what exists)

Persisted on `runs`: `run_id`, `started_at`, `finished_at`, `status`,
slot counts, `published` / `blocked` / `failed`, `ai_calls`, `ai_cost_usd`.

Stdout JSON is a subset. Remaining envelope fields are future alignment.

## Rollback

Revert `rdx-marketing` to the last GREEN SHA. To stop intake, rotate or unset
the Worker bearer secret (empty secret → 503, persist nothing). Do not delete
`marketing_events` or `content_items` to "undo" a draft; withdraw via approval
state if needed.

## Follow-ups (not blockers)

Phase 13 backlog is recorded in `docs/PHASE-13-BACKLOG.md`. Not authorised
for implementation until separately prioritised.
