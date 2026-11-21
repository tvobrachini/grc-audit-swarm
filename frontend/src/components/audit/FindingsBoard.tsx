import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Quote } from "lucide-react";
import { clsx } from "clsx";
import { api, type FindingView, type SessionDetail } from "@/api/client";
import { ApprovalGate } from "./ApprovalGate";
import { FindingDecisions } from "./decisions/FindingDecisions";

interface AuditFinding {
  control_id: string;
  vault_id_reference: string;
  exact_quote_from_evidence: string;
  test_conclusion: string;
  tod_conclusion?: string;
  toe_conclusion?: string;
  toe_basis?: string | null;
  items_tested?: number | null;
  exceptions_noted?: number | null;
  result?: string | null;
  preliminary_deficiency?: boolean | null;
  /** Old sessions, migrated from a single free-text severity field. */
  legacy_severity?: string | null;
}

interface WorkingPapers {
  theme?: string;
  findings?: AuditFinding[];
}

const RESULT_CONFIG: Record<
  string,
  { color: string; bg: string; border: string; dot: string }
> = {
  "No exception": {
    color: "text-green-400",
    bg: "bg-green-900/10",
    border: "border-green-800/40",
    dot: "bg-green-500",
  },
  Exception: {
    color: "text-red-400",
    bg: "bg-red-900/10",
    border: "border-red-700/40",
    dot: "bg-red-500",
  },
  "Not tested": {
    color: "text-[var(--color-text-muted)]",
    bg: "bg-[var(--color-bg-elevated)]",
    border: "border-[var(--color-border)]",
    dot: "bg-[var(--color-text-muted)]",
  },
};

function resultConfig(s: string | null | undefined) {
  return (
    RESULT_CONFIG[s ?? ""] ?? {
      color: "text-[var(--color-text-muted)]",
      bg: "bg-[var(--color-bg-elevated)]",
      border: "border-[var(--color-border)]",
      dot: "bg-[var(--color-text-muted)]",
    }
  );
}

/** Deterministic vault check: the quote must appear verbatim in the stored,
 * digest-verified evidence record (POST /api/evidence/verify). Skipped for
 * "Not tested" findings, which legitimately carry no vault reference. */
function VaultBadge({ finding, sessionId }: { finding: AuditFinding; sessionId: string }) {
  const { vault_id_reference: vaultId, exact_quote_from_evidence: quote } = finding;
  const { data, isError } = useQuery({
    queryKey: ["verify", sessionId, vaultId, quote],
    queryFn: () => api.evidence.verify(vaultId, quote, sessionId),
    enabled: !!vaultId && !!quote,
    staleTime: Infinity,
  });
  if (!vaultId || !quote) return null;
  if (isError) {
    return (
      <span className="text-[10px] text-[var(--color-text-muted)]">
        verification unavailable
      </span>
    );
  }
  if (!data) return null;
  return data.verified ? (
    <span className="rounded bg-green-900/30 px-1.5 py-0.5 text-[10px] text-green-400">
      Quote verified in vault
    </span>
  ) : (
    <span className="rounded bg-red-900/30 px-1.5 py-0.5 text-[10px] text-red-400">
      Quote not verified
    </span>
  );
}

function Field({ label, value }: { label: string; value?: string | number | null }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div>
      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
        {label}
      </p>
      <p className="text-xs text-[var(--color-text-secondary)] leading-relaxed">{value}</p>
    </div>
  );
}

