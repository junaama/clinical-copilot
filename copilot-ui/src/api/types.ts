/**
 * Wire contract types. Mirrors agentforge-docs/CHAT-API-CONTRACT.md exactly.
 *
 * This file is the boundary — every value flowing in from /chat MUST be parsed
 * through these types before any component touches it. Drift between this file
 * and the backend's response schema is a bug; do not "fix" the type by widening
 * it locally.
 */

// ──────────────────────────────────────────────────────────────────────────────
// Citation card kinds — closed set per CHAT-API-CONTRACT.md
// ──────────────────────────────────────────────────────────────────────────────

export const CITATION_CARDS = [
  'vitals',
  'labs',
  'medications',
  'problems',
  'allergies',
  'prescriptions',
  'encounters',
  'documents',
  'guideline',
  'other',
] as const;

export type CitationCard = (typeof CITATION_CARDS)[number];

export interface Citation {
  readonly card: CitationCard;
  readonly label: string;
  readonly fhir_ref: string | null;
}

// ──────────────────────────────────────────────────────────────────────────────
// Block variants
// ──────────────────────────────────────────────────────────────────────────────

export type Trend = 'up' | 'down' | 'flat';

export interface CohortRow {
  readonly id: string;
  readonly name: string;
  readonly age: number;
  readonly room: string;
  readonly score: number;
  readonly trend: Trend;
  readonly reasons: readonly string[];
  readonly self: boolean;
  readonly fhir_ref: string | null;
}

export interface TriageBlock {
  readonly kind: 'triage';
  readonly lead: string;
  readonly cohort: readonly CohortRow[];
  readonly citations: readonly Citation[];
  readonly followups: readonly string[];
}

export interface Delta {
  readonly label: string;
  readonly from: string;
  readonly to: string;
  readonly dir: Trend;
}

export const TIMELINE_KINDS = [
  'Lab',
  'Order',
  'Med admin',
  'Nursing note',
  'Imaging',
  'Vital',
  'Other',
] as const;

export type TimelineKind = (typeof TIMELINE_KINDS)[number];

export interface TimelineEvent {
  readonly t: string;
  readonly kind: TimelineKind;
  readonly text: string;
  readonly fhir_ref: string | null;
}

export interface OvernightBlock {
  readonly kind: 'overnight';
  readonly lead: string;
  readonly deltas: readonly Delta[];
  readonly timeline: readonly TimelineEvent[];
  readonly citations: readonly Citation[];
  readonly followups: readonly string[];
}

export interface PlainBlock {
  readonly kind: 'plain';
  readonly lead: string;
  readonly citations: readonly Citation[];
  readonly followups: readonly string[];
}

export type Block = TriageBlock | OvernightBlock | PlainBlock;

// ──────────────────────────────────────────────────────────────────────────────
// Request / response envelopes
// ──────────────────────────────────────────────────────────────────────────────

export interface ChatRequest {
  readonly conversation_id: string;
  readonly patient_id: string;
  readonly user_id: string;
  readonly message: string;
  readonly smart_access_token: string;
}

// ──────────────────────────────────────────────────────────────────────────────
// Route metadata — issue 039
// The closed set is the wire identifier; the frontend uses it to dispatch on
// header copy and badge styling. ``label`` is the user-facing string the UI
// renders verbatim — backend owns the copy.
// ──────────────────────────────────────────────────────────────────────────────

export const ROUTE_KINDS = [
  'chart',
  'panel',
  'guideline',
  'document',
  'clarify',
  'refusal',
] as const;

export type RouteKind = (typeof ROUTE_KINDS)[number];

export interface ChatRoute {
  readonly kind: RouteKind;
  readonly label: string;
}

// ──────────────────────────────────────────────────────────────────────────────
// Diagnostics — issue 042
// Per-turn graph decision + supervisor action so a developer or grader can
// audit why a turn took the route it did, without leaking internals into
// the clinical answer. Always present on the wire; empty strings mean
// "not set this turn".
// ──────────────────────────────────────────────────────────────────────────────

