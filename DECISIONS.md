# Architecture Decision Records: GRC Audit Swarm

This file records design decisions as they are implemented in the code today. Each record states what the code does and what that does and does not give you. Where the repository history or a code comment explains a choice, the record cites it. Where it does not, the record says nothing about motive.

Standards such as IIA Standards 12.3 (formerly 2340) and 14.6 (formerly 2330) and PCAOB AS 1215 are cited as design inspiration only. This project makes no compliance claim against any of them.

---

## ADR-001: Sequential crews with a fail-closed QA reviewer in each

**Status:** Accepted

**Decision.** The workflow runs as three sequential CrewAI crews: Planning (5 agents), Fieldwork (3 agents) and Reporting (4 agents, 5 tasks). Each crew includes a QA reviewer agent built with `get_crew_llm(temperature=0.0)` (`src/swarm/crews/*_crew.py`), while the working agents use `temperature=0.1`. `AuditFlow._run_crew_with_qa` (`src/swarm/audit_flow.py`) handles all three phases the same way:

- QA fails closed. A missing or unparseable QA result, or one without a boolean `approved`, counts as a rejection (`QA_UNPARSEABLE_REASON`), never as a pass.
- On a rejection the crew is re-run once, with the rejection reason inserted into the drafting prompt (`qa_feedback` for Planning and Fieldwork, `tone_qa_feedback` for Reporting).
- A second rejection moves the phase to `QA_REJECTED_PHASE_n` and keeps the rejected draft for review. A crew exception, or an artifact that does not parse into its schema, moves it to `ERROR_PHASE_n`.

**Consequences.**
- More LLM calls, latency and tokens than a single-agent run; a rejected phase costs up to two full crew runs.
- Temperature 0 lowers variance on hosted models but does not make QA deterministic.
- The QA reviewer is another LLM. It does not prove that an output is correct.
- In Reporting the tone QA task runs before the OSCAL and assembly tasks within the same crew run, so a tone rejection re-runs the whole Reporting crew.

**History.** The first versions used LangGraph. The engine was moved to CrewAI in commit `7312aab` (2026-04-02). The commit message does not give a reason, so none is recorded here.

---

## ADR-002: Evidence vault with SHA-256 hashes and verbatim-quote checks

**Status:** Accepted

**Decision.** `EvidenceAssuranceProtocol.register_evidence` (`src/swarm/evidence.py`) redacts 12-digit AWS account IDs (bare, or in the `1234-5678-9012` form), assigns a UUID, computes a SHA-256 hash of the redacted payload and writes the record as one JSON file. Optional Fernet encryption at rest is available through `VAULT_ENCRYPTION_KEY`; when it is on, the record stores an HMAC-SHA256 keyed from that key (via a labelled derivation, so the Fernet key is not reused directly) instead of the bare SHA-256. `verify_exact_quote()` checks that a quote cited by the field auditor appears verbatim in the stored payload, and the UI shows a verified or unverified badge for it.

