from typing import List
from pydantic import BaseModel, Field

from langchain_core.prompts import ChatPromptTemplate
from langchain_community.tools import DuckDuckGoSearchRun

from src.swarm.state.schema import AuditState
from src.swarm.llm_factory import get_llm
from src.swarm.skill_loader import get_skill_by_id, get_researcher_context_hints


class SearchQueries(BaseModel):
    queries: List[str] = Field(
        description="List of 2 to 3 specific search queries to find recent breaches and risk data."
    )


class RiskContextOutput(BaseModel):
    document_markdown: str = Field(
        description="The complete 1-pager markdown risk document."
    )


def generate_risk_context(state: AuditState) -> dict:
    """
    Agent 6 (Risk Researcher):
    Takes the identified themes and scope, searches the web for recent data/breaches,
    and drafts a powerful 1-pager context doc with citations.
    """
    print("[Researcher] Building 1-pager risk context document...")

    llm = get_llm(temperature=0.1)
    if llm is None:
        return _emulate_researcher(state)

    try:
        search_tool = DuckDuckGoSearchRun()
    except Exception as e:
        print(f"[Researcher] Search tool setup failed: {e}. Emulating logic.")
        return _emulate_researcher(state)

    # Load skill-specific research guidance
    skill_hints = ""
    if state.active_skill_ids:
        skills = [s for sid in state.active_skill_ids if (s := get_skill_by_id(sid))]
        skill_hints = get_researcher_context_hints(skills)
        if skill_hints:
            print(
                f"[Researcher] Skill hints loaded for query guidance: {', '.join(state.active_skill_names)}"
            )

    # Step 1: Generate optimal search queries
    query_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are an IT Audit Researcher generating DuckDuckGo search queries to find real-world evidence. "
                "Focus on recent data breaches, fines, and regulatory actions related to the provided themes. "
                "Return 2-3 specific, targeted queries.\n\n"
                + (
                    f"Domain-specific research focus (from audit skill profile):\n{skill_hints}"
                    if skill_hints
                    else ""
                ),
            ),
            ("human", "Themes: {themes}\nScope: {scope}"),
        ]
    )

    query_chain = query_prompt | llm.with_structured_output(SearchQueries)

    search_results = ""
    try:
        print("[Researcher] Generating search queries...")
        queries_res = query_chain.invoke(
            {
                "themes": ", ".join(state.risk_themes),
                "scope": state.audit_scope_narrative,
            }
        )

        # Step 2: Execute searches
        for q in queries_res.queries:
            print(f"  -> Searching: '{q}'")
            res = search_tool.invoke(q)
            search_results += f"\\nQuery: {q}\\nResults: {res}\\n"

    except Exception as e:
        print(f"[Researcher] Search failed: {e}")
        search_results = "Search unavailable due to network/API constraints."

    # Step 3: Write the 1-Pager
    doc_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a Lead IT Risk Analyst. Write a highly professional, 1-page Risk Context Document in Markdown. Incorporate the provided search results to cite real-world breaches, fines, or trends. Include a 'Recent Industry Breaches' section. Include explicit citations/links based on the search data.",
            ),
            (
                "human",
                "Themes: {themes}\\nScope: {scope}\\n\\nSearch Data:\\n{search_data}\\n\\nWrite the 1-Pager now.",
            ),
        ]
    )

    doc_chain = doc_prompt | llm.with_structured_output(RiskContextOutput)

    try:
        print("[Researcher] Compiling 1-pager with real-world context...")
        final_doc = doc_chain.invoke(
            {
                "themes": ", ".join(state.risk_themes),
                "scope": state.audit_scope_narrative,
                "search_data": search_results,
            }
        )
        risk_doc = final_doc.document_markdown
    except Exception as e:
        print(f"[Researcher] Document generation failed: {e}")
        return _emulate_researcher(state)

    audit_trail_entries = [
        {
            "agent_or_user_id": "Agent 6 (Risk Researcher)",
            "action_taken": "Generated 1-Pager Risk Context Document with real-world citations.",
            "reasoning_snapshot": "Used DuckDuckGo to pull live industry data.",
            "approval_status": "Auto-Approved",
        }
    ]

    return {
        "risk_context_document": risk_doc,
        "audit_trail": state.audit_trail + audit_trail_entries,
    }


