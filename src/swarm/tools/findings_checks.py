"""Read-only parsing of third-party security-tool findings, shared by the
CrewAI tools (``swarm.tools.findings_tools``) and the optional upload API
route (``api.routers.imports``).

Two sources are supported:

* **Prowler** JSON output — either the current OCSF ("Open Cybersecurity
  Schema Framework") Detection Finding format Prowler 4/5 write by default,
  or the older flat ``--output json`` format from Prowler 3.x. Both are a
  JSON array of finding records; this module never executes Prowler itself,
  it only parses a file someone already produced.
* **AWS Security Hub** findings in AWS Security Finding Format (ASFF), as
  returned by ``securityhub:GetFindings``.

Nothing here writes anywhere, calls the evidence vault, or imports CrewAI —
see ``swarm.tools.aws_checks`` for the same separation on the AWS side.

**Honesty, by construction, not just in the docs:** a finding here records
what the scanning tool reported at the moment it ran (or, for Security Hub,
at the moment ``GetFindings`` was called). A ``PASS`` / ``PASSED`` is
configuration evidence — test of design and implementation — not evidence
that the control operated correctly over a period; callers (see
``swarm.tools.findings_tools``) put that caveat in the text the audit agents
read, and it is repeated in ``docs/INTEGRATIONS.md``. Severities
(``severity`` / ``Severity.Label``) are the scanning tool's own
classification, not an audit deficiency rating.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# A Prowler JSON export is a config/CI artifact, not attacker-controlled
# network input, but it is still untrusted (it may be edited, stale, or from
# a compromised pipeline) and its size bounds the parsing and prompt work.
MAX_PROWLER_FILE_BYTES = 10 * 1024 * 1024  # 10 MB

# How many individual findings are listed verbatim in the summary text before
# it switches to "see counts above" — keeps the text the agent quotes from
# bounded even for a large scan, while every finding still counts toward the
# per-status and per-check totals.
MAX_DETAIL_LINES = 300

_TRUNCATE_DETAIL = 240  # status_detail / description / title / id cap
_TRUNCATE_SHORT = 64  # status, severity, region, record/workflow state
_TRUNCATE_RESOURCE = 512  # resource ARNs / ids can legitimately be long

# Severity order used to put the most important findings first, so the
# per-finding detail cap never hides a critical failure behind passes.
_SEVERITY_RANK = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "informational": 4,
    "info": 4,
}

_DELIMITER_LOOKALIKE = re.compile(r"<{3,}|>{3,}")

HONESTY_NOTE = (
    "This import reflects what the scanning tool reported at the time it "
    "ran (or, for Security Hub, at the time this read was made): a PASS/"
    "PASSED result is configuration and implementation evidence (test of "
    "design), not proof that the control operated effectively over a "
    "period. Severities and pass/fail results are the tool's own "
    "classification, not an audit deficiency rating — that classification "
    "is made later by the auditor, and (per engagement severity) by "
    "Reporting."
)


class FindingsImportError(ValueError):
    """The input could not be treated as findings evidence (bad size/format)."""


def clean_field(value: Any, limit: int = _TRUNCATE_DETAIL) -> str:
    """One line of plain text from an untrusted scanner field.

    Every field taken from a findings file or API response goes through
    this before it reaches the summary text. The summary is registered as
    evidence and agents quote it line by line, so a field must never be able
    to start a new line: all whitespace (including CR, tab, NEL U+0085 and
    the Unicode line/paragraph separators U+2028/U+2029) is collapsed to
    single spaces, other control and format characters (e.g. NUL, ESC, bidi
    overrides) are dropped, runs of three or more ``<`` / ``>`` are shortened
    to two (so a field cannot imitate the untrusted-data delimiters the
    tools wrap this text in), and the result is capped at ``limit``
    characters.
    """
    text = str(value)
    # Bound the per-character work before cleaning; the cap applies after.
    text = text[: limit * 4 + 64]
    out = []
    for ch in text:
        if ch.isspace():
            out.append(" ")
        elif unicodedata.category(ch).startswith("C"):
            continue
        else:
            out.append(ch)
    text = _DELIMITER_LOOKALIKE.sub(lambda m: m.group(0)[0] * 2, "".join(out))
    return " ".join(text.split())[:limit]


def _clean_optional(value: Any, limit: int) -> str | None:
    if value is None or value == "":
        return None
    return clean_field(value, limit) or None


def _severity_rank(severity: str) -> int:
    return _SEVERITY_RANK.get(severity.lower(), len(_SEVERITY_RANK))


# ─── normalized shapes ────────────────────────────────────────────────────────


@dataclass
class NormalizedFinding:
    """One finding, normalized across Prowler's two JSON formats."""

    check_id: str
    title: str
    status: str  # PASS / FAIL / MANUAL / WARNING / ERROR / UNKNOWN
    severity: str
    region: str | None
    resource_uid: str | None
    status_detail: str
    compliance: list[str] = field(default_factory=list)
    extra_resources: int = 0  # resources beyond the first, if any


