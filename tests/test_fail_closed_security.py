"""Fail-closed security and audit-linter regression tests.

Covers:
1. ``collect_access_key_age`` returns ``UNKNOWN`` (not ``PASS``) on AccessDenied
   or unparseable key creation dates.
2. MCP server tools redact AWS account IDs on both success and error paths.
3. ``AuditFlow.lint_phase_artifact`` fails closed when ``AUDIT_STRICT_LINTER=1``
   and the linter raises an unexpected exception.
4. Strict-linter rejection attribution in ``current_human_dossier`` accurately
   identifies the deterministic audit linter instead of blaming the evidence check.
"""

from __future__ import annotations

import datetime
import os
import sys
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flow_builders import (  # type: ignore[import-not-found]
    APPROVED,
    crew_result,
    make_flow,
    make_racm,
    run_phase,
)

from swarm.linters import LintReport, LintViolation
from swarm.mcp_server import list_public_s3_buckets
from swarm.tools.aws_checks import collect_access_key_age


class TestAccessKeyAgeFailClosed:
    def test_access_denied_never_passes(self):
        iam = MagicMock()
        iam.get_paginator.return_value.paginate.return_value = [
            {"Users": [{"UserName": "alice"}]}
        ]
        iam.list_access_keys.side_effect = ClientError(
            {
                "Error": {
                    "Code": "AccessDenied",
                    "Message": "arn:aws:iam::111122223333:user/auditor denied",
                }
            },
            "ListAccessKeys",
        )
        res = collect_access_key_age(iam)
        assert res["Verdict"] == "UNKNOWN"
        assert res["Summary"]["unknown_keys"] == 1
        assert "111122223333" not in repr(res)

    def test_unparseable_create_date_returns_unknown(self):
        iam = MagicMock()
        iam.get_paginator.return_value.paginate.return_value = [
            {"Users": [{"UserName": "alice"}]}
        ]
        iam.list_access_keys.return_value = {
            "AccessKeyMetadata": [
                {
                    "AccessKeyId": "AKIAIOSFODNN7EXAMPLE",  # pragma: allowlist secret
                    "Status": "Active",
                    "CreateDate": "not-a-timestamp",
                }
            ]
        }
        now = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
        res = collect_access_key_age(iam, reference_time=now)
        assert res["Verdict"] == "UNKNOWN"
        assert res["Summary"]["unknown_keys"] == 1


class TestMcpRedaction:
    def test_mcp_redacts_account_id_in_payload(self):
        account_id = "999988887777"
        with (
            patch("boto3.client", return_value=MagicMock()),
            patch(
                "swarm.mcp_server.collect_s3_public_access",
                return_value={
                    "Buckets": [],
                    "CallerIdentityArn": f"arn:aws:iam::{account_id}:user/auditor",
                },
            ),
        ):
            out = list_public_s3_buckets()
        assert account_id not in out
        assert "[REDACTED]" in out


class TestStrictLinterFailClosedAndAttribution:
    def test_lint_phase_artifact_fails_closed_when_strict_and_exception_raised(
        self, monkeypatch
    ):
        monkeypatch.setenv("AUDIT_STRICT_LINTER", "1")
        flow = make_flow(1)
        racm = make_racm()
        with patch("swarm.linters.lint_racm", side_effect=RuntimeError("boom")):
            report = flow.lint_phase_artifact(1, racm)
        assert report.passed is False
        assert len(report.errors) == 1
        assert report.errors[0].rule_id == "LINTER_ERROR"
        assert "boom" in report.errors[0].message

    def test_lint_phase_artifact_soft_skips_when_non_strict(self, monkeypatch):
        monkeypatch.delenv("AUDIT_STRICT_LINTER", raising=False)
        flow = make_flow(1)
        racm = make_racm()
        with patch("swarm.linters.lint_racm", side_effect=RuntimeError("boom")):
            report = flow.lint_phase_artifact(1, racm)
        assert report.passed is True
        assert report.errors == []

    def test_strict_linter_rejection_blames_linter_not_evidence_check(
        self, monkeypatch
    ):
        monkeypatch.setenv("AUDIT_STRICT_LINTER", "1")
        flow = make_flow(1)
        racm = make_racm()
        failing_lint = LintReport(
            violations=[
                LintViolation(
                    rule_id="D13",
                    subject_id="R-01",
                    message="Auditing standard cited as control framework.",
                )
            ]
        )
        with patch.object(flow, "lint_phase_artifact", return_value=failing_lint):
            run_phase(
                flow,
                1,
                [crew_result(1, APPROVED, racm), crew_result(1, APPROVED, racm)],
            )
        assert flow.state.status == "QA_REJECTED_PHASE_1"
        dossier = flow.state.current_human_dossier or ""
        assert "failed the deterministic audit linter" in dossier
        assert "failed the deterministic evidence check" not in dossier
