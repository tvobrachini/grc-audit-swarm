import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api, type EffectiveView, type SessionDetail } from "@/api/client";
import { renderWithClient, makeSession } from "@/test/testUtils";
import { ReviewerIdentityProvider } from "@/hooks/ReviewerIdentityProvider";
import { FindingDecisions } from "./FindingDecisions";

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      sessions: { ...actual.api.sessions, decisions: { record: vi.fn(), list: vi.fn() } },
    },
  };
});

const mockedApi = vi.mocked(api, { deep: true });

function effectiveWith(overrides: Partial<EffectiveView["findings"][number]> = {}): EffectiveView {
  return {
    decisions_required: true,
    deficiency_scale: null,
    findings: [
      {
        control_id: "CTRL-001",
        key_control: true,
        draft: {
          tod_conclusion: "Effective",
          toe_conclusion: "Effective",
          result: "Exception",
          preliminary_deficiency: true,
        },
        effective: {
          tod_conclusion: "Effective",
          toe_conclusion: "Effective",
          result: "Exception",
          preliminary_deficiency: true,
        },
        review_status: "not_reviewed",
        review: null,
        scope_limitation: null,
        differs_from_draft: false,
        review_required_for_gate_2: true,
        ...overrides,
      },
    ],
    deficiencies: [],
    engagement_conclusion: null,
    missing_for_gate: {},
    reviewer_change_rate: { subjects_decided: 0, subjects_changed: 0, rate: null, published: false },
    stale_decision_ids: [],
    superseded_decision_ids: [],
  };
}

function session(effective: EffectiveView): SessionDetail {
  return makeSession({ status: "WAITING_HUMAN_GATE_2", phase: 2, effective });
}

beforeEach(() => {
  mockedApi.sessions.decisions.record.mockReset();
});

describe("FindingDecisions", () => {
  it("submits a sign-off with the reviewer's name", async () => {
    const user = userEvent.setup();
    mockedApi.sessions.decisions.record.mockResolvedValue({
      decision_id: "d1",
      phase: 2,
      artifact: "working_papers",
      draft_digest: "x",
      subject_type: "finding",
      subject_id: "CTRL-001",
      decision_type: "sign_off",
      values: {},
      rationale: "",
      decided_by: "M. Alvarez",
      identity_source: "declared",
      decided_at: "2026-01-01T00:00:00Z",
      supersedes: null,
      state: "active",
    });

    renderWithClient(
      <ReviewerIdentityProvider>
        <FindingDecisions session={session(effectiveWith())} controlId="CTRL-001" />
      </ReviewerIdentityProvider>
    );

    await user.type(screen.getByPlaceholderText("Your name / ID"), "M. Alvarez");
    await user.click(screen.getByRole("button", { name: /^sign off$/i }));

    await waitFor(() =>
      expect(mockedApi.sessions.decisions.record).toHaveBeenCalledWith(
        "s1",
        expect.objectContaining({
          decision_type: "sign_off",
          subject_id: "CTRL-001",
          decided_by: "M. Alvarez",
        })
      )
    );
  });

  it("requires a rationale before a challenge can be submitted", async () => {
    const user = userEvent.setup();
    renderWithClient(
      <ReviewerIdentityProvider>
        <FindingDecisions session={session(effectiveWith())} controlId="CTRL-001" />
      </ReviewerIdentityProvider>
    );

    await user.type(screen.getByPlaceholderText("Your name / ID"), "M. Alvarez");
    await user.click(screen.getByRole("button", { name: /challenge/i }));

    const submit = screen.getByRole("button", { name: /submit challenge/i });
    expect(submit).toBeDisabled();

    await user.type(screen.getByPlaceholderText(/rationale/i), "Evidence does not support this.");
    expect(submit).toBeEnabled();

    await user.click(submit);
    await waitFor(() =>
      expect(mockedApi.sessions.decisions.record).toHaveBeenCalledWith(
        "s1",
        expect.objectContaining({
          decision_type: "challenge",
          rationale: "Evidence does not support this.",
        })
      )
    );
  });

  it("supersedes the active decision when re-signing off", async () => {
    const user = userEvent.setup();
    mockedApi.sessions.decisions.record.mockResolvedValue({
      decision_id: "d2",
      phase: 2,
      artifact: "working_papers",
      draft_digest: "x",
      subject_type: "finding",
      subject_id: "CTRL-001",
      decision_type: "sign_off",
      values: {},
      rationale: "",
      decided_by: "M. Alvarez",
      identity_source: "declared",
      decided_at: "2026-01-01T00:01:00Z",
      supersedes: "d1",
      state: "active",
    });

    const effective = effectiveWith({
      review_status: "signed_off",
      review: {
        decision_id: "d1",
        decision_type: "sign_off",
        decided_by: "M. Alvarez",
        identity_source: "declared",
        decided_at: "2026-01-01T00:00:00Z",
        rationale: "",
        values: {},
        supersedes: null,
      },
    });

    renderWithClient(
      <ReviewerIdentityProvider>
        <FindingDecisions session={session(effective)} controlId="CTRL-001" />
      </ReviewerIdentityProvider>
    );

    expect(screen.getByText(/Signed off by M\. Alvarez/)).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("Your name / ID"), "M. Alvarez");
    await user.click(screen.getByRole("button", { name: /re-sign off/i }));

    await waitFor(() =>
      expect(mockedApi.sessions.decisions.record).toHaveBeenCalledWith(
        "s1",
        expect.objectContaining({ decision_type: "sign_off", supersedes: "d1" })
      )
    );
  });

  it("shows a stale decision as needing a fresh review", () => {
    const effective = effectiveWith({
      review_status: "signed_off",
      review: {
        decision_id: "d1",
        decision_type: "sign_off",
        decided_by: "M. Alvarez",
        identity_source: "declared",
        decided_at: "2026-01-01T00:00:00Z",
        rationale: "",
        values: {},
        supersedes: null,
      },
    });
    effective.stale_decision_ids = ["d1"];

    renderWithClient(
      <ReviewerIdentityProvider>
        <FindingDecisions session={session(effective)} controlId="CTRL-001" />
      </ReviewerIdentityProvider>
    );
    expect(screen.getByText(/Stale — draft reworked after this decision/)).toBeInTheDocument();
  });
});

describe("FindingDecisions actions follow the gate", () => {
  const notTested = {
    tod_conclusion: "Not tested",
    toe_conclusion: "Not tested",
    result: "Not tested",
    preliminary_deficiency: false,
  };

  it("offers sign-off and challenge at Gate 2 but not scope limitation", () => {
    renderWithClient(
      <FindingDecisions
        session={session(effectiveWith({ draft: notTested, effective: notTested }))}
        controlId="CTRL-001"
      />
    );
    expect(screen.getByRole("button", { name: /Sign off/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Challenge/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /scope limitation/ })).not.toBeInTheDocument();
  });

  it("offers only scope limitation for an untested control at Gate 3", () => {
    renderWithClient(
      <FindingDecisions
        session={makeSession({
          status: "WAITING_HUMAN_GATE_3",
          phase: 3,
          effective: effectiveWith({ draft: notTested, effective: notTested }),
        })}
        controlId="CTRL-001"
      />
    );
    expect(screen.getByRole("button", { name: /Record scope limitation/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Sign off/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Challenge/ })).not.toBeInTheDocument();
  });

  it("is read-only once the audit is completed", () => {
    renderWithClient(
      <FindingDecisions
        session={makeSession({
          status: "COMPLETED",
          phase: 3,
          effective: effectiveWith({ draft: notTested, effective: notTested }),
        })}
        controlId="CTRL-001"
      />
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
