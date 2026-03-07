import os
from typing import List

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.swarm.state.schema import AuditState, ControlMatrixItem, AuditProcedure
from src.swarm.llm_factory import get_llm
from src.swarm.skill_loader import get_skill_by_id, get_specialist_prompt

class EnhancedProcedureOutput(BaseModel):
    tod_steps: List[str] = Field(description="Enriched Test of Design steps focusing on the specific specialist role context.")
    toe_steps: List[str] = Field(description="Enriched Test of Effectiveness steps focusing on the specific specialist role context.")
    substantive_steps: List[str] = Field(description="Enriched Substantive testing steps focusing on the specific specialist role context.")
    erl_items: List[str] = Field(description="Enriched Evidence Request List items needed from the auditee, specific to the domain/tools.")

def inject_specialist_tests(state: AuditState) -> dict:
    """
    Agent 4 (Dynamic Specialist):
    Reviews the baseline control matrix and injects hyper-specific, technical
    audit steps based on the dynamic roles assigned by the Orchestrator.
    """
    roles = state.specialist_roles_required
    if not roles or "IT General Auditor" in roles:
        # If it's just a general ITGC, we don't necessarily need hyper-specific injection
        print("[Specialist] No hyper-specific specialist required for this scope.")
        return {}

    print(f"[Specialist] {', '.join(roles)} injecting specific tests into baseline matrix...")

    llm = get_llm(temperature=0.2)
    if llm is None:
        print("[Specialist] No LLM available. Emulating logic.")
        return _emulate_specialist(state)

    # ─── Load Skill System Prompt ───────────────────────────────────────────────
    # If the Orchestrator matched skills, use their combined expert system prompts.
    # Fall back to a generic roles-based prompt if no skills are loaded.
    if state.active_skill_ids:
        loaded_skills = [s for sid in state.active_skill_ids if (s := get_skill_by_id(sid))]
        skill_system_prompt = get_specialist_prompt(loaded_skills)
        print(f"[Specialist] Loaded skill prompts: {', '.join(state.active_skill_names)}")
    else:
        skill_system_prompt = (
            f"You are acting as the following specialized IT Audit Roles: {', '.join(roles)}. "
            "Enhance the provided audit procedures with highly technical, domain-specific checks. "
            "Do not write generic procedures — write exactly what a specialist in this domain would check."
        )
        print("[Specialist] No skills loaded, using role-based prompt.")

    specialist_prompt = ChatPromptTemplate.from_messages([
        ("system", skill_system_prompt + "\n\nYour task: Take the baseline audit procedure provided and ENHANCE it with highly specific, technical steps."),
        ("human", "Control ID: {control_id}\n"
                  "Domain: {domain}\n"
                  "Description: {description}\n\n"
                  "Current Baseline Procedures:\n"
                  "TOD: {tod}\n"
                  "TOE: {toe}\n"
                  "Substantive: {sub}\n"
                  "ERL: {erl}\n\n"
                  "Rewrite and enhance these procedures through the lens of your specialized expertise.")
    ])
    
    structured_enhancer = llm.with_structured_output(EnhancedProcedureOutput)
    enhancer_chain = specialist_prompt | structured_enhancer

    enhanced_matrix = []
    
    for item in state.control_matrix:
        if not item.procedures:
            enhanced_matrix.append(item)
            continue
            
        try:
            print(f"  -> Enhancing procedure for {item.control_id}...")
            result = enhancer_chain.invoke({
                "control_id": item.control_id,
                "domain": item.domain,
                "description": item.description,
                "tod": "\n".join(item.procedures.tod_steps),
                "toe": "\n".join(item.procedures.toe_steps),
                "sub": "\n".join(item.procedures.substantive_steps),
                "erl": "\n".join(item.procedures.erl_items)
            })
            
            # Update the item with enhanced procedures
            item.procedures.tod_steps = result.tod_steps
            item.procedures.toe_steps = result.toe_steps
            item.procedures.substantive_steps = result.substantive_steps
            item.procedures.erl_items = result.erl_items
            
        except Exception as e:
            print(f"[Specialist] Failed to enhance {item.control_id}: {e}")
            
        enhanced_matrix.append(item)

    audit_trail_entries = [{
        "agent_or_user_id": f"Agent 4 ({'/'.join(roles)})",
        "action_taken": f"Injected domain-specific technical procedures into {len(enhanced_matrix)} controls.",
        "reasoning_snapshot": "Leveraged deep technical expertise to ensure findings are actionable for engineers.",
        "approval_status": "Auto-Approved"
    }]

    return {
        "control_matrix": enhanced_matrix,
        "audit_trail": state.audit_trail + audit_trail_entries
    }

def _emulate_specialist(state: AuditState) -> dict:
    """Fallback logic when no API key is present."""
    roles = state.specialist_roles_required
    enhanced_matrix = []
    
    for item in state.control_matrix:
        # Deep copy wasn't strictly necessary since we mutate in place, 
        # but good practice if we want immutable state history later.
        if item.control_id == "CST-01" and "AWS Cloud Security Architect" in roles:
            if item.procedures:
                item.procedures.substantive_steps.append(
                    "Run 'aws ec2 describe-instances --filters Name=image-id,Values=ami-xxxxxx' to verify golden AMI enforcement."
                )
                item.procedures.toe_steps.append(
                    "Review AWS CloudTrail logs specifically for 'RunInstances' events bypassing the CI/CD pipeline."
                )
        enhanced_matrix.append(item)
        
    audit_trail_entries = [{
        "agent_or_user_id": f"Agent 4 ({'/'.join(roles)} Mock)",
        "action_taken": "Mocked injection of technical domain procedures.",
        "reasoning_snapshot": "Added mock AWS CLI commands.",
        "approval_status": "Auto-Approved"
    }]
        
    return {
        "control_matrix": enhanced_matrix,
        "audit_trail": state.audit_trail + audit_trail_entries
    }
