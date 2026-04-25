import { clsx } from "clsx";

const STATUS_LABELS: Record<string, string> = {
  WAITING_FOR_SCOPE: "Idle",
  RUNNING_PHASE_1: "Planning",
  WAITING_HUMAN_GATE_1: "Review P1",
  RUNNING_PHASE_2: "Fieldwork",
  WAITING_HUMAN_GATE_2: "Review P2",
  RUNNING_PHASE_3: "Reporting",
  WAITING_HUMAN_GATE_3: "Review P3",
  COMPLETED: "Complete",
  ERROR: "Error",
  QA_REJECTED_PHASE_1: "QA Fail",
  QA_REJECTED_PHASE_2: "QA Fail",
  QA_REJECTED_PHASE_3: "QA Fail",
};

const STATUS_COLORS: Record<string, string> = {
  WAITING_FOR_SCOPE: "bg-[var(--color-bg-elevated)] text-[var(--color-text-muted)]",
  RUNNING_PHASE_1: "bg-violet-900/40 text-violet-300",
  RUNNING_PHASE_2: "bg-violet-900/40 text-violet-300",
  RUNNING_PHASE_3: "bg-violet-900/40 text-violet-300",
  WAITING_HUMAN_GATE_1: "bg-amber-900/40 text-amber-300",
  WAITING_HUMAN_GATE_2: "bg-amber-900/40 text-amber-300",
  WAITING_HUMAN_GATE_3: "bg-amber-900/40 text-amber-300",
  COMPLETED: "bg-green-900/40 text-green-300",
  ERROR: "bg-red-900/40 text-red-400",
  QA_REJECTED_PHASE_1: "bg-red-900/40 text-red-400",
  QA_REJECTED_PHASE_2: "bg-red-900/40 text-red-400",
  QA_REJECTED_PHASE_3: "bg-red-900/40 text-red-400",
};

interface Props {
  status: string;
  className?: string;
}

export function StatusChip({ status, className }: Props) {
  const label = STATUS_LABELS[status] ?? status;
  const color =
    STATUS_COLORS[status] ??
    "bg-[var(--color-bg-elevated)] text-[var(--color-text-secondary)]";

  return (
    <span
      className={clsx(
        "inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium leading-none",
        color,
        className
      )}
    >
      {label}
    </span>
  );
}
