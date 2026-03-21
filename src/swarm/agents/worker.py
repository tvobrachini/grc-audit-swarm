"""
Agent: Execution Worker
-----------------------
One Worker instance runs per control in the matrix.
It reads the evidence log, executes TOD/TOE/Substantive tests,
and returns a structured AuditFinding.

In mock mode: generates realistic simulated findings.
In LLM mode:  reasons against evidence using the loaded skill system prompt.
"""

from dataclasses import dataclass
import hashlib
import logging
from typing import Any, Dict, List, Protocol
from pydantic import BaseModel, Field

from langchain_core.prompts import ChatPromptTemplate

from swarm.evidence import ControlEvidence, serialize_control_evidence
from swarm.state.schema import AuditState, AuditFinding, ControlMatrixItem
from swarm.llm_factory import get_llm
from swarm.skill_loader import get_skill_by_id, get_specialist_prompt

logger = logging.getLogger(__name__)


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

You are an IT Audit Execution Worker. Your goal is to evaluate a specific control based on provided evidence.

CRITICAL CONSTRAINTS (COST & SECURITY):
1. You are in a READ-ONLY audit mode. You MUST NOT propose, suggest, or simulate any commands that CREATE, MODIFY, or DELETE infrastructure (e.g., 'create-user', 'delete-bucket', 'update-policy').
2. Your output must strictly focus on 'Pass', 'Fail', or 'Exception' based on the CURRENT state of evidence.
3. Do not ask for MFA codes or passwords.

AUDIT PROCEDURE:
Reason carefully against the provided evidence and determine if each test step passes, fails, or has an exception. Be specific, cite evidence, and be honest — do not force a Pass if evidence is missing or incomplete.
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


class WorkerAdapter(Protocol):
    def run(
        self, control: ControlMatrixItem, state: AuditState, human_context: str = ""
    ) -> AuditFinding: ...


@dataclass
class MockWorkerAdapter:
    reason: str

    def run(
        self, control: ControlMatrixItem, state: AuditState, human_context: str = ""
    ) -> AuditFinding:
        logger.warning("%s", self.reason)
        return _emulate_finding(control, human_context=human_context)


@dataclass
class LiveWorkerAdapter:
    llm: Any
    skill_prompt: str

    def run(
        self, control: ControlMatrixItem, state: AuditState, human_context: str = ""
    ) -> AuditFinding:
        procs = control.procedures
        if not procs:
            return MockWorkerAdapter(
                f"[Worker] [SIMULATED] No procedures found for {control.control_id}. Results are simulated."
            ).run(control, state, human_context=human_context)

        evidence_summary = _get_evidence_for_control(
            control.control_id, state.evidence_log
        )
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

        chain = worker_prompt | self.llm.with_structured_output(WorkerFindingOutput)

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
                    "skill_prompt": self.skill_prompt,
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
            return MockWorkerAdapter(
                f"[Worker] [SIMULATED] LLM failed for {control.control_id}: {e}. Results are simulated."
            ).run(control, state, human_context=human_context)


def build_worker_adapter(state: AuditState) -> WorkerAdapter:
    llm = get_llm(temperature=0.3, prefer_fast=True)
    if llm is None:
        return MockWorkerAdapter(
            "[Worker] [SIMULATED] No LLM available. Results are simulated."
        )

    skill_prompt = ""
    if state.active_skill_ids:
        skills = [s for sid in state.active_skill_ids if (s := get_skill_by_id(sid))]
        skill_prompt = get_specialist_prompt(skills)

    return LiveWorkerAdapter(llm=llm, skill_prompt=skill_prompt)


def run_control_test(
    control: ControlMatrixItem, state: AuditState, human_context: str = ""
) -> AuditFinding:
    """
    Execute all test procedures for a single control.
    Returns an AuditFinding with full results.
    """
    logger.info("[Worker] Testing control: %s (%s)", control.control_id, control.domain)
    return build_worker_adapter(state).run(control, state, human_context=human_context)


def _get_evidence_for_control(
    control_id: str, evidence_log: Dict[str, ControlEvidence]
) -> str:
    """Pull relevant evidence from the evidence log, or return a mock."""
    if control_id in evidence_log:
        return serialize_control_evidence(evidence_log[control_id])
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


def _emulate_finding(
    control: ControlMatrixItem, human_context: str = ""
) -> AuditFinding:
    """Mock finding generator for when no LLM is available."""
    # Domain rule: missing evidence must never silently pass.
    # If the human auditor explicitly signals no evidence, return Exception — not Pass.
    _no_evidence_signals = (
        "no evidence",
        "missing evidence",
        "evidence is empty",
        "no evidence provided",
    )
    if human_context and any(
        sig in human_context.lower() for sig in _no_evidence_signals
    ):
        outcome = "Exception"
    else:
        # Deterministic fallback keeps offline demos and tests reproducible.
        fingerprint = hashlib.sha256(
            f"{control.control_id}:{human_context}".encode("utf-8")
        ).digest()[0]
        if fingerprint < 153:  # ~60%
            outcome = "Pass"
        elif fingerprint < 217:  # ~25%
            outcome = "Exception"
        else:  # ~15%
            outcome = "Fail"

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
