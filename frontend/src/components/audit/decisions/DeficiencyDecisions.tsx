import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import {
  api,
  describeError,
  type DeficiencyView,
  type RecordDecisionBody,
  type SessionDetail,
} from "@/api/client";
import { useReviewerIdentity } from "@/hooks/useReviewerIdentity";
import { ReviewerFields } from "./ReviewerFields";

// Mirrors swarm.schema.SCALE_CLASSIFICATIONS.
const SCALE_CLASSIFICATIONS: Record<string, string[]> = {
  "ICFR deficiency scale": [
    "Control Deficiency",
    "Significant Deficiency",
    "Material Weakness",
    "Not a deficiency",
  ],
  "Risk rating": ["Low", "Medium", "High", "Not a deficiency"],
};
const RISK_RATINGS = ["Low", "Medium", "High"];
const AGREEMENTS = ["agree", "partial", "disagree"];

interface Props {
  session: SessionDetail;
  deficiencyId: string;
}

type Dialog = "classify" | "writeup" | "management_response" | null;

export function DeficiencyDecisions({ session, deficiencyId }: Props) {
  const qc = useQueryClient();
  const { name } = useReviewerIdentity();
  const effective = session.effective;
  const view: DeficiencyView | undefined = effective?.deficiencies.find(
    (d) => d.deficiency_id === deficiencyId,
  );
  const [dialog, setDialog] = useState<Dialog>(null);

  const record = useMutation({
    mutationFn: (body: RecordDecisionBody) =>
      api.sessions.decisions.record(session.session_id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["session", session.session_id] });
      qc.invalidateQueries({ queryKey: ["sessions"] });
      setDialog(null);
    },
  });

  if (!effective || !view) return null;

  const isStale = (ref: { decision_id: string } | null) =>
    ref ? effective.stale_decision_ids.includes(ref.decision_id) : false;

  const canSubmitName = !!name.trim();
  // Classification and write-up belong to Gate 3; management responses may
  // still be transcribed after completion (review_policy.py).
  const atGate3 = session.status === "WAITING_HUMAN_GATE_3";
  const scale = effective.deficiency_scale ?? "ICFR deficiency scale";
  const classifyStale = isStale(view.classification_decision);

  return (
    <div className="space-y-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-3">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <span className="font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
          Conclusion of record:
        </span>
        <span className="rounded border px-2 py-0.5 font-semibold text-[var(--color-text-secondary)]">
          {view.effective.classification}
        </span>
        {view.differs_from_draft && (
          <span className="italic text-[var(--color-text-muted)]">
            AI draft: {view.draft.classification} (likelihood{" "}
            {view.draft.likelihood}, magnitude {view.draft.magnitude})
          </span>
        )}
        <span
          className={clsx(
            "rounded px-2 py-0.5 font-medium",
            view.classification_source === "reviewer"
              ? "bg-green-900/30 text-green-400"
              : "bg-amber-900/30 text-amber-400",
          )}
        >
          {view.classification_source === "reviewer"
            ? `Classified by ${view.classification_decision?.decided_by ?? ""}`
            : "Proposed (AI draft) — not yet classified"}
        </span>
        {classifyStale && (
          <span className="rounded bg-red-900/30 px-2 py-0.5 font-medium text-red-400">
            Stale — draft reworked after this decision
          </span>
        )}
      </div>

      <ReviewerFields />

      <div className="flex flex-wrap gap-2">
        {atGate3 && (
          <>
            <button
              onClick={() => setDialog("classify")}
              disabled={!canSubmitName || record.isPending}
              className="rounded-lg border border-violet-700/40 px-3 py-1.5 text-xs text-violet-400 hover:bg-violet-900/20 disabled:opacity-50"
            >
              Classify
            </button>
            <button
              onClick={() => setDialog("writeup")}
              disabled={!canSubmitName || record.isPending}
              className="rounded-lg border border-violet-700/40 px-3 py-1.5 text-xs text-violet-400 hover:bg-violet-900/20 disabled:opacity-50"
            >
              {view.writeup ? "Edit write-up" : "Write-up"}
            </button>
          </>
        )}
        <button
          onClick={() => setDialog("management_response")}
          disabled={!canSubmitName || record.isPending}
          className="rounded-lg border border-violet-700/40 px-3 py-1.5 text-xs text-violet-400 hover:bg-violet-900/20 disabled:opacity-50"
        >
          {view.management_response
            ? "Edit management response"
            : "Management response"}
        </button>
      </div>

      {record.isError && (
        <p role="alert" className="text-[11px] text-red-400">
          {describeError(record.error)}
        </p>
      )}

      {dialog === "classify" && (
        <ClassifyForm
          view={view}
          scale={scale}
          decidedBy={name}
          isStale={classifyStale}
          onCancel={() => setDialog(null)}
          onSubmit={(body) => record.mutate(body)}
          pending={record.isPending}
        />
      )}
      {dialog === "writeup" && (
        <WriteupForm
          view={view}
          decidedBy={name}
          isStale={isStale(view.writeup)}
          onCancel={() => setDialog(null)}
          onSubmit={(body) => record.mutate(body)}
          pending={record.isPending}
        />
      )}
      {dialog === "management_response" && (
        <ManagementResponseForm
          view={view}
          decidedBy={name}
          isStale={isStale(view.management_response)}
          onCancel={() => setDialog(null)}
          onSubmit={(body) => record.mutate(body)}
          pending={record.isPending}
        />
      )}
    </div>
  );
}

