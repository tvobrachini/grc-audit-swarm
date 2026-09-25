# Architecture Decision Records: GRC Audit Swarm

This file records design decisions as they are implemented in the code today. Each record states what the code does and what that does and does not give you. Where the repository history or a code comment explains a choice, the record cites it. Where it does not, the record says nothing about motive.

Standards such as IIA Standards 12.3 (formerly 2340) and 14.6 (formerly 2330) and PCAOB AS 1215 are cited as design inspiration only. This project makes no compliance claim against any of them.

---

## ADR-001: Sequential crews with a QA reviewer in each

**Status:** Accepted

**Decision.** The workflow runs as three sequential CrewAI crews: Planning (5 agents), Fieldwork (3 agents) and Reporting (4 agents). Each crew ends with a QA reviewer agent built with `get_crew_llm(temperature=0.0)` (`src/swarm/crews/*_crew.py`), while the working agents use `temperature=0.1`. If QA rejects the RACM (Planning) or the working papers (Fieldwork), `audit_flow.py` re-runs that crew once with the rejection reason as feedback, then stops retrying.

**Consequences.**
- More LLM calls, latency and tokens than a single-agent run.
- Temperature 0 lowers variance on hosted models but does not make QA deterministic.
- The QA reviewer is another LLM. It does not prove that an output is correct.

**History.** The first versions used LangGraph. The engine was moved to CrewAI in commit `7312aab` (2026-04-02). The commit message does not give a reason, so none is recorded here.

---

## ADR-002: Evidence vault with SHA-256 hashes and verbatim-quote checks

**Status:** Accepted

**Decision.** `EvidenceAssuranceProtocol.register_evidence` (`src/swarm/evidence.py`) redacts 12-digit AWS account IDs, assigns a UUID, computes a SHA-256 hash of the redacted payload and writes the record as one JSON file. Optional Fernet encryption at rest is available through `VAULT_ENCRYPTION_KEY`; when it is on, the record stores an HMAC-SHA256 keyed from that key (via a labelled derivation, so the Fernet key is not reused directly) instead of the bare SHA-256. `verify_exact_quote()` checks that a quote cited by the field auditor appears verbatim in the stored payload, and the UI shows a verified or unverified badge for it.

**Consequences.**
- A quote that does not appear in the collected evidence is flagged.
- The hash is stored in the same JSON file as the payload, and the file is an ordinary writable file. Without encryption the hash detects accidental corruption only. It does not prevent or prove the absence of tampering.
- With encryption, a bare SHA-256 next to the ciphertext would let anyone holding the file confirm a guessed payload (evidence is small and guessable, e.g. a password policy or one user's MFA flag), which is why the digest is keyed. Fernet tokens are themselves authenticated, so edits to an encrypted record are detected without the key; deleting a record, or replacing it with an unencrypted one, is not. Records written before the keyed digest keep verifying against their stored SHA-256, and `python -m swarm.evidence migrate-digests` (with `PYTHONPATH=src` and the key set) rewrites them with the keyed digest. It only migrates a record whose payload still matches its stored hash, so corrupted evidence is never re-sealed.
- A verbatim quote shows the words exist in the evidence. It does not show that the conclusion drawn from them is correct.
- Matching is an exact substring match. Paraphrases are marked unverified.

**Inspiration.** Documentation-integrity principles from PCAOB AS 1215 and IIA Standard 14.6 (formerly 2330). Neither standard requires hashing.

---

## ADR-003: Native, read-only boto3 calls for evidence collection

**Status:** Accepted

**Decision.** Live evidence comes from a small set of boto3 calls (`src/swarm/tools/aws_tools.py`): `iam:GetAccountPasswordPolicy`, `iam:ListUsers`, `iam:ListMFADevices`, `s3:ListBuckets`, `s3:GetPublicAccessBlock` and `s3:GetBucketAcl`. Every call is a read. The tools do not shell out to the AWS CLI.

**Consequences.**
- The required IAM permissions are short and listed in the README.
- Supporting another AWS service means writing another tool function.
- Account-ID redaction and the evidence vault apply on the CrewAI tool path. The standalone MCP server (`src/swarm/mcp_server.py`) makes the same reads but does not redact or register evidence, so do not use it where that matters.

---

## ADR-004: Human approval gates between phases

**Status:** Accepted

**Decision.** `AuditStateMachine` in `src/swarm/audit_flow.py` refuses to start Phase 2 (Fieldwork) until Gate 1 is approved and refuses to start Phase 3 (Reporting) until Gate 2 is approved. Each approval is recorded with the approver's identifier in an audit trail.

**Consequences.**
- Fieldwork, and therefore the AWS calls, does not run until a person approves the plan.
- The workflow waits at each gate until someone acts.

**Inspiration.** IIA Standard 12.3, formerly 2340 (supervision of engagements). This is design inspiration, not a compliance claim.

---

## ADR-005: OSCAL-inspired report structure

**Status:** Accepted

**Decision.** In Phase 3 an agent turns the narrative findings into a Pydantic model modelled on NIST OSCAL Security Assessment Results (`src/swarm/schema.py`, including an `import-ap` link and an OSCAL version field).

**Consequences.**
- Findings exist as structured data next to the narrative report.
- The model is OSCAL-inspired. It uses Python-style field names and is not validated against the official OSCAL schema in this repository, so it should not be assumed to load into an OSCAL tool without conversion.

---

## ADR-006: LLM provider selection

**Status:** Accepted

**Decision.** `src/swarm/llm_factory.py` picks the first provider whose configuration is present, in this order: Ollama (local), NVIDIA NIM, Gemini, OpenAI, Groq. The comments in that file describe each entry: Ollama as "no limits, zero cost", Gemini as "most generous free-tier TPM", and Groq as "fastest, but harsh TPM limits".

**Consequences.**
- With several keys set, Groq is used last, not first.
- Each provider's models behave differently, so results vary with the provider selected.
