import os
from typing import List

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.swarm.state.schema import AuditState, ControlMatrixItem, AuditProcedure
from src.swarm.llm_factory import get_llm

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

    specialist_prompt = ChatPromptTemplate.from_messages([
        ("system", "You are acting as the following specialized IT Audit Roles: {roles}. "
                   "Your job is to take a generic baseline IT audit procedure and ENHANCE it "
                   "with highly technical, domain-specific checks (e.g., specific AWS CLI commands, "
                   "Kubernetes kubectl checks, or specific PCI-DSS parameters). "
                   "Do not write a generic procedure; write exactly what your specialist persona would check."),
        ("human", "Control ID: {control_id}\n"
                  "Domain: {domain}\n"
                  "Description: {description}\n\n"
                  "Current Baseline Procedures:\n"
                  "TOD: {tod}\n"
                  "TOE: {toe}\n"
                  "Substantive: {sub}\n"
                  "ERL: {erl}\n\n"
                  "Rewrite and enhance these procedures through the lens of your specialized role.")
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
                "roles": ", ".join(roles),
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