@dataclass
class NormalizedSecurityHubFinding:
    """One ASFF finding, normalized for the summary text."""

    finding_id: str
    title: str
    compliance_status: str  # PASSED / FAILED / WARNING / NOT_AVAILABLE / UNKNOWN
    severity_label: str
    record_state: str
    workflow_status: str
    generator_id: str
    product_name: str
    region: str | None
    resource_uid: str | None
    description: str
    related_requirements: list[str] = field(default_factory=list)
    extra_resources: int = 0


# ─── Prowler ──────────────────────────────────────────────────────────────────


def check_prowler_file_size(size_bytes: int) -> None:
    if size_bytes > MAX_PROWLER_FILE_BYTES:
        raise FindingsImportError(
            f"Prowler findings file is {size_bytes} bytes; the limit is "
            f"{MAX_PROWLER_FILE_BYTES} bytes."
        )
    if size_bytes == 0:
        raise FindingsImportError("Prowler findings file is empty.")


def _flatten_compliance(value: Any) -> list[str]:
    """``{"CIS-1.4": ["2.1.1", "2.1.2"], ...}`` -> ``["CIS-1.4: 2.1.1, 2.1.2"]``.

    Tolerant of the shape not matching (returns ``[]``): compliance mappings
    are informational, never load-bearing for the status/severity fields.
    """
    if not isinstance(value, dict):
        return []
    out = []
    for framework, reqs in value.items():
        if isinstance(reqs, list):
            reqs_str = ", ".join(str(r) for r in reqs)
        else:
            reqs_str = str(reqs)
        entry = f"{framework}: {reqs_str}" if reqs_str else str(framework)
        out.append(clean_field(entry))
    return out


def _dict_field(rec: dict, key: str) -> dict:
    value = rec.get(key)
    return value if isinstance(value, dict) else {}


def _list_field(rec: dict, key: str) -> list:
    value = rec.get(key)
    return value if isinstance(value, list) else []


def _normalize_ocsf_record(rec: dict) -> NormalizedFinding:
    metadata = _dict_field(rec, "metadata")
    finding_info = _dict_field(rec, "finding_info")
    unmapped = _dict_field(rec, "unmapped")
    resources = _list_field(rec, "resources")

    check_id = metadata.get("event_code") or finding_info.get("uid")
    if not check_id:
        raise ValueError(
            "OCSF record has neither metadata.event_code nor finding_info.uid"
        )

    status = rec.get("status_code") or rec.get("status") or "UNKNOWN"
    severity = rec.get("severity") or "unknown"
    status_detail = clean_field(rec.get("status_detail") or "")

    first_resource = (
        resources[0] if resources and isinstance(resources[0], dict) else {}
    )
    region = first_resource.get("region") or (
        rec.get("cloud", {}).get("region")
        if isinstance(rec.get("cloud"), dict)
        else None
    )
    resource_uid = first_resource.get("uid") or first_resource.get("name")

    return NormalizedFinding(
        check_id=clean_field(check_id),
        title=clean_field(finding_info.get("title") or check_id),
        status=clean_field(status, _TRUNCATE_SHORT).upper(),
        severity=clean_field(severity, _TRUNCATE_SHORT).lower(),
        region=_clean_optional(region, _TRUNCATE_SHORT),
        resource_uid=_clean_optional(resource_uid, _TRUNCATE_RESOURCE),
        status_detail=status_detail,
        compliance=_flatten_compliance(unmapped.get("compliance")),
        extra_resources=max(0, len(resources) - 1),
    )


