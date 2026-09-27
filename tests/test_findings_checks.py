"""Tests for the pure Prowler/Security Hub findings parsing logic
(``swarm.tools.findings_checks``). No CrewAI, no vault, no AWS calls here —
see ``tests/test_findings_tools.py`` for the tool wrappers and vault
registration.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.tools.findings_checks import (  # noqa: E402
    HONESTY_NOTE,
    FindingsImportError,
    build_prowler_summary,
    build_securityhub_filters,
    build_securityhub_summary,
    check_prowler_file_size,
    collect_securityhub_findings,
    parse_asff_records,
    parse_prowler_findings,
)

FIXTURES = Path(__file__).parent / "fixtures" / "findings"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


# ─── Prowler: OCSF format ─────────────────────────────────────────────────────


class TestParseOcsf:
    def test_detects_ocsf_format(self):
        result = parse_prowler_findings(_read("prowler_ocsf_sample.json"))
        assert result.format == "ocsf"

    def test_normalizes_pass_fail_manual(self):
        result = parse_prowler_findings(_read("prowler_ocsf_sample.json"))
        statuses = {f.check_id: f.status for f in result.findings}
        assert statuses["s3_bucket_level_public_access_block"] in ("FAIL", "PASS")
        by_status = [f.status for f in result.findings]
        assert "PASS" in by_status
        assert "FAIL" in by_status
        assert "MANUAL" in by_status

    def test_check_id_from_event_code(self):
        result = parse_prowler_findings(_read("prowler_ocsf_sample.json"))
        check_ids = {f.check_id for f in result.findings}
        assert "s3_bucket_level_public_access_block" in check_ids
        assert "iam_root_hardware_mfa_enabled" in check_ids
        assert "iam_password_policy_uppercase" in check_ids

    def test_region_and_resource_extracted(self):
        result = parse_prowler_findings(_read("prowler_ocsf_sample.json"))
        fail = next(f for f in result.findings if f.status == "FAIL")
        assert fail.region == "us-east-1"
        assert fail.resource_uid == "arn:aws:s3:::my-public-bucket"

    def test_compliance_flattened(self):
        result = parse_prowler_findings(_read("prowler_ocsf_sample.json"))
        fail = next(f for f in result.findings if f.status == "FAIL")
        assert any("CIS-1.4: 2.1.1" == c for c in fail.compliance)
        assert any("SOC2: CC6.1" == c for c in fail.compliance)

    def test_severity_and_status_detail(self):
        result = parse_prowler_findings(_read("prowler_ocsf_sample.json"))
        fail = next(f for f in result.findings if f.status == "FAIL")
        assert fail.severity == "high"
        assert "does not have Public Access Block" in fail.status_detail

    def test_tool_version_detected(self):
        result = parse_prowler_findings(_read("prowler_ocsf_sample.json"))
        assert result.tool_version == "4.6.0"

    def test_malformed_record_skipped_and_counted(self):
        result = parse_prowler_findings(_read("prowler_ocsf_sample.json"))
        assert len(result.errors) == 1
        assert "record #5" in result.errors[0]
        # 4 well-formed records out of 5 total.
        assert len(result.findings) == 4

    def test_account_ids_present_in_raw_findings_before_registration(self):
        # The parser itself does not redact — redaction happens at vault
        # registration time (see test_findings_tools.py), same division of
        # responsibility as swarm.tools.aws_checks.
        result = parse_prowler_findings(_read("prowler_ocsf_sample.json"))
        assert any(
            f.resource_uid and "123456789012" in f.resource_uid for f in result.findings
        )


class TestParseLegacy:
    def test_detects_legacy_format(self):
        result = parse_prowler_findings(_read("prowler_legacy_sample.json"))
        assert result.format == "legacy"

    def test_fields_mapped(self):
        result = parse_prowler_findings(_read("prowler_legacy_sample.json"))
        by_id = {f.check_id: f for f in result.findings}
        assert by_id["s3_bucket_public_access"].status == "FAIL"
        assert by_id["s3_bucket_public_access"].region == "us-west-2"
        assert (
            by_id["s3_bucket_public_access"].resource_uid
            == "arn:aws:s3:::legacy-public-bucket"
        )
        assert by_id["iam_password_policy_minimum_length_14"].status == "PASS"

    def test_compliance_mapping(self):
        result = parse_prowler_findings(_read("prowler_legacy_sample.json"))
        by_id = {f.check_id: f for f in result.findings}
        compliance = by_id["s3_bucket_public_access"].compliance
        assert "CIS-1.4: 2.1.1" in compliance
        assert "SOC2: CC6.1, CC6.6" in compliance

    def test_no_tool_version_in_legacy_format(self):
        result = parse_prowler_findings(_read("prowler_legacy_sample.json"))
        assert result.tool_version is None

    def test_malformed_record_skipped(self):
        result = parse_prowler_findings(_read("prowler_legacy_sample.json"))
        assert len(result.findings) == 2
        assert len(result.errors) == 1


class TestParseErrors:
    def test_malformed_json_raises(self):
        with pytest.raises(FindingsImportError, match="Not valid JSON"):
            parse_prowler_findings(_read("prowler_malformed.json"))

    def test_non_array_top_level_raises(self):
        with pytest.raises(FindingsImportError, match="JSON array"):
            parse_prowler_findings(_read("prowler_not_an_array.json"))

    def test_empty_array_raises(self):
        with pytest.raises(FindingsImportError, match="no records"):
            parse_prowler_findings("[]")

    def test_all_records_not_objects(self):
        result = parse_prowler_findings(json.dumps([1, "two", None]))
        assert result.findings == []
        assert len(result.errors) == 3


class TestFileSizeLimit:
    def test_within_limit_ok(self):
        check_prowler_file_size(1024)

    def test_over_limit_raises(self):
        with pytest.raises(FindingsImportError, match="limit"):
            check_prowler_file_size(10 * 1024 * 1024 + 1)

    def test_empty_file_raises(self):
        with pytest.raises(FindingsImportError, match="empty"):
            check_prowler_file_size(0)


class TestProwlerSummary:
    def test_summary_contains_honesty_note(self):
        result = parse_prowler_findings(_read("prowler_ocsf_sample.json"))
        summary = build_prowler_summary(result)
        assert HONESTY_NOTE in summary

    def test_summary_groups_by_check_id(self):
        result = parse_prowler_findings(_read("prowler_ocsf_sample.json"))
        summary = build_prowler_summary(result)
        assert "s3_bucket_level_public_access_block" in summary
        assert "iam_password_policy_uppercase" in summary

    def test_summary_reports_counts(self):
        result = parse_prowler_findings(_read("prowler_ocsf_sample.json"))
        summary = build_prowler_summary(result)
        assert "2 PASS" in summary
        assert "1 FAIL" in summary
        assert "1 MANUAL" in summary
        assert "1 unparseable" in summary

    def test_summary_lists_unparseable_records(self):
        result = parse_prowler_findings(_read("prowler_ocsf_sample.json"))
        summary = build_prowler_summary(result)
        assert "Unparseable records" in summary
        assert "record #5" in summary

    def test_summary_truncation_when_many_findings(self):
        from swarm.tools import findings_checks as fc

        records = []
        for i in range(fc.MAX_DETAIL_LINES + 20):
            records.append(
                {
                    "metadata": {"event_code": "some_check"},
                    "status_code": "PASS",
                    "severity": "low",
                    "status_detail": "ok",
                    "finding_info": {"uid": f"finding-{i}"},
                    "resources": [{"uid": f"resource-{i}", "region": "us-east-1"}],
                }
            )
        result = parse_prowler_findings(json.dumps(records))
        summary = build_prowler_summary(result)
        assert "further finding(s) omitted" in summary


# ─── Security Hub ─────────────────────────────────────────────────────────────


class TestSecurityHubFilters:
    def test_default_filters(self):
        filters = build_securityhub_filters()
        assert filters["RecordState"] == [{"Value": "ACTIVE", "Comparison": "EQUALS"}]
        assert filters["WorkflowStatus"] == [
            {"Value": "SUPPRESSED", "Comparison": "NOT_EQUALS"}
        ]
        assert "ProductName" not in filters
        assert "GeneratorId" not in filters
        assert "ComplianceStatus" not in filters

    def test_optional_filters_added(self):
        filters = build_securityhub_filters(
            product_name="Security Hub",
            generator_id="aws-foundational-security-best-practices/v/1.0.0/S3.8",
            compliance_status="FAILED",
        )
        assert filters["ProductName"] == [
            {"Value": "Security Hub", "Comparison": "EQUALS"}
        ]
        assert filters["GeneratorId"][0]["Value"].endswith("S3.8")
        assert filters["ComplianceStatus"] == [
            {"Value": "FAILED", "Comparison": "EQUALS"}
        ]


class TestParseAsff:
    def _records(self):
        return json.loads(_read("securityhub_asff_sample.json"))

    def test_parses_compliance_status(self):
        result = parse_asff_records(self._records())
        statuses = {f.generator_id: f.compliance_status for f in result.findings}
        assert (
            statuses["aws-foundational-security-best-practices/v/1.0.0/S3.8"]
            == "FAILED"
        )
        assert (
            statuses["aws-foundational-security-best-practices/v/1.0.0/IAM.7"]
            == "PASSED"
        )

    def test_severity_region_resource(self):
        result = parse_asff_records(self._records())
        s3_finding = next(f for f in result.findings if f.generator_id.endswith("S3.8"))
        assert s3_finding.severity_label == "MEDIUM"
        assert s3_finding.region == "us-east-1"
        assert s3_finding.resource_uid == "arn:aws:s3:::my-public-bucket"

    def test_related_requirements(self):
        result = parse_asff_records(self._records())
        s3_finding = next(f for f in result.findings if f.generator_id.endswith("S3.8"))
        assert "NIST.800-53.r5 AC-3" in s3_finding.related_requirements

    def test_malformed_record_skipped(self):
        result = parse_asff_records(self._records())
        assert len(result.findings) == 2
        assert len(result.errors) == 1
        assert "finding #3" in result.errors[0]

    def test_non_dict_record(self):
        result = parse_asff_records([1, 2])
        assert result.findings == []
        assert len(result.errors) == 2


class TestSecurityHubSummary:
    def test_summary_contains_honesty_note_and_counts(self):
        result = parse_asff_records(json.loads(_read("securityhub_asff_sample.json")))
        summary = build_securityhub_summary(
            result, filters=build_securityhub_filters(), truncated=False
        )
        assert HONESTY_NOTE in summary
        assert "1 FAILED" in summary
        assert "1 PASSED" in summary

    def test_summary_notes_truncation(self):
        result = parse_asff_records(json.loads(_read("securityhub_asff_sample.json")))
        summary = build_securityhub_summary(
            result, filters=build_securityhub_filters(), truncated=True
        )
        assert "truncated" in summary


class FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kwargs):
        return iter(self._pages)


class FakeSecurityHubClient:
    def __init__(self, pages):
        self._pages = pages

    def get_paginator(self, name):
        assert name == "get_findings"
        return FakePaginator(self._pages)


class TestCollectSecurityHubFindings:
    def test_single_page(self):
        client = FakeSecurityHubClient([{"Findings": [{"Id": "a"}, {"Id": "b"}]}])
        findings, truncated = collect_securityhub_findings(client, {}, max_findings=10)
        assert len(findings) == 2
        assert truncated is False

    def test_multiple_pages(self):
        client = FakeSecurityHubClient(
            [
                {"Findings": [{"Id": "a"}], "NextToken": "x"},
                {"Findings": [{"Id": "b"}]},
            ]
        )
        findings, truncated = collect_securityhub_findings(client, {}, max_findings=10)
        assert len(findings) == 2
        assert truncated is False

    def test_truncates_at_max(self):
        client = FakeSecurityHubClient(
            [{"Findings": [{"Id": str(i)} for i in range(5)], "NextToken": "x"}]
        )
        findings, truncated = collect_securityhub_findings(client, {}, max_findings=3)
        assert len(findings) == 3
        assert truncated is True
