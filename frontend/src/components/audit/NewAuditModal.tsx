import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api, describeError } from "@/api/client";

interface Props {
  onClose: () => void;
  onCreated: (sessionId: string) => void;
}

const DEFAULT_FRAMEWORKS = ["COSO", "PCAOB", "IIA"];
// Mirrors the API limit (src/api/scope_document.py); the server enforces it.
const MAX_DOCUMENT_BYTES = 5 * 1024 * 1024;

export function NewAuditModal({ onClose, onCreated }: Props) {
  const qc = useQueryClient();
  const [theme, setTheme] = useState("");
  const [context, setContext] = useState("");
  const [frameworks, setFrameworks] = useState(DEFAULT_FRAMEWORKS);
  const [scopeFile, setScopeFile] = useState<File | null>(null);
  const documentTooLarge = scopeFile !== null && scopeFile.size > MAX_DOCUMENT_BYTES;

  const mutation = useMutation({
    mutationFn: () => {
      const body = { theme, business_context: context, frameworks };
      return scopeFile
        ? api.sessions.createWithDocument(body, scopeFile)
        : api.sessions.create(body);
    },
    onSuccess: (session) => {
      qc.invalidateQueries({ queryKey: ["sessions"] });
      onCreated(session.session_id);
      onClose();
    },
  });

  const toggleFramework = (fw: string) =>
    setFrameworks((prev) =>
      prev.includes(fw) ? prev.filter((f) => f !== fw) : [...prev, fw]
    );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div
        className="w-full max-w-lg rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-surface)] p-6 shadow-2xl"
        role="dialog"
        aria-modal="true"
      >
        <div className="mb-5 flex items-center justify-between">
          <h2 className="text-base font-semibold text-[var(--color-text-primary)]">
            New Audit
          </h2>
          <button
            onClick={onClose}
            className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
          >
            <X size={16} />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-[var(--color-text-secondary)]">
              Audit Theme
            </label>
            <input
              type="text"
              value={theme}
              onChange={(e) => setTheme(e.target.value)}
              placeholder="e.g. S3 Exposure Assessment"
              className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] outline-none focus:border-violet-500"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-[var(--color-text-secondary)]">
              Business Context
            </label>
            <textarea
              value={context}
              onChange={(e) => setContext(e.target.value)}
              rows={4}
              placeholder="Describe the environment, scope, and risk areas..."
              className="w-full resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] outline-none focus:border-violet-500"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-[var(--color-text-secondary)]">
              Scope document (optional — PDF, .txt or .md, max 5 MB)
            </label>
            <input
              type="file"
              accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown"
              onChange={(e) => setScopeFile(e.target.files?.[0] ?? null)}
              className="block w-full text-xs text-[var(--color-text-secondary)] file:mr-3 file:rounded file:border-0 file:bg-[var(--color-bg-elevated)] file:px-2.5 file:py-1 file:text-xs file:text-[var(--color-text-primary)]"
            />
            <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
              Its text is added to the business context, marked as untrusted
              user-supplied content.
            </p>
            {documentTooLarge && (
              <p role="alert" className="mt-1 text-[11px] text-red-400">
                The document is larger than 5 MB.
              </p>
            )}
          </div>

          <div>
            <label className="mb-2 block text-xs font-medium text-[var(--color-text-secondary)]">
              Frameworks
            </label>
            <div className="flex gap-2">
              {["COSO", "PCAOB", "IIA", "ISO 27001", "SOC 2"].map((fw) => (
                <button
                  key={fw}
                  type="button"
                  onClick={() => toggleFramework(fw)}
                  className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                    frameworks.includes(fw)
                      ? "bg-violet-700 text-white"
                      : "border border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-violet-500 hover:text-violet-400"
                  }`}
                >
                  {fw}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]"
          >
            Cancel
          </button>
          <button
            onClick={() => mutation.mutate()}
            disabled={
              !theme.trim() ||
              (!context.trim() && !scopeFile) ||
              documentTooLarge ||
              mutation.isPending
            }
            className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-500 disabled:opacity-50"
          >
            {mutation.isPending ? "Launching..." : "Launch Audit"}
          </button>
        </div>

        {mutation.isError && (
          <p role="alert" className="mt-3 text-xs text-red-400">
            {describeError(mutation.error)}
          </p>
        )}
      </div>
    </div>
  );
}