def _normalize_legacy_record(rec: dict) -> NormalizedFinding:
    check_id = rec.get("CheckID")
    if not check_id:
        raise ValueError("legacy Prowler record has no CheckID")
    resource_uid = rec.get("ResourceArn") or rec.get("ResourceId")
    return NormalizedFinding(
        check_id=clean_field(check_id),
        title=clean_field(rec.get("CheckTitle") or check_id),
        status=clean_field(rec.get("Status") or "UNKNOWN", _TRUNCATE_SHORT).upper(),
        severity=clean_field(rec.get("Severity") or "unknown", _TRUNCATE_SHORT).lower(),
        region=_clean_optional(rec.get("Region"), _TRUNCATE_SHORT),
        resource_uid=_clean_optional(resource_uid, _TRUNCATE_RESOURCE),
        status_detail=clean_field(rec.get("StatusExtended") or ""),
        compliance=_flatten_compliance(rec.get("Compliance")),
    )


def _is_ocsf_record(rec: dict) -> bool:
    return "finding_info" in rec or "status_code" in rec or "class_uid" in rec


def _prowler_tool_version(records: list[Any]) -> str | None:
    for rec in records:
        if not isinstance(rec, dict):
            continue
        metadata = rec.get("metadata")
        if isinstance(metadata, dict):
            product = metadata.get("product")
            if isinstance(product, dict) and product.get("version"):
                return clean_field(product["version"], _TRUNCATE_SHORT)
    return None


def _prowler_generated_at(records: list[Any]) -> str | None:
    """Latest ``finding_info.created_time``/``time`` seen, if any is present.

    Prowler's OCSF output carries a timestamp per finding, not one report
    generation time, so this is the latest observed timestamp, not
    necessarily the moment the run finished — labelled as such by the
    caller.
    """
    times: list[Any] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        for key in ("time", "time_dt"):
            if rec.get(key) is not None:
                times.append(rec[key])
        finding_info = rec.get("finding_info")
        if isinstance(finding_info, dict) and finding_info.get("created_time_dt"):
            times.append(finding_info["created_time_dt"])
    if not times:
        return None
    try:
        latest = max(times, key=lambda t: str(t))
    except TypeError:
        latest = times[-1]
    return clean_field(latest, _TRUNCATE_SHORT)


@dataclass
class ProwlerParseResult:
    format: str  # "ocsf" | "legacy" | "unknown"
    findings: list[NormalizedFinding]
    errors: list[str]
    tool_version: str | None
    generated_at: str | None


