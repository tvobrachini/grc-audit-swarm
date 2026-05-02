export const API_URL = "";
const API_AUTH_TOKEN = import.meta.env.VITE_API_AUTH_TOKEN as string | undefined;

export interface SessionSummary {
  session_id: string;
  name: string;
  status: string;
  phase: number;
  needs_input: boolean;
  created_at: string;
}

export interface SessionDetail extends SessionSummary {
  theme: string;
  business_context: string;
  frameworks: string[];
  current_human_dossier: string;
  racm_plan: Record<string, unknown> | null;
  working_papers: Record<string, unknown> | null;
  final_report: Record<string, unknown> | null;
  approval_trail: Array<{ gate: string; human: string; timestamp: string }>;
  qa_rejection_reason: string | null;
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (API_AUTH_TOKEN) {
    headers.Authorization = `Bearer ${API_AUTH_TOKEN}`;
  }

  const res = await fetch(`${API_URL}${path}`, {
    headers,
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  sessions: {
    list: () => request<SessionSummary[]>("/api/sessions"),
    get: (id: string) => request<SessionDetail>(`/api/sessions/${id}`),
    create: (body: {
      theme: string;
      business_context: string;
      frameworks: string[];
      name?: string;
    }) =>
      request<SessionSummary>("/api/sessions", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    approve: (id: string, gate_number: number, human_id: string) =>
      request<SessionSummary>(`/api/sessions/${id}/approve`, {
        method: "PATCH",
        body: JSON.stringify({ gate_number, human_id }),
      }),
    delete: (id: string) =>
      fetch(`${API_URL}/api/sessions/${id}`, {
        method: "DELETE",
        headers: API_AUTH_TOKEN ? { Authorization: `Bearer ${API_AUTH_TOKEN}` } : undefined,
      }),
  },
  evidence: {
    verify: (vault_id: string, exact_quote: string) =>
      request<{ vault_id: string; verified: boolean }>("/api/evidence/verify", {
        method: "POST",
        body: JSON.stringify({ vault_id, exact_quote }),
      }),
  },
};
