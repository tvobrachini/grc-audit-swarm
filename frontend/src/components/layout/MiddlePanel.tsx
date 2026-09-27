import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { api, describeError, type SessionDetail, type AuditEvent } from "@/api/client";
import { phaseProblem } from "@/api/status";
import { PhaseBar } from "@/components/ui/PhaseBar";
import { AgentFeed } from "@/components/audit/AgentFeed";
import { RACMTree } from "@/components/audit/RACMTree";
import { FindingsBoard } from "@/components/audit/FindingsBoard";
import { ReportView } from "@/components/audit/ReportView";
import { PhaseRecovery } from "@/components/audit/PhaseRecovery";
import { ExportBar } from "@/components/audit/ExportBar";

interface Props {
  session: SessionDetail;
  events: AuditEvent[];
  onDeleted: () => void;
}

const DRAFT_FIELD = { 1: "racm_plan", 2: "working_papers", 3: "final_report" } as const;

function ArtifactView({ session, phase }: { session: SessionDetail; phase: number }) {
  if (phase === 1) return <RACMTree session={session} />;
  if (phase === 2) return <FindingsBoard session={session} />;
  return <ReportView session={session} />;
}

export function MiddlePanel({ session, events, onDeleted }: Props) {
  const { status, phase } = session;
  const problem = phaseProblem(status);
  const qc = useQueryClient();

  const del = useMutation({
    mutationFn: () => api.sessions.delete(session.session_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sessions"] });
      onDeleted();
    },
  });

  const hasRejectedDraft =
    problem?.kind === "qa_rejected" &&
    session[DRAFT_FIELD[problem.phase as 1 | 2 | 3]] !== null;

  // Mirrors the backend's _has_sign_off (src/api/routers/sessions.py): once
  // any gate has been approved, or the audit is completed, deletion is
  // refused so the approved work and its trail are kept.
  const hasSignOff =
    status === "COMPLETED" ||
    phase >= 2 ||
    session.approval_trail.some((e) => e.action === "gate_approval");
  const deleteDisabled = del.isPending || status.startsWith("RUNNING_PHASE") || hasSignOff;
  const deleteTitle = hasSignOff
    ? "Cannot delete: a gate has been approved (or the audit is complete), so this audit and its approval trail are kept."
    : "Delete this audit";

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <div className="flex items-center gap-4 border-b border-[var(--color-border)] px-6 py-3">
        <div className="flex-1 min-w-0">
          <h2 className="truncate text-sm font-semibold text-[var(--color-text-primary)]">
            {session.name}
          </h2>
          {session.prepared_by && (
            <p className="truncate text-[10px] text-[var(--color-text-muted)]">
              Prepared by {session.prepared_by}
            </p>
          )}
        </div>
        <PhaseBar phase={phase} status={status} />
        <button
          title={deleteTitle}
          onClick={() => {
            if (window.confirm(`Delete "${session.name}"? This cannot be undone.`)) {
              del.mutate();
            }
          }}
          disabled={deleteDisabled}
          className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-bg-elevated)] hover:text-red-400 disabled:opacity-30"
        >
          <Trash2 size={14} />
        </button>
      </div>
      {del.isError && (
        <p role="alert" className="px-6 py-1 text-xs text-red-400">
          {describeError(del.error)}
        </p>
      )}

      <ExportBar session={session} />

      <div className="flex-1 overflow-y-auto p-6">
        {status.startsWith("RUNNING_PHASE") && (
          <AgentFeed events={events} phase={phase} />
        )}

        {status === "WAITING_HUMAN_GATE_1" && <RACMTree session={session} />}

        {status === "WAITING_HUMAN_GATE_2" && <FindingsBoard session={session} />}

        {(status === "WAITING_HUMAN_GATE_3" || status === "COMPLETED") && (
          <ReportView session={session} />
        )}

        {problem && (
          <div className="flex flex-col gap-4">
            <PhaseRecovery session={session} problem={problem} />
            {hasRejectedDraft && (
              <div className="rounded-xl border border-dashed border-red-700/60 p-4">
                <p className="mb-3 text-[11px] font-semibold uppercase tracking-widest text-red-400">
                  Rejected draft — not approved (kept for review)
                </p>
                <ArtifactView session={session} phase={problem.phase} />
              </div>
            )}
          </div>
        )}

        {status === "WAITING_FOR_SCOPE" && (
          <div className="flex h-40 items-center justify-center text-sm text-[var(--color-text-muted)]">
            Waiting for scope input...
          </div>
        )}
      </div>
    </div>
  );
}
