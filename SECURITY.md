# RDX Marketing security

Standard: `docs/RDX-PLATFORM-STANDARD-v1.md`.

## Secrets

Never commit or print `RDX_MARKETING_DATABASE_URL` or
`RDX_MARKETING_BRIDGE_SECRET`. The receiver stores them as Wrangler secrets.
`.dev.vars` is gitignored.

Buffer tokens are optional while publisher is off. Do not require them for
intake or the daily job.

## Data the platform may store

Sanitised trend signals (four keys), drafts, evidence, approvals, publication
audit (empty while publisher is off).

## Data the platform may not store as capture intelligence

Solicitation numbers, opportunity ids, agency, prime, incumbent, scores,
strategy. TREND_SIGNAL v1 refuses extra keys rather than redacting them.

Follow-up: rejected rows currently persist the offered JSON for audit. That is
not permission to use it. See `docs/rejected-payload-at-rest.md`.

## Auth surfaces

- Daily job: GitHub-hosted, no public HTTP.
- Capture receiver: Bearer `RDX_MARKETING_BRIDGE_SECRET`. Empty secret → 503.
  Wrong secret → 401, no persist. `GET /health` is the only unauthenticated path.

## AI and publishing

AI drafting is off. A model cannot approve or schedule. Publisher is
`NullPublisher`. `NEVER_PUBLISH` cannot be overridden.
