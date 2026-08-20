# Phase 13 backlog — checkpoint

Status: **FOLLOW-UP, NOT BLOCKERS**. Not authorised for implementation.

The current RDX platform build phase is **CLOSED**.

| Item | Value |
| --- | --- |
| RDX Platform Standard v1 | COMPLETE |
| CaptureOS | GREEN / STANDARDIZED |
| RDX Marketing | GREEN / STANDARDIZED |
| TREND_SIGNAL v1 | DOCUMENTED / ACTIVE |
| CaptureOS freeze SHA | `055227a4800364c881b66dbf161b0f52bcfd3b34` |
| Marketing freeze SHA | `321d55dda3b914f5b5965e09598616026e165753` |
| Phase 12 | GREEN — WAITING FOR NATURAL QUALIFYING TREND |
| Phase 13 | backlog only |

Do not implement items on this list because they are written down. William
must separately prioritise the next platform initiative or follow-up.

## Governing principle

Every RDX platform owns its own data, deployment, schedule, secrets, health,
tests, and failure domain. Platforms communicate only through documented,
versioned contracts.

Every future RDX platform must conform to RDX Platform Standard v1 before
production integration with another RDX platform.

- CaptureOS is the reference intelligence/analysis platform.
- RDX Marketing is the reference content/engagement platform.
- TREND_SIGNAL v1 is the first reference inter-platform contract.

## Operating posture

Observe rather than expand. Do not reopen completed stabilization without
evidence of a production defect.

Do not:

- enable Buffer, LinkedIn publishing, or Marketing AI
- enable automatic approval or automatic publication
- raise CaptureOS AI calls, cost, or retries
- enable a second reasoning hop
- lower the trend threshold below 3
- manufacture a production TREND_SIGNAL

Allow normal schedules to continue.

When the first naturally qualifying trend occurs, verify:

1. one sanitised four-field POST
2. one Marketing event
3. one `HUMAN_APPROVAL_REQUIRED` draft
4. no duplicate draft
5. publications = 0
6. Marketing AI calls = 0

August observation at close: `TREND_ZERO_TRUST_DEMAND = 2` (below 3).

## Backlog (FOLLOW-UP)

| ID | Item | Notes |
| --- | --- | --- |
| 13-1 | True ingestion refusal / hashing for rejected Marketing payloads | Design: `docs/rejected-payload-at-rest.md` on Marketing. Do not persist raw rejected bodies. |
| 13-2 | CaptureOS `opportunity_id` FK width alignment | PK is `VARCHAR(160)`; several FKs remain `VARCHAR(96)`. |
| 13-3 | GitHub Actions Node.js deprecation cleanup and remaining CI noise | Legacy Docker CI noise on the remote. |
| 13-4 | Buffer channel environment-name standardization | `BUFFER_CHANNEL_LINKEDIN` vs `_COMPANY` / `_FOUNDER`. Publishing stays off. |
| 13-5 | Common RDX observability / dashboard | Run-envelope alignment is documented; no shared database. |
| 13-6 | Future human-controlled publishing approval workflow | Approval-to-publish discussion only after a stable natural draft. Not auto-post. |

Production blockers: **none**.
