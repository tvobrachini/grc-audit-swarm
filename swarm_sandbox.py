import streamlit as st
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "src"))
from swarm.audit_flow import AuditFlow

st.set_page_config(page_title="TDD Sandbox - GRC Swarm", layout="wide")
st.title("🚧 CrewAI Execution Sandbox (TDD)")
st.markdown("This sandbox runs the new IIA Phase 1-3 AI architecture independently to avoid crashing the main app during component refactoring.")

if "flow" not in st.session_state:
    st.session_state.flow = AuditFlow()
    st.session_state.phase = 0

flow = st.session_state.flow

st.markdown("### Step 1: Input Business Context")
theme = st.text_input("Theme", "Public S3 Buckets Exposure")
context = st.text_area("Context", "We are a fintech handling sensitive data. Ensure no buckets are publicly accessible.")

if st.session_state.phase == 0:
    if st.button("🚀 Run Phase 1 (Planning Crew)"):
        flow.state.theme = theme
        flow.state.business_context = context
        flow.state.frameworks = ["PCAOB AS 2201", "CIS AWS Foundations"]
        
        with st.spinner("AI Planning Crew executing (This may take several minutes)..."):
            flow.generate_planning()
            st.session_state.phase = 1
        st.rerun()

if st.session_state.phase >= 1:
    st.success("✅ Phase 1 (Planning) Complete!")
    try:
        st.json(flow.state.racm_plan)
    except Exception as e:
        st.error(f"Could not render racm_plan: {e}")
    
    st.info(flow.state.current_human_dossier)
    if st.session_state.phase == 1:
        if st.button("Human Gate 1: Approve Planning (IIA 2340 Stamping)"):
            with st.spinner("Executing Phase 2 Fieldwork Crew (Querying Evidence)..."):
                flow.generate_fieldwork(human_id="AUDITOR_001")
                st.session_state.phase = 2
            st.rerun()

if st.session_state.phase >= 2:
    st.success("✅ Phase 2 (Execution) Complete!")
    try:
        st.json(flow.state.working_papers)
    except Exception as e:
        st.error(f"Could not render working_papers: {e}")
    
    st.info(flow.state.current_human_dossier)
    if st.session_state.phase == 2:
        if st.button("Human Gate 2: Approve Fieldwork & Hashed Evidence (IIA 2340 Stamping)"):
            with st.spinner("Executing Phase 3 Reporting Crew..."):
                flow.generate_reporting(human_id="AUDITOR_001")
                st.session_state.phase = 3
            st.rerun()

if st.session_state.phase == 3:
    st.success("🎉 Phase 3 (Reporting) Complete! Swarm Run Finished.")
    try:
        st.json(flow.state.final_report)
    except Exception as e:
        st.error(f"Could not render final_report: {e}")
    
    st.markdown("### 📝 Engagement Supervision Audit Trail (IIA 2340)")
    st.table(flow.state.approval_trail)