def parse_prowler_findings(raw_text: str) -> ProwlerParseResult:
    """Parse Prowler JSON (OCSF or legacy) text into normalized findings.

    Raises :class:`FindingsImportError` if the top level is not a JSON array
    (Prowler's own output shape, in both formats). Individual malformed
    records are skipped and counted in ``errors`` rather than aborting the
    whole import — one bad record should not hide the rest of a scan.
    """
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise FindingsImportError(f"Not valid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise FindingsImportError(
            "Prowler findings file must be a JSON array of finding records "
            f"(got {type(data).__name__})"
        )
    if not data:
        raise FindingsImportError("Prowler findings file contains no records.")

    fmt = "unknown"
    for rec in data:
        if isinstance(rec, dict):
            fmt = "ocsf" if _is_ocsf_record(rec) else "legacy"
            break

    findings: list[NormalizedFinding] = []
    errors: list[str] = []
    for index, rec in enumerate(data):
        if not isinstance(rec, dict):
            errors.append(f"record #{index + 1}: not a JSON object")
            continue
        try:
            if _is_ocsf_record(rec):
                findings.append(_normalize_ocsf_record(rec))
            else:
                findings.append(_normalize_legacy_record(rec))
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f"record #{index + 1}: {clean_field(exc)}")

    return ProwlerParseResult(
        format=fmt,
        findings=findings,
        errors=errors,
        tool_version=_prowler_tool_version(data),
        generated_at=_prowler_generated_at(data),
    )


