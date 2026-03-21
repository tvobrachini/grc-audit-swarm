import logging
from typing import List
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from swarm.state.schema import AuditState
from swarm.runtime_adapters import build_llm_adapter
from swarm.skill_loader import get_skill_by_id, get_focus_domains

logger = logging.getLogger(__name__)


class ReviewOutput(BaseModel):
    is_approved: bool = Field(
        description="True if the control matrix is comprehensive, relevant, and non-contradictory. False if it needs revision."
    )
    feedback: str = Field(
        description="Detailed feedback to the Mapper/Specialist on what needs to be fixed. Empty if approved."
    )


def challenger_review(state: AuditState) -> dict:
    """
    Agent 5 (Challenger):
    Reviews the compiled control matrix before it is presented to a human.
    Checks for logical gaps, missing obvious controls based on the scope,
    or vague procedures.
    """
    logger.info("[Challenger] Reviewing draft matrix for completeness and rigor...")

    runtime = build_llm_adapter(temperature=0)
    if not runtime.is_live:
        logger.info("[Challenger] %s Auto-approving for emulation.", runtime.reason)
        return _emulate_challenger(state)
    llm = runtime.llm

    # Serialize the matrix for the LLM context
    matrix_str = ""
    for item in state.control_matrix:
        matrix_str += f"Control ID: {item.control_id} ({item.domain})\n"
        if item.procedures:
            matrix_str += f" - TOD Steps: {item.procedures.tod_steps}\n"
            matrix_str += f" - TOE Steps: {item.procedures.toe_steps}\n"

    review_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are the Lead QA Audit Partner. Your job is to rigorously review the draft audit matrix against the original scope. If the test steps are too generic, or if apparent risk themes are missing coverage, you must REJECT it and provide feedback. If it looks comprehensive, APPROVE it.",
            ),
            (
                "human",
                "Original Narrative Scope: {scope}\n"
                "Identified Themes: {themes}\n\n"
                "Draft Control Matrix & Procedures:\n{matrix}\n\n"
                "Does this matrix adequately and specifically address the scope?",
            ),
        ]
    )

    structured_reviewer = llm.with_structured_output(ReviewOutput)
    reviewer_chain = review_prompt | structured_reviewer

    try:
        logger.info("[Challenger] Prompting Lead QA Partner LLM...")
        result = reviewer_chain.invoke(
            {
                "scope": state.audit_scope_narrative,
                "themes": ", ".join(state.risk_themes),
                "matrix": matrix_str,
            }
        )

        status = "Approved" if result.is_approved else "Rejected"
        feedback = result.feedback

    except Exception as e:
        logger.warning("[Challenger] LLM review failed: %s", e)
        return _emulate_challenger(state)

    logger.info("[Challenger] Status: %s", status)
    if feedback:
        logger.info("[Challenger] Feedback: %s", feedback)

    audit_trail_entries = [
        {
            "agent_or_user_id": "Agent 5 (Challenger/QA Lead)",
            "action_taken": f"Reviewed Draft Matrix: {status}",
            "reasoning_snapshot": feedback
            if feedback
            else "Matrix meets quality standards.",
            "approval_status": "Auto-Approved",
        }
    ]

    return {
        "revision_feedback": feedback if not result.is_approved else "",
        "audit_trail": state.audit_trail + audit_trail_entries,
    }


