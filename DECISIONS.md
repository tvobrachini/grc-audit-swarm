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

**Decision.** `EvidenceAssuranceProtocol.register_evidence` (`src/swarm/evidence.py`) redacts 12-digit AWS account IDs (bare, or in the `1234-5678-9012` form), assigns a UUID, computes a SHA-256 hash of the redacted payload and writes the record as one JSON file. Optional Fernet encryption at rest is available through `VAULT_ENCRYPTION_KEY`; when it is on, the record stores an HMAC-SHA256 keyed from that key (via a labeled derivation, so the Fernet key is not reused directly) instead of the bare SHA-256. `verify_exact_quote()` checks that a quote cited by the field auditor appears verbatim in the stored payload, and the UI shows a verified or unverified badge for it.

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

At `WAITING_HUMAN_GATE_n` a reviewer can approve, or return the phase for rework (`WAITING_HUMAN_GATE_n → RUNNING_PHASE_n`) with required review notes. From `QA_REJECTED_PHASE_n` a reviewer can retry the phase or override the rejection. From `ERROR_PHASE_n` only a retry is possible. An override requires a reason and moves the phase to its normal gate, which still has to be approved. It is refused if the phase kept no artifact, and a gate approval is refused if the phase has no artifact to approve. A re-run passes feedback to the crew through the same per-phase input the automatic QA retry uses: after a return, the review notes; after a human retry out of a QA rejection, the stored rejection reason (`AuditFlow._carried_feedback`, read back from the persisted trail). If the re-run is then auto-retried, the new QA reason is added to that feedback rather than replacing it.

For Fieldwork, a deterministic check (`swarm.evidence.unverified_findings`) verifies every finding's quote against the vault after the QA agent. An unverified quote is treated as a QA rejection that lists the control IDs; a finding without a quote passes only if it concludes "Not tested". Gate 2 approval re-runs the check and is refused unless every unverified control was accepted by a supervisor override recorded against the same working papers (matched by digest).

`AuditFlow._stamp_trail` appends one entry per action to `approval_trail`: `gate`, `human` (the reviewer identifier), `timestamp` (UTC, ISO 8601) and `action` (`audit_created`, `gate_approval`, `return_for_rework`, `retry` or `qa_override`). A gate approval records the `artifact` and its `artifact_digest`. A return records `notes`. A retry also records `previous_status` and `previous_reason`. An override records `reason`, the `qa_rejection_reason` it overrode, the accepted artifact's digest and, for Fieldwork, `unverified_controls`. Entries are hash-chained (ADR-009). The API returns 409 for an invalid transition (such as approving the same gate twice) or a policy refusal (segregation of duties, unverified evidence, missing artifact), and 422 for a blank reviewer, notes or reason (`src/api/routers/sessions.py`). Every action is persisted before any crew it starts runs. `DELETE /api/sessions/{id}` returns 409 once a gate has been approved or the audit is completed; only drafts can be deleted.

**Consequences.**
- Fieldwork, and therefore the AWS calls, does not run until a person approves the plan. No report is marked complete without a final sign-off.
- The workflow waits at each gate until someone acts. A reviewer who disagrees can send the work back with notes instead of choosing between approving and abandoning it.
- A Fieldwork result whose quotes are not in the vault cannot reach Gate 2 approval without a named supervisor's written justification.
- The reviewer identifier is free text supplied by the client. The API has one shared token and does not authenticate individual reviewers, so the trail records who the reviewer said they were, and the segregation-of-duties rules (ADR-009) compare those declared names. ADR-012 adds optional per-reviewer tokens that replace the typed name.

**Inspiration.** IIA Standard 12.3, formerly 2340 (supervision of engagements). This is design inspiration, not a compliance claim.

---

## ADR-005: OSCAL Assessment Results export

**Status:** Accepted (supersedes "OSCAL-inspired report structure")

**Context.** In Phase 3 an agent writes `oscal_sar`, a Pydantic model loosely modelled on OSCAL (`OSCAL_SAR_Schema` in `src/swarm/schema.py`, Python-style field names). Until this change the `oscal.json` export was that model dumped as-is: OSCAL-shaped, but not valid OSCAL.

**Decision.** `GET /api/sessions/{id}/export/oscal.json` returns an OSCAL **Assessment Results** document, `oscal-version` **1.2.1**, built by a deterministic converter (`src/swarm/oscal_ar.py`) from the gate-reviewed artifacts: the RACM, the working papers, the final report's deficiency evaluation and the approval trail. The LLM keeps producing `oscal_sar` in its own format; the converter uses only its document title and, as a labelled remark, its narrative per control. Controls, evidence IDs and conclusions come from the working papers and RACM, so model output cannot add controls or evidence references to the export.

