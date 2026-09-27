import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, MessageSquareWarning, ShieldAlert } from "lucide-react";
import { clsx } from "clsx";
import {
  api,
  describeError,
  type RecordDecisionBody,
  type SessionDetail,
} from "@/api/client";
import { useReviewerIdentity } from "@/hooks/useReviewerIdentity";
import { ReviewerFields } from "./ReviewerFields";

// Mirrors swarm.schema.DesignConclusion / OperatingConclusion.
const TOD_OPTIONS = ["Effective", "Ineffective", "Not tested"];
const TOE_OPTIONS = ["Effective", "Exceptions noted", "Ineffective", "Not tested"];

interface Props {
  session: SessionDetail;
  controlId: string;
}

type Dialog = "challenge" | "scope_limitation" | null;

/** Per-finding reviewer decisions: draft vs conclusion of record, review
 * status, and the sign-off / challenge / scope-limitation actions (see
 * DECISIONS.md ADR-011). Renders nothing if the session has no effective
 * view yet (pre-ADR-011 sessions, or before working papers exist). */
export function FindingDecisions({ session, controlId }: Props) {
  const qc = useQueryClient();
  const { name } = useReviewerIdentity();
  const effective = session.effective;
  const view = effective?.findings.find((f) => f.control_id === controlId);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [tod, setTod] = useState("");
  const [toe, setToe] = useState("");
  const [rationale, setRationale] = useState("");

  const record = useMutation({
    mutationFn: (body: RecordDecisionBody) => api.sessions.decisions.record(session.session_id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["session", session.session_id] });
      qc.invalidateQueries({ queryKey: ["sessions"] });
      setDialog(null);
      setRationale("");
      setTod("");
      setToe("");
    },
  });

  if (!effective || !view) return null;

  const isStale = view.review ? effective.stale_decision_ids.includes(view.review.decision_id) : false;
  const scopeIsStale = view.scope_limitation
    ? effective.stale_decision_ids.includes(view.scope_limitation.decision_id)
    : false;

  const openChallenge = () => {
    setTod(view.review?.values.tod_conclusion ?? view.draft.tod_conclusion);
    setToe(view.review?.values.toe_conclusion ?? view.draft.toe_conclusion);
    setRationale(view.review?.decision_type === "challenge" ? view.review.rationale : "");
    setDialog("challenge");
  };

  const signOff = () => {
    const body: RecordDecisionBody = {
      decision_type: "sign_off",
      subject_id: controlId,
      decided_by: name.trim(),
      ...(view.review && !isStale ? { supersedes: view.review.decision_id } : {}),
    };
    record.mutate(body);
  };

  const submitChallenge = () => {
    const values: Record<string, string> = {};
    if (tod && tod !== view.draft.tod_conclusion) values.tod_conclusion = tod;
    if (toe && toe !== view.draft.toe_conclusion) values.toe_conclusion = toe;
    record.mutate({
      decision_type: "challenge",
      subject_id: controlId,
      decided_by: name.trim(),
      values,
      rationale: rationale.trim(),
      ...(view.review && !isStale ? { supersedes: view.review.decision_id } : {}),
    });
  };

  const submitScopeLimitation = () => {
    record.mutate({
      decision_type: "scope_limitation",
      subject_id: controlId,
      decided_by: name.trim(),
      rationale: rationale.trim(),
      ...(view.scope_limitation && !scopeIsStale
        ? { supersedes: view.scope_limitation.decision_id }
        : {}),
    });
  };

  const canSubmitName = !!name.trim();
  const statusLabel =
    view.review_status === "signed_off"
      ? `Signed off by ${view.review?.decided_by ?? ""}`
      : view.review_status === "challenged"
        ? `Challenged by ${view.review?.decided_by ?? ""}`
        : "Not reviewed";

  return (
    <div className="space-y-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-2">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <span className="font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
          Conclusion of record:
        </span>
        <span className="text-[var(--color-text-secondary)]">
          ToD {view.effective.tod_conclusion} / ToE {view.effective.toe_conclusion} /{" "}
          {view.effective.result}
        </span>
        {view.differs_from_draft && (
          <span className="italic text-[var(--color-text-muted)]">
            AI draft: ToD {view.draft.tod_conclusion} / ToE {view.draft.toe_conclusion} /{" "}
            {view.draft.result}
          </span>
        )}
        <span
          className={clsx(
            "rounded px-2 py-0.5 font-medium",
            view.review_status === "not_reviewed"
              ? "bg-amber-900/30 text-amber-400"
              : "bg-green-900/30 text-green-400"
          )}
        >
          {statusLabel}
        </span>
        {isStale && (
          <span className="rounded bg-red-900/30 px-2 py-0.5 font-medium text-red-400">
            Stale — draft reworked after this decision
          </span>
        )}
        {view.scope_limitation && (
          <span
            className={clsx(
              "rounded px-2 py-0.5 font-medium",
              scopeIsStale ? "bg-red-900/30 text-red-400" : "bg-sky-900/30 text-sky-400"
            )}
          >
            {scopeIsStale
              ? "Scope limitation — stale, reworked since"
              : `Scope limitation recorded by ${view.scope_limitation.decided_by}`}
          </span>
        )}
      </div>

      <ReviewerFields />

      <div className="flex flex-wrap gap-2">
        <button
          onClick={signOff}
          disabled={!canSubmitName || record.isPending}
          className="flex items-center gap-1.5 rounded-lg border border-green-700/40 px-3 py-1.5 text-xs text-green-400 hover:bg-green-900/20 disabled:opacity-50"
        >
          <CheckCircle2 size={12} />
          {view.review && !isStale ? "Re-sign off" : "Sign off"}
        </button>
        <button
          onClick={openChallenge}
          disabled={!canSubmitName || record.isPending}
          className="flex items-center gap-1.5 rounded-lg border border-amber-700/40 px-3 py-1.5 text-xs text-amber-400 hover:bg-amber-900/20 disabled:opacity-50"
        >
          <MessageSquareWarning size={12} />
          Challenge
        </button>
        {view.effective.result === "Not tested" && (
          <button
            onClick={() => {
              setRationale(view.scope_limitation?.rationale ?? "");
              setDialog("scope_limitation");
            }}
            disabled={!canSubmitName || record.isPending}
            className="flex items-center gap-1.5 rounded-lg border border-sky-700/40 px-3 py-1.5 text-xs text-sky-400 hover:bg-sky-900/20 disabled:opacity-50"
          >
            <ShieldAlert size={12} />
            Record scope limitation
          </button>
        )}
      </div>

      {record.isError && (
        <p role="alert" className="text-[11px] text-red-400">
          {describeError(record.error)}
        </p>
      )}

      {dialog === "challenge" && (
        <div className="space-y-2 rounded-lg border border-amber-700/40 bg-amber-900/10 p-3">
          <p className="text-xs font-medium text-amber-300">
            Challenge {controlId} — change ToD/ToE only to withdraw a positive
            conclusion to "Not tested" without rework; any other change needs the
            phase returned for rework.
          </p>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-[11px] text-[var(--color-text-secondary)]">
              ToD conclusion
              <select
                value={tod}
                onChange={(e) => setTod(e.target.value)}
                className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 py-1.5 text-xs text-[var(--color-text-primary)]"
              >
                {TOD_OPTIONS.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-[11px] text-[var(--color-text-secondary)]">
              ToE conclusion
              <select
                value={toe}
                onChange={(e) => setToe(e.target.value)}
                className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 py-1.5 text-xs text-[var(--color-text-primary)]"
              >
                {TOE_OPTIONS.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <textarea
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            rows={2}
            placeholder="Rationale (required)"
            className="w-full resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-2 text-xs text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] outline-none focus:border-amber-500"
          />
          <div className="flex gap-2">
            <button
              onClick={submitChallenge}
              disabled={!canSubmitName || !rationale.trim() || record.isPending}
              className="rounded-lg bg-amber-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-600 disabled:opacity-50"
            >
              Submit challenge
            </button>
            <button
              onClick={() => setDialog(null)}
              className="rounded-lg px-3 py-1.5 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {dialog === "scope_limitation" && (
        <div className="space-y-2 rounded-lg border border-sky-700/40 bg-sky-900/10 p-3">
          <p className="text-xs font-medium text-sky-300">
            Record how this untested control is reported as a scope limitation.
          </p>
          <textarea
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            rows={2}
            placeholder="Scope limitation as it will be reported (required)"
            className="w-full resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-2 text-xs text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] outline-none focus:border-sky-500"
          />
          <div className="flex gap-2">
            <button
              onClick={submitScopeLimitation}
              disabled={!canSubmitName || !rationale.trim() || record.isPending}
              className="rounded-lg bg-sky-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-600 disabled:opacity-50"
            >
              Record scope limitation
            </button>
            <button
              onClick={() => setDialog(null)}
              className="rounded-lg px-3 py-1.5 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
