import { clsx } from "clsx";
import type { SessionDetail, TrailEntry } from "@/api/client";

interface Props {
  session: SessionDetail;
}

const ACTIONS: Record<string, { verb: string; dot: string }> = {
  gate_approval: { verb: "Approved by", dot: "bg-green-500" },
  retry: { verb: "Retry requested by", dot: "bg-violet-500" },
  qa_override: { verb: "QA rejection overridden by", dot: "bg-amber-500" },
};

function Detail({ label, value }: { label: string; value?: string }) {
  if (!value) return null;
  return (
    <p className="mt-1 whitespace-pre-wrap break-words text-[11px] text-[var(--color-text-secondary)]">
      <span className="text-[var(--color-text-muted)]">{label}: </span>
      {value}
    </p>
  );
}

function Entry({ entry }: { entry: TrailEntry }) {
  const action = ACTIONS[entry.action ?? "gate_approval"] ?? {
    verb: "Recorded by",
    dot: "bg-[var(--color-text-muted)]",
  };
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3">
      <div className="flex items-center gap-2">
        <span className={clsx("h-1.5 w-1.5 rounded-full", action.dot)} />
        <span className="text-xs font-medium text-[var(--color-text-primary)]">
          {entry.gate}
        </span>
      </div>
      <p className="mt-1 text-[11px] text-[var(--color-text-secondary)]">
        {action.verb}{" "}
        <span className="text-[var(--color-text-primary)]">{entry.human}</span>
      </p>
      <Detail label="Justification" value={entry.reason} />
      <Detail label="QA rejection overridden" value={entry.qa_rejection_reason} />
      <Detail label="Retried from" value={entry.previous_status} />
      <Detail label="Previous reason" value={entry.previous_reason} />
      <p className="mt-0.5 text-[10px] text-[var(--color-text-muted)]">
        {new Date(entry.timestamp).toLocaleString()}
      </p>
    </div>
  );
}

/** Approvals, retries and QA overrides, oldest first. */
export function AuditTrailPane({ session }: Props) {
  return (
    <div className="space-y-2">
      {session.approval_trail.length === 0 && (
        <p className="text-xs text-[var(--color-text-muted)] opacity-50">
          No approvals recorded yet
        </p>
      )}
      {session.approval_trail.map((entry, i) => (
        <Entry key={i} entry={entry} />
      ))}
    </div>
  );
}
