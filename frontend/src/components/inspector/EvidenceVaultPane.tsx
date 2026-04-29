import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ShieldCheck, ShieldX } from "lucide-react";
import type { AuditEvent } from "@/api/client";
import { api } from "@/api/client";

interface Props {
  events: AuditEvent[];
}

export function EvidenceVaultPane({ events }: Props) {
  const vaultEvents = events.filter((e) => e.type === "vault_entry");
  const [verifying, setVerifying] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, boolean>>({});

  const mutation = useMutation({
    mutationFn: ({ vault_id, exact_quote }: { vault_id: string; exact_quote: string }) =>
      api.evidence.verify(vault_id, exact_quote),
    onSuccess: (data) => {
      setResults((prev) => ({ ...prev, [data.vault_id]: data.verified }));
      setVerifying(null);
    },
  });

  return (
    <div className="space-y-2">
      {vaultEvents.length === 0 && (
        <p className="text-xs text-[var(--color-text-muted)] opacity-50">
          Evidence will appear here as fieldwork runs...
        </p>
      )}
      {vaultEvents.map((e, i) => {
        const verified = results[e.vault_id!];
        return (
          <div
            key={i}
            className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3"
          >
            <div className="flex items-center gap-2">
              <span className="font-mono text-[10px] text-violet-400">
                {e.vault_id}
              </span>
              {verified === true && (
                <ShieldCheck size={12} className="ml-auto text-green-400" />
              )}
              {verified === false && (
                <ShieldX size={12} className="ml-auto text-red-400" />
              )}
            </div>
            <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
              {e.source}
            </p>
            {verified === undefined && verifying !== e.vault_id && (
              <button
                onClick={() => {
                  setVerifying(e.vault_id!);
                  mutation.mutate({
                    vault_id: e.vault_id!,
                    exact_quote: e.source ?? "",
                  });
                }}
                className="mt-1.5 text-[10px] text-violet-400 hover:underline"
              >
                Verify
              </button>
            )}
            {verifying === e.vault_id && (
              <span className="mt-1.5 text-[10px] text-[var(--color-text-muted)]">
                Verifying...
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
