from typing import List

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.swarm.state.schema import AuditState
from src.swarm.llm_factory import get_llm
from src.swarm.skill_loader import get_skill_by_id, get_specialist_prompt


class EnhancedProcedureOutput(BaseModel):
    tod_steps: List[str] = Field(
        description="Enriched Test of Design steps focusing on the specific specialist role context."
    )
    toe_steps: List[str] = Field(
        description="Enriched Test of Effectiveness steps focusing on the specific specialist role context."
    )
    substantive_steps: List[str] = Field(
        description="Enriched Substantive testing steps focusing on the specific specialist role context."
    )
    erl_items: List[str] = Field(
        description="Enriched Evidence Request List items needed from the auditee, specific to the domain/tools."
    )


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

    print(
        f"[Specialist] {', '.join(roles)} injecting specific tests into baseline matrix..."
    )

    llm = get_llm(temperature=0.2)
    if llm is None:
        print("[Specialist] No LLM available. Emulating logic.")
        return _emulate_specialist(state)

    # ─── Load Skill System Prompt ───────────────────────────────────────────────
    # If the Orchestrator matched skills, use their combined expert system prompts.
    # Fall back to a generic roles-based prompt if no skills are loaded.
    if state.active_skill_ids:
        loaded_skills = [
            s for sid in state.active_skill_ids if (s := get_skill_by_id(sid))
        ]
        skill_system_prompt = get_specialist_prompt(loaded_skills)
        print(
            f"[Specialist] Loaded skill prompts: {', '.join(state.active_skill_names)}"
        )
    else:
        skill_system_prompt = (
            f"You are acting as the following specialized IT Audit Roles: {', '.join(roles)}. "
            "Enhance the provided audit procedures with highly technical, domain-specific checks. "
            "Do not write generic procedures — write exactly what a specialist in this domain would check."
        )
        print("[Specialist] No skills loaded, using role-based prompt.")

    specialist_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                skill_system_prompt
                + "\n\nYour task: Take the baseline audit procedure provided and ENHANCE it with highly specific, technical steps.",
            ),
            (
                "human",
                "Control ID: {control_id}\n"
                "Domain: {domain}\n"
                "Description: {description}\n\n"
                "Current Baseline Procedures:\n"
                "TOD: {tod}\n"
                "TOE: {toe}\n"
                "Substantive: {sub}\n"
                "ERL: {erl}\n\n"
                "Rewrite and enhance these procedures through the lens of your specialized expertise.",
            ),
        ]
    )

    structured_enhancer = llm.with_structured_output(EnhancedProcedureOutput)
    enhancer_chain = specialist_prompt | structured_enhancer

    enhanced_matrix = []

    for item in state.control_matrix:
        if not item.procedures:
            enhanced_matrix.append(item)
            continue

        try:
            print(f"  -> Enhancing procedure for {item.control_id}...")
            result = enhancer_chain.invoke(
                {
                    "control_id": item.control_id,
                    "domain": item.domain,
                    "description": item.description,
                    "tod": "\n".join(item.procedures.tod_steps),
                    "toe": "\n".join(item.procedures.toe_steps),
                    "sub": "\n".join(item.procedures.substantive_steps),
                    "erl": "\n".join(item.procedures.erl_items),
                }
            )

            # Update the item with enhanced procedures
            item.procedures.tod_steps = result.tod_steps
            item.procedures.toe_steps = result.toe_steps
            item.procedures.substantive_steps = result.substantive_steps
            item.procedures.erl_items = result.erl_items

        except Exception as e:
            print(f"[Specialist] Failed to enhance {item.control_id}: {e}")

        enhanced_matrix.append(item)

    audit_trail_entries = [
        {
            "agent_or_user_id": f"Agent 4 ({'/'.join(roles)})",
            "action_taken": f"Injected domain-specific technical procedures into {len(enhanced_matrix)} controls.",
            "reasoning_snapshot": "Leveraged deep technical expertise to ensure findings are actionable for engineers.",
            "approval_status": "Auto-Approved",
        }
    ]

    return {
        "control_matrix": enhanced_matrix,
        "audit_trail": state.audit_trail + audit_trail_entries,
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

    audit_trail_entries = [
        {
            "agent_or_user_id": f"Agent 4 ({'/'.join(roles)} Mock)",
            "action_taken": "Mocked injection of technical domain procedures.",
            "reasoning_snapshot": "Added mock AWS CLI commands.",
            "approval_status": "Auto-Approved",
        }
    ]

    return {
        "control_matrix": enhanced_matrix,
        "audit_trail": state.audit_trail + audit_trail_entries,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2: Specialist Annotation of Findings
# ══════════════════════════════════════════════════════════════════════════════


class FindingAnnotationOutput(BaseModel):
    regulatory_implications: str = Field(
        description="Specific regulatory requirements, framework controls, or compliance citations that this finding implicates. "
        "Be precise: cite control IDs, article numbers, benchmark sections, or requirement numbers."
    )
    technical_root_cause: str = Field(
        description="Brief technical explanation of WHY this control failed based on the evidence."
    )


def annotate_findings_with_specialist(state: AuditState) -> dict:
    """
    Phase 2 Specialist: Reviews Worker findings and enriches each Fail/Exception
    finding with domain-specific regulatory implications and root cause analysis
    based on the loaded skill system prompt.
    """
    findings = state.testing_findings
    if not findings:
        return {}

    failed = [f for f in findings if f.status in ("Fail", "Exception")]
    if not failed:
        print("[Phase2 Specialist] No failures found — no annotation needed.")
        return {
            "audit_trail": state.audit_trail
            + [
                {
                    "agent_or_user_id": "Phase 2 Specialist",
                    "action_taken": "No Fail/Exception findings to annotate.",
                    "reasoning_snapshot": "All controls passed.",
                    "approval_status": "Auto-Approved",
                }
            ]
        }

    print(
        f"[Phase2 Specialist] Annotating {len(failed)} failed/exception findings with specialist context..."
    )

    llm = get_llm(temperature=0.1, prefer_fast=True)
    if llm is None:
        return _emulate_phase2_specialist(state, failed)

    # Load skill system prompt
    if state.active_skill_ids:
        skills = [s for sid in state.active_skill_ids if (s := get_skill_by_id(sid))]
        skill_prompt = get_specialist_prompt(skills)
        skill_names = ", ".join(state.active_skill_names)
    else:
        skill_prompt = (
            "You are a senior IT audit specialist with deep domain knowledge."
        )
        skill_names = "General ITGC"

    annotation_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                f"{skill_prompt}\n\n"
                "You are now in Phase 2 of an IT audit — the execution phase. "
                "Your job is to enrich this finding with expert domain context: "
                "specific regulatory citations and root cause analysis. "
                "DO NOT propose remediation, action plans, or recommendations.",
            ),
            (
                "human",
                "Control ID: {control_id}\n"
                "Status: {status}\n"
                "Finding: {justification}\n"
                "Evidence: {evidence}\n"
                "TOD: {tod_r} | TOE: {toe_r} | Substantive: {sub_r}\n\n"
                "As the {skill_names} specialist, annotate this finding with regulatory implications "
                "and technical root cause.",
            ),
        ]
    )

    chain = annotation_prompt | llm.with_structured_output(FindingAnnotationOutput)
    updated_findings = list(findings)

    for i, finding in enumerate(updated_findings):
        if finding.status not in ("Fail", "Exception"):
            continue
        try:
            result = chain.invoke(
                {
                    "control_id": finding.control_id,
                    "status": finding.status,
                    "justification": finding.justification,
                    "evidence": " | ".join(finding.evidence_extracted[:2]),
                    "tod_r": finding.tod_result or "—",
                    "toe_r": finding.toe_result or "—",
                    "sub_r": finding.substantive_result or "—",
                    "skill_names": skill_names,
                }
            )
            # Enrich the justification with specialist annotations
            updated_findings[i] = finding.model_copy(
                update={
                    "justification": (
                        f"{finding.justification}\n\n"
                        f"**🔬 Specialist Annotation ({skill_names}):**\n"
                        f"- **Regulatory Implications:** {result.regulatory_implications}\n"
                        f"- **Root Cause:** {result.technical_root_cause}"
                    )
                }
            )
            print(f"  ✓ Annotated {finding.control_id} with specialist context")
        except Exception as e:
            print(
                f"[Phase2 Specialist] Annotation failed for {finding.control_id}: {e}"
            )

    return {
        "testing_findings": updated_findings,
        "audit_trail": state.audit_trail
        + [
            {
                "agent_or_user_id": f"Phase 2 Specialist ({skill_names})",
                "action_taken": f"Annotated {len(failed)} findings with regulatory implications and root cause.",
                "reasoning_snapshot": f"Applied {skill_names} expertise to enrich findings with actionable remediation context.",
                "approval_status": "Auto-Approved",
            }
        ],
    }


