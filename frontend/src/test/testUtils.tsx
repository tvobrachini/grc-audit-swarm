import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { SessionDetail } from "@/api/client";

/** A fresh QueryClient per test, with retries off so failures resolve fast. */
export function newTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

export function renderWithClient(ui: ReactElement, client = newTestQueryClient()) {
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

/** A minimal, valid SessionDetail; override fields per test. */
export function makeSession(overrides: Partial<SessionDetail> = {}): SessionDetail {
  return {
    session_id: "s1",
    name: "Test audit",
    status: "WAITING_HUMAN_GATE_1",
    phase: 1,
    needs_input: true,
    created_at: "2026-01-01T00:00:00",
    prepared_by: "J. Rivera, IT Auditor",
    theme: "Test theme",
    business_context: "Test context",
    frameworks: ["COSO 2013"],
    current_human_dossier: "",
    racm_plan: null,
    working_papers: null,
    final_report: null,
    approval_trail: [],
    qa_rejection_reason: null,
    trail_verification: null,
    ...overrides,
  };
}
