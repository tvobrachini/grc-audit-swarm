"""
test_schema.py
--------------
Guardrails for the AuditState schema and Pydantic models.
Ensures all fields have correct types, defaults are safe, and
state objects can be created and mutated without validation errors.
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pydantic import ValidationError

from swarm.evidence import ControlEvidence, ToolEvidence
from swarm.state.schema import (
    AuditAction,
    AuditFinding,
    AuditProcedure,
    AuditState,
    ControlMatrixItem,
)


class TestAuditStateDefaults:
    def test_empty_state_is_valid(self):
        state = AuditState()
        assert state.audit_scope_narrative == ""
        assert state.risk_themes == []
        assert state.specialist_roles_required == []
        assert state.active_skill_ids == []
        assert state.active_skill_names == []
        assert state.control_matrix == []
        assert state.testing_findings == []
        assert state.execution_status == {}
        assert state.control_feedback == {}
        assert state.executive_summary == ""
        assert state.revision_feedback == ""
        assert state.audit_trail == []

    def test_state_with_scope_is_valid(self, minimal_state):
        assert minimal_state.audit_scope_narrative != ""

    def test_state_with_full_fields(self, aws_scope_state):
        assert aws_scope_state.active_skill_ids == ["aws_cloud_security"]
        assert aws_scope_state.active_skill_names == ["AWS Cloud Security Specialist"]
        assert "AWS Cloud Infrastructure" in aws_scope_state.risk_themes

    def test_evidence_log_accepts_typed_control_evidence(self):
        state = AuditState(
            evidence_log={
                "AC-01": ControlEvidence(
                    control_id="AC-01",
                    tool_results={
                        "get_iam_password_policy": ToolEvidence(
                            tool_name="get_iam_password_policy",
                            payload={"MinimumPasswordLength": 14},
                        )
                    },
                )
            }
        )
        assert (
            state.evidence_log["AC-01"]
            .tool_results["get_iam_password_policy"]
            .payload["MinimumPasswordLength"]
            == 14
        )

    def test_state_accepts_legacy_model_instances(self):
        class LegacyAuditProcedure(AuditProcedure):
            pass

        class LegacyControlMatrixItem(ControlMatrixItem):
            pass

        class LegacyAuditFinding(AuditFinding):
            pass

        class LegacyAuditAction(AuditAction):
            pass

        class LegacyToolEvidence(ToolEvidence):
            pass

        class LegacyControlEvidence(ControlEvidence):
            pass

        procedure = LegacyAuditProcedure(
            control_id="AC-01",
            tod_steps=["Review policy"],
            toe_steps=["Sample review"],
            substantive_steps=["Inspect exceptions"],
            erl_items=["Policy export"],
        )
        control = LegacyControlMatrixItem(
            control_id="AC-01",
            domain="Access Control",
            description="Quarterly review",
            procedures=procedure,
        )
        finding = LegacyAuditFinding(
            control_id="AC-01",
            agent_role="Worker",
            status="Pass",
            justification="All checks passed.",
            evidence_extracted=["No exceptions found."],
            tod_result="Pass",
            toe_result="Pass",
            substantive_result="Pass",
        )
        action = LegacyAuditAction(
            agent_or_user_id="legacy",
            action_taken="Loaded from legacy checkpoint.",
        )
        evidence = LegacyControlEvidence(
            control_id="AC-01",
            tool_results={
                "get_iam_password_policy": LegacyToolEvidence(
                    tool_name="get_iam_password_policy",
                    payload={"MinimumPasswordLength": 14},
                )
            },
        )

        state = AuditState(
            control_matrix=[control],
            testing_findings=[finding],
            audit_trail=[action],
            evidence_log={"AC-01": evidence},
        )

        assert state.control_matrix[0].control_id == "AC-01"
        assert state.testing_findings[0].status == "Pass"
        assert state.audit_trail[0].agent_or_user_id == "legacy"
        assert (
            state.evidence_log["AC-01"]
            .tool_results["get_iam_password_policy"]
            .payload["MinimumPasswordLength"]
            == 14
        )


class TestAuditFinding:
    def test_pass_finding_valid(self, pass_finding):
        assert pass_finding.status == "Pass"
        assert pass_finding.risk_rating is None
        assert pass_finding.tod_result == "Pass"
        assert pass_finding.toe_result == "Pass"
        assert pass_finding.substantive_result == "Pass"

    def test_fail_finding_has_risk_rating(self, fail_finding):
        assert fail_finding.status == "Fail"
        assert fail_finding.risk_rating in ("High", "Medium", "Low")

    def test_exception_finding_valid(self, exception_finding):
        assert exception_finding.status == "Exception"
        assert exception_finding.risk_rating == "Medium"

    def test_finding_status_values(self):
        """Status must be one of the three accepted values."""
        for valid_status in ("Pass", "Fail", "Exception"):
            f = AuditFinding(
                control_id="TEST-01",
                agent_role="TestAgent",
                status=valid_status,
                justification="Test.",
                evidence_extracted=["Evidence item."],
            )
            assert f.status == valid_status

    def test_invalid_finding_status_is_rejected(self):
        with pytest.raises(ValidationError):
            AuditFinding(
                control_id="TEST-01",
                agent_role="TestAgent",
                status="Unknown",
                justification="Test.",
                evidence_extracted=["Evidence item."],
            )

    def test_invalid_risk_rating_is_rejected(self):
        with pytest.raises(ValidationError):
            AuditFinding(
                control_id="TEST-01",
                agent_role="TestAgent",
                status="Fail",
                justification="Test.",
                evidence_extracted=["Evidence item."],
                risk_rating="Critical",
            )

    def test_finding_evidence_extracted_is_list(self, pass_finding):
        assert isinstance(pass_finding.evidence_extracted, list)
        assert len(pass_finding.evidence_extracted) >= 1

    def test_finding_copy_preserves_fields(self, fail_finding):
        updated = fail_finding.model_copy(
            update={"justification": "Updated narrative."}
        )
        assert updated.justification == "Updated narrative."
        assert updated.control_id == fail_finding.control_id
        assert updated.status == fail_finding.status


class TestControlMatrixItem:
    def test_control_with_procedures(self, sample_control):
        assert sample_control.control_id == "AC-01"
        assert sample_control.procedures is not None
        assert len(sample_control.procedures.tod_steps) >= 1
        assert len(sample_control.procedures.toe_steps) >= 1
        assert len(sample_control.procedures.substantive_steps) >= 1
        assert len(sample_control.procedures.erl_items) >= 1

    def test_control_without_procedures_is_valid(self):
        ctrl = ControlMatrixItem(
            control_id="NET-01",
            domain="Network Security",
            description="Firewall rule review.",
        )
        assert ctrl.procedures is None


class TestAuditStateWithFindings:
    def test_state_with_multiple_findings(self, state_with_findings):
        assert len(state_with_findings.testing_findings) == 3
        statuses = [f.status for f in state_with_findings.testing_findings]
        assert "Pass" in statuses
        assert "Fail" in statuses
        assert "Exception" in statuses

    def test_execution_status_matches_findings(self, state_with_findings):
        for f in state_with_findings.testing_findings:
            assert f.control_id in state_with_findings.execution_status

    def test_control_feedback_defaults_empty(self, state_with_findings):
        assert state_with_findings.control_feedback == {}


class TestAuditAction:
    def test_audit_action_has_timestamp(self):
        action = AuditAction(
            agent_or_user_id="TestAgent",
            action_taken="Ran test.",
            reasoning_snapshot="Checked something.",
        )
        assert action.timestamp != ""
        assert action.agent_or_user_id == "TestAgent"
