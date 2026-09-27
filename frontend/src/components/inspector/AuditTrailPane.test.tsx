import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { render } from "@testing-library/react";
import { makeSession } from "@/test/testUtils";
import { AuditTrailPane } from "./AuditTrailPane";
import type { TrailVerification } from "@/api/client";

function verification(overrides: Partial<TrailVerification>): TrailVerification {
  return {
    ok: false,
    status: "ok",
    entries: 1,
    legacy_entries: 0,
    first_broken_index: null,
    keyed: false,
    anchored: false,
    head_hash: null,
    changed_since_approval: [],
    detail: "",
    ...overrides,
  };
}

describe("AuditTrailPane", () => {
  it("renders an 'ok' badge with an honest tooltip", () => {
    const session = makeSession({
      trail_verification: verification({ ok: true, status: "ok", detail: "Chain intact." }),
    });
    render(<AuditTrailPane session={session} />);
    expect(screen.getByText("Trail intact")).toBeInTheDocument();
    expect(screen.getByText(/does not identify who acted/i)).toBeInTheDocument();
  });

  it("renders a 'legacy' badge", () => {
    const session = makeSession({
      trail_verification: verification({ status: "legacy_unchained", legacy_entries: 3 }),
    });
    render(<AuditTrailPane session={session} />);
    expect(screen.getByText("Legacy trail (unchained)")).toBeInTheDocument();
  });

  it("renders a 'broken at index N' badge", () => {
    const session = makeSession({
      trail_verification: verification({ status: "broken", first_broken_index: 2 }),
    });
    render(<AuditTrailPane session={session} />);
    expect(screen.getByText("Trail broken")).toBeInTheDocument();
    expect(screen.getByText(/First problem at entry 2/)).toBeInTheDocument();
  });

  it("renders a 'truncated' badge", () => {
    const session = makeSession({
      trail_verification: verification({ status: "truncated" }),
    });
    render(<AuditTrailPane session={session} />);
    expect(screen.getByText("Trail truncated")).toBeInTheDocument();
  });

  it("renders an 'artifact changed' badge naming the affected gate", () => {
    const session = makeSession({
      trail_verification: verification({
        status: "artifact_changed",
        changed_since_approval: ["Gate 2 (Fieldwork)"],
      }),
    });
    render(<AuditTrailPane session={session} />);
    expect(screen.getByText("Artifact changed after approval")).toBeInTheDocument();
    expect(screen.getByText(/Gate 2 \(Fieldwork\)/)).toBeInTheDocument();
  });

  it("renders return_for_rework entries with notes", () => {
    const session = makeSession({
      approval_trail: [
        {
          gate: "Return for rework (Planning)",
          human: "M. Alvarez, IT Audit Manager",
          timestamp: "2026-01-01T00:00:00",
          action: "return_for_rework",
          notes: "Add a completeness procedure for the population.",
        },
      ],
    });
    render(<AuditTrailPane session={session} />);
    expect(screen.getByText(/Returned for rework by/)).toBeInTheDocument();
    expect(
      screen.getByText(/Add a completeness procedure for the population/)
    ).toBeInTheDocument();
  });
});