**Consequences.**
- A quote that does not appear in the collected evidence is flagged.
- The hash is stored in the same JSON file as the payload, and the file is an ordinary writable file. Without encryption the hash detects accidental corruption only. It does not prevent or prove the absence of tampering.
- With encryption, a bare SHA-256 next to the ciphertext would let anyone holding the file confirm a guessed payload (evidence is small and guessable, e.g. a password policy or one user's MFA flag), which is why the digest is keyed. Fernet tokens are themselves authenticated, so edits to an encrypted record are detected without the key; deleting a record, or replacing it with an unencrypted one, is not. Records written before the keyed digest keep verifying against their stored SHA-256, and `python -m swarm.evidence migrate-digests` (with `PYTHONPATH=src` and the key set) rewrites them with the keyed digest. It only migrates a record whose payload still matches its stored hash, so corrupted evidence is never re-sealed.
- A verbatim quote shows the words exist in the evidence. It does not show that the conclusion drawn from them is correct.
- Matching is an exact substring match. Paraphrases are marked unverified, and quotes shorter than 8 characters are always rejected because they would match almost any payload.

**Inspiration.** Documentation-integrity principles from PCAOB AS 1215 and IIA Standard 14.6 (formerly 2330). Neither standard requires hashing.

---

## ADR-003: Native, read-only boto3 calls for evidence collection

**Status:** Accepted

**Decision.** Live evidence comes from a small set of boto3 calls in `src/swarm/tools/aws_checks.py`: `iam:GetAccountPasswordPolicy`, `iam:ListUsers`, `iam:ListMFADevices`, `s3:ListAllMyBuckets` (ListBuckets), `s3:GetAccountPublicAccessBlock` (S3 Control GetPublicAccessBlock), `s3:GetBucketPublicAccessBlock`, `s3:GetBucketPolicyStatus` and `s3:GetBucketAcl`, plus `sts:GetCallerIdentity`, which needs no IAM permission. Every call is a read. The tools do not shell out to the AWS CLI. The CrewAI tools (`aws_tools.py`) and the standalone MCP server both call this module, so they share one implementation.

The S3 check follows AWS's own semantics rather than a heuristic:

- Block Public Access is read at the account level (the account ID comes from `GetCallerIdentity` and is not output or logged) and the bucket level. Each flag's effective value is account OR bucket.
- A bucket policy is public if `GetBucketPolicyStatus` says so (AWS's evaluation; no policy means not public by policy). It makes the bucket public only if `RestrictPublicBuckets` is off at both levels.
- An ACL grant to `AllUsers` or `AuthenticatedUsers` is public. It makes the bucket public only if `IgnorePublicAcls` is off at both levels.
- `BlockPublicAcls` and `BlockPublicPolicy` only reject new public ACLs and policies, so they do not change the verdict for what is already in place.
- A read that fails (for example AccessDenied) makes that input unknown. The verdict is PUBLIC, NOT_PUBLIC or UNKNOWN; it is UNKNOWN whenever the known inputs do not decide it, and the output says which read failed.

The IAM tools paginate `ListUsers` and `ListMFADevices`. A missing password policy (`NoSuchEntity`) is reported as a finding, not as a collection error.

**Consequences.**
- The required IAM permissions are short and listed in the README.
- A partial Block Public Access configuration is no longer reported as public on its own, and a bucket made public by its policy or by an `AuthenticatedUsers` grant is no longer missed.
- Still not covered: access points and their policies, object-level ACLs, and cross-account access through a policy AWS does not classify as public. The root user is not returned by `ListUsers`, so root MFA is not covered.
- `tests/eval/` checks the tools against planted misconfigurations in a moto account; where moto is not faithful to AWS (notably `GetBucketPolicyStatus`), the branch is covered with botocore Stubber instead.
- Supporting another AWS service means writing another tool function.
- Account-ID redaction of the full output and the evidence vault apply on the CrewAI tool path only. The standalone MCP server (`src/swarm/mcp_server.py`) makes the same reads and redacts AWS error messages, but does not redact its final output or register evidence, so do not use it where that matters.

---

## ADR-004: Three human approval gates, enforced by a state machine

**Status:** Accepted

**Decision.** `AuditStateMachine` (`src/swarm/state/machine.py`) defines every allowed status change in one transition table. Each named transition also checks its source state, and anything else raises `InvalidTransitionError` without changing the status. There are three gates:

- Gate 1 (`WAITING_HUMAN_GATE_1`) must be approved before Fieldwork runs.
- Gate 2 (`WAITING_HUMAN_GATE_2`) must be approved before Reporting runs.
- Gate 3 (`WAITING_HUMAN_GATE_3`) must be approved before the audit is `COMPLETED`.

From `QA_REJECTED_PHASE_n` a reviewer can retry the phase or override the rejection. From `ERROR_PHASE_n` only a retry is possible. An override requires a reason and moves the phase to its normal gate, which still has to be approved. It is refused if the phase kept no artifact. A human retry out of a QA rejection passes the stored rejection reason to the crew (`AuditFlow._retry_feedback`, read back from the persisted trail).

`AuditFlow._stamp_trail` appends one entry per action to `approval_trail`: `gate`, `human` (the reviewer identifier), `timestamp` (UTC, ISO 8601) and `action` (`gate_approval`, `retry` or `qa_override`). A retry also records `previous_status` and `previous_reason`. An override records `reason` and the `qa_rejection_reason` it overrode. The API returns 409 for an invalid transition, such as approving the same gate twice, and 422 for a blank reviewer or reason (`src/api/routers/sessions.py`).

**Consequences.**
- Fieldwork, and therefore the AWS calls, does not run until a person approves the plan. No report is marked complete without a final sign-off.
- The workflow waits at each gate until someone acts.
- The reviewer identifier is free text supplied by the client. The API has one shared token and does not authenticate individual reviewers, so the trail records who the reviewer said they were.

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

---

## ADR-007: Single UI: FastAPI + React; Streamlit removed

**Status:** Accepted

**Context.** The project had two front ends: a Streamlit app (`app.py`, `src/ui/`) and a React app backed by FastAPI. Each reached the audit workflow through its own code, and their behaviour differed. Pull request #135 lists the gaps it closed: Gate 3 could not be approved in the React UI, its error panel checked for a status that does not exist, demo mode and most exports existed only in Streamlit, and Streamlit only warned (instead of refusing) when demo mode was on in production.

**Decision.** FastAPI (`src/api/`) plus React (`frontend/`) is the only UI. Streamlit was removed in commit `d17d878`, after the features worth keeping were moved to the API and React: API-level `DEMO_MODE`, exports, retry and QA override, scope-document upload and per-finding vault checks. The lab-files picker (which browsed the server's filesystem) and a "request changes" box were dropped rather than ported. Removing Streamlit also removed the `streamlit` and `pandas` dependencies.

**Consequences.**
- Gate and phase logic has one caller path: the API calls `AuditFlow`, and the UI only calls the API.
- Running the full UI needs two processes (API and frontend) or Docker Compose.

---

## ADR-008: The API token is injected by nginx, not shipped to the browser

**Status:** Accepted

**Decision.** Every `/api/*` route requires a shared token (`API_AUTH_TOKEN`, checked in `src/api/auth.py` with a constant-time comparison). The API returns 503 if no token is configured and 401 if the token is missing or wrong; `/health` is open. In the Compose deployment the React bundle carries no token. nginx adds `Authorization: Bearer ${API_AUTH_TOKEN}` when it proxies `/api/` to the API (`frontend/nginx.conf.template`, rendered by the nginx image's envsubst step at container start). The comment there gives the reason: the token should not be baked into the public JavaScript bundle, and the browser's `EventSource` (used for the live agent feed) cannot set headers anyway.

`VITE_API_AUTH_TOKEN` exists for the Vite dev server only. It is compiled into the bundle, so it must not be set for a Compose or production build.

**Consequences.**
- The token does not appear in the browser. Anyone who can reach the frontend's port can still use the API through the proxy, which is why Compose binds ports to `127.0.0.1`.
- One shared token means there is no per-user identity or authorisation (see ADR-004).
- In the Vite dev setup the agent feed's `EventSource` request has no token and gets 401. Status still refreshes through polling.