def _emulate_phase2_specialist(state: AuditState, failed_findings) -> dict:
    """Mock Phase 2 specialist annotation."""
    skill_names = (
        ", ".join(state.active_skill_names)
        if state.active_skill_names
        else "General ITGC"
    )
    updated = list(state.testing_findings)
    mock_annotations = {
        "AC": (
            "NIST CSF PR.AC-1 / CIS Control 5.1 — IAM policy violations imply unauthorized data exposure risk.",
            "Access review process breakdown or orphan account management gap.",
        ),
        "LOG": (
            "PCI Req 10.2 / NIST AU-2 — Log integrity failures prevent forensic traceability.",
            "SIEM ingestion gap or CloudTrail misconfig on specific regions.",
        ),
        "CST": (
            "CIS AWS Benchmark 2.1 / AWS WAF-SEC06 — Non-Golden AMI instances lack hardening baseline.",
            "CI/CD pipeline bypass allowing non-compliant instance launches.",
        ),
        "CRY": (
            "PCI Req 3.5 / NIST SC-28 — Unencrypted data at rest violates cardholder data protection.",
            "RDS instance created before encryption policy enforcement.",
        ),
        "CHG": (
            "COBIT BAI06 / ITIL Change Management — Unapproved emergency changes indicate process bypass.",
            "Lack of automated guard rails on emergency change approval flow.",
        ),
        "NET": (
            "CIS AWS 4.1-4.2 / NIST SC-7 — Unrestricted SSH violates network segmentation principle.",
            "Security group rule misconfiguration; no automated compliance checker active.",
        ),
        "VUL": (
            "PCI Req 6.3.3 / NIST SI-2 — Critical CVE unpatched beyond SLA = active exploitable exposure.",
            "Patch ticket not generated automatically on critical CVE detection.",
        ),
    }
    for i, f in enumerate(updated):
        if f.status not in ("Fail", "Exception"):
            continue
        prefix = f.control_id.split("-")[0]
        impl, cause = mock_annotations.get(
            prefix,
            (
                "General IT control failure with compliance implications.",
                "Process or technical gap in control implementation.",
            ),
        )
        updated[i] = f.model_copy(
            update={
                "justification": (
                    f"{f.justification}\n\n**🔬 Specialist Annotation ({skill_names}):**\n"
                    f"- **Regulatory Implications:** {impl}\n- **Root Cause:** {cause}"
                )
            }
        )
    return {
        "testing_findings": updated,
        "audit_trail": state.audit_trail
        + [
            {
                "agent_or_user_id": f"Phase 2 Specialist Mock ({skill_names})",
                "action_taken": f"Annotated {len(failed_findings)} findings (mock mode).",
                "reasoning_snapshot": "Mock annotations applied based on control domain prefix.",
                "approval_status": "Auto-Approved",
            }
        ],
    }
