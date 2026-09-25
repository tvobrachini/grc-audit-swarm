import { useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { api, describeError, type ExportKind, type SessionDetail } from "@/api/client";

interface Props {
  session: SessionDetail;
}

function exportsFor(session: SessionDetail): { kind: ExportKind; label: string }[] {
  const out: { kind: ExportKind; label: string }[] = [];
  if (session.racm_plan) out.push({ kind: "racm.xlsx", label: "RACM (.xlsx)" });
  if (session.working_papers)
    out.push({ kind: "working-papers.xlsx", label: "Working papers (.xlsx)" });
  if (session.final_report) {
    out.push({ kind: "report.md", label: "Report (.md)" });
    if (session.final_report.oscal_sar)
      out.push({ kind: "oscal.json", label: "OSCAL results (.json)" });
  }
  return out;
}

/** Download buttons for every artifact the session has so far. */
export function ExportBar({ session }: Props) {
  const [pending, setPending] = useState<ExportKind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const items = exportsFor(session);
  if (items.length === 0) return null;

  const run = async (kind: ExportKind) => {
    setPending(kind);
    setError(null);
    try {
      await api.sessions.export(session.session_id, kind);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setPending(null);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-[var(--color-border)] px-6 py-2">
      <span className="text-[10px] font-semibold uppercase tracking-widest text-[var(--color-text-muted)]">
        Export
      </span>
      {items.map(({ kind, label }) => (
        <button
          key={kind}
          onClick={() => run(kind)}
          disabled={pending !== null}
          className="flex items-center gap-1.5 rounded border border-[var(--color-border)] px-2 py-1 text-[11px] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] disabled:opacity-50"
        >
          {pending === kind ? (
            <Loader2 size={11} className="animate-spin" />
          ) : (
            <Download size={11} className="text-violet-400" />
          )}
          {label}
        </button>
      ))}
      {error && (
        <span role="alert" className="text-[11px] text-red-400">
          {error}
        </span>
      )}
    </div>
  );
}
