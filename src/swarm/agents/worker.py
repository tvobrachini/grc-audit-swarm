"""
Agent: Execution Worker
-----------------------
One Worker instance runs per control in the matrix.
It reads the evidence log, executes TOD/TOE/Substantive tests,
and returns a structured AuditFinding.

In mock mode: generates realistic simulated findings.
In LLM mode:  reasons against evidence using the loaded skill system prompt.
"""

import random
from typing import List, Dict, Any
from pydantic import BaseModel, Field

from langchain_core.prompts import ChatPromptTemplate

from src.swarm.state.schema import AuditState, AuditFinding, ControlMatrixItem
from src.swarm.llm_factory import get_llm
from src.swarm.skill_loader import get_skill_by_id, get_specialist_prompt


class WorkerFindingOutput(BaseModel):
    status: str = Field(description="Exactly one of: 'Pass', 'Fail', 'Exception'")
    justification: str = Field(
        description="Detailed narrative explaining the status and what was found during testing."
    )
    evidence_extracted: List[str] = Field(
        description="Exact data points or quotes from the evidence that support this finding."
    )
    risk_rating: str = Field(description="'High', 'Medium', 'Low', or 'N/A' (for Pass)")
    tod_result: str = Field(description="'Pass' or 'Fail' for the Test of Design step.")
    toe_result: str = Field(
        description="'Pass', 'Fail', or 'Exception' for the Test of Effectiveness step."
    )
    substantive_result: str = Field(
        description="'Pass', 'Fail', or 'Exception' for the Substantive Testing step."
    )


WORKER_SYSTEM_PROMPT = """{skill_prompt}

You are executing an IT audit test for a specific control. Reason carefully against the provided evidence and determine if each test step passes, fails, or has an exception. Be specific, cite evidence, and be honest — do not force a Pass if evidence is missing or incomplete.
CRITICAL INSTRUCTION: DO NOT propose remediation, action plans, or recommendations. Evaluate ONLY the current state based on evidence. EVALUATE THE EVIDENCE STRICTLY.
{human_ctx_section}"""

WORKER_HUMAN_PROMPT = """Control ID: {control_id}
Domain: {domain}
Description: {description}

Procedures to execute:
TOD Steps: {tod}
TOE Steps: {toe}
Substantive Steps: {sub}

Available Evidence:
{evidence}

Execute these tests against the evidence and return your finding."""


def run_control_test(
    control: ControlMatrixItem, state: AuditState, human_context: str = ""
) -> AuditFinding:
    """
    Execute all test procedures for a single control.
    Returns an AuditFinding with full results.
    """
    print(f"[Worker] Testing control: {control.control_id} ({control.domain})")

    llm = get_llm(temperature=0.3, prefer_fast=True)
    if llm is None:
        return _emulate_finding(control)

    # Load skill context
    skill_prompt = ""
    if state.active_skill_ids:
        skills = [s for sid in state.active_skill_ids if (s := get_skill_by_id(sid))]
        skill_prompt = get_specialist_prompt(skills)

    # Serialize procedures
    procs = control.procedures
    if not procs:
        return _emulate_finding(control)

    evidence_summary = _get_evidence_for_control(control.control_id, state.evidence_log)
    human_ctx_section = (
        f"\n\nAdditional human auditor context:\n{human_context}"
        if human_context
        else ""
    )

    worker_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", WORKER_SYSTEM_PROMPT),
            ("human", WORKER_HUMAN_PROMPT),
        ]
    )

    chain = worker_prompt | llm.with_structured_output(WorkerFindingOutput)

    try:
        result = chain.invoke(
            {
                "control_id": control.control_id,
                "domain": control.domain,
                "description": control.description,
                "tod": "\n".join(procs.tod_steps),
                "toe": "\n".join(procs.toe_steps),
                "sub": "\n".join(procs.substantive_steps),
                "evidence": evidence_summary,
                "skill_prompt": skill_prompt,
                "human_ctx_section": human_ctx_section,
            }
        )

        return AuditFinding(
            control_id=control.control_id,
            agent_role=f"Execution Worker ({control.domain})",
            status=result.status,
            justification=result.justification,
            evidence_extracted=result.evidence_extracted,
            risk_rating=result.risk_rating if result.status != "Pass" else None,
            tod_result=result.tod_result,
            toe_result=result.toe_result,
            substantive_result=result.substantive_result,
        )
    except Exception as e:
        print(f"[Worker] LLM failed for {control.control_id}: {e}")
        return _emulate_finding(control)


def _get_evidence_for_control(control_id: str, evidence_log: Dict[str, Any]) -> str:
    """Pull relevant evidence from the evidence log, or return a mock."""
    if control_id in evidence_log:
        ev = evidence_log[control_id]
        return str(ev)
    # Return mock evidence when no real evidence log exists
    return _mock_evidence(control_id)


