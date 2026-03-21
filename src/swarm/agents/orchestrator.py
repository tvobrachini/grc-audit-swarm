import logging
from typing import List

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from swarm.runtime_adapters import build_llm_adapter
from swarm.skill_loader import detect_skills_from_scope
from swarm.state.schema import AuditState

logger = logging.getLogger(__name__)


class OrchestratorOutput(BaseModel):
    risk_themes: List[str] = Field(
        description=(
            "High-level risk domains identified in the scope narrative. "
            "Examples: 'AWS Cloud Infrastructure', 'PCI-DSS Payment Processing', "
            "'GDPR Data Privacy', 'HIPAA Healthcare Compliance', 'General IT General Controls (ITGC)'."
        )
    )
    specialist_roles_required: List[str] = Field(
        description=(
            "Specialist auditor roles needed to cover the identified themes. "
            "Examples: 'AWS Cloud Security Architect', 'PCI Internal Auditor', "
            "'Data Privacy Officer (DPO)', 'HIPAA Privacy & Security Officer', 'IT General Auditor'."
        )
    )


_ORCHESTRATOR_SYSTEM = (
    "You are an experienced IT Audit Orchestrator. "
    "Read the audit scope narrative and extract the primary risk themes and the specialist "
    "auditor roles required to execute the audit. Be precise and domain-specific. "
    "If the scope mentions cloud infrastructure (AWS, GCP, Azure, EKS, S3, IAM), include cloud themes. "
    "If it mentions payments, card data, POS, or cardholder data, include PCI-DSS. "
    "If it mentions personal data, GDPR, data subjects, or EU, include privacy themes. "
    "If it mentions healthcare, ePHI, EHR, or HIPAA, include healthcare compliance. "
    "Default to 'General IT General Controls (ITGC)' only if no specific domain is identifiable."
)


def analyze_scope_and_themes(state: AuditState) -> dict:
    """
    Agent: Orchestrator — reads the raw scope and identifies themes & dynamic roles.
    Also runs skill auto-detection from the skills/ library.
    Uses an LLM when available; falls back to keyword heuristics.
    """
    scope_text = state.audit_scope_narrative
    logger.info("[Orchestrator] Analysing scope: '%s...'", scope_text[:80])

    themes, roles = _extract_themes_with_llm(scope_text)

    # Skill auto-detection (keyword-based, YAML library)
    matched_skills = detect_skills_from_scope(scope_text)
    skill_ids = [s.get("id", "") for s in matched_skills]
    skill_names = [s.get("name", "") for s in matched_skills]
    logger.info("[Orchestrator] Matched skills: %s", ", ".join(skill_names) or "None")

    return {
        "risk_themes": themes,
        "specialist_roles_required": roles,
        "active_skill_ids": skill_ids,
        "active_skill_names": skill_names,
        "audit_trail": [
            {
                "agent_or_user_id": "Orchestrator",
                "action_taken": (
                    f"Identified {len(themes)} risk themes: {', '.join(themes)}. "
                    f"Loaded skills: {', '.join(skill_names) or 'ITGC (default)'}."
                ),
                "reasoning_snapshot": (
                    f"Parsed scope narrative and auto-detected skill modules: "
                    f"{', '.join(skill_ids) or 'itgc_general'}"
                ),
                "approval_status": "Auto-Approved",
            }
        ],
    }


def _extract_themes_with_llm(scope_text: str):
    """Try LLM-based extraction; fall back to keyword heuristics if unavailable."""
    runtime = build_llm_adapter(temperature=0.0)
    if not runtime.is_live:
        logger.info("[Orchestrator] %s Using keyword heuristics.", runtime.reason)
        return _keyword_themes(scope_text)

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", _ORCHESTRATOR_SYSTEM),
            ("human", "Audit Scope Narrative:\n{scope}"),
        ]
    )
    assert runtime.llm is not None
    chain = prompt | runtime.llm.with_structured_output(OrchestratorOutput)
    try:
        result = chain.invoke({"scope": scope_text})
        themes = result.risk_themes or ["General IT General Controls (ITGC)"]
        roles = result.specialist_roles_required or ["IT General Auditor"]
        logger.info("[Orchestrator] LLM extracted themes: %s", ", ".join(themes))
        return themes, roles
    except Exception as exc:
        logger.warning("[Orchestrator] LLM extraction failed: %s — falling back.", exc)
        return _keyword_themes(scope_text)


def _keyword_themes(scope_text: str):
    """Keyword-based fallback theme extraction."""
    themes = []
    roles = []

    if "AWS" in scope_text or "EKS" in scope_text:
        themes.append("AWS Cloud Infrastructure")
        roles.append("AWS Cloud Security Architect")

    if (
        "payment" in scope_text.lower()
        or "credit card" in scope_text.lower()
        or "PCI" in scope_text
        or "cardholder" in scope_text.lower()
    ):
        themes.append("PCI-DSS Payment Processing")
        roles.append("PCI Internal Auditor")

    if (
        "HIPAA" in scope_text
        or "ePHI" in scope_text
        or "healthcare" in scope_text.lower()
        or "EHR" in scope_text
    ):
        themes.append("HIPAA Healthcare Compliance")
        roles.append("HIPAA Privacy & Security Officer")

    if (
        "GDPR" in scope_text
        or "personal data" in scope_text.lower()
        or "data subject" in scope_text.lower()
    ):
        themes.append("GDPR Data Privacy")
        roles.append("Data Privacy Officer (DPO)")

    if not themes:
        themes.append("General IT General Controls (ITGC)")
        roles.append("IT General Auditor")

    return themes, roles
