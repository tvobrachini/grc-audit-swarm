# Integrations: importing existing security-tool findings

Fieldwork's Evidence Collector does not only run its own boto3 reads
(password policy, IAM users' MFA, S3 public access — see
[Architecture](ARCHITECTURE.md)). It can also import findings that scanning
tools an audit client already runs have produced: **Prowler** and **AWS
Security Hub**. Both are read-only, both go through the same evidence vault
as every other tool (account-ID redaction, an integrity digest, exact-quote
verification), and both are additive — nothing here changes how the
existing AWS tools work.

## What each import is, and is not, evidence of

A Prowler check or a Security Hub finding records what that tool observed at
one point in time: the moment the scan ran, or the moment
`securityhub:GetFindings` was called. That is **test-of-design and
implementation evidence** — it shows a control was (or was not) configured
correctly when read. It is **not test-of-operating-effectiveness evidence
for a period** by itself: a single `PASS`/`PASSED` does not show the control
held for every day of the engagement's period of reliance, any more than a
single AWS API read does elsewhere in this project. The field auditor's
prompt (`src/swarm/config/fieldwork_tasks.yaml`) already carries this rule
for every evidence tool; the two importers below say it again, in the text
itself, so it goes wherever a vault ID and its quote travel.

Severities (Prowler's `severity`, Security Hub's `Severity.Label`) and
pass/fail results are the scanning tool's own classification. They are not
an audit deficiency rating — that classification (control deficiency /
significant deficiency / material weakness, or the risk-rating scale for a
non-ICFR engagement) is made later, by the field auditor and by Reporting's
deficiency evaluation, from the RACM's test steps and expected results, not
from a scanner's own severity label.

## Prowler

**What is read.** A Prowler JSON findings file, from the path in the
`PROWLER_FINDINGS_PATH` environment variable. Nothing here runs Prowler or
shells out to it — it only parses a file the client's own Prowler run (or
CI pipeline) already wrote to disk (or a Docker/EBS volume mounted at that
path). If the variable is not set, the "Import Prowler Findings" tool says
so and registers nothing.

**Formats tested.** Both of Prowler's JSON shapes are a JSON array of
finding records, and the importer tells them apart per record (so a file
that mixes both, or has a few malformed entries, still gets the well-formed
records):

- **OCSF** (Open Cybersecurity Schema Framework) Detection Finding — the
  default `--output json-ocsf` / `-M json-ocsf` format in Prowler 4.x and
  5.x. Fields read: `status_code` (`PASS`/`FAIL`/`MANUAL`, falling back to
  `status`), `severity`, `metadata.event_code` (the check ID, falling back
  to `finding_info.uid`), `finding_info.title`, `status_detail`,
  `resources[0].uid` / `resources[0].region` (further resources are counted,
  not each listed), `metadata.product.version` (Prowler's own version, if
  present), and `unmapped.compliance` (a framework → requirement-ids
  mapping, flattened to text).
