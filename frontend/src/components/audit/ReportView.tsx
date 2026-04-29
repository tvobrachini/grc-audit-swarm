import type { SessionDetail } from "@/api/client";
import { Download, CheckCircle } from "lucide-react";

interface Props {
  session: SessionDetail;
}

export function ReportView({ session }: Props) {
  const report = session.final_report as Record<string, unknown> | null;

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex items-center gap-2">
        <CheckCircle size={16} className="text-green-400" />
        <h3 className="text-sm font-semibold text-green-300">
          Audit Complete
        </h3>
      </div>

      {session.approval_trail.length > 0 && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-3">
          <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
            Approval Trail
          </p>
          {session.approval_trail.map((entry, i) => (
            <div key={i} className="flex items-baseline gap-2 text-xs py-0.5">
              <span className="text-violet-400">{entry.gate}</span>
              <span className="text-[var(--color-text-secondary)]">
                {entry.human}
              </span>
              <span className="ml-auto text-[var(--color-text-muted)]">
                {new Date(entry.timestamp).toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="flex-1 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)]">
        {report ? (
          <pre className="p-4 text-xs text-[var(--color-text-secondary)] whitespace-pre-wrap break-words">
            {typeof report.executive_summary === "string"
              ? report.executive_summary
              : JSON.stringify(report, null, 2)}
          </pre>
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-muted)]">
            Report not available
          </div>
        )}
      </div>

      {report && (
        <button
          onClick={() => {
            const blob = new Blob([JSON.stringify(report, null, 2)], {
              type: "application/json",
            });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `audit-report-${session.session_id.slice(0, 8)}.json`;
            a.click();
            URL.revokeObjectURL(url);
          }}
          className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]"
        >
          <Download size={12} />
          Download Report JSON
        </button>
      )}
    </div>
  );
}
