"""
conftest.py
-----------
Shared pytest fixtures for the GRC Audit Swarm test suite.
All fixtures run in mock mode (no API keys required).
"""

import os
import sys
import pytest

# Ensure src/ is on the path for all tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.state.schema import (
    AuditState,
    AuditFinding,
    AuditProcedure,
    ControlMatrixItem,
)


@pytest.fixture
def minimal_state():
    """Bare minimum state — only scope narrative set."""
    return AuditState(audit_scope_narrative="AWS EKS IAM and CloudTrail audit")


@pytest.fixture
def aws_scope_state():
    """State as it would look after Orchestrator runs on an AWS scope."""
    return AuditState(
        audit_scope_narrative="AWS EKS cluster IAM access control and CloudTrail logging review.",
        risk_themes=["AWS Cloud Infrastructure"],
        specialist_roles_required=["AWS Cloud Security Architect"],
        active_skill_ids=["aws_cloud_security"],
        active_skill_names=["AWS Cloud Security Specialist"],
        audit_trail=[],
    )


@pytest.fixture
def pci_scope_state():
    return AuditState(
        audit_scope_narrative="PCI-DSS v4 cardholder data environment audit of payment processing systems.",
        risk_themes=["PCI-DSS Payment Processing"],
        specialist_roles_required=["PCI Internal Auditor"],
        active_skill_ids=["pci_dss"],
        active_skill_names=["PCI-DSS Payment Security Specialist"],
        audit_trail=[],
    )


@pytest.fixture
def sample_procedure():
    return AuditProcedure(
        control_id="AC-01",
        tod_steps=[
            "Review IAM access policy document.",
            "Confirm quarterly review requirement is documented.",
        ],
        toe_steps=[
            "Extract full population of AD users.",
            "Sample 25 users and verify access review completion.",
        ],
        substantive_steps=["Verify no orphan or terminated accounts remain active."],
        erl_items=["IAM Credentials Report", "Access Review Tracker Q4 2025"],
    )


@pytest.fixture
def sample_control(sample_procedure):
    return ControlMatrixItem(
        control_id="AC-01",
        domain="Access Control",
        description="Quarterly user access review for all privileged accounts.",
        procedures=sample_procedure,
    )


@pytest.fixture
def pass_finding():
    return AuditFinding(
        control_id="AC-01",
        agent_role="Execution Worker (Access Control)",
        status="Pass",
        justification="All 25 sampled users had documented quarterly access reviews.",
        evidence_extracted=[
            "IAM report shows 325/325 active users reviewed for Q4 2025."
        ],
        risk_rating=None,
        tod_result="Pass",
        toe_result="Pass",
        substantive_result="Pass",
    )


@pytest.fixture
def fail_finding():
    return AuditFinding(
        control_id="VUL-02",
        agent_role="Execution Worker (Vulnerability Management)",
        status="Fail",
        justification="Critical CVE-2025-44810 not patched within 15-day SLA.",
        evidence_extracted=[
            "Tenable scan: CVE-2025-44810 unpatched 18 days.",
            "No patch ticket created.",
        ],
        risk_rating="High",
        tod_result="Pass",
        toe_result="Fail",
        substantive_result="Fail",
    )


@pytest.fixture
def exception_finding():
    return AuditFinding(
        control_id="LOG-04",
        agent_role="Execution Worker (Audit & Accountability)",
        status="Exception",
        justification="3 of 15 alert rules lack runbooks.",
        evidence_extracted=["AWS Security Hub: 12/15 alert runbooks present."],
        risk_rating="Medium",
        tod_result="Pass",
        toe_result="Exception",
        substantive_result="Exception",
    )


@pytest.fixture
def state_with_findings(
    aws_scope_state, pass_finding, fail_finding, exception_finding, sample_control
):
    """State that has completed Phase 2 execution — findings populated."""
    aws_scope_state.control_matrix = [sample_control]
    aws_scope_state.testing_findings = [pass_finding, fail_finding, exception_finding]
    aws_scope_state.execution_status = {
        "AC-01": "awaiting_review",
        "VUL-02": "awaiting_review",
        "LOG-04": "awaiting_review",
    }
    return aws_scope_state