Version choice: the latest NIST release is 1.2.3. The export declares 1.2.1 because compliance-trestle 5.1.0 (IBM / oscal-compass) accepts only 1.2.0–1.2.1 in `oscal-version`. The 1.2.3 assessment-results schema differs from 1.2.1 only by three extra component `type` values, which the export does not use.

**Mapping.**

| Source | OSCAL Assessment Results |
| --- | --- |
| Session | `metadata`: title (from `oscal_sar`, else the session name), `last-modified` = time of the latest approval-trail entry (not the export time, so exports are reproducible), `version` = digest of the report, working papers and RACM, props for session ID, status and report state. |
| Preparer and approvers (declared identities) | `parties` (type `person`), roles `prepared-by`, `gate-approver` (any gate approval) and `content-approver` (Gate 3), linked by `responsible-parties`. |
| Approval trail | `results[0].assessment-log.entries`, one per trail entry, `logged-by` the acting party; action, artifact digest and the entry's hash-chain values as props. |
| (no assessment plan) | `import-ap.href` = `#<uuid>` of a `back-matter` resource that describes the RACM as the plan and links to the RACM `.xlsx` export. |
| RACM control IDs | `reviewed-controls.control-selections[0].include-controls` (IDs mapped to OSCAL tokens; the original ID is kept as a `control-id` prop on the observation). |
| Each working-paper finding | One `observation`: `methods` EXAMINE when design and implementation (ToD) was concluded, TEST when operating effectiveness (ToE) was concluded, EXAMINE alone when nothing was tested; ToD / ToE conclusion, result, items tested, exceptions and the preliminary-deficiency flag as props; `relevant-evidence` pointing to a `back-matter` resource per vault record (vault ID, source operation, collection time; the payload is not embedded) with `quote-verified-in-vault` re-checked at export time; `collected` = the vault record's timestamp. |
| Each *tested* finding | One `finding` with target `objective-id` = the control ID: `satisfied`/`pass` for "No exception", `not-satisfied`/`fail` for "Exception". A satisfied finding whose ToE was not tested says so in the status remarks. |
| "Not tested" finding | An observation (result prop `Not tested`, remark) but no `finding`: an OSCAL finding target must be `satisfied` or `not-satisfied`, and neither is true. |
| Each deficiency evaluation | One `risk` (status `open`) with the proposed classification, scale, likelihood, magnitude and RACM risk IDs as props, `classification-state` = `draft` until the report is approved at Gate 3 (then `approved-with-report`), the draft wording in its description, and links both ways to the related observations and findings. |
| Audit scope | One result-local `component` (type `this-system`, status `other`) used as every observation's subject; the tool appears as an `assessment-platform` in each observation's `origins`. |

Custom props all use the namespace `https://github.com/tvobrachini/grc-audit-swarm/ns/oscal`. Every UUID is a version-5 UUID of the session ID plus the element (for example `observation/CTRL-02#1`), so exporting the same session twice gives the same document, and an element keeps its UUID across revisions, as OSCAL asks for per-subject identifiers.

**Validation.** `tests/test_oscal_ar.py` validates exports against NIST's official OSCAL 1.2.1 assessment-results JSON schema, vendored unchanged in `tests/fixtures/oscal/` (source URL, SHA-256 and license, which is US-government public domain plus CC0, in its README). It covers a DEMO_MODE session exported through the API, a synthetic report with exceptions, a not-tested control, a non-token control ID, legacy-severity findings, missing trail / RACM / `oscal_sar` and the committed `docs/sample-run/oscal.json`, plus prop / namespace / UUID / reference checks and determinism. The schema's patterns use Unicode property escapes that Python's `re` does not support, so the test evaluates `pattern` with the `regex` module; a set of deliberately broken documents proves the validator rejects bad UUIDs, tokens, namespaces, datetimes and statuses. It runs in CI's existing unit-test job. The demo sample (`docs/sample-run/oscal.json`) and a finding-rich document were also loaded with compliance-trestle 5.1.0 once, by hand; trestle is not a project dependency.

