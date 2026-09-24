# Architecture Decision Records (ADR) — GRC Audit Swarm

This document records the key architectural decisions, rationale, and tradeoffs made in designing and building **GRC Audit Swarm**.

---

## ADR-001: Sequential Multi-Agent Crews with Deterministic QA Retries

### Status
Accepted

### Context
Audits follow distinct professional phases: Planning (scoping & RACM), Fieldwork (testing & working papers), and Reporting (executive synthesis & compliance artifact generation). A single prompt or monolithic LLM agent commonly suffers from context saturation, hallucinations over complex controls, and inability to enforce professional rigor.

### Decision
Split the workflow into three isolated, sequential **CrewAI** crews:
1. `PlanningCrew` (5 agents)
2. `FieldworkCrew` (3 agents)
3. `ReportingCrew` (4 agents)

Each crew contains a dedicated **QA Reviewer agent** running with temperature $0.0$ and explicit rejection criteria (e.g., rejecting RACMs where substantive testing is omitted, or rejecting working papers where a control passes without supporting evidence). Rejections inject structured feedback into the crew state and trigger an automated retry loop.

### Tradeoffs & Consequences
- **Positive:** High fidelity outputs; domain-specific prompts remain small and focused; deterministic QA catches hallucinated conclusions.
- **Negative:** Increased latency and token consumption compared to linear single-agent execution.

---

## ADR-002: Immutable SHA-256 Evidence Vault & Verbatim Quote Verification

### Status
Accepted

### Context
In financial and IT audits (inspired by PCAOB AS 1215 and IIA Standard 2330 principles), documentation integrity is paramount. In AI-assisted auditing, a major risk is the LLM fabricating evidence or misattributing quotes from raw logs.

### Decision
Implement the `EvidenceAssuranceProtocol`:
1. Every piece of raw evidence collected (API responses, configurations, logs) is assigned a UUID, hashed via **SHA-256**, and stored in the local evidence vault.
2. The UI and verification layer run `verify_exact_quote()`, which validates that quotes cited by the field auditor match verbatim snippets in the hashed evidence file.
3. Citations display a visual verification badge (✅ Verified / ❌ Unverified).

### Tradeoffs & Consequences
- **Positive:** Zero tolerance for fabricated evidence; transparent provenance for every finding.
- **Negative:** Requires strict exact-match quoting by agents; minor variations in whitespace can flag as unverified unless normalized.

---

## ADR-003: Native Read-Only AWS API Calls (boto3) vs. Shell / CLI Execution

### Status
Accepted

### Context
Automating cloud audits requires querying cloud configurations. Running arbitrary shell scripts or CLI commands exposes the host system to command injection and potential unauthorized modifications.

### Decision
Build native Python tools using `boto3` for specific, granular read-only API calls (`get_iam_password_policy`, `list_iam_users_with_mfa`, `list_public_s3_buckets`). 
- Enforce strict read-only tool definitions with no mutation or deletion capabilities.
- Automatically scrub 12-digit AWS account IDs before saving evidence or presenting data to agents.

### Tradeoffs & Consequences
- **Positive:** Safe execution; no shell execution vulnerabilities; predictable structured data schema.
- **Negative:** Supporting new cloud services requires implementing dedicated Python tool functions rather than running arbitrary CLI scripts.

---

## ADR-004: Human-in-the-Loop (HITL) Supervision Gates

### Status
Accepted

### Context
IIA Standard 2340 states that internal audit engagements must be properly supervised. Completely autonomous end-to-end execution without human checkpoints creates liability and prevents domain experts from course-correcting audit scope.

### Decision
Introduce stateful Human Approval Gates between phases:
- **Gate 1:** After Planning QA passes, the human auditor reviews, edits, or approves the RACM before Fieldwork starts.
- **Gate 2:** After Fieldwork QA passes, the human auditor reviews working papers and severity ratings before final Report synthesis.

### Tradeoffs & Consequences
- **Positive:** Professional oversight; prevents unintended cloud API calls; allows human adjustment of risk ratings.
- **Negative:** Workflow pauses until human input is received (mitigated by asynchronous session persistence).

---

## ADR-005: NIST OSCAL Machine-Readable Export Schema

### Status
Accepted

### Context
Traditional audit deliverables are static PDF/Word documents that require manual re-entry into enterprise GRC systems (e.g., ServiceNow, Archer, OneTrust).

### Decision
Integrate a dedicated Compliance Documentation Engineer agent in Phase 3 that translates narrative findings into machine-readable **NIST OSCAL** (Open Security Controls Assessment Language) Security Assessment Results (`OSCAL_SAR_Schema`).

### Tradeoffs & Consequences
- **Positive:** Directly consumable by modern compliance-as-code platforms; establishes interoperability with enterprise GRC architectures.
- **Negative:** Adds schema transformation overhead at the end of Phase 3.
