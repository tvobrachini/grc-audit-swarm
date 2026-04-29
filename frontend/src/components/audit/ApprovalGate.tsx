import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle, Loader2 } from "lucide-react";
import { api } from "@/api/client";
import type { SessionDetail } from "@/api/client";

interface Props {
  session: SessionDetail;
}

const GATE_INFO: Record<string, { gate: number; title: string; desc: string }> = {
  WAITING_HUMAN_GATE_1: {
    gate: 1,
    title: "Gate 1 — Planning Review",
    desc: "Review and approve the Risk and Control Matrix (RACM) before fieldwork begins.",
  },
  WAITING_HUMAN_GATE_2: {
    gate: 2,
    title: "Gate 2 — Fieldwork Review",
    desc: "Review working papers and findings before the final report is drafted.",
  },
  WAITING_HUMAN_GATE_3: {
    gate: 3,
    title: "Gate 3 — Report Review",
    desc: "Final approval of the audit report before issuance.",
  },
};

export function ApprovalGate({ session }: Props) {
  const qc = useQueryClient();
  const [humanId, setHumanId] = useState("");
  const gateInfo = GATE_INFO[session.status];

  const mutation = useMutation({
    mutationFn: () =>
      api.sessions.approve(session.session_id, gateInfo.gate, humanId),
    onSuccess: () => {
      // Immediately update cache so polling can't flip back to gate state
      qc.setQueryData(["session", session.session_id], (old: SessionDetail | undefined) =>
        old ? { ...old, status: `RUNNING_PHASE_${gateInfo.gate + 1}`, needs_input: false } : old
      );
      qc.invalidateQueries({ queryKey: ["sessions"] });
      qc.invalidateQueries({ queryKey: ["session", session.session_id] });
    },
  });

  // Don't render gate if approval was already sent this session
  if (!gateInfo || mutation.isSuccess) return null;

  if (mutation.isPending) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-amber-700/40 bg-amber-900/10 px-5 py-4">
        <Loader2 size={14} className="animate-spin text-amber-400" />
        <span className="text-sm text-amber-300">Submitting approval...</span>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-amber-700/40 bg-amber-900/10 p-5">
      <div className="mb-3 flex items-center gap-2">
        <CheckCircle size={16} className="text-amber-400" />
        <h3 className="text-sm font-semibold text-amber-300">{gateInfo.title}</h3>
      </div>

      <p className="mb-4 text-xs text-[var(--color-text-secondary)]">
        {session.current_human_dossier || gateInfo.desc}
      </p>

      <div className="flex gap-2">
        <input
          type="text"
          value={humanId}
          onChange={(e) => setHumanId(e.target.value)}
          placeholder="Your name / ID"
          className="flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] outline-none focus:border-amber-500"
        />
        <button
          onClick={() => mutation.mutate()}
          disabled={!humanId.trim()}
          className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-500 disabled:opacity-50"
        >
          Approve & Proceed
        </button>
      </div>

      {mutation.isError && (
        <p className="mt-2 text-xs text-red-400">
          {(mutation.error as Error).message}
        </p>
      )}
    </div>
  );
}