def _mock_evidence(control_id: str) -> str:
    """Generate realistic mock evidence based on control ID prefix."""
    mock_db = {
        "AC": (
            "IAM Credentials Report (2026-01-15): 342 active users extracted. "
            "Access review tracker shows 318/342 completed for Q4 2025. "
            "3 users (user_7892, user_1043, user_5511) have no review record. "
            "All 3 flagged accounts were in 'Pending Offboarding' state since Nov 30 acquisition."
        ),
        "LOG": (
            "AWS CloudTrail status: ENABLED across all 4 regions. "
            "Log file validation: ON. S3 bucket: ct-logs-prod (encrypted, versioning ON). "
            "SIEM (Splunk) integration confirmed: last event received 2026-03-07T18:42Z. "
            "Alert rule review: 12 of 15 security alerts have runbooks. 3 (IAM-004, S3-009, EC2-017) have no runbook."
        ),
        "CST": (
            "AWS Security Hub: ENABLED. CIS AWS Benchmark score: 87%. "
            "5 EC2 instances running non-Golden AMI (ami-legacy-0092). "
            "Golden AMI Policy: ami-gold-2025-q4. "
            "Config rule 'approved-amis-by-id' showing NON_COMPLIANT for instances in us-east-1b AZ."
        ),
        "CRY": (
            "RDS encryption audit: 12/14 databases have KMS encryption ENABLED. "
            "2 databases (rds-reporting-01, rds-archive-03) have encryption DISABLED. "
            "Both are tagged as 'cardholder-data: no' in resource tags but are connected to the CDE VPC. "
            "TLS Config: ACM certificate shows TLS 1.2 enforced. TLS 1.0/1.1 deprecated."
        ),
        "CHG": (
            "ServiceNow change management: 245 changes in Q4 2025. "
            "232/245 have full approval chain documented. "
            "13 emergency changes found: 11 have post-implementation review. "
            "2 emergency changes (CHG0094821, CHG0094903) lack post-implementation approval."
        ),
        "NET": (
            "AWS Security Group audit: 156 security groups. "
            "4 security groups have inbound 0.0.0.0/0 on port 22 (SSH). "
            "Firewall ruleset review: Last reviewed 2025-09-15 (182 days ago — exceeds 180-day requirement by 2 days). "
            "WAF: ENABLED on ALB. 99.2% rule coverage."
        ),
        "VUL": (
            "Tenable scan results (2026-03-01): 1 Critical, 4 High, 12 Medium CVEs. "
            "Critical: CVE-2025-44810 (OpenSSL) — patch available 2026-02-14, NOT applied (18 days). "
            "SLA: Critical CVEs must be patched within 15 days. "
            "Patch management tracker shows issue was triaged but no patch ticket created."
        ),
    }
    prefix = control_id.split("-")[0]
    return mock_db.get(
        prefix,
        f"Evidence log for {control_id}: Policy document retrieved. "
        "Last review date: 2025-10-12. No exceptions noted in management self-assessment. "
        "Configuration screenshots provided. 8/10 sample items validated.",
    )


def _emulate_finding(control: ControlMatrixItem) -> AuditFinding:
    """Mock finding generator for when no LLM is available."""
    # Weighted random: 60% Pass, 25% Exception, 15% Fail
    outcome = random.choices(["Pass", "Exception", "Fail"], weights=[60, 25, 15])[0]  # nosec B311

    # Use realistic mock evidence

    # Use realistic mock evidence
    evidence_text = _mock_evidence(control.control_id)

    justifications = {
        "Pass": f"All test steps for {control.control_id} executed successfully. Evidence reviewed and aligned with control description. Population validated, sample selected and traced.",
        "Exception": f"Testing of {control.control_id} revealed a minor exception: one or more sample items could not be fully validated due to incomplete documentation. Control design appears sound but effectiveness evidence is partially missing.",
        "Fail": f"Control {control.control_id} has FAILED. Evidence shows the control is either not designed or not operating effectively for the audit period. Specific deficiency identified and documented.",
    }

    risk_map = {"Pass": None, "Exception": "Medium", "Fail": "High"}

    step_results_pass = {"Pass": "Pass", "Exception": "Pass", "Fail": "Fail"}
    step_results_eff = {"Pass": "Pass", "Exception": "Exception", "Fail": "Fail"}
    step_results_sub = {"Pass": "Pass", "Exception": "Exception", "Fail": "Fail"}

    return AuditFinding(
        control_id=control.control_id,
        agent_role=f"Execution Worker - Mock ({control.domain})",
        status=outcome,
        justification=justifications[outcome],
        evidence_extracted=[
            evidence_text[:200] + "...",
            f"Control domain: {control.domain}",
            "Evidence log reviewed and cross-referenced with procedure steps.",
        ],
        risk_rating=risk_map[outcome],
        tod_result=step_results_pass[outcome],
        toe_result=step_results_eff[outcome],
        substantive_result=step_results_sub[outcome],
    )