def _emulate_challenger(state: AuditState) -> dict:
    """Mock fallback logic."""
    audit_trail_entries = [
        {
            "agent_or_user_id": "Agent 5 (Challenger Mock)",
            "action_taken": "Reviewed Draft Matrix: Approved",
            "reasoning_snapshot": "Mocked approval.",
            "approval_status": "Auto-Approved",
        }
    ]

    return {
        "revision_feedback": "",
        "audit_trail": state.audit_trail + audit_trail_entries,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2: Challenger Reviews Execution Findings
# ══════════════════════════════════════════════════════════════════════════════


class FindingsChallengeOutput(BaseModel):
    overall_quality: str = Field(description="'Approved' or 'Concerns Raised'.")
    calibration_notes: List[str] = Field(
        description="List of specific notes per finding where severity, classification, or evidence is inconsistent. "
        "Each note should reference the exact control ID. Empty list if no concerns."
    )
    summary: str = Field(
        description="A 1-2 sentence summary of the overall findings quality review."
    )


def challenge_execution_findings(state: AuditState) -> dict:
    """
    Phase 2 Challenger: Acts as a senior audit partner reviewing all findings
    BEFORE the human sees them. Checks for:
    - Logical inconsistencies (e.g., TOD pass + TOE fail → should be Exception not Fail)
    - Evidence sufficiency (Pass findings with no evidence)
    - Severity calibration (single-sample exception marked High Risk)
    - Contradictions across controls in the same domain
    """
    findings = state.testing_findings
    if not findings:
        return {}

    logger.info(
        "[Phase2 Challenger] QA reviewing %d findings for consistency and calibration...",
        len(findings),
    )

    runtime = build_llm_adapter(temperature=0, prefer_fast=True)
    if not runtime.is_live:
        logger.info("[Phase2 Challenger] %s Emulating logic.", runtime.reason)
        return _emulate_phase2_challenger(state)
    llm = runtime.llm

    # Serialize findings for LLM review
    findings_text = "\n\n".join(
        [
            f"Control: {f.control_id} | Status: {f.status} | Risk: {f.risk_rating or 'N/A'}\n"
            f"TOD: {f.tod_result or '—'} | TOE: {f.toe_result or '—'} | Substantive: {f.substantive_result or '—'}\n"
            f"Evidence count: {len(f.evidence_extracted)} items\n"
            f"Finding: {f.justification[:300]}..."
            for f in findings
        ]
    )

    # Load skill focus domains for calibration context
    skill_names = (
        ", ".join(state.active_skill_names)
        if state.active_skill_names
        else "General ITGC"
    )
    focus_domain_context = ""
    if state.active_skill_ids:
        skills = [s for sid in state.active_skill_ids if (s := get_skill_by_id(sid))]
        domains = get_focus_domains(skills)
        if domains:
            focus_domain_context = (
                f"\nPriority control domains for this audit specialization ({skill_names}):\n"
                + ", ".join(domains)
                + "\nApply domain-appropriate severity standards when calibrating findings in these areas."
            )

    challenge_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a Senior Audit Partner conducting a quality review of AI-generated audit findings "
                "before they are presented to the Audit Committee. Your role is to challenge findings that:\n"
                "1. Have logical inconsistencies (e.g., TOD=Pass, TOE=Fail but marked as 'Fail' instead of 'Exception')\n"
                "2. Have insufficient evidence (Pass with 0 evidence items = suspicious)\n"
                "3. Have miscalibrated severity (single occurrence marked High Risk needs justification)\n"
                "4. Have contradictions between controls in the same domain\n\n"
                + focus_domain_context
                + "\n\nBe rigorous but fair. If findings are well-calibrated, approve them.",
            ),
            (
                "human",
                "Audit Scope: {scope}\n"
                "Active Specializations: {skills}\n\n"
                "Findings to Review:\n{findings}\n\n"
                "Conduct your quality review.",
            ),
        ]
    )

    chain = challenge_prompt | llm.with_structured_output(FindingsChallengeOutput)

    try:
        result = chain.invoke(
            {
                "scope": state.audit_scope_narrative[:400],
                "skills": skill_names,
                "findings": findings_text,
            }
        )

        notes_text = ""
        if result.calibration_notes:
            notes_text = "\n\n**⚖️ Challenger Quality Notes:**\n" + "\n".join(
                f"- {note}" for note in result.calibration_notes
            )

        logger.info("[Phase2 Challenger] Quality review: %s", result.overall_quality)
        if result.calibration_notes:
            logger.info(
                "[Phase2 Challenger] %d calibration note(s) added to findings.",
                len(result.calibration_notes),
            )

        return {
            "audit_trail": state.audit_trail
            + [
                {
                    "agent_or_user_id": "Phase 2 Challenger (Senior Audit Partner)",
                    "action_taken": f"Findings QA Review: {result.overall_quality}. {result.summary}",
                    "reasoning_snapshot": notes_text
                    or "No calibration concerns — findings meet quality standards.",
                    "approval_status": "Auto-Approved",
                }
            ],
            # Append challenger notes to executive summary if concerns raised
            "executive_summary": (
                (state.executive_summary or "")
                + (
                    f"\n\n---\n**⚖️ Senior Audit Partner QA Notes:**\n{notes_text}"
                    if notes_text
                    else ""
                )
            ),
        }

    except Exception as e:
        logger.warning("[Phase2 Challenger] Failed: %s", e)
        return _emulate_phase2_challenger(state)


def _emulate_phase2_challenger(state: AuditState) -> dict:
    """Mock Phase 2 challenger."""
    findings = state.testing_findings
    concerns = []
    for f in findings:
        if f.status == "Pass" and len(f.evidence_extracted) == 0:
            concerns.append(
                f"{f.control_id}: Marked Pass but no evidence extracted — recommend manual verification."
            )
        if f.tod_result == "Pass" and f.toe_result == "Fail" and f.status == "Fail":
            concerns.append(
                f"{f.control_id}: TOD=Pass + TOE=Fail pattern suggests 'Exception' may be more accurate than 'Fail'."
            )

    quality = "Concerns Raised" if concerns else "Approved"
    note = (
        (
            "\n\n**⚖️ Challenger Quality Notes:**\n"
            + "\n".join(f"- {c}" for c in concerns)
        )
        if concerns
        else ""
    )

    return {
        "audit_trail": state.audit_trail
        + [
            {
                "agent_or_user_id": "Phase 2 Challenger Mock (Senior Audit Partner)",
                "action_taken": f"Findings QA: {quality}. {len(concerns)} calibration note(s).",
                "reasoning_snapshot": note or "No calibration concerns in mock review.",
                "approval_status": "Auto-Approved",
            }
        ],
        "executive_summary": (state.executive_summary or "")
        + (f"\n\n---\n{note}" if note else ""),
    }
