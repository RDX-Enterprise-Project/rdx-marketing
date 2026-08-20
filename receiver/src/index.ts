/**
 * Persistent authenticated Marketing TREND_SIGNAL receiver.
 * GET /health (unauthenticated). POST /v1/capture/trends (Bearer).
 * Creates one HUMAN_APPROVAL_REQUIRED draft. Does not publish.
 */

import { Client } from "@neondatabase/serverless";

const PATH = "/v1/capture/trends";
const HEALTH_PATH = "/health";
const SERVICE_NAME = "rdx-marketing-capture";
const MAX_BODY_BYTES = 4 * 1024;
const EVENT_TREND_SIGNAL = "TREND_SIGNAL";
const INTAKE_ACCEPTED = "ACCEPTED";
const INTAKE_CONVERTED = "CONVERTED";
const INTAKE_REJECTED_UNSANITISED = "REJECTED_UNSANITISED";
const HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED";

const ALLOWED_TREND_CODES = new Set([
  "TREND_SECURITY_AUTOMATION_DEMAND",
  "TREND_SOC_MODERNISATION_DEMAND",
  "TREND_ZERO_TRUST_DEMAND",
  "TREND_AI_GOVERNANCE_DEMAND",
  "TREND_CYBER_WORKFORCE_DEMAND",
  "TREND_INCIDENT_RESPONSE_DEMAND",
]);
const FORBIDDEN_KEYS = new Set([
  "solicitation_number",
  "solicitationnumber",
  "notice_id",
  "noticeid",
  "opportunity_id",
  "opportunityid",
  "agency",
  "office",
  "contracting_officer",
  "prime",
  "prime_name",
  "teaming_partner",
  "incumbent",
  "award_amount",
  "recommended_action",
  "score",
  "total_score",
  "capture_strategy",
  "price_to_win",
  "response_deadline",
  "set_aside",
  "naics",
  "psc",
  "customer",
  "customer_name",
  "url",
  "source_url",
]);
const ALLOWED_KEYS = new Set(["signal_code", "observed_period", "direction", "confidence"]);
const ALLOWED_DIRECTIONS = new Set(["INCREASING", "STEADY", "DECREASING"]);
const ALLOWED_CONFIDENCE = new Set(["LOW", "MODERATE", "HIGH"]);
const PERIOD_PATTERN = /^\d{4}(-(Q[1-4]|0[1-9]|1[0-2]))?$/;

const TOPIC_BY_CODE: Record<
  string,
  { pillar: string; topic: string; core: string; disclosure: string }
> = {
  TREND_SECURITY_AUTOMATION_DEMAND: {
    pillar: "soar_automation",
    topic: "Security automation demand",
    disclosure: "PUBLIC",
    core:
      "Security operations modernisation is becoming less about adding another " +
      "security product and more about connecting the tools an organisation " +
      "already owns, with a human still accountable for the outcome.",
  },
  TREND_SOC_MODERNISATION_DEMAND: {
    pillar: "cybersecurity_education",
    topic: "SOC modernisation",
    disclosure: "PUBLIC",
    core:
      "Modernising a security operations centre is mostly an integration and " +
      "process problem, not a procurement problem.",
  },
  TREND_ZERO_TRUST_DEMAND: {
    pillar: "cybersecurity_education",
    topic: "Zero trust in practice",
    disclosure: "PUBLIC",
    core:
      "Zero trust succeeds or fails on identity and instrumentation, long before " +
      "any product decision gets made.",
  },
  TREND_AI_GOVERNANCE_DEMAND: {
    pillar: "cybersecurity_education",
    topic: "Governing agent-assisted operations",
    disclosure: "PUBLIC",
    core:
      "Automating a response step does not remove accountability for it. " +
      "Governance is what makes an automated action defensible afterwards.",
  },
  TREND_CYBER_WORKFORCE_DEMAND: {
    pillar: "academy_workforce",
    topic: "Cyber workforce development",
    disclosure: "PUBLIC_AFTER_APPROVAL",
    core:
      "The shortage in security operations is rarely raw headcount. It is people " +
      "who can build and maintain the automation the tools assume you already have.",
  },
  TREND_INCIDENT_RESPONSE_DEMAND: {
    pillar: "cybersecurity_education",
    topic: "Incident response readiness",
    disclosure: "PUBLIC",
    core:
      "Response speed comes from rehearsed handoffs and working integrations, " +
      "not from adding another alert source.",
  },
};