- **Legacy** `--output json` (Prowler 3.x) — a flat record per check:
  `CheckID`, `CheckTitle`, `Status`, `StatusExtended`, `Severity`, `Region`,
  `ResourceArn`/`ResourceId`, and `Compliance` (same framework → requirement
  mapping shape as OCSF's `unmapped.compliance`).

These field names come from Prowler's public OCSF and JSON output
documentation and hand-built sample files (`tests/fixtures/findings/`), not
a captured run of a real Prowler scan — there is no live Prowler instance in
this repository's test or CI environment. If a real export uses field names
that differ from what is read here (a schema revision, a custom OCSF
mapping, a very old or very new Prowler version), the affected records are
skipped and counted in the summary's "unparseable records" list rather than
silently dropped or guessed at; they never cause the whole import to fail.

**Limits.** JSON only, and the extension is checked (`.json`). Up to 10 MB
(`MAX_PROWLER_FILE_BYTES` in `src/swarm/tools/findings_checks.py`); a larger
or empty file is reported as an error, not silently truncated. The registered
summary lists up to 300 individual findings verbatim (grouped and counted
by check ID first); beyond that it says how many more exist rather than
growing without bound — the per-status and per-check counts always cover
every finding, listed or not.

**Where it runs.**

- As a CrewAI tool, "Import Prowler Findings" (`src/swarm/tools/findings_tools.py`),
  available to the Fieldwork Evidence Collector alongside the AWS reads.
- As an API upload, `POST /api/sessions/{id}/imports/prowler`
  (`src/api/routers/imports.py`): multipart-upload a `.json` file for an
  existing session and get back the vault ID and the exact summary text.
  Same size limit and parsing, requires the same `API_AUTH_TOKEN` as every
  other `/api/*` route, and does not touch the session's flow state, RACM or
  working papers — it only writes one evidence-vault record, for a reviewer
  or the Evidence Collector to cite.

## AWS Security Hub

**What is read.** One read-only call, `securityhub:GetFindings`, paginated,
filtered by default to:

- `RecordState` = `ACTIVE`
- `WorkflowStatus` != `SUPPRESSED`

and optionally narrowed further by three environment variables:
`SECURITYHUB_PRODUCT_NAME`, `SECURITYHUB_GENERATOR_ID`,
`SECURITYHUB_COMPLIANCE_STATUS` (each becomes an `EQUALS` filter).
`SECURITYHUB_MAX_FINDINGS` (default 1000) bounds how many findings are read
across pages; if the account has more matching findings than that, the
summary says the read was truncated instead of reporting a partial count as
complete.

**Fields read (AWS Security Finding Format / ASFF).** `Id`, `Title`,
`Compliance.Status` (`PASSED`/`FAILED`/`WARNING`/`NOT_AVAILABLE`),
`Severity.Label`, `RecordState`, `Workflow.Status` (falling back to the
deprecated `WorkflowState`), `GeneratorId`,
`ProductFields["aws/securityhub/ProductName"]` (falling back to the last
segment of `ProductArn`), `Resources[0].Id` / `Resources[0].Region` (further
resources counted, not each listed), `Description`, and
`Compliance.RelatedRequirements`.

**IAM permission.** `securityhub:GetFindings` only — see the minimal
read-only policy in [Security and data handling](SECURITY-AND-DATA.md). This
tool calls no other Security Hub or Config API, and never calls
`BatchUpdateFindings` or anything else that would change a finding's state
or workflow.

**Where it runs.** As a CrewAI tool, "Get Security Hub Findings"
(`src/swarm/tools/findings_tools.py`), available to the Fieldwork Evidence
Collector. There is no upload/API route for Security Hub — the account
being audited is already reachable via the same credentials the other AWS
tools use.

## Testing notes

- Parsing logic (`src/swarm/tools/findings_checks.py`) is pure Python with
  no AWS or CrewAI dependency, tested directly in
  `tests/test_findings_checks.py` against hand-built fixtures in
  `tests/fixtures/findings/`: an OCSF sample and a legacy Prowler sample
  (each with PASS/FAIL/MANUAL, multiple regions, AWS account IDs to redact,
  and a deliberately malformed record), and an ASFF sample (a FAILED and a
  PASSED finding, plus a malformed record with no `Id`).
- The CrewAI tool wrappers (`tests/test_findings_tools.py`) exercise vault
  registration, redaction, and env-var wiring, and use
  `botocore.stub.Stubber` for Security Hub rather than moto: moto's
  Security Hub support does not cover `GetFindings` pagination in the
  version this project pins, while `Stubber` lets every filter, page and
  error case be asserted exactly (the same approach
  `tests/test_aws_tools.py` already uses for the S3/IAM tools).
- The upload route is tested end-to-end in `tests/test_api_imports.py`
  (auth, unknown session, oversized/malformed/non-UTF-8 uploads, and the
  declared-Content-Length rejection in `src/api/main.py`'s upload-size
  middleware).

## What this does not do

- It does not run Prowler, install it, or manage Security Hub's own
  configuration (enabling standards, ingesting other integrations' findings
  into it, etc.) — both are read-only consumers of output the client's own
  tooling already produces.
- It does not deduplicate a finding that shows up from both an AWS tool
  read and a Prowler/Security Hub import for the same control; each import
  is registered as its own vault record, and it is the field auditor's job
  (per the fieldwork prompts) to decide which evidence best supports which
  control's test steps.
- It does not classify severity or decide test-of-operating-effectiveness
  for you — see "What each import is, and is not, evidence of" above.