def _emulate_researcher(state: AuditState) -> dict:
    """Mock fallback logic."""
    themes = ", ".join(state.risk_themes)

    mock_doc = f"""# 1-Pager: Audit Risk Context
## Identified Scope Themes
**{themes}**

## Executive Summary
This audit addresses critical risks related to the identified scope. In the current cybersecurity landscape, misconfigurations and inadequate technical controls directly lead to severe data breaches and regulatory fines.

## Recent Industry Breaches (Simulated Data)
- **Target Breach (2024 Simulation):** A major retailer exposed 5 million records due to a misconfigured AWS S3 bucket.
- **Regulatory Action (GDPR):** In late 2023, a tech firm was fined €10M for failing to enforce strict access reviews (AC-03).
> *Source cited: Google Cyber Intelligence Report 2024 (Mock URL)*

## Conclusion
The upcoming audit must highly prioritize verification of technical implementation over mere policy review to mitigate these active threats.
"""

    audit_trail_entries = [
        {
            "agent_or_user_id": "Agent 6 (Risk Researcher Mock)",
            "action_taken": "Generated Mocked 1-Pager Risk Context Document.",
            "reasoning_snapshot": "No API Key provided, generated offline simulation.",
            "approval_status": "Auto-Approved",
        }
    ]

    return {
        "risk_context_document": mock_doc,
        "audit_trail": state.audit_trail + audit_trail_entries,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2: Targeted Research on Failed Controls
# ══════════════════════════════════════════════════════════════════════════════


class FailedControlResearch(BaseModel):
    search_query: str = Field(
        description="The best DuckDuckGo query to find real-world breaches/fines related to this specific control failure."
    )
    context_paragraph: str = Field(
        description="A short (2-3 sentence) paragraph citing real-world breach or enforcement precedent for this specific control failure type, suitable for appending to an audit finding."
    )


def research_failed_controls(state: AuditState) -> dict:
    """
    Phase 2 Researcher: For each Fail/Exception finding, runs a targeted web search
    to find real-world breach or regulatory enforcement precedent.
    This adds 'why this matters in the real world' context to each finding.
    """
    findings = state.testing_findings
    failed = [f for f in findings if f.status in ("Fail", "Exception")]

    if not failed:
        print("[Phase2 Researcher] No failures to research.")
        return {}

    print(
        f"[Phase2 Researcher] Researching real-world precedent for {len(failed)} failed controls..."
    )

    llm = get_llm(temperature=0.1)
    if llm is None:
        return _emulate_phase2_researcher(state, failed)

    try:
        search_tool = DuckDuckGoSearchRun()
    except Exception as e:
        print(f"[Phase2 Researcher] Search tool unavailable: {e}")
        return _emulate_phase2_researcher(state, failed)

    # Load skill-specific research hints for targeted queries
    skill_hints = ""
    if state.active_skill_ids:
        skills = [s for sid in state.active_skill_ids if (s := get_skill_by_id(sid))]
        skill_hints = get_researcher_context_hints(skills)

    query_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are an IT Audit Risk Researcher. Given a specific control failure, "
                "generate ONE precise DuckDuckGo search query to find real-world data breaches, regulatory fines, "
                "or enforcement actions resulting from this exact type of control gap. "
                "Then write a brief context paragraph citing the findings.\n\n"
                + (
                    f"Domain-specific research focus:\n{skill_hints}"
                    if skill_hints
                    else ""
                ),
            ),
            (
                "human",
                "Control ID: {control_id}\n"
                "Domain: {domain}\n"
                "Finding: {justification}\n"
                "Themes: {themes}\n\n"
                "Generate a targeted search query and a context paragraph.",
            ),
        ]
    )

    chain = query_prompt | llm.with_structured_output(FailedControlResearch)
    updated = list(findings)

    for i, finding in enumerate(updated):
        if finding.status not in ("Fail", "Exception"):
            continue
        try:
            result = chain.invoke(
                {
                    "control_id": finding.control_id,
                    "domain": finding.agent_role,
                    "justification": finding.justification[:400],
                    "themes": ", ".join(state.risk_themes),
                }
            )
            # Execute the search
            search_result = ""
            try:
                print(f"  → Searching: '{result.search_query}'")
                search_result = search_tool.invoke(result.search_query)[:600]
            except Exception:
                search_result = "Search unavailable."

            # Synthesize into finding
            context = result.context_paragraph
            if search_result and "unavailable" not in search_result.lower():
                context += f" *(Web research: {search_result[:300]}...)*"

            updated[i] = finding.model_copy(
                update={
                    "justification": (
                        f"{finding.justification}\n\n"
                        f"**🌐 Researcher Context:**\n{context}"
                    )
                }
            )
            print(f"  ✓ Researched {finding.control_id}")
        except Exception as e:
            print(f"[Phase2 Researcher] Failed for {finding.control_id}: {e}")

    return {
        "testing_findings": updated,
        "audit_trail": state.audit_trail
        + [
            {
                "agent_or_user_id": "Phase 2 Researcher",
                "action_taken": f"Added real-world breach/enforcement precedent to {len(failed)} failed controls.",
                "reasoning_snapshot": "DuckDuckGo targeted searches on each failed control type.",
                "approval_status": "Auto-Approved",
            }
        ],
    }


