import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api, type EffectiveView, type SessionDetail } from "@/api/client";
import { renderWithClient, makeSession } from "@/test/testUtils";
import { ReviewerIdentityProvider } from "@/hooks/ReviewerIdentityProvider";
import { DeficiencyDecisions } from "./DeficiencyDecisions";

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

function baseDeficiency(
  overrides: Partial<EffectiveView["deficiencies"][number]> = {}
): EffectiveView["deficiencies"][number] {
  return {
    deficiency_id: "DEF-01",
    title: "IAM access review not evidenced",
    related_findings: ["CTRL-004"],
    draft: { classification: "Significant Deficiency", likelihood: "Medium", magnitude: "High" },
    effective: {
      classification: "Significant Deficiency",
      likelihood: "Medium",
      magnitude: "High",
    },
    classification_source: "ai_draft",
    classification_decision: null,
    differs_from_draft: false,
    writeup: null,
    management_response: null,
    ...overrides,
  };
}

function effectiveWith(deficiency: EffectiveView["deficiencies"][number]): EffectiveView {
  return {
    decisions_required: true,
    deficiency_scale: "ICFR deficiency scale",
    findings: [],
    deficiencies: [deficiency],
    engagement_conclusion: null,
    missing_for_gate: {},
    reviewer_change_rate: { subjects_decided: 0, subjects_changed: 0, rate: null, published: false },
    stale_decision_ids: [],
    superseded_decision_ids: [],
  };
}

function session(effective: EffectiveView): SessionDetail {
  return makeSession({ status: "WAITING_HUMAN_GATE_3", phase: 3, effective });
}

beforeEach(() => {
  mockedApi.sessions.decisions.record.mockReset();
});

function renderDeficiency(effective: EffectiveView) {
  return renderWithClient(
    <ReviewerIdentityProvider>
      <DeficiencyDecisions session={session(effective)} deficiencyId="DEF-01" />
    </ReviewerIdentityProvider>
  );
}

describe("DeficiencyDecisions", () => {
  it("requires a rationale to classify away from the AI draft", async () => {
    const user = userEvent.setup();
    renderDeficiency(effectiveWith(baseDeficiency()));

    await user.type(screen.getByPlaceholderText("Your name / ID"), "S. Chen");
    await user.click(screen.getByRole("button", { name: /^classify$/i }));

    const classificationSelect = screen.getByLabelText(/classification/i);
    await user.selectOptions(classificationSelect, "Material Weakness");
    await user.selectOptions(screen.getByLabelText(/likelihood/i), "High");

    const submit = screen.getByRole("button", { name: /record classification/i });
    expect(submit).toBeDisabled();

    await user.type(screen.getByPlaceholderText(/rationale/i), "New evidence of a material misstatement.");
    expect(submit).toBeEnabled();

    await user.click(submit);
    await waitFor(() =>
      expect(mockedApi.sessions.decisions.record).toHaveBeenCalledWith(
        "s1",
        expect.objectContaining({
          decision_type: "classify",
          values: expect.objectContaining({ classification: "Material Weakness" }),
        })
      )
    );
  });

  it("supersedes the active classification when it is edited again", async () => {
    const user = userEvent.setup();
    const deficiency = baseDeficiency({
      classification_source: "reviewer",
      draft: { classification: "Control Deficiency", likelihood: "Low", magnitude: "Medium" },
      effective: { classification: "Control Deficiency", likelihood: "Low", magnitude: "Medium" },
      classification_decision: {
        decision_id: "c1",
        decision_type: "classify",
        decided_by: "S. Chen",
        identity_source: "declared",
        decided_at: "2026-01-01T00:00:00Z",
        rationale: "Initial call.",
        values: { classification: "Control Deficiency", likelihood: "Low", magnitude: "Medium" },
        supersedes: null,
      },
    });
    renderDeficiency(effectiveWith(deficiency));

    expect(screen.getByText(/Classified by S\. Chen/)).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("Your name / ID"), "S. Chen");
    await user.click(screen.getByRole("button", { name: /^classify$/i }));
    const submit = screen.getByRole("button", { name: /record classification/i });
    // Unchanged from the current conclusion of record: no rationale needed.
    await user.click(submit);

    await waitFor(() =>
      expect(mockedApi.sessions.decisions.record).toHaveBeenCalledWith(
        "s1",
        expect.objectContaining({ decision_type: "classify", supersedes: "c1" })
      )
    );
  });
});