function ClassifyForm({
  view,
  scale,
  decidedBy,
  isStale,
  onCancel,
  onSubmit,
  pending,
}: {
  view: DeficiencyView;
  scale: string;
  decidedBy: string;
  isStale: boolean;
  onCancel: () => void;
  onSubmit: (body: RecordDecisionBody) => void;
  pending: boolean;
}) {
  const options =
    SCALE_CLASSIFICATIONS[scale] ??
    SCALE_CLASSIFICATIONS["ICFR deficiency scale"];
  const [classification, setClassification] = useState(
    view.effective.classification,
  );
  const [likelihood, setLikelihood] = useState(view.effective.likelihood);
  const [magnitude, setMagnitude] = useState(view.effective.magnitude);
  const [rationale, setRationale] = useState("");

  const differs =
    classification !== view.draft.classification ||
    likelihood !== view.draft.likelihood ||
    magnitude !== view.draft.magnitude;

  const submit = () =>
    onSubmit({
      decision_type: "classify",
      subject_id: view.deficiency_id,
      decided_by: decidedBy.trim(),
      values: { classification, likelihood, magnitude },
      rationale: rationale.trim(),
      ...(view.classification_decision && !isStale
        ? { supersedes: view.classification_decision.decision_id }
        : {}),
    });

  return (
    <div className="space-y-2 rounded-lg border border-violet-700/40 bg-violet-900/10 p-3">
      <div className="grid grid-cols-3 gap-2">
        <label className="text-[11px] text-[var(--color-text-secondary)]">
          Classification
          <select
            value={classification}
            onChange={(e) => setClassification(e.target.value)}
            className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 py-1.5 text-xs text-[var(--color-text-primary)]"
          >
            {options.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[11px] text-[var(--color-text-secondary)]">
          Likelihood
          <select
            value={likelihood}
            onChange={(e) => setLikelihood(e.target.value)}
            className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 py-1.5 text-xs text-[var(--color-text-primary)]"
          >
            {RISK_RATINGS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[11px] text-[var(--color-text-secondary)]">
          Magnitude
          <select
            value={magnitude}
            onChange={(e) => setMagnitude(e.target.value)}
            className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 py-1.5 text-xs text-[var(--color-text-primary)]"
          >
            {RISK_RATINGS.map((o) => (
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
        placeholder={
          differs
            ? "Rationale (required — differs from the AI draft)"
            : "Rationale (optional)"
        }
        className="w-full resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-2 text-xs text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] outline-none focus:border-violet-500"
      />
      <div className="flex gap-2">
        <button
          onClick={submit}
          disabled={
            !decidedBy.trim() || (differs && !rationale.trim()) || pending
          }
          className="rounded-lg bg-violet-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-600 disabled:opacity-50"
        >
          Record classification
        </button>
        <button
          onClick={onCancel}
          className="rounded-lg px-3 py-1.5 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function WriteupForm({
  view,
  decidedBy,
  isStale,
  onCancel,
  onSubmit,
  pending,
}: {
  view: DeficiencyView;
  decidedBy: string;
  isStale: boolean;
  onCancel: () => void;
  onSubmit: (body: RecordDecisionBody) => void;
  pending: boolean;
}) {
  const existing = view.writeup?.values ?? {};
  const [criteria, setCriteria] = useState(existing.criteria ?? "");
  const [condition, setCondition] = useState(existing.condition ?? "");
  const [cause, setCause] = useState(existing.cause ?? "");
  const [effect, setEffect] = useState(existing.effect ?? "");
  const [recommendation, setRecommendation] = useState(
    existing.recommendation ?? "",
  );

  const complete = [criteria, condition, cause, effect, recommendation].every(
    (v) => v.trim(),
  );

  const submit = () =>
    onSubmit({
      decision_type: "writeup",
      subject_id: view.deficiency_id,
      decided_by: decidedBy.trim(),
      values: { criteria, condition, cause, effect, recommendation },
      ...(view.writeup && !isStale
        ? { supersedes: view.writeup.decision_id }
        : {}),
    });

  const fields: [string, string, (v: string) => void][] = [
    ["Criteria", criteria, setCriteria],
    ["Condition", condition, setCondition],
    ["Cause", cause, setCause],
    ["Effect", effect, setEffect],
    ["Recommendation", recommendation, setRecommendation],
  ];

  return (
    <div className="space-y-2 rounded-lg border border-violet-700/40 bg-violet-900/10 p-3">
      {fields.map(([label, value, set]) => (
        <label
          key={label}
          className="block text-[11px] text-[var(--color-text-secondary)]"
        >
          {label}
          <textarea
            value={value}
            onChange={(e) => set(e.target.value)}
            rows={2}
            className="mt-1 w-full resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 py-1.5 text-xs text-[var(--color-text-primary)] outline-none focus:border-violet-500"
          />
        </label>
      ))}
      <div className="flex gap-2">
        <button
          onClick={submit}
          disabled={!decidedBy.trim() || !complete || pending}
          className="rounded-lg bg-violet-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-600 disabled:opacity-50"
        >
          Save write-up
        </button>
        <button
          onClick={onCancel}
          className="rounded-lg px-3 py-1.5 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function ManagementResponseForm({
  view,
  decidedBy,
  isStale,
  onCancel,
  onSubmit,
  pending,
}: {
  view: DeficiencyView;
  decidedBy: string;
  isStale: boolean;
  onCancel: () => void;
  onSubmit: (body: RecordDecisionBody) => void;
  pending: boolean;
}) {
  const existing = view.management_response?.values ?? {};
  const [text, setText] = useState(existing.text ?? "");
  const [agreement, setAgreement] = useState(existing.agreement ?? "agree");
  const [receivedFrom, setReceivedFrom] = useState(
    existing.received_from ?? "",
  );
  const [receivedOn, setReceivedOn] = useState(existing.received_on ?? "");
  const [owner, setOwner] = useState(existing.action_owner_role ?? "");
  const [targetDate, setTargetDate] = useState(existing.target_date ?? "");
  const [rationale, setRationale] = useState(
    view.management_response?.rationale ?? "",
  );

  const needsPlan = agreement === "agree" || agreement === "partial";
  const needsRebuttal = agreement === "disagree";
  const complete =
    text.trim() &&
    receivedFrom.trim() &&
    receivedOn.trim() &&
    (!needsPlan || (owner.trim() && targetDate.trim())) &&
    (!needsRebuttal || rationale.trim());

  const submit = () =>
    onSubmit({
      decision_type: "management_response",
      subject_id: view.deficiency_id,
      decided_by: decidedBy.trim(),
      values: {
        text,
        agreement,
        received_from: receivedFrom,
        received_on: receivedOn,
        ...(needsPlan
          ? { action_owner_role: owner, target_date: targetDate }
          : {}),
      },
      rationale: rationale.trim(),
      ...(view.management_response && !isStale
        ? { supersedes: view.management_response.decision_id }
        : {}),
    });

  return (
    <div className="space-y-2 rounded-lg border border-violet-700/40 bg-violet-900/10 p-3">
      <label className="block text-[11px] text-[var(--color-text-secondary)]">
        Response text
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          className="mt-1 w-full resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 py-1.5 text-xs text-[var(--color-text-primary)] outline-none focus:border-violet-500"
        />
      </label>
      <div className="grid grid-cols-3 gap-2">
        <label className="text-[11px] text-[var(--color-text-secondary)]">
          Agreement
          <select
            value={agreement}
            onChange={(e) => setAgreement(e.target.value)}
            className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 py-1.5 text-xs text-[var(--color-text-primary)]"
          >
            {AGREEMENTS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[11px] text-[var(--color-text-secondary)]">
          Received from
          <input
            type="text"
            value={receivedFrom}
            onChange={(e) => setReceivedFrom(e.target.value)}
            className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 py-1.5 text-xs text-[var(--color-text-primary)]"
          />
        </label>
        <label className="text-[11px] text-[var(--color-text-secondary)]">
          Received on
          <input
            type="date"
            value={receivedOn}
            onChange={(e) => setReceivedOn(e.target.value)}
            className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 py-1.5 text-xs text-[var(--color-text-primary)]"
          />
        </label>
      </div>
      {needsPlan && (
        <div className="grid grid-cols-2 gap-2">
          <label className="text-[11px] text-[var(--color-text-secondary)]">
            Action owner (role)
            <input
              type="text"
              value={owner}
              onChange={(e) => setOwner(e.target.value)}
              className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 py-1.5 text-xs text-[var(--color-text-primary)]"
            />
          </label>
          <label className="text-[11px] text-[var(--color-text-secondary)]">
            Target date
            <input
              type="date"
              value={targetDate}
              onChange={(e) => setTargetDate(e.target.value)}
              className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-2 py-1.5 text-xs text-[var(--color-text-primary)]"
            />
          </label>
        </div>
      )}
      <textarea
        value={rationale}
        onChange={(e) => setRationale(e.target.value)}
        rows={2}
        placeholder={
          needsRebuttal
            ? "Auditor's rebuttal (required for a disagreement)"
            : "Rationale (optional)"
        }
        className="w-full resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-base)] px-3 py-2 text-xs text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] outline-none focus:border-violet-500"
      />
      <div className="flex gap-2">
        <button
          onClick={submit}
          disabled={!decidedBy.trim() || !complete || pending}
          className="rounded-lg bg-violet-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-600 disabled:opacity-50"
        >
          Save management response
        </button>
        <button
          onClick={onCancel}
          className="rounded-lg px-3 py-1.5 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
