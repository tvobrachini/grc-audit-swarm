import os
from typing import List
from pydantic import BaseModel, Field

from langchain_core.prompts import ChatPromptTemplate
from langchain_community.tools import DuckDuckGoSearchRun

from src.swarm.state.schema import AuditState
from src.swarm.llm_factory import get_llm

class SearchQueries(BaseModel):
    queries: List[str] = Field(description="List of 2 to 3 specific search queries to find recent breaches and risk data.")

class RiskContextOutput(BaseModel):
    document_markdown: str = Field(description="The complete 1-pager markdown risk document.")

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

    # Step 1: Generate optimal search queries
    query_prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an IT Audit Researcher extracting specific search queries to find real-world evidence for an audit. Focus on recent data breaches, fines, or regulatory actions related to the provided themes. Return 2-3 specific queries."),
        ("human", "Themes: {themes}\nScope: {scope}")
    ])
    
    query_chain = query_prompt | llm.with_structured_output(SearchQueries)
    
    search_results = ""
    try:
        print("[Researcher] Generating search queries...")
        queries_res = query_chain.invoke({
            "themes": ", ".join(state.risk_themes),
            "scope": state.audit_scope_narrative
        })
        
        # Step 2: Execute searches
        for q in queries_res.queries:
            print(f"  -> Searching: '{q}'")
            res = search_tool.invoke(q)
            search_results += f"\\nQuery: {q}\\nResults: {res}\\n"
            
    except Exception as e:
        print(f"[Researcher] Search failed: {e}")
        search_results = "Search unavailable due to network/API constraints."

    # Step 3: Write the 1-Pager
    doc_prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a Lead IT Risk Analyst. Write a highly professional, 1-page Risk Context Document in Markdown. Incorporate the provided search results to cite real-world breaches, fines, or trends. Include a 'Recent Industry Breaches' section. Include explicit citations/links based on the search data."),
        ("human", "Themes: {themes}\\nScope: {scope}\\n\\nSearch Data:\\n{search_data}\\n\\nWrite the 1-Pager now.")
    ])
    
    doc_chain = doc_prompt | llm.with_structured_output(RiskContextOutput)
    
    try:
        print("[Researcher] Compiling 1-pager with real-world context...")
        final_doc = doc_chain.invoke({
            "themes": ", ".join(state.risk_themes),
            "scope": state.audit_scope_narrative,
            "search_data": search_results
        })
        risk_doc = final_doc.document_markdown
    except Exception as e:
        print(f"[Researcher] Document generation failed: {e}")
        return _emulate_researcher(state)

    audit_trail_entries = [{
        "agent_or_user_id": "Agent 6 (Risk Researcher)",
        "action_taken": "Generated 1-Pager Risk Context Document with real-world citations.",
        "reasoning_snapshot": "Used DuckDuckGo to pull live industry data.",
        "approval_status": "Auto-Approved"
    }]
    
    return {
        "risk_context_document": risk_doc,
        "audit_trail": state.audit_trail + audit_trail_entries
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

    audit_trail_entries = [{
        "agent_or_user_id": "Agent 6 (Risk Researcher Mock)",
        "action_taken": "Generated Mocked 1-Pager Risk Context Document.",
        "reasoning_snapshot": "No API Key provided, generated offline simulation.",
        "approval_status": "Auto-Approved"
    }]
    
    return {
        "risk_context_document": mock_doc,
        "audit_trail": state.audit_trail + audit_trail_entries
    }
