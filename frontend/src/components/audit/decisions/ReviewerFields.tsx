import { useReviewerIdentity } from "@/hooks/useReviewerIdentity";

/** Reviewer name + optional personal token, shared across the approval gate
 * and every decision form so a reviewer doesn't retype their name. The token
 * is sent as `X-Reviewer-Token` (see api/client.ts); the backend may not
 * check it yet. */
export function ReviewerFields({
  namePlaceholder = "Your name / ID",
}: {
  namePlaceholder?: string;
}) {
  const { name, setName, token, setToken } = useReviewerIdentity();
  return (
    <div className="flex flex-col gap-2 sm:flex-row">
      <input
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder={namePlaceholder}
        className="flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] outline-none focus:border-amber-500"
      />
      <input
        type="password"
        value={token}
        onChange={(e) => setToken(e.target.value)}
        placeholder="Reviewer token (optional)"
        autoComplete="off"
        className="flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] outline-none focus:border-amber-500"
      />
    </div>
  );
}
