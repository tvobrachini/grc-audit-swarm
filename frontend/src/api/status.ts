/** Helpers over the backend AuditStatus strings (src/swarm/state/machine.py). */

export type PhaseProblem = { kind: "qa_rejected" | "error"; phase: number };

/** QA_REJECTED_PHASE_n / ERROR_PHASE_n → {kind, phase}; otherwise null. */
export function phaseProblem(status: string): PhaseProblem | null {
  const m = /^(QA_REJECTED|ERROR)_PHASE_([123])$/.exec(status);
  if (!m) return null;
  return { kind: m[1] === "QA_REJECTED" ? "qa_rejected" : "error", phase: Number(m[2]) };
}

export const PHASE_LABELS: Record<number, string> = {
  1: "Planning",
  2: "Fieldwork",
  3: "Reporting",
};
