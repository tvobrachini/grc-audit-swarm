import { useEffect, useRef } from "react";
import type { AuditEvent } from "@/api/client";

interface Props {
  events: AuditEvent[];
}

export function AgentLogPane({ events }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const logEvents = events.filter(
    (e) => e.type === "agent_step" || e.type === "agent_log" || e.type === "status"
  );

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logEvents.length]);

  return (
    <div className="h-full overflow-y-auto font-mono text-[11px] text-[var(--color-text-muted)] leading-relaxed">
      {logEvents.length === 0 && (
        <span className="opacity-40">No log entries yet...</span>
      )}
      {logEvents.map((e, i) => (
        <div key={i} className="mb-0.5">
          {e.type === "status" ? (
            <span className="text-violet-400">→ {e.status}</span>
          ) : (
            <>
              <span className="text-violet-500">[{e.agent}]</span>{" "}
              {e.preview ?? e.raw ?? ""}
            </>
          )}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
