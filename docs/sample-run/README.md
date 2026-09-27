# Sample run — exported artifacts

These four files were downloaded from a real run of the API with `DEMO_MODE=1`.
That means: **fixed, hand-written demo content**, not output from a language
model, and not evidence from a real AWS account. Every finding and control in
these files is labelled `[DEMO DATA]` or `[DEMO DATA — synthetic evidence]`
for the same reason inside the app itself. They exist so a reader can see the
exact shape of the export files without installing or running anything.

The session went through all three human approval gates before these were
exported, so each file also carries a real (demo) approval trail — including
one **Return for rework** at Gate 1 with reviewer notes, so the trail shows
genuine supervision rather than a rubber stamp, and a Gate 3 approval by a
different person than the Gate 2 approver (segregation of duties).

## Files

| File | What it is |
| --- | --- |
| `racm.xlsx` | The Risk and Control Matrix from Gate 1 (Planning): risks with likelihood and impact, mapped controls with their attributes (owner, frequency, nature, type, key control, assertions / objectives, IPE), the test-of-design / test-of-operating-effectiveness / substantive-testing steps, and the test design (population, sample size, sampling method, period of reliance). |
| `working-papers.xlsx` | The fieldwork findings from Gate 2: one row per control, with its test-of-design and operating-effectiveness conclusions, the basis for the operating-effectiveness conclusion, items tested, exceptions, result, preliminary-deficiency flag, conclusion, the evidence quote, and whether that quote is verified against the evidence vault (`Quote Verified in Vault`). The sample shows the three cases: a password policy read at one point in time (design effective, operating effectiveness not tested), a control with no evidence tool (not tested), and an exception. Demo evidence is synthetic but is written to the vault under a DEMO DATA label, so this column shows the real verification result ("Yes" for the two tested controls in this sample). |
| `report.md` | The final report from Gate 3: executive summary, detailed findings, the proposed engagement-level deficiency evaluation, and the full approval trail (who approved which gate, and when). |
| `oscal.json` | The same findings in the project's OSCAL-inspired structure, modelled on [OSCAL](https://pages.nist.gov/OSCAL/) Assessment Results (`observations` keyed to control IDs and vault references). It is not validated against the official OSCAL schema and may need conversion before an OSCAL tool will load it. |

## How to regenerate these

1. Start the API in demo mode (see "Try it in 2 minutes" in the main
   [README](../../README.md)):

   ```bash
   API_AUTH_TOKEN=dev-token DEMO_MODE=1 DEMO_STEP_DELAY=0 \
     SESSIONS_PATH=/tmp/grc-demo/s.json EVIDENCE_VAULT_PATH=/tmp/grc-demo/vault \
     TRAIL_ANCHORS_PATH=/tmp/grc-demo/anchors.json \
     PYTHONPATH=src uv run uvicorn api.main:app --port 8000
   ```

2. Create an audit (with a "Prepared by" identity) and approve it through
   all three gates — using a different declared identity for each of the
   preparer, the Gate 1/2 approver and the Gate 3 approver, and returning at
   least one gate for rework with notes — via the UI, or
   `scripts/capture_screenshots.mjs`, which does this as a side effect while
   taking screenshots.

3. Download each export (replace `<session-id>` and `<token>`):

   ```bash
   curl -H "Authorization: Bearer <token>" \
     http://localhost:8000/api/sessions/<session-id>/export/racm.xlsx -o racm.xlsx
   curl -H "Authorization: Bearer <token>" \
     http://localhost:8000/api/sessions/<session-id>/export/working-papers.xlsx -o working-papers.xlsx
   curl -H "Authorization: Bearer <token>" \
     http://localhost:8000/api/sessions/<session-id>/export/report.md -o report.md
   curl -H "Authorization: Bearer <token>" \
     http://localhost:8000/api/sessions/<session-id>/export/oscal.json -o oscal.json
   ```

These files were checked before committing to confirm they contain no
secrets, tokens, real AWS account IDs, or local file paths — only the fixed
demo content described above.