function FindingCard({
  finding,
  effectiveView,
  session,
}: {
  finding: AuditFinding;
  effectiveView?: FindingView;
  session: SessionDetail;
}) {
  const result = effectiveView?.effective.result ?? finding.result ?? "Not tested";
  const draftResult = effectiveView?.draft.result ?? finding.result ?? "Not tested";
  const todConclusion = effectiveView?.effective.tod_conclusion ?? finding.tod_conclusion;
  const toeConclusion = effectiveView?.effective.toe_conclusion ?? finding.toe_conclusion;
  const preliminaryDeficiency = effectiveView
    ? effectiveView.effective.preliminary_deficiency
    : !!finding.preliminary_deficiency;
  const differs = effectiveView?.differs_from_draft ?? false;
  const notTested = result === "Not tested";
  const [open, setOpen] = useState(result !== "No exception");
  const cfg = resultConfig(result);

  return (
    <div
      id={`decision-finding-${finding.control_id}`}
      className={clsx("rounded-xl border overflow-hidden", cfg.border, cfg.bg)}
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left"
      >
        <span className={clsx("h-2 w-2 rounded-full shrink-0", cfg.dot)} />
        <span className="font-mono text-[11px] text-violet-400 shrink-0">
          {finding.control_id}
        </span>
        <span className="flex-1" />
        {preliminaryDeficiency && (
          <span className="shrink-0 rounded bg-red-900/30 px-2 py-0.5 text-[10px] font-semibold text-red-400">
            preliminary deficiency
          </span>
        )}
        {finding.legacy_severity && (
          <span
            title="This finding predates the ToD/ToE split; the original free-text label is kept for traceability."
            className="shrink-0 rounded bg-[var(--color-bg-base)] px-2 py-0.5 text-[10px] text-[var(--color-text-muted)]"
          >
            legacy rating: {finding.legacy_severity}
          </span>
        )}
        {differs && draftResult !== result && (
          <span className="shrink-0 rounded bg-[var(--color-bg-base)] px-2 py-0.5 text-[10px] italic text-[var(--color-text-muted)]">
            AI draft: {draftResult}
          </span>
        )}
        <span
          className={clsx(
            "shrink-0 rounded px-2 py-0.5 text-[10px] font-semibold",
            cfg.color,
            "bg-black/20"
          )}
        >
          {result}
        </span>
        {open ? (
          <ChevronDown size={12} className="text-[var(--color-text-muted)] shrink-0" />
        ) : (
          <ChevronRight size={12} className="text-[var(--color-text-muted)] shrink-0" />
        )}
      </button>

      {open && (
        <div className="border-t border-[var(--color-border)]/50 px-4 py-3 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Field
              label="Test of Design (ToD)"
              value={
                differs &&
                effectiveView &&
                effectiveView.draft.tod_conclusion !== todConclusion
                  ? `${todConclusion} (AI draft: ${effectiveView.draft.tod_conclusion})`
                  : todConclusion
              }
            />
            <Field
              label="Test of Operating Effectiveness (ToE)"
              value={
                differs &&
                effectiveView &&
                effectiveView.draft.toe_conclusion !== toeConclusion
                  ? `${toeConclusion} (AI draft: ${effectiveView.draft.toe_conclusion})`
                  : toeConclusion
              }
            />
          </div>

          {finding.toe_basis && (
            <div>
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
                ToE Basis (reliance statement)
              </p>
              <p className="text-xs italic text-[var(--color-text-secondary)] leading-relaxed">
                {finding.toe_basis}
              </p>
            </div>
          )}

          <div className="flex flex-wrap gap-4">
            <Field label="Items Tested" value={finding.items_tested} />
            <Field label="Exceptions Noted" value={finding.exceptions_noted} />
          </div>

          <Field label="Test Conclusion" value={finding.test_conclusion} />

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

          <FindingDecisions session={session} controlId={finding.control_id} />

          {finding.vault_id_reference ? (
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-[var(--color-text-muted)]">Vault ID:</span>
              <span className="font-mono text-[10px] text-violet-400/70">
                {finding.vault_id_reference}
              </span>
              <VaultBadge finding={finding} sessionId={session.session_id} />
            </div>
          ) : (
            notTested && (
              <p className="text-[10px] text-[var(--color-text-muted)]">
                No vault reference — control was not tested.
              </p>
            )
          )}
        </div>
      )}
    </div>
  );
}

interface Props {
  session: SessionDetail;
}

const RESULT_ORDER = ["Exception", "Not tested", "No exception"];

export function FindingsBoard({ session }: Props) {
  const papers = session.working_papers as WorkingPapers | null;
  const findings: AuditFinding[] = papers?.findings ?? [];
  const effectiveByControl = new Map(
    (session.effective?.findings ?? []).map((fv) => [fv.control_id, fv])
  );

  const effectiveResult = (f: AuditFinding) =>
    effectiveByControl.get(f.control_id)?.effective.result ?? f.result ?? "Not tested";
  const effectiveToe = (f: AuditFinding) =>
    effectiveByControl.get(f.control_id)?.effective.toe_conclusion ?? f.toe_conclusion;
  const effectiveDeficiency = (f: AuditFinding) => {
    const ev = effectiveByControl.get(f.control_id);
    return ev ? ev.effective.preliminary_deficiency : !!f.preliminary_deficiency;
  };

  const counts = findings.reduce(
    (acc, f) => {
      const key = effectiveResult(f);
      acc[key] = (acc[key] ?? 0) + 1;
      return acc;
    },
    {} as Record<string, number>
  );
  const deficiencyCount = findings.filter(effectiveDeficiency).length;
  // "No exception" says nothing about operating effectiveness, so the ToE gap
  // is shown on its own and can't hide behind a clean result.
  const toeNotTested = findings.filter((f) => effectiveToe(f) === "Not tested").length;

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
                {counts["No exception"] ?? 0}
              </p>
              <p className="text-[10px] text-[var(--color-text-muted)]">no exception</p>
            </div>
            <div title="Controls whose operating effectiveness was not tested: the evidence covers design and implementation only, even where the result is No exception.">
              <p
                className={clsx(
                  "text-lg font-bold",
                  toeNotTested ? "text-amber-400" : "text-[var(--color-text-muted)]"
                )}
              >
                {toeNotTested}
              </p>
              <p className="text-[10px] text-[var(--color-text-muted)]">ToE not tested</p>
            </div>
            <div>
              <p
                className={clsx(
                  "text-lg font-bold",
                  deficiencyCount ? "text-red-400" : "text-[var(--color-text-muted)]"
                )}
              >
                {deficiencyCount}
              </p>
              <p className="text-[10px] text-[var(--color-text-muted)]">prelim. defic.</p>
            </div>
          </div>
        )}
      </div>

      {findings.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {Object.entries(counts)
            .filter(([, n]) => n > 0)
            .map(([result, n]) => {
              const cfg = resultConfig(result);
              return (
                <span
                  key={result}
                  className={clsx(
                    "rounded-full px-2.5 py-1 text-[10px] font-medium",
                    cfg.color,
                    cfg.bg,
                    "border",
                    cfg.border
                  )}
                >
                  {n} × {result}
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
            .sort(
              (a, b) =>
                RESULT_ORDER.indexOf(effectiveResult(a)) -
                RESULT_ORDER.indexOf(effectiveResult(b))
            )
            .map((f) => (
              <FindingCard
                key={f.control_id}
                finding={f}
                effectiveView={effectiveByControl.get(f.control_id)}
                session={session}
              />
            ))
        )}
      </div>

      <ApprovalGate key={session.status} session={session} />
    </div>
  );
}
