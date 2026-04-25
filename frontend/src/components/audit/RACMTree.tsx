import type { SessionDetail } from "@/api/client";
import { ApprovalGate } from "./ApprovalGate";

interface Props {
  session: SessionDetail;
}

export function RACMTree({ session }: Props) {
  const racm = session.racm_plan;

  return (
    <div className="flex h-full flex-col gap-4">
      <div>
        <h3 className="mb-1 text-sm font-semibold text-[var(--color-text-primary)]">
          Risk & Control Matrix
        </h3>
        <p className="text-xs text-[var(--color-text-muted)]">
          {session.current_human_dossier}
        </p>
      </div>

      <div className="flex-1 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)]">
        {racm ? (
          <pre className="p-4 text-xs text-[var(--color-text-secondary)] whitespace-pre-wrap break-words">
            {JSON.stringify(racm, null, 2)}
          </pre>
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-muted)]">
            RACM not yet available
          </div>
        )}
      </div>

      <ApprovalGate session={session} />
    </div>
  );
}
