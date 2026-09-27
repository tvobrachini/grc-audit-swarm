#!/usr/bin/env node
/**
 * Regenerate the screenshots in docs/screenshots/ from a live demo run.
 *
 * This script does not start the app itself. Start both of these first,
 * in separate terminals, from the repository root:
 *
 *   # Terminal 1: the API in demo mode
 *   API_AUTH_TOKEN=dev-token DEMO_MODE=1 DEMO_STEP_DELAY=0 \
 *     SESSIONS_PATH=/tmp/grc-demo/s.json EVIDENCE_VAULT_PATH=/tmp/grc-demo/vault \
 *     PYTHONPATH=src uv run uvicorn api.main:app --port 8000
 *
 *   # Terminal 2: the React dev server (proxies /api to :8000)
 *   cd frontend && npm ci && VITE_API_AUTH_TOKEN=dev-token npm run dev
 *
 * Then, from the repository root, capture the five "happy path" shots:
 *
 *   node scripts/capture_screenshots.mjs
 *
 * The sixth shot (QA rejection / retry / override) needs the API restarted
 * with DEMO_QA_REJECT_PHASE=2 set (stop terminal 1, re-run the same command
 * with that extra env var, leave terminal 2 as it is), then:
 *
 *   RUN=qa-reject node scripts/capture_screenshots.mjs
 *
 * Environment overrides:
 *   FRONTEND_URL   default http://localhost:5173 (match the port `npm run dev` prints)
 *   OUT_DIR        default docs/screenshots
 *   RUN            "normal" (default) or "qa-reject"
 *
 * Uses Chromium at /opt/pw-browsers/chromium (do not run `playwright
 * install`). All shots use a 1440x900 viewport, deviceScaleFactor 1, and
 * wait for network idle plus a visible selector rather than a fixed sleep.
 */
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const FRONTEND_URL = process.env.FRONTEND_URL || "http://localhost:5173";
const OUT_DIR = process.env.OUT_DIR || "docs/screenshots";
const CHROMIUM_PATH = process.env.CHROMIUM_PATH || "/opt/pw-browsers/chromium";
const RUN = process.env.RUN || "normal";

/** MiddlePanel's content area (and the findings list within it) keep whatever
 * scrollTop a previous gate's interactions left them at — the same
 * overflow-y-auto div is reused across phases instead of being remounted, so
 * without this a later gate's shot can open already scrolled past its own
 * top (e.g. straight to the approval trail, skipping the report and its
 * decision controls). Reset every auto-scrolling container before each
 * capture. */
async function resetScroll(page) {
  await page.evaluate(() => {
    document.querySelectorAll("*").forEach((el) => {
      if (getComputedStyle(el).overflowY === "auto") el.scrollTop = 0;
    });
  });
}

const DEFAULT_VIEWPORT = { width: 1440, height: 900 };

async function shot(page, name, viewport = DEFAULT_VIEWPORT) {
  await mkdir(OUT_DIR, { recursive: true });
  if (viewport !== DEFAULT_VIEWPORT) await page.setViewportSize(viewport);
  await resetScroll(page);
  const file = path.join(OUT_DIR, `${name}.png`);
  await page.screenshot({ path: file, fullPage: false });
  console.log(`captured ${file}`);
  if (viewport !== DEFAULT_VIEWPORT) await page.setViewportSize(DEFAULT_VIEWPORT);
}

/** ReportView (Gate 3 review and the completed screen) lays out the report,
 * the approval trail and the approval/decision panel as flex siblings where
 * only the report box scrolls internally (`overflow-y-auto`); per the
 * flexbox spec that gives it — alone among the three — an automatic minimum
 * size of 0, so once ADR-011's longer trail (a recorded entry per reviewer
 * decision, not just per gate) and the decision forms don't all fit in a
 * 900px-tall viewport, the report box (and every decision control inside it)
 * is squeezed away to zero height instead of scrolling: real content, zero
 * visible pixels. A short viewport is exactly what a docs screenshot doesn't
 * need, so these two shots use a taller one instead of chasing the layout. */
