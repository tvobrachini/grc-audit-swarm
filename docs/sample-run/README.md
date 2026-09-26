# Sample run — exported artifacts

These four files were downloaded from a real run of the API with `DEMO_MODE=1`.
That means: **fixed, hand-written demo content**, not output from a language
model, and not evidence from a real AWS account. Every finding and control in
these files is labelled `[DEMO DATA]` or `[DEMO DATA — synthetic evidence]`
for the same reason inside the app itself. They exist so a reader can see the
exact shape of the export files without installing or running anything.

The session went through all three human approval gates before these were
exported, so each file also carries a real (demo) approval trail.

## Files

| File | What it is |
| --- | --- |
| `racm.xlsx` | The Risk and Control Matrix from Gate 1 (Planning): risks, mapped controls, and the test-of-design / test-of-operation / substantive-testing steps for each control. |
| `working-papers.xlsx` | The fieldwork findings from Gate 2: one row per control tested, its severity, conclusion, the evidence quote, and whether that quote is verified against the evidence vault (`Quote Verified in Vault`). In demo mode this is always "No" — demo evidence is synthetic and is intentionally never written to the vault, so there is nothing to verify against. |
| `report.md` | The final report from Gate 3: executive summary, detailed findings, and the full approval trail (who approved which gate, and when). |
| `oscal.json` | The same findings expressed as an [OSCAL](https://pages.nist.gov/OSCAL/) Assessment Results document (`observations` keyed to control IDs), for interoperability with GRC tooling that consumes OSCAL. |

## How to regenerate these

1. Start the API in demo mode (see "Try it in 2 minutes" in the main
   [README](../../README.md)):

   ```bash
   API_AUTH_TOKEN=dev-token DEMO_MODE=1 DEMO_STEP_DELAY=0 \
     SESSIONS_PATH=/tmp/grc-demo/s.json EVIDENCE_VAULT_PATH=/tmp/grc-demo/vault \
     PYTHONPATH=src uv run uvicorn api.main:app --port 8000
   ```

2. Create an audit and approve it through all three gates (via the UI, or
   `scripts/capture_screenshots.mjs`, which does this as a side effect while
   taking screenshots).

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
