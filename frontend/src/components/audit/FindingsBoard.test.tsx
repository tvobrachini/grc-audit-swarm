import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient, makeSession } from "@/test/testUtils";
import { FindingsBoard } from "./FindingsBoard";

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      evidence: { verify: vi.fn().mockResolvedValue({ vault_id: "v1", verified: true }) },
    },
  };
});

function sessionWithFindings(findings: unknown[]) {
  return makeSession({
    status: "WAITING_HUMAN_GATE_2",
    phase: 2,
    working_papers: { theme: "IAM review", findings },
  });
}

describe("FindingsBoard", () => {
  it("renders ToD/ToE conclusions, items tested and exceptions", async () => {
    const user = userEvent.setup();
    const finding = {
      control_id: "CTRL-001",
      vault_id_reference: "vault-1",
      exact_quote_from_evidence: "MFA is enforced for all IAM users.",
      tod_conclusion: "Effective",
      toe_conclusion: "Effective",
      toe_basis: "Full population inspected for the period.",
      items_tested: 25,
      exceptions_noted: 0,
      result: "No exception",
      preliminary_deficiency: false,
      test_conclusion: "MFA enforcement confirmed for the full sample.",
    };
    renderWithClient(<FindingsBoard session={sessionWithFindings([finding])} />);

    // Collapsed by default for a "No exception" finding; open it.
    await user.click(screen.getByText("CTRL-001"));

    expect(screen.getAllByText("Effective", { selector: "p" }).length).toBe(2);
    expect(screen.getByText(/Full population inspected for the period/)).toBeInTheDocument();
    expect(screen.getByText("25")).toBeInTheDocument();
    expect(screen.getAllByText("0").length).toBeGreaterThan(0);
  });

  it("renders a 'Not tested' finding without a vault reference sensibly", () => {
    const finding = {
      control_id: "CTRL-002",
      vault_id_reference: "",
      exact_quote_from_evidence: "",
      tod_conclusion: "Not tested",
      toe_conclusion: "Not tested",
      result: "Not tested",
      preliminary_deficiency: false,
      test_conclusion: "Out of scope for this cycle.",
    };
    // "Not tested" findings render open by default (only "No exception" ones
    // start collapsed), so no click is needed to see the body.
    renderWithClient(<FindingsBoard session={sessionWithFindings([finding])} />);

    expect(screen.getByText(/No vault reference — control was not tested/)).toBeInTheDocument();
    expect(screen.queryByText(/Vault ID:/)).not.toBeInTheDocument();
  });

  it("labels legacy severity as 'legacy rating' for old sessions", () => {
    const finding = {
      control_id: "CTRL-003",
      vault_id_reference: "vault-3",
      exact_quote_from_evidence: "quote",
      tod_conclusion: "Ineffective",
      toe_conclusion: "Ineffective",
      result: "Exception",
      preliminary_deficiency: true,
      test_conclusion: "Migrated from a pre-split session.",
      legacy_severity: "Significant Deficiency",
    };
    renderWithClient(<FindingsBoard session={sessionWithFindings([finding])} />);

    expect(screen.getByText(/legacy rating: Significant Deficiency/)).toBeInTheDocument();
    expect(screen.queryByText(/^severity$/i)).not.toBeInTheDocument();
  });

  it("summarises counts by result", () => {
    const findings = [
      { control_id: "A", tod_conclusion: "Effective", toe_conclusion: "Effective", result: "No exception", test_conclusion: "x" },
      { control_id: "B", tod_conclusion: "Ineffective", toe_conclusion: "Ineffective", result: "Exception", preliminary_deficiency: true, test_conclusion: "y" },
      { control_id: "C", tod_conclusion: "Not tested", toe_conclusion: "Not tested", result: "Not tested", test_conclusion: "z" },
    ];
    renderWithClient(<FindingsBoard session={sessionWithFindings(findings)} />);

    expect(screen.getByText(/1 × No exception/)).toBeInTheDocument();
    expect(screen.getByText(/1 × Exception/)).toBeInTheDocument();
    expect(screen.getByText(/1 × Not tested/)).toBeInTheDocument();
  });
});
