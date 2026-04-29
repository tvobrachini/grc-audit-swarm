import { useState } from "react";
import { Plus } from "lucide-react";
import { clsx } from "clsx";
import { useAuditList } from "@/hooks/useAuditList";
import { StatusChip } from "@/components/ui/StatusChip";
import { NotificationBadge } from "@/components/ui/NotificationBadge";
import { NewAuditModal } from "@/components/audit/NewAuditModal";

interface Props {
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function AuditSidebar({ selectedId, onSelect }: Props) {
  const { data: sessions = [] } = useAuditList();
  const [showModal, setShowModal] = useState(false);

  const sorted = [...sessions].sort((a, b) => {
    if (a.needs_input && !b.needs_input) return -1;
    if (!a.needs_input && b.needs_input) return 1;
    if (a.status.startsWith("RUNNING") && !b.status.startsWith("RUNNING")) return -1;
    if (!a.status.startsWith("RUNNING") && b.status.startsWith("RUNNING")) return 1;
    return b.created_at.localeCompare(a.created_at);
  });

  return (
    <>
      <aside
        className="flex h-full w-[260px] shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-bg-surface)]"
        style={{ width: 260 }}
      >
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-3">
          <span className="text-[11px] font-semibold uppercase tracking-widest text-[var(--color-text-muted)]">
            Audits
          </span>
          <button
            onClick={() => setShowModal(true)}
            className="flex items-center gap-1 rounded px-2 py-1 text-[11px] text-violet-400 hover:bg-[var(--color-bg-elevated)]"
          >
            <Plus size={12} />
            New
          </button>
        </div>

        <div className="flex-1 overflow-y-auto py-1">
          {sorted.length === 0 && (
            <p className="px-4 py-6 text-center text-xs text-[var(--color-text-muted)]">
              No audits yet. Click + New to start.
            </p>
          )}
          {sorted.map((s) => {
            const isActive = s.session_id === selectedId;
            return (
              <button
                key={s.session_id}
                onClick={() => onSelect(s.session_id)}
                className={clsx(
                  "flex w-full flex-col gap-1 px-4 py-3 text-left transition-colors",
                  isActive
                    ? "border-l-2 border-violet-500 bg-[var(--color-bg-elevated)]"
                    : "border-l-2 border-transparent hover:bg-[var(--color-bg-elevated)]/50"
                )}
              >
                <div className="flex items-center gap-2">
                  <span
                    className={clsx(
                      "flex-1 truncate text-xs font-medium",
                      isActive
                        ? "text-[var(--color-text-primary)]"
                        : "text-[var(--color-text-secondary)]"
                    )}
                  >
                    {s.name}
                  </span>
                  {s.needs_input && <NotificationBadge />}
                </div>
                <StatusChip status={s.status} />
              </button>
            );
          })}
        </div>
      </aside>

      {showModal && (
        <NewAuditModal
          onClose={() => setShowModal(false)}
          onCreated={(id) => onSelect(id)}
        />
      )}
    </>
  );
}