const REPORT_SHOT_VIEWPORT = { width: 1440, height: 2400 };

// Real people, real supervision: these are the declared identities used
// throughout the recaptured demo run (see runNormal below).
const PREPARER = "J. Rivera, IT Auditor";
const GATE_1_APPROVER = "M. Alvarez, IT Audit Manager";
const GATE_2_APPROVER = "M. Alvarez, IT Audit Manager";
const GATE_3_APPROVER = "S. Chen, Audit Director"; // must differ from the Gate 2 approver

/** Pause between recorded actions so approval-trail timestamps are seconds
 * apart, not milliseconds — a screenshot of the trail should read as a real
 * review, not a scripted burst. */
async function settle(page, ms = 2000) {
  await page.waitForTimeout(ms);
}

/** Creates the audit through the UI and returns its session_id (read off the
 * POST /api/sessions response — the app has no session-id route to read it
 * from the URL). */
async function newAudit(page, theme, context, preparedBy = PREPARER) {
  const created = page.waitForResponse(
    (res) => res.request().method() === "POST" && new URL(res.url()).pathname === "/api/sessions"
  );
  await page.getByRole("button", { name: /new/i }).first().click();
  await page.getByPlaceholder(/e\.g\. S3 Exposure Assessment/i).fill(theme);
  await page.getByPlaceholder(/Describe the environment/i).fill(context);
  await page.getByPlaceholder(/J\. Rivera/i).fill(preparedBy);
  await page.getByRole("button", { name: /launch audit/i }).click();
  const { session_id: sessionId } = await (await created).json();
  await page.getByRole("dialog").waitFor({ state: "detached" }).catch(() => {});
  return sessionId;
}

/** The Authorization header the running frontend build sends to the API
 * (baked in from VITE_API_AUTH_TOKEN, or absent in the plain dev setup where
 * nothing is required) — captured off a real app request so this script's
 * own API calls (posting reviewer decisions) are authorized the same way. */
function captureAuthHeader(page) {
  let header;
  const listener = (req) => {
    if (header) return;
    const auth = req.headers()["authorization"];
    if (auth && new URL(req.url()).pathname.startsWith("/api/")) header = auth;
  };
  page.on("request", listener);
  return {
    stop: () => page.off("request", listener),
    get: () => header,
  };
}

