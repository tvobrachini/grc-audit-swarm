import json
import logging
import os
from pathlib import Path
from typing import List

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from swarm.runtime_adapters import build_llm_adapter
from swarm.state.schema import AuditProcedure, AuditState, ControlMatrixItem

logger = logging.getLogger(__name__)

# Resolve the SCF database path relative to this file — works regardless of cwd
_THIS_DIR = Path(__file__).resolve().parent
_DEFAULT_SCF_PATH = _THIS_DIR.parent.parent.parent / "data" / "scf_parsed.json"


def get_scf_db_path() -> str:
    env_path = os.environ.get("SCF_DATA_PATH")
    if env_path:
        return env_path
    return str(_DEFAULT_SCF_PATH)


class ScfMappingOutput(BaseModel):
    selected_control_ids: List[str] = Field(
        description="List of exact SCF Control IDs (e.g. 'AC-01', 'CRY-02') that best map to the provided risk themes."
    )
    mapping_justification: str = Field(
        description="Brief explanation of why these specific controls were selected for the given risk themes."
    )


class AuditProcedureOutput(BaseModel):
    control_id: str = Field(
        description="The ID of the control this procedure belongs to"
    )
    tod_steps: List[str] = Field(
        description="Test of Design steps (e.g., policy review, settings check)."
    )
    toe_steps: List[str] = Field(
        description="Test of Effectiveness steps (e.g., sampling logs over time)."
    )
    substantive_steps: List[str] = Field(
        description="Substantive testing steps for deep-dive transactional evaluation."
    )
    erl_items: List[str] = Field(
        description="Evidence Request List items needed from the auditee."
    )


def map_controls_and_design_tests(state: AuditState) -> dict:
    """
    Control Mapper:
    Phase 2a: Pulls relevant controls from SCF based on Orchestrator's risk themes.
    Phase 2b: Designs baseline test procedures for those controls.
    Incorporates Challenger QA feedback when revising after a rejection.
    """
    logger.info("[Mapper] Analysing risk themes: %s", ", ".join(state.risk_themes))

    runtime = build_llm_adapter(temperature=0)
    if not runtime.is_live:
        logger.warning("[Mapper] %s Emulating logic.", runtime.reason)
        return _emulate_mapping(state)
    llm = runtime.llm
    if llm is None:
        return _emulate_mapping(state)

    scf_path = get_scf_db_path()
    try:
        with open(scf_path, encoding="utf-8") as f:
            scf_db = json.load(f)
    except FileNotFoundError:
        logger.warning("[Mapper] SCF database not found at %s. Emulating.", scf_path)
        return _emulate_mapping(state)

    # Heuristic filtering: score controls against risk themes
    themes_lower = " ".join(state.risk_themes).lower()
    candidate_controls = []

    for control in scf_db:
        score = 0
        desc_lower = control.get("description", "").lower()
        domain_lower = control.get("domain", "").lower()

        for word in themes_lower.split():
            if len(word) > 3:
                if word in desc_lower:
                    score += 1
                if word in domain_lower:
                    score += 2

        if score > 0:
            candidate_controls.append(
                {
                    "id": control["control_id"],
                    "domain": control["domain"],
                    "description": control["description"],
                    "score": score,
                }
            )

    candidate_controls.sort(key=lambda x: x["score"], reverse=True)
    top_candidates = candidate_controls[:20]

    if not top_candidates:
        logger.info("[Mapper] No heuristic matches found. Defaulting to emulation.")
        return _emulate_mapping(state)

    context_str = "\n".join(
        [
            f"- {c['id']} ({c['domain']}): {c['description'][:100]}..."
            for c in top_candidates
        ]
    )

    # Include Challenger QA feedback when re-mapping after a revision cycle
    challenger_note = ""
    if state.challenger_feedback:
        challenger_note = (
            f"\n\nQA Challenger feedback from previous review cycle — "
            f"address these specific gaps when selecting controls:\n{state.challenger_feedback}"
        )
        logger.info(
            "[Mapper] Incorporating Challenger feedback into control selection."
        )

    mapping_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are an expert IT Auditor. Map the identified risk themes and real-world Risk Context "
                "1-Pager to the most relevant controls from the provided framework candidate list. "
                "Select up to 3 core controls that directly address the citations/breaches mentioned. "
                "DO NOT select vaguely related controls — anchor them to the real-world risk context.",
            ),
            (
                "human",
                "Risk Themes: {themes}\n\n"
                "1-Pager Risk Context:\n{risk_context}\n\n"
                "Candidate Controls:\n{context}"
                "{challenger_note}\n\n"
                "Select the best controls to audit.",
            ),
        ]
    )

    mapper_chain = mapping_prompt | llm.with_structured_output(ScfMappingOutput)

    logger.info("[Mapper] Asking LLM to select from candidate controls...")
    try:
        mapping_result = mapper_chain.invoke(
            {
                "themes": ", ".join(state.risk_themes),
                "risk_context": state.risk_context_document,
                "context": context_str,
                "challenger_note": challenger_note,
            }
        )
        selected_ids = mapping_result.selected_control_ids
        justification = mapping_result.mapping_justification
    except Exception as exc:
        logger.warning("[Mapper] LLM mapping failed: %s. Emulating.", exc)
        return _emulate_mapping(state)

    selected_full_controls = [c for c in scf_db if c["control_id"] in selected_ids]

    # Phase 2b: Design procedures for each selected control
    design_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a Senior IT Auditor. Design detailed audit procedures "
                "(TOD, TOE, Substantive, and ERL) for the provided baseline IT control. "
                "Be specific and tactical.",
            ),
            (
                "human",
                "Design an Audit Procedure for this control:\n"
                "ID: {control_id}\nDomain: {domain}\nDescription: {description}",
            ),
        ]
    )

    designer_chain = design_prompt | llm.with_structured_output(AuditProcedureOutput)
    control_matrix = []

    logger.info("[Mapper] Designing baseline audit procedures...")
    for control in selected_full_controls:
        try:
            procedure_result = designer_chain.invoke(
                {
                    "control_id": control["control_id"],
                    "domain": control["domain"],
                    "description": control["description"],
                }
            )

            control_matrix.append(
                ControlMatrixItem(
                    control_id=control["control_id"],
                    domain=control["domain"],
                    description=control["description"],
                    procedures=AuditProcedure(
                        control_id=procedure_result.control_id,
                        tod_steps=procedure_result.tod_steps,
                        toe_steps=procedure_result.toe_steps,
                        substantive_steps=procedure_result.substantive_steps,
                        erl_items=procedure_result.erl_items,
                    ),
                )
            )
        except Exception as exc:
            logger.warning(
                "[Mapper] Procedure design failed for %s: %s — skipping.",
                control["control_id"],
                exc,
            )

    if not control_matrix:
        logger.warning("[Mapper] Matrix empty after processing. Falling back.")
        return _emulate_mapping(state)

    audit_trail_entries = [
        {
            "agent_or_user_id": "Control Mapper",
            "action_taken": f"Selected {len(selected_full_controls)} SCF controls.",
            "reasoning_snapshot": justification,
            "approval_status": "Auto-Approved",
        },
        {
            "agent_or_user_id": "Procedure Designer",
            "action_taken": "Designed baseline audit procedures for selected controls.",
            "reasoning_snapshot": "Based on ISACA IT Audit frameworks and SCF control descriptions.",
            "approval_status": "Auto-Approved",
        },
    ]

    logger.info("[Mapper] Complete. Designed initial control matrix.")
    return {
        "control_matrix": control_matrix,
        "audit_trail": state.audit_trail + audit_trail_entries,
    }


