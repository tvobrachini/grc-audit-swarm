import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, setReviewerToken } from "./client";

function okResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("reviewer token header", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(okResponse({}));
    vi.stubGlobal("fetch", fetchMock);
    setReviewerToken("");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setReviewerToken("");
  });

  it("is omitted from a decision request when no token is set", async () => {
    await api.sessions.decisions.record("s1", {
      decision_type: "sign_off",
      subject_id: "CTRL-001",
      decided_by: "M. Alvarez",
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Reviewer-Token"]).toBeUndefined();
  });

  it("is sent on a decision request once the reviewer has a token", async () => {
    setReviewerToken("secret-token");

    await api.sessions.decisions.record("s1", {
      decision_type: "sign_off",
      subject_id: "CTRL-001",
      decided_by: "M. Alvarez",
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Reviewer-Token"]).toBe("secret-token");
  });

  it("is sent on a gate-approval request once the reviewer has a token", async () => {
    setReviewerToken("secret-token");

    await api.sessions.approve("s1", 2, "M. Alvarez");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Reviewer-Token"]).toBe("secret-token");
  });

  it("is sent when creating an audit once the reviewer has a token", async () => {
    setReviewerToken("secret-token");

    await api.sessions.create({
      theme: "IAM",
      business_context: "ctx",
      frameworks: [],
      prepared_by: "J. Rivera",
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Reviewer-Token"]).toBe("secret-token");
  });

  it("stores the token in sessionStorage only, never localStorage", () => {
    setReviewerToken("secret-token");
    expect(sessionStorage.getItem("grc.reviewerToken")).toBe("secret-token");
    expect(localStorage.getItem("grc.reviewerToken")).toBeNull();
  });
});
