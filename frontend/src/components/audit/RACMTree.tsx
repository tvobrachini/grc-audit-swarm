import { useState } from "react";
import { ChevronDown, ChevronRight, ShieldAlert, FlaskConical, ClipboardList } from "lucide-react";
import { clsx } from "clsx";
import type { SessionDetail } from "@/api/client";
import { ApprovalGate } from "./ApprovalGate";

interface ControlTestStep {
  step_description: string;
  expected_result: string;
}

interface ControlTesting {
  test_of_design: ControlTestStep[];
  test_of_effectiveness: ControlTestStep[];
  substantive_testing?: ControlTestStep[];
}

interface Control {
  control_id: string;
  description: string;
  testing_procedures: ControlTesting;
}

interface Risk {
  risk_id: string;
  description: string;
  regulatory_mapping: string[];
  controls: Control[];
}

interface RACMData {
  theme?: string;
  risks?: Risk[];
}

type ProcedureTab = "tod" | "toe" | "substantive";

const TAB_CONFIG: { id: ProcedureTab; label: string; icon: React.ReactNode; key: keyof ControlTesting }[] = [
  { id: "tod", label: "Test of Design", icon: <ClipboardList size={11} />, key: "test_of_design" },
  { id: "toe", label: "Test of Effectiveness", icon: <FlaskConical size={11} />, key: "test_of_effectiveness" },
  { id: "substantive", label: "Substantive", icon: <ShieldAlert size={11} />, key: "substantive_testing" },
];

function ControlCard({ control }: { control: Control }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<ProcedureTab>("tod");

  const activeSteps =
    tab === "tod"
      ? control.testing_procedures.test_of_design
      : tab === "toe"
      ? control.testing_procedures.test_of_effectiveness
      : (control.testing_procedures.substantive_testing ?? []);

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-[var(--color-bg-elevated)]"
      >
        <span className="text-[10px] font-mono text-violet-400 shrink-0">
          {control.control_id}
        </span>
        <span className="flex-1 text-xs text-[var(--color-text-secondary)]">
          {control.description}
        </span>
        {open ? (
          <ChevronDown size={12} className="text-[var(--color-text-muted)] shrink-0" />
        ) : (
          <ChevronRight size={12} className="text-[var(--color-text-muted)] shrink-0" />
        )}
      </button>

      {open && (
        <div className="border-t border-[var(--color-border)]">
          <div className="flex border-b border-[var(--color-border)]">
            {TAB_CONFIG.map((t) => {
              const steps =
                t.id === "tod"
                  ? control.testing_procedures.test_of_design
                  : t.id === "toe"
                  ? control.testing_procedures.test_of_effectiveness
                  : control.testing_procedures.substantive_testing ?? [];
              if (steps.length === 0) return null;
              return (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={clsx(
                    "flex items-center gap-1 px-3 py-1.5 text-[10px] font-medium",
                    tab === t.id
                      ? "border-b-2 border-violet-500 text-violet-400"
                      : "text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]"
                  )}
                >
                  {t.icon}
                  {t.label}
                  <span className="ml-0.5 opacity-60">({steps.length})</span>
                </button>
              );
            })}
          </div>

          <div className="divide-y divide-[var(--color-border)]">
            {activeSteps.map((step, i) => (
              <div key={i} className="px-3 py-2.5">
                <div className="flex gap-2">
                  <span className="mt-0.5 h-4 w-4 shrink-0 rounded-full bg-violet-900/40 text-center text-[9px] font-bold text-violet-400 leading-4">
                    {i + 1}
                  </span>
                  <div className="space-y-1">
                    <p className="text-xs text-[var(--color-text-primary)]">
                      {step.step_description}
                    </p>
                    <p className="text-[11px] text-[var(--color-text-muted)]">
                      <span className="text-green-600">Expected: </span>
                      {step.expected_result}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function RiskRow({ risk }: { risk: Risk }) {
  const [open, setOpen] = useState(true);

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-[var(--color-bg-elevated)]/80"
      >
        <div className="mt-0.5 shrink-0">
          {open ? (
            <ChevronDown size={14} className="text-[var(--color-text-muted)]" />
          ) : (
            <ChevronRight size={14} className="text-[var(--color-text-muted)]" />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[11px] font-mono font-semibold text-amber-400">
              {risk.risk_id}
            </span>
            {risk.regulatory_mapping.map((fw) => (
              <span
                key={fw}
                className="rounded bg-violet-900/30 px-1.5 py-0.5 text-[9px] text-violet-400"
              >
                {fw}
              </span>
            ))}
          </div>
          <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
            {risk.description}
          </p>
        </div>
        <span className="ml-2 shrink-0 text-[10px] text-[var(--color-text-muted)]">
          {risk.controls.length} ctrl{risk.controls.length !== 1 ? "s" : ""}
        </span>
      </button>

      {open && (
        <div className="border-t border-[var(--color-border)] px-4 py-3 space-y-2">
          {risk.controls.map((ctrl) => (
            <ControlCard key={ctrl.control_id} control={ctrl} />
          ))}
        </div>
      )}
    </div>
  );
}

interface Props {
  session: SessionDetail;
}

export function RACMTree({ session }: Props) {
  const racm = session.racm_plan as RACMData | null;
  const risks = racm?.risks ?? [];

  const totalControls = risks.reduce((acc, r) => acc + r.controls.length, 0);

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
            Risk & Control Matrix
            {racm?.theme && (
              <span className="ml-2 font-normal text-[var(--color-text-muted)]">
                — {racm.theme}
              </span>
            )}
          </h3>
          <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
            {session.current_human_dossier}
          </p>
        </div>
        {risks.length > 0 && (
          <div className="flex gap-3 shrink-0 text-center">
            <div>
              <p className="text-lg font-bold text-amber-400">{risks.length}</p>
              <p className="text-[10px] text-[var(--color-text-muted)]">risks</p>
            </div>
            <div>
              <p className="text-lg font-bold text-violet-400">{totalControls}</p>
              <p className="text-[10px] text-[var(--color-text-muted)]">controls</p>
            </div>
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto space-y-3">
        {risks.length === 0 ? (
          <div className="flex h-40 items-center justify-center rounded-lg border border-[var(--color-border)] text-sm text-[var(--color-text-muted)]">
            RACM not yet available
          </div>
        ) : (
          risks.map((risk) => <RiskRow key={risk.risk_id} risk={risk} />)
        )}
      </div>

      <ApprovalGate session={session} />
    </div>
  );
}
