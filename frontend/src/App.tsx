import { useState } from "react";
import { useAuditDetail } from "@/hooks/useAuditDetail";
import { useAuditStream } from "@/hooks/useAuditStream";
import { AuditSidebar } from "@/components/layout/AuditSidebar";
import { MiddlePanel } from "@/components/layout/MiddlePanel";
import { InspectorPanel } from "@/components/layout/InspectorPanel";

export default function App() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { data: session } = useAuditDetail(selectedId);
  const events = useAuditStream(selectedId);

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--color-bg-base)]">
      <AuditSidebar selectedId={selectedId} onSelect={setSelectedId} />

      {session ? (
        <>
          <MiddlePanel session={session} events={events} />
          <InspectorPanel session={session} events={events} />
        </>
      ) : (
        <div className="flex flex-1 items-center justify-center">
          <div className="text-center">
            <p className="text-2xl font-semibold text-[var(--color-text-muted)] opacity-20">
              GRC Audit Swarm
            </p>
            <p className="mt-2 text-sm text-[var(--color-text-muted)] opacity-20">
              Select an audit or click + New to begin
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
