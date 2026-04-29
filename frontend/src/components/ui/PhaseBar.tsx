import { clsx } from "clsx";

interface Props {
  phase: number;
  status: string;
}

const PHASES = ["Planning", "Fieldwork", "Reporting"];

export function PhaseBar({ phase, status }: Props) {
  return (
    <div className="flex gap-1">
      {PHASES.map((label, i) => {
        const n = i + 1;
        const isActive = phase === n;
        const isDone = phase > n || status === "COMPLETED";
        return (
          <div key={label} className="flex flex-col items-center gap-0.5">
            <div
              className={clsx(
                "h-1 w-14 rounded-full",
                isDone && "bg-green-500",
                isActive && "bg-violet-500",
                !isDone && !isActive && "bg-[var(--color-border)]"
              )}
            />
            <span
              className={clsx(
                "text-[10px]",
                isActive && "text-violet-400",
                isDone && "text-green-500",
                !isDone && !isActive && "text-[var(--color-text-muted)]"
              )}
            >
              {label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
