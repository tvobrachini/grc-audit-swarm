# Sample run — exported artifacts

These four files were downloaded from a single real run of the API with
`DEMO_MODE=1`. That means: **fixed, hand-written demo content**, not output
from a language model, and not evidence from a real AWS account. Every
finding and control in these files is labelled `[DEMO DATA]` or `[DEMO DATA —
synthetic evidence]` for the same reason inside the app itself. They exist so
a reader can see the exact shape of the export files without installing or
running anything.

The session went through all three human approval gates before these were
exported, so each file also carries a real (demo) approval trail — including
one **Return for rework** at Gate 1 with reviewer notes, so the trail shows
genuine supervision rather than a rubber stamp, and a Gate 3 approval by a
different person than the Gate 2 approver (segregation of duties). Since
[ADR-011](../../DECISIONS.md#adr-011-reviewer-decisions-as-an-append-only-record-beside-the-ai-drafts),
Gates 2 and 3 also require recorded reviewer decisions — a sign-off on every
finding, a classification and write-up for the deficiency, a scope limitation
for the untested key control, and an engagement conclusion — before they can
be approved, so all four exports show a **conclusion of record** (the
reviewer's decision) alongside the AI's draft wherever the reviewer's call is
what actually got reported, plus a management response recorded after the
report was issued.

## Files

| File | What it is |
| --- | --- |
| `racm.xlsx` | The Risk and Control Matrix from Gate 1 (Planning): risks with likelihood and impact, mapped controls with their attributes (owner, frequency, nature, type, key control, assertions / objectives, IPE), the test-of-design / test-of-operating-effectiveness / substantive-testing steps, and the test design (population, sample size, sampling method, period of reliance). |
| `working-papers.xlsx` | The fieldwork findings from Gate 2: one row per control, with its test-of-design and operating-effectiveness conclusions, the basis for the operating-effectiveness conclusion, items tested, exceptions, result, preliminary-deficiency flag, conclusion, the evidence quote, and whether that quote is verified against the evidence vault (`Quote Verified in Vault`). The sample shows the three cases: a password policy read at one point in time (design effective, operating effectiveness not tested), a control with no evidence tool (not tested), and an exception. Demo evidence is synthetic but is written to the vault under a DEMO DATA label, so this column shows the real verification result ("Yes" for the two tested controls in this sample). |
| `report.md` | The final report from Gate 3: executive summary, detailed findings, the reviewer's engagement conclusion, the deficiency evaluation (conclusion of record, with the AI draft shown alongside where the reviewer departed from it), the per-finding reviewer sign-offs, the recorded scope limitation, the management response, and the full approval trail — including each recorded reviewer decision, not only the three gate approvals. |
| `oscal.json` | The same audit as an [OSCAL](https://pages.nist.gov/OSCAL/) **Assessment Results** document (`oscal-version` 1.2.1). It validates against NIST's official OSCAL 1.2.1 assessment-results JSON schema, which the test suite enforces (`tests/test_oscal_ar.py`), and loads with compliance-trestle 5.1. It maps the RACM control IDs to `reviewed-controls`, each working-paper finding to an `observation` (evidence cited by vault ID), each tested control to a `finding` (satisfied / not-satisfied, with the reviewer sign-off as project-namespaced props), the deficiency evaluation to a `risk` (classification of record plus the AI draft), the engagement conclusion to an `attestation`, and the approval trail — gate approvals and reviewer decisions alike — to the `assessment-log`. There is no OSCAL assessment plan: `import-ap` points to a back-matter entry describing the RACM. See [ADR-005](../../DECISIONS.md#adr-005-oscal-assessment-results-export). |

## How to regenerate these

All four files come from one run of
[`scripts/demo_walkthrough.py`](../../scripts/demo_walkthrough.py), which
drives the real API in process (`DEMO_MODE=1`, no network, no browser): it
creates an audit, returns Gate 1 for rework once with reviewer notes, records
the Gate 2 and Gate 3 reviewer decisions through the API (`swarm.demo.
demo_review_decisions` supplies the example decisions — the application never
records decisions itself), approves every gate with a different declared
identity, records a management response after the report is issued, and
downloads the four exports:

```bash
uv run python scripts/demo_walkthrough.py docs/sample-run
```

These files were checked before committing to confirm they contain no
secrets, tokens, real AWS account IDs, or local file paths — only the fixed
demo content described above.