def _emulate_mapping(state: AuditState) -> dict:
    """Fallback logic when no API key is present or LLM call fails."""
    themes = state.risk_themes
    matrix = []

    if "AWS Cloud Infrastructure" in themes:
        matrix.append(
            ControlMatrixItem(
                control_id="CST-01",
                domain="Cloud Security",
                description="Cloud environments are secured consistently with on-premise minimum security standards.",
                procedures=AuditProcedure(
                    control_id="CST-01",
                    tod_steps=["Review Cloud Security Architecture Policy."],
                    toe_steps=[
                        "Verify AWS Security Hub is enabled and active across all accounts."
                    ],
                    substantive_steps=[
                        "Sample 5 EC2 instances and verify they apply the approved Golden AMI."
                    ],
                    erl_items=[
                        "Cloud Security Policy",
                        "Screenshot of AWS Security Hub Dashboard status",
                    ],
                ),
            )
        )
        matrix.append(
            ControlMatrixItem(
                control_id="AC-01",
                domain="Access Control",
                description="Access to cloud environments is strictly controlled using IAM policies and logically restricted.",
                procedures=AuditProcedure(
                    control_id="AC-01",
                    tod_steps=[
                        "Review Cloud Access Management policies specifying MFA constraints."
                    ],
                    toe_steps=[
                        "Examine IAM Identity Center setup for enforced MFA on AWS console."
                    ],
                    substantive_steps=[
                        "Pull IAM credentials report and verify no root users have active access keys."
                    ],
                    erl_items=[
                        "AWS IAM Credentials Report",
                        "IAM Identity Center Policy settings",
                    ],
                ),
            )
        )
        matrix.append(
            ControlMatrixItem(
                control_id="LOG-04",
                domain="Audit & Accountability",
                description="Centralized audit logging is enabled and protected from tampering.",
                procedures=AuditProcedure(
                    control_id="LOG-04",
                    tod_steps=[
                        "Analyse Logging Strategy Document for CloudTrail requirements."
                    ],
                    toe_steps=[
                        "Ensure AWS CloudTrail is enabled on all regions and log file validation is ON."
                    ],
                    substantive_steps=[
                        "Trace a sample of CloudTrail events to the SIEM platform."
                    ],
                    erl_items=[
                        "CloudTrail status screenshots",
                        "SIEM integration evidence",
                    ],
                ),
            )
        )
    elif "PCI-DSS Payment Processing" in themes:
        matrix.append(
            ControlMatrixItem(
                control_id="CRY-01",
                domain="Cryptography",
                description="Sensitive data, including cardholder data, is encrypted at rest and in transit.",
                procedures=AuditProcedure(
                    control_id="CRY-01",
                    tod_steps=[
                        "Review Encryption Policy mapping to PCI DSS Req 3 & 4."
                    ],
                    toe_steps=[
                        "Verify RDS databases containing CHD have KMS encryption enabled."
                    ],
                    substantive_steps=[
                        "Sample network traffic captures (TLS 1.2+ forced)."
                    ],
                    erl_items=["Encryption Standard Doc", "AWS KMS Key configurations"],
                ),
            )
        )
        matrix.append(
            ControlMatrixItem(
                control_id="NET-02",
                domain="Network Security",
                description="Firewall rules restricting incoming and outgoing traffic to the cardholder data environment.",
                procedures=AuditProcedure(
                    control_id="NET-02",
                    tod_steps=["Inspect Firewall ruleset review policy."],
                    toe_steps=[
                        "Confirm the last semi-annual firewall ruleset review was signed off."
                    ],
                    substantive_steps=[
                        "Sample egress security group configurations preventing outside lateral movement."
                    ],
                    erl_items=[
                        "Firewall Ruleset Review Approval",
                        "AWS Security Group configs",
                    ],
                ),
            )
        )
        matrix.append(
            ControlMatrixItem(
                control_id="VUL-03",
                domain="Vulnerability Management",
                description="Critical security patches are applied within 30 days of release to CHD systems.",
                procedures=AuditProcedure(
                    control_id="VUL-03",
                    tod_steps=["Read Patch Management SLA definitions."],
                    toe_steps=[
                        "Confirm active vulnerability scanning tools are authenticating onto hosts."
                    ],
                    substantive_steps=[
                        "Sample 3 recent critical CVEs and verify patching times against Splunk logs."
                    ],
                    erl_items=["Patch Management SLAs", "Tenable/Qualys Scan Reports"],
                ),
            )
        )
    else:  # ITGC default
        matrix.append(
            ControlMatrixItem(
                control_id="AC-03",
                domain="Access Control",
                description="User access is reviewed periodically.",
                procedures=AuditProcedure(
                    control_id="AC-03",
                    tod_steps=[
                        "Review Access Management Policy for quarterly review requirements."
                    ],
                    toe_steps=[
                        "Inspect the last 2 quarters documented access reviews for completion."
                    ],
                    substantive_steps=[
                        "Sample 10 random active AD users and trace back to their access review approval."
                    ],
                    erl_items=[
                        "Access Control Policy",
                        "Q1 and Q2 User Access Review sign-off sheets",
                    ],
                ),
            )
        )
        matrix.append(
            ControlMatrixItem(
                control_id="CHG-02",
                domain="Change Management",
                description="All production changes are tested and approved prior to rollout.",
                procedures=AuditProcedure(
                    control_id="CHG-02",
                    tod_steps=["Review SDLC policy requiring QA and peer reviews."],
                    toe_steps=[
                        "Verify Jira/ServiceNow workflow mandates approval gates."
                    ],
                    substantive_steps=[
                        "Select 5 recent PRs merged to main; verify peer approval and QA signoff exist."
                    ],
                    erl_items=["SDLC Document", "Sample Jira Change Tickets"],
                ),
            )
        )
        matrix.append(
            ControlMatrixItem(
                control_id="DCH-01",
                domain="Data Center Security",
                description="Physical access to data centers is restricted to authorized individuals.",
                procedures=AuditProcedure(
                    control_id="DCH-01",
                    tod_steps=[
                        "Read Physical Security policy outlining badging systems."
                    ],
                    toe_steps=[
                        "Review the active badge list vs. the authorized data center personnel list."
                    ],
                    substantive_steps=[
                        "Test the badge entry logs to find instances of tailgating alarms or unauthorized attempts."
                    ],
                    erl_items=["Physical Security Policy", "Badge Access Log Report"],
                ),
            )
        )

    audit_trail_entries = [
        {
            "agent_or_user_id": "Control Mapper (Mock)",
            "action_taken": "Mocked baseline control mapping and procedure design.",
            "reasoning_snapshot": f"Based on themes: {themes}",
            "approval_status": "Auto-Approved",
        }
    ]

    return {
        "control_matrix": matrix,
        "audit_trail": state.audit_trail + audit_trail_entries,
    }