type Json = Record<string, unknown>;
type Signal = {
  signal_code: string;
  observed_period: string;
  direction: string;
  confidence: string;
};

function jsonResponse(status: number, payload: Json): Response {
  return Response.json(payload, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

function pythonDump(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number" || typeof value === "boolean") return JSON.stringify(value);
  if (Array.isArray(value)) {
    return "[" + value.map(pythonDump).join(", ") + "]";
  }
  if (typeof value === "object") {
    const record = value as Json;
    const keys = Object.keys(record).sort();
    return (
      "{" +
      keys.map((key) => JSON.stringify(key) + ": " + pythonDump(record[key])).join(", ") +
      "}"
    );
  }
  return JSON.stringify(String(value));
}

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function secretsMatch(provided: string, expected: string): Promise<boolean> {
  if (!expected) return false;
  const encoder = new TextEncoder();
  const [left, right] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(provided)),
    crypto.subtle.digest("SHA-256", encoder.encode(expected)),
  ]);
  return crypto.subtle.timingSafeEqual(left, right);
}

async function eventIdFor(payload: Json): Promise<string> {
  const present = new Set(Object.keys(payload).map((k) => k.toLowerCase()));
  let seed: string;
  if (
    [...present].every((k) => ALLOWED_KEYS.has(k)) &&
    present.has("signal_code") &&
    present.has("observed_period")
  ) {
    seed = pythonDump({
      observed_period: String(payload.observed_period ?? "").trim(),
      signal_code: String(payload.signal_code ?? "").trim().toUpperCase(),
    });
  } else {
    seed = pythonDump(payload);
  }
  const hex = await sha256Hex(seed);
  return "EVT-" + hex.slice(0, 24);
}

function validate(payload: Json): Signal {
  const present = new Set(Object.keys(payload).map((k) => k.toLowerCase()));
  const forbidden = [...present].filter((k) => FORBIDDEN_KEYS.has(k)).sort();
  if (forbidden.length) {
    throw new Error(
      "payload carries capture-intelligence field(s) " +
        forbidden.join(", ") +
        "; marketing accepts only sanitised trend signals",
    );
  }
  const unexpected = [...present].filter((k) => !ALLOWED_KEYS.has(k)).sort();
  if (unexpected.length) {
    throw new Error(
      "payload carries unrecognised field(s) " +
        unexpected.join(", ") +
        "; the trend-signal shape is fixed",
    );
  }
  const code = String(payload.signal_code ?? "").trim().toUpperCase();
  if (!ALLOWED_TREND_CODES.has(code)) {
    throw new Error(JSON.stringify(payload.signal_code) + " is not an allowed trend signal code");
  }
  const direction = String(payload.direction ?? "INCREASING").toUpperCase();
  if (!ALLOWED_DIRECTIONS.has(direction)) {
    throw new Error("direction " + JSON.stringify(payload.direction) + " is not recognised");
  }
  const period = String(payload.observed_period ?? "").trim();
  if (!period) {
    throw new Error("trend signal requires an observed_period");
  }
  if (!PERIOD_PATTERN.test(period)) {
    throw new Error(
      "observed_period " +
        JSON.stringify(period) +
        " carries free text; expected a period code such as 2026, 2026-Q3, or 2026-08",
    );
  }
  const confidence = String(payload.confidence ?? "MODERATE").toUpperCase();
  if (!ALLOWED_CONFIDENCE.has(confidence)) {
    throw new Error("confidence " + JSON.stringify(payload.confidence) + " is not recognised");
  }
  return { signal_code: code, observed_period: period, direction, confidence };
}

function bearerToken(header: string | null): string {
  const raw = header || "";
  if (raw.toLowerCase().startsWith("bearer ")) return raw.slice(7).trim();
  return raw.trim();
}

