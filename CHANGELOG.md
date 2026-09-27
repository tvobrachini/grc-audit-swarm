# Changelog

This file summarises what GRC Audit Swarm does at each tagged version. It is not a line-by-line commit log; the full history is in `git log`.

## Unreleased

**Reviewer decisions (ADR-011)**
- At Gates 2 and 3 a reviewer records decisions beside the AI draft: sign-off or challenge per finding, a scope limitation for untested controls, a classification, five-part write-up and management response per deficiency, and an engagement conclusion. Gates 2 and 3 refuse approval until the required decisions exist, and list what is missing.
- Each decision is appended to the approval trail; Gate 2 and 3 approvals also record a digest of that phase's decisions, so a decision edited, removed or added afterwards is reported by trail verification.
- Exports (report, working papers, OSCAL) show the reviewer's conclusion of record, with the AI draft beside it where they differ.
- The UI has decision controls on the findings board and the report view, and a checklist of missing decisions at each gate.

**Per-reviewer tokens (ADR-012)**
- Optional `REVIEWER_TOKENS_FILE`: when set, creating an audit, gate actions and decisions need a personal `X-Reviewer-Token`, the token's owner is recorded as the person acting, and trail entries record `identity_source` (declared or authenticated). Only hashes are stored; a CLI issues tokens. Not SSO and no MFA — see docs/SECURITY-AND-DATA.md.

**Evidence imports**
- Read-only import of Prowler JSON (OCSF and legacy) and AWS Security Hub (ASFF) findings as vault evidence, labelled as point-in-time results rather than operating effectiveness (docs/INTEGRATIONS.md).

**Evaluation**
- Two answer-key setups that scored correct answers as wrong were fixed, and the key now rejects unknown fields. The key remains an unsigned draft.
- The harness's automated reviewer records the decisions each gate now requires; offline replay metrics are unchanged.

## v0.1.0 — 2026-09-27

First tagged version. Summary of what the project does at this point:

**Audit workflow and review mechanics**
- Three-phase flow (Planning, Fieldwork, Reporting) run as CrewAI crews, each ending with an automatic QA reviewer and a human gate (`src/swarm/audit_flow.py`, `src/swarm/state/machine.py`).
- QA reviewers run at temperature 0 and fail closed: a rejected or unparsable output triggers one automatic retry with the rejection reason, and a second rejection stops the phase in `QA_REJECTED_PHASE_n`.
- At each gate a reviewer can approve, return the phase for rework with required notes, retry a rejected or failed phase, or approve despite a QA rejection with a written override reason (`src/swarm/review_policy.py`).
- Segregation of duties: the preparer of an audit cannot approve a gate, return work or override a QA rejection on it; the Gate 3 approver must differ from the Gate 2 approver.
- Hash-chained approval trail: every action (creation, approval, return, retry, override) is appended with a reviewer name, UTC timestamp and a SHA-256 digest of the artifact acted on, each entry chained to the one before it. `GET /api/sessions/{id}/trail/verify` recomputes the chain and reports whether it is intact; a separate anchor file (`TRAIL_ANCHORS_PATH`) also records the entry count and last hash to help detect entries cut from the end.
- Deleting an audit (`DELETE /api/sessions/{id}`) only works on drafts; anything past the first approved gate is kept.
- Generation provenance: each phase run records the provider/model, QA model, temperatures, CrewAI/app versions, a prompt fingerprint and outcome (`generation_runs` on `GET /api/sessions/{id}`), and gate approvals, retries, returns and overrides reference the run they acted on; this does not make LLM output replayable (see DECISIONS.md ADR-010).

**Audit methodology model**
- RACM schema with risks (likelihood/impact), controls (owner, frequency, nature, type, key-control flag, assertions/IT objectives, IPE), and test-of-design / test-of-effectiveness / substantive steps with population, sample size, sampling method and period of reliance.
- Working papers with separate test-of-design and operating-effectiveness conclusions per control (Effective / Exceptions noted / Ineffective / Not tested), exceptions, a preliminary-deficiency flag, an exact evidence quote and a vault ID.
- A draft engagement-level deficiency evaluation at Gate 3 (aggregation, compensating controls, likelihood x magnitude, classification), explicitly labelled as the reporting crew's draft for the auditor's judgement, not a conclusion.
- An OSCAL-inspired results structure for exported findings.

**Evidence and AWS checks**
- Read-only boto3 evidence tools: IAM account password policy, IAM users with MFA status, and S3 bucket public-access verdicts (PUBLIC / NOT_PUBLIC / UNKNOWN) combining bucket policy status, ACL grants and account/bucket-level Block Public Access.
- A minimal IAM read-only policy matching exactly what the tools call.
- Evidence vault: one JSON record per piece of evidence, AWS account-ID redaction, SHA-256 or keyed-HMAC digests, optional Fernet encryption, and a deterministic (non-LLM) check that every finding's quoted evidence verifies against the vault record it cites.
- A digest-migration command for evidence sealed before keyed digests existed.

**Evaluation**
- `tests/eval/` uses moto to seed a fresh in-memory AWS account with planted misconfigurations and checks the deterministic evidence tools against them (coverage, redaction, vault storage and quote verification), across password-policy, MFA and S3 public-access scenarios.
- That evaluates the evidence-collection layer only.
- An LLM-layer evaluation harness (`evals/`, [docs/EVALUATION.md](docs/EVALUATION.md)): 13 scenarios planted in simulated AWS with a draft answer key, and metrics led by the false-pass rate (plus false fails, "Not tested" correctness, ToE basis, citation faithfulness, coverage, QA catch rate on seeded bad working papers, deficiency-classification agreement and run-to-run consistency). It is tested offline in replay mode. No real-model results are published in this version, and the answer key has not yet been reviewed by the owner.

**Security and supply chain**
- Bearer-token auth on all `/api/*` routes, CORS allow-list, non-root API container, nginx security headers, and a refusal to start with `DEMO_MODE=1` in `production`/`staging`.
- Scope documents (PDF or text) are size-limited and wrapped as untrusted content before being added to a crew's input.
- CI runs pre-commit (ruff, bandit, detect-secrets, pip-audit), pyright, the pytest suite across Python 3.11–3.13, frontend lint/build/`npm audit`, and Docker builds; all GitHub Actions are pinned to commit SHAs and Dependabot tracks uv, npm, GitHub Actions and both Dockerfiles.

**UI and demo mode**
- FastAPI backend and React (Vite) frontend, with a findings board, RACM view, report view, approval-trail pane and export downloads (RACM/working-papers `.xlsx`, `report.md`, `oscal.json`).
- `DEMO_MODE=1` replaces the crews with fixed, clearly labelled demo artifacts so the full state machine, gates, trail and exports can be exercised with no LLM or AWS credentials.

Earlier history — including the initial audit-flow prototype, the introduction of QA gates, the evidence vault and the demo-mode UI — is in `git log`.
