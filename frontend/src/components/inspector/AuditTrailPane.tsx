import { clsx } from "clsx";
import { ShieldCheck, ShieldAlert, ShieldQuestion, ShieldX } from "lucide-react";
import type { SessionDetail, TrailEntry, TrailVerification } from "@/api/client";

interface Props {
  session: SessionDetail;
}

const ACTIONS: Record<string, { verb: string; dot: string }> = {
  audit_created: { verb: "Prepared by", dot: "bg-violet-500" },
  gate_approval: { verb: "Approved by", dot: "bg-green-500" },
  retry: { verb: "Retry requested by", dot: "bg-violet-500" },
  qa_override: { verb: "QA rejection overridden by", dot: "bg-amber-500" },
  return_for_rework: { verb: "Returned for rework by", dot: "bg-red-500" },
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
      <Detail label="Reviewer notes" value={entry.notes} />
      <Detail label="Justification" value={entry.reason} />
      <Detail label="QA rejection overridden" value={entry.qa_rejection_reason} />
      <Detail label="Retried from" value={entry.previous_status} />
      <Detail label="Previous reason" value={entry.previous_reason} />
      <Detail label="Unverified controls accepted" value={entry.unverified_controls} />
      <p className="mt-0.5 text-[10px] text-[var(--color-text-muted)]">
        {new Date(entry.timestamp).toLocaleString()}
      </p>
    </div>
  );
}

const BADGE_CONFIG: Record<
  string,
  { label: string; icon: typeof ShieldCheck; color: string; tooltip: string }
> = {
  ok: {
    label: "Trail intact",
    icon: ShieldCheck,
    color: "text-green-400 border-green-700/40 bg-green-900/10",
    tooltip:
      "Every entry's hash matches its content and the one before it, in order. " +
      "This proves nothing in the recorded trail was edited, reordered or " +
      "removed from the middle — it does not identify who acted, since " +
      "identities here are self-declared, not authenticated.",
  },
  legacy_unchained: {
    label: "Legacy trail (unchained)",
    icon: ShieldQuestion,
    color: "text-amber-400 border-amber-700/40 bg-amber-900/10",
    tooltip:
      "These entries predate hash-chaining and carry no verifiable link " +
      "between them — they are shown as recorded, but cannot be proven " +
      "untampered.",
  },
  broken: {
    label: "Trail broken",
    icon: ShieldX,
    color: "text-red-400 border-red-700/40 bg-red-900/10",
    tooltip:
      "An entry's hash no longer matches its content or the entry before it: " +
      "the recorded trail was edited, reordered, or an entry was removed.",
  },
  truncated: {
    label: "Trail truncated",
    icon: ShieldX,
    color: "text-red-400 border-red-700/40 bg-red-900/10",
    tooltip:
      "The trail has fewer entries than its separately stored anchor recorded: " +
      "entries were removed from the end.",
  },
  unkeyed: {
    label: "Trail unkeyed",
    icon: ShieldAlert,
    color: "text-amber-400 border-amber-700/40 bg-amber-900/10",
    tooltip:
      "A signing key is configured, but part of the chain is not sealed with " +
      "it — those entries may predate the key, or could have been recomputed " +
      "without it. They cannot be proven untampered.",
  },
  key_unavailable: {
    label: "Trail unverifiable (no key)",
    icon: ShieldQuestion,
    color: "text-amber-400 border-amber-700/40 bg-amber-900/10",
    tooltip:
      "Part of the chain is sealed with a signing key that is not configured " +
      "on this server, so it cannot be recomputed here.",
  },
  artifact_changed: {
    label: "Artifact changed after approval",
    icon: ShieldAlert,
    color: "text-red-400 border-red-700/40 bg-red-900/10",
    tooltip:
      "The hash chain is intact, but an approved artifact's content no longer " +
      "matches the digest recorded at the time it was approved.",
  },
};

function VerificationBadge({ verification }: { verification: TrailVerification }) {
  const cfg = BADGE_CONFIG[verification.status] ?? {
    label: verification.status,
    icon: ShieldQuestion,
    color: "text-[var(--color-text-muted)] border-[var(--color-border)] bg-[var(--color-bg-elevated)]",
    tooltip: verification.detail,
  };
  const Icon = cfg.icon;
  return (
    <div
      title={verification.detail || cfg.tooltip}
      className={clsx(
        "mb-2 flex items-start gap-2 rounded-lg border px-3 py-2 text-[11px]",
        cfg.color
      )}
    >
      <Icon size={14} className="mt-0.5 shrink-0" />
      <div>
        <p className="font-semibold">{cfg.label}</p>
        <p className="mt-0.5 text-[10px] opacity-80">{cfg.tooltip}</p>
        {verification.first_broken_index != null && (
          <p className="mt-0.5 text-[10px] opacity-80">
            First problem at entry {verification.first_broken_index}.
          </p>
        )}
        {verification.changed_since_approval.length > 0 && (
          <p className="mt-0.5 text-[10px] opacity-80">
            Affected: {verification.changed_since_approval.join(", ")}
          </p>
        )}
      </div>
    </div>
  );
}

/** Approvals, returns, retries and QA overrides, oldest first, with the
 * hash-chain verification result shown at the top. */
export function AuditTrailPane({ session }: Props) {
  return (
    <div className="space-y-2">
      {session.trail_verification && (
        <VerificationBadge verification={session.trail_verification} />
      )}
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