**Consequences and limits.**
- The document is schema-valid OSCAL. Schema validation does not check OSCAL's Metaschema constraints (allowed values, cross-references) or FedRAMP rules; the tests check that every internal UUID reference resolves, but no external OSCAL validator runs in CI.
- There is no OSCAL assessment plan, system security plan, catalog or profile. Control IDs are the engagement's own RACM IDs, not catalog IDs, so a tool that resolves controls against a catalog will not find them. `import-ap` resolves only to a description of the RACM.
- ToD / ToE conclusions, exception counts, deficiency classifications and scales have no OSCAL equivalent and are custom props that other tools will ignore. OSCAL has no "not tested" finding state, so untested controls appear only as observations.
- Party names are the identities people declared in the app; the API does not authenticate reviewers (ADR-004, ADR-009).
- The detailed report narrative is not embedded (the result's description is the executive summary); it stays in `report.md`.
- The export is available once the session has a final report and working papers, including a draft awaiting Gate 3; its `report-state` prop and the risks' `classification-state` say which.

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

**Context.** The project had two front ends: a Streamlit app (`app.py`, `src/ui/`) and a React app backed by FastAPI. Each reached the audit workflow through its own code, and their behavior differed. Pull request #135 lists the gaps it closed: Gate 3 could not be approved in the React UI, its error panel checked for a status that does not exist, demo mode and most exports existed only in Streamlit, and Streamlit only warned (instead of refusing) when demo mode was on in production.

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
- One shared token means there is no per-user identity or authorization (see ADR-004); ADR-012 adds an optional per-reviewer token on top of it.
- In the Vite dev setup the agent feed's `EventSource` request has no token and gets 401. Status still refreshes through polling.

---

## ADR-009: Hash-chained approval trail and segregation-of-duties policy

**Status:** Accepted

**Decision (trail).** The approval trail is append-only: entries are added only through `swarm.trail.append_entry`. Each entry stores `hash_alg`, `prev_hash` (the previous entry's `entry_hash`, or 64 zeros for the first) and `entry_hash`, a digest over the canonical JSON of the entry (sorted keys, compact separators, UTF-8; every field except `entry_hash`). With `VAULT_ENCRYPTION_KEY` set the digest is HMAC-SHA256 under a key derived from it with its own label (`grc-audit-swarm/approval-trail/hmac-sha256/v1`, see `swarm.evidence.derive_labelled_key`), so it is never the vault's HMAC key; otherwise it is SHA-256. Entries written before chaining are left as they are; the first chained entry's `prev_hash` is a digest over them, which fixes their content from then on. `swarm.trail.verify_trail` recomputes the chain and returns `ok`, a `status` and `first_broken_index`. `FlowRepository.save` also writes the entry count and last hash to a separate anchor file (`TRAIL_ANCHORS_PATH`); the anchor never moves backwards.

What verification detects: an edited entry; reordered entries; a removed or inserted entry anywhere except at the end; a gate-approved artifact that no longer matches the digest recorded at approval (`artifact_changed`). With the key it also detects a chain recomputed without the key: such entries can only be unkeyed and are reported as `unkeyed` when they are not sealed by a later keyed entry. Against the anchor it detects a trail shorter than the anchored count (`truncated`) or one that diverges from the anchored hash.

What it does not detect: removal of entries from the end, or of the whole trail, when there is no anchor or the anchor file was edited too; a full rewrite by someone who can edit the sessions file and the anchor file, unless the key is in use and they do not have it; a trail copied from another session (the chain is not bound to the session ID, only the anchor file is keyed by it). Legacy trails report `legacy_unchained`. A keyed trail read without the key reports `key_unavailable`. None of this is a guarantee that the trail is untampered; it makes certain changes detectable.

**Decision (segregation of duties).** Each audit records `prepared_by` at creation, also as the trail's first entry (`audit_created`). `swarm.review_policy.sod_violation` is the single policy function:
1. The preparer may not approve a gate, override a QA rejection, or return work (`PREPARER_EXCLUDED_ACTIONS`). A retry is allowed; it re-runs preparation rather than reviewing it. The preparer is taken from both `prepared_by` and the `audit_created` entry.
2. A gate's approver must differ from the approvers of the gates listed in `DISTINCT_APPROVER_GATES` (default: Gate 3 differs from Gate 2, the manager / in-charge separation).
Identities are compared after case folding and collapsing whitespace. Audits created before `prepared_by` existed have no preparer, so rule 1 cannot apply to them.

**Consequences.**
- A self-review or a single person signing off both fieldwork and the report is refused with a 409 that names the rule, and the owner can change the policy in one place.
- Identity is declared, not authenticated (ADR-008): the checks stop honest mistakes and make self-review visible; they do not stop someone typing another name. Real enforcement needs per-user authentication; ADR-012 adds an opt-in form of it.
- The anchor file only adds protection if it is kept where the people who can edit the sessions file cannot edit it (separate storage or an append-only store); by default it sits next to the sessions file.

---

## ADR-010: Generation provenance — what is recorded and what it does and does not prove

**Status:** Accepted

**Context.** A PCAOB inspector or external auditor reviewing this project as a working paper would ask: which model produced this artifact, on which prompt, and is the record of that tamper-evident? Before this decision, an artifact carried no record of how it was generated, and a gate approval, retry or override did not say which run of the crew it acted on.

**Decision (what is recorded).** Every attempted phase run (`AuditFlow.generate_planning` / `_fieldwork` / `_reporting`, including a QA auto-retry and a human-initiated retry) appends one `GenerationRun` to `AuditState.generation_runs` (`src/swarm/state/schema.py`): a `run_id`; the `provider`/`model` the drafting agents ran on and the `qa_provider`/`qa_model` the QA reviewer ran on (`swarm.llm_factory.describe_crew_llm` / `describe_qa_llm` — provider and model *names* only, built without constructing a client, so a describe call can never itself leak a key, base URL or token); the `temperature` and `qa_temperature` used (0.1 / 0.0, matching the crews' `Agent` construction); the installed `crewai_version` and `app_version` (`swarm.evidence.app_version`: installed package metadata, falling back to `pyproject.toml`'s version for a source checkout); a `prompt_fingerprint` (see below); `started_at`/`ended_at` timestamps; the `attempts` made (1 or 2 — `AuditFlow._MAX_QA_ATTEMPTS`); `demo_mode`; and an `outcome` (`qa_approved`, `qa_rejected` or `error`). It is exposed read-only on `GET /api/sessions/{id}` as `generation_runs` (`src/api/models.py: GenerationRunSummary`) and persisted/restored by `FlowRepository` through `AuditState`'s ordinary (de)serialisation — no repository code change was needed for it.

**Decision (prompt fingerprint).** `swarm.audit_flow.prompt_fingerprint(phase, skill_context)` is a SHA-256 over the phase's agent and task YAML config files (raw bytes, length-prefixed and concatenated in a fixed order) plus, when a domain skill is active, the specialist prompt text `skill_loader.get_specialist_prompt` injects into an agent's backstory. It is deterministic: the same config files and active skills always fingerprint the same; a config edit or a different skill set changes it. It identifies *which prompts* a run used, not the model's actual output.

**Decision (trail linkage).** A gate approval, retry, return-for-rework and QA override each add `generation_run_id`, `generation_prompt_fingerprint` and `generation_model` (`"{provider}/{model}"`) for the generation run the action reviewed or is retrying (`AuditFlow._run_provenance_extra`, the most recent `generation_runs` entry for that phase) — so an approval is tied to exactly what was reviewed, not just to an artifact digest. These are ordinary fields on the trail entry: `swarm.trail.canonical_entry` already hashes every field except `entry_hash`, so they are covered by the hash chain automatically, with no change to the chaining logic itself; a trail entry from before this field existed simply lacks them and still verifies (`legacy_unchained` / `ok` as before — see ADR-009).

**Decision (evidence metadata).** `EvidenceAssuranceProtocol.register_evidence` takes an optional `metadata` dict: AWS region, the API operation(s) called (and any non-sensitive parameters), the collecting tool's name, `app_version`, and the caller's identity as an ARN with the account id redacted (`swarm.tools.aws_checks.get_caller_identity` — reuses the `sts:GetCallerIdentity` call the S3 tool already makes for Block Public Access, so no new AWS call or permission is added for it; the two IAM tools do not make that call and so record no caller identity, which the docstring calls out — never omit by recording a substitute). Every string in `metadata` is redacted the same way as the payload before it is stored (`_sanitize_metadata`). Chosen digest design: metadata is folded into the *same* integrity digest as the payload (`_digest_input`: payload, then a NUL byte, then metadata's canonical JSON, when metadata is given), so tampering with either is detected exactly the same way as tampering with the payload today — no second digest field to keep in sync. A record with no metadata (every record written before this field existed, and any record not given one) hashes the payload alone, byte-for-byte as before, so old digests, `verify_exact_quote` and `migrate_legacy_digests` all keep working unchanged on it.

**Decision (DEMO_MODE).** A demo run's `GenerationRun` records `provider="demo"`, `model="fixed-content"` (also for its QA fields) and `demo_mode=True`, through the same code path as a real run — `AuditFlow._start_generation_run` reads `demo_mode_requested()` (which never raises) rather than `demo_mode_enabled()` (which raises when `DEMO_MODE` is set in a blocked environment), so that guard's raise still surfaces from inside the crew build/kickoff try/except exactly as before, not from provenance capture.

**Consequences.**
- An inspector can now answer "which model, on which prompt, produced this draft, and did the same run get approved?" for every phase and every gate action, from data recorded automatically, not from an engineer's memory.
- What this does **not** prove: LLM output is not deterministic even from the identical provider, model, prompt and temperature, so recording all four does not make a run replayable or reproducible bit-for-bit; two runs with an identical `prompt_fingerprint` can still produce different artifacts. `describe_crew_llm`/`describe_qa_llm` name the provider and model the process was configured to use, not a guarantee that provider's servers ran the request unmodified, nor that a proxy or provider-side model alias did not resolve to a different underlying model than the name implies. Identity in the trail is still declared, not authenticated (ADR-008/009): a generation run does not by itself prove a human reviewed the artifact it names, only that the review action is tied to that run's record.
- Recording the caller identity ARN needed a small refactor of `swarm.tools.aws_checks.get_account_bpa` (it now takes an `account_id`, from a new `get_caller_identity(sts)`, instead of an `sts` client) so the identity is captured from a call the S3 tool already made, rather than adding a new AWS call and permission; this is why that function's signature changed in this change (see its tests in `tests/test_aws_checks.py`).
- `GenerationRun` and `GenerationRunSummary` (API) are a second, small schema surface to keep in sync when a new field is added; both are simple flat models, kept deliberately free of anything except names, hashes and timestamps.

---

## ADR-011: Reviewer decisions as an append-only record beside the AI drafts

**Status:** Accepted (Option B, slices 1 and 2). The defaults listed under "Provisional" are awaiting the owner's confirmation.

**Context.** A reviewer could approve a gate or return it for rework, and nothing else. They could not record their own conclusion, a final deficiency classification, a per-finding sign-off, a finding write-up or management's response, and the report still called every classification "proposed" after Gate 3. The only way to change a conclusion was to ask the crew to redraft it, which could also change findings the reviewer had no issue with. Three designs were compared: fields on the artifacts (A), a separate append-only record (B), and reviewer edits with diffs in the trail (C). B was chosen because it keeps the AI draft and the human decision apart, fits the existing trail and digest semantics, and leaves the eval harness measuring the model.

**Decision (data).** `AuditState.review_decisions` is an append-only list of `ReviewDecision` (`src/swarm/schema.py`): `decision_id`, `phase` (2 or 3: the gate whose approval seals it), `artifact` (`working_papers` or `final_report`), `draft_digest` (the artifact's digest when the decision was made), `subject_type` (`finding` by control ID, `deficiency` by deficiency ID, `engagement`), `subject_id`, `decision_type`, `values` (strings, validated per type), `rationale`, `decided_by`, `identity_source`, `decided_at` and `supersedes`. The AI-drafted artifact models are unchanged, so the crews never see a decision field. Only `AuditFlow.record_decision` appends; validation and policy are in `swarm.review_decisions.build_decision`.

| Type | Subject | Values | Rationale |
|---|---|---|---|
| `sign_off` | finding | none | optional |
| `challenge` | finding | optional `tod_conclusion` / `toe_conclusion` (see provisional 3) | required |
| `scope_limitation` | finding concluded Not tested | none | required (the limitation as reported) |
| `classify` | deficiency | `classification` on the report's scale; `likelihood` / `magnitude` default to the draft's; the Material Weakness rule applies | required when it differs from the draft |
| `writeup` | deficiency | `criteria`, `condition`, `cause`, `effect`, `recommendation` | optional |
| `management_response` | deficiency | `text`, `agreement` (agree / partial / disagree), `received_from`, `received_on`; `action_owner_role` and `target_date` for agree / partial | required for disagree (the auditor's rebuttal) |
| `engagement_conclusion` | engagement | `conclusion`: Satisfactory / Needs improvement / Unsatisfactory | required |

**Decision (states and the effective view).** Each subject has slots: a finding has a review slot (`sign_off` or `challenge`) and a `scope_limitation` slot; a deficiency has `classify`, `writeup` and `management_response` slots; the engagement has one conclusion slot. A decision is *superseded* once a later decision names it, *stale* once its artifact no longer matches its `draft_digest` (the phase was returned for rework and redrafted), and otherwise *active*. A slot holds at most one active decision: recording another without `supersedes` is refused, and `supersedes` must name the slot's active decision. The effective view (`swarm.review_decisions.effective_view`) is the draft plus the active decision per slot; it is the only thing the report, the working-papers spreadsheet, the OSCAL export and the API's `effective` block render. It also lists what each gate still needs, and the reviewer change rate.

**Decision (API).** `POST /api/sessions/{id}/decisions` appends one decision (201, the stored decision with `state`); 409 for a status in which the type cannot be recorded, a preparer excluded by policy, a conclusion change that needs rework, or a supersede conflict; 422 for an unknown type or subject, invalid values or a missing rationale. `GET /api/sessions/{id}/decisions` returns every decision with its state and the effective view. `GET /api/sessions/{id}` adds `review_decisions`, `effective` and `review_decisions_required`. A gate approval refused for missing decisions returns 409 with `detail` (a string, as before) and `missing_decisions` (`gate`, `subject_type`, `subject_id`, `required`, `reason`).

**Decision (gate preconditions).** Audits created through the API set `review_decisions_required`, also recorded in the chained `audit_created` entry so editing the stored flag does not lift it. For them, Gate 2 needs a sign-off or challenge on every finding whose draft result is Exception and every key-control finding; Gate 3 needs a classification of every deficiency, a scope limitation for every key control whose conclusion of record is Not tested, and the engagement conclusion. They are checked after the existing SoD and evidence checks. Audits created before this change, and flows built directly in Python without the flag (the offline eval harness's synthetic reviewer), keep the previous gate behaviour.

**Decision (trail and digests).** Each decision appends a `review_decision` entry carrying `decision_id`, `decision_digest` (SHA-256 over the decision's canonical JSON), `phase`, `decision_type`, `subject`, `artifact`, `identity_source` and any `supersedes`. A Gate 2 or Gate 3 approval also records `decisions_digest` and `decisions_count` over that phase's decisions recorded so far. `verify_trail` (now given the decisions) reports a decision that no longer matches its entry, one with no entry, or an entry whose decision is gone (`decision_changed`, listed in `decisions_changed`), and a gate whose sealed decisions changed (`artifact_changed`, in `changed_since_approval`). Management responses recorded after COMPLETED are not in the Gate 3 seal, by design. `artifact_digest` still covers the artifact alone, so `artifact_changed` keeps its meaning for the drafts, and trails without the new fields verify as before.

**Decision (exports).** The report opens with the engagement conclusion, headed "Deficiency Evaluation (conclusion of record)" when every deficiency is classified by a reviewer ("… where decided" with undecided rows marked "proposed"; the old "(proposed)" wording when none is), shows each reviewer classification with the declared identity and date and "AI draft: Medium" when it differs, then the five-part write-ups and management responses (with the auditor's rebuttal on a disagreement), the per-finding sign-off table and the scope limitations, and lists each decision in the trail. The working-papers spreadsheet shows the conclusions of record plus the review status, reviewer, rationale, the AI draft where it differs and the scope limitation. In OSCAL, a risk's `classification` prop is the conclusion of record, with `classification-source` (reviewer / ai-draft), `ai-draft-classification` always, `decided-by`, `decided-at`, `identity-source` and `decision-id`, and a party origin for the reviewer; the write-up's recommendation is a `recommendation` remediation, an agreed management response a `planned` remediation with the target date as the risk deadline, and every response a risk-log entry. Observations and findings carry `reviewer-sign-off`, `reviewed-by` and `scope-limitation`; a withdrawn conclusion keeps the draft in `ai-draft-*` props and no longer produces a finding. The engagement conclusion is a result attestation. The document still validates against NIST's schema (tested).

**Decision (DEMO_MODE).** The demo never records a decision: its gates wait for a person. `swarm.demo.demo_review_decisions` holds the example decisions of the scripted walk-through, and `scripts/demo_walkthrough.py` posts them through the API (acting as the reviewer) and writes the four exports, so `docs/sample-run/` can be regenerated.

**Provisional — awaiting owner confirmation.** Each is a named constant or function in `src/swarm/review_policy.py`.

1. Option B, append-only, drafts never change. *Alternative:* A (fields on the artifacts) or C (reviewer edits with diffs).
2. A reviewer may change a classification without rework; a rationale is required when it differs from the draft (`classification_differs`). *Alternative:* only by return for rework.
3. Without rework a ToD/ToE conclusion may only be withdrawn from "Effective" to "Not tested" (`conclusion_change_allowed`, `CONCLUSION_DOWNGRADE_FROM`); withdrawing "Ineffective" or "Exceptions noted" would remove an exception without new work, so it needs rework, as does any other change. *Alternative:* allow changes with an evidence reference.
4. Before Gate 2, every Exception and every key-control finding needs a sign-off or challenge (`GATE_2_REVIEW_EXCEPTIONS`, `GATE_2_REVIEW_KEY_CONTROLS`). *Alternative:* all findings, or optional.
5. Classifications are recorded at Gate 3 by anyone except the preparer; every deficiency needs one (`GATE_3_CLASSIFY_EVERY_DEFICIENCY`); the Gate 3 approver still differs from the Gate 2 approver. *Alternative:* only the Gate 3 approver may classify.
6. An engagement conclusion is required before Gate 3 on the scale Satisfactory / Needs improvement / Unsatisfactory, with a rationale (`GATE_3_ENGAGEMENT_CONCLUSION`, `ENGAGEMENT_CONCLUSION_SCALE`). *Alternative:* optional, or no scale.
7. Management responses are transcribed by the auditor with `received_from` / `received_on`; no new state; recordable at Gate 3 and after COMPLETED (`DECISION_RECORDABLE_STATUSES`). *Alternative:* a `MANAGEMENT_RESPONSES` state before COMPLETED, or an authenticated auditee role.
8. A disagreement needs the auditor's rebuttal in the rationale; both are shown (`management_response_needs_rebuttal`). *Alternative:* escalation workflow.
9. The five-part write-up is per deficiency and written by a person; no AI drafting in this change. *Alternative:* per finding, or AI-drafted for confirmation.
10. Exports show the conclusion of record and the AI draft alongside when they differ (`SHOW_AI_DRAFT_WHEN_DIFFERENT`). *Alternative:* record only.
11. A decision can be superseded while its type is still recordable: phase-2 decisions until Gate 2 is approved, phase-3 decisions until COMPLETED, management responses afterwards too (`DECISION_RECORDABLE_STATUSES`). This is stricter than "until COMPLETED" for sign-offs so that a Gate 2 approval seals them. *Alternative:* allow post-approval corrections and report them as changes.
12. Built now with `identity_source: "declared"` on every decision (`DEFAULT_IDENTITY_SOURCE`); per-user authentication will set `authenticated`. *Alternative:* wait for authentication. *Update:* ADR-012 sets `authenticated` when per-reviewer tokens are configured.
13. The reviewer change rate (share of reviewed findings and classified deficiencies where the reviewer challenged or changed the draft) is computed per session and exposed in `effective.reviewer_change_rate`, not published as a claim (`REVIEWER_CHANGE_RATE_PUBLISHED = False`). *Alternative:* publish it from real runs.
14. The preparer may not record `sign_off`, `challenge`, `classify`, `scope_limitation` or `engagement_conclusion` (`PREPARER_EXCLUDED_DECISIONS`); they may draft a write-up and transcribe a management response. *Alternative:* exclude the preparer from all decisions.
15. A key control whose conclusion of record is Not tested needs a `scope_limitation` decision before Gate 3 (`GATE_3_SCOPE_LIMITATION_FOR_UNTESTED_KEY_CONTROLS`); added because the demo already told the auditor to decide this before Gate 3. *Alternative:* optional.

**Consequences.**
- The report of a completed audit states the reviewer's conclusions, marked as the reviewer's, instead of "proposed" drafts, and a reader can still see what the AI proposed.
- Two sources of truth: every renderer must use the effective view. The report, spreadsheet, OSCAL export and API all do, and the tests check each.
- Identities are still declared (ADR-004, ADR-008, ADR-009) unless per-reviewer tokens are configured (ADR-012): a decision records who someone said they were. The SoD rules stop honest mistakes and make self-review visible; they do not stop someone typing another name.
- The trail makes an edited, removed or injected decision detectable, with the same limits as ADR-009 (an editor who can rewrite the sessions file and the anchor, and holds the key or none is set, can recompute everything).
- The eval harness (`evals/pipeline.py`) builds its flows without the flag, so its synthetic reviewer still approves gates without decisions and the model metrics are unchanged. Follow-up: have the synthetic reviewer record decisions, which would let the harness measure the reviewer change rate too.
- The existing UI does not yet record decisions; the API contract above is what it will build on. `scripts/capture_screenshots.mjs` still only approves gates, so on API-created audits it now stops at Gate 2 until it records sign-offs.

---

## ADR-012: Opt-in per-reviewer tokens

**Status:** Accepted (opt-in; off by default).

**Context.** One shared API token guards the API (ADR-008), so every reviewer identity is typed by the caller and the segregation-of-duties rules (ADR-009, ADR-011) compare typed names. Anyone who can reach the UI can approve a gate or sign off a finding under a colleague's name. A full identity provider (OIDC single sign-on through the reverse proxy) is the proper fix, but it needs infrastructure this project does not ship. This record adds the smallest step that ties an action to a person the operator issued a credential to.

**Decision (tokens file and CLI).** `REVIEWER_TOKENS_FILE` names a JSON file: `{"version": 1, "reviewers": [{"name", "token_hash", "created_at"}]}`. `python -m api.reviewer_tokens add "Name"` (`src/api/reviewer_tokens.py`) generates a token with `secrets.token_urlsafe(32)` (256 bits, prefixed `grcrt_`), prints it once and writes only `sha256:<hex>` of it, atomically and with mode 0600; `--replace` rotates, `remove` revokes, `list` prints names only. The file is validated as a whole: a wrong version, a blank or over-long name, two names equal after case and whitespace normalisation (the SoD checks could not tell them apart), a malformed digest or a digest under two names makes it unusable. The API re-reads it when its mtime or size changes.

**Decision (hashing).** A plain SHA-256 of the token, with no salt and no server key. A salt or a slow KDF protects low-entropy secrets (passwords) from dictionary and precomputed attacks; these tokens are 256 bits from the OS CSPRNG, so a leaked digest cannot be inverted or matched against a precomputed table either way. An HMAC keyed by a server secret would only add protection against someone who can read the file but not the key; in this deployment both sit in the same container and environment, and the key would become one more secret to rotate, with every token invalidated when it changes. The `sha256:` prefix leaves room for another scheme later. Lookup hashes the presented token and compares it with every stored digest using `hmac.compare_digest`, without stopping at a match, so neither the timing nor the response depends on which names exist; the request carries only the token, never a name to look up.

**Decision (API contract).** The personal token arrives in the `X-Reviewer-Token` header, in addition to the shared API token (nginx still injects the shared one; the reviewer's own passes through). With the file configured, these routes require it: `POST /api/sessions` and `/api/sessions/with-document` (the preparer), `PATCH …/approve`, `POST …/return`, `POST …/retry`, `POST …/qa-override` and `POST …/decisions`. The token's name replaces the typed `prepared_by` / `human_id` / `decided_by`, which may be omitted; a typed name that names someone else (compared ignoring case and spacing) is refused with 403 rather than silently replaced, so a client that shows one name and sends another finds out. Refusals are JSON with `detail` (a string, as elsewhere) and `code`: 401 `reviewer_token_missing`, 401 `reviewer_token_invalid`, 403 `reviewer_name_mismatch`, 429 `reviewer_token_rate_limited` (with `Retry-After`; 10 invalid tokens from one client address within 60 seconds, in memory), 503 `reviewer_tokens_unavailable` (the file is set but missing or malformed; the API never falls back to declared names). The shared token is checked first, so a missing API token is still a 401 with `WWW-Authenticate: Bearer`. `GET /api/config` gains `reviewer_tokens` (bool) and `GET /api/reviewer` returns `{name, identity_source}` for the presented token so a client can check it. Without the file, the header is ignored and behaviour is unchanged, except that a missing name is now reported by the flow (422, as before) rather than by request validation (also 422).

Preparers need a token too: taking the preparer from the token closes the gap in which a reviewer could create an audit under someone else's name and then approve it themselves. Reads, exports, evidence, imports and deleting an unapproved draft need only the shared token.

**Decision (record).** `AuditFlow`'s reviewer methods (`record_preparer`, `begin_phase_2/3`, `finalize_audit`, `retry_phase`, `return_for_rework`, `override_qa_rejection`, `record_decision`) take `identity_source` (`declared` by default, or `authenticated`). It is stored on each `ReviewDecision` (the field existed since ADR-011) and, new here, on every trail entry a person causes. The entry hash covers every field, so changing `identity_source` afterwards is detected as an edited entry. Trails written before the field existed have entries without it; they verify as before and read as declared, and a trail may mix both. Exports say which: the report's trail lines mark authenticated entries, the report and spreadsheet notes describe identities as declared, authenticated or mixed according to the decisions shown, the spreadsheet's reviewer column is headed accordingly, and in OSCAL each assessment-log entry carries an `identity-source` prop and each party's remarks say how its identity was established.

**Alternatives considered.**
- OIDC single sign-on at the reverse proxy, passing a verified identity header: the right answer for a shared deployment, but it needs an identity provider and proxy configuration this repository does not include.
- Mutual TLS client certificates: strong, but certificate issuance and browser installation are heavy for the intended single-team use.
- HMAC-keyed or salted slow hashes: see "Decision (hashing)".
- Keep declared identities only: leaves impersonation by typing a name as the easiest attack.

**Consequences.**
- With tokens on, a reviewer cannot act under a colleague's name by typing it, and the SoD checks compare issued identities.
- It is not single sign-on, has no MFA, and a token is a long-lived bearer secret: whoever holds it acts as that reviewer until it is rotated or removed. How the browser stores it is the UI's choice.
- Anyone who can write the tokens file, or run the CLI on the server, can issue a token under any name. Server access remains the ability to impersonate reviewers.
- The rate limit is per client address and per process: behind the Compose nginx every browser shares one address, so one client sending bad tokens delays everyone for up to a minute. The tokens' entropy, not the limit, is what keeps them from being guessed.
- Tokens cross the network in a header on every reviewer action; beyond the default `127.0.0.1` binding the deployment needs TLS.
- Identities recorded before tokens were enabled remain declared, and the record says so per entry.
