export const API_URL = "";
// VITE_API_AUTH_TOKEN is a DEV-ONLY convenience for `npm run dev` against a
// local API (it gets baked into the JS bundle at build time, which is fine
// for a throwaway local build but must never be set for a production build).
// In the production/compose build, nginx injects the Authorization header
// server-side (see frontend/nginx.conf.template) so this is left unset and
// requests simply omit the header, letting the proxy add it.
const API_AUTH_TOKEN = import.meta.env.VITE_API_AUTH_TOKEN as string | undefined;

export interface SessionSummary {
  session_id: string;
  name: string;
  status: string;
  phase: number;
  needs_input: boolean;
  created_at: string;
  prepared_by: string;
}

/** One approval-trail entry. `action` is audit_created | gate_approval | retry |
 * qa_override | return_for_rework; extra keys depend on the action (reason,
 * notes, qa_rejection_reason, artifact_digest, unverified_controls, …). */
export interface TrailEntry {
  gate: string;
  human: string;
  timestamp: string;
  action?: string;
  reason?: string;
  notes?: string;
  qa_rejection_reason?: string;
  previous_status?: string;
  previous_reason?: string;
  artifact?: string;
  artifact_digest?: string;
  unverified_controls?: string;
  hash_alg?: string;
  prev_hash?: string;
  entry_hash?: string;
}

/** Result of recomputing the approval trail's hash chain
 * (GET /api/sessions/{id}/trail/verify). `status` is one of ok |
 * legacy_unchained | broken | truncated | unkeyed | key_unavailable |
 * artifact_changed. `ok` is true only for "ok". */
export interface TrailVerification {
  ok: boolean;
  status: string;
  entries: number;
  legacy_entries: number;
  first_broken_index: number | null;
  keyed: boolean;
  anchored: boolean;
  head_hash: string | null;
  changed_since_approval: string[];
  detail: string;
}

/** One reviewer decision type (POST /api/sessions/{id}/decisions). Values
 * depend on the type — see RecordDecisionBody and DECISIONS.md ADR-011. */
export type DecisionType =
  | "sign_off"
  | "challenge"
  | "classify"
  | "scope_limitation"
  | "writeup"
  | "management_response"
  | "engagement_conclusion";

/** A compact copy of the decision a subject's effective record rests on. */
export interface DecisionRef {
  decision_id: string;
  decision_type: string;
  decided_by: string;
  identity_source: string;
  decided_at: string;
  rationale: string;
  values: Record<string, string>;
  supersedes: string | null;
}

export interface FindingConclusions {
  tod_conclusion: string;
  toe_conclusion: string;
  result: string;
  preliminary_deficiency: boolean;
}

export interface FindingView {
  control_id: string;
  key_control: boolean | null;
  draft: FindingConclusions;
  effective: FindingConclusions;
  /** signed_off | challenged | not_reviewed */
  review_status: string;
  review: DecisionRef | null;
  scope_limitation: DecisionRef | null;
  differs_from_draft: boolean;
  review_required_for_gate_2: boolean;
}

export interface Classification {
  classification: string;
  likelihood: string;
  magnitude: string;
}

export interface DeficiencyView {
  deficiency_id: string;
  title: string;
  related_findings: string[];
  draft: Classification;
  effective: Classification;
  /** "reviewer" once a classify decision is active, else "ai_draft". */
  classification_source: string;
  classification_decision: DecisionRef | null;
  differs_from_draft: boolean;
  writeup: DecisionRef | null;
  management_response: DecisionRef | null;
}

export interface ReviewerChangeRate {
  subjects_decided: number;
  subjects_changed: number;
  rate: number | null;
  published: boolean;
}

/** One outstanding decision a gate approval needs (returned in a 409's
 * `missing_decisions`, and pre-approval in `effective.missing_for_gate`). */
export interface MissingDecision {
  gate: number;
  subject_type: string;
  subject_id: string;
  required: string[];
  reason: string;
}

/** The conclusion of record: AI drafts plus active reviewer decisions
 * (GET /api/sessions/{id} -> effective; see DECISIONS.md ADR-011). */
export interface EffectiveView {
  decisions_required: boolean;
  deficiency_scale: string | null;
  findings: FindingView[];
  deficiencies: DeficiencyView[];
  engagement_conclusion: DecisionRef | null;
  missing_for_gate: Record<string, MissingDecision[]>;
  reviewer_change_rate: ReviewerChangeRate;
  stale_decision_ids: string[];
  superseded_decision_ids: string[];
}

