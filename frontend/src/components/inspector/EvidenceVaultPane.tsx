import { useQuery } from "@tanstack/react-query";
import { ShieldCheck, ShieldX } from "lucide-react";
import { api, type SessionDetail } from "@/api/client";

interface VaultFinding {
  control_id: string;
  vault_id_reference: string;
  exact_quote_from_evidence: string;
}

interface Props {
  session: SessionDetail;
}

/** Lists the evidence records the working papers cite, each checked against
 * the vault: the quote must appear verbatim in the stored, digest-verified
 * record (POST /api/evidence/verify). Shares its query cache with the
 * findings board badges. */
export function EvidenceVaultPane({ session }: Props) {
  const findings = (
    (session.working_papers as { findings?: VaultFinding[] } | null)?.findings ?? []
  ).filter((f) => f.vault_id_reference);

  if (findings.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-muted)] opacity-50">
        Evidence records appear here once fieldwork has produced working papers.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {findings.map((f) => (
        <VaultRecord key={`${f.control_id}-${f.vault_id_reference}`} finding={f} />
      ))}
    </div>
  );
}

function VaultRecord({ finding }: { finding: VaultFinding }) {
  const { vault_id_reference: vaultId, exact_quote_from_evidence: quote } = finding;
  const { data, isError, isPending } = useQuery({
    queryKey: ["verify", vaultId, quote],
    queryFn: () => api.evidence.verify(vaultId, quote),
    enabled: !!vaultId && !!quote,
    staleTime: Infinity,
  });

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] p-3">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[10px] text-violet-400">{finding.control_id}</span>
        {data?.verified === true && (
          <ShieldCheck size={12} className="ml-auto text-green-400" aria-label="verified" />
        )}
        {data?.verified === false && (
          <ShieldX size={12} className="ml-auto text-red-400" aria-label="not verified" />
        )}
      </div>
      <p className="mt-1 break-all font-mono text-[10px] text-[var(--color-text-muted)]">
        {vaultId}
      </p>
      {quote && (
        <p className="mt-1 text-[11px] italic text-[var(--color-text-muted)]">"{quote}"</p>
      )}
      <p className="mt-1.5 text-[10px] text-[var(--color-text-muted)]">
        {!quote
          ? "No quote cited"
          : isError
            ? "Verification unavailable"
            : isPending
              ? "Verifying..."
              : data?.verified
                ? "Quote found verbatim in the stored record"
                : "Quote not found in the stored record"}
      </p>
    </div>
  );
}
