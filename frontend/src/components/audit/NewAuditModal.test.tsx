import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api } from "@/api/client";
import { renderWithClient } from "@/test/testUtils";
import { NewAuditModal } from "./NewAuditModal";

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      sessions: { ...actual.api.sessions, create: vi.fn(), createWithDocument: vi.fn() },
    },
  };
});

const mockedApi = vi.mocked(api, { deep: true });

beforeEach(() => {
  mockedApi.sessions.create.mockReset();
});

describe("NewAuditModal", () => {
  it("keeps Launch Audit disabled until prepared_by is filled in", async () => {
    const user = userEvent.setup();
    renderWithClient(<NewAuditModal onClose={vi.fn()} onCreated={vi.fn()} />);

    await user.type(screen.getByPlaceholderText(/S3 Exposure Assessment/i), "IAM review");
    await user.type(
      screen.getByPlaceholderText(/Describe the environment/i),
      "Production AWS account."
    );

    const launch = screen.getByRole("button", { name: /launch audit/i });
    expect(launch).toBeDisabled();

    await user.type(screen.getByPlaceholderText(/J\. Rivera/i), "J. Rivera, IT Auditor");
    expect(launch).toBeEnabled();
  });

  it("explains that the preparer cannot approve gates", () => {
    renderWithClient(<NewAuditModal onClose={vi.fn()} onCreated={vi.fn()} />);
    expect(
      screen.getByText(/preparer cannot approve gates|preparer cannot approve/i)
    ).toBeInTheDocument();
  });

  it("sends prepared_by and control-framework defaults on create", async () => {
    const user = userEvent.setup();
    mockedApi.sessions.create.mockResolvedValue({
      session_id: "s1",
      name: "IAM review audit",
      status: "RUNNING_PHASE_1",
      phase: 1,
      needs_input: false,
      created_at: "",
      prepared_by: "J. Rivera, IT Auditor",
    });

    renderWithClient(<NewAuditModal onClose={vi.fn()} onCreated={vi.fn()} />);
    await user.type(screen.getByPlaceholderText(/S3 Exposure Assessment/i), "IAM review");
    await user.type(
      screen.getByPlaceholderText(/Describe the environment/i),
      "Production AWS account."
    );
    await user.type(screen.getByPlaceholderText(/J\. Rivera/i), "J. Rivera, IT Auditor");
    await user.click(screen.getByRole("button", { name: /launch audit/i }));

    expect(mockedApi.sessions.create).toHaveBeenCalledWith(
      expect.objectContaining({
        prepared_by: "J. Rivera, IT Auditor",
        frameworks: ["COSO 2013", "NIST SP 800-53", "CIS Controls"],
      })
    );
  });
});
