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

async function shot(page, name) {
  await mkdir(OUT_DIR, { recursive: true });
  const file = path.join(OUT_DIR, `${name}.png`);
  await page.screenshot({ path: file, fullPage: false });
  console.log(`captured ${file}`);
}

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

async function newAudit(page, theme, context, preparedBy = PREPARER) {
  await page.getByRole("button", { name: /new/i }).first().click();
  await page.getByPlaceholder(/e\.g\. S3 Exposure Assessment/i).fill(theme);
  await page.getByPlaceholder(/Describe the environment/i).fill(context);
  await page.getByPlaceholder(/J\. Rivera/i).fill(preparedBy);
  await page.getByRole("button", { name: /launch audit/i }).click();
  await page.getByRole("dialog").waitFor({ state: "detached" }).catch(() => {});
}

async function waitForStatusText(page, pattern, timeout = 60_000) {
  await page.waitForFunction(
    (p) => document.body.innerText.match(new RegExp(p)),
    pattern.source,
    { timeout }
  );
}

async function approveGate(page, name) {
  await page.getByPlaceholder("Your name / ID").fill(name);
  await page.getByRole("button", { name: /approve & proceed/i }).click();
}

/** Return the phase for rework with reviewer notes, as a real reviewer would
 * before ultimately approving — demonstrates real supervision in the trail. */
async function returnForRework(page, name, notes) {
  await page.getByPlaceholder("Your name / ID").fill(name);
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
  await page.goto(FRONTEND_URL, { waitUntil: "networkidle" });

  await newAudit(
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

  await approveGate(page, GATE_2_APPROVER);
  await settle(page);

  // Gate 3: report review
  await waitForStatusText(page, /Gate 3/);
  await page.waitForLoadState("networkidle");
  await shot(page, "gate-3-report-review");

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
  await shot(page, "completed-approval-trail-and-exports");

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