def _status_counts(statuses: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in statuses:
        counts[s] = counts.get(s, 0) + 1
    return counts


def build_prowler_summary(result: ProwlerParseResult) -> str:
    """Compact, quotable text describing a parsed Prowler import.

    This exact text is what gets registered as evidence (see
    ``swarm.tools.findings_tools``), so the audit agents can copy any line
    of it verbatim and have the vault confirm the quote.
    """
    counts = _status_counts([f.status for f in result.findings])
    lines: list[str] = [
        f"Prowler findings import (format: {result.format}, "
        f"tool version: {result.tool_version or 'not present in file'}, "
        f"latest finding timestamp in file: {result.generated_at or 'not present in file'})",
        HONESTY_NOTE,
        "",
        f"Summary: {len(result.findings)} findings parsed, "
        + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        + (
            f", {len(result.errors)} unparseable record(s) skipped"
            if result.errors
            else ""
        ),
        "",
    ]

    by_check: dict[str, list[NormalizedFinding]] = {}
    for f in result.findings:
        by_check.setdefault(f.check_id, []).append(f)

    def finding_key(f: NormalizedFinding) -> tuple[bool, int]:
        return (f.status != "FAIL", _severity_rank(f.severity))

    def check_key(check_id: str) -> tuple[bool, int, str]:
        # Checks with a FAIL first, then by their most severe finding, so the
        # detail cap below can only ever cut lower-priority lines.
        failing, rank = min(finding_key(f) for f in by_check[check_id])
        return (failing, rank, check_id)

    lines.append(
        "Findings by check (checks with a FAIL first, then by severity; every "
        "check's header line gives its complete per-status counts):"
    )
    printed = 0
    omitted = 0
    for check_id in sorted(by_check, key=check_key):
        group = sorted(by_check[check_id], key=finding_key)
        check_counts = _status_counts([f.status for f in group])
        counts_str = ", ".join(f"{v} {k}" for k, v in sorted(check_counts.items()))
        lines.append(f"- {check_id} ({group[0].title}): {counts_str}")
        shown = 0
        for f in group:
            if printed >= MAX_DETAIL_LINES:
                break
            resource = f.resource_uid or "unknown resource"
            if f.extra_resources:
                resource += f" (+{f.extra_resources} more resource(s))"
            region = f.region or "unknown region"
            compliance = f"; compliance={f.compliance}" if f.compliance else ""
            lines.append(
                f"    [{f.status}] severity={f.severity} region={region} "
                f'resource={resource} status_detail="{f.status_detail}"{compliance}'
            )
            printed += 1
            shown += 1
        if shown < len(group):
            omitted += len(group) - shown
            lines.append(
                f"    ... {len(group) - shown} finding line(s) for this check "
                "not listed (detail limit reached); counted in the header above."
            )
    if omitted:
        lines.append(
            f"... {omitted} further finding(s) omitted from this listing "
            f"(limit: {MAX_DETAIL_LINES} detail lines); the per-check header "
            "lines and the overall Summary line above include them."
        )

    if result.errors:
        lines.append("")
        lines.append("Unparseable records (skipped, not counted as findings):")
        for err in result.errors[:MAX_DETAIL_LINES]:
            lines.append(f"- {err}")

    return "\n".join(lines)


# ─── AWS Security Hub (ASFF) ──────────────────────────────────────────────────


def build_securityhub_filters(
    *,
    product_name: str | None = None,
    generator_id: str | None = None,
    compliance_status: str | None = None,
) -> dict:
    """``GetFindings`` filters: active, not suppressed, plus optional narrowing.

    Read-only by construction — this only builds a filter dict for a read
    API call, it never calls ``BatchUpdateFindings`` or anything else that
    would change a finding's state.
    """
    filters: dict[str, list[dict[str, str]]] = {
        "RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}],
        "WorkflowStatus": [{"Value": "SUPPRESSED", "Comparison": "NOT_EQUALS"}],
    }
    if product_name:
        filters["ProductName"] = [{"Value": product_name, "Comparison": "EQUALS"}]
    if generator_id:
        filters["GeneratorId"] = [{"Value": generator_id, "Comparison": "EQUALS"}]
    if compliance_status:
        filters["ComplianceStatus"] = [
            {"Value": compliance_status, "Comparison": "EQUALS"}
        ]
    return filters


def collect_securityhub_findings(
    client, filters: dict, *, max_findings: int = 1000
) -> tuple[list[dict], bool]:
    """Paginated ``GetFindings`` read. Returns ``(findings, truncated)``.

    ``truncated`` is True if ``max_findings`` was reached with more pages
    available — the summary says so, rather than silently reporting a
    partial count as complete.
    """
    findings: list[dict] = []
    paginator = client.get_paginator("get_findings")
    truncated = False
    for page in paginator.paginate(Filters=filters, PaginationConfig={"PageSize": 100}):
        page_findings = page.get("Findings", [])
        remaining = max_findings - len(findings)
        if remaining <= 0:
            truncated = True
            break
        if len(page_findings) > remaining:
            findings.extend(page_findings[:remaining])
            truncated = (
                page.get("NextToken") is not None or len(page_findings) > remaining
            )
            break
        findings.extend(page_findings)
    return findings, truncated


def _normalize_asff_record(rec: dict) -> NormalizedSecurityHubFinding:
    finding_id = rec.get("Id")
    if not finding_id:
        raise ValueError("ASFF record has no Id")
    compliance = _dict_field(rec, "Compliance")
    severity = _dict_field(rec, "Severity")
    workflow = _dict_field(rec, "Workflow")
    product_fields = _dict_field(rec, "ProductFields")
    resources = _list_field(rec, "Resources")
    first_resource = (
        resources[0] if resources and isinstance(resources[0], dict) else {}
    )

    product_name = product_fields.get("aws/securityhub/ProductName")
    if not product_name:
        product_arn = str(rec.get("ProductArn") or "")
        product_name = product_arn.rsplit("/", 1)[-1] if product_arn else "unknown"

    related = compliance.get("RelatedRequirements")
    related_requirements = (
        [clean_field(r) for r in related] if isinstance(related, list) else []
    )

    def short_upper(value: Any) -> str:
        return clean_field(value, _TRUNCATE_SHORT).upper()

    return NormalizedSecurityHubFinding(
        finding_id=clean_field(finding_id, _TRUNCATE_RESOURCE),
        title=clean_field(rec.get("Title") or finding_id),
        compliance_status=short_upper(compliance.get("Status") or "NOT_AVAILABLE"),
        severity_label=short_upper(severity.get("Label") or "UNKNOWN"),
        record_state=short_upper(rec.get("RecordState") or "UNKNOWN"),
        workflow_status=short_upper(
            workflow.get("Status") or rec.get("WorkflowState") or "UNKNOWN"
        ),
        generator_id=clean_field(rec.get("GeneratorId") or "unknown"),
        product_name=clean_field(product_name),
        region=_clean_optional(first_resource.get("Region"), _TRUNCATE_SHORT),
        resource_uid=_clean_optional(first_resource.get("Id"), _TRUNCATE_RESOURCE),
        description=clean_field(rec.get("Description") or ""),
        related_requirements=related_requirements,
        extra_resources=max(0, len(resources) - 1),
    )


@dataclass
class SecurityHubParseResult:
    findings: list[NormalizedSecurityHubFinding]
    errors: list[str]


def parse_asff_records(records: list[Any]) -> SecurityHubParseResult:
    findings: list[NormalizedSecurityHubFinding] = []
    errors: list[str] = []
    for index, rec in enumerate(records):
        if not isinstance(rec, dict):
            errors.append(f"finding #{index + 1}: not a JSON object")
            continue
        try:
            findings.append(_normalize_asff_record(rec))
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f"finding #{index + 1}: {clean_field(exc)}")
    return SecurityHubParseResult(findings=findings, errors=errors)


