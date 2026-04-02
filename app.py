"""
GRC Audit Swarm — Streamlit Application (CrewAI Engine)
=========================================
Phase 1: AI Planning Phase (RACM Generation)
Phase 2: AI Execution Phase (Evidence Hashing)
Phase 3: AI Reporting Phase (Tone QA)
"""

import streamlit as st
import os
import sys

from dotenv import load_dotenv
load_dotenv()
sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

from swarm.audit_flow import AuditFlow
from ui.components.styles import inject_swarm_css
from ui.components.sidebar import render_sidebar
from ui.components.phase1_review import render_phase1_review
from ui.components.phase2_review import render_phase2_review

st.set_page_config(page_title="GRC Audit Swarm", page_icon="🛡️", layout="wide", initial_sidebar_state="expanded")

inject_swarm_css()
render_sidebar()

st.title("🎯 Swarm Audit Command Center (CrewAI Native)")

if "flow" not in st.session_state:
    st.session_state.flow = AuditFlow()
    st.session_state.phase = 0

flow = st.session_state.flow

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 0 — Scope Input
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.phase == 0:
    st.markdown("### Step 1: Define Target & Business Context")
    theme = st.text_input("Audit Theme", "Public S3 Buckets Exposure")
    context = st.text_area("Context", "We are a fintech handling sensitive data. Ensure no buckets are publicly accessible.")
    
    if st.button("🚀 Launch Execution (CrewAI Planning Phase)", type="primary"):
        flow.state.theme = theme
        flow.state.business_context = context
        flow.state.frameworks = ["PCAOB AS 2201", "CIS AWS Foundations"]
        
        with st.spinner("AI Planning Crew generating the Risk and Control Matrix..."):
            flow.generate_planning()
            st.session_state.phase = 1
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — Planning Phase Gate (RACM)
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.phase == 1:
    def on_phase1_feedback(command: str):
        with st.spinner("Phase 2 Execution Crew querying vaults & hashing evidence..."):
            flow.generate_fieldwork(human_id="AUDITOR_UI_001")
            st.session_state.phase = 2
        st.rerun()

    render_phase1_review(flow.state.racm_plan, on_phase1_feedback)

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — Execution Phase Gate (Working Papers)
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.phase == 2:
    def on_phase2_finalize():
        with st.spinner("Phase 3 Reporting Crew engaging Tone QA & Summarization..."):
            flow.generate_reporting(human_id="AUDITOR_UI_001")
            st.session_state.phase = 3
        st.rerun()
        
    render_phase2_review(flow.state.working_papers, on_phase2_finalize)

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — Final Report and Immutable Audit Trail
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.phase == 3:
    st.success("🎉 Swarm Run Finished. Phase 3 (Reporting) Output Received!")
    
    rep = flow.state.final_report or {}
    st.markdown("### 📊 Board Executive Summary")
    st.info(rep.get("executive_summary", "No summary provided."))
    
    with st.expander("Detailed Engineering Matrix"):
        st.markdown(rep.get("detailed_report", "No details provided."))
    
    st.markdown("---")
    st.markdown("### 📝 Engagement Supervision Audit Trail (IIA 2340 Stamping)")
    st.table(flow.state.approval_trail)
    
    if st.button("🔄 Start New Audit", type="primary"):
        st.session_state.clear()
        st.rerun()
