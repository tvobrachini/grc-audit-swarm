import type { SessionDetail } from "@/api/client";
import { CheckCircle, FileText } from "lucide-react";
import { ApprovalGate } from "./ApprovalGate";
import { AuditTrailPane } from "@/components/inspector/AuditTrailPane";

interface Props {
  session: SessionDetail;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

export function ReportView({ session }: Props) {
  const report = session.final_report;
  const completed = session.status === "COMPLETED";
  const summary = text(report?.executive_summary);
  const detail = text(report?.detailed_report);

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex items-center gap-2">
        {completed ? (
          <CheckCircle size={16} className="text-green-400" />
        ) : (
          <FileText size={16} className="text-amber-400" />
        )}
        <h3
          className={`text-sm font-semibold ${completed ? "text-green-300" : "text-amber-300"}`}
        >
          {completed
            ? "Audit Complete"
            : session.status === "WAITING_HUMAN_GATE_3"
              ? "Final report — awaiting Gate 3 approval"
              : "Final report draft"}
        </h3>
      </div>

      <div className="flex-1 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)]">
        {report ? (
          <div className="space-y-4 p-4">
            <section>
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
                Executive Summary
              </p>
              <p className="whitespace-pre-wrap break-words text-xs text-[var(--color-text-secondary)]">
                {summary ?? "No summary provided."}
              </p>
            </section>
            {detail && (
              <section>
                <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
                  Detailed Report
                </p>
                <p className="whitespace-pre-wrap break-words text-xs text-[var(--color-text-secondary)]">
                  {detail}
                </p>
              </section>
            )}
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-muted)]">
            Report not available
          </div>
        )}
      </div>

      {session.approval_trail.length > 0 && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-3">
          <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
            Approval Trail
          </p>
          <AuditTrailPane session={session} />
        </div>
      )}

      <ApprovalGate session={session} />
    </div>
  );
}
