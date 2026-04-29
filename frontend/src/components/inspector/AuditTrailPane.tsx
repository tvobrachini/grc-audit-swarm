import type { SessionDetail } from "@/api/client";

interface Props {
  session: SessionDetail;
}

export function AuditTrailPane({ session }: Props) {
  return (
    <div className="space-y-2">
      {session.approval_trail.length === 0 && (
        <p className="text-xs text-[var(--color-text-muted)] opacity-50">
          No approvals recorded yet
        </p>
      )}
      {session.approval_trail.map((entry, i) => (
        <div
          key={i}
          className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3"
        >
          <div className="flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-green-500" />
            <span className="text-xs font-medium text-[var(--color-text-primary)]">
              {entry.gate}
            </span>
          </div>
          <p className="mt-1 text-[11px] text-[var(--color-text-secondary)]">
            Approved by{" "}
            <span className="text-[var(--color-text-primary)]">{entry.human}</span>
          </p>
          <p className="mt-0.5 text-[10px] text-[var(--color-text-muted)]">
            {new Date(entry.timestamp).toLocaleString()}
          </p>
        </div>
      ))}
    </div>
  );
}