/** A stored reviewer decision plus its current state (active | superseded |
 * stale — see DECISIONS.md ADR-011). */
export interface ReviewDecisionRecord {
  decision_id: string;
  phase: number;
  artifact: string;
  draft_digest: string;
  subject_type: string;
  subject_id: string;
  decision_type: string;
  values: Record<string, string>;
  rationale: string;
  decided_by: string;
  identity_source: string;
  decided_at: string;
  supersedes: string | null;
  state: string;
}

export interface ReviewDecisionsResponse {
  decisions: ReviewDecisionRecord[];
  effective: EffectiveView | null;
}

export interface RecordDecisionBody {
  decision_type: DecisionType;
  subject_id: string;
  subject_type?: string;
  values?: Record<string, string>;
  rationale?: string;
  decided_by: string;
  /** decision_id of the active decision this one corrects. */
  supersedes?: string;
}

export interface SessionDetail extends SessionSummary {
  theme: string;
  business_context: string;
  frameworks: string[];
  current_human_dossier: string;
  racm_plan: Record<string, unknown> | null;
  working_papers: Record<string, unknown> | null;
  final_report: Record<string, unknown> | null;
  approval_trail: TrailEntry[];
  qa_rejection_reason: string | null;
  trail_verification: TrailVerification | null;
  review_decisions: ReviewDecisionRecord[];
  effective: EffectiveView | null;
  review_decisions_required: boolean;
}

export interface AppConfig {
  demo_mode: boolean;
  /** True when the server requires per-reviewer tokens (ADR-012). */
  reviewer_tokens?: boolean;
}

export interface AuditEvent {
  type: string;
  status?: string;
  agent?: string;
  task?: string;
  preview?: string;
  raw?: string;
  vault_id?: string;
  source?: string;
  verified?: boolean;
  artifact?: string;
  reason?: string;
}

/** A non-2xx API response. `message` is the server's `detail` when present.
 * `missingDecisions` is set for a gate-approval 409 that carries the
 * structured `missing_decisions` list (see DECISIONS.md ADR-011). */
export class ApiError extends Error {
  readonly status: number;
  readonly missingDecisions?: MissingDecision[];

  constructor(status: number, message: string, missingDecisions?: MissingDecision[]) {
    super(message);
    this.status = status;
    this.missingDecisions = missingDecisions;
  }
}

function authHeaders(): Record<string, string> {
  return API_AUTH_TOKEN ? { Authorization: `Bearer ${API_AUTH_TOKEN}` } : {};
}

// A per-reviewer personal token (a parallel change is adding per-reviewer
// authentication on the backend). Kept only in this tab's sessionStorage —
// never localStorage — and sent as X-Reviewer-Token on decision and
// gate-action requests, and on audit creation, when the reviewer has one. The
// server checks it only when REVIEWER_TOKENS_FILE is configured (ADR-012).
const REVIEWER_TOKEN_KEY = "grc.reviewerToken";

export function getReviewerToken(): string {
  try {
    return sessionStorage.getItem(REVIEWER_TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setReviewerToken(token: string): void {
  try {
    if (token) sessionStorage.setItem(REVIEWER_TOKEN_KEY, token);
    else sessionStorage.removeItem(REVIEWER_TOKEN_KEY);
  } catch {
    // Private window, blocked storage, etc. — the token just isn't remembered.
  }
}

function reviewerHeaders(): Record<string, string> {
  const token = getReviewerToken();
  return token ? { "X-Reviewer-Token": token } : {};
}

function detailText(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  // FastAPI 422 validation errors: [{loc, msg, …}, …]
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : ""))
      .filter(Boolean);
    if (msgs.length) return msgs.join("; ");
  }
  return null;
}

async function raiseForStatus(res: Response): Promise<Response> {
  if (res.ok) return res;
  const text = await res.text();
  let message = text || res.statusText;
  let missingDecisions: MissingDecision[] | undefined;
  try {
    const body = JSON.parse(text) as {
      detail?: unknown;
      missing_decisions?: MissingDecision[];
    };
    message = detailText(body.detail) ?? message;
    if (Array.isArray(body.missing_decisions)) missingDecisions = body.missing_decisions;
  } catch {
    // not JSON — keep the raw text
  }
  throw new ApiError(res.status, message, missingDecisions);
}

async function request<T>(
  path: string,
  init?: RequestInit,
  extraHeaders: Record<string, string> = {}
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...authHeaders(),
    ...extraHeaders,
  };
  const res = await fetch(`${API_URL}${path}`, { headers, ...init });
  await raiseForStatus(res);
  return res.json() as Promise<T>;
}