type EventRow = {
  event_id: string;
  intake_status: string;
  rejection_reason: string | null;
  content_id: string | null;
  payload: Json | null;
};

async function nextContentId(client: Client, year: number): Promise<string> {
  const prefix = "MKT-" + String(year).padStart(4, "0") + "-";
  const result = await client.query<{ content_id: string }>(
    "SELECT content_id FROM content_items WHERE content_id LIKE $1 ORDER BY content_id DESC LIMIT 1",
    [prefix + "%"],
  );
  const highest = result.rows[0]?.content_id;
  const sequence = highest ? parseInt(highest.split("-").pop() || "0", 10) + 1 : 1;
  return prefix + String(sequence).padStart(5, "0");
}

async function convertEvent(
  client: Client,
  eventId: string,
  signal: Signal,
  now: Date,
): Promise<string> {
  const existing = await client.query<{ content_id: string | null }>(
    "SELECT content_id FROM marketing_events WHERE event_id = $1",
    [eventId],
  );
  const already = existing.rows[0]?.content_id;
  if (already) return already;
  const topic = TOPIC_BY_CODE[signal.signal_code];
  const contentId = await nextContentId(client, now.getUTCFullYear());
  const notes = {
    derived_from_signal: signal.signal_code,
    direction: signal.direction,
  };
  await client.query(
    `INSERT INTO content_items (
        content_id, created_at, updated_at, campaign, pillar, topic, core_message,
        target_audience, cta, media_requirement, disclosure_class, classified_by,
        classified_at, approval_requirement, approval_status, lifecycle_status,
        origin, origin_reference, notes
     ) VALUES (
        $1, $2, $2, $3, $4, $5, $6,
        $7, $8, $9, $10, NULL,
        NULL, $11, 'DRAFT', 'DRAFT',
        'event', $12, $13::jsonb
     )`,
    [
      contentId,
      now.toISOString(),
      "RDX Authority Building",
      topic.pillar,
      topic.topic,
      topic.core,
      "security leaders and practitioners",
      "More on how RDX approaches this at rdxenterprise.com",
      "NONE",
      topic.disclosure,
      HUMAN_APPROVAL_REQUIRED,
      signal.signal_code,
      JSON.stringify(notes),
    ],
  );
  await client.query(
    "UPDATE marketing_events SET intake_status = $1, content_id = $2 WHERE event_id = $3",
    [INTAKE_CONVERTED, contentId, eventId],
  );
  return contentId;
}

async function handleTrendPost(
  client: Client,
  payload: Json,
  now: Date,
): Promise<{ status: number; body: Json }> {
  const eventId = await eventIdFor(payload);
  const found = await client.query<EventRow>(
    "SELECT event_id, intake_status, rejection_reason, content_id, payload FROM marketing_events WHERE event_id = $1",
    [eventId],
  );
  const existing = found.rows[0];
  if (existing) {
    if (existing.intake_status === INTAKE_ACCEPTED || existing.intake_status === INTAKE_CONVERTED) {
      let signal: Signal | null = null;
      try {
        signal = validate((existing.payload || {}) as Json);
      } catch {
        signal = null;
      }
      const contentId = signal ? await convertEvent(client, eventId, signal, now) : existing.content_id;
      return {
        status: 200,
        body: { status: existing.intake_status, event_id: eventId, content_id: contentId },
      };
    }
    return {
      status: 400,
      body: {
        status: existing.intake_status,
        event_id: eventId,
        reason: existing.rejection_reason,
      },
    };
  }

  let signal: Signal;
  try {
    signal = validate(payload);
  } catch (err) {
    const reason = err instanceof Error ? err.message : "unsanitised";
    await client.query(
      `INSERT INTO marketing_events (
          event_id, received_at, source_system, event_type, payload, intake_status, rejection_reason
       ) VALUES ($1, $2, 'captureos', $3, $4::jsonb, $5, $6)`,
      [eventId, now.toISOString(), EVENT_TREND_SIGNAL, JSON.stringify(payload), INTAKE_REJECTED_UNSANITISED, reason],
    );
    return {
      status: 400,
      body: { status: INTAKE_REJECTED_UNSANITISED, event_id: eventId, reason },
    };
  }

  await client.query(
    `INSERT INTO marketing_events (
        event_id, received_at, source_system, event_type, payload, intake_status, rejection_reason
     ) VALUES ($1, $2, 'captureos', $3, $4::jsonb, $5, NULL)`,
    [eventId, now.toISOString(), EVENT_TREND_SIGNAL, JSON.stringify(signal), INTAKE_ACCEPTED],
  );
  const contentId = await convertEvent(client, eventId, signal, now);
  return {
    status: 200,
    body: { status: INTAKE_CONVERTED, event_id: eventId, content_id: contentId },
  };
}

