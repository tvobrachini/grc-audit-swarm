import os
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from src.swarm.state.schema import AuditState
from src.swarm.llm_factory import get_llm

class ReviewOutput(BaseModel):
    is_approved: bool = Field(description="True if the control matrix is comprehensive, relevant, and non-contradictory. False if it needs revision.")
    feedback: str = Field(description="Detailed feedback to the Mapper/Specialist on what needs to be fixed. Empty if approved.")

def challenger_review(state: AuditState) -> dict:
    """
    Agent 5 (Challenger): 
    Reviews the compiled control matrix before it is presented to a human.
    Checks for logical gaps, missing obvious controls based on the scope,
    or vague procedures.
    """
    print("[Challenger] Reviewing draft matrix for completeness and rigor...")

    llm = get_llm(temperature=0)
    if llm is None:
        print("[Challenger] No LLM available. Auto-approving for emulation.")
        return _emulate_challenger(state)
        
    # Serialize the matrix for the LLM context
    matrix_str = ""
    for item in state.control_matrix:
        matrix_str += f"Control ID: {item.control_id} ({item.domain})\n"
        if item.procedures:
            matrix_str += f" - TOD Steps: {item.procedures.tod_steps}\n"
            matrix_str += f" - TOE Steps: {item.procedures.toe_steps}\n"
    
    review_prompt = ChatPromptTemplate.from_messages([
        ("system", "You are the Lead QA Audit Partner. Your job is to rigorously review the draft audit matrix against the original scope. If the test steps are too generic, or if apparent risk themes are missing coverage, you must REJECT it and provide feedback. If it looks comprehensive, APPROVE it."),
        ("human", "Original Narrative Scope: {scope}\n"
                  "Identified Themes: {themes}\n\n"
                  "Draft Control Matrix & Procedures:\n{matrix}\n\n"
                  "Does this matrix adequately and specifically address the scope?")
    ])

    structured_reviewer = llm.with_structured_output(ReviewOutput)
    reviewer_chain = review_prompt | structured_reviewer
    
    try:
        print("[Challenger] Prompting Lead QA Partner LLM...")
        result = reviewer_chain.invoke({
            "scope": state.audit_scope_narrative,
            "themes": ", ".join(state.risk_themes),
            "matrix": matrix_str
        })
        
        status = "Approved" if result.is_approved else "Rejected"
        feedback = result.feedback
        
    except Exception as e:
        print(f"[Challenger] LLM Review failed: {e}")
        return _emulate_challenger(state)

    print(f"[Challenger] Status: {status}")
    if feedback:
        print(f"[Challenger] Feedback: {feedback}")

    audit_trail_entries = [{
        "agent_or_user_id": "Agent 5 (Challenger/QA Lead)",
        "action_taken": f"Reviewed Draft Matrix: {status}",
        "reasoning_snapshot": feedback if feedback else "Matrix meets quality standards.",
        "approval_status": "Auto-Approved"
    }]

    return {
        "revision_feedback": feedback if not result.is_approved else "",
        "audit_trail": state.audit_trail + audit_trail_entries
    }


def _emulate_challenger(state: AuditState) -> dict:
    """Mock fallback logic."""
    audit_trail_entries = [{
        "agent_or_user_id": "Agent 5 (Challenger Mock)",
        "action_taken": "Reviewed Draft Matrix: Approved",
        "reasoning_snapshot": "Mocked approval.",
        "approval_status": "Auto-Approved"
    }]
    
    return {
        "revision_feedback": "",
        "audit_trail": state.audit_trail + audit_trail_entries
    }
