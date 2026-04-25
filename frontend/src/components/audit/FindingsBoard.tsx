import type { SessionDetail } from "@/api/client";
import { ApprovalGate } from "./ApprovalGate";

interface Props {
  session: SessionDetail;
}

function statusColor(s: string) {
  if (s === "Pass") return "text-green-400";
  if (s === "Fail") return "text-red-400";
  return "text-amber-400";
}

export function FindingsBoard({ session }: Props) {
  const papers = session.working_papers as Record<string, unknown> | null;
  const findings = (papers?.findings as unknown[]) ?? [];

  return (
    <div className="flex h-full flex-col gap-4">
      <div>
        <h3 className="mb-1 text-sm font-semibold text-[var(--color-text-primary)]">
          Working Papers — Findings
        </h3>
        <p className="text-xs text-[var(--color-text-muted)]">
          {session.current_human_dossier}
        </p>
      </div>

      <div className="flex-1 overflow-y-auto space-y-2">
        {findings.length === 0 ? (
          <div className="flex h-40 items-center justify-center rounded-lg border border-[var(--color-border)] text-sm text-[var(--color-text-muted)]">
            No findings yet
          </div>
        ) : (
          findings.map((f, i) => {
            const finding = f as Record<string, unknown>;
            return (
              <div
                key={i}
                className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-3"
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono text-violet-400">
                    {finding.control_id as string}
                  </span>
                  <span
                    className={`text-xs font-medium ${statusColor(
                      finding.status as string
                    )}`}
                  >
                    {finding.status as string}
                  </span>
                </div>
                <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
                  {finding.justification as string}
                </p>
                {!!finding.risk_rating && (
                  <span className="mt-1.5 inline-block rounded bg-red-900/30 px-1.5 py-0.5 text-[10px] text-red-400">
                    {String(finding.risk_rating)} risk
                  </span>
                )}
              </div>
            );
          })
        )}
      </div>

      <ApprovalGate session={session} />
    </div>
  );
}
