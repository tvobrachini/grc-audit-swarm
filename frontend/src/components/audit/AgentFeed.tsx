import { useEffect, useRef } from "react";
import { clsx } from "clsx";
import { type AuditEvent } from "@/api/client";

interface Props {
  events: AuditEvent[];
  phase: number;
}

const PHASE_AGENTS: Record<number, string[]> = {
  1: ["Audit Director", "Regulatory Analyst", "Risk Specialist", "Auditor", "QA Reviewer"],
  2: ["Evidence Collector", "Field Auditor", "QA Field Reviewer"],
  3: ["Lead Writer", "Concluder", "QA Tone Reviewer", "OSCAL Engineer"],
};

export function AgentFeed({ events, phase }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  const agentSteps = events.filter((e) => e.type === "agent_step" || e.type === "agent_log");
  const lastActiveAgent = agentSteps[agentSteps.length - 1]?.agent ?? "";
  const agents = PHASE_AGENTS[phase] ?? [];

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="grid gap-2">
        {agents.map((agent) => {
          const agentEvents = agentSteps.filter((e) => e.agent === agent);
          const isActive = agent === lastActiveAgent;
          const isDone = agentEvents.length > 0 && !isActive;
          const lastOutput = agentEvents[agentEvents.length - 1];

          return (
            <div
              key={agent}
              className={clsx(
                "rounded-lg border px-4 py-3 transition-all",
                isActive &&
                  "border-violet-500/50 bg-violet-900/10",
                isDone &&
                  "border-green-800/40 bg-green-900/5",
                !isActive &&
                  !isDone &&
                  "border-[var(--color-border)] bg-[var(--color-bg-elevated)]"
              )}
            >
              <div className="flex items-center gap-2">
                <span
                  className={clsx(
                    "h-2 w-2 rounded-full",
                    isActive && "animate-pulse bg-violet-400",
                    isDone && "bg-green-500",
                    !isActive && !isDone && "bg-[var(--color-text-muted)]"
                  )}
                />
                <span
                  className={clsx(
                    "text-sm font-medium",
                    isActive && "text-violet-300",
                    isDone && "text-[var(--color-text-secondary)]",
                    !isActive && !isDone && "text-[var(--color-text-muted)]"
                  )}
                >
                  {agent}
                </span>
                {isActive && (
                  <span className="ml-auto text-[10px] text-violet-400">
                    active
                  </span>
                )}
              </div>

              {lastOutput && (
                <p className="mt-1.5 line-clamp-2 text-xs text-[var(--color-text-muted)]">
                  {lastOutput.preview ?? lastOutput.raw ?? ""}
                </p>
              )}
            </div>
          );
        })}
      </div>

      <div className="flex-1 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3 font-mono text-xs text-[var(--color-text-muted)]">
        {agentSteps.length === 0 && (
          <span className="opacity-40">Waiting for agent activity...</span>
        )}
        {agentSteps.map((e, i) => (
          <div key={i} className="mb-1 leading-relaxed">
            <span className="text-violet-500">[{e.agent}]</span>{" "}
            {e.preview ?? e.raw ?? ""}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