/** User-facing text for a failed action (409 = state changed, 422 = invalid input). */
export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 409) {
      return `Not possible in the current state — the audit may have moved on. ${err.message}`;
    }
    if (err.status === 422) return `Invalid input: ${err.message}`;
    if (err.status === 413) return `Too large: ${err.message}`;
    return `Error ${err.status}: ${err.message}`;
  }
  return err instanceof Error ? err.message : String(err);
}

function filenameFrom(res: Response, fallback: string): string {
  const header = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(header);
  return match?.[1] ?? fallback;
}

/** Fetch a same-origin /api file and save it (nginx adds the token in production). */
async function download(path: string, fallbackName: string): Promise<void> {
  const res = await raiseForStatus(
    await fetch(`${API_URL}${path}`, { headers: authHeaders() })
  );
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filenameFrom(res, fallbackName);
  a.click();
  // Revoke after the click has been handled (immediate revoke can cancel it).
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export type ExportKind = "racm.xlsx" | "working-papers.xlsx" | "report.md" | "oscal.json";

export interface CreateSessionBody {
  theme: string;
  business_context: string;
  frameworks: string[];
  name?: string;
  prepared_by: string;
}

export const api = {
  config: () => request<AppConfig>("/api/config"),
  sessions: {
    list: () => request<SessionSummary[]>("/api/sessions"),
    get: (id: string) => request<SessionDetail>(`/api/sessions/${id}`),
    create: (body: CreateSessionBody) =>
      request<SessionSummary>(
        "/api/sessions",
        { method: "POST", body: JSON.stringify(body) },
        reviewerHeaders()
      ),
    createWithDocument: async (body: CreateSessionBody, document: File) => {
      const form = new FormData();
      form.append("theme", body.theme);
      form.append("business_context", body.business_context);
      body.frameworks.forEach((f) => form.append("frameworks", f));
      if (body.name) form.append("name", body.name);
      form.append("prepared_by", body.prepared_by);
      form.append("document", document);
      // No Content-Type header: the browser sets the multipart boundary.
      const res = await raiseForStatus(
        await fetch(`${API_URL}/api/sessions/with-document`, {
          method: "POST",
          headers: { ...authHeaders(), ...reviewerHeaders() },
          body: form,
        })
      );
      return (await res.json()) as SessionSummary;
    },
    approve: (id: string, gate_number: number, human_id: string) =>
      request<SessionSummary>(
        `/api/sessions/${id}/approve`,
        { method: "PATCH", body: JSON.stringify({ gate_number, human_id }) },
        reviewerHeaders()
      ),
    retry: (id: string, phase: number, human_id: string) =>
      request<SessionSummary>(
        `/api/sessions/${id}/retry`,
        { method: "POST", body: JSON.stringify({ phase, human_id }) },
        reviewerHeaders()
      ),
    qaOverride: (id: string, phase: number, human_id: string, reason: string) =>
      request<SessionSummary>(
        `/api/sessions/${id}/qa-override`,
        { method: "POST", body: JSON.stringify({ phase, human_id, reason }) },
        reviewerHeaders()
      ),
    returnForRework: (id: string, phase: number, human_id: string, notes: string) =>
      request<SessionSummary>(
        `/api/sessions/${id}/return`,
        { method: "POST", body: JSON.stringify({ phase, human_id, notes }) },
        reviewerHeaders()
      ),
    verifyTrail: (id: string) =>
      request<TrailVerification>(`/api/sessions/${id}/trail/verify`),
    delete: async (id: string) => {
      await raiseForStatus(
        await fetch(`${API_URL}/api/sessions/${id}`, {
          method: "DELETE",
          headers: authHeaders(),
        })
      );
    },
    export: (id: string, kind: ExportKind) =>
      download(`/api/sessions/${id}/export/${kind}`, kind),
    decisions: {
      list: (id: string) =>
        request<ReviewDecisionsResponse>(`/api/sessions/${id}/decisions`),
      record: (id: string, body: RecordDecisionBody) =>
        request<ReviewDecisionRecord>(
          `/api/sessions/${id}/decisions`,
          { method: "POST", body: JSON.stringify(body) },
          reviewerHeaders()
        ),
    },
  },
  evidence: {
    verify: (vault_id: string, exact_quote: string) =>
      request<{ vault_id: string; verified: boolean }>("/api/evidence/verify", {
        method: "POST",
        body: JSON.stringify({ vault_id, exact_quote }),
      }),
  },
};
