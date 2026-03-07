from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
import sqlite3
import os
from pydantic import ValidationError
from typing import Dict, Any

from src.swarm.state.schema import AuditState
from src.swarm.agents.orchestrator import analyze_scope_and_themes
from src.swarm.agents.researcher import generate_risk_context
from src.swarm.agents.mapper import map_controls_and_design_tests
from src.swarm.agents.specialist import inject_specialist_tests
from src.swarm.agents.challenger import challenger_review
# Initialize the StateGraph with our strict Pydantic schema
workflow = StateGraph(AuditState)

# Phase 1: Planning Nodes
def orchestrator_node(state: dict) -> dict:
    """Agent 1 Wrapper: Reads the raw scope and identifies themes & dynamic roles."""
    return analyze_scope_and_themes(state)

def researcher_node(state: AuditState) -> dict:
    """Agent 6: Generates 1-Pager context using web search."""
    return generate_risk_context(state)

def control_mapper_node(state: AuditState) -> dict:
    """Agent 2 & 3: Pulls generic controls & designs baseline tests."""
    return map_controls_and_design_tests(state)

def dynamic_specialist_node(state: AuditState) -> dict:
    """Agent 4: Injects hyper-specific tests based on the dynamic roles."""
    return inject_specialist_tests(state)

def challenger_node(state: AuditState) -> dict:
    """Agent 5: Reviews the draft matrix for completeness and non-contradiction."""
    return challenger_review(state)

def should_revise(state: AuditState) -> str:
    """Conditional edge logic after the Challenger review."""
    if state.revision_feedback != "":
        print("Challenger requested revisions. Routing back to Mapper...")
        return "revise"
    print("Challenger approved. Routing to Human Review Checkpoint...")
    return "proceed_to_human"

def human_review_node(state: AuditState) -> dict:
    """
    Breakpoint: Halts execution so a human can review Phase 1 outputs.
    Streamlit will inject revision_feedback into state before resuming.
    """
    return {}

def human_should_revise(state: AuditState) -> str:
    if state.revision_feedback != "":
        print("Human requested revisions. Routing back to Swarm...")
        return "revise"
    print("Human approved. Ending Phase 1.")
    return "end"

# Add nodes to the graph
workflow.add_node("orchestrator", orchestrator_node)
workflow.add_node("researcher", researcher_node)
workflow.add_node("control_mapper", control_mapper_node)
workflow.add_node("dynamic_specialists", dynamic_specialist_node)
workflow.add_node("challenger", challenger_node)
workflow.add_node("human_review", human_review_node)

# Phase 1 Edges (The Flow)
workflow.set_entry_point("orchestrator")
workflow.add_edge("orchestrator", "researcher")
workflow.add_edge("researcher", "control_mapper")
workflow.add_edge("control_mapper", "dynamic_specialists")
workflow.add_edge("dynamic_specialists", "challenger")

# The Challenger will either loop back or go to the Human Review
workflow.add_conditional_edges(
    "challenger",
    should_revise,
    {
        "revise": "researcher", # Can loop back to researcher if feedback dictates
        "proceed_to_human": "human_review"
    }
)

# The Human Review node loops back if the user provides feedback in the UI
workflow.add_conditional_edges(
    "human_review",
    human_should_revise,
    {
        "revise": "researcher",
        "end": END
    }
)

# Ensure data directory exists
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../data")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "audit_checkpoints.sqlite")

# Compile the graph
# We add a 'SqliteSaver' here to persist Human-in-the-loop pauses across sessions.
# We must use checkpointer=memory in the compile signature, but since stream/invoke
# can happen across different files, we configure the sqlite connection per execution.
# For Streamlit compatibility where app.py imports swarm_app, we'll initialize the
# sqlite connection in app.py and pass it to the RunnableConfig, or leave the 
# checkpointer object attached here if it's thread-safe.

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
memory = SqliteSaver(conn)

app = workflow.compile(checkpointer=memory, interrupt_before=["human_review"])

if __name__ == "__main__":
    print("--- Swarm Architecture Graph Compiled Successfully ---")
    
    # Test a simple invocation
    initial_state = {
        "audit_scope_narrative": "We are migrating to AWS EKS and need an audit.",
        "audit_trail": []
    }
    
    # Run the graph
    final_state = app.invoke(initial_state)
    print("\n--- Final Output State ---")
    print(final_state)