def build_securityhub_summary(
    result: SecurityHubParseResult, *, filters: dict, truncated: bool
) -> str:
    """Compact, quotable text describing a Security Hub findings read.

    Registered verbatim as evidence (see ``swarm.tools.findings_tools``),
    same as :func:`build_prowler_summary`.
    """
    counts = _status_counts([f.compliance_status for f in result.findings])
    filter_desc = clean_field(
        ", ".join(
            f"{name}={[c['Value'] for c in conds]}"
            for name, conds in sorted(filters.items())
        ),
        1024,
    )
    lines: list[str] = [
        f"AWS Security Hub findings import (GetFindings, filters: {filter_desc})",
        HONESTY_NOTE,
        "",
        f"Summary: {len(result.findings)} findings read"
        + (
            " (truncated at the configured limit; more findings exist)"
            if truncated
            else ""
        )
        + ", "
        + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        + (
            f", {len(result.errors)} unparseable record(s) skipped"
            if result.errors
            else ""
        ),
        "",
        "Findings:",
    ]
    ordered = sorted(
        result.findings,
        key=lambda x: (
            x.compliance_status != "FAILED",
            _severity_rank(x.severity_label),
            x.generator_id,
            x.finding_id,
        ),
    )
    for f in ordered[:MAX_DETAIL_LINES]:
        resource = f.resource_uid or "unknown resource"
        if f.extra_resources:
            resource += f" (+{f.extra_resources} more resource(s))"
        region = f.region or "unknown region"
        related = (
            f"; related_requirements={f.related_requirements}"
            if f.related_requirements
            else ""
        )
        lines.append(
            f"- [{f.compliance_status}] {f.generator_id} ({f.title}) "
            f"severity={f.severity_label} product={f.product_name} region={region} "
            f"resource={resource} record_state={f.record_state} "
            f'workflow_status={f.workflow_status} description="{f.description}"{related} '
            f"id={f.finding_id}"
        )
    rest = ordered[MAX_DETAIL_LINES:]
    if rest:
        lines.append(
            f"... {len(rest)} further finding(s) omitted from this listing "
            f"(limit: {MAX_DETAIL_LINES}; FAILED and higher-severity findings "
            "are listed first). Omitted findings by generator, with counts:"
        )
        by_generator: dict[str, list[str]] = {}
        for f in rest:
            by_generator.setdefault(f.generator_id, []).append(f.compliance_status)
        for generator_id in sorted(by_generator):
            counts = _status_counts(by_generator[generator_id])
            counts_str = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
            lines.append(f"  - {generator_id}: {counts_str}")

    if result.errors:
        lines.append("")
        lines.append("Unparseable records (skipped, not counted as findings):")
        for err in result.errors[:MAX_DETAIL_LINES]:
            lines.append(f"- {err}")

    return "\n".join(lines)
