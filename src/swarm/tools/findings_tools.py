"""CrewAI tools that import existing security-tool findings as evidence.

Two read-only sources, both parsed by ``swarm.tools.findings_checks``:

* **Prowler** — a JSON findings file (OCSF or legacy format) at
  ``PROWLER_FINDINGS_PATH``. Nothing here runs Prowler; it only reads a file
  someone already produced with it.
* **AWS Security Hub** — a read-only ``securityhub:GetFindings`` call,
  filtered to active, non-suppressed findings (optionally narrowed further
  by ``SECURITYHUB_PRODUCT_NAME`` / ``SECURITYHUB_GENERATOR_ID`` /
  ``SECURITYHUB_COMPLIANCE_STATUS``).

Both tools register the exact summary text they return in the evidence
vault (see ``swarm.evidence.EvidenceAssuranceProtocol.register_evidence``),
with account IDs redacted and non-sensitive collection metadata attached —
the same pattern as ``swarm.tools.aws_tools``. The metadata is folded into
the vault record's integrity digest, so quote checks fail if it is edited
after the fact.

These tools do not, on their own, prove anything about operating
effectiveness: see ``HONESTY_NOTE`` in ``swarm.tools.findings_checks`` and
``docs/INTEGRATIONS.md``.
"""

from __future__ import annotations

import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from crewai.tools import tool

from swarm.evidence import EvidenceAssuranceProtocol, _redact_account_ids
from swarm.evidence import app_version as _app_version
from swarm.tools.findings_checks import (
    FindingsImportError,
    build_prowler_summary,
    build_securityhub_filters,
    build_securityhub_summary,
    check_prowler_file_size,
    collect_securityhub_findings,
    parse_asff_records,
    parse_prowler_findings,
)

_APP_VERSION = _app_version()

MAX_PROWLER_PATH_LENGTH = 4096


def _boto_client(service: str):
    return boto3.client(service)


def _region_of(client) -> str | None:
    try:
        region = client.meta.region_name
    except AttributeError:
        return None
    return region if isinstance(region, str) else None


def describe_error(exc: BaseException) -> str:
    """Short, account-ID-redacted description of a boto error (see
    ``swarm.tools.aws_checks.describe_error``, duplicated here so this
    module has no import-time dependency on the AWS evidence tools)."""
    if isinstance(exc, ClientError):
        err = exc.response.get("Error", {})
        code = err.get("Code", "Unknown")
        message = err.get("Message", "")
        text = f"{code}: {message}" if message else code
    else:
        text = f"{type(exc).__name__}: {exc}"
    return _redact_account_ids(text)


def _collection_metadata(
    tool_name: str,
    operation: str,
    *,
    region: str | None = None,
    parameters: dict | None = None,
) -> dict:
    metadata: dict = {
        "tool": tool_name,
        "operation": operation,
        "app_version": _APP_VERSION,
        "region": region,
    }
    if parameters:
        metadata["parameters"] = parameters
    return metadata


def _register_and_format(raw_output: str, source: str, *, metadata: dict) -> str:
    vault_record = EvidenceAssuranceProtocol.register_evidence(
        raw_output, source, metadata=metadata
    )
    return f"Vault ID: {vault_record['vault_id']}\nRaw Output: {_redact_account_ids(raw_output)}"


# ─── Prowler ──────────────────────────────────────────────────────────────────


@tool("Import Prowler Findings")
def import_prowler_findings(context: str = "") -> str:
    """Reads a Prowler JSON findings file (current OCSF format or the older
    flat "json" format) from the path in the PROWLER_FINDINGS_PATH
    environment variable, and registers a compact, quotable summary
    (findings grouped by check ID, with status/severity/region/resource and
    compliance mappings) as evidence. A PASS/FAIL is Prowler's own
    classification of what it found at scan time — configuration and
    implementation evidence, not proof of operating effectiveness over a
    period. If PROWLER_FINDINGS_PATH is not set, says so and does not
    register anything (there is nothing to import)."""
    path = os.environ.get("PROWLER_FINDINGS_PATH")
    if not path:
        return (
            "Prowler findings import skipped: PROWLER_FINDINGS_PATH is not "
            "set, so there is no findings file to import."
        )
    if len(path) > MAX_PROWLER_PATH_LENGTH:
        raw_output = (
            "Error importing Prowler findings: configured path is implausibly long."
        )
    elif not path.lower().endswith(".json"):
        raw_output = (
            "Error importing Prowler findings: PROWLER_FINDINGS_PATH must "
            "point to a .json file (Prowler's JSON or OCSF-JSON output)."
        )
    else:
        try:
            size = os.path.getsize(path)
            check_prowler_file_size(size)
            with open(path, "r", encoding="utf-8") as f:
                raw_text = f.read()
            result = parse_prowler_findings(raw_text)
        except OSError as exc:
            raw_output = f"Error reading Prowler findings file: {exc.strerror or exc}"
        except FindingsImportError as exc:
            raw_output = f"Error importing Prowler findings: {exc}"
        else:
            raw_output = build_prowler_summary(result)

    source = "prowler.findings_import"
    metadata = _collection_metadata(
        "import_prowler_findings",
        "local_file_read",
        parameters={"source_path_basename": os.path.basename(path)},
    )
    return _register_and_format(raw_output, source, metadata=metadata)


# ─── AWS Security Hub ─────────────────────────────────────────────────────────


@tool("Get Security Hub Findings")
def get_securityhub_findings(context: str = "") -> str:
    """Reads active, non-suppressed AWS Security Hub findings via the
    read-only securityhub:GetFindings API (paginated), optionally narrowed
    by the SECURITYHUB_PRODUCT_NAME, SECURITYHUB_GENERATOR_ID and
    SECURITYHUB_COMPLIANCE_STATUS environment variables, and registers a
    compact, quotable summary as evidence. A PASSED compliance status is
    what the finding's generator reported at read time — configuration and
    implementation evidence, not proof of operating effectiveness over a
    period."""
    region = None
    filters = build_securityhub_filters(
        product_name=os.environ.get("SECURITYHUB_PRODUCT_NAME"),
        generator_id=os.environ.get("SECURITYHUB_GENERATOR_ID"),
        compliance_status=os.environ.get("SECURITYHUB_COMPLIANCE_STATUS"),
    )
    try:
        max_findings = int(os.environ.get("SECURITYHUB_MAX_FINDINGS", "1000"))
    except ValueError:
        max_findings = 1000
    try:
        client = _boto_client("securityhub")
        region = _region_of(client)
        raw_findings, truncated = collect_securityhub_findings(
            client, filters, max_findings=max_findings
        )
        parsed = parse_asff_records(raw_findings)
        raw_output = build_securityhub_summary(
            parsed, filters=filters, truncated=truncated
        )
    except (ClientError, BotoCoreError) as e:
        raw_output = f"Error fetching Security Hub findings: {describe_error(e)}"

    source = "aws.securityhub.get_findings"
    metadata = _collection_metadata(
        "get_securityhub_findings",
        "securityhub:GetFindings",
        region=region,
        parameters={"filters": filters, "max_findings": max_findings},
    )
    return _register_and_format(raw_output, source, metadata=metadata)
