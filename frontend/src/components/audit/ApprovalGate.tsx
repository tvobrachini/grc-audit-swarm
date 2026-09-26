import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle, Loader2, Undo2 } from "lucide-react";
import { api, describeError } from "@/api/client";
import type { SessionDetail } from "@/api/client";

interface Props {
  session: SessionDetail;
}

const GATE_INFO: Record<
  string,
  { gate: number; title: string; desc: string; sodNote: string }
> = {
  WAITING_HUMAN_GATE_1: {
    gate: 1,
    title: "Gate 1 — Planning Review",
    desc: "Review and approve the Risk and Control Matrix (RACM) before fieldwork begins.",
    sodNote: "The preparer of this audit cannot approve this gate.",
  },
  WAITING_HUMAN_GATE_2: {
    gate: 2,
    title: "Gate 2 — Fieldwork Review",
    desc: "Review working papers and findings before the final report is drafted.",
    sodNote: "The preparer of this audit cannot approve this gate.",
  },
  WAITING_HUMAN_GATE_3: {
    gate: 3,
    title: "Gate 3 — Report Review",
    desc: "Final approval of the audit report before issuance.",
    sodNote:
      "Segregation of duties: Gate 3 must be approved by someone other than " +
      "the Gate 2 approver; the preparer cannot approve.",
  },
};

/** Approve, or return for rework with required reviewer notes. */
export function ApprovalGate({ session }: Props) {
  const qc = useQueryClient();
  const [humanId, setHumanId] = useState("");
  const [showReturn, setShowReturn] = useState(false);
  const [notes, setNotes] = useState("");
  const gateInfo = GATE_INFO[session.status];

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["sessions"] });
    qc.invalidateQueries({ queryKey: ["session", session.session_id] });
  };

  const approveMutation = useMutation({
    mutationFn: () =>
      api.sessions.approve(session.session_id, gateInfo.gate, humanId.trim()),
    onSuccess: () => {
      // Immediately update cache so polling can't flip back to gate state
      // (gate 3 completes the audit; gates 1/2 start the next phase).
      const next =
        gateInfo.gate === 3 ? "COMPLETED" : `RUNNING_PHASE_${gateInfo.gate + 1}`;
      qc.setQueryData(["session", session.session_id], (old: SessionDetail | undefined) =>
        old ? { ...old, status: next, needs_input: false } : old
      );
      refresh();
    },
  });

  const returnMutation = useMutation({
    mutationFn: () =>
      api.sessions.returnForRework(
        session.session_id,
        gateInfo.gate,
        humanId.trim(),
        notes.trim()
      ),
    onSuccess: () => {
      qc.setQueryData(["session", session.session_id], (old: SessionDetail | undefined) =>
        old
          ? { ...old, status: `RUNNING_PHASE_${gateInfo.gate}`, needs_input: false }
          : old
      );
      refresh();
    },
  });

  // Don't render gate if an action was already sent this session
  if (!gateInfo || approveMutation.isSuccess || returnMutation.isSuccess) return null;

  const busy = approveMutation.isPending || returnMutation.isPending;

  if (busy) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-amber-700/40 bg-amber-900/10 px-5 py-4">
        <Loader2 size={14} className="animate-spin text-amber-400" />
        <span className="text-sm text-amber-300">
          {approveMutation.isPending ? "Submitting approval..." : "Returning for rework..."}
        </span>
      </div>
    );
  }

  const error = approveMutation.error ?? returnMutation.error;

  return (
    <div className="rounded-xl border border-amber-700/40 bg-amber-900/10 p-5">
      <div className="mb-3 flex items-center gap-2">
        <CheckCircle size={16} className="text-amber-400" />
        <h3 className="text-sm font-semibold text-amber-300">{gateInfo.title}</h3>
      </div>

      <p className="mb-2 text-xs text-[var(--color-text-secondary)]">
        {session.current_human_dossier || gateInfo.desc}
      </p>
      <p className="mb-4 text-[11px] italic text-amber-200/80">{gateInfo.sodNote}</p>

      <label className="mb-1 block text-[11px] font-medium text-[var(--color-text-secondary)]">
        Approver name / ID — a different person from the preparer (and, at Gate
        3, from the Gate 2 approver)
      </label>
      <div className="flex gap-2">
        <input
          type="text"
          value={humanId}
          onChange={(e) => setHumanId(e.target.value)}
          placeholder="Your name / ID"
          className="flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] outline-none focus:border-amber-500"
        />
        <button
          onClick={() => approveMutation.mutate()}
          disabled={!humanId.trim()}
          className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-500 disabled:opacity-50"
        >
          Approve & Proceed
        </button>
        {!showReturn && (
          <button
            onClick={() => setShowReturn(true)}
            className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] px-4 py-2 text-sm text-[var(--color-text-secondary)] hover:border-red-500 hover:text-red-400"
          >
            <Undo2 size={14} />
            Return for rework
          </button>
        )}
      </div>

      {showReturn && (
        <div className="mt-4 rounded-lg border border-red-700/40 bg-red-900/10 p-3">
          <label className="mb-1 block text-xs font-medium text-red-300">
            Reviewer notes (required) — fed back to the phase as rework feedback
          </label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            placeholder="What must change before this can be approved?"
            className="w-full resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] outline-none focus:border-red-500"
          />
          <div className="mt-2 flex gap-2">
            <button
              onClick={() => returnMutation.mutate()}
              disabled={!humanId.trim() || !notes.trim()}
              className="rounded-lg bg-red-700 px-4 py-2 text-sm font-medium text-white hover:bg-red-600 disabled:opacity-50"
            >
              Return for rework
            </button>
            <button
              onClick={() => setShowReturn(false)}
              className="rounded-lg px-3 py-2 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {error && (
        <p role="alert" className="mt-2 text-xs text-red-400">
          {describeError(error)}
        </p>
      )}
    </div>
  );
}
