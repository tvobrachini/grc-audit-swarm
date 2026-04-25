import { FileDown } from "lucide-react";
import type { SessionDetail } from "@/api/client";

interface Props {
  session: SessionDetail;
}

function downloadJson(data: unknown, filename: string) {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function FilesPane({ session }: Props) {
  const prefix = session.session_id.slice(0, 8);
  const files = [
    session.racm_plan && {
      label: `racm_${prefix}.json`,
      data: session.racm_plan,
    },
    session.working_papers && {
      label: `working_papers_${prefix}.json`,
      data: session.working_papers,
    },
    session.final_report && {
      label: `final_report_${prefix}.json`,
      data: session.final_report,
    },
  ].filter(Boolean) as { label: string; data: unknown }[];

  return (
    <div className="space-y-2">
      {files.length === 0 && (
        <p className="text-xs text-[var(--color-text-muted)] opacity-50">
          Files will appear here as phases complete...
        </p>
      )}
      {files.map(({ label, data }) => (
        <button
          key={label}
          onClick={() => downloadJson(data, label)}
          className="flex w-full items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-2 text-left text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]"
        >
          <FileDown size={12} className="text-violet-400 shrink-0" />
          {label}
        </button>
      ))}
    </div>
  );
}