async function handleHealth(env: Env): Promise<Response> {
  const url = (env.RDX_MARKETING_DATABASE_URL || "").trim();
  if (!url) {
    return jsonResponse(503, { status: "unavailable", reason: "database_not_configured", service: SERVICE_NAME });
  }
  const client = new Client(url);
  try {
    await client.connect();
    await client.query("SELECT 1");
    return jsonResponse(200, { status: "ok", service: SERVICE_NAME, database: "ok" });
  } catch (err) {
    console.error(JSON.stringify({ message: "health_db_failed", error: err instanceof Error ? err.message : "error" }));
    return jsonResponse(503, { status: "unavailable", reason: "database_unreachable", service: SERVICE_NAME });
  } finally {
    try {
      await client.end();
    } catch {
      /* ignore */
    }
  }
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    try {
      if (path === HEALTH_PATH) {
        if (request.method === "GET" || request.method === "HEAD") {
          const response = await handleHealth(env);
          if (request.method === "HEAD") {
            return new Response(null, { status: response.status, headers: response.headers });
          }
          return response;
        }
        return jsonResponse(405, { status: "method_not_allowed" });
      }

      if (path !== PATH) {
        return jsonResponse(404, { status: "not_found" });
      }
      if (request.method !== "POST") {
        return jsonResponse(405, { status: "method_not_allowed" });
      }

      const secret = (env.RDX_MARKETING_BRIDGE_SECRET || "").trim();
      if (!secret) {
        return jsonResponse(503, { status: "unavailable", reason: "bridge_secret_not_configured" });
      }
      const provided = bearerToken(request.headers.get("Authorization"));
      if (!(await secretsMatch(provided, secret))) {
        return jsonResponse(401, { status: "unauthorized" });
      }

      const declared = Number(request.headers.get("Content-Length") || "0");
      if (declared > MAX_BODY_BYTES) {
        return jsonResponse(413, { status: "rejected", reason: "body_too_large" });
      }
      const body = new Uint8Array(await request.arrayBuffer());
      if (body.byteLength > MAX_BODY_BYTES) {
        return jsonResponse(413, { status: "rejected", reason: "body_too_large" });
      }

      let payload: Json;
      try {
        const parsed: unknown = JSON.parse(new TextDecoder().decode(body));
        if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
          return jsonResponse(400, { status: "rejected", reason: "payload_must_be_object" });
        }
        payload = parsed as Json;
      } catch {
        return jsonResponse(400, { status: "rejected", reason: "invalid_json" });
      }

      const databaseUrl = (env.RDX_MARKETING_DATABASE_URL || "").trim();
      if (!databaseUrl) {
        return jsonResponse(503, { status: "unavailable", reason: "database_not_configured" });
      }

      const client = new Client(databaseUrl);
      await client.connect();
      try {
        await client.query("BEGIN");
        const result = await handleTrendPost(client, payload, new Date());
        await client.query("COMMIT");
        return jsonResponse(result.status, result.body);
      } catch (err) {
        try {
          await client.query("ROLLBACK");
        } catch {
          /* ignore */
        }
        throw err;
      } finally {
        ctx.waitUntil(client.end());
      }
    } catch (err) {
      console.error(
        JSON.stringify({
          message: "unhandled error",
          error: err instanceof Error ? err.message : "unknown",
          path,
        }),
      );
      return jsonResponse(500, { status: "error" });
    }
  },
} satisfies ExportedHandler<Env>;
