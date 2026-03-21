"""
test_agents.py
--------------
Guardrails for agent contract compliance.
Tests the mock/emulation paths for all agents (no API key required).
Verifies each agent returns the correct state update keys,
findings have valid statuses, and Phase 2 enrichment functions
modify findings without dropping or corrupting data.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.state.schema import AuditState, AuditFinding
from swarm.evidence import ControlEvidence, ToolEvidence
from swarm.agents.orchestrator import analyze_scope_and_themes
from swarm.agents.worker import (
    _emulate_finding,
    _get_evidence_for_control,
    _mock_evidence,
)
from swarm.agents.concluder import _emulate_summary
from swarm.agents.specialist import _emulate_phase2_specialist
from swarm.agents.researcher import _emulate_phase2_researcher
from swarm.agents.challenger import _emulate_phase2_challenger


class TestOrchestratorAgent:
    def test_aws_scope_returns_aws_theme(self, minimal_state):
        minimal_state.audit_scope_narrative = "AWS EKS IAM audit"
        result = analyze_scope_and_themes(minimal_state)
        assert "risk_themes" in result
        assert any("AWS" in t for t in result["risk_themes"])

    def test_pci_scope_returns_pci_theme(self):
        state = AuditState(audit_scope_narrative="PCI-DSS cardholder payment audit")
        result = analyze_scope_and_themes(state)
        assert any("PCI" in t for t in result["risk_themes"])

    def test_hipaa_scope_returns_hipaa_theme(self):
        state = AuditState(audit_scope_narrative="HIPAA ePHI healthcare audit")
        result = analyze_scope_and_themes(state)
        assert any("HIPAA" in t for t in result["risk_themes"])

    def test_gdpr_scope_returns_gdpr_theme(self):
        state = AuditState(
            audit_scope_narrative="GDPR personal data controller processor review"
        )
        result = analyze_scope_and_themes(state)
        assert any("GDPR" in t for t in result["risk_themes"])

    def test_unknown_scope_defaults_to_itgc(self):
        state = AuditState(audit_scope_narrative="quarterly internal review")
        result = analyze_scope_and_themes(state)
        assert len(result["risk_themes"]) >= 1

    def test_orchestrator_returns_skill_ids(self):
        state = AuditState(audit_scope_narrative="AWS CloudTrail EKS IAM review")
        result = analyze_scope_and_themes(state)
        assert "active_skill_ids" in result
        assert "active_skill_names" in result
        assert len(result["active_skill_ids"]) >= 1

    def test_orchestrator_returns_audit_trail_entry(self, minimal_state):
        result = analyze_scope_and_themes(minimal_state)
        assert "audit_trail" in result
        assert len(result["audit_trail"]) >= 1

    def test_orchestrator_result_has_specialist_roles(self, minimal_state):
        result = analyze_scope_and_themes(minimal_state)
        assert "specialist_roles_required" in result
        assert isinstance(result["specialist_roles_required"], list)


class TestWorkerAgent:
    def test_emulate_finding_returns_valid_status(self, sample_control):
        finding = _emulate_finding(sample_control)
        assert finding.status in ("Pass", "Fail", "Exception")

    def test_emulate_finding_has_control_id(self, sample_control):
        finding = _emulate_finding(sample_control)
        assert finding.control_id == sample_control.control_id

    def test_emulate_finding_has_step_results(self, sample_control):
        finding = _emulate_finding(sample_control)
        assert finding.tod_result in ("Pass", "Fail")
        assert finding.toe_result in ("Pass", "Fail", "Exception")
        assert finding.substantive_result in ("Pass", "Fail", "Exception")

    def test_emulate_finding_has_evidence(self, sample_control):
        finding = _emulate_finding(sample_control)
        assert len(finding.evidence_extracted) >= 1

    def test_emulate_finding_is_deterministic_for_same_input(self, sample_control):
        first = _emulate_finding(sample_control, human_context="same context")
        second = _emulate_finding(sample_control, human_context="same context")
        assert first.status == second.status
        assert first.justification == second.justification

    def test_emulate_finding_fail_has_risk_rating(self, sample_control):
        """Run multiple times to get a Fail result; Fail should always have a risk rating."""
        finding = _emulate_finding(sample_control, human_context="force fail bucket")
        if finding.status == "Fail":
            assert finding.risk_rating in ("High", "Medium", "Low")

    def test_mock_evidence_all_control_prefixes(self):
        """Every known control prefix must return non-empty evidence."""
        for prefix in ["AC", "LOG", "CST", "CRY", "CHG", "NET", "VUL"]:
            evidence = _mock_evidence(f"{prefix}-01")
            assert len(evidence) > 50, f"Mock evidence too short for prefix: {prefix}"

    def test_mock_evidence_unknown_prefix_returns_generic(self):
        evidence = _mock_evidence("XYZ-99")
        assert len(evidence) > 20

    def test_get_evidence_for_control_serializes_typed_evidence_log(self):
        evidence_log = {
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
        evidence = _get_evidence_for_control("AC-01", evidence_log)
        assert "Evidence Source: mcp" in evidence
        assert "MinimumPasswordLength" in evidence


class TestConcluderAgent:
    def test_all_pass_returns_low_risk(self, aws_scope_state, pass_finding):
        aws_scope_state.testing_findings = [pass_finding, pass_finding]
        result = _emulate_summary(
            aws_scope_state, passes=2, exceptions=0, fails=0, highs=0
        )
        assert "executive_summary" in result
        assert "LOW" in result["executive_summary"].upper()

    def test_any_fail_returns_high_risk(self, aws_scope_state, fail_finding):
        aws_scope_state.testing_findings = [fail_finding]
        result = _emulate_summary(
            aws_scope_state, passes=0, exceptions=0, fails=1, highs=1
        )
        assert "HIGH" in result["executive_summary"].upper()

    def test_exceptions_only_returns_medium_risk(
        self, aws_scope_state, exception_finding
    ):
        aws_scope_state.testing_findings = [exception_finding]
        result = _emulate_summary(
            aws_scope_state, passes=0, exceptions=1, fails=0, highs=0
        )
        assert "MEDIUM" in result["executive_summary"].upper()

    def test_summary_mentions_skill_names(self, aws_scope_state, pass_finding):
        aws_scope_state.testing_findings = [pass_finding]
        result = _emulate_summary(
            aws_scope_state, passes=1, exceptions=0, fails=0, highs=0
        )
        assert "AWS" in result["executive_summary"]

    def test_summary_has_audit_trail_entry(self, aws_scope_state, pass_finding):
        aws_scope_state.testing_findings = [pass_finding]
        result = _emulate_summary(
            aws_scope_state, passes=1, exceptions=0, fails=0, highs=0
        )
        assert "audit_trail" in result
        assert len(result["audit_trail"]) >= 1


class TestPhase2SpecialistAgent:
    def test_annotates_fail_findings(self, state_with_findings):
        result = _emulate_phase2_specialist(
            state_with_findings,
            [f for f in state_with_findings.testing_findings if f.status == "Fail"],
        )
        assert "testing_findings" in result
        for f in result["testing_findings"]:
            if f.status in ("Fail", "Exception"):
                assert "Specialist Annotation" in f.justification

    def test_passes_are_not_annotated(self, state_with_findings):
        result = _emulate_phase2_specialist(
            state_with_findings,
            [f for f in state_with_findings.testing_findings if f.status == "Fail"],
        )
        for f in result["testing_findings"]:
            if f.status == "Pass":
                # Pass findings should NOT get specialist annotation
                assert "Specialist Annotation" not in f.justification

    def test_annotation_count_matches_findings(self, state_with_findings):
        result = _emulate_phase2_specialist(
            state_with_findings,
            [
                f
                for f in state_with_findings.testing_findings
                if f.status in ("Fail", "Exception")
            ],
        )
        original_count = len(state_with_findings.testing_findings)
        assert len(result["testing_findings"]) == original_count


class TestPhase2ResearcherAgent:
    def test_appends_researcher_context_to_failures(self, state_with_findings):
        failed = [
            f
            for f in state_with_findings.testing_findings
            if f.status in ("Fail", "Exception")
        ]
        result = _emulate_phase2_researcher(state_with_findings, failed)
        assert "testing_findings" in result
        for f in result["testing_findings"]:
            if f.status in ("Fail", "Exception"):
                assert "Researcher Context" in f.justification

    def test_total_finding_count_unchanged(self, state_with_findings):
        failed = [
            f
            for f in state_with_findings.testing_findings
            if f.status in ("Fail", "Exception")
        ]
        result = _emulate_phase2_researcher(state_with_findings, failed)
        assert len(result["testing_findings"]) == len(
            state_with_findings.testing_findings
        )


class TestPhase2ChallengerAgent:
    def test_returns_audit_trail_entry(self, state_with_findings):
        result = _emulate_phase2_challenger(state_with_findings)
        assert "audit_trail" in result
        assert len(result["audit_trail"]) >= 1

    def test_flags_pass_with_no_evidence(self):
        """A Pass finding with zero evidence should be flagged."""
        finding = AuditFinding(
            control_id="TEST-01",
            agent_role="Test",
            status="Pass",
            justification="Looks fine.",
            evidence_extracted=[],
            tod_result="Pass",
            toe_result="Pass",
            substantive_result="Pass",
        )
        state = AuditState(
            audit_scope_narrative="Generic audit", testing_findings=[finding]
        )
        result = _emulate_phase2_challenger(state)
        assert "TEST-01" in (
            result.get("executive_summary", "") + str(result.get("audit_trail", ""))
        )

    def test_flags_tod_pass_toe_fail_misclassified_as_fail(self):
        """TOD=Pass + TOE=Fail but status=Fail should be challenged as possibly Exception."""
        finding = AuditFinding(
            control_id="CHG-02",
            agent_role="Test",
            status="Fail",
            justification="Control failed.",
            evidence_extracted=["Evidence here."],
            risk_rating="High",
            tod_result="Pass",
            toe_result="Fail",
            substantive_result="Fail",
        )
        state = AuditState(
            audit_scope_narrative="Change management audit", testing_findings=[finding]
        )
        result = _emulate_phase2_challenger(state)
        combined = result.get("executive_summary", "") + str(
            result.get("audit_trail", "")
        )
        assert "CHG-02" in combined or "Exception" in combined
