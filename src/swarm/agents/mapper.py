import os
import json
from typing import List

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from swarm.state.schema import AuditState, ControlMatrixItem, AuditProcedure
from swarm.llm_factory import get_llm


# Helper function to get the SCF database path
def get_scf_db_path() -> str:
    # Try different relative paths based on where this script is executed from
    possible_paths = [
        "../../data/scf_parsed.json",  # From scf-langgraph-swarm/src/agents
        "../data/scf_parsed.json",  # From main src/agents (future state)
        "data/scf_parsed.json",  # From root
    ]

    current_dir = os.path.dirname(os.path.abspath(__file__))
    for path in possible_paths:
        full_path = os.path.normpath(os.path.join(current_dir, path))
        if os.path.exists(full_path):
            return full_path

    # Fallback: configurable via env var, avoids hardcoded developer path
    return os.environ.get(
        "SCF_DATA_PATH",
        os.path.normpath(os.path.join(current_dir, "../../data/scf_parsed.json")),
    )


# We design an internal Pydantic schema just for the LLM to output its mapping
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
    Agent 2 & 3 (Control Mapper):
    Phase 2a: Pulls generic controls from SCF based on Orchestrator's risk themes.
    Phase 2b: Designs baseline test procedures for those controls.
    """
    print(f"[Mapper] Analyzing risk themes: {', '.join(state.risk_themes)}")

    # Ensure API Key
    if not os.environ.get("OPENAI_API_KEY") and not os.environ.get("GROQ_API_KEY"):
        print("[Mapper] Warning: No API keys found. Emulating logic.")
        return _emulate_mapping(state)

    # In a real environment, we'd use LangChain here.
    llm = get_llm(temperature=0)
    if llm is None:
        print("[Mapper] No LLM available. Emulating logic.")
        return _emulate_mapping(state)

    # --- Phase 2a: Control Retrieval (Simulated RAG/Search) ---
    # In a full production system, we would embed the 1100+ SCF controls and do a vector search.
    # For this swarm phase, we'll do a keyword-based heuristic chunking of the JSON database
    # to provide a manageable context to the LLM.

    scf_path = get_scf_db_path()
    try:
        with open(scf_path, "r", encoding="utf-8") as f:
            scf_db = json.load(f)
    except FileNotFoundError:
        print(f"[Mapper] Error: SCF database not found at {scf_path}. Emulating.")
        return _emulate_mapping(state)

    # Heuristic filtering: find controls whose domain or description matches the risk themes somewhat
    themes_lower = " ".join(state.risk_themes).lower()
    candidate_controls = []

    for control in scf_db:
        # Score the control against the themes (very naive heuristic)
        score = 0
        desc_lower = control.get("description", "").lower()
        domain_lower = control.get("domain", "").lower()

        for theme_word in themes_lower.split():
            if len(theme_word) > 3:  # Ignore short words
                if theme_word in desc_lower:
                    score += 1
                if theme_word in domain_lower:
                    score += 2

        if score > 0:
            # Keep a summary to send to the LLM
            candidate_controls.append(
                {
                    "id": control["control_id"],
                    "domain": control["domain"],
                    "description": control["description"],
                    "score": score,
                }
            )

    # Sort and take top 20 candidates to avoid blowing up context window
    candidate_controls.sort(key=lambda x: x["score"], reverse=True)
    top_candidates = candidate_controls[:20]

    if not top_candidates:
        print("[Mapper] RAG heuristic found no matches. Defaulting.")
        return _emulate_mapping(state)

    # Format context for LLM
    context_str = "\n".join(
        [
            f"- {c['id']} ({c['domain']}): {c['description'][:100]}..."
            for c in top_candidates
        ]
    )

    mapping_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are an expert IT Auditor. Map the identified risk themes and real-world Risk Context 1-Pager to the most relevant controls from the provided framework candidate list. Select up to 3 core controls that directly address the citations/breaches mentioned. DO NOT just select vaguely related ones; anchor them to the real-world context.",
            ),
            (
                "human",
                "Risk Themes: {themes}\n\n1-Pager Risk Context:\n{risk_context}\n\nCandidate Controls:\n{context}\n\nSelect the best controls to audit.",
            ),
        ]
    )

    structured_mapper = llm.with_structured_output(ScfMappingOutput)
    mapper_chain = mapping_prompt | structured_mapper

    print("[Mapper] Asking LLM to select from candidate controls...")
    try:
        mapping_result = mapper_chain.invoke(
            {
                "themes": ", ".join(state.risk_themes),
                "risk_context": state.risk_context_document,
                "context": context_str,
            }
        )
        selected_ids = mapping_result.selected_control_ids
        justification = mapping_result.mapping_justification
    except Exception as e:
        print(f"[Mapper] LLM Mapping failed: {e}. Emulating.")
        return _emulate_mapping(state)

    # Retrieve full definitions for selected controls
    selected_full_controls = [c for c in scf_db if c["control_id"] in selected_ids]

    # --- Phase 2b: Test Design ---
    design_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a Senior IT Auditor. Design detailed audit procedures (TOD, TOE, Substantive, and ERL) for the provided baseline IT control. Be specific and tactical.",
            ),
            (
                "human",
                "Design an Audit Procedure for this control:\nID: {control_id}\nDomain: {domain}\nDescription: {description}",
            ),
        ]
    )

    structured_designer = llm.with_structured_output(AuditProcedureOutput)
    designer_chain = design_prompt | structured_designer

    control_matrix = []

    print("[Mapper] Asking LLM to design baseline audit procedures...")
    for control in selected_full_controls:
        try:
            procedure_result = designer_chain.invoke(
                {
                    "control_id": control["control_id"],
                    "domain": control["domain"],
                    "description": control["description"],
                }
            )

            matrix_item = ControlMatrixItem(
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
            control_matrix.append(matrix_item)

        except Exception as e:
            print(
                f"[Mapper] LLM Procedure design failed for {control['control_id']}: {e}"
            )
            # Do not append if failed, skip to next

    # Log Actions
    audit_trail_entries = [
        {
            "agent_or_user_id": "Agent 2 (Control Mapper)",
            "action_taken": f"Selected {len(selected_full_controls)} baseline SCF controls.",
            "reasoning_snapshot": justification,
            "approval_status": "Auto-Approved",
        },
        {
            "agent_or_user_id": "Agent 3 (Procedure Designer)",
            "action_taken": "Designed baseline audit procedures for the selected controls.",
            "reasoning_snapshot": "Based on standard ISACA IT Audit frameworks and SCF control descriptions.",
            "approval_status": "Auto-Approved",
        },
    ]

    if not control_matrix:
        print("[Mapper] Matrix empty after processing, falling back to emulation.")
        return _emulate_mapping(state)

    print("[Mapper] Complete. Designed initial control matrix.")
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
                        "Analyze Logging Strategy Document for Cloud Trail requirements."
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
    else:  # ITGC
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
                        "Inspect the last 2 quarters documented access reviews for completion (signed off by managers)."
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
            "agent_or_user_id": "Agent 2/3 (Mapper/Designer Mock)",
            "action_taken": "Mocked baseline control mapping and procedure design.",
            "reasoning_snapshot": f"Based on themes: {themes}",
            "approval_status": "Auto-Approved",
        }
    ]

    return {
        "control_matrix": matrix,
        "audit_trail": state.audit_trail + audit_trail_entries,
    }
