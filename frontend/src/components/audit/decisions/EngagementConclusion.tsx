import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, describeError, type RecordDecisionBody, type SessionDetail } from "@/api/client";
import { useReviewerIdentity } from "@/hooks/useReviewerIdentity";
import { ReviewerFields } from "./ReviewerFields";

// Mirrors swarm.schema.EngagementRating / review_policy.ENGAGEMENT_CONCLUSION_SCALE.
const SCALE = ["Satisfactory", "Needs improvement", "Unsatisfactory"];

export function EngagementConclusion({ session }: { session: SessionDetail }) {
  const qc = useQueryClient();
  const { name } = useReviewerIdentity();
  const conclusion = session.effective?.engagement_conclusion ?? null;
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(conclusion?.values.conclusion ?? SCALE[0]);
  const [rationale, setRationale] = useState(conclusion?.rationale ?? "");

  const record = useMutation({
    mutationFn: (body: RecordDecisionBody) => api.sessions.decisions.record(session.session_id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["session", session.session_id] });
      qc.invalidateQueries({ queryKey: ["sessions"] });
      setOpen(false);
    },
  });

  if (!session.effective) return null;
  const isStale = conclusion
    ? session.effective.stale_decision_ids.includes(conclusion.decision_id)
    : false;

  const submit = () =>
    record.mutate({
      decision_type: "engagement_conclusion",
      subject_id: "engagement",
      decided_by: name.trim(),
      values: { conclusion: value },
      rationale: rationale.trim(),
      ...(conclusion && !isStale ? { supersedes: conclusion.decision_id } : {}),
    });

  return (
    <div
      id="decision-engagement"
      className="space-y-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-3"
    >
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <span className="font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
          Engagement conclusion:
        </span>
        <span className="rounded border px-2 py-0.5 font-semibold text-[var(--color-text-secondary)]">
          {conclusion ? conclusion.values.conclusion : "Not yet recorded"}
        </span>
        {conclusion && (
          <span className="text-[var(--color-text-muted)]">by {conclusion.decided_by}</span>
        )}
        {isStale && (
          <span className="rounded bg-red-900/30 px-2 py-0.5 font-medium text-red-400">
            Stale — draft reworked after this decision
          </span>
        )}
      </div>

      {!open ? (
        <button
          onClick={() => setOpen(true)}
          className="rounded-lg border border-violet-700/40 px-3 py-1.5 text-xs text-violet-400 hover:bg-violet-900/20"
        >
          {conclusion ? "Change engagement conclusion" : "Record engagement conclusion"}
        </button>
      ) : (
        <div className="space-y-2">
          <ReviewerFields />
          <label className="block text-[11px] text-[var(--color-text-secondary)]">
            Conclusion
            <select
              value={value}
              onChange={(e) => setValue(e.target.value)}
              className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 py-1.5 text-xs text-[var(--color-text-primary)]"
            >
              {SCALE.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          </label>
          <textarea
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            rows={2}
            placeholder="Rationale (required)"
            className="w-full resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-2 text-xs text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] outline-none focus:border-violet-500"
          />
          <div className="flex gap-2">
            <button
              onClick={submit}
              disabled={!name.trim() || !rationale.trim() || record.isPending}
              className="rounded-lg bg-violet-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-600 disabled:opacity-50"
            >
              Record conclusion
            </button>
            <button
              onClick={() => setOpen(false)}
              className="rounded-lg px-3 py-1.5 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {record.isError && (
        <p role="alert" className="text-[11px] text-red-400">
          {describeError(record.error)}
        </p>
      )}
    </div>
  );
}
