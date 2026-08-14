# RDX Marketing Engine

External marketing automation for RDX Enterprise across LinkedIn, Facebook, and
Instagram. It maintains the calendar, produces platform-specific drafts, routes
sensitive content for approval, schedules and publishes what is genuinely
cleared, collects performance, and reports weekly — without a daily Claude
session.

Claude becomes an optional creative resource, not the marketing runtime.

```
events + calendar ──> content object ──> platform variants
                            │                    │
                            ▼                    ▼
                     policy engine  ────>  approval queue ──> Buffer ──> LinkedIn
                     (fails closed)        (human or rule)              Facebook
                            │                                          Instagram
                            └──> BLOCKED (never leaves the building)        │
                                                                            ▼
                                                              metrics ──> weekly report
```

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

A dry run against SQLite with publishing disabled:

```bash
.venv/bin/python -m app.daily_run --database-url "sqlite+pysqlite:///./rdx_marketing.db" --json
```

Work the approval queue:

```bash
python scripts/approve.py list
python scripts/approve.py show MKT-2026-00042
python scripts/approve.py approve MKT-2026-00042 --as william.farrell --note "checked"
```

Weekly report:

```bash
python -m app.weekly_report --out weekly-marketing.md
```

## The rules this engine encodes

**Creation and publication are separate authorities.** A content item is a
draft and nothing more. Only `app/policy/engine.py` can say something may leave
the building, and every publish path — including retries — goes through it.

**Missing classification fails closed.** No disclosure class, an unrecognised
class, `INTERNAL_ONLY`, `CONFIDENTIAL`, or `NEVER_PUBLISH` all block. Absence of
a decision is never permission.

**`NEVER_PUBLISH` cannot be overridden.** Not by a scheduler, not by an
approval, not by an operating mode.

**A model is never an approver.** `authorise_publication` accepts only
`human:<name>` or `rule:<rule_id>`. `DraftResponse` has no field for a
classification, an approval, or a schedule, so a model cannot return one — and
the prompt never carries the disclosure class in the first place.

**A generated statement is never its own evidence.** Evidence records live in
their own table with accepted kinds fixed in config; `record_evidence` rejects
generated artefacts and `link_claim` refuses to treat a content item as a source.
Pillars that make company claims cannot publish without evidence linked.

**Capture intelligence is refused, not redacted.** The CaptureOS bridge accepts
one shape — a sanitised trend signal from a fixed vocabulary — and rejects
anything carrying a solicitation number, agency, prime, incumbent, score, or
free text. Stripping fields out of a rich payload and using the remainder is how
strategy leaks; the interesting part survives the redaction.

**Quality outranks schedule completion.** A slot with no qualified content is
skipped and the reason is recorded. The weekly report shows skipped slots, so a
thin week is visible rather than filled with something nobody wanted to publish.

**A provider outage does not lose approved content.** A failed publication stays
in `FAILED` with its error and `retryable=True`, the content stays approved, and
the next run picks it up. `publication_events` is append-only.

**Identical copy everywhere is a defect.** LinkedIn is long and technical,
Facebook conversational, Instagram short and visual-first with hashtags in the
first comment. `build_all` raises if two variants share a body.

**House style.** No em dashes, no marketing filler, no "AI security" (the
practice is Agentic Ops Security), no "pending SDVOSB" — RDX has been certified
since 2026-06-24.

## Operating modes

Set in `config/policy.yaml`:

| Mode | Behaviour |
| --- | --- |
| `MODE_1_APPROVAL_REQUIRED` | Everything waits for a human. |
| `MODE_2_TRUSTED_AUTOPUBLISH` | Auto-eligible pillars with `PUBLIC` classification flow. |
| `MODE_3_MIXED` | **Current.** Routine education flows; anything touching customers, partners, contracts, or company claims stops for a human. |

Under `MODE_3_MIXED` only `cybersecurity_education` and `soar_automation` are
auto-eligible, and only at `PUBLIC` classification. Everything else queues.

## Layout

```
app/
  content/     canonical content object, platform variants, evidence
  policy/      the disclosure and publication engine, restricted-phrase scan
  calendar/    cadence, slot planning, skip-rather-than-settle
  platforms/   LinkedIn / Facebook / Instagram adapters and house style
  publisher/   SocialPublisher contract, Buffer gateway, lifecycle + retry
  approval/    the queue; human or configured rule, never a model
  metrics/     collection and attribution
  media/       asset library with provenance and usage rights
  ai/          drafting adapters, per-task models, cost ledger, budget
  reports/     the weekly performance report
  capture_bridge.py   the CaptureOS boundary
  pipeline.py         the run, end to end
  daily_run.py        python -m app.daily_run
  weekly_report.py    python -m app.weekly_report
config/        policy.yaml, pillars.yaml, cadence.yaml, platforms.yaml, ai.yaml
migrations/    generated PostgreSQL DDL
```

## AI

Off by default. The deterministic templates produce publishable copy on their
own, and a model is only ever asked to improve prose:

```yaml
enabled: false
budget:
  max_calls_per_run: 0
  max_cost_usd_per_run: 0.0
```

Models are configured per task, so cheap work can use a cheap model and only
high-value founder copy needs an expensive one. Every call is ledgered in
`ai_usage` with provider, model, tokens, and cost. When the budget is exhausted
the template stands and the run continues.

## Status

| Increment | State |
| --- | --- |
| 1. Schema, classifications, evidence model, policy engine | done |
| 2. Content calendar and canonical content model | done |
| 3. Buffer integration | done (adapter + retry semantics; **account/channel discovery not implemented** — channel ids are configured by hand) |
| 4. Draft, approval, scheduling, publication lifecycle | done |
| 5. LinkedIn / Facebook / Instagram adapters | done |
| 6. Metrics collection and weekly report | done |
| 7. AI drafting adapters with provider/cost controls | done (interface, ledger, budget; no live provider wired) |
| 8. CaptureOS marketing-event integration | done |

The Buffer GraphQL mutation and metrics query in `app/publisher/buffer.py` are
written against Buffer's documented shape and have **not been exercised against
the live API**. Verify field names with a real token before enabling publishing,
and keep `default_create_as_draft: true` for the first live runs so nothing
posts while that is being confirmed.
