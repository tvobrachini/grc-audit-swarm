"""
Agent: Concluder
----------------
Runs after all Worker agents complete.
Aggregates all AuditFindings, calculates the overall risk score,
and drafts an Executive Summary paragraph for the findings dashboard.
"""

import logging

from pydantic import BaseModel, Field

from langchain_core.prompts import ChatPromptTemplate

from swarm.state.schema import AuditState
from swarm.runtime_adapters import build_llm_adapter

logger = logging.getLogger(__name__)


class ConcluderOutput(BaseModel):
    executive_summary: str = Field(
        description="A 2-3 paragraph executive summary of the audit findings, suitable for a CAO or CTO audience. "
        "Include: overall risk posture and top findings. DO NOT propose remediation action plans."
    )
    overall_risk_score: str = Field(
        description="'Critical', 'High', 'Medium', or 'Low' based on the aggregate findings."
    )


def produce_executive_summary(state: AuditState) -> dict:
    """
    Concluder agent: aggregates all findings and writes an executive summary.
    """
    findings = state.testing_findings
    if not findings:
        return {
            "executive_summary": "No findings were generated.",
            "audit_trail": state.audit_trail,
        }

    total = len(findings)
    passes = sum(1 for f in findings if f.status == "Pass")
    exceptions = sum(1 for f in findings if f.status == "Exception")
    fails = sum(1 for f in findings if f.status == "Fail")
    highs = sum(1 for f in findings if f.risk_rating == "High")

    logger.info(
        "[Concluder] Aggregating %d findings: %d Pass, %d Exception, %d Fail",
        total,
        passes,
        exceptions,
        fails,
    )

    runtime = build_llm_adapter(temperature=0.4, prefer_fast=True)
    if not runtime.is_live:
        logger.info("[Concluder] %s Falling back to template.", runtime.reason)
        return _emulate_summary(state, passes, exceptions, fails, highs)
    llm = runtime.llm
    assert llm is not None

    findings_text = "\n\n".join(
        [
            f"Control: {f.control_id} | Status: {f.status} | Risk: {f.risk_rating or 'N/A'}\n"
            f"Finding: {f.justification}\n"
            f"Evidence: {'; '.join(f.evidence_extracted[:2])}"
            for f in findings
        ]
    )

    scope = state.audit_scope_narrative[:500]
    skills = (
        ", ".join(state.active_skill_names)
        if state.active_skill_names
        else "General ITGC"
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a Chief Audit Executive drafting an executive summary for an IT audit engagement. "
                "Your audience is the Audit Committee and CISO. Be direct, factual, and risk-focused. "
                "Do not soften findings — use precise language about what failed and why it matters.",
            ),
            (
                "human",
                f"Audit Scope: {scope}\n"
                f"Active Skill Specializations: {skills}\n\n"
                f"Findings Summary:\n"
                f"Total Controls Tested: {total}\n"
                f"Pass: {passes} | Exception: {exceptions} | Fail: {fails}\n"
                f"High Risk Findings: {highs}\n\n"
                f"Detailed Findings:\n{findings_text}\n\n"
                "Write the executive summary.",
            ),
        ]
    )

    chain = prompt | llm.with_structured_output(ConcluderOutput)
    try:
        result = chain.invoke({})
        overall_risk = result.overall_risk_score
        summary = result.executive_summary
    except Exception as e:
        logger.warning("[Concluder] LLM failed. Falling back to template. Error: %s", e)
        return _emulate_summary(state, passes, exceptions, fails, highs)

    trail_entry = {
        "agent_or_user_id": "Concluder Agent",
        "action_taken": f"Executive Summary produced. Overall Risk: {overall_risk}. {passes}P/{exceptions}E/{fails}F.",
        "reasoning_snapshot": f"Aggregated {total} findings across {len(set(f.control_id.split('-')[0] for f in findings))} domains.",
        "approval_status": "Auto-Approved",
    }

    return {
        "executive_summary": f"**Overall Risk: {overall_risk}**\n\n{summary}",
        "audit_trail": state.audit_trail + [trail_entry],
    }


def _emulate_summary(
    state: AuditState, passes: int, exceptions: int, fails: int, highs: int
) -> dict:
    total = passes + exceptions + fails
    if fails > 0 or highs > 0:
        posture = "**Overall Risk: HIGH**"
        assessment = (
            f"The audit identified **{fails} control failure(s)** and **{exceptions} exception(s)** "
            f"across {total} controls tested. Management attention is required for the {highs} high-risk finding(s)."
        )
    elif exceptions > 0:
        posture = "**Overall Risk: MEDIUM**"
        assessment = (
            f"The audit identified **{exceptions} exception(s)** out of {total} controls tested. "
            f"No critical failures were found, but management attention is required for the {exceptions} exceptions."
        )
    else:
        posture = "**Overall Risk: LOW**"
        assessment = (
            f"All {total} controls tested passed successfully. "
            f"The control environment is operating effectively for the audit period. "
            f"Continued monitoring is recommended."
        )

    scope_preview = state.audit_scope_narrative[:120]
    skills = (
        ", ".join(state.active_skill_names)
        if state.active_skill_names
        else "General ITGC"
    )

    summary = (
        f"{posture}\n\n"
        f"**Audit Scope:** {scope_preview}...\n"
        f"**Specializations Applied:** {skills}\n\n"
        f"**Results:** {passes} Pass | {exceptions} Exception | {fails} Fail (of {total} total)\n\n"
        f"{assessment}\n\n"
        f"*This summary was generated by the GRC Audit Swarm. Human auditor review and sign-off required before distribution.*"
    )

    return {
        "executive_summary": summary,
        "audit_trail": state.audit_trail
        + [
            {
                "agent_or_user_id": "Concluder Agent (Mock)",
                "action_taken": f"Template summary produced. {passes}P/{exceptions}E/{fails}F.",
                "reasoning_snapshot": "No LLM available; template-based summary generated.",
                "approval_status": "Auto-Approved",
            }
        ],
    }
