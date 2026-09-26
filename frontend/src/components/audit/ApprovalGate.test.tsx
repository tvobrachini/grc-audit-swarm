import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError, api } from "@/api/client";
import { renderWithClient, makeSession } from "@/test/testUtils";
import { ApprovalGate } from "./ApprovalGate";

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return {
    ...actual,
    api: {
      sessions: {
        approve: vi.fn(),
        returnForRework: vi.fn(),
      },
    },
  };
});

const mockedApi = vi.mocked(api, { deep: true });

beforeEach(() => {
  mockedApi.sessions.approve.mockReset();
  mockedApi.sessions.returnForRework.mockReset();
});

describe("ApprovalGate", () => {
  it("shows the SoD rule and requires an approver before approving", async () => {
    renderWithClient(<ApprovalGate session={makeSession()} />);
    expect(screen.getByText(/preparer of this audit cannot approve/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approve & proceed/i })).toBeDisabled();
  });

  it("shows the Gate 3 distinct-approver rule", () => {
    renderWithClient(
      <ApprovalGate session={makeSession({ status: "WAITING_HUMAN_GATE_3" })} />
    );
    expect(
      screen.getByText(/must be approved by someone other than the Gate 2 approver/i)
    ).toBeInTheDocument();
  });

  it("disables Return for rework until notes are entered", async () => {
    const user = userEvent.setup();
    renderWithClient(<ApprovalGate session={makeSession()} />);

    await user.type(screen.getByPlaceholderText("Your name / ID"), "M. Alvarez");
    await user.click(screen.getByRole("button", { name: /return for rework/i }));

    const submit = screen.getByRole("button", { name: /^return for rework$/i });
    expect(submit).toBeDisabled();

    await user.type(
      screen.getByPlaceholderText(/what must change/i),
      "Please add a completeness procedure for CTRL-001."
    );
    expect(submit).toBeEnabled();
  });

  it("submits return-for-rework with phase, human_id and notes", async () => {
    const user = userEvent.setup();
    mockedApi.sessions.returnForRework.mockResolvedValue({
      session_id: "s1",
      name: "Test audit",
      status: "RUNNING_PHASE_1",
      phase: 1,
      needs_input: false,
      created_at: "",
      prepared_by: "J. Rivera, IT Auditor",
    });

    renderWithClient(<ApprovalGate session={makeSession()} />);
    await user.type(screen.getByPlaceholderText("Your name / ID"), "M. Alvarez");
    await user.click(screen.getByRole("button", { name: /return for rework/i }));
    await user.type(screen.getByPlaceholderText(/what must change/i), "Fix the sample size.");
    await user.click(screen.getByRole("button", { name: /^return for rework$/i }));

    await waitFor(() =>
      expect(mockedApi.sessions.returnForRework).toHaveBeenCalledWith(
        "s1",
        1,
        "M. Alvarez",
        "Fix the sample size."
      )
    );
  });

  it("shows the 409 message verbatim on a blocked approval", async () => {
    const user = userEvent.setup();
    mockedApi.sessions.approve.mockRejectedValue(
      new ApiError(
        409,
        "Cannot approve gate 1 (status=WAITING_HUMAN_GATE_1): Segregation of duties: " +
          "'J. Rivera' prepared this audit and cannot perform 'gate_approval' on it. " +
          "A different reviewer must act."
      )
    );

    renderWithClient(<ApprovalGate session={makeSession()} />);
    await user.type(screen.getByPlaceholderText("Your name / ID"), "J. Rivera");
    await user.click(screen.getByRole("button", { name: /approve & proceed/i }));

    await waitFor(() =>
      expect(
        screen.getByText(/Segregation of duties: 'J\. Rivera' prepared this audit/)
      ).toBeInTheDocument()
    );
  });
});
