import type { SessionDetail } from "@/api/client";
import { CheckCircle, FileText } from "lucide-react";
import { clsx } from "clsx";
import { ApprovalGate } from "./ApprovalGate";
import { AuditTrailPane } from "@/components/inspector/AuditTrailPane";

interface Props {
  session: SessionDetail;
}

interface DeficiencyEvaluation {
  deficiency_id: string;
  title: string;
  related_findings: string[];
  related_risks?: string[];
  compensating_controls: string;
  likelihood: string;
  magnitude: string;
  classification: string;
  rationale: string;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

const CLASSIFICATION_COLOR: Record<string, string> = {
  "Material Weakness": "text-red-400 bg-red-900/20 border-red-700/40",
  "Significant Deficiency": "text-orange-400 bg-orange-900/20 border-orange-700/40",
  "Control Deficiency": "text-amber-400 bg-amber-900/20 border-amber-700/40",
  High: "text-red-400 bg-red-900/20 border-red-700/40",
  Medium: "text-amber-400 bg-amber-900/20 border-amber-700/40",
  Low: "text-green-400 bg-green-900/20 border-green-700/40",
  "Not a deficiency": "text-green-400 bg-green-900/20 border-green-700/40",
};

function DeficiencyCard({ evaluation }: { evaluation: DeficiencyEvaluation }) {
  const cls =
    CLASSIFICATION_COLOR[evaluation.classification] ??
    "text-[var(--color-text-muted)] bg-[var(--color-bg-elevated)] border-[var(--color-border)]";
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[11px] text-violet-400">
          {evaluation.deficiency_id}
        </span>
        <span className="text-xs font-medium text-[var(--color-text-primary)]">
          {evaluation.title}
        </span>
        <span
          className={clsx(
            "ml-auto rounded border px-2 py-0.5 text-[10px] font-semibold",
            cls
          )}
        >
          {evaluation.classification}
        </span>
      </div>
      <div className="flex flex-wrap gap-3 text-[11px] text-[var(--color-text-secondary)]">
        <span>
          <span className="text-[var(--color-text-muted)]">Likelihood: </span>
          {evaluation.likelihood}
        </span>
        <span>
          <span className="text-[var(--color-text-muted)]">Magnitude: </span>
          {evaluation.magnitude}
        </span>
        {evaluation.related_findings.length > 0 && (
          <span>
            <span className="text-[var(--color-text-muted)]">Related findings: </span>
            {evaluation.related_findings.join(", ")}
          </span>
        )}
      </div>
      <p className="text-[11px] text-[var(--color-text-secondary)]">
        <span className="text-[var(--color-text-muted)]">Compensating controls: </span>
        {evaluation.compensating_controls}
      </p>
      <p className="text-[11px] italic text-[var(--color-text-secondary)]">
        {evaluation.rationale}
      </p>
    </div>
  );
}

export function ReportView({ session }: Props) {
  const report = session.final_report;
  const completed = session.status === "COMPLETED";
  const summary = text(report?.executive_summary);
  const detail = text(report?.detailed_report);
  const deficiencyScale = text(report?.deficiency_scale);
  const evaluations = (report?.deficiency_evaluations ?? []) as DeficiencyEvaluation[];

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
            {evaluations.length > 0 && (
              <section>
                <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
                  Deficiency evaluation (draft for Gate 3)
                </p>
                <p className="mb-2 text-[11px] italic text-[var(--color-text-muted)]">
                  {deficiencyScale ? `Scale: ${deficiencyScale}. ` : ""}
                  This aggregation, likelihood/magnitude and classification are
                  the reporting crew's draft — the auditor's judgement at Gate
                  3 is the actual conclusion, not this text.
                </p>
                <div className="space-y-2">
                  {evaluations.map((e) => (
                    <DeficiencyCard key={e.deficiency_id} evaluation={e} />
                  ))}
                </div>
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
