import os
from langchain_core.prompts import ChatPromptTemplate
from src.swarm.state.schema import AuditState

# IMPORTANT: Future LLM configuration goes here.
# For now, this is a placeholder function that simulates the structured JSON output
# expected from the Orchestrator LLM based on our Pydantic schema.

def analyze_scope_and_themes(state: AuditState) -> dict:
    """
    Agent 1 (Orchestrator): Reads the raw scope and identifies themes & dynamic roles.
    This function will use a LangChain LLM equipped with Structured Output 
    parsing.
    """
    scope_text = state.audit_scope_narrative
    
    # Example logic that would normally be driven by the LLM
    print(f"[Orchestrator] Mining risk themes from scope: '{scope_text}'")
    
    themes = []
    roles = []
    
    if "AWS" in scope_text or "EKS" in scope_text:
        themes.append("AWS Cloud Infrastructure")
        roles.append("AWS Cloud Security Architect")
    
    if "payment" in scope_text.lower() or "credit card" in scope_text.lower():
        themes.append("PCI-DSS Payment Processing")
        roles.append("PCI Internal Auditor")

    # If no themes found, default
    if not themes:
        themes.append("General IT General Controls (ITGC)")
        roles.append("IT General Auditor")
        
    # The dictionary returned must align with the fields in AuditState
    return {
        "risk_themes": themes,
        "specialist_roles_required": roles,
        # Log the action directly to the immutable audit trail!
        "audit_trail": [
            {
                "agent_or_user_id": "Agent 1 (Orchestrator)",
                "action_taken": f"Identified {len(themes)} risk themes: {', '.join(themes)}",
                "reasoning_snapshot": "Parsed from initial Audit Scope narrative.",
                "approval_status": "Auto-Approved"
            }
        ]
    }