async function apiFetch(page, authHeader, path, options = {}) {
  const res = await page.request.fetch(`${FRONTEND_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(authHeader ? { Authorization: authHeader } : {}),
    },
    ...options,
  });
  if (!res.ok()) {
    throw new Error(`${options.method ?? "GET"} ${path} -> ${res.status()}: ${await res.text()}`);
  }
  return res.json();
}

/** Records, through the real API (acting as the reviewer, same as
 * scripts/demo_walkthrough.py), the minimum reviewer decisions Gate 2 needs:
 * a sign-off on every finding the effective view says still needs one (see
 * DECISIONS.md ADR-011). An API-created audit cannot clear Gate 2 without
 * these. The wording here is generic — this script only needs *a* screenshot
 * of a passable audit, not the demo walk-through's exact narrative. */
async function recordGate2Decisions(page, authHeader, sessionId, decidedBy) {
  const detail = await apiFetch(page, authHeader, `/api/sessions/${sessionId}`);
  const effective = detail.effective;
  if (!effective) return; // pre-ADR-011 session: no decisions required
  for (const f of effective.findings) {
    if (!f.review_required_for_gate_2 || f.review) continue;
    await apiFetch(page, authHeader, `/api/sessions/${sessionId}/decisions`, {
      method: "POST",
      data: {
        decision_type: "sign_off",
        subject_id: f.control_id,
        decided_by: decidedBy,
        rationale: "Agreed with the AI draft's conclusion.",
      },
    });
  }
}

/** Same idea for Gate 3: a classification for every deficiency, a scope
 * limitation for every untested key control, and the engagement conclusion. */
async function recordGate3Decisions(page, authHeader, sessionId, decidedBy) {
  const detail = await apiFetch(page, authHeader, `/api/sessions/${sessionId}`);
  const effective = detail.effective;
  if (!effective) return;
  for (const d of effective.deficiencies) {
    if (d.classification_decision) continue;
    await apiFetch(page, authHeader, `/api/sessions/${sessionId}/decisions`, {
      method: "POST",
      data: {
        decision_type: "classify",
        subject_id: d.deficiency_id,
        decided_by: decidedBy,
        values: { ...d.draft },
        rationale: "Agreed with the AI draft's classification.",
      },
    });
  }
  for (const f of effective.findings) {
    if (!f.key_control || f.effective.result !== "Not tested" || f.scope_limitation) continue;
    await apiFetch(page, authHeader, `/api/sessions/${sessionId}/decisions`, {
      method: "POST",
      data: {
        decision_type: "scope_limitation",
        subject_id: f.control_id,
        decided_by: decidedBy,
        rationale: `${f.control_id} was not tested; reported as a scope limitation.`,
      },
    });
  }
  if (!effective.engagement_conclusion) {
    await apiFetch(page, authHeader, `/api/sessions/${sessionId}/decisions`, {
      method: "POST",
      data: {
        decision_type: "engagement_conclusion",
        subject_id: "engagement",
        decided_by: decidedBy,
        values: { conclusion: "Needs improvement" },
        rationale: "Recorded for the screenshot walk-through.",
      },
    });
  }
}

async function waitForStatusText(page, pattern, timeout = 60_000) {
  await page.waitForFunction(
    (p) => document.body.innerText.match(new RegExp(p)),
    pattern.source,
    { timeout }
  );
}

/** Waits for ApprovalGate's "N decision(s) still needed" checklist to clear
 * once the recorded decisions have been picked up by the session poll
 * (useAuditDetail refetches every 3s) — confirms the gate is now approvable
 * before clicking Approve. */
async function waitForChecklistClear(page, timeout = 15_000) {
  await page
    .getByText(/decisions? still needed before this gate can be approved/i)
    .waitFor({ state: "detached", timeout })
    .catch(() => {});
}

/** The approval gate's own "Your name / ID" field, disambiguated from the
 * identical placeholder on each per-finding / per-deficiency decision form
 * (ReviewerFields) that now stays on screen alongside it once Gate 2/3 cards
 * are expanded. */
function approverNameInput(page) {
  return page
    .locator("label", { hasText: /Approver name \/ ID/i })
    .locator("xpath=following-sibling::div[1]")
    .getByPlaceholder("Your name / ID");
}

async function approveGate(page, name) {
  await approverNameInput(page).fill(name);
  await page.getByRole("button", { name: /approve & proceed/i }).click();
}

/** Return the phase for rework with reviewer notes, as a real reviewer would
 * before ultimately approving — demonstrates real supervision in the trail. */
async function returnForRework(page, name, notes) {
  await approverNameInput(page).fill(name);
  await page.getByRole("button", { name: /return for rework/i }).click();
  await page.getByPlaceholder(/what must change/i).fill(notes);
}

async function submitReturnForRework(page) {
  await page.getByRole("button", { name: /return for rework/i }).click();
}

async function runNormal(browser) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });
  const page = await ctx.newPage();
  const auth = captureAuthHeader(page);
  await page.goto(FRONTEND_URL, { waitUntil: "networkidle" });

  const sessionId = await newAudit(
    page,
    "AWS IAM and S3 access controls",
    "Annual IT general controls review of IAM policy hygiene and S3 bucket " +
      "access controls for a mid-size SaaS company's production AWS account."
  );

  // Gate 1: planning review (RACM)
  await waitForStatusText(page, /Gate 1/);
  await page.waitForLoadState("networkidle");
  await shot(page, "gate-1-planning-review-racm");

  // Expand the first control to show its attributes and test design.
  await page
    .locator("button")
    .filter({ hasText: /^CTRL-/ })
    .first()
    .click();
  await page.waitForTimeout(300);
  await shot(page, "racm-control-attributes");

  // A real reviewer returns Gate 1 for rework once, with notes, before
  // approving — the trail should show real supervision, not a rubber stamp.
  await returnForRework(
    page,
    GATE_1_APPROVER,
    "Please add a completeness procedure for the IAM credential report " +
      "population before I can approve the RACM."
  );
  await shot(page, "return-for-rework");
  await submitReturnForRework(page);
  await settle(page);

  // Planning re-runs; wait for Gate 1 again, then approve for real.
  await waitForStatusText(page, /Gate 1/);
  await page.waitForLoadState("networkidle");
  await approveGate(page, GATE_1_APPROVER);
  await settle(page);

  // Gate 2: fieldwork review (findings board + vault verification badges)
  await waitForStatusText(page, /Gate 2/);
  await page.waitForLoadState("networkidle");
  await page
    .getByText(/quote verified in vault|quote not verified/i)
    .first()
    .waitFor({ timeout: 15_000 })
    .catch(() => {});
  await shot(page, "gate-2-findings-board-vault-verification");

  // An API-created audit needs reviewer decisions before Gate 2 can be
  // approved (DECISIONS.md ADR-011); record them via the API. The session
  // query polls every 3s (see useAuditDetail), so the UI picks the sign-offs
  // up on its own — a full page.reload() would instead drop the app's
  // in-memory selected-session state (App.tsx has no session-id route) and
  // bounce back to the empty "select an audit" screen. Capture a shot of the
  // findings board showing the sign-offs and the missing-decisions checklist
  // now satisfied, then approve.
  await recordGate2Decisions(page, auth.get(), sessionId, GATE_2_APPROVER);
  await page.getByText(/signed off by/i).first().waitFor({ timeout: 15_000 });
  await waitForChecklistClear(page);
  await approveGate(page, GATE_2_APPROVER);
  await settle(page);

  // Gate 3: report review
  await waitForStatusText(page, /Gate 3/);
  await page.waitForLoadState("networkidle");
  await shot(page, "gate-3-report-review", REPORT_SHOT_VIEWPORT);

  // Same for Gate 3: classifications, any scope limitation and the
  // engagement conclusion. Same reasoning as Gate 2: let the poll pick the
  // decisions up instead of reloading the page.
  await recordGate3Decisions(page, auth.get(), sessionId, GATE_3_APPROVER);
  await page.getByText(/classified by/i).first().waitFor({ timeout: 15_000 });
  await waitForChecklistClear(page);
  await approveGate(page, GATE_3_APPROVER);
  await settle(page);

  // Completed: approval trail (with the verification badge) + export buttons
  await waitForStatusText(page, /Audit Complete/);
  await page.waitForLoadState("networkidle");
  await page.getByText("Export").first().waitFor();
  await page
    .getByText(/trail intact|legacy trail/i)
    .first()
    .waitFor({ timeout: 15_000 })
    .catch(() => {});
  await shot(page, "completed-approval-trail-and-exports", REPORT_SHOT_VIEWPORT);

  auth.stop();
  await ctx.close();
}

async function runQaReject(browser) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });
  const page = await ctx.newPage();
  await page.goto(FRONTEND_URL, { waitUntil: "networkidle" });

  await newAudit(
    page,
    "S3 bucket public access review",
    "Follow-up review of S3 bucket policies and Block Public Access " +
      "settings after a prior finding."
  );

  await waitForStatusText(page, /Gate 1/);
  await approveGate(page, "M. Alvarez, IT Audit Manager");

  // Phase 2 QA rejects; the retry/override panel appears instead of Gate 2.
  await waitForStatusText(page, /rejected by the QA reviewer/);
  await page.waitForLoadState("networkidle");
  await shot(page, "qa-rejection-retry-and-override");

  await ctx.close();
}

async function main() {
  const browser = await chromium.launch({ executablePath: CHROMIUM_PATH });
  if (RUN === "qa-reject") {
    await runQaReject(browser);
  } else {
    await runNormal(browser);
  }
  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
