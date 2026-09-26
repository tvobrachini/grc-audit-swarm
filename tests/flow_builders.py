"""Shared builders for AuditFlow tests: schema-valid artifacts and mocked crew results."""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.audit_flow import AuditFlow
from swarm.schema import (
    AuditFindingSchema,
    Control,
    ControlTestStep,
    ControlTesting,
    FinalReportSchema,
    Population,
    QA_PushbackSchema,
    Risk,
    RiskControlMatrixSchema,
    WorkingPaperSchema,
)


def make_racm() -> RiskControlMatrixSchema:
    step = ControlTestStep(step_description="Inspect policy", expected_result="OK")
    return RiskControlMatrixSchema(
        theme="AWS S3",
        risks=[
            Risk(
                risk_id="RISK-01",
                description="Public buckets expose customer data",
                regulatory_mapping=["CIS AWS 2.1.5"],
                controls=[
                    Control(
                        control_id="CTRL-01",
                        description="Block public access enabled account-wide",
                        control_owner="Cloud Platform Lead",
                        frequency="Continuous",
                        nature="Automated",
                        control_type="Preventive",
                        key_control=True,
                        assertions=["Confidentiality"],
                        testing_procedures=ControlTesting(
                            test_of_design=[step],
                            test_of_effectiveness=[step],
                            substantive_testing=[step],
                            population=Population(
                                source="All S3 buckets",
                                completeness_procedure="Agree to console count",
                            ),
                            sample_size=1,
                            sampling_method="Test of one",
                            period_of_reliance="FY2026",
                        ),
                    )
                ],
            )
        ],
    )


def make_papers() -> WorkingPaperSchema:
    return WorkingPaperSchema(
        theme="AWS S3",
        findings=[
            AuditFindingSchema(
                control_id="CTRL-01",
                vault_id_reference="vault-abc123",
                exact_quote_from_evidence="BlockPublicAcls: true",
                tod_conclusion="Effective",
                toe_conclusion="Effective",
                toe_basis="Test of one; relies on change-management ITGCs.",
                test_conclusion="Control operating effectively.",
            )
        ],
    )


def make_report() -> FinalReportSchema:
    return FinalReportSchema(
        executive_summary="No exceptions.",
        detailed_report="CTRL-01 passed.",
        compliance_tone_approved=True,
    )


PHASES = {
    1: {
        "crew": "PlanningCrew",
        "tasks": [
            "context_task",
            "crosswalk_task",
            "weighting_task",
            "racm_drafting_task",
            "qa_gate_task",
        ],
        "qa": "qa_gate_task",
        "artifact": "racm_drafting_task",
        "field": "racm_plan",
        "make": make_racm,
        "start": "WAITING_FOR_SCOPE",
        "run": "generate_planning",
    },
    2: {
        "crew": "FieldworkCrew",
        "tasks": [
            "evidence_collection_task",
            "execution_evaluation_task",
            "eval_qa_gate_task",
        ],
        "qa": "eval_qa_gate_task",
        "artifact": "execution_evaluation_task",
        "field": "working_papers",
        "make": make_papers,
        "start": "RUNNING_PHASE_2",
        "run": "generate_fieldwork",
    },
    3: {
        "crew": "ReportingCrew",
        "tasks": [
            "drafting_task",
            "executive_summary_task",
            "tone_qa_task",
            "generate_oscal_sar_task",
            "final_report_assembly_task",
        ],
        "qa": "tone_qa_task",
        "artifact": "final_report_assembly_task",
        "field": "final_report",
        "make": make_report,
        "start": "RUNNING_PHASE_3",
        "run": "generate_reporting",
    },
}


def crew_result(phase: int, qa, artifact):
    spec = PHASES[phase]
    outputs = []
    for name in spec["tasks"]:
        t = MagicMock()
        t.name = name
        t.pydantic = None
        if name == spec["qa"]:
            t.pydantic = qa
        elif name == spec["artifact"]:
            t.pydantic = artifact
        outputs.append(t)
    result = MagicMock()
    result.tasks_output = outputs
    return result


def make_flow(phase: int) -> AuditFlow:
    flow = AuditFlow(initial_status=PHASES[phase]["start"])
    flow.state.theme = "AWS S3"
    flow.state.business_context = "Fintech storing customer data in S3"
    flow.state.frameworks = ["CIS AWS"]
    if phase >= 2:
        flow.state.racm_plan = make_racm()
    if phase >= 3:
        flow.state.working_papers = make_papers()
    return flow


def run_phase(flow: AuditFlow, phase: int, results):
    mock_crew = MagicMock()
    mock_crew.kickoff.side_effect = list(results)
    with patch(f"swarm.audit_flow.{PHASES[phase]['crew']}") as MockCrew:
        MockCrew.return_value.crew.return_value = mock_crew
        getattr(flow, PHASES[phase]["run"])()
    return mock_crew, MockCrew


APPROVED = QA_PushbackSchema(approved=True)
