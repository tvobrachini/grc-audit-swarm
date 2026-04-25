import type { SessionDetail, AuditEvent } from "@/api/client";
import { PhaseBar } from "@/components/ui/PhaseBar";
import { AgentFeed } from "@/components/audit/AgentFeed";
import { RACMTree } from "@/components/audit/RACMTree";
import { FindingsBoard } from "@/components/audit/FindingsBoard";
import { ReportView } from "@/components/audit/ReportView";

interface Props {
  session: SessionDetail;
  events: AuditEvent[];
}

export function MiddlePanel({ session, events }: Props) {
  const { status, phase } = session;

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <div className="flex items-center gap-4 border-b border-[var(--color-border)] px-6 py-3">
        <h2 className="flex-1 truncate text-sm font-semibold text-[var(--color-text-primary)]">
          {session.name}
        </h2>
        <PhaseBar phase={phase} status={status} />
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {status.startsWith("RUNNING_PHASE") && (
          <AgentFeed events={events} phase={phase} />
        )}

        {status === "WAITING_HUMAN_GATE_1" && <RACMTree session={session} />}

        {status === "WAITING_HUMAN_GATE_2" && <FindingsBoard session={session} />}

        {(status === "WAITING_HUMAN_GATE_3" || status === "COMPLETED") && (
          <ReportView session={session} />
        )}

        {(status === "ERROR" ||
          status.startsWith("QA_REJECTED")) && (
          <div className="rounded-xl border border-red-700/40 bg-red-900/10 p-5">
            <p className="text-sm font-medium text-red-300">Audit Error</p>
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">
              {session.qa_rejection_reason ?? "An unexpected error occurred."}
            </p>
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
