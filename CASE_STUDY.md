# Case Study: Keeping Audit Discipline in an Agent Workflow

**Role:** Personal project by Tiago Brachini (IT audit, ITGC/SOX, cloud security)
**Stack:** Python, CrewAI, boto3, FastAPI, React, pytest
**Status:** Working prototype. Not benchmarked. Not used on a real engagement.

---

## The problem, from the audit side

Language models can draft a risk matrix or a finding in seconds. The drafting was never the hard part of an audit. The hard parts are the things that make a draft trustworthy:

1. **Supervision.** Work moves from planning to fieldwork to reporting only after a reviewer signs off, and the sign-off is documented. A pipeline that runs end to end on its own skips the step that gives the output its standing.
2. **Evidence integrity.** A finding is only as good as the evidence behind it. The reviewer needs to know where a quote came from and whether the record was changed afterwards. Models paraphrase and sometimes invent quotes.
3. **Test design versus effectiveness.** A control that is designed well can still fail in operation. A test plan that relies on inquiry alone, or skips substantive testing, is a weak plan even when it reads well.
4. **Reviewer challenge.** Senior reviewers send work back. That pushback, and the reason for it, belongs in the record.
5. **Conclusion of record.** A finished report should state what the reviewer decided, not just what the AI proposed. Sign-offs, classifications, write-ups, management responses and the engagement conclusion need to be reviewer decisions the record can point to, separate from the draft.

This project asks a narrow question: if agents draft the work, can the workflow around them still enforce these four things?

---

## Design decisions mapped to audit concepts

| Audit concept | What the code does | Where |
|---|---|---|
| Phased engagement | Three sequential crews: Planning (RACM), Fieldwork (working papers), Reporting (report and structured results) | `src/swarm/crews/`, `src/swarm/audit_flow.py` |
| Supervision and sign-off (inspired by IIA Standard 12.3, formerly 2340) | A state machine blocks each phase until a person approves the previous one. There are three gates, the last one after Reporting. Out-of-order or repeated approvals are refused. | `src/swarm/state/machine.py` |
| Documented review | Every approval, retry and override is logged with the reviewer's name, a timestamp and the action. An override also records its reason and the QA rejection it overrode. | `AuditFlow._stamp_trail` in `audit_flow.py` |
| Reviewer pushback | Each crew ends with a QA reviewer agent. A rejection, or a QA answer that cannot be parsed, fails the phase closed. The crew is re-run once with the reason as feedback. After that a person decides: retry, or accept the draft with a written justification and then approve the gate as usual. | `_run_crew_with_qa` in `audit_flow.py` |
| Test design vs effectiveness | The RACM schema requires test-of-design, test-of-effectiveness and substantive steps for every control. The Planning QA prompt rejects inquiry-only effectiveness tests and missing substantive steps. | `src/swarm/schema.py`, `src/swarm/config/planning_tasks.yaml` |
| Evidence integrity (inspired by PCAOB AS 1215 and IIA Standard 14.6, formerly 2330) | Evidence is redacted, stored with a SHA-256 digest (or a keyed HMAC plus Fernet encryption when a key is set), and each quote in the working papers is checked word for word against the stored record | `src/swarm/evidence.py` |
| Scope and access limits | Evidence tools make read-only AWS calls. The README lists the eight IAM actions they need. AWS account IDs are redacted before storage and before the model sees the output. | `src/swarm/tools/aws_checks.py` (boto3 reads), `aws_tools.py` (CrewAI wrappers) |
| Consistent inputs between phases | Fieldwork receives the approved RACM. Reporting receives the scope, a RACM summary and the approved working papers. A test checks that every prompt placeholder is filled. | `tests/test_prompt_inputs.py` |
| Conclusion of record (ADR-011) | Reviewer decisions (sign-off/challenge, scope limitation, classification, five-part write-up, management response, engagement conclusion) are appended to the trail and never change the AI draft. Exports render the "effective view": the reviewer's decision where one exists, the AI draft labelled otherwise, and both together when they differ. | `src/swarm/review_decisions.py`, `frontend/src/components/audit/decisions/` |
| Reviewer identity (ADR-012) | By default a reviewer's name in the trail is declared (typed by the caller behind one shared API token). An operator can opt into per-reviewer tokens (`REVIEWER_TOKENS_FILE`) so the trail instead records an identity the application authenticated, still without SSO or MFA. | `src/api/reviewer_tokens.py` |

The standards are cited as design inspiration. Neither AS 1215 nor the IIA Standards require hashing or any other specific mechanism. AS 1215 governs audits of public-company financial statements, which this tool does not perform. The project makes no compliance claim.

---

## What this does and does not show

**It shows** that the workflow controls can be enforced in code and tested:

- a phase cannot start without the previous gate's approval;
- QA fails closed, and the rejection reason is carried into the retry;
- an override cannot skip the human gate and always carries a justification;
- a quote that is not in the stored evidence is flagged;
- the approval trail records who acted, when, and why.

The test suite covers these paths with mocked crews. `DEMO_MODE` lets anyone walk through them in the UI without API keys.

**It does not show:**

- that the agents' RACMs, findings or reports are correct. The QA reviewer is also a model, and nothing here has been measured against auditor-prepared work;
- that the evidence vault cannot be tampered with. The digest sits in the same writable file as the evidence, and deleting a record is not detected;
- broad evidence coverage. Live collection is limited to the IAM password policy, IAM user MFA, and S3 public access (bucket policy status, ACLs, and bucket- and account-level Block Public Access). Access points, object ACLs and the root user are not covered;
- authenticated reviewer identity by default. Unless per-reviewer tokens are turned on, the trail records the name the reviewer typed, behind a single shared API token; even with tokens on, it is not single sign-on and has no MFA;
- time or cost savings. None have been measured.

## Intended outcomes (not yet measured)

If the approach holds up, a reviewer would spend their time challenging a structured draft with linked evidence instead of assembling it. The next step to test that would be to compare agent drafts with auditor-prepared RACMs and findings for the same scope, and to record how often QA and human reviewers reject drafts, and why.

---

## Where to look in the code

1. `src/swarm/audit_flow.py`: phase orchestration, the fail-closed QA retry loop, gate actions and the approval trail.
2. `src/swarm/state/machine.py`: the allowed state transitions.
3. `src/swarm/evidence.py`: redaction, digests, optional encryption, quote verification and `migrate-digests`.
4. `src/swarm/tools/aws_checks.py` (boto3 reads) and `aws_tools.py` (CrewAI wrappers): the read-only AWS evidence tools.
5. `src/swarm/crews/` and `src/swarm/config/`: the crews and their agent and task prompts.
6. `src/api/` and `frontend/src/`: the FastAPI backend and the React UI (gates, retry and override, findings with vault checks, exports, and reviewer decisions in `frontend/src/components/audit/decisions/`).
7. `src/swarm/review_decisions.py`: reviewer decisions, the effective view and per-reviewer tokens (ADR-011, ADR-012).
8. `tests/`: including `test_audit_flow_gates.py`, `test_api_gates.py`, `test_evidence.py`, `test_review_decisions.py` and `test_prompt_inputs.py`.

Design records are in [DECISIONS.md](DECISIONS.md). Setup and limitations are in the [README](README.md).
