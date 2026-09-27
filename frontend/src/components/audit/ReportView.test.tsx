import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithClient, makeSession } from "@/test/testUtils";
import { ReportView } from "./ReportView";

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return {
    ...actual,
    api: { ...actual.api, evidence: { verify: vi.fn() } },
  };
});

describe("ReportView", () => {
  it("renders the deficiency evaluation section as a draft for Gate 3", () => {
    const session = makeSession({
      status: "WAITING_HUMAN_GATE_3",
      phase: 3,
      final_report: {
        executive_summary: "Overall the control environment is reasonably designed.",
        detailed_report: "Details.",
        compliance_tone_approved: true,
        deficiency_scale: "ICFR deficiency scale",
        deficiency_evaluations: [
          {
            deficiency_id: "DEF-01",
            title: "IAM access review not evidenced quarterly",
            related_findings: ["CTRL-004"],
            related_risks: ["RISK-002"],
            compensating_controls: "None identified.",
            likelihood: "Medium",
            magnitude: "High",
            classification: "Significant Deficiency",
            rationale: "One quarter of the review was not performed.",
          },
        ],
      },
    });

    renderWithClient(<ReportView session={session} />);

    expect(screen.getByText(/Deficiency evaluation \(draft for Gate 3\)/)).toBeInTheDocument();
    expect(screen.getByText("DEF-01")).toBeInTheDocument();
    expect(screen.getByText(/IAM access review not evidenced quarterly/)).toBeInTheDocument();
    expect(screen.getByText("Significant Deficiency")).toBeInTheDocument();
    expect(screen.getByText(/One quarter of the review was not performed/)).toBeInTheDocument();
    expect(screen.getByText(/auditor's judgement at Gate/)).toBeInTheDocument();
  });

  it("renders nothing about deficiencies when there are none", () => {
    const session = makeSession({
      status: "WAITING_HUMAN_GATE_3",
      phase: 3,
      final_report: {
        executive_summary: "Summary.",
        detailed_report: "",
        compliance_tone_approved: true,
        deficiency_evaluations: [],
      },
    });
    renderWithClient(<ReportView session={session} />);
    expect(screen.queryByText(/Deficiency evaluation/)).not.toBeInTheDocument();
  });
});