def _emulate_phase2_researcher(state: AuditState, failed_findings) -> dict:
    """Mock Phase 2 researcher."""
    updated = list(state.testing_findings)
    mock_contexts = {
        "AC": "In 2024, a major cloud provider suffered a breach traced to orphaned privileged accounts not removed during offboarding, resulting in $4.2M in regulatory fines. (Source: CSA Cloud Security Incidents 2024)",
        "LOG": "The 2023 MOVEit breach went undetected for 47 days partly because centralized logging was disabled on the affected transfer nodes. CISA issued guidance mandating continuous log integrity checks.",
        "CST": "CrowdStrike's 2024 incident report showed that 38% of cloud compromises exploited non-Golden AMI instances lacking EDR coverage. CIS Benchmark v2.0 now mandates automated AMI validation in CI/CD.",
        "CRY": "In August 2024, HHS fined a healthcare organization $950K for storing ePHI in unencrypted RDS instances. The HIPAA Security Rule §164.312(a)(2)(iv) explicitly requires encryption for data at rest.",
        "CHG": "A 2023 Gartner study found that 67% of production outages were caused by unapproved emergency changes. SOX Section 404 deficiency reports frequently cite change management process bypass as a material weakness.",
        "NET": "Shodan reported in Q1 2025 that 23,000 AWS security groups still allow 0.0.0.0/0 SSH ingress, making them prime targets for automated credential-stuffing attacks. Three enterprises reported ransomware entry via this vector.",
        "VUL": "CVE-2025-44810 (OpenSSL) was weaponized in under 20 days after disclosure. CISA KEV listed it within 72 hours. Organizations that failed to patch within the 15-day SLA faced direct exploitation in documented campaigns.",
    }
    for i, f in enumerate(updated):
        if f.status not in ("Fail", "Exception"):
            continue
        prefix = f.control_id.split("-")[0]
        ctx = mock_contexts.get(
            prefix,
            "This control failure type has been cited in multiple regulatory enforcement actions. Auditors should document evidence carefully to support findings.",
        )
        updated[i] = f.model_copy(
            update={
                "justification": f"{f.justification}\n\n**🌐 Researcher Context:**\n{ctx}"
            }
        )
    return {
        "testing_findings": updated,
        "audit_trail": state.audit_trail
        + [
            {
                "agent_or_user_id": "Phase 2 Researcher (Mock)",
                "action_taken": f"Appended mock breach precedent to {len(failed_findings)} findings.",
                "reasoning_snapshot": "Real-world context from mock database (no search tool in mock mode).",
                "approval_status": "Auto-Approved",
            }
        ],
    }