export interface ChatDiagnostics {
  readonly decision: string;
  readonly supervisor_action: string;
}

export interface ChatState {
  readonly patient_id: string | null;
  readonly workflow_id: string;
  readonly classifier_confidence: number;
  readonly message_count: number;
  readonly route: ChatRoute;
  readonly diagnostics: ChatDiagnostics;
}

export interface ChatResponse {
  readonly conversation_id: string;
  readonly reply: string;
  readonly block: Block;
  readonly state: ChatState;
}

// ──────────────────────────────────────────────────────────────────────────────
// Parsing — narrow `unknown` from fetch into the typed shape, fail loudly
// ──────────────────────────────────────────────────────────────────────────────

/** Throws if the value is not a non-empty string. */
function asString(value: unknown, field: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`Invalid ChatResponse: ${field} must be a non-empty string`);
  }
  return value;
}

function asOptionalString(value: unknown, field: string): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== 'string') {
    throw new Error(`Invalid ChatResponse: ${field} must be a string or null`);
  }
  return value;
}

function asNumber(value: unknown, field: string): number {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    throw new Error(`Invalid ChatResponse: ${field} must be a number`);
  }
  return value;
}

function asTrend(value: unknown, field: string): Trend {
  if (value === 'up' || value === 'down' || value === 'flat') return value;
  throw new Error(`Invalid ChatResponse: ${field} must be up | down | flat`);
}

function asTimelineKind(value: unknown, field: string): TimelineKind {
  if (typeof value === 'string' && (TIMELINE_KINDS as readonly string[]).includes(value)) {
    return value as TimelineKind;
  }
  throw new Error(`Invalid ChatResponse: ${field} not a known timeline kind`);
}

function asCitationCard(value: unknown, field: string): CitationCard {
  if (typeof value === 'string' && (CITATION_CARDS as readonly string[]).includes(value)) {
    return value as CitationCard;
  }
  throw new Error(`Invalid ChatResponse: ${field} not a known citation card`);
}

function asRouteKind(value: unknown, field: string): RouteKind {
  if (typeof value === 'string' && (ROUTE_KINDS as readonly string[]).includes(value)) {
    return value as RouteKind;
  }
  throw new Error(`Invalid ChatResponse: ${field} not a known route kind`);
}

function asArray(value: unknown, field: string): readonly unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`Invalid ChatResponse: ${field} must be an array`);
  }
  return value;
}

function asObject(value: unknown, field: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error(`Invalid ChatResponse: ${field} must be an object`);
  }
  return value as Record<string, unknown>;
}

function parseCitation(raw: unknown, field: string): Citation {
  const obj = asObject(raw, field);
  return {
    card: asCitationCard(obj.card, `${field}.card`),
    label: asString(obj.label, `${field}.label`),
    fhir_ref: asOptionalString(obj.fhir_ref, `${field}.fhir_ref`),
  };
}

function parseCitations(raw: unknown, field: string): readonly Citation[] {
  return asArray(raw, field).map((c, i) => parseCitation(c, `${field}[${i}]`));
}

function parseFollowups(raw: unknown, field: string): readonly string[] {
  return asArray(raw, field).map((s, i) => asString(s, `${field}[${i}]`));
}

function parseCohortRow(raw: unknown, field: string): CohortRow {
  const obj = asObject(raw, field);
  return {
    id: asString(obj.id, `${field}.id`),
    name: asString(obj.name, `${field}.name`),
    age: asNumber(obj.age, `${field}.age`),
    room: asOptionalString(obj.room, `${field}.room`) ?? '',
    score: asNumber(obj.score, `${field}.score`),
    trend: asTrend(obj.trend, `${field}.trend`),
    reasons: asArray(obj.reasons, `${field}.reasons`).map((r, i) =>
      asString(r, `${field}.reasons[${i}]`),
    ),
    self: obj.self === true,
    fhir_ref: asOptionalString(obj.fhir_ref, `${field}.fhir_ref`),
  };
}

