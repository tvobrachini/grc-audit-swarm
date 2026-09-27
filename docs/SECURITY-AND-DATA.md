# Security and data handling

- **Read-only AWS access.** The tools call only read APIs, and the policy below is all they need. The agents' prompts also say not to change anything, but the IAM policy and the tool code are what enforce read-only access.
- **Redaction.** 12-digit numbers in AWS account-ID form (`123456789012` or `1234-5678-9012`) are replaced with `[REDACTED]` before evidence is stored and before tool output is returned to the agents. Any other standalone 12-digit number is redacted as well. Evidence metadata (region, API operation, tool name, app version, caller ARN) is redacted the same way before it is stored.
- **Evidence vault.** One JSON file per evidence record, with a SHA-256 digest covering the payload and, when present, the collection metadata. With `VAULT_ENCRYPTION_KEY` set, records are Fernet-encrypted and carry an HMAC-SHA256 digest keyed from that key. See [Limitations](LIMITATIONS.md) for what this does and does not detect.
- **API token.** Every `/api/*` route requires `API_AUTH_TOKEN` (the API returns 503 if it is not set, and 401 for a wrong or missing token). `/health` is open. In Compose, nginx injects the token server-side.
- **Deployment defaults.** Compose publishes ports on `127.0.0.1` only. The API container runs as a non-root user. nginx sets a Content-Security-Policy and other hardening headers. The API refuses to start with `DEMO_MODE=1` when `ENVIRONMENT` is `production` or `staging`.
- **Untrusted input.** Scope documents are size-limited and wrapped as untrusted content — see [Architecture](ARCHITECTURE.md#how-it-works).
- **Reviewer identity: declared by default, per-reviewer tokens optional.** By default the API uses one shared token, not per-person accounts: the name recorded against a gate action or decision is whatever the reviewer typed (`identity_source: "declared"`), and the segregation-of-duties checks (preparer vs. approver, Gate 2 vs. Gate 3 approver, preparer vs. reviewer decisions) compare typed names only. They catch accidental self-review and make it visible in the trail, but they do not stop someone who deliberately types a different name. With `REVIEWER_TOKENS_FILE` set, audit creation and every reviewer action also need the person's own token in `X-Reviewer-Token`; the name comes from the token (`identity_source: "authenticated"`) and a typed name that differs is refused. See [Per-reviewer tokens](#per-reviewer-tokens) for what that does and does not give you.

<details>
<summary>Minimal read-only AWS IAM policy</summary>

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "GRCAuditSwarmReadOnlyEvidenceCollection",
      "Effect": "Allow",
      "Action": [
        "iam:GetAccountPasswordPolicy",
        "iam:ListUsers",
        "iam:ListMFADevices",
        "s3:ListAllMyBuckets",
        "s3:GetAccountPublicAccessBlock",
        "s3:GetBucketPublicAccessBlock",
        "s3:GetBucketPolicyStatus",
        "s3:GetBucketAcl",
        "securityhub:GetFindings"
      ],
      "Resource": "*"
    }
  ]
}
```

These map to the boto3 calls in `src/swarm/tools/aws_checks.py`: `iam.get_account_password_policy`, `iam.list_users`, `iam.list_mfa_devices`, `s3.list_buckets`, `s3control.get_public_access_block` (account-level Block Public Access), `s3.get_public_access_block`, `s3.get_bucket_policy_status` and `s3.get_bucket_acl`. The S3 check also calls `sts.get_caller_identity` to get the account ID for the S3 Control call, and (since generation provenance was added) to record the caller's ARN as evidence metadata with the account ID redacted; that call needs no IAM permission of its own. If one of the S3 read permissions is missing, the affected buckets are reported as UNKNOWN (with the reason) rather than public or not public. `securityhub:GetFindings` is the one call `src/swarm/tools/findings_tools.py` makes for the "Get Security Hub Findings" tool (paginated, filtered to active/non-suppressed findings) — see [Integrations](INTEGRATIONS.md). The Prowler import reads a local JSON file and calls no AWS API at all. The separate `aws_safety_heartbeat.py` script (a cost check for a lab account) also uses `sts:GetCallerIdentity`, `ec2:DescribeInstances` and `rds:DescribeDBInstances`, which the audit itself does not need.

</details>

## Approval trail: hash chain and anchor file

Each trail entry is hash-chained to the one before it (`prev_hash`, `entry_hash`); with `VAULT_ENCRYPTION_KEY` set, the chain uses HMAC-SHA256 keyed from it. `GET /api/sessions/{id}/trail/verify` recomputes the chain and detects an edited entry, reordered entries, a removed entry other than the last one, and an approved artifact that changed after approval. With the key, it also detects someone who recomputed every hash without knowing it.

What the chain alone cannot detect: entries cut from the *end* of the trail. A chain that has had its last N entries deleted, with the remaining entries left otherwise untouched, still verifies — there is nothing after the last entry to say more should exist. That is what the anchor file is for: `TRAIL_ANCHORS_PATH` records, separately, the entry count and last hash per audit. `trail/verify` compares the live trail against its anchor and flags a mismatch.

The anchor only helps if it is genuinely harder to edit than the sessions file. If the same process or person can write both files with the same privileges, they can truncate the trail and update the anchor to match, and nothing will detect it. **This repository does not configure that separation for you** — by default both files can live on the same volume with the same write permissions, which gives no more protection than the hash chain alone.

**Where to keep the trail anchor file.** Some options, roughly in order of how much they raise the bar for the API process itself to also be the one tampering:

- **A separate volume or host path with different write permissions.** Mount `TRAIL_ANCHORS_PATH` from a path the API's runtime user can write but cannot later remove or overwrite outside of appending (for example, a directory owned by a different user, writable via a small privileged helper, or a filesystem mounted append-only). This protects against an operator or compromised process editing the sessions file and quietly rewriting the anchor to match, since they would need separate access to also change the anchor. It does not protect against someone with access to both paths, and it needs you to actually configure the separate ownership or mount — the app itself has no way to enforce it.
- **An S3 bucket with Object Lock (or another write-once / WORM store).** Write each anchor update as a new object version to a bucket with Object Lock in compliance or governance mode (or an equivalent write-once location your infrastructure provides). This protects against retroactive edits to *past* anchors, including by someone with API and filesystem access, because the lock is enforced by the storage layer, not the application. It does not stop someone from simply not shipping a new anchor for a truncated trail — you would need to notice a gap in anchor history, not just check the latest one. The app writes a plain local file today; wiring it to object storage is infrastructure you would add.
- **Do nothing (the default).** Fine for a demo or a low-stakes personal engagement, and it still gives the tamper-evidence the hash chain provides for edits that don't also touch the end of the trail. Not sufficient if you actually need to defend against an end-of-trail truncation attack from someone with the same access as the API process.

None of these are configured by this repository out of the box; they are deployment choices for whoever runs it for something that matters.

## What the trail does and does not detect

It detects: an edited entry's content, entries reordered, an entry removed from the middle, and an artifact that changed after it was approved (via digest mismatch). With `VAULT_ENCRYPTION_KEY` set, it also detects a rebuild of the whole chain by someone who does not have the key.

It does not detect: entries removed from the end (without an anchor file kept separately, as above); a person typing someone else's name in the reviewer field when per-reviewer tokens are not configured (the entry then says `identity_source: "declared"`), or, when they are, someone using another reviewer's token or issuing a token under someone else's name; or that a human genuinely read and considered the artifact before approving it — the trail records that an approval action happened, not the quality of the review behind it.

## Vault crypto and token handling

Without `VAULT_ENCRYPTION_KEY`, evidence records are plain JSON with a SHA-256 digest: this detects accidental corruption, not deliberate tampering, since anyone who can write the file can recompute a matching digest. With the key set, records are Fernet-encrypted and digests are HMAC-SHA256 keyed from it: editing a record without the key is now detectable, but deleting a record, or replacing it with an unencrypted one, is not — there is nothing to compare against once the record is simply gone.

`API_AUTH_TOKEN` is one shared bearer token for every `/api/*` route; it identifies a client as authorized, not a specific person. `VITE_API_AUTH_TOKEN` exists only so `npm run dev` can call a local API directly — it is compiled into the built JavaScript bundle, so it must never be set for a Compose or production build (use nginx's server-side token injection instead, as Compose already does).

## Per-reviewer tokens

Opt-in (`REVIEWER_TOKENS_FILE`, DECISIONS.md ADR-012). The file lists reviewer names, each with the SHA-256 digest of a random 256-bit token; it never holds a token. `PYTHONPATH=src uv run python -m api.reviewer_tokens add "Name"` issues a token, prints it once and stores only the digest (`--replace` rotates it, `remove` revokes it, `list` shows names only). The API re-reads the file when it changes.

When the file is configured:

- Creating an audit, approving a gate, returning work, retrying a phase, overriding a QA rejection and recording a reviewer decision each need a valid `X-Reviewer-Token` header **in addition to** the shared API token. Reading sessions, exports and evidence, deleting an unapproved draft and uploading imports need only the shared token.
- The token's name replaces the typed name (`prepared_by`, `human_id`, `decided_by`). A typed name may be left out; if it is present and names someone else, the request is refused with 403 rather than silently rewritten.
- Decisions and every new trail entry carry `identity_source: "authenticated"`. The entry hash covers it, so changing it later is detected as an edit.
- Refusals: 401 `reviewer_token_missing` or `reviewer_token_invalid`, 403 `reviewer_name_mismatch`, 429 `reviewer_token_rate_limited` (with `Retry-After`) after 10 invalid tokens from one client address in 60 seconds, 503 `reviewer_tokens_unavailable` if the file is missing or malformed. The API never falls back to declared names when the file is set but unusable.
- A token is looked up by its digest against every entry in constant time, so a failed attempt says nothing about which names exist. Tokens are not logged.

What this gives you: the name on an approval or decision is the name the server issued a token to, so a reviewer cannot act under a colleague's name by typing it, and the segregation-of-duties checks compare issued identities instead of typed text.

What it does not give you:

- **Not single sign-on and no MFA.** A token is a long-lived bearer secret. Whoever holds it acts as that reviewer until it is replaced or removed. How the browser keeps it is up to the UI.
- **Whoever can issue tokens can be anyone.** Anyone who can write the tokens file, or run the CLI on the server, can issue a token under any name, including a second identity for themselves under a colleague's name. Protect the file (the CLI writes it with mode 0600) and treat server access as the ability to impersonate reviewers.
- **The rate limit is per client address and per process.** Behind the Compose nginx proxy every browser shares the proxy's address, so one client sending bad tokens makes everyone wait out the window. Tokens are 256-bit random values, so the limit is a brake on noise, not what keeps them from being guessed.
- **Transport.** Compose binds to `127.0.0.1` and serves plain HTTP. If you expose the app further, put TLS in front of it: the token travels in a header on every reviewer action.
- **Earlier entries stay as they were.** Entries and decisions recorded before tokens were configured keep `declared` (or no field, for trails written before the field existed; read those as declared). Verification accepts both; exports label identities by their source.

## Localhost binding

Compose's published ports are bound to `127.0.0.1` only, not `0.0.0.0`, so the API and frontend are not reachable from other hosts on the network by default. Exposing them beyond localhost (a different bind address, a reverse proxy, a cloud load balancer) is a deployment decision this repository does not make for you, and it changes the threat model — the shared bearer token and CORS allow-list were designed assuming a trusted local or single-operator context.

## Untrusted documents

A scope document (PDF or text, up to 5 MB / 30 pages / 20,000 extracted characters) is content a user uploads, not content the project controls. Its extracted text is wrapped in delimiters that label it to the model as untrusted, user-supplied content, which reduces the chance a crafted document changes agent behavior through prompt injection. It does not remove that risk — an LLM can still be influenced by adversarial text inside a delimited block. The human gates, not the delimiters, are the actual control against a bad outcome reaching a report: nothing is issued without a person approving each phase.

To report a vulnerability, see [SECURITY.md](../SECURITY.md).
