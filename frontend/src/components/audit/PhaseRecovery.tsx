import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, RotateCcw, ShieldCheck } from "lucide-react";
import { api, describeError, type SessionDetail } from "@/api/client";
import { PHASE_LABELS, type PhaseProblem } from "@/api/status";

interface Props {
  session: SessionDetail;
  problem: PhaseProblem;
}

const INPUT_CLASS =
  "w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] outline-none focus:border-violet-500";

/**
 * Supervisor actions for a phase that ended QA_REJECTED_PHASE_n or
 * ERROR_PHASE_n: retry the phase, or (QA rejection only, and only when a
 * draft exists) accept the draft with a mandatory justification. Both are
 * stamped in the approval trail by the backend.
 */
export function PhaseRecovery({ session, problem }: Props) {
  const qc = useQueryClient();
  const [humanId, setHumanId] = useState("");
  const [reason, setReason] = useState("");
  const [showOverride, setShowOverride] = useState(false);
  const label = PHASE_LABELS[problem.phase];
  const artifactField = (["racm_plan", "working_papers", "final_report"] as const)[
    problem.phase - 1
  ];
  const canOverride = problem.kind === "qa_rejected" && session[artifactField] !== null;

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["sessions"] });
    qc.invalidateQueries({ queryKey: ["session", session.session_id] });
  };

  const retry = useMutation({
    mutationFn: () => api.sessions.retry(session.session_id, problem.phase, humanId.trim()),
    onSuccess: refresh,
  });
  const override = useMutation({
    mutationFn: () =>
      api.sessions.qaOverride(session.session_id, problem.phase, humanId.trim(), reason.trim()),
    onSuccess: refresh,
  });
  const busy = retry.isPending || override.isPending;
  const error = retry.error ?? override.error;

  return (
    <div className="rounded-xl border border-red-700/40 bg-red-900/10 p-5">
      <div className="mb-2 flex items-center gap-2">
        <AlertTriangle size={16} className="text-red-400" />
        <h3 className="text-sm font-semibold text-red-300">
          {problem.kind === "qa_rejected"
            ? `${label} — rejected by the QA reviewer (after one automatic retry)`
            : `${label} — the crew failed`}
        </h3>
      </div>
      <p className="mb-4 whitespace-pre-wrap text-xs text-[var(--color-text-secondary)]">
        {session.qa_rejection_reason ?? "No reason recorded."}
      </p>

      <label className="mb-1 block text-xs font-medium text-[var(--color-text-secondary)]">
        Your name / ID (recorded in the approval trail)
      </label>
      <input
        type="text"
        value={humanId}
        onChange={(e) => setHumanId(e.target.value)}
        placeholder="e.g. jane.doe@company.com"
        className={INPUT_CLASS}
      />

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          onClick={() => retry.mutate()}
          disabled={!humanId.trim() || busy}
          className="flex items-center gap-1.5 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-500 disabled:opacity-50"
        >
          {retry.isPending ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
          Retry {label}
        </button>
        {canOverride && !showOverride && (
          <button
            onClick={() => setShowOverride(true)}
            disabled={busy}
            className="flex items-center gap-1.5 rounded-lg border border-amber-700/60 px-4 py-2 text-sm text-amber-300 hover:bg-amber-900/20 disabled:opacity-50"
          >
            <ShieldCheck size={14} />
            Approve despite QA rejection…
          </button>
        )}
      </div>
      {problem.kind === "qa_rejected" && (
        <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">
          A retry re-runs the phase with this rejection reason as feedback.
        </p>
      )}

      {canOverride && showOverride && (
        <div className="mt-4 rounded-lg border border-amber-700/40 bg-amber-900/10 p-3">
          <label className="mb-1 block text-xs font-medium text-amber-300">
            Justification for accepting the rejected draft (required)
          </label>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            placeholder="Why is proceeding despite the QA rejection appropriate?"
            className={`${INPUT_CLASS} resize-none`}
          />
          <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
            The draft then goes to the normal Gate {problem.phase} review; the override,
            your name and this justification are recorded in the approval trail.
          </p>
          <div className="mt-2 flex gap-2">
            <button
              onClick={() => override.mutate()}
              disabled={!humanId.trim() || !reason.trim() || busy}
              className="flex items-center gap-1.5 rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-500 disabled:opacity-50"
            >
              {override.isPending && <Loader2 size={14} className="animate-spin" />}
              Accept draft with override
            </button>
            <button
              onClick={() => setShowOverride(false)}
              disabled={busy}
              className="rounded-lg px-3 py-2 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {error && (
        <p role="alert" className="mt-3 text-xs text-red-400">
          {describeError(error)}
        </p>
      )}
    </div>
  );
}