function parseDelta(raw: unknown, field: string): Delta {
  const obj = asObject(raw, field);
  return {
    label: asString(obj.label, `${field}.label`),
    from: asString(obj.from, `${field}.from`),
    to: asString(obj.to, `${field}.to`),
    dir: asTrend(obj.dir, `${field}.dir`),
  };
}

function parseTimelineEvent(raw: unknown, field: string): TimelineEvent {
  const obj = asObject(raw, field);
  return {
    t: asString(obj.t, `${field}.t`),
    kind: asTimelineKind(obj.kind, `${field}.kind`),
    text: asString(obj.text, `${field}.text`),
    fhir_ref: asOptionalString(obj.fhir_ref, `${field}.fhir_ref`),
  };
}

function parseBlock(raw: unknown): Block {
  const obj = asObject(raw, 'block');
  const kind = obj.kind;
  if (kind === 'triage') {
    return {
      kind: 'triage',
      lead: asString(obj.lead, 'block.lead'),
      cohort: asArray(obj.cohort, 'block.cohort').map((r, i) =>
        parseCohortRow(r, `block.cohort[${i}]`),
      ),
      citations: parseCitations(obj.citations, 'block.citations'),
      followups: parseFollowups(obj.followups, 'block.followups'),
    };
  }
  if (kind === 'overnight') {
    return {
      kind: 'overnight',
      lead: asString(obj.lead, 'block.lead'),
      deltas: asArray(obj.deltas, 'block.deltas').map((d, i) =>
        parseDelta(d, `block.deltas[${i}]`),
      ),
      timeline: asArray(obj.timeline, 'block.timeline').map((e, i) =>
        parseTimelineEvent(e, `block.timeline[${i}]`),
      ),
      citations: parseCitations(obj.citations, 'block.citations'),
      followups: parseFollowups(obj.followups, 'block.followups'),
    };
  }
  if (kind === 'plain') {
    return {
      kind: 'plain',
      lead: asString(obj.lead, 'block.lead'),
      citations: parseCitations(obj.citations ?? [], 'block.citations'),
      followups: parseFollowups(obj.followups ?? [], 'block.followups'),
    };
  }
  throw new Error(`Invalid ChatResponse: unknown block.kind ${String(kind)}`);
}

function parseRoute(raw: unknown, field: string): ChatRoute {
  const obj = asObject(raw, field);
  return {
    kind: asRouteKind(obj.kind, `${field}.kind`),
    label: asString(obj.label, `${field}.label`),
  };
}

/** Diagnostics fields are allowed to be empty strings — the backend uses
 *  the empty value to mean "not set this turn". */
function asDiagnosticString(value: unknown, field: string): string {
  if (typeof value !== 'string') {
    throw new Error(`Invalid ChatResponse: ${field} must be a string`);
  }
  return value;
}

function parseDiagnostics(raw: unknown, field: string): ChatDiagnostics {
  const obj = asObject(raw, field);
  return {
    decision: asDiagnosticString(obj.decision, `${field}.decision`),
    supervisor_action: asDiagnosticString(
      obj.supervisor_action,
      `${field}.supervisor_action`,
    ),
  };
}

function parseState(raw: unknown): ChatState {
  const obj = asObject(raw, 'state');
  return {
    patient_id: asOptionalString(obj.patient_id, 'state.patient_id'),
    workflow_id: asString(obj.workflow_id, 'state.workflow_id'),
    classifier_confidence: asNumber(
      obj.classifier_confidence,
      'state.classifier_confidence',
    ),
    message_count: asNumber(obj.message_count, 'state.message_count'),
    route: parseRoute(obj.route, 'state.route'),
    diagnostics: parseDiagnostics(obj.diagnostics, 'state.diagnostics'),
  };
}

/** Parse an unknown JSON value as a ChatResponse, throwing on any drift. */
export function parseChatResponse(raw: unknown): ChatResponse {
  const obj = asObject(raw, 'response');
  return {
    conversation_id: asString(obj.conversation_id, 'conversation_id'),
    reply: asString(obj.reply, 'reply'),
    block: parseBlock(obj.block),
    state: parseState(obj.state),
  };
}
