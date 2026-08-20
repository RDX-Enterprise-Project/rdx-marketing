# Follow-up: rejected payload at rest

Status: design only. Not part of scheduled-draft activation.

## Current behaviour

`app.capture_bridge.intake` persists the inbound JSON on every refusal
(`REJECTED_UNSANITISED`, `REJECTED_UNKNOWN_TYPE`) so the rejection is
auditable. That stores capture-intelligence fields in the Marketing
database after Marketing has already refused them.

## Required behaviour

A rejected sensitive payload must not persist the raw body.

Prefer storing only:

- rejection category (`REJECTED_UNSANITISED`, `REJECTED_UNKNOWN_TYPE`, or a
  more specific code such as `forbidden_keys` / `unexpected_keys` /
  `invalid_period`)
- timestamp (`received_at`)
- payload hash (`sha256` of the raw request body, hex)

Keep `event_id` as today: hash of the sanitised identity when the shape is
already four-key, otherwise hash of the offered object. The at-rest row
must not include the offered object.

## Suggested schema

Add nullable columns rather than overloading `payload`:

- `payload_hash TEXT`
- `rejection_category TEXT`

For rejected rows:

- `payload` is NULL or `{}`
- `payload_hash` is set
- `rejection_reason` stays a category/code, not a dump of forbidden values

Accepted rows continue to store the four-key sanitised object only.

## Out of scope for Phase 12

Scheduled draft generation keeps today's persist-rejected-payload behaviour
so the activation diff stays a receiver + enablement change. Implement this
hardening as its own migration, with tests that a leaky POST leaves
`payload` empty and `publications` still 0.
