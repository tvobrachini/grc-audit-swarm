import { useState } from "react";
import { ChevronRight, ChevronLeft } from "lucide-react";
import type { SessionDetail, AuditEvent } from "@/api/client";
import { AgentLogPane } from "@/components/inspector/AgentLogPane";
import { EvidenceVaultPane } from "@/components/inspector/EvidenceVaultPane";
import { FilesPane } from "@/components/inspector/FilesPane";
import { FrameworkPane } from "@/components/inspector/FrameworkPane";
import { AuditTrailPane } from "@/components/inspector/AuditTrailPane";

interface Props {
  session: SessionDetail;
  events: AuditEvent[];
}

function panelConfig(
  status: string
): { title: string; tabs: { id: string; label: string }[] } {
  if (status.startsWith("RUNNING_PHASE_1")) {
    return {
      title: "References",
      tabs: [
        { id: "log", label: "Agent Log" },
        { id: "frameworks", label: "Frameworks" },
      ],
    };
  }
  if (status === "WAITING_HUMAN_GATE_1") {
    return {
      title: "References",
      tabs: [
        { id: "frameworks", label: "Frameworks" },
        { id: "files", label: "Files" },
      ],
    };
  }
  if (status.startsWith("RUNNING_PHASE_2")) {
    return {
      title: "Evidence Vault",
      tabs: [
        { id: "vault", label: "Vault" },
        { id: "log", label: "Agent Log" },
      ],
    };
  }
  if (status === "WAITING_HUMAN_GATE_2") {
    return {
      title: "Evidence",
      tabs: [
        { id: "vault", label: "Vault" },
        { id: "files", label: "Files" },
      ],
    };
  }
  if (status.startsWith("RUNNING_PHASE_3")) {
    return {
      title: "Draft",
      tabs: [
        { id: "log", label: "Agent Log" },
        { id: "files", label: "Files" },
      ],
    };
  }
  if (status === "COMPLETED") {
    return {
      title: "Audit Trail",
      tabs: [
        { id: "trail", label: "Approvals" },
        { id: "files", label: "Files" },
      ],
    };
  }
  return {
    title: "Details",
    tabs: [
      { id: "log", label: "Log" },
      { id: "files", label: "Files" },
    ],
  };
}

export function InspectorPanel({ session, events }: Props) {
  const [collapsed, setCollapsed] = useState(false);
  const config = panelConfig(session.status);
  const [activeTab, setActiveTab] = useState(config.tabs[0]?.id ?? "log");

  if (collapsed) {
    return (
      <div className="flex h-full w-8 shrink-0 flex-col items-center border-l border-[var(--color-border)] bg-[var(--color-bg-surface)] py-3">
        <button
          onClick={() => setCollapsed(false)}
          className="text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]"
        >
          <ChevronLeft size={14} />
        </button>
      </div>
    );
  }

  return (
    <div
      className="flex h-full w-[320px] shrink-0 flex-col border-l border-[var(--color-border)] bg-[var(--color-bg-surface)]"
      style={{ width: 320 }}
    >
      <div className="flex items-center border-b border-[var(--color-border)] px-3 py-2">
        <span className="flex-1 text-[11px] font-semibold uppercase tracking-widest text-[var(--color-text-muted)]">
          {config.title}
        </span>
        <button
          onClick={() => setCollapsed(true)}
          className="text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]"
        >
          <ChevronRight size={14} />
        </button>
      </div>

      <div className="flex border-b border-[var(--color-border)]">
        {config.tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-3 py-2 text-[11px] font-medium ${
              activeTab === tab.id
                ? "border-b-2 border-violet-500 text-violet-400"
                : "text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        {activeTab === "log" && <AgentLogPane events={events} />}
        {activeTab === "vault" && <EvidenceVaultPane events={events} />}
        {activeTab === "frameworks" && (
          <FrameworkPane frameworks={session.frameworks} />
        )}
        {activeTab === "files" && <FilesPane session={session} />}
        {activeTab === "trail" && <AuditTrailPane session={session} />}
      </div>
    </div>
  );
}
