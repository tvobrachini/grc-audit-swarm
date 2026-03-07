import os
from langchain_core.prompts import ChatPromptTemplate
from src.swarm.state.schema import AuditState
from src.swarm.skill_loader import detect_skills_from_scope

# IMPORTANT: Future LLM configuration goes here.
# For now, this is a placeholder function that simulates the structured JSON output
# expected from the Orchestrator LLM based on our Pydantic schema.

def analyze_scope_and_themes(state: AuditState) -> dict:
    """
    Agent 1 (Orchestrator): Reads the raw scope and identifies themes & dynamic roles.
    Also runs skill auto-detection from the skills/ library.
    """
    scope_text = state.audit_scope_narrative
    
    print(f"[Orchestrator] Mining risk themes from scope: '{scope_text[:80]}...'")
    
    themes = []
    roles = []
    
    if "AWS" in scope_text or "EKS" in scope_text:
        themes.append("AWS Cloud Infrastructure")
        roles.append("AWS Cloud Security Architect")
    
    if "payment" in scope_text.lower() or "credit card" in scope_text.lower() or "PCI" in scope_text:
        themes.append("PCI-DSS Payment Processing")
        roles.append("PCI Internal Auditor")

    if "HIPAA" in scope_text or "ePHI" in scope_text or "healthcare" in scope_text.lower():
        themes.append("HIPAA Healthcare Compliance")
        roles.append("HIPAA Privacy & Security Officer")

    if "GDPR" in scope_text or "personal data" in scope_text.lower():
        themes.append("GDPR Data Privacy")
        roles.append("Data Privacy Officer (DPO)")

    # If no themes found, default
    if not themes:
        themes.append("General IT General Controls (ITGC)")
        roles.append("IT General Auditor")

    # ─── Skill Auto-Detection ────────────────────────────────────────────────
    matched_skills = detect_skills_from_scope(scope_text)
    skill_ids   = [s.get("id", "") for s in matched_skills]
    skill_names = [s.get("name", "") for s in matched_skills]
    print(f"[Orchestrator] Matched skills: {', '.join(skill_names) or 'None'}")

    return {
        "risk_themes": themes,
        "specialist_roles_required": roles,
        "active_skill_ids": skill_ids,
        "active_skill_names": skill_names,
        "audit_trail": [
            {
                "agent_or_user_id": "Agent 1 (Orchestrator)",
                "action_taken": f"Identified {len(themes)} risk themes: {', '.join(themes)}. Loaded skills: {', '.join(skill_names) or 'ITGC (default)'}.",
                "reasoning_snapshot": f"Parsed scope narrative and auto-detected skill modules: {', '.join(skill_ids) or 'itgc_general'}",
                "approval_status": "Auto-Approved"
            }
        ]
    }
