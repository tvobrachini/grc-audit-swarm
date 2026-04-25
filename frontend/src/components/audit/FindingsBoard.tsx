import { useState } from "react";
import { ChevronDown, ChevronRight, Quote } from "lucide-react";
import { clsx } from "clsx";
import type { SessionDetail } from "@/api/client";
import { ApprovalGate } from "./ApprovalGate";

interface AuditFinding {
  control_id: string;
  vault_id_reference: string;
  exact_quote_from_evidence: string;
  test_conclusion: string;
  severity: string;
}

interface WorkingPapers {
  theme?: string;
  findings?: AuditFinding[];
}

const SEVERITY_CONFIG: Record<
  string,
  { color: string; bg: string; border: string; dot: string }
> = {
  Pass: {
    color: "text-green-400",
    bg: "bg-green-900/10",
    border: "border-green-800/40",
    dot: "bg-green-500",
  },
  "Control Deficiency": {
    color: "text-amber-400",
    bg: "bg-amber-900/10",
    border: "border-amber-700/40",
    dot: "bg-amber-500",
  },
  "Significant Deficiency": {
    color: "text-orange-400",
    bg: "bg-orange-900/10",
    border: "border-orange-700/40",
    dot: "bg-orange-500",
  },
  "Material Weakness": {
    color: "text-red-400",
    bg: "bg-red-900/10",
    border: "border-red-700/40",
    dot: "bg-red-500",
  },
};

function severityConfig(s: string) {
  return (
    SEVERITY_CONFIG[s] ?? {
      color: "text-[var(--color-text-muted)]",
      bg: "bg-[var(--color-bg-elevated)]",
      border: "border-[var(--color-border)]",
      dot: "bg-[var(--color-text-muted)]",
    }
  );
}

function FindingCard({ finding }: { finding: AuditFinding }) {
  const [open, setOpen] = useState(finding.severity !== "Pass");
  const cfg = severityConfig(finding.severity);

  return (
    <div className={clsx("rounded-xl border overflow-hidden", cfg.border, cfg.bg)}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left"
      >
        <span className={clsx("h-2 w-2 rounded-full shrink-0", cfg.dot)} />
        <span className="font-mono text-[11px] text-violet-400 shrink-0">
          {finding.control_id}
        </span>
        <span
          className={clsx(
            "ml-auto shrink-0 rounded px-2 py-0.5 text-[10px] font-semibold",
            cfg.color,
            "bg-black/20"
          )}
        >
          {finding.severity}
        </span>
        {open ? (
          <ChevronDown size={12} className="text-[var(--color-text-muted)] shrink-0" />
        ) : (
          <ChevronRight size={12} className="text-[var(--color-text-muted)] shrink-0" />
        )}
      </button>

      {open && (
        <div className="border-t border-[var(--color-border)]/50 px-4 py-3 space-y-3">
          <div>
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
              Test Conclusion
            </p>
            <p className="text-xs text-[var(--color-text-secondary)] leading-relaxed">
              {finding.test_conclusion}
            </p>
          </div>

          {finding.exact_quote_from_evidence && (
            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-2">
              <div className="mb-1.5 flex items-center gap-1.5">
                <Quote size={10} className="text-violet-400" />
                <span className="text-[10px] font-semibold uppercase tracking-wide text-violet-400">
                  Evidence Quote
                </span>
              </div>
              <p className="text-[11px] italic text-[var(--color-text-muted)] leading-relaxed">
                "{finding.exact_quote_from_evidence}"
              </p>
            </div>
          )}

          {finding.vault_id_reference && (
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-[var(--color-text-muted)]">Vault ID:</span>
              <span className="font-mono text-[10px] text-violet-400/70">
                {finding.vault_id_reference}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

interface Props {
  session: SessionDetail;
}

export function FindingsBoard({ session }: Props) {
  const papers = session.working_papers as WorkingPapers | null;
  const findings: AuditFinding[] = papers?.findings ?? [];

  const counts = findings.reduce(
    (acc, f) => {
      const key = f.severity;
      acc[key] = (acc[key] ?? 0) + 1;
      return acc;
    },
    {} as Record<string, number>
  );

  const hasDeficiencies =
    (counts["Control Deficiency"] ?? 0) +
    (counts["Significant Deficiency"] ?? 0) +
    (counts["Material Weakness"] ?? 0);

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
            Working Papers — Findings
            {papers?.theme && (
              <span className="ml-2 font-normal text-[var(--color-text-muted)]">
                — {papers.theme}
              </span>
            )}
          </h3>
          <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
            {session.current_human_dossier}
          </p>
        </div>

        {findings.length > 0 && (
          <div className="flex gap-3 shrink-0 text-center">
            <div>
              <p className="text-lg font-bold text-green-400">
                {counts["Pass"] ?? 0}
              </p>
              <p className="text-[10px] text-[var(--color-text-muted)]">pass</p>
            </div>
            <div>
              <p
                className={clsx(
                  "text-lg font-bold",
                  hasDeficiencies ? "text-red-400" : "text-[var(--color-text-muted)]"
                )}
              >
                {hasDeficiencies}
              </p>
              <p className="text-[10px] text-[var(--color-text-muted)]">defic.</p>
            </div>
          </div>
        )}
      </div>

      {findings.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {Object.entries(counts)
            .filter(([, n]) => n > 0)
            .map(([sev, n]) => {
              const cfg = severityConfig(sev);
              return (
                <span
                  key={sev}
                  className={clsx(
                    "rounded-full px-2.5 py-1 text-[10px] font-medium",
                    cfg.color,
                    cfg.bg,
                    "border",
                    cfg.border
                  )}
                >
                  {n} × {sev}
                </span>
              );
            })}
        </div>
      )}

      <div className="flex-1 overflow-y-auto space-y-2">
        {findings.length === 0 ? (
          <div className="flex h-40 items-center justify-center rounded-lg border border-[var(--color-border)] text-sm text-[var(--color-text-muted)]">
            No findings yet
          </div>
        ) : (
          findings
            .slice()
            .sort((a, b) => {
              const order = [
                "Material Weakness",
                "Significant Deficiency",
                "Control Deficiency",
                "Pass",
              ];
              return order.indexOf(a.severity) - order.indexOf(b.severity);
            })
            .map((f, i) => <FindingCard key={i} finding={f} />)
        )}
      </div>

      <ApprovalGate session={session} />
    </div>
  );
}
